"""工单助手 工单层：让平台从「等你说话」变成「按规则替你推进工作」。

参考 deepseek-ai/dsh-superagent-prototype 的理念：
- 一件工作 = 一个 work item，不是一段聊天；
- 只有需要人决定时才进入 needs_attention（授权、提问、失败）；
- 每个工单在自己的会话 / 工作区里执行，结果回到工单；
- 来源（手动 / GitHub / agent）先过准入，再决定观察还是自动执行。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.services import attention_service
from services.agent_zero.stores import settings_store, zero_store
from services.agent_zero.stores.zero_store import (
    DEFAULT_ROLE_ID,
    DEFAULT_WORKSPACE_ID,
    _q,
)
from services.agent_zero.task_layer import conversation, conv_worker, run_service
from services.shared.auth import LOCAL_USER_ID

logger = logging.getLogger(__name__)

STATUSES = ("inbox", "working", "needs_attention", "done", "closed")
STATUS_LABELS = {
    "inbox": "待分派",
    "working": "进行中",
    "needs_attention": "需要你",
    "done": "已完成",
    "closed": "已关闭",
}
SOURCES = ("manual", "github", "agent", "import")
POLICY_KEY = "superagent"

DEFAULT_POLICY: Dict[str, Any] = {
    "mode": "observe",  # off | observe | auto
    "permission_mode": "auto",
    "auto_execute": False,
    "allowed_sources": ["manual"],
    "allowed_actors": [],
    "default_workspace_id": DEFAULT_WORKSPACE_ID,
    "default_role_id": DEFAULT_ROLE_ID,
    "default_team_id": "",
}


def _limit(value: Any, default: int = 100, maximum: int = 200) -> int:
    try:
        n = int(value or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, maximum))


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── 策略 / 准入 ──────────────────────────────────────────────────────────

def get_policy() -> Dict[str, Any]:
    saved = settings_store.get(POLICY_KEY, {}) or {}
    policy = dict(DEFAULT_POLICY)
    if isinstance(saved, dict):
        policy.update({k: v for k, v in saved.items() if v is not None})
    if policy.get("mode") not in ("off", "observe", "auto"):
        policy["mode"] = "observe"
    if policy.get("permission_mode") not in ("auto", "once", "session", "always"):
        policy["permission_mode"] = "auto"
    if not isinstance(policy.get("allowed_sources"), list):
        policy["allowed_sources"] = list(DEFAULT_POLICY["allowed_sources"])
    policy["allowed_sources"] = [s for s in policy["allowed_sources"] if s in SOURCES]
    if not isinstance(policy.get("allowed_actors"), list):
        policy["allowed_actors"] = []
    policy["allowed_actors"] = [str(a).strip() for a in policy["allowed_actors"] if str(a).strip()]
    policy["auto_execute"] = bool(policy.get("auto_execute"))
    return policy


def save_policy(patch: Dict[str, Any]) -> Dict[str, Any]:
    current = get_policy()
    for key in DEFAULT_POLICY:
        if key in patch:
            current[key] = patch[key]
    if current.get("mode") not in ("off", "observe", "auto"):
        raise ValueError("mode 必须是 off / observe / auto")
    if current.get("permission_mode") not in ("auto", "once", "session", "always"):
        raise ValueError("permission_mode 必须是 auto / once / session / always")
    current["allowed_sources"] = [s for s in (current.get("allowed_sources") or [])
                                  if s in SOURCES]
    current["allowed_actors"] = [str(a).strip() for a in (current.get("allowed_actors") or [])
                                 if str(a).strip()]
    current["auto_execute"] = bool(current.get("auto_execute"))
    settings_store.put(POLICY_KEY, current)
    zero_store.append_audit("local", "superagent_policy", args={"policy": current})
    return current


def admission(item: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """来源 / 用户是否被允许推进这个工单。返回 (allowed, reason)。"""
    policy = policy or get_policy()
    mode = policy.get("mode") or "observe"
    if item.get("source") == "manual":
        return True, ""
    if mode == "off":
        return False, "工单助手 已关闭，等人工打开"
    if item.get("source") not in (policy.get("allowed_sources") or []):
        return False, f"来源 {item.get('source') or 'unknown'} 未授权"
    allowed_actors = policy.get("allowed_actors") or []
    actor = str(item.get("source_actor") or "").strip()
    if allowed_actors and actor and actor not in allowed_actors:
        return False, f"来源用户 {actor} 未授权"
    return True, ""


# ── 状态同步 ─────────────────────────────────────────────────────────────

def _pending_map() -> Dict[str, str]:
    """conversation_id -> 需要人的原因（待授权 / 待回答）。"""
    out: Dict[str, str] = {}
    try:
        for row in attention_service.pending_approvals(200):
            cid = str(row.get("conversation_id") or "")
            if cid:
                out.setdefault(cid, f"等待授权：{row.get('tool') or row.get('name') or '工具'}")
        for row in attention_service.pending_questions():
            cid = str(row.get("conversation_id") or "")
            if cid:
                out.setdefault(cid, "等待你回答问题")
    except Exception:
        logger.debug("读取待处理工单失败", exc_info=True)
    return out


def _last_assistant_text(conv_id: str) -> Optional[str]:
    if not conv_id:
        return None
    SessionLocal = get_session_local()
    try:
        with SessionLocal() as db:
            text_value = db.execute(
                text(
                    f"SELECT props->>'content' AS content FROM {_q('gdag_nodes')} "
                    f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
                    f"AND props->>'stage' = 'exploration' AND props->>'by' = 'assistant' "
                    f"AND COALESCE(props->>'kind', '') NOT IN ('checkpoint','task_proposal') "
                    f"ORDER BY created_at DESC, rowid DESC LIMIT 1"
                ),
                {"cid": conv_id},
            ).scalar()
    except Exception:
        return None
    body = str(text_value or "").strip()
    if not body:
        return None
    return body[:1200]


def _update_status(item_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    sets = ", ".join(f"{key} = :{key}" for key in fields)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('work_items')} SET {sets} WHERE id = :id"),
            {**fields, "id": item_id},
        )
        db.commit()


def pending_reason(conversation_id: str) -> Optional[str]:
    """该会话当前需要人处理的原因（待授权 / 待回答）；无则 None。"""
    cid = str(conversation_id or "").strip()
    if not cid:
        return None
    return _pending_map().get(cid)


def _auto_goal(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """派活时自动创建的对话级目标（自动续跑用）；不是自动工单则 None。"""
    meta = item.get("meta") or {}
    if not meta.get("auto_goal"):
        return None
    conv_id = str(item.get("conversation_id") or "")
    if not conv_id:
        return None
    try:
        from services.agent_zero.task_layer import goal_service

        goal = goal_service.get_goal(conv_id)
    except Exception:
        logger.debug("读取工单目标失败 conversation=%s", conv_id, exc_info=True)
        return None
    if not goal:
        return None
    goal_id = str(meta.get("goal_id") or "")
    if goal_id and str(goal.get("id") or "") != goal_id:
        return None
    return goal


def _goal_blocked(goal: Dict[str, Any]) -> bool:
    try:
        from services.agent_zero.task_layer import goal_service

        return goal_service.is_blocked(goal)
    except Exception:
        return False


def _goal_blocked_reason(goal: Dict[str, Any]) -> str:
    note = str((goal or {}).get("note") or "").strip()
    try:
        from services.agent_zero.task_layer import goal_service

        prefix = goal_service.BLOCKED_PREFIX
        if note.startswith(prefix):
            note = note[len(prefix):].strip()
    except Exception:
        pass
    return note or "等待你的决定"


def _sync_item(item: Dict[str, Any], pending: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """把 run 的真实状态收敛到工单；只有需要人时停在 needs_attention。

    自动工单（派活时建了对话级目标）以**目标**为准：目标仍 active 就还算是
    进行中（系统会自动续跑 / 重试），achieved 才算完成，exhausted / closed /
    blocked 停下来等人——不能因为某一轮 run 结束就把工单标成完成。
    """
    if item.get("status") in ("closed",):
        return item
    # 后台任务自动建的工单由任务生命周期维护（一次任务可能有多个成员 run），
    # 不能按单个 run 的状态来回覆盖。
    if str((item.get("meta") or {}).get("managed_by") or "") == "team_task":
        return item
    pending = pending if pending is not None else _pending_map()
    conv_id = str(item.get("conversation_id") or "")
    run_id = str(item.get("run_id") or "")
    run = run_service.get_run(run_id) if run_id else None
    reason = pending.get(conv_id) if conv_id else None
    status = str(item.get("status") or "inbox")

    if status in ("done", "closed") and not reason:
        return item

    goal = _auto_goal(item)
    goal_status = str((goal or {}).get("status") or "")
    blocked = bool(goal) and goal_status == "active" and _goal_blocked(goal)

    # 重新打开后还没派活：上一轮的 run 已经结束，别把刚打开的工单又按旧 run
    # 收敛成「已完成」，等用户重新派活即可。
    if status == "inbox" and (not run or run.get("status") not in ("queued", "running")):
        if not reason:
            return item

    if reason:
        target, attention = "needs_attention", reason
    elif not run_id:
        if status in ("working", "needs_attention"):
            if item.get("attention_reason"):
                return item
            target, attention = "needs_attention", "还没有运行记录，重新派活即可"
        else:
            return item
    elif not run:
        target, attention = "needs_attention", "运行记录已丢失，重新派活即可"
    elif run.get("status") in ("queued", "running"):
        target, attention = "working", None
    elif run.get("status") == "stopped":
        target, attention = "needs_attention", "运行已停止，确认后继续"
    elif goal and blocked:
        target, attention = "needs_attention", _goal_blocked_reason(goal)
    elif goal and goal_status == "exhausted":
        target, attention = "needs_attention", "自动续跑次数已用尽，确认后继续"
    elif goal and goal_status == "closed":
        target, attention = "needs_attention", "目标已手动结束，确认是否完成"
    elif goal and goal_status == "active":
        # 目标没完成（这一轮成功或失败都算）：系统会自动续跑 / 重试，保持进行中。
        target, attention = "working", None
    elif run.get("status") != "done":
        target, attention = "needs_attention", f"运行失败：{run.get('error') or '未知错误'}"
    else:
        target, attention = "done", None

    fields: Dict[str, Any] = {}
    if target != status:
        fields["status"] = target
    if (attention or "") != (item.get("attention_reason") or ""):
        fields["attention_reason"] = attention
    if target == "done":
        fields["finished_at"] = _now()
        if not item.get("result_summary"):
            summary = _last_assistant_text(conv_id)
            if summary:
                fields["result_summary"] = summary
    elif target in ("needs_attention", "working") and item.get("finished_at"):
        fields["finished_at"] = None
    if fields:
        _update_status(item["id"], **fields)
        item.update(fields)
    return item


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item["meta"] = _loads(item.get("meta"), {})
    item["priority"] = int(item.get("priority") or 0)
    item["status_label"] = STATUS_LABELS.get(str(item.get("status") or ""), "")
    return item


def _list_rows(status: Optional[str] = None, limit: int = 100,
               conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
    limit = _limit(limit)
    conds = []
    params: Dict[str, Any] = {"n": limit}
    if status in STATUSES:
        conds.append("w.status = :status")
        params["status"] = status
    if conversation_id:
        conds.append("w.conversation_id = :cid")
        params["cid"] = str(conversation_id)
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(
                f"SELECT w.*, ws.name AS workspace_name, r.name AS role_name, "
                f"r.title AS role_title, d.name AS role_department, "
                f"t.name AS team_name "
                f"FROM {_q('work_items')} w "
                f"LEFT JOIN {_q('workspaces')} ws ON ws.id = w.workspace_id "
                f"LEFT JOIN {_q('roles')} r ON r.id = w.role_id "
                f"LEFT JOIN {_q('departments')} d ON d.id = r.department_id "
                f"LEFT JOIN {_q('agent_teams')} t ON t.id = w.team_id "
                f"{where} "
                f"ORDER BY CASE w.status WHEN 'needs_attention' THEN 0 "
                f"WHEN 'working' THEN 1 WHEN 'inbox' THEN 2 ELSE 3 END, "
                f"w.priority DESC, w.updated_at DESC, w.id DESC LIMIT :n"
            ),
            params,
        ).mappings().all()
    return [_public(dict(r)) for r in rows]


def _get_row(item_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(
                f"SELECT w.*, ws.name AS workspace_name, r.name AS role_name, "
                f"r.title AS role_title, d.name AS role_department, "
                f"t.name AS team_name "
                f"FROM {_q('work_items')} w "
                f"LEFT JOIN {_q('workspaces')} ws ON ws.id = w.workspace_id "
                f"LEFT JOIN {_q('roles')} r ON r.id = w.role_id "
                f"LEFT JOIN {_q('departments')} d ON d.id = r.department_id "
                f"LEFT JOIN {_q('agent_teams')} t ON t.id = w.team_id "
                f"WHERE w.id = :id"
            ),
            {"id": item_id},
        ).mappings().first()
    return _public(dict(row)) if row else None


def stats() -> Dict[str, int]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT status, COUNT(*) AS n FROM {_q('work_items')} GROUP BY status")
        ).mappings().all()
    out = {status: 0 for status in STATUSES}
    for row in rows:
        if row["status"] in out:
            out[row["status"]] = int(row["n"] or 0)
    out["attention"] = out["needs_attention"]
    out["open"] = out["inbox"] + out["working"] + out["needs_attention"]
    out["total"] = sum(out[s] for s in STATUSES)
    return out


def _attach_usage(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给工单补 token 用量（输入 / 输出 / 速度 / 缓存）。

    - 后台任务自动建的工单（meta.managed_by=team_task）：按 meta.task_id 汇总
      该任务成员 run 的用量，不把同一会话里其它任务的 token 算进来；
    - 手动 / 来源工单：按它独占的会话汇总（每次派活都会新建一个会话）。
    """
    from services.agent_zero.services import usage_service

    conv_ids: List[str] = []
    task_ids: List[str] = []
    for item in items:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        task_id = str(meta.get("task_id") or "").strip()
        if str(meta.get("managed_by") or "") == "team_task" and task_id:
            task_ids.append(task_id)
        else:
            conv_id = str(item.get("conversation_id") or "").strip()
            if conv_id:
                conv_ids.append(conv_id)
    conv_usage = usage_service.usage_by_conversation(conv_ids, stage="exploration")
    task_usage = usage_service.usage_by_task(task_ids)
    for item in items:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        task_id = str(meta.get("task_id") or "").strip()
        if str(meta.get("managed_by") or "") == "team_task" and task_id:
            usage = task_usage.get(task_id)
        else:
            usage = conv_usage.get(str(item.get("conversation_id") or "").strip())
        item["usage"] = usage or usage_service.empty_usage()
    return items


def _usage_totals(items: List[Dict[str, Any]]) -> Dict[str, int]:
    totals = {"tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
              "cached_tokens": 0, "llm_ms": 0, "rounds": 0, "turns": 0}
    for item in items:
        usage = item.get("usage") or {}
        totals["tokens"] += int(usage.get("total_tokens") or 0)
        totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        totals["cached_tokens"] += int(usage.get("cached_tokens") or 0)
        totals["llm_ms"] += int(usage.get("llm_ms") or 0)
        totals["rounds"] += int(usage.get("rounds") or 0)
        totals["turns"] += int(usage.get("turns") or 0)
    return totals


def list_items(status: Optional[str] = None, limit: int = 100,
               conversation_id: Optional[str] = None) -> Dict[str, Any]:
    pending = _pending_map()
    items = _attach_usage([_sync_item(item, pending)
                           for item in _list_rows(status, limit, conversation_id)])
    summary = stats()
    summary.update(_usage_totals(items))
    return {"items": items, "stats": summary, "policy": get_policy()}


def get_item(item_id: str) -> Optional[Dict[str, Any]]:
    row = _get_row(item_id)
    if not row:
        return None
    return _attach_usage([_sync_item(row, _pending_map())])[0]


# ── CRUD ─────────────────────────────────────────────────────────────────

def find_by_ref(ref: str) -> Optional[Dict[str, Any]]:
    """按 source_ref 找工单（后台任务自动建单用它幂等关联）。"""
    value = str(ref or "").strip()
    if not value:
        return None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT id FROM {_q('work_items')} WHERE source_ref = :ref LIMIT 1"),
            {"ref": value},
        ).scalar()
    return get_item(str(row)) if row else None


def create_from_task(task: Dict[str, Any], conv: Optional[Dict[str, Any]] = None
                     ) -> Optional[Dict[str, Any]]:
    """角色 / 群组的后台任务 → 自动创建一张工单（同一任务幂等）。

    工单是任务在「工单」面板里的可观测入口；状态由任务生命周期维护
    （`_sync_item` 不会用单个成员 run 去覆盖它）。
    """
    task_id = str((task or {}).get("id") or "").strip()
    if not task_id:
        return None
    ref = f"team_task:{task_id}"
    existing = find_by_ref(ref)
    if existing:
        return existing
    members = [str(m.get("role_name") or m.get("role_id") or "")
               for m in (task.get("members") or [])]
    try:
        return create_item({
            "title": str(task.get("title") or "后台任务"),
            "body": str(task.get("goal") or ""),
            "source": "agent",
            "source_ref": ref,
            "source_actor": "角色 / 群组任务",
            "status": "working",
            "conversation_id": str(task.get("conversation_id") or ""),
            "role_id": str(task.get("leader_role_id") or ""),
            "team_id": str(task.get("team_id") or ""),
            "workspace_id": str((conv or {}).get("workspace_id") or ""),
            "meta": {"task_id": task_id, "managed_by": "team_task",
                     "members": members},
        })
    except Exception:
        logger.exception("后台任务自动建工单失败 task=%s", task_id)
        return None


def mark_auto_handled(task: Dict[str, Any], note: str = "") -> None:
    """任务失败但已自动交回角色：工单不再挂在「需要你」，记录一句说明。

    自动继续是默认行为，失败也被角色接手了；如果还留一张「需要你」，等于又
    要求用户点一下，和「全自动」的预期相反。
    """
    task_id = str((task or {}).get("id") or "").strip()
    if not task_id:
        return
    item = find_by_ref(f"team_task:{task_id}")
    if not item:
        return
    summary = str(item.get("result_summary") or "").strip()
    text = (summary + "\n" + note).strip() if note else summary
    _update_status(
        item["id"], status="done", attention_reason=None,
        finished_at=_now(), result_summary=text[:4000] or None,
    )


def finish_task(task: Dict[str, Any], summary: str = "") -> Optional[Dict[str, Any]]:
    """后台任务结束 → 收敛对应工单状态（完成 / 需要你）。"""
    task_id = str((task or {}).get("id") or "").strip()
    if not task_id:
        return None
    item = find_by_ref(f"team_task:{task_id}")
    if not item:
        return None
    raw = str(task.get("status") or "")
    if raw == "done":
        status, attention = "done", None
    elif raw in ("failed", "stopped"):
        status = "needs_attention"
        attention = ("任务执行失败，确认后可重新派发" if raw == "failed"
                     else "任务已停止，确认后可重新派发")
    else:
        return item
    fields: Dict[str, Any] = {"status": status}
    if attention:
        fields["attention_reason"] = attention
    else:
        fields["attention_reason"] = None
        fields["finished_at"] = _now()
    if summary:
        fields["result_summary"] = summary[:4000]
    _update_status(item["id"], **fields)
    return get_item(item["id"]) or item


def create_item(payload: Dict[str, Any], *, actor: str = "local") -> Dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("工单标题不能为空")
    source = str(payload.get("source") or "manual").strip().lower()
    if source not in SOURCES:
        source = "manual"
    policy = get_policy()
    body = str(payload.get("body") or "").strip()
    source_actor = str(payload.get("source_actor") or "").strip() or ("local" if source == "manual" else "")
    item_id = zero_store.new_id("work")
    workspace_id = str(payload.get("workspace_id") or "").strip() \
        or str(policy.get("default_workspace_id") or DEFAULT_WORKSPACE_ID)
    role_id = str(payload.get("role_id") or "").strip() \
        or str(policy.get("default_role_id") or DEFAULT_ROLE_ID)
    team_id = str(payload.get("team_id") or "").strip() \
        or str(policy.get("default_team_id") or "").strip() or None
    status = "inbox"
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    allowed, reason = admission({
        "source": source, "source_actor": source_actor,
    }, policy)
    attention = None
    if not allowed:
        status = "needs_attention"
        attention = reason
    # 内部自动建单（角色 / 群组的后台任务）可以显式指定状态：
    # 任务已经在后台跑，工单只是它在前台的可观测入口，不该被来源策略拦成待处理。
    explicit_status = str(payload.get("status") or "").strip()
    if explicit_status in STATUSES:
        status = explicit_status
        attention = None if status in ("inbox", "working", "done") else attention
    conv_id = str(payload.get("conversation_id") or "").strip() or None
    run_id = str(payload.get("run_id") or "").strip() or None
    started_at = _now() if status == "working" else None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(
                f"INSERT INTO {_q('work_items')} "
                f"(id, title, body, source, source_ref, source_actor, status, priority, "
                f"workspace_id, role_id, team_id, conversation_id, run_id, "
                f"attention_reason, meta, started_at, created_at, updated_at) "
                f"VALUES (:id, :title, :body, :source, :ref, :actor, :status, :priority, "
                f":ws, :role, :team, :conv, :run, :attention, :meta, :started, :now, :now)"
            ),
            {
                "id": item_id, "title": title[:200], "body": body,
                "source": source, "ref": str(payload.get("source_ref") or "").strip()[:500] or None,
                "actor": source_actor[:120] or None,
                "status": status, "priority": max(0, min(9, int(payload.get("priority") or 0))),
                "ws": workspace_id, "role": role_id, "team": team_id,
                "conv": conv_id, "run": run_id,
                "attention": attention, "meta": json.dumps(meta, ensure_ascii=False),
                "started": started_at, "now": _now(),
            },
        )
        db.commit()
    zero_store.append_audit(actor, "work_item_create",
                           args={"id": item_id, "title": title, "source": source})
    return get_item(item_id) or {}


def update_item(item_id: str, patch: Dict[str, Any], *, actor: str = "local") -> Dict[str, Any]:
    item = _get_row(item_id)
    if not item:
        raise ValueError("工单不存在")
    fields: Dict[str, Any] = {}
    for key in ("title", "body", "source", "source_ref", "source_actor", "attention_reason",
                "result_summary", "workspace_id", "role_id", "team_id"):
        if key in patch:
            value = patch[key]
            fields[key] = str(value or "").strip() or None if key not in ("title", "body") else str(value or "").strip()
    if "priority" in patch:
        fields["priority"] = max(0, min(9, int(patch.get("priority") or 0)))
    if "meta" in patch and isinstance(patch.get("meta"), dict):
        fields["meta"] = json.dumps(patch["meta"], ensure_ascii=False)
    if "status" in patch:
        status = str(patch.get("status") or "").strip()
        if status not in STATUSES:
            raise ValueError(f"非法工单状态: {status}")
        fields["status"] = status
        if status in ("done", "closed"):
            fields["finished_at"] = _now()
        elif status in ("inbox", "working"):
            fields["finished_at"] = None
            if status == "working":
                fields["attention_reason"] = None
    if not fields:
        return item
    _update_status(item_id, **fields)
    if fields.get("status") in ("done", "closed"):
        # 人工收尾：停掉自动续跑，不然目标还会继续开新一轮。
        _close_item_goal(item)
    zero_store.append_audit(actor, "work_item_update", args={"id": item_id, "fields": list(fields)})
    return get_item(item_id) or {}


def _close_item_goal(item: Dict[str, Any]) -> None:
    conv_id = str(item.get("conversation_id") or "")
    if not conv_id or not (item.get("meta") or {}).get("auto_goal"):
        return
    try:
        from services.agent_zero.task_layer import goal_service

        goal_service.close_goal(conv_id)
    except Exception:
        logger.debug("结束工单目标失败 conversation=%s", conv_id, exc_info=True)


def delete_item(item_id: str, *, actor: str = "local") -> bool:
    item = _get_row(item_id)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        result = db.execute(
            text(f"DELETE FROM {_q('work_items')} WHERE id = :id"), {"id": item_id}
        )
        db.commit()
    if result.rowcount:
        if item:
            _close_item_goal(item)
        zero_store.append_audit(actor, "work_item_delete", args={"id": item_id})
    return bool(result.rowcount)


# ── 派活 ─────────────────────────────────────────────────────────────────

def _prompt(item: Dict[str, Any]) -> str:
    source = str(item.get("source") or "manual")
    ref = str(item.get("source_ref") or "").strip()
    return (
        f"# 工单：{item.get('title') or ''}\n\n"
        f"{item.get('body') or '（没有补充说明）'}\n\n"
        f"---\n"
        f"工单 ID：{item.get('id')}\n"
        f"来源：{source}{(' · ' + ref) if ref else ''}\n\n"
        "请直接推进并完成这个工单。需要人决定的工单，停下来提问；"
        "完成后用几行说明结果、关键改动和验证情况。"
    )


def _goal_objective(item: Dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    text = f"完成工单：{title}"
    if body:
        text += f"\n\n{body}"
    return text[:4000]


# 工单自动续跑的轮数上限：不暴露成配置，用尽后停在「需要你」，防止跑飞。
WORK_ITEM_MAX_RUNS = 20


async def dispatch_item(item_id: str, *, actor: str = "local") -> Dict[str, Any]:
    item = get_item(item_id)
    if not item:
        raise ValueError("工单不存在")
    if item.get("status") == "working":
        raise ValueError("工单正在执行中")
    if item.get("status") == "closed":
        raise ValueError("工单已关闭，先重新打开")
    if item.get("status") == "done":
        raise ValueError("工单已完成，先重新打开")
    run = run_service.get_run(item["run_id"]) if item.get("run_id") else None
    if run and run.get("status") in ("queued", "running"):
        raise ValueError("上一次运行还在进行")
    policy = get_policy()
    allowed, reason = admission(item, policy)
    if not allowed:
        update_item(item_id, {"status": "needs_attention", "attention_reason": reason})
        return get_item(item_id) or item

    workspace_id = str(item.get("workspace_id") or policy.get("default_workspace_id") or DEFAULT_WORKSPACE_ID)
    role_id = str(item.get("role_id") or policy.get("default_role_id") or DEFAULT_ROLE_ID)
    team_id = str(item.get("team_id") or policy.get("default_team_id") or "").strip() or None
    prompt = _prompt(item)
    conv = conversation.create_conversation(
        workspace_id, LOCAL_USER_ID, role_id=role_id, team_id=team_id,
        source="superagent", permission_mode=str(policy.get("permission_mode") or "auto"),
        run_mode="autonomous",
    )
    conv_id = conv["id"]
    # 自动续跑：给工单建一个对话级目标。模型报告 achieved 才算完成；否则每轮
    # run 结束后（包括失败）由 goal_service 自动入队下一轮。
    meta = dict(item.get("meta") or {})
    try:
        from services.agent_zero.task_layer import goal_service

        prev_conv = str(item.get("conversation_id") or "")
        if prev_conv and prev_conv != conv_id:
            # 重新派活会新建会话：旧会话的目标必须先结束，否则它会继续续跑。
            goal_service.close_goal(prev_conv)
        goal = goal_service.set_goal(
            conv_id,
            objective=_goal_objective(item),
            max_runs=WORK_ITEM_MAX_RUNS,
        )
        meta.update({"auto_goal": True, "goal_id": str(goal.get("id") or "")})
        prompt += (
            "\n\n（这是一张会自动续跑的工单：每轮结束后系统会接着推进。"
            "真正完成时调用 goal_report(status=\"achieved\") 结束续跑；"
            "卡住需要人决定时调用 goal_report(status=\"blocked\")。）"
        )
    except Exception:
        meta.pop("auto_goal", None)
        meta.pop("goal_id", None)
        logger.warning("工单自动续跑初始化失败 item=%s", item_id, exc_info=True)
    msg = conversation.send_message(conv_id, prompt, LOCAL_USER_ID, queued=True)
    run = run_service.create_run(
        conv_id, LOCAL_USER_ID, role_id=role_id, content=prompt,
        mode="autonomous", auto_execute=bool(policy.get("auto_execute")),
        queued_msg_id=(msg or {}).get("id"),
    )
    try:
        await conv_worker.request_run(run["id"])
    except Exception:
        logger.warning("工单 run 触发失败，worker 周期会拾取", exc_info=True)
    _update_status(
        item_id,
        status="working", conversation_id=conv_id, run_id=run["id"],
        attention_reason=None, started_at=_now(), finished_at=None,
        meta=json.dumps(meta, ensure_ascii=False),
    )
    zero_store.append_audit(actor, "work_item_dispatch",
                            args={"id": item_id, "conversation_id": conv_id, "run_id": run["id"]})
    return get_item(item_id) or item

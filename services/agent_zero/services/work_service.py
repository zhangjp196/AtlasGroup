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


def _sync_item(item: Dict[str, Any], pending: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """把 run 的真实状态收敛到工单；只有需要人时停在 needs_attention。"""
    if item.get("status") in ("closed",):
        return item
    pending = pending if pending is not None else _pending_map()
    conv_id = str(item.get("conversation_id") or "")
    run_id = str(item.get("run_id") or "")
    run = run_service.get_run(run_id) if run_id else None
    reason = pending.get(conv_id) if conv_id else None
    status = str(item.get("status") or "inbox")

    if status in ("done", "closed") and not reason:
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
    elif run.get("status") == "done":
        target, attention = "done", None
    elif run.get("status") == "stopped":
        target, attention = "needs_attention", "运行已停止，确认后继续"
    else:
        target, attention = "needs_attention", f"运行失败：{run.get('error') or '未知错误'}"

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


def _list_rows(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    limit = _limit(limit)
    where = ""
    params: Dict[str, Any] = {"n": limit}
    if status in STATUSES:
        where = "WHERE w.status = :status"
        params["status"] = status
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


def list_items(status: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    pending = _pending_map()
    items = [_sync_item(item, pending) for item in _list_rows(status, limit)]
    return {"items": items, "stats": stats(), "policy": get_policy()}


def get_item(item_id: str) -> Optional[Dict[str, Any]]:
    row = _get_row(item_id)
    return _sync_item(row, _pending_map()) if row else None


# ── CRUD ─────────────────────────────────────────────────────────────────

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
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(
                f"INSERT INTO {_q('work_items')} "
                f"(id, title, body, source, source_ref, source_actor, status, priority, "
                f"workspace_id, role_id, team_id, attention_reason, meta, created_at, updated_at) "
                f"VALUES (:id, :title, :body, :source, :ref, :actor, :status, :priority, "
                f":ws, :role, :team, :attention, :meta, :now, :now)"
            ),
            {
                "id": item_id, "title": title[:200], "body": body,
                "source": source, "ref": str(payload.get("source_ref") or "").strip()[:500] or None,
                "actor": source_actor[:120] or None,
                "status": status, "priority": max(0, min(9, int(payload.get("priority") or 0))),
                "ws": workspace_id, "role": role_id, "team": team_id,
                "attention": attention, "meta": json.dumps(meta, ensure_ascii=False),
                "now": _now(),
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
    zero_store.append_audit(actor, "work_item_update", args={"id": item_id, "fields": list(fields)})
    return get_item(item_id) or {}


def delete_item(item_id: str, *, actor: str = "local") -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        result = db.execute(
            text(f"DELETE FROM {_q('work_items')} WHERE id = :id"), {"id": item_id}
        )
        db.commit()
    if result.rowcount:
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
    )
    zero_store.append_audit(actor, "work_item_dispatch",
                            args={"id": item_id, "conversation_id": conv_id, "run_id": run["id"]})
    return get_item(item_id) or item

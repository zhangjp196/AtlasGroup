"""定时任务 + 自由触发。

两种触发方式共用一张表：

- ``scheduled``：按固定时间触发（一次 / 间隔 / 每天 / 每周）；
- ``smart``：到达评估时间后，由模型结合 ``condition`` 与最近运行结果判断
  「现在要不要主动做这件事」。它不是简单的 cron：没有固定执行时刻，也可能
  在一次评估后决定继续等待。

任务每次运行都落到普通会话里，因此仍然可以多角色参与、查看历史、继续追问。
后台 ticker 由 ``webapp`` 启动，默认每 20 秒检查一次；模型评估本身按任务
``schedule.every_minutes`` 限频，避免频繁调用 LLM。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores import zero_store
from services.agent_zero.stores.zero_store import (
    DEFAULT_ROLE_ID,
    DEFAULT_WORKSPACE_ID,
    _q,
)
from services.agent_zero.task_layer import conversation, conv_worker, run_service
from services.shared.auth import LOCAL_USER_ID

logger = logging.getLogger(__name__)

STATUSES = ("active", "paused", "done")
TRIGGER_MODES = ("scheduled", "smart", "manual")
SCHEDULE_KINDS = ("once", "interval", "daily", "weekly", "smart")
DEFAULT_EVAL_MINUTES = 30
MIN_EVAL_MINUTES = 5
DEFAULT_INTERVAL_MINUTES = 60
TICK_INTERVAL_S = 20.0

_TICK_LOCK = asyncio.Lock()
_TICKER: Optional[asyncio.Task] = None


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _now_dt() -> datetime:
    return datetime.now().replace(microsecond=0)


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(microsecond=0)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None


def _fmt(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_minutes(value: Any, default: int, minimum: int = 1) -> int:
    return max(minimum, min(10_080, _int(value, default)))


def _row(row: Any) -> Dict[str, Any]:
    out = dict(row)
    out["schedule"] = _loads(out.get("schedule"), {})
    out["meta"] = _loads(out.get("meta"), {})
    for key in ("next_run_at", "last_run_at", "created_at", "updated_at"):
        value = out.get(key)
        out[key] = str(value) if value is not None else None
    return out


def _limit(value: Any, default: int = 100, maximum: int = 200) -> int:
    return max(1, min(_int(value, default) or default, maximum))


# ── 校验 / 规范化 ────────────────────────────────────────────────────────

def _normalize_schedule(kind: str, raw: Any) -> Dict[str, Any]:
    schedule = dict(raw or {}) if isinstance(raw, dict) else {}
    kind = kind if kind in SCHEDULE_KINDS else "daily"
    if kind == "once":
        at = _parse_dt(schedule.get("at"))
        if not at:
            raise ValueError("一次性任务需要 at（YYYY-MM-DD HH:MM）")
        return {"at": _fmt(at)}
    if kind == "interval":
        return {"every_minutes": _clamp_minutes(
            schedule.get("every_minutes"), DEFAULT_INTERVAL_MINUTES)}
    if kind == "daily":
        value = str(schedule.get("time") or "09:00").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", value):
            raise ValueError("每天任务需要 time（HH:MM）")
        hour, minute = (int(part) for part in value.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError("time 超出范围（HH:MM）")
        return {"time": f"{hour:02d}:{minute:02d}"}
    if kind == "weekly":
        value = str(schedule.get("time") or "09:00").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", value):
            raise ValueError("每周任务需要 time（HH:MM）")
        hour, minute = (int(part) for part in value.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError("time 超出范围（HH:MM）")
        weekday = _int(schedule.get("weekday"), 0)
        if weekday < 0 or weekday > 6:
            raise ValueError("weekday 必须是 0（周一）到 6（周日）")
        return {"weekday": weekday, "time": f"{hour:02d}:{minute:02d}"}
    # smart：这里的时间是「多久评估一次」，不是执行时刻。
    return {"every_minutes": _clamp_minutes(
        schedule.get("every_minutes"), DEFAULT_EVAL_MINUTES, MIN_EVAL_MINUTES)}


def _resolve_target(data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    role_id = str(data.get("role_id") or "").strip() or None
    workspace_id = zero_store.public_workspace_id(data.get("workspace_id"))
    if role_id and not zero_store.get_role(role_id):
        raise ValueError(f"角色不存在: {role_id}")
    if workspace_id and not zero_store.get_workspace(workspace_id):
        raise ValueError(f"工作区不存在: {workspace_id}")
    return role_id, workspace_id or DEFAULT_WORKSPACE_ID


def _conversation_exists(conv_id: Optional[str]) -> bool:
    return bool(conv_id and conversation.get_conversation(str(conv_id)))


# ── 下一次运行时间 ───────────────────────────────────────────────────────

def compute_next(task: Dict[str, Any], after: Optional[datetime] = None) -> Optional[str]:
    """按任务配置计算下一次触发（或评估）时间；不适用返回 None。"""
    kind = str(task.get("schedule_kind") or "daily")
    trigger = str(task.get("trigger_mode") or "scheduled")
    if trigger == "manual":
        return None
    schedule = _loads(task.get("schedule"), {}) or {}
    base = (after or _now_dt()).replace(microsecond=0)
    if kind == "once":
        at = _parse_dt(schedule.get("at"))
        if not at:
            return None
        return _fmt(at if at > base else base)
    if kind == "interval":
        minutes = _clamp_minutes(schedule.get("every_minutes"), DEFAULT_INTERVAL_MINUTES)
        return _fmt(base + timedelta(minutes=minutes))
    if kind == "daily":
        hour, minute = _hm(schedule.get("time"), 9, 0)
        candidate = base.replace(hour=hour, minute=minute, second=0)
        if candidate <= base:
            candidate += timedelta(days=1)
        return _fmt(candidate)
    if kind == "weekly":
        hour, minute = _hm(schedule.get("time"), 9, 0)
        weekday = max(0, min(6, _int(schedule.get("weekday"), 0)))
        days = (weekday - base.weekday()) % 7
        candidate = (base + timedelta(days=days)).replace(
            hour=hour, minute=minute, second=0)
        if candidate <= base:
            candidate += timedelta(days=7)
        return _fmt(candidate)
    # smart
    minutes = _clamp_minutes(schedule.get("every_minutes"), DEFAULT_EVAL_MINUTES,
                             MIN_EVAL_MINUTES)
    return _fmt(base + timedelta(minutes=minutes))


def _hm(value: Any, default_hour: int, default_minute: int) -> Tuple[int, int]:
    raw = str(value or "").strip()
    match = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if not match:
        return default_hour, default_minute
    return int(match.group(1)), int(match.group(2))


# ── CRUD ────────────────────────────────────────────────────────────────

def list_tasks(limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
    where = ""
    params: Dict[str, Any] = {"n": _limit(limit)}
    if status in STATUSES:
        where = "WHERE status = :status"
        params["status"] = status
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('scheduled_tasks')} {where} "
                 f"ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, "
                 f"COALESCE(next_run_at, updated_at) ASC, created_at DESC LIMIT :n"),
            params,
        ).mappings().all()
    return [_row(row) for row in rows]


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('scheduled_tasks')} WHERE id = :id"),
            {"id": task_id},
        ).mappings().first()
    return _row(row) if row else None


def create_task(data: Dict[str, Any]) -> Dict[str, Any]:
    title = str(data.get("title") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    if not title:
        raise ValueError("任务标题不能为空")
    if not prompt:
        raise ValueError("任务内容不能为空")
    trigger_mode = str(data.get("trigger_mode") or "scheduled").strip()
    if trigger_mode not in TRIGGER_MODES:
        raise ValueError("trigger_mode 必须是 scheduled / smart / manual")
    schedule_kind = str(data.get("schedule_kind") or "").strip()
    if trigger_mode == "smart":
        schedule_kind = "smart"
    elif not schedule_kind:
        schedule_kind = "daily"
    if schedule_kind not in SCHEDULE_KINDS:
        raise ValueError("schedule_kind 必须是 once / interval / daily / weekly / smart")
    if schedule_kind == "smart":
        trigger_mode = "smart"
    schedule = _normalize_schedule(schedule_kind, data.get("schedule"))
    role_id, workspace_id = _resolve_target(data)
    conversation_id = str(data.get("conversation_id") or "").strip() or None
    if conversation_id and not _conversation_exists(conversation_id):
        raise ValueError(f"会话不存在: {conversation_id}")
    run_mode = str(data.get("run_mode") or "chat").strip()
    if run_mode not in ("chat", "autonomous"):
        raise ValueError("run_mode 必须是 chat / autonomous")
    max_runs = max(0, _int(data.get("max_runs"), 0))
    task = {
        "id": zero_store.new_id("sch"),
        "title": title,
        "prompt": prompt,
        "workspace_id": workspace_id,
        "role_id": role_id,
        "conversation_id": conversation_id,
        "trigger_mode": trigger_mode,
        "schedule_kind": schedule_kind,
        "schedule": schedule,
        "condition": str(data.get("condition") or "").strip(),
        "status": "active",
        "run_mode": run_mode,
        "auto_execute": bool(data.get("auto_execute")),
        "max_runs": max_runs,
        "run_count": 0,
        "meta": data.get("meta") if isinstance(data.get("meta"), dict) else {},
    }
    task["next_run_at"] = compute_next(task)
    if task["next_run_at"] is None and trigger_mode != "manual":
        task["next_run_at"] = _fmt(_now_dt())
    _insert(task)
    zero_store.append_audit(LOCAL_USER_ID, "schedule_create",
                            args={"id": task["id"], "title": title,
                                  "trigger_mode": trigger_mode})
    return get_task(task["id"]) or task


def _insert(task: Dict[str, Any]) -> None:
    now = _now()
    fields = {
        **task,
        "schedule": json.dumps(task.get("schedule") or {}, ensure_ascii=False),
        "meta": json.dumps(task.get("meta") or {}, ensure_ascii=False),
        "created_at": now,
        "updated_at": now,
    }
    columns = ", ".join(f'"{key}"' for key in fields)
    params = ", ".join(f":{key}" for key in fields)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('scheduled_tasks')} ({columns}) VALUES ({params})"),
            fields,
        )
        db.commit()


def update_task(task_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    current = get_task(task_id)
    if not current:
        raise ValueError(f"任务不存在: {task_id}")
    merged = dict(current)
    for key in ("title", "prompt", "condition", "run_mode", "status",
                "trigger_mode", "schedule_kind", "conversation_id",
                "workspace_id", "role_id", "max_runs", "auto_execute", "meta"):
        if key in patch:
            merged[key] = patch[key]
    if merged.get("title") is not None:
        merged["title"] = str(merged.get("title") or "").strip()
    if not str(merged.get("title") or "").strip():
        raise ValueError("任务标题不能为空")
    if not str(merged.get("prompt") or "").strip():
        raise ValueError("任务内容不能为空")
    trigger_mode = str(merged.get("trigger_mode") or "scheduled")
    schedule_kind = str(merged.get("schedule_kind") or "daily")
    if trigger_mode == "smart":
        schedule_kind = "smart"
    if schedule_kind == "smart":
        trigger_mode = "smart"
    if trigger_mode not in TRIGGER_MODES:
        raise ValueError("trigger_mode 必须是 scheduled / smart / manual")
    if schedule_kind not in SCHEDULE_KINDS:
        raise ValueError("schedule_kind 必须是 once / interval / daily / weekly / smart")
    merged["trigger_mode"] = trigger_mode
    merged["schedule_kind"] = schedule_kind
    merged["schedule"] = _normalize_schedule(schedule_kind, merged.get("schedule"))
    merged["run_mode"] = str(merged.get("run_mode") or "chat")
    if merged["run_mode"] not in ("chat", "autonomous"):
        raise ValueError("run_mode 必须是 chat / autonomous")
    if "status" in patch and patch["status"] not in STATUSES:
        raise ValueError("status 必须是 active / paused / done")
    conversation_id = str(merged.get("conversation_id") or "").strip() or None
    if conversation_id and not _conversation_exists(conversation_id):
        raise ValueError(f"会话不存在: {conversation_id}")
    merged["conversation_id"] = conversation_id
    role_id, workspace_id = _resolve_target(merged)
    merged["role_id"] = role_id
    merged["workspace_id"] = workspace_id
    merged["max_runs"] = max(0, _int(merged.get("max_runs"), 0))
    if merged.get("status") == "active":
        merged["next_run_at"] = compute_next(merged, _now_dt())
    elif merged.get("status") in ("paused", "done"):
        merged["next_run_at"] = None
    _update(task_id, merged)
    zero_store.append_audit(LOCAL_USER_ID, "schedule_update",
                            args={"id": task_id, "fields": sorted(patch.keys())})
    return get_task(task_id) or merged


def _update(task_id: str, task: Dict[str, Any]) -> None:
    fields = {
        key: task.get(key) for key in (
            "title", "prompt", "workspace_id", "role_id", "conversation_id",
            "trigger_mode", "schedule_kind", "condition", "status", "run_mode",
            "auto_execute", "max_runs", "run_count", "last_run_at",
            "last_status", "last_run_id", "last_conversation_id", "last_result",
            "next_run_at",
        )
    }
    fields["schedule"] = json.dumps(task.get("schedule") or {}, ensure_ascii=False)
    fields["meta"] = json.dumps(task.get("meta") or {}, ensure_ascii=False)
    fields["updated_at"] = _now()
    sets = ", ".join(f'"{key}" = :{key}' for key in fields)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('scheduled_tasks')} SET {sets} WHERE id = :id"),
            {**fields, "id": task_id},
        )
        db.commit()


def _patch(task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    if not fields:
        return get_task(task_id)
    fields["updated_at"] = _now()
    sets = ", ".join(f'"{key}" = :{key}' for key in fields)
    if "schedule" in fields and isinstance(fields["schedule"], dict):
        fields["schedule"] = json.dumps(fields["schedule"], ensure_ascii=False)
    if "meta" in fields and isinstance(fields["meta"], dict):
        fields["meta"] = json.dumps(fields["meta"], ensure_ascii=False)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('scheduled_tasks')} SET {sets} WHERE id = :id"),
            {**fields, "id": task_id},
        )
        db.commit()
    return get_task(task_id)


def delete_task(task_id: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"DELETE FROM {_q('scheduled_tasks')} WHERE id = :id"),
            {"id": task_id},
        )
        db.commit()
    deleted = bool(res.rowcount)
    if deleted:
        zero_store.append_audit(LOCAL_USER_ID, "schedule_delete",
                                args={"id": task_id})
    return deleted


def pause_task(task_id: str, paused: bool = True) -> Dict[str, Any]:
    current = get_task(task_id)
    if not current:
        raise ValueError(f"任务不存在: {task_id}")
    status = "paused" if paused else "active"
    return update_task(task_id, {"status": status})


# ── 执行 ────────────────────────────────────────────────────────────────

def _last_assistant_text(conv_id: Optional[str]) -> str:
    if not conv_id:
        return ""
    SessionLocal = get_session_local()
    try:
        with SessionLocal() as db:
            value = db.execute(
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
        return ""
    return str(value or "").strip()[:1200]


def _task_conversation(task: Dict[str, Any]) -> Dict[str, Any]:
    """取任务会话；没有就新建（标题固定为「定时 · 标题」）。"""
    conv_id = str(task.get("conversation_id") or task.get("last_conversation_id") or "")
    if conv_id:
        conv = conversation.get_conversation(conv_id)
        if conv:
            return conv
    conv = conversation.create_conversation(
        str(task.get("workspace_id") or DEFAULT_WORKSPACE_ID),
        LOCAL_USER_ID,
        role_id=str(task.get("role_id") or DEFAULT_ROLE_ID),
        source="schedule",
        permission_mode="auto" if task.get("auto_execute") else None,
        run_mode=str(task.get("run_mode") or "chat"),
    )
    try:
        from services.agent_zero.task_layer import title_service

        title_service.rename(conv["id"], f"定时 · {task.get('title') or '任务'}")
    except Exception:
        logger.debug("设置定时任务会话标题失败", exc_info=True)
    return conv


async def run_task(task_id: str, trigger: str = "manual") -> Dict[str, Any]:
    """立即执行一次任务（手动 / 定时 / 自由触发共用）。"""
    task = get_task(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    if trigger == "scheduled" and task.get("status") != "active":
        raise ValueError("任务未启用")
    if task.get("max_runs") and _int(task.get("run_count")) >= _int(task.get("max_runs")):
        _patch(task_id, status="done", next_run_at=None)
        raise ValueError("任务已达到最大运行次数")

    conv = _task_conversation(task)
    conv_id = str(conv["id"])
    prompt = str(task.get("prompt") or "").strip()
    role_id = str(task.get("role_id") or conv.get("role_id") or DEFAULT_ROLE_ID)
    silent = bool((task.get("meta") or {}).get("silent"))
    # 主动消息不写用户气泡：run 直接带内部指令，模型输出会作为 assistant 消息落库。
    message = None if silent else conversation.send_message(
        conv_id, prompt, LOCAL_USER_ID, queued=True)
    run = run_service.create_run(
        conv_id, LOCAL_USER_ID, role_id=role_id, content=prompt,
        mode=str(task.get("run_mode") or "chat"),
        auto_execute=bool(task.get("auto_execute")),
        queued_msg_id=(message or {}).get("id"),
    )
    try:
        await conv_worker.request_run(run["id"])
    except Exception:
        logger.warning("定时任务 run 触发失败，worker 周期会拾取", exc_info=True)

    now = _now_dt()
    run_count = _int(task.get("run_count")) + 1
    status = str(task.get("status") or "active")
    if task.get("schedule_kind") == "once":
        next_run = None
        status = "done"
    else:
        next_run = compute_next(task, now)
    if task.get("max_runs") and run_count >= _int(task.get("max_runs")):
        next_run = None
        status = "done"
    updated = _patch(
        task_id,
        conversation_id=conv_id,
        last_conversation_id=conv_id,
        last_run_id=run["id"],
        last_run_at=_fmt(now),
        last_status="running",
        last_result="",
        run_count=run_count,
        next_run_at=next_run,
        status=status,
        meta={**(task.get("meta") or {}),
              "last_trigger": trigger,
              "last_run_started_at": _fmt(now)},
    )
    zero_store.append_audit(LOCAL_USER_ID, "schedule_run",
                            args={"id": task_id, "run_id": run["id"],
                                  "conversation_id": conv_id, "trigger": trigger})
    return updated or task


def _sync_running(task: Dict[str, Any]) -> Dict[str, Any]:
    """把已结束 run 的状态同步回任务（只更新 running 的那次）。"""
    if task.get("last_status") != "running" or not task.get("last_run_id"):
        return task
    try:
        run = run_service.get_run(str(task["last_run_id"]))
    except Exception:
        return task
    if not run or run.get("status") not in run_service.TERMINAL_STATUSES:
        return task
    status = str(run.get("status") or "")
    result = _last_assistant_text(str(task.get("last_conversation_id") or ""))
    return _patch(task["id"], last_status=status, last_result=result) or task


def _running(task: Dict[str, Any]) -> bool:
    if task.get("last_status") != "running" or not task.get("last_run_id"):
        return False
    try:
        run = run_service.get_run(str(task["last_run_id"]))
    except Exception:
        return False
    return bool(run and run.get("status") not in run_service.TERMINAL_STATUSES)


# ── 主动消息（模型在会话中主动向用户发起）──────────────────────────────

PROACTIVE_KEY = "proactive"
DEFAULT_PROACTIVE_POLICY: Dict[str, Any] = {
    "enabled": True,
    "max_per_day": 6,
    "min_gap_minutes": 10,
    "quiet_start": "",
    "quiet_end": "",
}


def get_proactive_policy() -> Dict[str, Any]:
    from services.agent_zero.stores import settings_store

    saved = settings_store.get(PROACTIVE_KEY, {}) or {}
    policy = dict(DEFAULT_PROACTIVE_POLICY)
    if isinstance(saved, dict):
        policy.update({k: v for k, v in saved.items() if v is not None})
    policy["enabled"] = bool(policy.get("enabled", True))
    policy["max_per_day"] = max(0, min(100, _int(policy.get("max_per_day"), 6)))
    policy["min_gap_minutes"] = max(0, min(1440, _int(policy.get("min_gap_minutes"), 10)))
    policy["quiet_start"] = str(policy.get("quiet_start") or "")[:5]
    policy["quiet_end"] = str(policy.get("quiet_end") or "")[:5]
    return policy


def save_proactive_policy(patch: Dict[str, Any]) -> Dict[str, Any]:
    from services.agent_zero.stores import settings_store

    current = get_proactive_policy()
    for key in DEFAULT_PROACTIVE_POLICY:
        if key in (patch or {}):
            current[key] = (patch or {})[key]
    current["enabled"] = bool(current.get("enabled", True))
    current["max_per_day"] = max(0, min(100, _int(current.get("max_per_day"), 6)))
    current["min_gap_minutes"] = max(0, min(1440, _int(current.get("min_gap_minutes"), 10)))
    current["quiet_start"] = str(current.get("quiet_start") or "")[:5]
    current["quiet_end"] = str(current.get("quiet_end") or "")[:5]
    settings_store.put(PROACTIVE_KEY, current)
    zero_store.append_audit(LOCAL_USER_ID, "proactive_policy", args={"policy": current})
    return current


def _minutes_of_day(value: Any) -> Optional[int]:
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(value or "").strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _in_quiet_hours(policy: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    start = _minutes_of_day(policy.get("quiet_start"))
    end = _minutes_of_day(policy.get("quiet_end"))
    if start is None or end is None or start == end:
        return False
    current = (now or _now_dt())
    minute = current.hour * 60 + current.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def proactive_tasks(conversation_id: Optional[str] = None,
                    limit: int = 50) -> List[Dict[str, Any]]:
    tasks = list_tasks(limit=200)
    out = []
    for task in tasks:
        meta = task.get("meta") or {}
        if meta.get("kind") != "proactive":
            continue
        if conversation_id and str(task.get("conversation_id") or "") != conversation_id:
            continue
        out.append(task)
        if len(out) >= max(1, min(limit, 200)):
            break
    return out


def create_proactive(conv: Dict[str, Any], role: Dict[str, Any], message: str,
                     delay_minutes: Any = 1, reason: str = "") -> Dict[str, Any]:
    """在一条已有会话里安排一条主动消息（不写用户气泡）。"""
    policy = get_proactive_policy()
    if not policy.get("enabled"):
        raise ValueError("全局 Agent 已关闭主动消息")
    if _in_quiet_hours(policy):
        raise ValueError("当前处于主动消息的安静时段")
    text = str(message or "").strip()
    if not text:
        raise ValueError("主动消息内容不能为空")
    conv_id = str(conv.get("id") or "").strip()
    if not conv_id:
        raise ValueError("会话不存在")
    today = _now()[:10]
    today_items = [t for t in proactive_tasks(conv_id, limit=200)
                   if str(t.get("created_at") or "").startswith(today)]
    if len(today_items) >= int(policy.get("max_per_day") or 0):
        raise ValueError(f"今天这个会话的主动消息已达上限（{policy.get('max_per_day')} 条）")
    gap = int(policy.get("min_gap_minutes") or 0)
    if gap and today_items:
        latest = max(_parse_dt(t.get("created_at")) or _now_dt() for t in today_items)
        if (_now_dt() - latest) < timedelta(minutes=gap):
            raise ValueError(f"距上一条主动消息不足 {gap} 分钟")
    delay = max(1, min(1440, _int(delay_minutes, 1)))
    at = _fmt(_now_dt() + timedelta(minutes=delay))
    prompt = (
        "[主动消息] 现在由你主动向用户发起一条消息，不需要等待用户提问。\n"
        f"要点：{text}\n"
        "要求：先给结论或进展，再给必要细节；不要复述这段系统说明；"
        "没有新进展就不要硬凑。"
    )
    task = create_task({
        "title": f"主动 · {conv.get('title') or '会话'}",
        "prompt": prompt,
        "trigger_mode": "scheduled",
        "schedule_kind": "once",
        "schedule": {"at": at},
        "role_id": str(role.get("id") or conv.get("role_id") or DEFAULT_ROLE_ID),
        "workspace_id": str(conv.get("workspace_id") or DEFAULT_WORKSPACE_ID),
        "conversation_id": conv_id,
        "run_mode": "chat",
        "meta": {
            "kind": "proactive",
            "silent": True,
            "reason": str(reason or "")[:500],
            "created_by": str(role.get("id") or ""),
        },
    })
    return task


def cancel_proactive(task_id: str) -> bool:
    task = get_task(task_id)
    if not task or (task.get("meta") or {}).get("kind") != "proactive":
        return False
    return delete_task(task_id)


# ── 自由触发（模型判断）─────────────────────────────────────────────────

def _parse_decision(raw: str) -> Tuple[bool, str]:
    body = str(raw or "").strip()
    match = re.search(r"\{.*\}", body, re.S)
    if match:
        body = match.group(0)
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        low = body.lower()
        return ("trigger" in low and "true" in low), body[:300]
    return bool(data.get("trigger")), str(data.get("reason") or "")[:500]


async def evaluate(task: Dict[str, Any]) -> Tuple[bool, str]:
    """让模型判断自由触发任务现在是否应该运行。失败时保守返回 False。"""
    from services.agent_zero.runtime.zero import llm as llm_mod

    try:
        code = str(llm_mod.get_active_model_code() or "default")
        models = {"summary": code, "loop": code, "fallback": code}
        last = task.get("last_run_at") or "从未运行"
        result = task.get("last_result") or "暂无结果"
        prompt = (
            "你是一个主动任务触发器。请判断是否应该**现在**执行下面的任务。\n"
            "只有在条件成立、且现在执行有价值时才触发；不确定时选择不触发。\n\n"
            f"当前时间：{_now()}\n"
            f"任务标题：{task.get('title') or ''}\n"
            f"任务内容：{task.get('prompt') or ''}\n"
            f"自由触发条件：{task.get('condition') or '根据任务目标判断现在是否值得主动执行'}\n"
            f"上次运行：{last}\n"
            f"上次结果：{result}\n\n"
            '只返回 JSON，不要解释：{"trigger": true 或 false, "reason": "一句话理由"}'
        )
        decision = await asyncio.wait_for(
            llm_mod.ZeroLlm(models).chat_once(
                "summary",
                [{"role": "system",
                  "content": "你只输出一行 JSON，不要 Markdown，不要额外文字。"},
                 {"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=160, timeout=25.0),
            timeout=30.0,
        )
        trigger, reason = _parse_decision(str(getattr(decision, "content", "") or ""))
        logger.info("自由触发评估 task=%s trigger=%s reason=%s",
                    task.get("id"), trigger, reason)
        return trigger, reason
    except Exception as exc:
        logger.warning("自由触发评估失败 task=%s: %s", task.get("id"), exc)
        return False, f"评估失败: {exc}"


# ── 后台 ticker ─────────────────────────────────────────────────────────

def _due_tasks(now: str) -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('scheduled_tasks')} "
                 f"WHERE status = 'active' AND next_run_at IS NOT NULL "
                 f"AND next_run_at <= :now "
                 f"ORDER BY next_run_at ASC LIMIT 20"),
            {"now": now},
        ).mappings().all()
    return [_row(row) for row in rows]


async def tick() -> int:
    """检查一次到期任务；返回本轮处理数量。"""
    async with _TICK_LOCK:
        now_dt = _now_dt()
        now = _fmt(now_dt)
        handled = 0
        for due in _due_tasks(now or ""):
            task = _sync_running(due)
            if task.get("status") != "active":
                continue
            if _running(task):
                _patch(task["id"], next_run_at=_fmt(now_dt + timedelta(minutes=1)))
                continue
            try:
                if task.get("trigger_mode") == "smart":
                    should, reason = await evaluate(task)
                    task = get_task(task["id"]) or task
                    if task.get("status") != "active":
                        continue
                    if should:
                        await run_task(task["id"], trigger="smart")
                    else:
                        _patch(
                            task["id"],
                            next_run_at=compute_next(task, _now_dt()),
                            meta={**(task.get("meta") or {}),
                                  "last_evaluated_at": _fmt(now_dt),
                                  "last_eval_reason": reason},
                        )
                else:
                    await run_task(task["id"], trigger="scheduled")
                handled += 1
            except Exception:
                logger.exception("执行定时任务失败 task=%s", task.get("id"))
                # 失败也要推进下一次，避免同一分钟反复重试把日志刷爆。
                try:
                    _patch(task["id"], next_run_at=compute_next(task, _now_dt()))
                except Exception:
                    logger.debug("推进失败任务时间失败", exc_info=True)
        return handled


async def _ticker_loop() -> None:
    # 启动时先等一个周期：避免应用启动那一秒和首屏请求抢 sqlite。
    await asyncio.sleep(TICK_INTERVAL_S)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("定时任务 tick 失败")
        await asyncio.sleep(TICK_INTERVAL_S)


def start_ticker() -> asyncio.Task:
    global _TICKER
    if _TICKER and not _TICKER.done():
        return _TICKER
    _TICKER = asyncio.create_task(_ticker_loop())
    return _TICKER


async def stop_ticker() -> None:
    global _TICKER
    task = _TICKER
    _TICKER = None
    if not task:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("停止定时任务 ticker 失败", exc_info=True)

"""工单助手 的注意力快照：跨会话待授权 / 未读 / 运行 / 会话授权。

界面（`GET /app/attention`）与工单助手 的 `app_manage` 工具共用这一份数据，
避免两边各写一套查询后口径漂移。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.services import ask_service
from services.agent_zero.stores.zero_store import _q
from services.agent_zero.task_layer import conversation, permissions, run_service


def _limit(value: Any, default: int = 100) -> int:
    try:
        n = int(value or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 200))


def pending_approvals(limit: int = 100) -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT a.*, c.title AS conversation_title "
                 f"FROM {_q('tool_approvals')} a "
                 f"JOIN {_q('conversations')} c ON c.id = a.conversation_id "
                 f"WHERE a.status = 'pending' "
                 f"ORDER BY a.created_at DESC, a.id DESC LIMIT :n"),
            {"n": _limit(limit)},
        ).mappings().all()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("args", "reasons"):
            raw = item.get(key)
            if isinstance(raw, str):
                try:
                    item[key] = json.loads(raw)
                except ValueError:
                    item[key] = [] if key == "reasons" else {}
        out.append(item)
    return out


def pending_questions() -> List[Dict[str, Any]]:
    return ask_service.pending_all()


def conversation_sessions(limit: int = 100) -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, title, role_id, team_id, bound_workspace_id, "
                 f"permission_mode, updated_at FROM {_q('conversations')} "
                 f"WHERE archived = 0 AND COALESCE(zone, '') <> 'global-agent' "
                 f"ORDER BY updated_at DESC, id DESC LIMIT :n"),
            {"n": _limit(limit)},
        ).mappings().all()
    return [dict(r) for r in rows]


def unread_conversations(limit: int = 100) -> List[Dict[str, Any]]:
    """未读会话（最近一条助手消息晚于已读水位）。

    别在这里对每个会话做相关子查询：那是 O(会话数 × 全表) 的扫描，300 个会话
    就要 1s。改成先用一次分组聚合拿到每个会话的「最近助手消息时间」，再与
    会话表 JOIN 过滤；未读条数 / 最后一条预览交给按页批量统计。
    """
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"WITH last_seen AS ("
                 f"  SELECT json_extract(props, '$.conversation_id') AS cid, "
                 f"         MAX(created_at) AS last_at "
                 f"  FROM {_q('gdag_nodes')} "
                 f"  WHERE type = 'fact' "
                 f"    AND json_extract(props, '$.by') = 'assistant' "
                 f"  GROUP BY cid) "
                 f"SELECT c.id, c.title, c.role_id, c.team_id, c.bound_workspace_id, "
                 f"       c.updated_at, c.last_read_at "
                 f"FROM {_q('conversations')} c "
                 f"JOIN last_seen l ON l.cid = c.id "
                 f"WHERE c.archived = 0 "
                 f"  AND COALESCE(c.zone, '') <> 'global-agent' "
                 f"  AND COALESCE(l.last_at, '') > COALESCE(c.last_read_at, '') "
                 f"ORDER BY c.updated_at DESC, c.id DESC LIMIT :n"),
            {"n": _limit(limit)},
        ).mappings().all()
        out = [dict(r) for r in rows]
        conversation._page_message_stats(db, out)
    for item in out:
        item.pop("last_read_at", None)
        item.pop("message_count", None)
        item.pop("unread", None)
    return out


def snapshot(limit: int = 100) -> Dict[str, Any]:
    n = _limit(limit)
    approvals = pending_approvals(n)
    questions = pending_questions()
    sessions = conversation_sessions(n)
    title_by_id = {s["id"]: str(s.get("title") or "") for s in sessions}
    for q in questions:
        q["conversation_title"] = title_by_id.get(str(q.get("conversation_id") or ""), "")
    unread = unread_conversations(n)
    running = run_service.list_active_runs(
        limit=min(n, 100), exclude_zone=conversation.GLOBAL_AGENT_ZONE)
    policy = permissions.get_policy()
    policy["run_mode"] = conversation.last_run_mode()
    counts = {
        "approvals": len(approvals),
        "questions": len(questions),
        "authorizations": len(approvals) + len(questions),
        "unread": len(unread),
        "running": len(running),
        "sessions": len(sessions),
    }
    return {
        "counts": counts,
        "approvals": approvals,
        "questions": questions,
        "unread": unread,
        "running": running,
        "sessions": sessions,
        "policy": policy,
    }

"""AtlasX 存储访问层（本地 sqlite 单用户）。

只保留对话 / 工作区 / 默认角色 / 审计所需的最小集合。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.version import APP_NAME, LEGACY_APP_NAME

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_ID = "default"
DEFAULT_ROLE_ID = "default"


def _q(table: str) -> str:
    return f'"{table}"'


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _json(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)


_JSON_FIELDS = ("member_role_ids", "zones", "origin", "envelope", "mentions",
                "props", "sources", "data", "args", "result", "cost", "value")


def decode_row(row: Any) -> Dict[str, Any]:
    """sqlite 的 text() 查询不会按 JSON 列解码，这里统一还原 JSON 字段。"""
    out = dict(row)
    for field in _JSON_FIELDS:
        val = out.get(field)
        if isinstance(val, str):
            try:
                out[field] = json.loads(val)
            except Exception:
                pass
    return out


def touch_conversation(conv_id: Optional[str]) -> None:
    """刷新对话活跃时间（列表排序用；失败静默）。有活动即解除归档。"""
    if not conv_id:
        return
    try:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            db.execute(
                text(f"UPDATE {_q('conversations')} "
                     f"SET updated_at = datetime('now','localtime'), archived = 0 "
                     f"WHERE id = :id"),
                {"id": conv_id},
            )
            db.commit()
    except Exception as e:
        logger.debug("对话 updated_at 刷新失败 %s: %s", conv_id, e)


# ── 工作区 ───────────────────────────────────────────────────


def create_workspace(kind: str, ws_id: str, name: str, created_by: str,
                     member_role_ids: Optional[list] = None,
                     origin: Optional[dict] = None,
                     zones: Optional[dict] = None,
                     root_path: Optional[str] = None,
                     archived: bool = False) -> None:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('workspaces')} "
                 f"(id, kind, name, member_role_ids, zones, origin, root_path, archived, created_by) "
                 f"VALUES (:id, :kind, :name, :members, :zones, :origin, :root, :archived, :by) "
                 f"ON CONFLICT (id) DO NOTHING"),
            {"id": ws_id, "kind": kind, "name": name,
             "members": _json(member_role_ids) if member_role_ids else None,
             "zones": _json(zones) if zones else None,
             "origin": _json(origin) if origin else None,
             "root": root_path or None,
             "archived": 1 if archived else 0,
             "by": created_by},
        )
        db.commit()


def get_workspace(ws_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('workspaces')} WHERE id = :id"), {"id": ws_id}
        ).mappings().first()
    return decode_row(row) if row else None


def list_workspaces(limit: int = 100, offset: int = 0,
                    include_archived: bool = False,
                    keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """工作区列表；按各工作区最近会话更新时间倒序（归档排在最后），
    附带对话数、最近会话更新时间与最近 6 条会话预览。
    keyword 按名称模糊过滤。"""
    SessionLocal = get_session_local()
    clauses = [] if include_archived else ["w.archived = 0"]
    kw = (keyword or "").strip()
    if kw:
        clauses.append("w.name LIKE :kw")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with SessionLocal() as db:
        rows = db.execute(
            text(
                f"SELECT w.*, "
                f"(SELECT COUNT(*) FROM {_q('conversations')} c "
                f" WHERE c.workspace_id = w.id AND c.archived = 0) AS conversation_count, "
                f"(SELECT MAX(c.updated_at) FROM {_q('conversations')} c "
                f" WHERE c.workspace_id = w.id) AS last_conv_updated, "
                f"(SELECT json_group_array(json_object('id', rc.id, 'title', rc.title, "
                f"'updated_at', rc.updated_at, 'active', rc.is_live)) "
                f"FROM (SELECT c3.id, c3.title, c3.updated_at, "
                f"  (CASE WHEN EXISTS (SELECT 1 FROM {_q('conversation_runs')} r "
                f"    WHERE r.conversation_id = c3.id "
                f"    AND r.status IN ('queued','running','waiting')) "
                f"  OR EXISTS (SELECT 1 FROM {_q('goals')} gg "
                f"    WHERE gg.conversation_id = c3.id AND gg.status = 'active') "
                f"  THEN 1 ELSE 0 END) AS is_live "
                f"  FROM {_q('conversations')} c3 "
                f"  WHERE c3.workspace_id = w.id AND c3.archived = 0 "
                f"  ORDER BY is_live DESC, c3.updated_at DESC LIMIT 6) rc) AS recent_conversations "
                f"FROM {_q('workspaces')} w {where} "
                f"ORDER BY CASE WHEN w.archived THEN 1 ELSE 0 END, "
                f"last_conv_updated DESC, w.created_at DESC, w.id "
                f"LIMIT :limit OFFSET :offset"
            ),
            {"limit": max(1, min(int(limit), 500)), "offset": max(0, int(offset)),
             "kw": f"%{kw}%"},
        ).mappings().all()
    return [decode_row(r) for r in rows]


def count_workspaces(include_archived: bool = False,
                     keyword: Optional[str] = None) -> int:
    SessionLocal = get_session_local()
    clauses = [] if include_archived else ["archived = 0"]
    kw = (keyword or "").strip()
    if kw:
        clauses.append("name LIKE :kw")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT COUNT(*) FROM {_q('workspaces')} {where}"),
            {"kw": f"%{kw}%"},
        ).first()
    return int(row[0]) if row else 0


def update_workspace(ws_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    allowed = {"name", "member_role_ids", "host_role_id", "zones", "root_path", "archived"}
    sets, params = [], {"id": ws_id}
    for k, v in (fields or {}).items():
        if k not in allowed:
            continue
        sets.append(f"{k} = :{k}")
        if k in ("member_role_ids", "zones"):
            params[k] = _json(v)
        elif k == "archived":
            params[k] = 1 if v else 0
        else:
            params[k] = v
    if not sets:
        return get_workspace(ws_id)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('workspaces')} SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        db.commit()
    return get_workspace(ws_id)


# ── 角色 ─────────────────────────────────────────────────────


def list_roles(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, group_id, name, persona, expertise, avatar, envelope, "
                 f"model_code, scope, source, session_id, created_at "
                 f"FROM {_q('roles')} ORDER BY created_at ASC, id "
                 f"LIMIT :limit OFFSET :offset"),
            {"limit": max(1, min(int(limit), 100)), "offset": max(0, int(offset))},
        ).mappings().all()
    return [decode_row(r) for r in rows]


def get_role(role_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT id, group_id, name, persona, expertise, avatar, envelope, "
                 f"model_code, scope, source, session_id "
                 f"FROM {_q('roles')} WHERE id = :id"),
            {"id": role_id},
        ).mappings().first()
    return decode_row(row) if row else None


def upsert_role(row: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(row.get("id") or new_id("role"))
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('roles')} "
                 f"(id, group_id, name, persona, expertise, avatar, envelope, model_code, "
                 f"scope, source, session_id) "
                 f"VALUES (:id, :gid, :name, :persona, :expertise, :avatar, :envelope, "
                 f":model_code, COALESCE(:scope, 'persistent'), "
                 f"COALESCE(:source, 'user'), :session_id) "
                 f"ON CONFLICT (id) DO UPDATE SET "
                 f"group_id = :gid, name = :name, persona = :persona, "
                 f"expertise = :expertise, "
                 f"avatar = CASE WHEN :avatar IS NULL THEN roles.avatar ELSE :avatar END, "
                 f"envelope = :envelope, "
                 f"model_code = CASE WHEN :model_code IS NULL THEN roles.model_code "
                 f"ELSE :model_code END, "
                 f"scope = CASE WHEN :scope IS NULL THEN roles.scope ELSE :scope END, "
                 f"source = CASE WHEN :source IS NULL THEN roles.source ELSE :source END, "
                 f"session_id = CASE WHEN :session_id IS NULL THEN roles.session_id "
                 f"ELSE :session_id END"),
            {"id": rid, "gid": row.get("group_id"),
             "name": str(row.get("name") or "")[:128] or None,
             "persona": row.get("persona"), "expertise": row.get("expertise"),
             "avatar": row.get("avatar") if row.get("avatar") is not None else None,
             "envelope": _json(row.get("envelope") or {}),
             "model_code": (str(row.get("model_code") or "").strip() or None),
             "scope": (str(row.get("scope") or "").strip() or None),
             "source": (str(row.get("source") or "").strip() or None),
             "session_id": row.get("session_id")},
        )
        db.commit()
    return get_role(rid) or {}


def delete_role(role_id: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(text(f"DELETE FROM {_q('roles')} WHERE id = :id"), {"id": role_id})
        db.commit()
    return True


# ── 审计 ─────────────────────────────────────────────────────


def append_audit(actor: str, action: str,
                 args: Optional[dict] = None, result: Optional[dict] = None,
                 cost: Optional[dict] = None) -> None:
    """append-only 审计（旧调用方第一个参数是未使用的 task_id）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('audit_logs')} (actor, action, args, result, cost) "
                 f"VALUES (:actor, :action, :args, :result, :cost)"),
            {"actor": actor or "local", "action": action,
             "args": _json(args) if args is not None else None,
             "result": _json(result) if result is not None else None,
             "cost": _json(cost) if cost is not None else None},
        )
        db.commit()


# ── 默认工作区 / 角色 ────────────────────────────────────────


DEFAULT_TOOLS = [
    "read_file", "read_office", "write_file", "edit_file", "list_files",
    "save_analysis", "ask_user", "shell", "web",
    "memory_read", "memory_write", "workspace", "codegraph",
]

DEFAULT_ENVELOPE = {
    "tools": DEFAULT_TOOLS,
    "data_scope": "camp",
    "network": "allow",
    "egress_allowlist": [],
    "write_paths": ["workspace"],
    "db": {"allowed": [], "read_only": True, "table_whitelist": []},
    "models": {},
    "principles": [],
}


def ensure_defaults() -> Dict[str, Any]:
    """确保默认工作区与默认角色存在（幂等）。"""
    ws = get_workspace(DEFAULT_WORKSPACE_ID)
    if not ws:
        create_workspace(
            "group", DEFAULT_WORKSPACE_ID, "Workspace", "local",
            member_role_ids=[DEFAULT_ROLE_ID],
        )
        update_workspace(DEFAULT_WORKSPACE_ID, {"host_role_id": DEFAULT_ROLE_ID})
    role = get_role(DEFAULT_ROLE_ID)
    if not role:
        upsert_role({
            "id": DEFAULT_ROLE_ID,
            "group_id": DEFAULT_WORKSPACE_ID,
            "name": APP_NAME,
            "persona": "务实、直接、先动手再汇报。",
            "expertise": "本地文件操作、脚本执行、网页抓取与多步任务执行。",
            "envelope": DEFAULT_ENVELOPE,
        })
    elif str(role.get("name") or "") == LEGACY_APP_NAME:
        # 默认角色还是旧品牌名（用户没改过）→ 跟着品牌改名；用户自定义过则不动。
        upsert_role({
            "id": role["id"],
            "group_id": role.get("group_id"),
            "name": APP_NAME,
            "persona": role.get("persona"),
            "expertise": role.get("expertise"),
            "avatar": role.get("avatar"),
            "envelope": role.get("envelope") or DEFAULT_ENVELOPE,
        })
    # 老角色的 envelope 里没有 read_office（Office/PDF 工具是新加的）：补上，
    # 只追加不删除，用户显式收窄的工具清单不会被改动。
    _grant_missing_tools()
    # 已下线的工具（如独立 python 工具）还会留在老库的 envelope 里：顺手清掉，
    # 否则「工具清单」里会一直躺着一个 registry 不存在的 code。
    _drop_retired_tools()
    # 默认工作区是纯聊天工作区：没有 root_path，也不创建工作目录。
    return {"workspace_id": DEFAULT_WORKSPACE_ID, "role_id": DEFAULT_ROLE_ID}


def _drop_retired_tools() -> int:
    """从已有角色 envelope 里删掉 registry 已不存在的工具 code（幂等）。

    只删**本仓库 registry 里没有**的 code：别名先归一（`read_document` →
    `read_office`），归一后仍不存在的才删。用户显式收窄的清单不受影响。
    """
    from services.agent_zero.tools import registry

    known = set(registry.known_codes())
    cleaned = 0
    for role in list_roles(limit=200):
        envelope = dict(role.get("envelope") or {})
        tools = list(envelope.get("tools") or [])
        kept, seen = [], set()
        for t in tools:
            raw = str(t)
            # MCP 工具（mcp__<server>__<tool>）不在 registry 里，不能当残留删掉
            code = raw if raw.startswith("mcp__") else registry.canonical(raw)
            if not code.startswith("mcp__") and code not in known:
                continue
            if code in seen:
                continue
            seen.add(code)
            kept.append(code)
        if kept == tools:
            continue
        envelope["tools"] = kept
        upsert_role({
            "id": role["id"],
            "group_id": role.get("group_id"),
            "name": role.get("name"),
            "persona": role.get("persona"),
            "expertise": role.get("expertise"),
            "avatar": role.get("avatar"),
            "envelope": envelope,
        })
        cleaned += 1
    return cleaned


def _grant_missing_tools(extras=("read_office", "save_analysis", "ask_user")) -> int:
    """给已有角色追加新工具（仅当它已被授予 read_file）。幂等，只加不删。"""
    granted = 0
    for role in list_roles(limit=100):
        envelope = dict(role.get("envelope") or {})
        tools = list(envelope.get("tools") or [])
        if not tools or "read_file" not in tools:
            continue
        missing = [x for x in extras if x not in tools]
        if not missing:
            continue
        envelope["tools"] = tools + missing
        upsert_role({
            "id": role["id"],
            "group_id": role.get("group_id"),
            "name": role.get("name"),
            "persona": role.get("persona"),
            "expertise": role.get("expertise"),
            "avatar": role.get("avatar"),
            "envelope": envelope,
        })
        granted += 1
    return granted

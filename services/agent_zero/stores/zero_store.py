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
# 普通会话的兜底角色叫「通用助手」；AtlasX 只作为全局 Agent（工单助手）的角色存在。
DEFAULT_ROLE_NAME = "通用助手"
GLOBAL_ROLE_ID = "global-agent"
GLOBAL_ROLE_NAME = APP_NAME
SYSTEM_ROLE_IDS = frozenset({DEFAULT_ROLE_ID, GLOBAL_ROLE_ID})


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


# ── 部门 ─────────────────────────────────────────────────────


def list_departments() -> List[Dict[str, Any]]:
    """全部部门（扁平结构，调用方按 parent_id 自己组树）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, name, avatar, parent_id, sort, created_at "
                 f"FROM {_q('departments')} ORDER BY sort ASC, created_at ASC, name ASC"),
        ).mappings().all()
    return [decode_row(r) for r in rows]


def get_department(dept_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT id, name, avatar, parent_id, sort, created_at "
                 f"FROM {_q('departments')} WHERE id = :id"),
            {"id": dept_id},
        ).mappings().first()
    return decode_row(row) if row else None


def department_paths() -> Dict[str, str]:
    """部门 id → 完整路径（`公司 / 产品部 / 设计组`）。"""
    rows = list_departments()
    by_id = {str(r.get("id") or ""): r for r in rows}
    cache: Dict[str, str] = {}

    def path(dept_id: str) -> str:
        dept_id = str(dept_id or "")
        if not dept_id or dept_id not in by_id:
            return ""
        if dept_id in cache:
            return cache[dept_id]
        seen = set()
        names: List[str] = []
        cur = by_id[dept_id]
        while cur and str(cur.get("id") or "") not in seen:
            seen.add(str(cur.get("id") or ""))
            names.append(str(cur.get("name") or ""))
            cur = by_id.get(str(cur.get("parent_id") or ""))
        cache[dept_id] = " / ".join(reversed(names))
        return cache[dept_id]

    return {dept_id: path(dept_id) for dept_id in by_id}


def department_path(dept_id: Optional[str]) -> str:
    if not dept_id:
        return ""
    return department_paths().get(str(dept_id), "")


def role_counts_by_department() -> Dict[str, int]:
    """部门 id → 直属角色数（未安排部门不在此表里）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT department_id, COUNT(*) AS n FROM {_q('roles')} "
                 f"WHERE department_id IS NOT NULL AND department_id != '' "
                 f"GROUP BY department_id"),
        ).all()
    return {str(r[0]): int(r[1] or 0) for r in rows}


def upsert_department(row: Dict[str, Any]) -> Dict[str, Any]:
    dept_id = str(row.get("id") or new_id("dept"))
    parent_id = str(row.get("parent_id") or "").strip() or None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('departments')} "
                 f"(id, name, avatar, parent_id, sort) "
                 f"VALUES (:id, :name, NULLIF(:avatar, ''), :parent, :sort) "
                 f"ON CONFLICT (id) DO UPDATE SET "
                 f"name = :name, "
                 f"avatar = CASE WHEN :avatar IS NULL THEN departments.avatar "
                 f"ELSE NULLIF(:avatar, '') END, "
                 f"parent_id = :parent, sort = :sort"),
            {"id": dept_id, "name": str(row.get("name") or "")[:120] or dept_id,
             "avatar": row.get("avatar"),
             "parent": parent_id, "sort": int(row.get("sort") or 0)},
        )
        db.commit()
    return get_department(dept_id) or {}


def delete_department(dept_id: str) -> Dict[str, Any]:
    """删除部门：子部门上移到父级，部门内角色变为「未安排」。"""
    current = get_department(dept_id)
    if not current:
        return {"deleted": False, "id": dept_id}
    parent_id = current.get("parent_id")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        roles = db.execute(
            text(f"UPDATE {_q('roles')} SET department_id = NULL "
                 f"WHERE department_id = :id"),
            {"id": dept_id},
        ).rowcount
        children = db.execute(
            text(f"UPDATE {_q('departments')} SET parent_id = :parent "
                 f"WHERE parent_id = :id"),
            {"parent": parent_id, "id": dept_id},
        ).rowcount
        db.execute(
            text(f"DELETE FROM {_q('departments')} WHERE id = :id"),
            {"id": dept_id},
        )
        db.commit()
    return {"deleted": True, "id": dept_id,
            "roles_unassigned": max(0, int(roles or 0)),
            "children_moved": max(0, int(children or 0))}


# ── 角色 ─────────────────────────────────────────────────────


def list_roles(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, group_id, name, title, department_id, persona, expertise, "
                 f"avatar, envelope, model_code, scope, source, session_id, created_at "
                 f"FROM {_q('roles')} ORDER BY created_at ASC, id "
                 f"LIMIT :limit OFFSET :offset"),
            {"limit": max(1, min(int(limit), 2000)), "offset": max(0, int(offset))},
        ).mappings().all()
    return [decode_row(r) for r in rows]


def get_role(role_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT id, group_id, name, title, department_id, persona, expertise, "
                 f"avatar, envelope, model_code, scope, source, session_id "
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
                 f"(id, group_id, name, title, department_id, persona, expertise, "
                 f"avatar, envelope, model_code, scope, source, session_id) "
                 f"VALUES (:id, :gid, :name, :title, NULLIF(:department_id, ''), "
                 f":persona, :expertise, :avatar, :envelope, "
                 f"NULLIF(:model_code, ''), COALESCE(:scope, 'persistent'), "
                 f"COALESCE(:source, 'user'), :session_id) "
                 f"ON CONFLICT (id) DO UPDATE SET "
                 f"group_id = :gid, name = :name, persona = :persona, "
                 f"expertise = :expertise, "
                 f"title = CASE WHEN :title IS NULL THEN roles.title ELSE :title END, "
                 f"department_id = CASE WHEN :department_id IS NULL THEN roles.department_id "
                 f"WHEN :department_id = '' THEN NULL ELSE :department_id END, "
                 f"avatar = CASE WHEN :avatar IS NULL THEN roles.avatar ELSE :avatar END, "
                 f"envelope = :envelope, "
                 f"model_code = CASE WHEN :model_code IS NULL THEN roles.model_code "
                 f"ELSE NULLIF(:model_code, '') END, "
                 f"scope = CASE WHEN :scope IS NULL THEN roles.scope ELSE :scope END, "
                 f"source = CASE WHEN :source IS NULL THEN roles.source ELSE :source END, "
                 f"session_id = CASE WHEN :session_id IS NULL THEN roles.session_id "
                 f"ELSE :session_id END"),
            {"id": rid, "gid": row.get("group_id"),
             "name": str(row.get("name") or "")[:128] or None,
             "title": row.get("title"),
             "department_id": row.get("department_id"),
             "persona": row.get("persona"), "expertise": row.get("expertise"),
             "avatar": row.get("avatar") if row.get("avatar") is not None else None,
             "envelope": _json(row.get("envelope") or {}),
             "model_code": (str(row["model_code"]).strip() if row.get("model_code") is not None else None),
             "scope": (str(row.get("scope") or "").strip() or None),
             "source": (str(row.get("source") or "").strip() or None),
             "session_id": row.get("session_id")},
        )
        db.commit()
    return get_role(rid) or {}


def delete_role(role_id: str) -> bool:
    # 角色删除时带走它的专属技能（全局技能不受影响）。
    try:
        from services.agent_zero.services import skill_service

        skill_service.delete_role_skills(role_id)
    except Exception as e:
        logger.debug("清理角色专属技能失败（忽略）: %s", e)
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
    """确保默认工作区与默认角色存在（幂等）。

    - ``DEFAULT_ROLE_ID``：普通会话的兜底角色「通用助手」，不再叫 AtlasX；
    - ``GLOBAL_ROLE_ID``：全局 Agent（工单助手）专用角色，名字是 AtlasX；
    - 历史全局会话如果还绑着默认角色，统一迁到全局角色。
    """
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
            "name": DEFAULT_ROLE_NAME,
            "persona": "务实、直接、先动手再汇报。",
            "expertise": "本地文件操作、脚本执行、网页抓取与多步任务执行。",
            "envelope": DEFAULT_ENVELOPE,
        })
    elif str(role.get("name") or "") in (APP_NAME, LEGACY_APP_NAME):
        # 旧版本把默认角色命名为品牌名；现在 AtlasX 只属于全局 Agent。
        upsert_role({
            "id": role["id"],
            "group_id": role.get("group_id"),
            "name": DEFAULT_ROLE_NAME,
            "persona": role.get("persona"),
            "expertise": role.get("expertise"),
            "avatar": role.get("avatar"),
            "envelope": role.get("envelope") or DEFAULT_ENVELOPE,
        })
    _ensure_global_agent_role()
    _migrate_global_agent_conversations()
    # 老角色的 envelope 里没有 read_office（Office/PDF 工具是新加的）：补上，
    # 只追加不删除，用户显式收窄的工具清单不会被改动。
    _grant_missing_tools()
    # 已下线的工具（如独立 python 工具）还会留在老库的 envelope 里：顺手清掉，
    # 否则「工具清单」里会一直躺着一个 registry 不存在的 code。
    _drop_retired_tools()
    # 默认工作区是纯聊天工作区：没有 root_path，也不创建工作目录。
    return {"workspace_id": DEFAULT_WORKSPACE_ID, "role_id": DEFAULT_ROLE_ID,
            "global_role_id": GLOBAL_ROLE_ID}


def _ensure_global_agent_role() -> None:
    """全局 Agent 角色（AtlasX）只服务 ``zone=global-agent`` 的会话。"""
    role = get_role(GLOBAL_ROLE_ID)
    if not role:
        upsert_role({
            "id": GLOBAL_ROLE_ID,
            "group_id": DEFAULT_WORKSPACE_ID,
            "name": GLOBAL_ROLE_NAME,
            "title": "全局 Agent",
            "persona": "克制、清楚、先结论后动作。代表用户管理整个 App，不执行用户项目代码。",
            "expertise": "跨会话授权、未读、运行状态与组织/角色/群组管理。",
            "envelope": DEFAULT_ENVELOPE,
            "scope": "persistent",
            "source": "system",
        })
        return
    if str(role.get("name") or "") in (LEGACY_APP_NAME, ""):
        upsert_role({
            "id": role["id"],
            "group_id": role.get("group_id"),
            "name": GLOBAL_ROLE_NAME,
            "title": role.get("title") or "全局 Agent",
            "persona": role.get("persona"),
            "expertise": role.get("expertise"),
            "avatar": role.get("avatar"),
            "envelope": role.get("envelope") or DEFAULT_ENVELOPE,
            "scope": "persistent",
            "source": "system",
        })


def _migrate_global_agent_conversations() -> None:
    """历史全局会话（role_id 为空 / 默认角色）改绑到 AtlasX 全局角色。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET role_id = :gid "
                 f"WHERE COALESCE(zone, '') = 'global-agent' "
                 f"AND (role_id IS NULL OR role_id = '' OR role_id = :did)"),
            {"gid": GLOBAL_ROLE_ID, "did": DEFAULT_ROLE_ID},
        )
        db.commit()


def _drop_retired_tools() -> int:
    """从已有角色 envelope 里删掉 registry 已不存在的工具 code（幂等）。

    只删**本仓库 registry 里没有**的 code：别名先归一（`read_document` →
    `read_office`），归一后仍不存在的才删。用户显式收窄的清单不受影响。
    """
    from services.agent_zero.tools import registry

    known = set(registry.known_codes())
    cleaned = 0
    for role in list_roles(limit=2000):
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
    for role in list_roles(limit=2000):
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

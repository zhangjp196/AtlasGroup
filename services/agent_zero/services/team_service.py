"""Agent 团队（工作区级）= IM 里的「群」。

团队既可以由模型通过 `team_manage` 动态创建，也可以由界面通过
`routes/team_routes.py` 管理。对话绑定团队后按 `mode` 执行：

- ``orchestrate``（默认）：主持角色单角色运行，成员通过 `delegate(target=team)`
  被委派子任务；
- ``roundtable``：主持与成员各自应答（`_group_reply_stream`），`@成员` 点名；
- ``single``：团队只作角色预设，指定角色直接跑。

旧的工作区群组（`workspaces.host_role_id` / `member_role_ids`）不再是聊天路由依据。
V3 起只承认显式绑定到会话的团队；`ensure_team_for_conversation` 仅作为
旧数据兼容工具保留，不再在对话时自动调用（否则单聊会被归一成「默认群聊」）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores import zero_store

logger = logging.getLogger(__name__)

_MAX_MEMBERS = 12
# 团队协作模式与圆桌上限（1..12）；默认 4，与旧的 GROUP_MAX_SPEAKERS 对齐。
MODES = ("orchestrate", "roundtable", "single")
KINDS = ("group", "participants")
DEFAULT_MODE = "orchestrate"
DEFAULT_SPEAKERS = 4
MAX_SPEAKERS_LIMIT = 12


def normalize_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in KINDS else "group"


def normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in MODES else DEFAULT_MODE


def normalize_speakers(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SPEAKERS
    return max(1, min(n, MAX_SPEAKERS_LIMIT))


def _q(name: str) -> str:
    return zero_store._q(name)


def _json_list(val: Any) -> List[Any]:
    """JSON 列经 raw SQL 返回为字符串——解析回列表。"""
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return val if isinstance(val, list) else []


def _role_brief(role: Optional[Dict[str, Any]],
                paths: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    if not role:
        return None
    envelope = role.get("envelope") if isinstance(role.get("envelope"), dict) else {}
    tools = envelope.get("tools") if isinstance(envelope.get("tools"), list) else []
    dept_id = str(role.get("department_id") or "")
    paths = paths if paths is not None else zero_store.department_paths()
    return {
        "id": role.get("id"),
        "name": str(role.get("name") or role.get("id") or ""),
        "title": str(role.get("title") or ""),
        "department_id": dept_id,
        "department": paths.get(dept_id, ""),
        "expertise": str(role.get("expertise") or "")[:600],
        "avatar": role.get("avatar") or "",
        "tools": [str(t) for t in tools][:40],
        "model_code": str(role.get("model_code") or ""),
    }


def _envelope_with(envelope: Any) -> Dict[str, Any]:
    """以默认包络为底，合并调用方显式给出的工具子集 / 策略。

    只有**已知工具 code** 会被收进包络（拼错的工具名不会变成死配置）；
    没给 ``tools`` 时保持默认全集，避免老团队创建路径突然收窄能力。
    """
    base = dict(zero_store.DEFAULT_ENVELOPE)
    if not isinstance(envelope, dict):
        return base
    raw_tools = envelope.get("tools")
    if isinstance(raw_tools, list) and raw_tools:
        try:
            from services.agent_zero.tools import registry as _registry

            known = set(_registry.known_codes())
            tools = [_registry.canonical(str(t)) for t in raw_tools if str(t).strip()]
            tools = [t for t in dict.fromkeys(tools) if t in known]
        except Exception:
            tools = [str(t) for t in raw_tools if str(t).strip()]
        if tools:
            base["tools"] = tools
    for key in ("data_scope", "network", "egress_allowlist", "write_paths", "db",
                "models", "principles"):
        if key in envelope and envelope.get(key) is not None:
            base[key] = envelope[key]
    return base


def _ensure_role(ws_id: str, role_id: str, name: str, expertise: str,
                 envelope: Any = None, model_code: Any = None,
                 avatar: Any = None) -> Dict[str, Any]:
    """创建/更新团队成员角色：默认能力包络，可按角色收窄工具 / 指定模型。

    - ``avatar`` 只在显式给出非空值时写入；不带头像 = 保持原值；
    - 已有角色被复用（例如按名字加入群组）时，不覆盖 persona / 标题 / 部门 /
      能力包络等未显式提供的字段，避免把正式角色改成“临时角色”。
    """
    current = zero_store.get_role(role_id) or {}
    is_new = not current
    model = str(model_code or "").strip() or None
    avatar_url = str(avatar or "").strip() or None
    clean_expertise = str(expertise or "").strip()
    payload: Dict[str, Any] = {
        "id": role_id,
        # 新角色跟随群组工作区；已有角色保留自己绑定，避免每次保存群组
        # 都把成员角色刚切换的工作区冲回群组工作区。
        # （群组显式切换工作区时，update_team 会统一迁移成员角色。）
        "group_id": current.get("group_id") if not is_new else ws_id,
        "name": name or current.get("name") or role_id,
        # persona 在 upsert 里是无条件写入，已有角色必须显式回填。
        "persona": current.get("persona") or "",
        "expertise": (clean_expertise[:600] if clean_expertise
                      else current.get("expertise") or ""),
        "envelope": (_envelope_with(envelope) if envelope is not None or is_new
                     else current.get("envelope") or zero_store.DEFAULT_ENVELOPE),
    }
    if is_new:
        # 只有团队自动创建的角色才允许在移出群组时清理；复用的角色库角色不能删。
        payload["source"] = "team"
    if model is not None:
        payload["model_code"] = model
    if avatar_url:
        payload["avatar"] = avatar_url
    return zero_store.upsert_role(payload)


def _team_public(team: Dict[str, Any]) -> Dict[str, Any]:
    paths = zero_store.department_paths()
    leader = _role_brief(zero_store.get_role(str(team.get("leader_role_id") or "")),
                         paths)
    # 系统角色（通用助手 / 全局 AtlasX）不属于任何群聊：旧数据里如果主持是它们，
    # 展示成无 id 的「主持」占位，真正的修复在 normalize_system_roles()。
    if not leader or str(leader.get("id") or "") == zero_store.GLOBAL_ROLE_ID:
        leader = {"id": None, "name": "主持", "expertise": "", "avatar": "",
                  "title": "", "department_id": "", "department": ""}
    # 成员按 id / 名字去重，并排除主持：旧数据可能把主持也写进 member_role_ids，
    # 直接回给前端会显示成两个同名成员。
    leader_id = str(leader.get("id") or "")
    leader_name = str(leader.get("name") or "").strip().lower()
    members = []
    seen_ids: set = set()
    seen_names: set = set()
    for rid in _json_list(team.get("member_role_ids")):
        if str(rid) == zero_store.GLOBAL_ROLE_ID:
            continue
        b = _role_brief(zero_store.get_role(str(rid)), paths)
        if not b:
            continue
        mid = str(b.get("id") or "")
        name = str(b.get("name") or "").strip().lower()
        if not mid or mid in seen_ids:
            continue
        if leader_id and mid == leader_id:
            continue
        if name and (name == leader_name or name in seen_names):
            continue
        seen_ids.add(mid)
        if name:
            seen_names.add(name)
        members.append(b)
    return {
        "id": team.get("id"),
        "kind": "participants" if str(team.get("kind") or "") == "participants" else "group",
        "workspace_id": team.get("workspace_id"),
        "name": team.get("name") or "",
        "avatar": str(team.get("avatar") or ""),
        "description": team.get("description") or "",
        "mode": normalize_mode(team.get("mode")),
        "max_speakers": normalize_speakers(team.get("max_speakers")),
        "leader": leader,
        "members": members,
        "created_at": str(team.get("created_at") or ""),
        "updated_at": str(team.get("updated_at") or ""),
    }


_MODE_NOTICE = {"orchestrate": "主持编排", "roundtable": "圆桌", "single": "单角"}


def _notice(conv_ids: List[str], text: str) -> None:
    """给相关会话写一条 IM 系统消息（失败不影响主流程）。"""
    text = str(text or "").strip()
    if not text:
        return
    try:
        from services.agent_zero.task_layer import conversation

        for cid in conv_ids:
            if cid:
                conversation.append_system_message(str(cid), text)
    except Exception:
        logger.debug("团队系统消息写入失败（忽略）", exc_info=True)


def bound_conversation_ids(team_id: str) -> List[str]:
    rows = None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id FROM {_q('conversations')} WHERE team_id = :tid"),
            {"tid": team_id},
        ).all()
    return [str(r[0]) for r in (rows or [])]


def _fetch_team(team_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('agent_teams')} WHERE id = :id"),
            {"id": team_id},
        ).mappings().first()
    return dict(row) if row else None


def list_teams(ws_id: Optional[str] = None,
               kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """团队列表。``ws_id`` 为空 = 全部（V3：群组独立于工作区）。

    ``kind`` 为空 = 全部（群组 + 会话私有参与角色）；前端群组页只看
    ``kind="group"``，会话侧仍需要拿到私有参与角色来解析成员。
    workspace_id 列只保留为历史来源，不再参与可见性 / 权限判断。
    """
    where: List[str] = []
    params: Dict[str, Any] = {}
    if ws_id:
        where.append("workspace_id = :ws")
        params["ws"] = ws_id
    target_kind = "" if str(kind or "").strip().lower() in ("", "all") \
        else normalize_kind(kind)
    if target_kind:
        where.append("kind = :kind")
        params["kind"] = target_kind
    sql = f"SELECT * FROM {_q('agent_teams')}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(text(sql), params).mappings().all()
    return [_team_public(dict(r)) for r in rows]


def get_team(team_id: str) -> Optional[Dict[str, Any]]:
    row = _fetch_team(team_id)
    return _team_public(row) if row else None


def _role_referenced_elsewhere(role_id: str, team_id: Optional[str]) -> bool:
    """角色是否还被别的团队 / 会话 / 工作区引用（删除前必须检查）。

    共享角色是合法用法：删一个团队不能把另一个团队还在用的角色删掉。
    """
    rid = str(role_id or "").strip()
    if not rid:
        return False
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, leader_role_id, member_role_ids FROM {_q('agent_teams')}"),
        ).mappings().all()
        for row in rows:
            if team_id and str(row["id"]) == str(team_id):
                continue
            if str(row["leader_role_id"] or "") == rid:
                return True
            if rid in [str(x) for x in _json_list(row["member_role_ids"])]:
                return True
        conv = db.execute(
            text(f"SELECT 1 FROM {_q('conversations')} WHERE role_id = :rid LIMIT 1"),
            {"rid": rid},
        ).first()
        if conv:
            return True
        work = db.execute(
            text(f"SELECT 1 FROM {_q('work_items')} WHERE role_id = :rid LIMIT 1"),
            {"rid": rid},
        ).first()
        if work:
            return True
        for table, column in (
            ("conversation_runs", "role_id"),
            ("conversation_runs", "target_role_id"),
            ("team_tasks", "leader_role_id"),
            ("skills", "role_id"),
        ):
            hit = db.execute(
                text(f"SELECT 1 FROM {_q(table)} WHERE {column} = :rid LIMIT 1"),
                {"rid": rid},
            ).first()
            if hit:
                return True
        task_member = db.execute(
            text(f"SELECT 1 FROM {_q('team_tasks')} tt, json_each(tt.members) j "
                 f"WHERE j.value->>'role_id' = :rid LIMIT 1"),
            {"rid": rid},
        ).first()
        if task_member:
            return True
        ws = db.execute(
            text(f"SELECT 1 FROM {_q('workspaces')} WHERE host_role_id = :rid LIMIT 1"),
            {"rid": rid},
        ).first()
        if ws:
            return True
    return False


def _sync_roles(ws_id: str, leader: Dict[str, Any],
                members: List[Dict[str, Any]],
                old_member_ids: List[str], old_leader_id: Optional[str],
                team_id: Optional[str] = None) -> tuple:
    """确保 leader/member 角色存在；删除旧团队角色中不再使用的。"""
    existing_roles = zero_store.list_roles(limit=2000)

    def _find_by_name(spec: Dict[str, Any]) -> str:
        """按名字复用已有持久角色，避免同名角色被重新创建成无头像的新角色。"""
        name = str(spec.get("name") or "").strip().lower()
        if not name:
            return ""
        rows = [
            row for row in existing_roles
            if str(row.get("name") or "").strip().lower() == name
            and str(row.get("id") or "") != zero_store.GLOBAL_ROLE_ID
            and str(row.get("scope") or "persistent") == "persistent"
        ]
        if not rows:
            return ""
        expertise = str(spec.get("expertise") or "").strip().lower()
        if expertise:
            exact = [row for row in rows
                     if str(row.get("expertise") or "").strip().lower() == expertise]
            if exact:
                rows = exact
        # 有头像的正式角色优先，避免同名重复角色里挑到旧的空头像角色。
        rows.sort(key=lambda row: (
            0 if str(row.get("avatar") or "").strip() else 1,
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
        ))
        return str(rows[0].get("id") or "")

    def _resolve(spec: Dict[str, Any], prefix: str) -> str:
        rid = str(spec.get("id") or "").strip()
        # 系统角色不能进入群组：旧数据如果引用它们，就地换成一个新的团队角色。
        if rid == zero_store.GLOBAL_ROLE_ID:
            rid = ""
        if not rid:
            # 模型通常只给 name；优先复用角色库里同名持久角色（保留头像 / 人格）。
            rid = _find_by_name(spec) or zero_store.new_id(prefix)
        env = spec.get("envelope")
        if not isinstance(env, dict) and isinstance(spec.get("tools"), list):
            env = {"tools": spec.get("tools")}
        _ensure_role(ws_id, rid, str(spec.get("name") or "").strip()[:128],
                     str(spec.get("expertise") or "").strip(),
                     envelope=env,
                     model_code=spec.get("model_code") or spec.get("model"),
                     avatar=spec.get("avatar"))
        return rid

    leader_id = _resolve(leader or {}, "teamrole")
    # 成员去重：同一个 id 或同名角色只保留一个，且不能和主持重复。
    # 旧前端可能把主持也勾成成员，或对同一角色重复点「邀请」；这里统一收口。
    member_ids = []
    seen_ids: set = set()
    seen_names: set = set()
    for spec in (members or []):
        spec = spec if isinstance(spec, dict) else {}
        sid = str(spec.get("id") or "").strip()
        sname = str(spec.get("name") or "").strip().lower()
        if sid == zero_store.GLOBAL_ROLE_ID:
            continue
        if sid and (sid == leader_id or sid in seen_ids):
            continue
        if sname and sname in seen_names:
            continue
        rid = _resolve(spec, "teamrole")
        if rid == leader_id or rid in seen_ids:
            continue
        seen_ids.add(rid)
        if sname:
            seen_names.add(sname)
        member_ids.append(rid)
        if len(member_ids) >= _MAX_MEMBERS:
            break
    stale = set(old_member_ids) | ({old_leader_id} if old_leader_id else set())
    stale -= {leader_id} | set(member_ids)
    for rid in stale:
        if not rid or rid in zero_store.SYSTEM_ROLE_IDS:
            continue
        role = zero_store.get_role(rid) or {}
        # 只清理团队自动创建的角色。复用的角色库角色即使暂时没有别的引用，
        # 移出群组也不能从角色库里删掉。
        if str(role.get("source") or "") != "team":
            continue
        if not _role_referenced_elsewhere(rid, team_id):
            zero_store.delete_role(rid)
    return leader_id, member_ids


def create_team(ws_id: str, name: str, description: str = "",
                leader: Optional[Dict[str, Any]] = None,
                members: Optional[List[Dict[str, Any]]] = None,
                mode: Any = None,
                max_speakers: Any = None,
                team_id: Optional[str] = None,
                avatar: Optional[str] = None,
                kind: Any = None) -> Dict[str, Any]:
    name = str(name or "").strip()[:120]
    if not name:
        raise ValueError("团队名称不能为空")
    # V3：群组不再必须属于某个工作区；workspace_id 仅作为历史来源保存。
    ws_id = str(ws_id or "").strip() or zero_store.DEFAULT_WORKSPACE_ID
    if not zero_store.get_workspace(ws_id):
        raise ValueError(f"工作区不存在: {ws_id}")
    team_id = str(team_id or "").strip() or zero_store.new_id("team")
    leader_id, member_ids = _sync_roles(ws_id, leader or {"name": "主持"},
                                        members or [], [], None)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('agent_teams')} "
                 f"(id, kind, workspace_id, name, avatar, description, mode, max_speakers, "
                 f"leader_role_id, member_role_ids) "
                 f"VALUES (:id, :kind, :ws, :name, :avatar, :desc, :mode, :speakers, "
                 f":leader, :members)"),
            {"id": team_id, "kind": normalize_kind(kind), "ws": ws_id, "name": name,
             "avatar": str(avatar or "").strip() or None,
             "desc": str(description or "").strip()[:1000],
             "mode": normalize_mode(mode), "speakers": normalize_speakers(max_speakers),
             "leader": leader_id, "members": zero_store._json(member_ids)},
        )
        db.commit()
    zero_store.append_audit("local", "team_create",
                            args={"workspace_id": ws_id, "team_id": team_id,
                                  "name": name, "members": len(member_ids)})
    return get_team(team_id) or {}


def update_team(team_id: str, name: Optional[str] = None,
                description: Optional[str] = None,
                leader: Optional[Dict[str, Any]] = None,
                members: Optional[List[Dict[str, Any]]] = None,
                mode: Any = None,
                max_speakers: Any = None,
                avatar: Optional[str] = None,
                workspace_id: Optional[str] = None) -> Dict[str, Any]:
    row = _fetch_team(team_id)
    if not row:
        raise ValueError(f"团队不存在: {team_id}")
    ws_id = str(row.get("workspace_id") or "")
    new_ws_id = None
    if workspace_id is not None:
        new_ws_id = str(workspace_id or "").strip() or zero_store.DEFAULT_WORKSPACE_ID
        ws = zero_store.get_workspace(new_ws_id)
        if not ws or ws.get("archived"):
            raise ValueError(f"工作区不存在或已归档: {new_ws_id}")
    leader_id = str(row.get("leader_role_id") or "")
    member_ids = [str(x) for x in _json_list(row.get("member_role_ids"))]
    if name is not None:
        name = str(name).strip()[:120]
        if not name:
            raise ValueError("团队名称不能为空")
    if leader is not None or members is not None:
        leader_id, member_ids = _sync_roles(
            ws_id,
            leader if leader is not None else _role_brief(zero_store.get_role(leader_id)) or {"id": leader_id, "name": "主持", "expertise": ""},
            members if members is not None else [
                _role_brief(zero_store.get_role(mid)) or {"id": mid} for mid in member_ids
            ],
            member_ids, leader_id or None, team_id=team_id)
    old_name = str(row.get("name") or "")
    old_mode = normalize_mode(row.get("mode"))
    old_member_ids = [str(x) for x in _json_list(row.get("member_role_ids"))]
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('agent_teams')} SET "
                 f"workspace_id = CASE WHEN :ws IS NULL THEN workspace_id ELSE :ws END, "
                 f"name = COALESCE(:name, name), "
                 f"avatar = CASE WHEN :avatar IS NULL THEN avatar "
                 f"ELSE NULLIF(:avatar, '') END, "
                 f"description = COALESCE(:desc, description), "
                 f"mode = CASE WHEN :mode IS NULL THEN mode ELSE :mode END, "
                 f"max_speakers = CASE WHEN :speakers IS NULL THEN max_speakers "
                 f"ELSE :speakers END, "
                 f"leader_role_id = :leader, "
                 f"member_role_ids = :members, "
                 f"updated_at = datetime('now','localtime') "
                 f"WHERE id = :id"),
            {"id": team_id, "ws": new_ws_id, "name": name, "avatar": avatar,
             "desc": description,
             "mode": normalize_mode(mode) if mode is not None else None,
             "speakers": normalize_speakers(max_speakers) if max_speakers is not None else None,
             "leader": leader_id, "members": zero_store._json(member_ids)},
        )
        if new_ws_id and new_ws_id != ws_id:
            # 群组换了工作区：绑定它的「跟随」群聊会话跟着迁移；
            # 手动指定工作区的会话保留自己的选择。
            db.execute(
                text(f"UPDATE {_q('conversations')} SET workspace_id = :ws, "
                     f"workspace_custom = 0, "
                     f"updated_at = datetime('now','localtime') WHERE team_id = :id "
                     f"AND COALESCE(workspace_custom, 0) = 0"),
                {"ws": new_ws_id, "id": team_id},
            )
            # 群成员角色也跟随群组工作区（单聊该角色时使用同一目录）。
            role_ids = [rid for rid in ([leader_id] + list(member_ids)) if rid]
            if role_ids:
                marks = ",".join(f":r{i}" for i in range(len(role_ids)))
                db.execute(
                    text(f"UPDATE {_q('roles')} SET group_id = :ws "
                         f"WHERE id IN ({marks})"),
                    {"ws": new_ws_id,
                     **{f"r{i}": rid for i, rid in enumerate(role_ids)}},
                )
        db.commit()
    # IM 系统消息：只对绑定该群的会话发；一次 update 可能同时改多项。
    conv_ids = bound_conversation_ids(team_id)
    if conv_ids:
        new_name = str(name if name is not None else old_name)
        added = [str(x) for x in member_ids if str(x) not in old_member_ids]
        removed = [str(x) for x in old_member_ids if str(x) not in member_ids]
        for rid in added:
            role = zero_store.get_role(rid)
            _notice(conv_ids, f"「{(role or {}).get('name') or rid}」加入了群聊")
        for rid in removed:
            role = zero_store.get_role(rid)
            _notice(conv_ids, f"「{(role or {}).get('name') or rid}」退出了群聊")
        new_mode = normalize_mode(mode) if mode is not None else old_mode
        if new_mode != old_mode:
            _notice(conv_ids, f"协作模式切换为「{_MODE_NOTICE[new_mode]}」")
        if name is not None and new_name != old_name:
            _notice(conv_ids, f"群名改为「{new_name}」")
    return get_team(team_id) or {}


def delete_team(team_id: str) -> bool:
    row = _fetch_team(team_id)
    if not row:
        return False
    bound = bound_conversation_ids(team_id)
    team_name = str(row.get("name") or team_id)
    # 删除团队成员角色（先清对话绑定，FK 无关但保持整洁）
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET team_id = NULL WHERE team_id = :id"),
            {"id": team_id},
        )
        db.commit()
    ids = [str(x) for x in _json_list(row.get("member_role_ids"))]
    leader_id = str(row.get("leader_role_id") or "")
    if leader_id and leader_id not in zero_store.SYSTEM_ROLE_IDS:
        ids.append(leader_id)
    for rid in ids:
        role = zero_store.get_role(rid) or {}
        # 只清理团队自动创建的角色；从角色库选进群的角色必须保留。
        if str(role.get("source") or "") != "team":
            continue
        if (rid and rid not in zero_store.SYSTEM_ROLE_IDS
                and not _role_referenced_elsewhere(rid, team_id)):
            zero_store.delete_role(rid)
    with SessionLocal() as db:
        # 群级永久授权随群一起失效，避免留下指向已删除团队的孤儿记录。
        db.execute(
            text(f"DELETE FROM {_q('permission_scope_grants')} WHERE team_id = :id"),
            {"id": team_id},
        )
        db.execute(text(f"DELETE FROM {_q('agent_teams')} WHERE id = :id"),
                   {"id": team_id})
        db.commit()
    _notice(bound, f"群聊「{team_name}」已解散，你已回到单聊")
    return True


def cleanup_orphan_participants(team_id: str) -> bool:
    """清理不再被任何会话绑定的 participants-* 团队（不删角色库角色）。"""
    tid = str(team_id or "").strip()
    if not tid:
        return False
    row = _fetch_team(tid)
    if not row or str(row.get("kind") or "group") != "participants":
        return False
    if bound_conversation_ids(tid):
        return False
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"DELETE FROM {_q('permission_scope_grants')} WHERE team_id = :id"),
            {"id": tid},
        )
        db.execute(text(f"DELETE FROM {_q('agent_teams')} WHERE id = :id"), {"id": tid})
        db.commit()
    return True


def bind_team(conv_id: str, team_id: Optional[str],
              *, notice: Optional[str] = None,
              allow_participants: bool = False) -> Dict[str, Any]:
    old_team_id = ""
    try:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            row = db.execute(
                text(f"SELECT COALESCE(team_id, '') FROM {_q('conversations')} WHERE id = :id"),
                {"id": conv_id},
            ).first()
        old_team_id = str(row[0] or "") if row else ""
    except Exception:
        old_team_id = ""
    if team_id:
        team = get_team(team_id) or {}
        if str(team.get("kind") or "group") == "participants" and not allow_participants:
            raise ValueError("会话私有参与角色不能作为群组绑定")
        from services.agent_zero.task_layer.conversation import GLOBAL_AGENT_ZONE

        SessionLocal = get_session_local()
        with SessionLocal() as db:
            row = db.execute(
                text(f"SELECT COALESCE(zone, '') FROM {_q('conversations')} "
                     f"WHERE id = :id"),
                {"id": conv_id},
            ).first()
        if row and str(row[0] or "") == GLOBAL_AGENT_ZONE:
            raise ValueError("全局 Agent 不能加入群组")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET team_id = :tid, "
                 f"updated_at = datetime('now','localtime') WHERE id = :id"),
            {"tid": team_id or None, "id": conv_id},
        )
        db.commit()
    try:
        from services.agent_zero.task_layer import conversation

        conversation.sync_conversation_workspace(conv_id)
    except Exception:
        logger.debug("同步会话工作区失败（忽略）", exc_info=True)
    if notice:
        _notice([conv_id], notice)
    elif team_id:
        team = get_team(team_id)
        _notice([conv_id], f"你已加入群聊「{(team or {}).get('name') or team_id}」")
    if old_team_id and old_team_id != str(team_id or ""):
        cleanup_orphan_participants(old_team_id)
    return {"bound": bool(team_id), "team_id": team_id}


def team_for_conv(conv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tid = str(conv.get("team_id") or "").strip()
    if not tid:
        return None
    team = get_team(tid)
    if not team:
        return None
    # 只有主持、没有成员 = 不是群聊。旧版本可能把这种「默认团队」绑到单聊上；
    # 这里统一按单聊处理，避免单聊被显示成「默认群聊」或出现主持口吻。
    if not (team.get("members") or []):
        return None
    # V3：群组独立，不要求与会话同工作区（workspace_id 仅历史来源）。
    return team


_MODE_HINTS = {
    "orchestrate": (
        "执行方式：收到任务后先判断**谁真的需要参与**，只挑 1–2 个与任务直接相关、"
        "专长匹配的成员；不要为了「覆盖」把全体成员都派上，与任务无关的成员不 @、"
        "不 delegate。确有必要一次派多人时，仍然只选最相关的少数几个（受本群"
        "「单轮参与上限」约束），并按重要性排序。"
        "用一次 `delegate(target=team, assignments=[{member_id, goal}, ...])` 批量派发，"
        "不要用多次单发、也不要逐个等待。派发时你直接 @ 成员，由成员自己回复；"
        "不要以用户身份转发消息，也不要说「我帮你转达」。"
        "**分流规则**：只是征求成员意见 / 让成员说一句看法 → 直接 `@成员`，"
        "成员会在会话里回复；需要成员动手做事（写代码、跑命令、查资料并产出结果、"
        "长时间任务）→ 必须用 `delegate` 明确登记为**后台任务**，并在正文里说明"
        "「已创建后台任务」，不要在正文里用 @ 代替派发。"
        "用户只是点名 / 打招呼（如「X 在吗」）时，直接 @X 把话交给它，"
        "自己只做一句简短说明，不要替成员回答或展开长文。"
        "**派发后立即收尾**：只说明分工与下一步，不要再自己 shell / 读文件 / 改代码"
        "去重复执行同一个任务；等用户在会话里点「继续」再整合、校验与最终汇报。"
    ),
    "roundtable": (
        "执行方式：这是圆桌群聊——你与成员会各自应答；用户用 `@成员名` 点名时由该成员回复。"
        "你回复里 @ 成员 = 请它接一句；只 @ 与当前话题直接相关的 1–2 个成员，"
        "不要每轮把全员都 @ 上。要让它动手做事（工具 / 长任务）时，"
        "明确写「后台任务」或改用 `delegate` 派发，不要在正文里让它干等。"
        "只说你自己的判断，不要替成员转发或编造他们的结论。"
    ),
    "single": (
        "执行方式：团队只作角色预设，本轮只有你在应答；不需要委派成员。"
    ),
}


def roster_text(team: Dict[str, Any],
                role: Optional[Dict[str, Any]] = None) -> str:
    """团队名单 → 注入系统提示的正文（含模式、成员工具子集与模型）。

    ``role`` 用来区分视角：主持看到完整名单与编排职责；成员只看到自己是谁、
    以及「圆桌里等 @ / 编排里等委派」的行为约束——否则成员也会被提示成主持。
    """
    mode = normalize_mode(team.get("mode"))
    leader_id = str(((team.get("leader") or {}).get("id")) or "")
    mine = str((role or {}).get("id") or "")
    if mine and leader_id and mine != leader_id:
        name = str((role or {}).get("name") or mine)
        lines = [f"你是团队「{team['name']}」的成员「{name}」。"]
        title = str((role or {}).get("title") or "").strip()
        department = str((role or {}).get("department")
                         or zero_store.department_path((role or {}).get("department_id"))
                         or "").strip()
        if title:
            lines.append(f"你的岗位：{title}")
        if department:
            lines.append(f"你所属部门：{department}")
        expertise = str((role or {}).get("expertise") or "").strip()
        if expertise:
            lines.append(f"你的专长：{expertise}")
        if mode == "roundtable":
            lines.append(
                "协作模式：`roundtable`（圆桌群聊）——用户或其它角色 `@你` 时由你直接回应；"
                "只讲你自己的判断，不要替主持或其它成员发言。")
        else:
            lines.append(
                "协作模式：`orchestrate`（主持编排）——你处理主持委派给你的子任务；"
                "用户或其它角色 `@你` 时直接回应，不要转给主持；"
                "需要其它角色配合时，直接 `delegate(target=team, assignments=[...])` @ 对方，"
                "不要转给用户或主持；没有委派或点名时不要主动发言。")
        return "\n".join(lines)
    lines = [f"你是团队「{team['name']}」的**主持**，负责编排与收尾。"]
    leader = team.get("leader") or {}
    if str(leader.get("title") or "").strip():
        lines.append(f"你的岗位：{str(leader.get('title')).strip()}")
    if str(leader.get("department") or "").strip():
        lines.append(f"你所属部门：{str(leader.get('department')).strip()}")
    if team.get("description"):
        lines.append(f"团队目标：{team['description']}")
    lines.append(f"协作模式：`{mode}`（{_MODE_HINTS[mode]}）")
    for m in team.get("members") or []:
        bits = []
        title = str(m.get("title") or "").strip()
        if title:
            bits.append(f"岗位：{title}")
        department = str(m.get("department") or "").strip()
        if department:
            bits.append(f"部门：{department}")
        exp = str(m.get("expertise") or "").strip()
        if exp:
            bits.append(f"专长：{exp}")
        tools = [str(t) for t in (m.get("tools") or []) if str(t).strip()]
        if tools:
            shown = "、".join(tools[:8]) + ("…" if len(tools) > 8 else "")
            bits.append(f"工具：{shown}")
        model = str(m.get("model_code") or "").strip()
        if model:
            bits.append(f"模型：{model}")
        suffix = f"（{'；'.join(bits)}）" if bits else ""
        lines.append(f"- 成员「{m['name']}」{suffix}")
    if not team.get("members"):
        lines.append("- （暂无成员）")
    else:
        participants = normalize_speakers(team.get("max_speakers"))
        lines.append(
            f"派发任务：先判断谁真的需要参与，**默认只挑 1–2 个专长匹配的成员**；"
            f"本轮最多 {participants} 名成员参与，不要每轮把全员都派上。"
            "确需多人时用 `delegate(target=team, assignments=[...])` 一次派发最相关的少数几个"
            "（按重要性排序），各成员会并行执行，结果回到群聊后由你整合。"
            "你直接 @ 成员派活，不要以用户身份转发，也不要说「我帮你转达」。"
            "分清两种：只征求一句意见 → 直接 `@成员`（成员在会话里回复）；"
            "需要成员动手做事（工具 / 长任务 / 产出结果）→ 用 `delegate` 登记为后台任务，"
            "并在正文里说明「已创建后台任务」，不要用 @ 代替真正的派发。"
            "**派发后本轮就收尾**：不要自己再执行同一个任务；成员完成、用户点「继续」后再整合。"
        )
    return "\n".join(lines)


def default_team_id(ws_id: str) -> str:
    """旧工作区群组归一后的默认团队 id（确定性，便于幂等）。"""
    return f"team-default-{ws_id}"


def ensure_workspace_team(ws_id: str, *, bind_conversation: Optional[str] = None
                          ) -> Optional[Dict[str, Any]]:
    """把旧的工作区群组（host + members）归一成一个默认团队，幂等。

    - 只认工作区里**已存在**的角色；工作区没有群组信号时返回 None；
    - 已存在默认团队时直接复用（不覆盖用户后来改过的名字 / 模式 / 成员）；
    - ``bind_conversation``：会话还没有 team_id 时顺手绑上。
    """
    ws_id = str(ws_id or "").strip()
    if not ws_id:
        return None
    tid = default_team_id(ws_id)
    existing = get_team(tid)
    if existing:
        if bind_conversation:
            _bind_if_empty(bind_conversation, tid)
        return existing
    ws = zero_store.get_workspace(ws_id)
    if not ws:
        return None
    host = str(ws.get("host_role_id") or "").strip()
    member_ids = [str(x) for x in _json_list(ws.get("member_role_ids")) if str(x).strip()]
    # 主持必须是真实团队角色；系统角色（通用助手 / 全局 AtlasX）不能当群主持，
    # 否则单聊/空群会反复出现 AtlasX。
    leader_id = host if (host and host != zero_store.GLOBAL_ROLE_ID
                         and zero_store.get_role(host)) else ""
    if not leader_id:
        return None
    members = [rid for rid in dict.fromkeys(member_ids)
               if rid != leader_id and rid != zero_store.GLOBAL_ROLE_ID
               and zero_store.get_role(rid)]
    # V3：工作区只是文件容器。「主持 + 至少一个成员」才算旧群组的群聊信号。
    # 只有默认角色（新添加的文件夹工作区 / 默认工作区）不建默认群聊——
    # 否则一条单聊会被归一成「默认群聊」。
    if not members:
        return None
    name = str(ws.get("name") or "").strip()
    if not name or name in ("Workspace", "工作区", "默认工作区"):
        name = "默认群聊"
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('agent_teams')} "
                 f"(id, workspace_id, name, description, mode, max_speakers, "
                 f"leader_role_id, member_role_ids) "
                 f"VALUES (:id, :ws, :name, :desc, :mode, :speakers, :leader, :members) "
                 f"ON CONFLICT (id) DO NOTHING"),
            {"id": tid, "ws": ws_id, "name": name[:120],
             "desc": "由旧工作区群组自动归一（host + member_role_ids）",
             "mode": DEFAULT_MODE, "speakers": DEFAULT_SPEAKERS,
             "leader": leader_id, "members": zero_store._json(members)},
        )
        db.commit()
    team = get_team(tid)
    if team and bind_conversation:
        _bind_if_empty(bind_conversation, tid)
    return team


def _bind_if_empty(conv_id: str, team_id: str) -> None:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET team_id = :tid, "
                 f"updated_at = datetime('now','localtime') "
                 f"WHERE id = :id AND (team_id IS NULL OR team_id = '')"),
            {"tid": team_id, "id": conv_id},
        )
        db.commit()


def ensure_team_for_conversation(conv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """会话的有效团队：已绑定直接用；没绑定则按工作区群组惰性归一并绑定。"""
    team = team_for_conv(conv)
    if team:
        return team
    if str(conv.get("team_id") or "").strip():
        return None  # 绑了一个不存在的团队：不自动改绑，避免静默串群
    team = ensure_workspace_team(str(conv.get("workspace_id") or ""),
                                 bind_conversation=str(conv.get("id") or ""))
    if team:
        conv["team_id"] = team["id"]
    return team


def cleanup_phantom_default_teams() -> int:
    """清掉旧版本按工作区自动归一的「默认群聊」。

    旧行为把 `workspaces.host_role_id / member_role_ids` 惰性归一成
    `team-default-<ws>` 团队并绑到会话上，于是**一条单聊也变成了群聊**
    （标题变成「默认群聊」、输入框出现 `@成员`、系统提示里写着「你是主持」）。
    V3 里只有用户显式建的群组才算群聊：`team-default-*` 是旧版自动产物，
    全部解绑并删除；用户自建群组的 id 不是这个前缀，不受影响。幂等。
    """
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id FROM {_q('agent_teams')} WHERE id LIKE :pat"),
            {"pat": "team-default-%"},
        ).fetchall()
        legacy = [str(r[0]) for r in rows]
        if not legacy:
            return 0
        marks = ",".join(f":t{i}" for i in range(len(legacy)))
        params = {f"t{i}": tid for i, tid in enumerate(legacy)}
        db.execute(
            text(f"UPDATE {_q('conversations')} SET team_id = NULL, "
                 f"updated_at = datetime('now','localtime') "
                 f"WHERE team_id IN ({marks})"),
            params,
        )
        db.execute(
            text(f"DELETE FROM {_q('agent_teams')} WHERE id IN ({marks})"),
            params,
        )
        db.commit()
    return len(legacy)


def detach_global_agent_conversations() -> int:
    """解除全局 Agent 会话与群组的绑定（幂等）。

    全局 Agent 只处理审批，不是群聊。旧版本里模型用 `team_manage(create)`
    建群时会默认绑定当前会话，于是全局 Agent 会话带上了 team_id：
    群组页「打开群聊」复用它，标题被显示成「全局 Agent」，群头像 / 群成员
    也会串到全局 Agent 上。这里只清掉会话上的绑定，群组本身保留；
    用户从群组页进入时会新建一条普通群会话。
    """
    from services.agent_zero.task_layer.conversation import GLOBAL_AGENT_ZONE

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} SET team_id = NULL, "
                 f"updated_at = datetime('now','localtime') "
                 f"WHERE COALESCE(zone, '') = :zone AND team_id IS NOT NULL"),
            {"zone": GLOBAL_AGENT_ZONE},
        )
        db.commit()
        return int(res.rowcount or 0)


def repair_team_member_avatars() -> int:
    """修复旧版本“按名字加群”生成的无头像角色（幂等）。

    旧逻辑只拿到成员名字就新建角色，导致角色库里有头像的正式角色被替换成
    一个没有头像 / 人格的新角色。这里把无头像的主持 / 成员换回角色库里同名
    且有头像的持久角色，并清理不再被引用的重复角色。
    """
    roles = zero_store.list_roles(limit=2000)
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for role in roles:
        rid = str(role.get("id") or "")
        name = str(role.get("name") or "").strip().lower()
        if (not rid or not name or rid == zero_store.GLOBAL_ROLE_ID
                or not str(role.get("avatar") or "").strip()
                or str(role.get("scope") or "persistent") != "persistent"):
            continue
        by_name.setdefault(name, []).append(role)
    if not by_name:
        return 0

    SessionLocal = get_session_local()
    fixed = 0
    removed: set = set()
    replacements: Dict[str, str] = {}
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('agent_teams')}"),
        ).mappings().all()
        for row in rows:
            team = dict(row)
            leader_id = str(team.get("leader_role_id") or "")
            member_ids = [str(x) for x in _json_list(team.get("member_role_ids"))]
            if not leader_id and not member_ids:
                continue

            changed = False

            def _avatarless_target(role_id: str) -> str:
                role = zero_store.get_role(role_id) or {}
                name = str(role.get("name") or "").strip().lower()
                if not name or str(role.get("avatar") or "").strip():
                    return ""
                candidates = by_name.get(name) or []
                target = str(candidates[0].get("id") or "") if candidates else ""
                return target if target and target != role_id else ""

            leader_target = _avatarless_target(leader_id) if leader_id else ""
            if leader_target:
                removed.add(leader_id)
                replacements[leader_id] = leader_target
                leader_id = leader_target
                changed = True

            seen = {leader_id} if leader_id else set()
            new_ids: List[str] = []
            for mid in member_ids:
                target = _avatarless_target(mid)
                if target:
                    changed = True
                    replacements[mid] = target
                    if target not in seen:
                        new_ids.append(target)
                        seen.add(target)
                    removed.add(mid)
                    continue
                if mid not in seen:
                    new_ids.append(mid)
                    seen.add(mid)
            # 如果新主持原本在成员里，成员列表要去重。
            new_ids = [mid for mid in new_ids if mid != leader_id]
            if changed:
                db.execute(
                    text(f"UPDATE {_q('agent_teams')} SET leader_role_id = :leader, "
                         f"member_role_ids = :members, "
                         f"updated_at = datetime('now','localtime') WHERE id = :id"),
                    {"leader": leader_id or None,
                     "members": zero_store._json(new_ids), "id": team.get("id")},
                )
                fixed += 1
        # 会话 / 工单如果引用了被替换的无头像角色，也改回正式角色。
        for old_id, new_id in replacements.items():
            db.execute(
                text(f"UPDATE {_q('conversations')} SET role_id = :new "
                     f"WHERE role_id = :old"),
                {"new": new_id, "old": old_id},
            )
            db.execute(
                text(f"UPDATE {_q('work_items')} SET role_id = :new "
                     f"WHERE role_id = :old"),
                {"new": new_id, "old": old_id},
            )
            db.execute(
                text(f"UPDATE {_q('workspaces')} SET host_role_id = :new "
                     f"WHERE host_role_id = :old"),
                {"new": new_id, "old": old_id},
            )
        db.commit()

    for rid in removed:
        if not rid or rid in zero_store.SYSTEM_ROLE_IDS:
            continue
        role = zero_store.get_role(rid) or {}
        # 只清理看起来是自动生成的角色：团队创建，或没有岗位 / 部门 / 人格。
        looks_auto = str(role.get("source") or "") == "team" or not any(
            str(role.get(key) or "").strip()
            for key in ("persona", "title", "department_id"))
        if not looks_auto:
            continue
        if not _role_referenced_elsewhere(rid, None):
            zero_store.delete_role(rid)
    return fixed


def normalize_system_roles() -> int:
    """把全局 AtlasX 从所有群组里清出去（幂等）。

    - 成员里的 ``GLOBAL_ROLE_ID`` 直接移除；
    - 主持是全局角色时，新建一个同名「主持」团队角色顶替，避免群聊路由落回
      全局 Agent（这正是旧版本「群聊里没人成员却总出现 AtlasX」的来源）。
    普通兜底角色「通用助手」仍可作为群成员，它不是 AtlasX。
    返回被修复的群组数。
    """
    SessionLocal = get_session_local()
    fixed = 0
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('agent_teams')}"),
        ).mappings().all()
        for row in rows:
            team = dict(row)
            ws_id = str(team.get("workspace_id") or "") or zero_store.DEFAULT_WORKSPACE_ID
            leader_id = str(team.get("leader_role_id") or "")
            member_ids = [str(x) for x in _json_list(team.get("member_role_ids"))]
            new_members = [rid for rid in member_ids
                           if rid and rid != zero_store.GLOBAL_ROLE_ID
                           and rid != leader_id]
            new_leader = leader_id
            if (not new_leader or new_leader == zero_store.GLOBAL_ROLE_ID
                    or not zero_store.get_role(new_leader)):
                # 旧群组的主持是全局角色 / 已删除：换成一个真实的「主持」角色。
                new_leader = zero_store.new_id("teamrole")
                _ensure_role(ws_id, new_leader, "主持", "群聊主持与任务编排")
                new_members = [rid for rid in new_members if rid != new_leader]
            if new_leader == leader_id and new_members == member_ids:
                continue
            db.execute(
                text(f"UPDATE {_q('agent_teams')} SET leader_role_id = :leader, "
                     f"member_role_ids = :members, "
                     f"updated_at = datetime('now','localtime') WHERE id = :id"),
                {"leader": new_leader, "members": zero_store._json(new_members),
                 "id": team.get("id")},
            )
            fixed += 1
        db.commit()
    return fixed

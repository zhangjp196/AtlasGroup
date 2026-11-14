"""对话入口：群工作区 / 角色 / 分区，两段式。

探索态：轻量上下文（GDAG fact 节点），不建任务；
转任务：探索上下文蒸馏为规划先验（constraints.exploration_prior）。
消息体落 GDAG，不建消息表。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from services.agent_zero.runtime.zero import file_ops
from services.agent_zero.runtime.zero import file_tools
from services.agent_zero.services import attachment_service
from services.agent_zero.stores import zero_store
from services.agent_zero.version import APP_NAME

logger = logging.getLogger(__name__)

GLOBAL_AGENT_ZONE = "global-agent"
GLOBAL_CHAT_ZONE = "global-chat"


def validate_conversation_workspace(workspace_id: Optional[str]) -> str:
    ws_id = zero_store.public_workspace_id(workspace_id)
    if ws_id:
        row = zero_store.get_workspace(ws_id)
        if not row:
            raise ValueError(f"工作区不存在: {ws_id}")
        if row.get("archived"):
            raise ValueError(f"工作区已归档: {ws_id}")
    return ws_id


def set_conversation_workspace(conv_id: str,
                               workspace_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """保存会话自己的工作区，空值解除绑定。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    ws_id = validate_conversation_workspace(workspace_id)
    with get_session_local()() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET workspace_id = :ws, "
                 f"workspace_custom = :custom, bound_workspace_id = NULL, "
                 f"updated_at = datetime('now','localtime') WHERE id = :id"),
            {"ws": ws_id or zero_store.DEFAULT_WORKSPACE_ID,
             "custom": int(bool(ws_id)), "id": conv_id},
        )
        db.commit()
    return get_conversation(conv_id)


def migrate_bound_workspaces() -> int:
    """把旧版 bound_workspace_id 迁移到会话自己的 workspace_id（幂等）。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} SET workspace_id = bound_workspace_id, "
                 f"workspace_custom = 1, "
                 f"bound_workspace_id = NULL, updated_at = datetime('now','localtime') "
                 f"WHERE COALESCE(bound_workspace_id, '') <> '' "
                 f"AND EXISTS (SELECT 1 FROM {_q('workspaces')} w "
                 f"  WHERE w.id = {_q('conversations')}.bound_workspace_id)"),
        )
        db.commit()
        return int(res.rowcount or 0)


def create_conversation(workspace_id: str, user: str,
                        role_id: Optional[str] = None,
                        zone: Optional[str] = None,
                        source: str = "chat",
                        team_id: Optional[str] = None, bound_workspace_id: Optional[str] = None,
                        model_code: Optional[str] = None,
                        thinking_level: Optional[str] = None,
                        permission_mode: Optional[str] = None,
                        run_mode: Optional[str] = None,
                        session_id: Optional[str] = None) -> Dict[str, Any]:
    """发起对话（本地单用户）。可绑定团队。

    `session_id` 指向会话根：普通会话为空（自己就是根），窗口里新增的「栏目」
    填根会话 id，各自持有独立的消息与上下文，但共用同一个会话卡片。
    """
    zone = str(zone or "").strip() or None
    role_id = str(role_id or "").strip() or None
    session_id = str(session_id or "").strip() or None
    session_root: Optional[Dict[str, Any]] = None
    if session_id:
        session_root = get_conversation(session_id)
        if not session_root:
            raise ValueError(f"栏目所属会话不存在: {session_id}")
        # 传进来的是栏目时归一到根，避免出现「栏目的栏目」。
        session_id = str(session_root.get("session_id") or session_root.get("id") or "")
        if not session_id:
            raise ValueError("栏目所属会话无效")
        role_id = role_id or session_root.get("role_id")
        zone = zone or session_root.get("zone")
        workspace_id = workspace_id or str(session_root.get("workspace_id") or "")
        model_code = model_code or session_root.get("model_code")
        thinking_level = thinking_level or session_root.get("thinking_level")
        permission_mode = permission_mode or session_root.get("permission_mode")
        run_mode = run_mode or session_root.get("run_mode")
    if zone == GLOBAL_AGENT_ZONE:
        # 全局 Agent 是 AtlasX 的唯一入口，且它本身不是群聊：
        # 即使调用方带了 team_id 也不能绑定，否则群组页会把这条会话当成群聊，
        # 显示成「全局 Agent」并让群头像 / 群成员串到全局 Agent 上。
        role_id = role_id or zero_store.GLOBAL_ROLE_ID
        team_id = None
    elif role_id == zero_store.GLOBAL_ROLE_ID:
        # 普通会话不能绑定全局角色。
        role_id = None

    # 群组 = 用户可管理的共享花名册；participants-* 是会话私有参与角色，
    # 只能由 participants 接口内部绑定，不能作为普通会话的 team_id 传进来。
    if team_id:
        from services.agent_zero.services import team_service

        team = team_service.get_team(str(team_id)) or {}
        if not team:
            raise ValueError(f"群组不存在: {team_id}")
        if str(team.get("kind") or "group") == "participants":
            raise ValueError("会话私有参与角色不能作为群组绑定")
        # 群聊的主持由群组决定：调用方没显式指定角色时，跟随群主。
        if not role_id:
            leader = team.get("leader") or {}
            role_id = str(leader.get("id") or "").strip() or None

    # 共享群组是长期实体：栏目继续留在同一个群里。
    # 会话私有参与角色（participants-*）不在这里继承——它绑定单条会话，
    # 分支会自己复制一份，普通栏目沿用调用方传入的团队。
    if not team_id and session_root:
        inherited_team = str(session_root.get("team_id") or "")
        if inherited_team and not inherited_team.startswith("participants-"):
            from services.agent_zero.services import team_service

            team = team_service.get_team(inherited_team) or {}
            if str(team.get("kind") or "group") == "group":
                team_id = inherited_team

    workspace_id = validate_conversation_workspace(bound_workspace_id or workspace_id)
    workspace_custom = bool(workspace_id)
    bound_workspace_id = None
    conv_id = zero_store.new_id("conv")
    from services.agent_zero.database import get_session_local

    SessionLocal = get_session_local()
    from sqlalchemy import text
    from services.agent_zero.stores.zero_store import _q
    from services.agent_zero.task_layer import permissions

    # 新对话继承上一条对话的权限档位与运行模式（默认「询问」/ agent）；
    # composer 在新建前已选好的档位 / 模式由调用方显式传入，优先采用。
    mode = str(permission_mode or "").strip()
    if mode not in permissions.MODES:
        mode = permissions.last_mode()
    run_mode = str(run_mode or "").strip()
    if run_mode not in ("chat", "autonomous", "goal"):
        run_mode = last_run_mode()

    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('conversations')} "
                 f"(id, workspace_id, bound_workspace_id, workspace_custom, role_id, zone, "
                 f"created_by, source, session_id, "
                 f"permission_mode, run_mode, team_id, model_code, thinking_level, last_read_at) "
                 f"VALUES (:id, :wid, :bwid, :wcustom, :rid, :zone, :by, :src, :session, :mode, :run_mode, "
                 f":team, :model, :think, datetime('now','localtime'))"),
            {"id": conv_id, "wid": workspace_id or zero_store.DEFAULT_WORKSPACE_ID, "bwid": None,
             "wcustom": 1 if workspace_custom else 0,
             "rid": role_id,
             "zone": zone, "by": user, "src": source or "chat", "session": session_id,
             "mode": mode, "run_mode": run_mode,
             "team": team_id or None,
             "model": (model_code or None), "think": (thinking_level or None)},
        )
        if session_id:
            # 新栏目 = 会话有了新活动：把根会话顶到列表前面。
            db.execute(
                text(f"UPDATE {_q('conversations')} SET updated_at = datetime('now','localtime') "
                     f"WHERE id = :id"),
                {"id": session_id},
            )
        db.commit()
    if team_id:
        team = None
        try:
            from services.agent_zero.services import team_service

            team = team_service.get_team(str(team_id))
        except Exception:
            team = None
        append_system_message(conv_id, f"你已加入群聊「{(team or {}).get('name') or team_id}」")
    zero_store.append_audit(user, "conversation_create",
                            args={"workspace_id": workspace_id,
                                  "workspace_custom": bool(workspace_custom),
                                  "role_id": role_id,
                                  "zone": zone, "conversation_id": conv_id,
                                  "source": source or "chat", "permission_mode": mode,
                                  "run_mode": run_mode, "team_id": team_id,
                                  "model_code": model_code or "",
                                  "thinking_level": thinking_level or ""})
    return {"id": conv_id, "workspace_id": workspace_id,
            "workspace_custom": bool(workspace_custom),
            "bound_workspace_id": bound_workspace_id or "",
            "role_id": role_id, "zone": zone, "source": source or "chat",
            "permission_mode": mode, "run_mode": run_mode,
            "team_id": team_id,
            "session_id": session_id or conv_id,
            "session_root_id": session_id or conv_id,
            "thread_count": 1,
            "model_code": model_code or "", "thinking_level": thinking_level or ""}


_GLOBAL_AGENT_ENSURE_LOCK = threading.Lock()


def ensure_global_chat_conversation(user: str = "local") -> Dict[str, Any]:
    """Keep the global collaboration conversation separate from management history."""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    with _GLOBAL_AGENT_ENSURE_LOCK:
        with get_session_local()() as db:
            row = db.execute(
                text(f"SELECT id, archived FROM {_q('conversations')} WHERE zone = :zone "
                     f"ORDER BY archived ASC, updated_at DESC, created_at DESC LIMIT 1"),
                {"zone": GLOBAL_CHAT_ZONE},
            ).mappings().first()
            if row:
                if row["archived"]:
                    db.execute(text(f"UPDATE {_q('conversations')} SET archived = 0 WHERE id = :id"),
                               {"id": row["id"]})
                    db.commit()
                return get_conversation(str(row["id"])) or {}
        return create_conversation("", user,
                                   role_id=zero_store.DEFAULT_ROLE_ID,
                                   zone=GLOBAL_CHAT_ZONE, source="global-chat")


def ensure_global_agent_conversation(user: str = "local") -> Dict[str, Any]:
    """返回**唯一**的全局 Agent 会话，不存在时创建。

    之前前端在会话列表里按 ``zone`` 找全局 Agent：列表只取最近 80 条，
    会话超过 30 天还会被自动归档，于是每次找不到就新建一条，最终出现多条
    全局 Agent 会话甚至把旧会话和群组混在一起。这里统一收口：

    - 优先复用未归档的会话；全部归档时恢复最近的一条；
    - 其余重复的全局 Agent 会话只归档、不删除，保留历史；
    - 进程内加锁，避免连点按钮创建出两条。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    with _GLOBAL_AGENT_ENSURE_LOCK:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            rows = db.execute(
                text(f"SELECT id, archived FROM {_q('conversations')} "
                     f"WHERE COALESCE(zone, '') = :zone "
                     f"ORDER BY archived ASC, updated_at DESC, created_at DESC, id DESC"),
                {"zone": GLOBAL_AGENT_ZONE},
            ).mappings().all()
            if rows:
                keep_id = str(rows[0]["id"])
                if rows[0].get("archived"):
                    db.execute(
                        text(f"UPDATE {_q('conversations')} SET archived = 0 "
                             f"WHERE id = :id"),
                        {"id": keep_id},
                    )
                duplicates = [str(r["id"]) for r in rows[1:] if not r.get("archived")]
                if duplicates:
                    marks = ",".join(f":d{i}" for i in range(len(duplicates)))
                    db.execute(
                        text(f"UPDATE {_q('conversations')} SET archived = 1 "
                             f"WHERE id IN ({marks})"),
                        {f"d{i}": cid for i, cid in enumerate(duplicates)},
                    )
                db.commit()
                return get_conversation(keep_id) or {}
        return create_conversation(
            "", user,
            zone=GLOBAL_AGENT_ZONE, source="global-agent")


def append_system_message(conv_id: str, text: str) -> Optional[Dict[str, Any]]:
    """写一条 `by='system'` 的对话消息（进群 / 退群 / 改模式这类 IM 通知）。

    系统消息只用于展示，不进入模型上下文（`_history_messages` 会过滤），
    也不影响未读（未读只认 assistant 消息）。
    """
    conv = get_conversation(conv_id)
    text = str(text or "").strip()
    if not conv or not text:
        return None
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node_id = zero_store.new_id("msg")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default", "confidence": 0.5,
        "props": {
            "conversation_id": conv_id,
            "workspace_id": conv.get("workspace_id"),
            "role_id": conv.get("role_id"),
            "content": text[:500],
            "stage": "exploration",
            "by": "system",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    })
    zero_store.touch_conversation(conv_id)
    return {"id": node_id, "stage": "exploration", "by": "system"}


def append_mention_message(conv_id: str, role: Dict[str, Any],
                           member: Dict[str, Any], goal: str) -> Optional[Dict[str, Any]]:
    """主持 @ 群成员：写一条可回放的转交消息（助手侧），并返回节点 id。"""
    conv = get_conversation(conv_id)
    goal = str(goal or "").strip()
    if not conv or not goal:
        return None
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node_id = zero_store.new_id("msg")
    member_name = str(member.get("name") or member.get("id") or "成员")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default", "confidence": 0.5,
        "props": {
            "conversation_id": conv_id,
            "workspace_id": conv.get("workspace_id"),
            "role_id": role.get("id") or "",
            "role_name": role.get("name") or "",
            "role_avatar": role.get("avatar") or "",
            "role_title": role.get("title") or "",
            "role_department": (role.get("department")
                                or zero_store.department_path(role.get("department_id"))),
            "content": f"@{member_name} {goal}"[:4000],
            "kind": "team_mention",
            # 模型上下文里它仍是「用户侧任务」（by=user），但界面要显示成
            # 主持直接 @ 成员，而不是「用户帮忙转发」。
            "display_by": "assistant",
            "mentions": [member.get("id") or ""],
            "stage": "exploration",
            # 作为“排队的用户消息”：成员 run 启动时才生效；
            # 主持正在跑的这一轮不会把它当成用户干预抢走。
            "by": "user",
            "queued": 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    })
    zero_store.touch_conversation(conv_id)
    return {"id": node_id, "stage": "exploration", "by": "assistant"}


def append_task_ack_message(conv_id: str, member: Dict[str, Any],
                            goal: str,
                            run_id: str = "") -> Optional[Dict[str, Any]]:
    """成员接到后台任务后的**前台回复**：「收到，我开始在后台执行…」。

    对话与任务分离：这条只是对话层的一句确认；真正的执行在后台 run 里，
    过程不进前台转录，完成后由 `append_task_done_message` 写触发器。

    ``run_id`` 只写进 ``anchor_run_id``：它是转录排序锚点，不是这条确认的产出。
    ``props.run_id`` 必须留给真正执行那一轮的助手回复——否则
    ``persist_interrupted_run`` / ``_member_result_text`` 会把这句确认误当成
    运行结果。
    """
    conv = get_conversation(conv_id)
    goal = str(goal or "").strip()
    if not conv or not goal:
        return None
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node_id = zero_store.new_id("msg")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default", "confidence": 0.5,
        "props": {
            "conversation_id": conv_id,
            "workspace_id": conv.get("workspace_id"),
            "role_id": member.get("id") or "",
            "role_name": member.get("name") or "",
            "role_avatar": member.get("avatar") or "",
            "role_title": member.get("title") or "",
            "role_department": (member.get("department")
                                or zero_store.department_path(member.get("department_id"))),
            "content": (f"收到，我在后台执行这个任务：{goal[:600]}"
                        "\n\n（执行过程不进对话；完成后我会在这里通知你。）"),
            "kind": "task_ack",
            # 排序锚点：确认消息紧跟「@成员」那条转交消息。@ 消息锚在成员 run 的
            # 启动时刻，而确认消息落库早于 run 启动；没有这个归属就只能按自己的
            # created_at 排，会跑到「@成员」**前面**（用户反馈的顺序错乱）。
            "anchor_run_id": str(run_id or ""),
            "stage": "exploration",
            "by": "assistant",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    })
    zero_store.touch_conversation(conv_id)
    return {"id": node_id, "stage": "exploration", "by": "assistant"}


def append_task_done_message(conv_id: str, task: Dict[str, Any],
                             summary: str = "") -> Optional[Dict[str, Any]]:
    """后台任务结束 → 写一条**前台触发器**（幂等：同一任务只写一条）。

    触发器是「结果已就绪」的入口；``team_task_service.on_run_finished``
    紧接着自动把结果交回对话，用户不需要再点「继续对话」。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.runtime.zero.gdag.store import GdagStore
    from services.agent_zero.stores.zero_store import _q

    conv = get_conversation(conv_id)
    task_id = str(task.get("id") or "").strip()
    if not conv or not task_id:
        return None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        existing = db.execute(
            text(f"SELECT id FROM {_q('gdag_nodes')} WHERE type = 'fact' "
                 f"AND props->>'conversation_id' = :cid "
                 f"AND props->>'kind' = 'task_done' "
                 f"AND props->>'task_id' = :tid LIMIT 1"),
            {"cid": conv_id, "tid": task_id},
        ).scalar()
    if existing:
        return {"id": str(existing), "duplicate": True}

    status = str(task.get("status") or "")
    title = str(task.get("title") or "后台任务")
    labels = {"done": "已完成", "failed": "失败", "stopped": "已结束",
              "paused": "已暂停"}
    label = labels.get(status, status or "已结束")
    node_id = zero_store.new_id("msg")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default", "confidence": 0.5,
        "props": {
            "conversation_id": conv_id,
            "workspace_id": conv.get("workspace_id"),
            "content": f"后台任务「{title}」{label}",
            "kind": "task_done",
            "task_id": task_id,
            "task_title": title,
            "task_status": status,
            "summary": str(summary or "")[:4000],
            "stage": "exploration",
            "by": "system",
            "display_by": "system",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    })
    zero_store.touch_conversation(conv_id)
    return {"id": node_id, "stage": "exploration", "by": "system"}


def mark_task_done_continued(conv_id: str, task_id: str) -> None:
    """标记「任务完成」触发器已交回对话（自动或手动）。

    前端据此把卡片文案从「结果还没有进入对话 / 继续对话」换成「已交回对话」，
    避免自动继续之后还留着一个点了会 409 的按钮。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    cid = str(conv_id or "").strip()
    tid = str(task_id or "").strip()
    if not cid or not tid:
        return
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('gdag_nodes')} SET props = json_set("
                 f"props, '$.continued', 1, '$.continued_at', :ts) "
                 f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
                 f"AND props->>'kind' = 'task_done' AND props->>'task_id' = :tid"),
            {"cid": cid, "tid": tid,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")},
        )
        db.commit()


def update_conversation_preferences(conv_id: str, *,
                                    model_code: Optional[str] = None,
                                    thinking_level: Optional[str] = None) -> Dict[str, Any]:
    """保存会话级模型 / 思考强度；空字符串表示回到默认。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    conv = get_conversation(conv_id)
    if not conv:
        raise ValueError(f"对话不存在: {conv_id}")
    sets = []
    params: Dict[str, Any] = {"id": conv_id}
    if model_code is not None:
        sets.append("model_code = :model")
        params["model"] = str(model_code).strip() or None
    if thinking_level is not None:
        sets.append("thinking_level = :think")
        params["think"] = str(thinking_level).strip() or None
    if not sets:
        return conv
    sets.append("updated_at = datetime('now','localtime')")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        db.commit()
    return get_conversation(conv_id) or conv


def get_conversation(conv_id: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('conversations')} WHERE id = :id"),
            {"id": conv_id},
        ).mappings().first()
        if not row:
            return None
        data = dict(row)
        root = str(data.get("session_id") or data.get("id") or "")
        data["session_id"] = root
        data["session_root_id"] = root
        data["thread_count"] = int(db.execute(
            text(f"SELECT COUNT(*) FROM {_q('conversations')} "
                 f"WHERE COALESCE(NULLIF(session_id, ''), id) = :root"),
            {"root": root},
        ).scalar() or 1)
    data["workspace_id"] = zero_store.public_workspace_id(data.get("workspace_id"))
    data["workspace_custom"] = bool(data["workspace_id"])
    return data


def fork_conversation(conv_id: str, *, message_id: Optional[str] = None,
                      user: str = "local",
                      title: Optional[str] = None) -> Dict[str, Any]:
    """从某条消息处分叉出一个新会话。

    - `message_id` 为空：复制整段对话；否则复制到该消息（含）为止。
    - 新会话继承工作区 / 角色 / 团队 / 模型 / 权限 / 运行模式，消息节点换新 id；
    - 附件文件一起复制，删除任一分叉不会影响另一边；
    - 文件检查点不复制：工作区是共享的，文件状态由当前工作区决定。
    """
    src = get_conversation(conv_id)
    if not src:
        raise ValueError(f"对话不存在: {conv_id}")
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.runtime.zero.gdag.store import GdagStore
    from services.agent_zero.stores.zero_store import _q, decode_row

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, camp, props, confidence, sources "
                 f"FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
                 f"AND props->>'stage' = 'exploration' "
                 f"AND COALESCE(props->>'kind', '') <> 'checkpoint' "
                 f"ORDER BY created_at ASC, rowid ASC"),
            {"cid": conv_id},
        ).mappings().all()
    items = [dict(r) for r in rows]
    if message_id:
        ids = [str(r["id"]) for r in items]
        if message_id not in ids:
            raise ValueError(f"消息不存在: {message_id}")
        items = items[:ids.index(message_id) + 1]
    if not items:
        raise ValueError("该对话没有可复制的消息")

    from services.agent_zero.services import team_service as _tsvc

    src_team = _tsvc.get_team(str(src.get("team_id") or "")) or {}
    src_is_shared_group = str(src_team.get("kind") or "group") == "group"
    new = create_conversation(
        str(src.get("workspace_id") or ""), user,
        role_id=src.get("role_id"),
        zone=None if src.get("zone") == GLOBAL_CHAT_ZONE else src.get("zone"),
        source="fork",
        # 共享群组是长期实体，分支继续留在群里；会话私有参与角色要复制一份，
        # 不能把两条会话绑到同一个 participants-* 团队上。
        team_id=src.get("team_id") if src_is_shared_group else None,
        model_code=src.get("model_code"),
        thinking_level=src.get("thinking_level"),
        permission_mode=src.get("permission_mode"),
        run_mode=src.get("run_mode"),
        # 分支仍属于原会话：在窗口左侧作为新栏目出现，不另占一张会话卡。
        session_id=str(src.get("session_root_id") or src.get("session_id") or src.get("id")),
    )
    new_id = str(new["id"])
    if src_team and not src_is_shared_group:
        try:
            leader = src_team.get("leader") or {}
            members = [
                {"id": m.get("id"), "name": m.get("name"), "expertise": m.get("expertise")}
                for m in (src_team.get("members") or []) if m.get("id")
            ]
            tid = f"participants-{new_id}-{zero_store.new_id('set')}"
            _tsvc.create_team(
                str(new.get("workspace_id") or src.get("workspace_id") or ""),
                str(src_team.get("name") or "会话参与角色"),
                leader={"id": leader.get("id"), "name": leader.get("name") or "主持"},
                members=members,
                mode=src_team.get("mode"), max_speakers=src_team.get("max_speakers"),
                team_id=tid, kind="participants",
            )
            _tsvc.bind_team(new_id, tid, allow_participants=True)
        except Exception:
            logger.debug("分支复制会话参与角色失败（忽略）", exc_info=True)
    store = GdagStore()
    copied = 0
    copied_attachments: List[Dict[str, Any]] = []
    try:
        for item in items:
            props = dict(decode_row({"props": item.get("props")}).get("props") or {})
            props["conversation_id"] = new_id
            props["queued"] = 0
            props.pop("inject", None)
            props["forked_from"] = str(item["id"])
            copied_attachments.extend(
                [a for a in (props.get("attachments") or []) if isinstance(a, dict)])
            store.upsert_node({
                "id": zero_store.new_id("msg"),
                "type": "fact",
                "camp": str(item.get("camp") or "default"),
                "props": props,
                "confidence": item.get("confidence"),
                "sources": item.get("sources"),
            })
            copied += 1
        from services.agent_zero.services import attachment_service

        attachment_service.copy_conversation_attachments(
            conv_id, new_id, copied_attachments)
    except Exception:
        # 分叉失败不留下半个会话 / 半套附件
        try:
            delete_conversation(new_id)
        except Exception:
            logger.exception("清理失败的分叉会话失败: %s", new_id)
        raise

    base_title = str(src.get("title") or "").strip()
    if not base_title:
        for item in items:
            props = decode_row({"props": item.get("props")}).get("props") or {}
            if str(props.get("by") or "") != "assistant":
                base_title = re.sub(r"\s+", " ", str(props.get("content") or "")).strip()[:60]
                break
    new_title = (str(title or "").strip()
                 or (base_title + " · 分支")[:200])
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET title = :title, "
                 f"title_source = :src_tag, forked_from_id = :parent, "
                 f"forked_from_message_id = :msg, "
                 f"updated_at = datetime('now','localtime') WHERE id = :id"),
            {"title": new_title,
             "src_tag": "user" if str(title or "").strip() else "fallback",
             "parent": conv_id, "msg": message_id or "", "id": new_id},
        )
        db.commit()
    zero_store.append_audit(
        user, "conversation_fork",
        args={"conversation_id": conv_id, "new_conversation_id": new_id,
              "message_id": message_id or "", "messages": copied},
    )
    return get_conversation(new_id) or new


RUN_MODES = ("chat", "autonomous", "goal")


def last_run_mode() -> str:
    """新会话应继承的运行模式：上一条会话的，否则已保存的默认（默认 agent）。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores import settings_store
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT run_mode FROM {_q('conversations')} "
                 f"ORDER BY updated_at DESC LIMIT 1"),
        ).first()
    mode = str((row[0] if row else "") or "")
    if mode in RUN_MODES:
        return mode
    cfg = settings_store.get("run_mode", {}) or {}
    mode = str(cfg.get("default") or "")
    return mode if mode in RUN_MODES else "autonomous"


def set_run_mode(conv_id: str, mode: str) -> str:
    """实时保存该会话的运行模式（chat/agent），并同步默认档位供新会话延续。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores import settings_store
    from services.agent_zero.stores.zero_store import _q

    mode = str(mode or "").strip()
    if mode not in RUN_MODES:
        raise ValueError(f"非法运行模式: {mode}")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET run_mode = :m, "
                 f"updated_at = datetime('now','localtime') WHERE id = :c"),
            {"m": mode, "c": conv_id},
        )
        db.commit()
    settings_store.put("run_mode", {"default": mode})
    zero_store.append_audit("local", "run_mode_set",
                            args={"conversation_id": conv_id, "mode": mode})
    return mode


def set_run_mode_default(mode: str) -> str:
    """保存默认运行模式（尚无对话时用户切换 chat/agent 的落点）。"""
    from services.agent_zero.stores import settings_store

    mode = str(mode or "").strip()
    if mode not in RUN_MODES:
        raise ValueError(f"非法运行模式: {mode}")
    settings_store.put("run_mode", {"default": mode})
    return mode


def send_message(conv_id: str, content: str, user: str,
                 refs: Optional[List[str]] = None,
                 queued: bool = False,
                 attachments: Optional[List[Dict[str, Any]]] = None,
                 prompt: Optional[str] = None) -> Dict[str, Any]:
    """探索态消息：写 GDAG 轻量上下文节点。refs = 引用的工作区文件路径。

    若对话有**正在执行的运行**，本条消息即为「对话内干预」——执行循环每轮会
    取走并注入上下文（见 `_drain_new_interventions`）。

    queued=True：队列模式排队消息——先落库，但**不**被正在执行的 run 当作干预取走；
    它对应的 run 会在当前 run 结束后按队列顺序执行。前端在排队期间只把它列在
    「消息队列」条上，等 run 开始（`mark_queued_message_active` 清掉 queued 标记）
    才作为用户消息出现在转录里。

    ``prompt``：命令展开后的模型侧正文（如 ``/review args`` 展开成完整指令）。
    转录里仍显示 ``content``，历史回放时用 ``prompt`` 进入模型上下文。
    """
    conv = get_conversation(conv_id)
    if not conv:
        raise ValueError(f"对话不存在: {conv_id}")
    content = (content or "").strip()
    ref_list = [str(r).strip() for r in (refs or []) if str(r).strip()]
    att_list = [a for a in (attachments or []) if isinstance(a, dict) and a.get("id")]
    # 允许「只发图片 / 附件」：正文为空但有附件时，消息仍然成立。
    if not content and not att_list and not ref_list:
        raise ValueError("消息为空")
    camp = "default"  # P0：camp 由工作区关联推导，缺省 default
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node_id = zero_store.new_id("msg")
    props: Dict[str, Any] = {
        "conversation_id": conv_id,
        "workspace_id": conv["workspace_id"],
        "role_id": conv.get("role_id"),
        "content": content,
        "refs": ref_list,
        "attachments": att_list,
        "stage": "exploration",
        "by": user,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "queued": 1 if queued else 0,
    }
    if prompt and prompt.strip() != content:
        props["prompt"] = prompt.strip()
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": camp,
        "props": props,
        "confidence": 0.5,
    })
    # 首条用户消息顺便成为会话标题（列表不再显示成助手末尾那句话）。
    title = re.sub(r"\s+", " ", content).strip()[:60]
    if title:
        from sqlalchemy import text as _text
        from services.agent_zero.database import get_session_local as _session
        from services.agent_zero.stores.zero_store import _q as _quote

        with _session()() as db:
            db.execute(
                _text(f"UPDATE {_quote('conversations')} SET title = :title, "
                      f"title_source = 'fallback' "
                      f"WHERE id = :id AND (title IS NULL OR trim(title) = '')"),
                {"title": title, "id": conv_id},
            )
            db.commit()
    # 会话活跃时间：用户发言即刷新（会话列表据此把最近有动静的会话顶上来）
    zero_store.touch_conversation(conv_id)
    return {"id": node_id, "stage": "exploration", "ok": True, "intervened": False}


# ── 探索态 LLM 对话（角色配置驱动）────────────────────────────

# 跨轮历史：条数 + token 预算双上限（vibecoding 长会话用「多留小消息、少留大消息」）。
# 20 条 ≈ 10 轮问答，长会话里很容易把早先的约定挤出窗口；40 条再按 token 预算裁剪，
# 短问答能多留几轮，长代码 / 大附件消息自动少留，历史不会吃掉窗口的大头。
CHAT_HISTORY = 60
CHAT_HISTORY_SHARE = 0.20      # 未显式配置时：历史最多占模型窗口的 20%
CHAT_HISTORY_MIN_TOKENS = 8_000
CHAT_HISTORY_MAX_TOKENS = 60_000
CHAT_HISTORY_FALLBACK_TOKENS = 30_000   # 窗口未知时的保守预算
CHAT_MAX_TOKENS = 0   # 0 = 不限，输出长度由模型配置的 context_window / max_tokens 决定


def _resolve_chat_role(conv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """解析对话的响应角色（与任务能力来源同优先级）：

    ⓪ 绑定 Agent 团队 → 团队主持角色
    ① 对话指定角色 `role_id` → ② 群工作区主持角色 → ③ 群工作区首个成员角色
    → ④ 角色独立工作区（按 id/name 匹配）。

    全局 Agent 只认 ``GLOBAL_ROLE_ID``（AtlasX）；普通会话永远不返回它。
    """
    if str(conv.get("zone") or "") == GLOBAL_AGENT_ZONE:
        return (zero_store.get_role(zero_store.GLOBAL_ROLE_ID)
                or zero_store.get_role(zero_store.DEFAULT_ROLE_ID))
    team_id = str(conv.get("team_id") or "").strip()
    if team_id:
        try:
            from services.agent_zero.services import team_service

            team = team_service.team_for_conv(conv)
            # 只有主持、没有成员 = 不是群聊（旧「默认群聊」归一的残留）：
            # 单聊用会话自己的角色，而不是团队主持。
            if team and team.get("members"):
                leader_id = (team.get("leader") or {}).get("id")
                role = zero_store.get_role(str(leader_id or ""))
                if role:
                    return role
        except Exception as e:
            logger.debug("团队主持角色解析跳过: %s", e)
    rid = str(conv.get("role_id") or "").strip()
    if rid and rid != zero_store.GLOBAL_ROLE_ID:
        role = zero_store.get_role(rid)
        if role:
            return role
    ws_id = str(conv.get("workspace_id") or "").strip()
    if not ws_id:
        return None
    ws = zero_store.get_workspace(ws_id)
    if not ws:
        return None
    host = str(ws.get("host_role_id") or "").strip()
    if host and host != zero_store.GLOBAL_ROLE_ID:
        role = zero_store.get_role(host)
        if role:
            return role
    for mid in (ws.get("member_role_ids") or []):
        if str(mid) == zero_store.GLOBAL_ROLE_ID:
            continue
        role = zero_store.get_role(str(mid))
        if role:
            return role
    if ws.get("kind") == "role":
        for r in zero_store.list_roles(limit=200):
            if str(r.get("id") or "") == zero_store.GLOBAL_ROLE_ID:
                continue
            if r["id"] == ws_id or (ws.get("name") and r.get("name") == ws["name"]):
                return r
    return None


# 圆桌模式每条消息最多几个角色应答；团队可覆盖（agent_teams.max_speakers）。
GROUP_MAX_SPEAKERS = 4


def _team_roles(team: Optional[Dict[str, Any]]) -> tuple:
    """团队 → (leader_role, member_roles)；leader 不在 members 里重复出现。

    V3 起多角色只认显式绑定到会话的团队（agent_teams）；工作区的旧群组列
    不再参与路由，单聊不会被归一成「默认群聊」。
    """
    if not team:
        return None, []
    leader = zero_store.get_role(str(((team.get("leader") or {}).get("id")) or ""))
    if leader and str(leader.get("id") or "") == zero_store.GLOBAL_ROLE_ID:
        leader = None
    members: List[Dict[str, Any]] = []
    for m in team.get("members") or []:
        r = zero_store.get_role(str((m or {}).get("id") or ""))
        if not r or str(r.get("id") or "") == zero_store.GLOBAL_ROLE_ID:
            continue
        if leader and r["id"] == leader["id"]:
            continue
        members.append(r)
    # 旧数据的主持是系统角色 / 已删除：把第一个真实成员提升为主持，
    # 否则群聊会落回会话默认角色（旧版本里就是 AtlasX）。
    if leader is None and members:
        leader = members.pop(0)
    return leader, members


def _team_route(team: Optional[Dict[str, Any]]) -> tuple:
    """团队 → (路由, leader, members)。

    ``roundtable`` 且确有成员 → 圆桌；其余（orchestrate / single / 缺成员）→ 主持单角色。
    单独抽出来是为了让 mode 路由可单测，不必起完整对话循环。
    """
    leader, members = _team_roles(team)
    mode = str((team or {}).get("mode") or "orchestrate")
    if mode == "roundtable" and members:
        return "roundtable", leader, members
    return "single", leader, members


def _team_reply_plan(team: Optional[Dict[str, Any]],
                     mentions: Optional[List[str]], content: str) -> tuple:
    """团队 → (是否走多角色群聊, leader, members, 被点名角色)。

    用户显式 @成员 时，任何模式都直接交给被点名的角色应答（``use_group=True``），
    不先落到主持那里代转；圆桌仍按原逻辑走多角色，其余情况由主持单角色运行。
    """
    route, leader, members = _team_route(team)
    all_roles = ([leader] if leader else []) + members
    addressed = _mention_targets(mentions, content, all_roles)
    use_group = bool(addressed) or (route == "roundtable" and bool(leader))
    return use_group, leader, members, addressed


def _roles_mentioned(text: str, roles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """文本中 @角色名 命中的角色（角色名含空格时按整名匹配）。

    `@name` 后须为词边界（空格 / 标点 / 结尾），避免前缀误命中（如 @分析 命中 @分析师）。
    """
    t = str(text or "")
    out: List[Dict[str, Any]] = []
    for r in roles or []:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        pattern = rf"@{re.escape(name)}(?![^\s，。！？、；：,.!?;:）)】\]}}])"
        if re.search(pattern, t):
            out.append(r)
    return out


def _mention_targets(mentions: Optional[List[str]], content: str,
                     roles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """解析显式点名：mentions（id 或名字）优先，其次正文里的 `@名字`。

    返回的都是**被点名的角色本身**——调用方据此直接交给该角色应答，
    不能再先转给主持代转（用户 @ 谁，就是谁答）。
    """
    all_roles = list(roles or [])
    by_id = {str(r.get("id") or ""): r for r in all_roles}
    by_name = {str(r.get("name") or "").strip(): r for r in all_roles}
    targets: List[Dict[str, Any]] = []
    for mid in (mentions or []):
        token = str(mid or "").strip()
        r = by_id.get(token) or by_name.get(token)
        if r and r["id"] not in [t["id"] for t in targets]:
            targets.append(r)
    if not targets:
        targets = _roles_mentioned(content, all_roles)
    return targets


# 角色在正文里**明确**提到后台任务 / 并行执行时，@ 成员登记为群组后台任务
# （执行画布可暂停 / 继续 / 结束），而不是一条普通的成员回复 run。
_BACKGROUND_MENTION_HINTS = (
    "后台任务", "后台执行", "后台跑", "放到后台", "放后台", "异步执行", "异步跑",
    "并行执行", "并行跑",
)

# 一条回复最多派发几个成员：防止主持在总结里顺带点名一堆人，瞬间放大 token。
_MENTION_DISPATCH_LIMIT = 3


def _wants_background_task(text: str) -> bool:
    body = str(text or "")
    return any(hint in body for hint in _BACKGROUND_MENTION_HINTS)


def _mention_task_text(text: str, name: str, limit: int = 600) -> str:
    """取 `@角色` 所在的那一句，作为交给该成员的任务描述。

    整段回复常常是给用户看的总结，直接转给成员会把无关内容也塞过去；
    按换行 / 句末标点切出被点名的那一句，既能保留上下文又不放大 token。
    """
    body = str(text or "").strip()
    name = str(name or "").strip()
    if not body or not name:
        return body[:limit]
    marker = f"@{name}"
    idx = body.find(marker)
    if idx < 0:
        return body[:limit]
    start = idx
    while start > 0 and body[start - 1] not in "\n。！？!?；;":
        start -= 1
    end = idx + len(marker)
    while end < len(body) and body[end] not in "\n。！？!?；;":
        end += 1
    return body[start:end].strip()[:limit]


async def _dispatch_reply_mentions(conv: Dict[str, Any], role: Dict[str, Any],
                                   text: str, user: str, *,
                                   run_id: str = "",
                                   skip_ids: Optional[set] = None
                                   ) -> List[Dict[str, Any]]:
    """角色回复里 `@成员` → 让被点名的成员真正接活（主会话不等待）。

    分流规则（与产品约定一致）：
    - 普通 `@成员`：给该成员排一条独立 run，worker 在后台执行，回复直接回到
      会话；当前 run 不阻塞，主会话发送按钮保持可用。
    - 正文明确说「后台任务 / 并行执行」：登记为群组后台任务（执行画布可见，
      可暂停 / 继续 / 结束），由用户点「继续」再交回主会话。

    已经是成员 run 时不再派发（由调用方保证），避免角色之间 @ 来 @ 去无限套娃。
    """
    body = str(text or "")
    if not body.strip() or "@" not in body:
        return []
    try:
        from services.agent_zero.services import team_service

        team = team_service.team_for_conv(conv)
    except Exception:
        logger.debug("团队信息读取失败（跳过 @ 派发）", exc_info=True)
        return []
    if not team:
        return []

    all_roles = ([team.get("leader")] if team.get("leader") else []) + list(
        team.get("members") or [])
    targets = _roles_mentioned(body, all_roles)
    if not targets:
        return []
    # 单轮参与上限同样约束「回复里 @ 成员」：不能靠一句话把全员都点起来。
    try:
        mention_limit = max(1, min(int(team.get("max_speakers") or 3),
                                   _MENTION_DISPATCH_LIMIT))
    except (TypeError, ValueError):
        mention_limit = _MENTION_DISPATCH_LIMIT
    targets = targets[:mention_limit]

    from services.agent_zero.task_layer import conv_worker, run_service

    skip = {str(x) for x in (skip_ids or set())}
    skip.add(str(role.get("id") or ""))
    conv_id = str(conv.get("id") or "")
    if not conv_id:
        return []

    background = _wants_background_task(body)
    background_specs: List[Dict[str, Any]] = []
    dispatched: List[Dict[str, Any]] = []

    for target in targets:
        tid = str(target.get("id") or "")
        name = str(target.get("name") or tid)
        if not tid or tid in skip:
            continue
        # 已经派过活（或主持已用 delegate 派过）就不再重复入队。
        try:
            if run_service.active_target_run(conv_id, tid):
                continue
        except Exception:
            logger.debug("@ 派发去重查询失败（继续派发）", exc_info=True)
        goal = _mention_task_text(body, name)
        if not goal:
            continue
        if background:
            background_specs.append({
                "role_id": tid, "role_name": name, "goal": goal,
                "context": "", "role": target,
            })
            continue
        try:
            mention = append_mention_message(conv_id, role, target, goal)
            run = run_service.create_run(
                conv_id, user,
                role_id=tid,
                target_role_id=tid,
                content=goal,
                mentions=[tid],
                mode="chat",
                queued_msg_id=(mention or {}).get("id"),
            )
            dispatched.append({
                "role_id": tid, "role_name": name,
                "run_id": str(run.get("id") or ""), "kind": "reply",
            })
            try:
                await conv_worker.request_run(str(run.get("id") or ""))
            except Exception:
                logger.debug("@ 成员 run 唤醒失败（worker 会轮询拾取）", exc_info=True)
            append_system_message(
                conv_id,
                f"{role.get('name') or '主持'} @ 了「{name}」：已交由它在后台回复。")
        except Exception:
            logger.exception("@ 成员回复派发失败 conv=%s role=%s", conv_id, tid)

    if background_specs:
        try:
            from services.agent_zero.services import team_task_service

            task = team_task_service.create_task(
                conv, team, role, background_specs, user)
            names = "、".join(s["role_name"] for s in background_specs)
            dispatched.append({
                "task_id": str(task.get("id") or ""),
                "run_ids": [str(m.get("run_id") or "")
                            for m in (task.get("members") or [])],
                "kind": "background",
                "members": [s["role_name"] for s in background_specs],
            })
            append_system_message(
                conv_id,
                f"已创建后台任务：{names} 在后台执行；完成后点「继续对话」再交回主会话。")
        except Exception:
            logger.exception("群组后台任务派发失败 conv=%s", conv_id)

    return dispatched


def _persist_assistant(conv: Dict[str, Any], role: Dict[str, Any], content: str,
                       model: Optional[str], duration_ms: int,
                       reasoning: str = "", tool_trace: Optional[List] = None,
                       trace: Optional[List] = None,
                       usage: Optional[Dict[str, Any]] = None,
                       outcome: str = "", error: str = "",
                       interrupted: bool = False, run_id: str = "",
                       stage: str = "exploration") -> str:
    """落库助手回复（含角色名 / 耗时 / 深度思考 / 工具调用情况），返回节点 id。

    思考与工具轨迹**一并落库**，这样刷新页面后仍能看到"当时它想了什么、调了什么"，
    而不只是流式时的一次性效果。

    ``stage="task"``：后台任务成员 run 的产出。**不进前台对话转录**（`list_messages`
    只取 exploration），由任务完成后的一条触发器把结果带回会话。
    """
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node_id = zero_store.new_id("msg")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default",
        "props": {
            "conversation_id": conv["id"],
            "workspace_id": conv["workspace_id"],
            "role_id": role.get("id"),
            "role_name": role.get("name"),
            "role_avatar": role.get("avatar") or "",
            "role_title": role.get("title") or "",
            "role_department": (role.get("department")
                                or zero_store.department_path(role.get("department_id"))),
            "content": content,
            "stage": stage,
            "by": "assistant",
            "model": model,
            "duration_ms": int(duration_ms or 0),
            # 深度思考与工具调用情况（可观测；摘要字段，不含文件原文）
            "reasoning": (reasoning or ""),
            "tool_trace": (tool_trace or []),
            # 有序轨迹：思考 / 工具 / 中间正文按发生顺序交错（刷新后仍可回看）
            "trace": (trace or []),
            # token 用量（输入/输出）：供「轨迹」面板指标条展示
            "usage": usage or {},
            "outcome": outcome or "",
            "error": error or "",
            "interrupted": bool(interrupted),
            "run_id": run_id or "",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "confidence": 0.5,
    })
    zero_store.touch_conversation(conv["id"])
    return node_id


def _persist_proposal(conv: Dict[str, Any], role: Dict[str, Any],
                      goal: str, reason: str) -> str:
    """落库「转任务建议」（待人工确认），返回节点 id。"""
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node_id = zero_store.new_id("msg")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default",
        "props": {
            "conversation_id": conv["id"],
            "workspace_id": conv["workspace_id"],
            "role_id": role.get("id"),
            "role_name": role.get("name"),
            "role_avatar": role.get("avatar") or "",
            "role_title": role.get("title") or "",
            "role_department": (role.get("department")
                                or zero_store.department_path(role.get("department_id"))),
            "kind": "task_proposal",
            "goal": str(goal or "")[:2000],
            "reason": str(reason or "")[:1000],
            "status": "pending",
            "content": f"[建议转为任务] {goal}",
            "stage": "exploration",
            "by": "assistant",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "confidence": 0.5,
    })
    zero_store.touch_conversation(conv["id"])
    return node_id


def mark_proposal(conv_id: str, node_id: str, status: str,
                  task_id: Optional[str] = None,
                  run_id: Optional[str] = None) -> bool:
    """更新转任务建议状态（pending/accepted/dismissed），可回写 task_id / run_id。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    expr = "json_set(props, '$.status', :st)"
    params: Dict[str, Any] = {"id": node_id, "cid": conv_id, "st": status}
    if task_id:
        expr = f"json_set({expr}, '$.task_id', :tid)"
        params["tid"] = task_id
    if run_id:
        expr = f"json_set({expr}, '$.run_id', :rid)"
        params["rid"] = run_id
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('gdag_nodes')} SET props = {expr} "
                 f"WHERE id = :id AND props->>'conversation_id' = :cid "
                 f"AND props->>'kind' = 'task_proposal' "
                 f"AND COALESCE(props->>'status', 'pending') = 'pending'"),
            params,
        )
        db.commit()
        return bool(res.rowcount)


def _get_proposal(conv_id: str, node_id: str) -> Optional[Dict[str, Any]]:
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT props FROM {_q('gdag_nodes')} WHERE id = :id"),
            {"id": node_id},
        ).scalar()
    if isinstance(row, str):
        try:
            row = json.loads(row)
        except Exception:
            row = {}
    props = row if isinstance(row, dict) else {}
    if not props or props.get("kind") != "task_proposal" \
            or str(props.get("conversation_id")) != conv_id:
        return None
    return props


def accept_proposal(conv_id: str, node_id: str, user: str,
                    goal: Optional[str] = None) -> Dict[str, Any]:
    """人工确认：在本对话内**启动自主执行**（动态规划推进到完成）。

    不再创建独立的「任务」实体——执行由后台 run 承载，进展与结果都回到本对话。
    """
    props = _get_proposal(conv_id, node_id)
    if not props:
        raise ValueError(f"转任务建议不存在: {node_id}")
    if str(props.get("status") or "pending") != "pending":
        raise ValueError("该建议已处理，无需重复执行")
    g = (goal or props.get("goal") or "").strip()
    run = start_autonomous_run(conv_id, g, user)
    mark_proposal(conv_id, node_id, "accepted", run_id=run["id"])
    return run


def start_autonomous_run(conv_id: str, goal: str, user: str, *,
                         role_id: Optional[str] = None) -> Dict[str, Any]:
    """在**本对话内**启动一次自主执行（动态规划），替代旧的「转任务」实体。

    目标落为一条用户消息（进入对话历史，run 据此执行），再建一个 autonomous run；
    回复由后台 worker 执行，浏览器关闭继续，进展 / 结果都回到本对话。
    """
    conv = get_conversation(conv_id)
    if not conv:
        raise ValueError(f"对话不存在: {conv_id}")
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("缺少执行目标 goal")
    # 目标进入对话历史：run 的上下文来自历史消息（见 _stream_role）
    msg = send_message(conv_id, goal, user, queued=True)
    from services.agent_zero.task_layer import run_service

    run = run_service.create_run(
        conv_id, user,
        role_id=role_id or conv.get("role_id"),
        content=goal, mode="autonomous",
        queued_msg_id=msg.get("id"),
    )
    zero_store.append_audit(
        user, "conversation_autonomous_run",
        args={"conversation_id": conv_id, "run_id": run["id"],
              "role_id": role_id or conv.get("role_id"), "goal_chars": len(goal)},
    )
    return run


def _chat_models(role: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """对话模型：优先 loop → plan → 通用 → fallback；均空返回 None。"""
    raw = role.get("envelope") or {}
    models = {r: str((raw.get("models") or {}).get(r) or "").strip()
              for r in ("general", "plan", "loop", "summary", "fallback")}
    loop = models["loop"] or models["plan"] or models["general"] or models["fallback"]
    if not loop:
        return None
    models["loop"] = loop
    models["fallback"] = models["fallback"] or loop
    return models


def role_chat_model_code(role: Dict[str, Any]) -> str:
    """角色显式模型优先，其次是角色运行配置中的模型。"""
    fixed = str(role.get("model_code") or "").strip()
    if fixed:
        return fixed
    loop = (_chat_models(role) or {}).get("loop") or ""
    return loop if loop != "default" else ""


def _resolved_chat_models(conv: Dict[str, Any], role: Dict[str, Any]) -> Dict[str, str]:
    from services.agent_zero.runtime.zero import llm as llm_mod

    selected = str(conv.get("model_code") or "").strip()
    fixed = str(role.get("model_code") or "").strip()
    default = llm_mod.get_active_model_code()
    base = fixed or selected or default
    models = _chat_models(role) or {"loop": "default", "fallback": "default"}
    if fixed:
        models["loop"] = fixed
    return {key: (base if code == "default" else code) for key, code in models.items()}


def chat_model_code(conv: Dict[str, Any]) -> str:
    """主持角色、会话模型与默认模型按实际运行的优先级解析。"""
    role = zero_store.get_role(str(conv.get("role_id") or "")) or {}
    return _resolved_chat_models(conv, role).get("loop") or "default"


def chat_vision_for(conv: Dict[str, Any]) -> bool:
    """使用实际对话模型校验图片支持。"""
    from services.agent_zero.runtime.zero import llm as llm_mod

    try:
        return bool(llm_mod.model_supports_vision(chat_model_code(conv)))
    except Exception:
        return False


# 行为准则 key → 注入系统提示的指令（与前端 views/agent_zero/principles.js 同源）。
_PRINCIPLE_GUIDES = {
    "goal_clarity": "先对齐目标与验收标准，再动手；目标模糊时先澄清，不擅自假设。",
    "evidence_first": "结论必须有可核验的依据（引用具体文件 / 命令 / 数据），不得臆测或编造。",
    "traceable_actions": "每一步动作清晰可回看：多步任务先用 todo_write 列清单，关键步骤留痕。",
}


def _chat_system_prompt(role: Dict[str, Any], workspace_id: Optional[str] = None,
                        progress: str = "",
                        autonomous: bool = False,
                        auto_execute: bool = False,
                        conv_id: Optional[str] = None,
                        user_query: str = "",
                        team: Optional[Dict[str, Any]] = None,
                        goal: Optional[Dict[str, Any]] = None,
                        skills_role_id: Optional[str] = None) -> str:
    """探索态系统提示：稳定工作准则 + 本轮动态上下文。

    静态部分（工作方式 / 工具使用 / 文件与代码 / 验证 / 输出 / 安全）在
    `prompt_builder` 里统一维护；这里只负责收集会随会话变化的素材：

      - 角色人格 / 专长 / 行为准则 / 能力包络
      - 当前工作区快照与代码图谱（`_workspace_snapshot`）
      - 工作区约定文件（`_project_rules`，AGENTS.md / CLAUDE.md 等）
      - 按用户消息召回的相关记忆（memory_service.recall）
      - 技能库、Agent 团队、目标模式、关联任务进展
      - 权限档位（危险操作可能需要确认）

    ``autonomous=True`` 时由 builder 追加动态规划式的自主执行要求。
    """
    from services.agent_zero.task_layer import prompt_builder

    name = role.get("name") or role.get("id")
    title = str(role.get("title") or "").strip()
    department = zero_store.department_path(role.get("department_id"))
    env = role.get("envelope") or {}
    principles = [
        _PRINCIPLE_GUIDES[k]
        for k in (env.get("principles") or [])
        if k in _PRINCIPLE_GUIDES
    ]
    caps: List[str] = []
    for key, label in (("tools", "工具"),):
        vals = env.get(key) or []
        if vals:
            caps.append(f"{label}: {', '.join(str(x) for x in vals)[:300]}")
    if env.get("data_scope"):
        caps.append(f"数据范围: {env['data_scope']}")

    team_text = ""
    if team:
        try:
            from services.agent_zero.services import team_service as _tsvc

            team_text = _tsvc.roster_text(team, role=role)
        except Exception:
            pass

    goal_text = ""
    if goal:
        budget = int(goal.get("token_budget") or 0)
        max_runs = int(goal.get("max_runs") or 0)
        goal_lines = [f"目标：{goal.get('objective')}"]
        goal_lines.append(
            f"进度：已运行 {goal.get('runs_used', 0)} 次"
            f"（{'上限 ' + str(max_runs) + ' 次' if max_runs > 0 else '不限次数'}）；"
            f"token 已用 {goal.get('tokens_used', 0)}"
            + (f" / 预算 {budget}" if budget else "（不限）")
        )
        if goal.get("note"):
            goal_lines.append(f"最近进展：{goal['note']}")
        goal_text = "\n".join(goal_lines)

    workspace_text = _workspace_snapshot(workspace_id) if workspace_id else ""
    workspace_has_dir = _workspace_has_dir(workspace_id) if workspace_id else False
    rules = _project_rules(workspace_id) if workspace_id else ""

    memories_text = ""
    if workspace_id:
        try:
            from services.agent_zero.services import memory_service

            mems = memory_service.recall(
                user_query, workspace_id=workspace_id, limit=4)
            if mems:
                lines = []
                for m in mems:
                    snippet = str(m.get("content") or "")
                    if len(snippet) > 220:
                        snippet = snippet[:220] + "…"
                    ents = []
                    for eid in (m.get("linked_entities") or [])[:3]:
                        # map:file:<ws>:<path> / ent:<ws>:<name> → 可读实体名
                        ents.append(eid.split(":")[-1] if ":" in eid else eid)
                    tag = f" → 挂接实体: {', '.join(ents)}" if ents else ""
                    lines.append(f"- [{m['memory_type']}] {snippet}{tag}")
                memories_text = (
                    "\n".join(lines)
                    + "\n记忆可能过时：与工作区现状冲突时，以实际文件为准。"
                )
        except Exception as e:
            logger.debug("记忆注入失败（不影响对话）: %s", e)

    skills_text = ""
    try:
        from services.agent_zero.services import skill_service

        skills_text = skill_service.skills_prompt(skills_role_id)
    except Exception:
        pass

    permission_mode = ""
    if not auto_execute:
        try:
            from services.agent_zero.task_layer import permissions

            permission_mode = (
                permissions.get_conversation_mode(conv_id) if conv_id
                else permissions.last_mode()
            )
        except Exception:
            permission_mode = ""

    return prompt_builder.build(prompt_builder.PromptContext(
        name=str(name or APP_NAME),
        now=time.strftime("%Y-%m-%d %H:%M:%S"),
        title=title,
        department=department,
        persona=str(role.get("persona") or "").strip(),
        expertise=str(role.get("expertise") or "").strip(),
        principles=principles,
        capabilities=caps,
        workspace=workspace_text,
        workspace_has_dir=workspace_has_dir,
        rules=rules,
        memories=memories_text,
        skills=skills_text,
        team=team_text,
        goal=goal_text,
        progress=progress,
        autonomous=autonomous,
        auto_execute=auto_execute,
        permission_mode=permission_mode,
    ))

# 对话阶段按角色能力包络放开**全部**工具（与历史任务节点同一 build_tool_specs）。
# 有"顺序语义"的工具：一轮里只要出现其一，就整轮走串行（不并发），避免乱序 / 共享状态竞争。
#
# 判定发生在 `resolve_call` 之后，所以这里写的是**归一化后的具体工具名**。名单必须覆盖
# 每一类「不走分发器、由本函数里的专有分支处理」的工具：
#   - 瞬时动作：propose_task / todo_write / goal_report；
#   - 会就地改共享状态：team_manage（本轮的 specs 与对话的 team 绑定）、goal_manage；
#   - 派生子代理 / 团队成员：call_subagent / call_team（长时间、且要回放事件顺序）；
#     delegate 是它们合并后的入口，resolve_call 会拆成上面两个名字，这里也留一份兜底；
#   - 等用户：ask_user；监督后台任务：tool_job。
# 漏掉其中任何一个，模型把「它 + 一个普通工具」放在同一轮时，那个调用会先被并发批
# 送进分发器（那里没有它的实现，白跑一次），再由专有分支重跑一遍——既浪费一轮往返，
# 也埋着「哪天分发器补上实现就变成双写」的隐患（2026-10-30 整理时发现
# goal_manage / team_manage / call_team 都漏了）。
_SEQ_CALLS_TOOLS = frozenset({
    "propose_task",
    "todo_write",
    "goal_report",
    "goal_manage",
    "call_subagent",
    "call_team",
    "delegate",
    "background_task",
    "proactive_send",
    "role_discussion",
    "team_manage",
    "department_manage",
    "role_manage",
    "schedule_manage",
    "app_manage",
    "session_manage",
    "ask_user",
    # 工具监督本身是「读共享注册表 + 可能长时间 wait」：走串行，避免与
    # 其它工具的并发批互相干扰（也让 wait 的语义可预测）。
    "tool_job",
})
# 历史消息 / 旧提示词里的名字：registry 里已没有对应工具（分发器会把它们当未知
# 工具原样返回），但对话循环的兼容分支仍会处理，因此同样必须走串行。单独列出，
# 让「当前工具」与「历史兼容」在代码里一眼可分。
_SEQ_CALLS_LEGACY = frozenset({
    "todo",                       # → todo_write 的旧名
    "spawn_subagent",
    "call_agent", "call_role", "spawn_agent",
})
_SEQ_CALLS = _SEQ_CALLS_TOOLS | _SEQ_CALLS_LEGACY


def tool_reg_canonical(name: str) -> str:
    """工具名归一（包名 / 别名 → 具体工具 code）；失败时原样返回。"""
    try:
        from services.agent_zero.tools import registry as _reg

        return _reg.canonical(name)
    except Exception:
        return name


# **永不转后台**的工具：它们的「长时间没返回」是设计语义，不是挂住。
#   - ask_user：等用户选，等多久都合理；转后台等于「没等答案就继续」；
#   - tool_job：本身就是监督工具，它 wait 时被转后台会变成套娃；
#   - propose_task / todo_write / goal_report：瞬时动作。
# 这些工具仍受 TOOL_MAX_SECONDS（默认 0 = 不限）约束，但没有前台预算。
NO_ADOPT_TOOLS = frozenset({"ask_user", "tool_job", "propose_task",
                            "todo_write", "todo", "goal_report", "goal_manage",
                            "delegate", "background_task", "role_discussion"})
# 工具轮次不设上限：模型自己不再返回 tool_calls 时才收尾。
# 曾用 MAX_CHAT_TOOL_ROUNDS=6 截断，导致「分析整个仓库」这类长任务在第 6 轮被砍断——
# 循环自然走完时最后一轮仍是 tool_calls，没有任何纯文字收尾，
# 最终 text 为空，前端只剩「模型未单独给出结论」（2026-10-01 线上现象）。

# ── 超时策略：对话本身没有会话超时，单个工具默认也不设墙钟上限 ──────────
#
# 前端已刻意不设会话超时（api/agentZero.js 里两个超时参数都是 0）。但**中间
# 还有一层**：nginx（/opt/homebrew/etc/nginx/nginx.conf）反代 /hzclaw2/ 时
# 设了 `proxy_read_timeout 60s`——上游 60 秒没有字节就断开连接。
# 所以慢工具执行期间不能真的静默，必须按这个间隔发心跳把 nginx 的空闲计时
# 压住（同样也能压住任何其它中间层的读超时）。
# ── 展示载荷总预算（一条消息内所有工具加起来）────────────────────────
#
# 0 = 不限。单个文件载荷仍由 file_ops 的展示上限约束，但不再按「一条消息
# 48 KB」把后续工具结果正文剥掉——长任务里那会让用户看不到后面的 diff / 文件内容。
DISPLAY_BUDGET_PER_MESSAGE = 0


def _fit_display(display: Optional[Dict[str, Any]],
                 used: int) -> Tuple[Optional[Dict[str, Any]], int]:
    """把一个展示载荷塞进本条消息的剩余预算，返回 (载荷, 新用量)。

    超预算时不整条丢弃 —— 丢掉的话用户看到的是「这一步没有内容」，
    比看到「读过 a.py 第 40-47 行，正文省略」糟糕得多。
    """
    if not display:
        return None, used
    cost = len(str(display.get("text") or display.get("diff") or ""))
    if not DISPLAY_BUDGET_PER_MESSAGE or used + cost <= DISPLAY_BUDGET_PER_MESSAGE:
        return display, used + cost
    stripped = {k: v for k, v in display.items()
                if k not in ("text", "diff", "entries")}
    stripped["omitted"] = True
    return stripped, used


TOOL_KEEPALIVE_SECONDS = 20.0      # 须明显小于 nginx 的 60s，留足抖动余量
# 单个工具的墙钟上限（兜底：防工具挂死时无限等下去）。0 = 不限。
# 注意：工具直接在本轮等待完成（有心跳保活），**不再转后台**。
TOOL_MAX_SECONDS = 0.0

# 说明：对话侧的文件工具**不再单列一套长名**（历史遗留）。read_file /
# edit_file / write_file / list_files 就是唯一那套，由 `_chat_tool_specs` 从
# registry 按 envelope 门控生成；长名（*_workspace_*）仅作别名保留兼容旧调用。

PROPOSE_TASK_TOOL: Dict[str, Any] = {
    "type": "function", "function": {
        "name": "propose_task",
        "description": (
            "当用户的目标已足够清晰、且确实希望执行时，调用本工具**提出**在本对话内"
            "自主执行（动态规划推进到完成）的建议；系统会请用户点击确认后才开始执行。"
            "仅用于「要去做」的诉求；纯咨询 / 闲聊 / 澄清阶段不要调用。"
        ),
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string", "description": "清晰、可执行的执行目标"},
            "reason": {"type": "string", "description": "为什么现在适合自主执行（可选）"},
        }, "required": ["goal"]},
    },
}

BACKGROUND_TASK_TOOL: Dict[str, Any] = {
    "type": "function", "function": {
        "name": "background_task",
        "description": (
            "把一段或多段耗时 / 多步的工作放到**后台任务**执行（对话与任务分离）。"
            "多项互不依赖的任务用 `tasks` 数组一次派发，它们会并行执行；"
            "调用后本轮只回一句确认并收尾：执行过程不会进入对话；"
            "完成后系统会在这个对话里生成任务完成触发器，由角色继续整合。"
            "适合长任务、批处理、需要大量工具的工作；简单问答 / 纯聊天不要用。"
        ),
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string", "description": "单个后台任务的目标 / 交付物（与 tasks 二选一）"},
            "title": {"type": "string", "description": "任务标题（可选，便于识别）"},
            "context": {"type": "string", "description": "补充上下文（可选）"},
            "tasks": {
                "type": "array",
                "description": "并行派发多项独立任务：每项各自建一条后台任务并同时执行。"
                               "只用于确实互不依赖的工作；不要一次只派一项后干等。",
                "items": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string",
                                 "description": "该任务的目标 / 交付物"},
                        "title": {"type": "string",
                                  "description": "任务标题（可选）"},
                        "context": {"type": "string",
                                    "description": "只给该任务的上下文（可选）"},
                    },
                    "required": ["goal"],
                },
            },
        }, "required": []},
    },
}

# 一次最多并行派发多少条后台任务（防止模型一口气建出几十条把队列打满）。
MAX_BACKGROUND_TASKS_PER_CALL = 8

ROLE_DISCUSSION_TOOL: Dict[str, Any] = {
    "type": "function", "function": {
        "name": "role_discussion",
        "description": (
            "让 2-3 个角色（含你自己）进行多轮直接讨论，不经过主持转述。"
            "适合头脑风暴、方案对比、交互验证、交叉审查。"
            "角色按顺序发言，后发言者能看到前面所有发言；讨论过程显示为子轨迹，"
            "最终把完整记录返回给你。"
        ),
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "讨论主题 / 要解决的问题"},
            "roles": {
                "type": "array",
                "description": "参与讨论的角色 id 或名字（2-3 个，必须包含至少两个不同角色）",
                "items": {"type": "string"},
            },
            "rounds": {
                "type": "integer", "minimum": 1, "maximum": 3,
                "description": "讨论轮数（默认 2）",
            },
            "mode": {
                "type": "string", "enum": ["brainstorm", "verify"],
                "description": "brainstorm=发散头脑风暴；verify=交互验证 / 交叉审查",
            },
            "context": {
                "type": "string",
                "description": "给参与角色的补充上下文（可选）",
            },
        }, "required": ["topic", "roles"]},
    },
}
MAX_DISCUSSION_ROLES = 3
MAX_DISCUSSION_ROUNDS = 3
PROACTIVE_SEND_TOOL: Dict[str, Any] = {
    "type": "function", "function": {
        "name": "proactive_send",
        "description": (
            "在当前会话里**主动向用户发起一条消息**（不需要用户先提问）。"
            "适合：后台任务完成后汇报、发现重要变化、长时间任务的中途进展、"
            "需要提醒用户但用户暂时没在对话里。"
            "消息会延迟几分钟后由你自己生成并出现在会话中；不要频繁使用，"
            "全局 Agent 会限制频率和安静时段。"
        ),
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string",
                         "description": "要主动告诉用户的要点（不是逐字稿，模型会据此生成回复）"},
            "delay_minutes": {"type": "integer", "minimum": 1, "maximum": 1440,
                               "description": "多少分钟后发起（默认 1 分钟）"},
            "reason": {"type": "string", "description": "为什么现在要主动发起（可选）"},
        }, "required": ["message"]},
    },
}
MAX_PROACTIVE_DELAY_MIN = 1440




def _background_task_items(args: Dict[str, Any]) -> List[Dict[str, str]]:
    """把 `background_task` 参数归一成任务列表（兼容旧的单 goal 调用）。

    `tasks` 里的每一项都是独立后台任务：各自一条 team_task、一个成员 run，
    由 worker 并行执行。没有 `tasks` 时退化为单任务。
    """
    items: List[Dict[str, str]] = []

    def _append(source: Dict[str, Any]) -> None:
        goal = str((source or {}).get("goal") or "").strip()
        if not goal:
            return
        items.append({
            "goal": goal[:8000],
            "title": str((source or {}).get("title") or "").strip()[:120],
            "context": str((source or {}).get("context") or "").strip()[:8000],
        })

    raw = (args or {}).get("tasks")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                _append(item)
    if not items:
        _append(args or {})
    return items[:MAX_BACKGROUND_TASKS_PER_CALL]


def _append_background_task_tool(specs: List[Dict[str, Any]]) -> None:
    """把「后台任务」工具补进本轮工具清单（角色会话 / 群组主持都可用）。

    群组里团队委派用 delegate；后台任务用于把**本角色自己**的多项独立工作
    并行放到后台，两者不冲突。
    """
    names = {(s.get("function") or {}).get("name") for s in specs}
    if "background_task" in names:
        return
    specs.append(BACKGROUND_TASK_TOOL)


def _append_role_discussion_tool(specs: List[Dict[str, Any]]) -> None:
    """把「角色讨论」工具补进本轮工具清单（含后台任务成员；子代理不注入）。"""
    names = {(s.get("function") or {}).get("name") for s in specs}
    if "role_discussion" in names:
        return
    specs.append(ROLE_DISCUSSION_TOOL)

def _append_proactive_tool(specs: List[Dict[str, Any]]) -> None:
    """主动消息只给主会话角色，子代理 / 后台任务成员不注入。"""
    names = {(s.get("function") or {}).get("name") for s in specs}
    if "proactive_send" in names:
        return
    specs.append(PROACTIVE_SEND_TOOL)


def _chat_envelope(role: Dict[str, Any]) -> Dict[str, Any]:
    """对话角色的能力包络（与任务节点同源，见 config_source.envelope_from_role）。"""
    from services.agent_zero.runtime.zero import config_source

    return config_source.envelope_from_role(role)


def _chat_tool_specs(envelope: Dict[str, Any],
                     caps: Any = None,
                     autonomous: bool = False,
                     workspace_id: Optional[str] = None,
                     mcp_specs: Optional[List[Dict[str, Any]]] = None,
                     team_bound: bool = False) -> List[Dict[str, Any]]:
    """对话可用工具 = 角色包络内的标准工具 + 转任务建议。

    **工具包**（`tools.packs: true`，默认）：按用途合并成 fs / net / memory /
    plan / skill / mcp，每个包一个 function + `op`，执行与权限仍按具体工具名
    （`packs.resolve_call` 负责解析）。

    **命令与代码类走独立工具**（`packs.STANDALONE_TOOLS`）：shell / tool_job /
    vcs / codegraph / workspace 各自一个 function，直接按名字调用。它们参数差异大、
    风险等级不同，套一层 `op` 只会让模型绕路（也更容易把整条命令写进 op）。
    `propose_task` 由对话层直接处理，保持独立。

    **任务 / 目标 / 协作合并为一个 `plan` 包**：todo（任务清单）/ goal（目标模式）
    / delegate（子代理与团队成员）/ team_admin / role_admin，边界都是「编排自己的执行」，
    合并后模型少一次包选择；旧包名 `agent` 作为别名继续可解析。

    **文件工具只有一套名字**（read_file / edit_file / write_file / list_files），
    与其他标准工具一样受 `envelope.tools` 门控——不再有"对话恒开的另一套长名"。
    此前对话额外恒开一套长名（list_workspace_files / read_workspace_file /
    edit_workspace_file / write_workspace_file），于是同一个「读文件」模型会看到
    两个工具（实测混淆），而且 edit/write 恒开、绕过了能力包络（与 parity 测试
    "读写必须显式授予" 相矛盾）。

    进度只有**任务清单**一个载体（`plan{op:todo}` = 整份覆盖式 `todo_write`）：
    多步任务先列清单，推进时更新状态；界面在对话里直接渲染这张清单，右侧不再有
    独立的「计划流程」面板。``autonomous`` 参数保留（自主执行同样使用它）。

    纯聊天工作区（没有用户文件夹）：只有 `workspace`（中间存储）不下发；shell 与
    文件类工具照常可用，落点是托管目录（`workspace_service.exec_root`）。
    """
    from services.agent_zero.tools import registry as tool_reg
    from services.agent_zero.tools import packs as tool_packs
    from services.agent_zero.services import workspace_service as wsvc

    has_dir = wsvc.has_working_dir(workspace_id) if workspace_id else False
    allowed = envelope.get("tools") or []
    excluded = tool_reg.WORKSPACE_BOUND_TOOLS if not workspace_id else frozenset()
    use_packs = tool_packs.enabled()

    specs: List[Dict[str, Any]] = []
    if use_packs:
        # 工具包：一个包一个 function + op；命令类工具由 unpacked_specs 单独下发
        specs.extend(tool_packs.build_pack_specs(
            allowed, has_working_dir=has_dir, excluded=excluded))
        # 独立工具：shell / workspace / ask_user（vcs / codegraph / tool_job
        # 由 dev 包统一下发）。
        specs.extend(tool_packs.unpacked_specs(
            allowed, has_working_dir=has_dir, excluded=excluded))
    else:
        for s in tool_reg.build_tool_specs(allowed, has_working_dir=has_dir):
            if s["function"]["name"] not in excluded:
                specs.append(s)
    specs.extend(list(getattr(caps, "tool_specs", None) or []))
    specs.append(PROPOSE_TASK_TOOL)

    # MCP 服务工具（mcp__<server>__<tool>）：由 _stream_role 预先列好传入
    specs.extend(mcp_specs or [])

    dedup: List[Dict[str, Any]] = []
    seen = set()
    for s in specs:
        n = s.get("function", {}).get("name")
        if n in seen:
            continue
        seen.add(n)
        dedup.append(s)
    return dedup


# ── 工具调用可观测（情况分析）────────────────────────────
#
# 探索态对话里模型会自己调工具（读文件、列任务、派生 agent…），但这些全部发生在
# 服务端：用户只看到一段最终文本，看不到"它到底干了什么、成没成"。这里把
# 每次调用的关键信号抽出来随流推给前端。
#
# **不外发原始结果**：工具结果可能含大段文件内容或敏感路径，只给摘要 + 大小，
# 避免把几万字文件内容通过 SSE 推给浏览器、撑爆渲染与日志。


def _args_skeleton(args: Dict[str, Any]) -> Dict[str, Any]:
    """参数过大时只留结构（键 + 类型/长度），**不外发值本身**。

    大字段往往是文件正文 / 长文本，回显前 40 字符既能塞爆 SSE 又可能泄内容；
    改成「类型 + 字符数」既够定位问题，又不碰敏感数据。
    """
    out: Dict[str, Any] = {}
    for k, v in list((args or {}).items())[:12]:
        if isinstance(v, (dict, list)):
            out[str(k)] = f"<{type(v).__name__} len={len(v)}>"
        elif isinstance(v, str):
            out[str(k)] = f"<str len={len(v)}>"
        else:
            out[str(k)] = v
    return out


def _args_previewable(args: Dict[str, Any]) -> bool:
    """参数小且全是短字符串才原样外发。"""
    try:
        blob = json.dumps(args or {}, ensure_ascii=False)
    except Exception:
        return False
    if len(blob) > 800:
        return False
    def _leaf_ok(v: Any) -> bool:
        if isinstance(v, str):
            return len(v) <= 200
        if isinstance(v, (int, float, bool)) or v is None:
            return True
        return _leaf_ok(json.dumps(v, ensure_ascii=False))
    try:
        return all(_leaf_ok(v) for v in (args or {}).values())
    except Exception:
        return False


def _result_is_error(result: str) -> bool:
    """工具结果是否表示失败（约定：JSON 里的 error 字段）。"""
    try:
        obj = json.loads(result)
        return bool(isinstance(obj, dict) and obj.get("error"))
    except Exception:
        return False



def _model_facing_text(result: str) -> str:
    """给模型的工具结果正文：有 output 信封就剥掉壳（与任务链路同一函数）。"""
    unwrapped = file_ops.unwrap_display(result)
    return unwrapped if isinstance(unwrapped, str) else (result or "")


def _result_display(result: str) -> Optional[Dict[str, Any]]:
    """给前端的展示载荷（无则 None，不塞进 trace 免得白占地方）。"""
    return file_tools.envelope_of(result)


def _result_summary(result: str) -> str:
    """结果摘要：给一句人话 + 大小，不外发全文。

    文件类工具带 display 载荷时按载荷说（路径 / 行号区间 / diff 条数），
    否则会退化成「返回字段：output, display」——那时明明有路径可写，对用户零信息量。
    """
    return file_tools.brief(result, size=len(result or ""))


# 主循环不设轮次上限（vibe coding 要能一直推进到任务真正完成）。
# 0 = 不限；如需临时加保险闸，把它改成正整数即可。
_MAX_TURN_ROUNDS = 0


async def _exec_chat_tool(loop: Any, task_ctx: Dict[str, Any], config: Dict[str, Any],
                          node: Dict[str, Any], caps: Any, conv: Dict[str, Any],
                          user: str, role: Dict[str, Any], name: str,
                          args: Dict[str, Any]) -> Any:
    """对话阶段执行一次工具调用：复用中央分发器。

    文件读写 / 沙箱 / 包络门控全部走同一实现——对话侧**不再**有旁路
    （此前 `_read_tool` 直连 file_tools，绕过了门控）。
    """
    from services.agent_zero.tools import registry as tool_reg

    # 复用中央工具分发器：沙箱 / 包络门控与任务节点同一实现。
    return await loop._dispatch(task_ctx, config, node,
                                {"name": name, "arguments": args}, caps)


async def _completed(value: Any) -> Any:
    """把"已经算好的结果"包成协程，让统一的心跳/收尾逻辑照常处理。"""
    return value


def _capture_run_memory(conv: Dict[str, Any], role: Dict[str, Any],
                        sink: Dict[str, Any]) -> None:
    """run 结束自动沉淀一条 experience 记忆（纯结构化提取，不调 LLM）。

    对标 Claude Code 的 auto-memory / Cline memory bank：记忆不能只靠
    模型「自觉」——系统层面每轮把目标 / 工具 / 产物 / 结论沉淀下来，
    下一轮对话才有可检索的历史。失败不影响对话。
    """
    try:
        from services.agent_zero.services import memory_service

        memory_service.capture_run_experience(
            conv, sink.get("user_query") or "", role, sink)
        # 后台整理（10 分钟节流，不阻塞对话）：合并近重复、归档低价值记忆
        memory_service.schedule_consolidate(conv.get("workspace_id"))
    except Exception as e:
        logger.debug("自动记忆沉淀失败（不影响对话）: %s", e)


# ── 子代理（call_agent）：不限轮次 / 不限嵌套 ───────────────
# 参考图里的 SUBAGENT + 嵌套子步：把子任务分派给工作区内的另一个角色，
# 它在一个**不限轮次**的子循环里执行，结论回灌父模型，
# 其步骤作为 `display.type='subagent'` 的子轨迹在前端嵌套展示。
# 0 = 不限制；保留常量是为了以后需要时可临时收紧。
_SUBAGENT_MAX_DEPTH = 0
_SUBAGENT_MAX_ROUNDS = 0


def _bind_activity(tact: Any, act_id: str, task: "asyncio.Future") -> None:
    """把执行任务挂到前台活动登记上，供进程面板单独取消。"""
    if not act_id or tact is None:
        return
    try:
        tact.attach(act_id, task)
    except Exception:
        logger.debug("绑定前台工具活动失败（忽略）", exc_info=True)


def _outer_cancelled() -> bool:
    """当前协程是否被外层取消（区别于 inner task 被进程面板单独取消）。"""
    try:
        cur = asyncio.current_task()
        return bool(cur is not None and cur.cancelling())
    except Exception:
        return True


async def _await_uncancelled(task: "asyncio.Future", timeout: float) -> bool:
    """等 task 至多 timeout 秒（**不取消它**）；返回是否已结束。

    与裸 `wait_for(shield(task))` 的区别在**取消语义**：外层被强制停止时，
    shield 会把 CancelledError 只抛给外层，内层 task 继续裸跑——停止之后工具
    还在写文件 / 跑命令，等于「停不下来」。这里在收到取消时先把 task 一起
    取消，再原样向上抛，保证停止真的传到工具层。

    但「从进程面板停单个工具」只会取消 inner task：此时外层没有 cancelling，
    不能把整个 run 一起取消，返回 True 让调用方按「工具已取消」继续。
    """
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    except asyncio.CancelledError:
        if task.cancelled() and not _outer_cancelled():
            return True
        task.cancel()
        raise


async def _ping_until_done(task: "asyncio.Future", run_id: str, *,
                            label: str, rid: str, rname: str,
                            waiting: str = "subagent"):
    """等待子代理/团队任务，同时每 20s 产出一条 ping。

    这些委派是**直接 await** 的（不走普通工具等待循环），若不发心跳，
    整条 run 会连续静默几分钟，被运行级看门狗误判成卡死。
    """
    from services.agent_zero.task_layer import run_service

    waited = 0.0
    while not task.done():
        if run_id and run_service.is_stop_requested(run_id):
            task.cancel()
            break
        if await _await_uncancelled(task, TOOL_KEEPALIVE_SECONDS):
            break
        waited += TOOL_KEEPALIVE_SECONDS
        yield {"ping": True, "elapsed_s": int(waited), "name": label,
               "waiting": waiting, "role_id": rid, "role_name": rname}


async def _await_subagent_result(task: "asyncio.Future", label: str,
                                  run_id: str = "") -> str:
    """收子代理结果；进程面板单独取消子任务时按「已取消」回灌，不中断整轮。"""
    try:
        return await task
    except asyncio.CancelledError:
        if _outer_cancelled():
            raise
        # 用户点的是「停止运行」而不是进程面板的单条停止：继续向上取消。
        if run_id:
            from services.agent_zero.task_layer import run_service

            if run_service.is_stop_requested(run_id):
                raise
        return json.dumps({"error": f"{label}已取消"}, ensure_ascii=False)


_SUBAGENT_PREVIEW_INTERVAL_S = 1.0


def _forward_subagent_event(run_id: str, call_id: str, agent: str, goal: str,
                            ev: Dict[str, Any], state: Dict[str, Any]) -> None:
    """把子代理的关键事件写进 run 事件流（前端据此打开实时的子代理窗口）。

    正文/思考按 1s 节流为预览，避免每个 token 落一条库；工具调用事件全量。
    """
    if not run_id:
        return
    payload: Dict[str, Any] = {
        "call_id": call_id, "agent": agent, "goal": goal,
        "ts": int(time.time() * 1000),
    }
    now = time.monotonic()
    if "tool_call" in ev:
        payload.update(event="tool_call", tool=ev.get("tool_call") or {})
    elif "tool_result" in ev:
        payload.update(event="tool_result", tool=ev.get("tool_result") or {})
    elif "thinking" in ev:
        if now - float(state.get("last_text") or 0) < _SUBAGENT_PREVIEW_INTERVAL_S:
            return
        state["last_text"] = now
        payload.update(event="progress", kind="thinking",
                       text=str(ev.get("thinking") or "")[-400:])
    elif "delta" in ev:
        if now - float(state.get("last_text") or 0) < _SUBAGENT_PREVIEW_INTERVAL_S:
            return
        state["last_text"] = now
        payload.update(event="progress", kind="text",
                       text=str(ev.get("delta") or "")[-400:])
    elif "error" in ev:
        payload.update(event="error", error=str(ev.get("error") or "")[:800])
    elif "done" in ev:
        payload.update(event="done")
    else:
        return
    try:
        from services.agent_zero.task_layer import run_service

        run_service.append_event(run_id, "subagent_event",
                                 {"subagent_event": payload})
    except Exception:
        logger.debug("转发子代理事件失败", exc_info=True)


def _subagent_messages(goal: str, *, instructions: str = "",
                       context: str = "") -> List[Dict[str, Any]]:
    """子代理的**唯一**上下文：目标 + 补充指令 + 父代理显式选择分享的内容。

    不读取父对话历史，避免子代理被无关的多轮消息带偏，也避免父上下文无限膨胀。
    """
    blocks = [f"目标：{goal}"]
    if instructions:
        blocks.append(f"补充指令：{instructions}")
    if context:
        blocks.append(
            "父代理提供的上下文（可能不完整；需要时用工具自行查证，不要当作全部事实）：\n"
            + context)
    blocks.append(
        "只完成这个子任务。你看不到父对话历史；完成后只返回简洁结论、关键证据和产物路径。")
    return [{"role": "user",
             "content": "<subagent-task>\n" + "\n\n".join(blocks) + "\n</subagent-task>"}]


def _narrow_role_tools(role: Dict[str, Any],
                       requested: Any) -> Dict[str, Any]:
    """按调用方给的 tools 收窄角色包络（只收窄、不放大）。

    团队成员默认用自己的 `roles.envelope.tools`；delegate 传了 tools 时再取交集，
    不在成员包络里的工具会被忽略（不能借委派绕过角色权限）。
    """
    if not isinstance(requested, list):
        return role
    req = [str(x).strip() for x in requested if str(x).strip()]
    if not req:
        return role
    env = dict(role.get("envelope") or {})
    parent = [str(x) for x in (env.get("tools") or [])]
    allowed = set(req)
    env["tools"] = [t for t in parent if t in allowed]
    return {**role, "envelope": env}


async def _exec_call_agent(conv: Dict[str, Any], user: str, role: Dict[str, Any],
                           args: Dict[str, Any], depth: int,
                           call_id: str = "") -> str:
    """调用工作区内的另一个角色执行子目标，返回工具结果 JSON（含子轨迹展示载荷）。"""
    if _SUBAGENT_MAX_DEPTH and depth >= _SUBAGENT_MAX_DEPTH:
        return json.dumps({"error": f"子代理嵌套过深（最多 {_SUBAGENT_MAX_DEPTH} 层）"}, ensure_ascii=False)
    role_id = str((args or {}).get("role_id") or "").strip()
    goal = str((args or {}).get("goal") or "").strip()
    if not goal and not role_id:
        return json.dumps({"error": "缺少 role_id / goal"}, ensure_ascii=False)
    child = zero_store.get_role(role_id) if role_id else None
    if role_id and not child:
        return json.dumps({"error": f"角色不存在: {role_id}"}, ensure_ascii=False)
    if child and zero_store.public_workspace_id(child.get("group_id")) != (conv.get("workspace_id") or ""):
        return json.dumps({"error": "该角色不属于当前工作区，不能调用"}, ensure_ascii=False)
    if child is None:
        child = role        # 未指定 role_id → 由当前角色自己再跑一轮
    child = _narrow_role_tools(child, (args or {}).get("tools"))
    from services.agent_zero.task_layer import prompt_builder

    child_sink: Dict[str, Any] = {}
    try:
        from services.agent_zero.services import ask_service

        run_id = ask_service.current_run_id()
        fstate: Dict[str, Any] = {}
        async for ev in _stream_role(
                conv, user, child, child_sink, autonomous=False,
                _depth=depth + 1, _max_rounds=_SUBAGENT_MAX_ROUNDS,
                isolated=True,
                context_messages=_subagent_messages(
                    goal, context=str((args or {}).get("context") or "").strip()[:8000]),
                system_addon=prompt_builder.SUBAGENT):
            _forward_subagent_event(run_id, call_id or f"sub-{id(child_sink)}",
                                    str(child.get("name") or "子代理"), goal, ev, fstate)
    except Exception as e:  # 子代理失败不阻断父循环
        logger.warning("子代理执行失败: %s", e)
        return json.dumps({"error": f"子代理执行失败: {e}"}, ensure_ascii=False)
    text = str(child_sink.get("text") or "").strip()
    display = {
        "type": "subagent",
        "agent": {"id": child.get("id"), "name": child.get("name"),
                  "avatar": child.get("avatar") or ""},
        "goal": goal,
        "trace": (child_sink.get("trace") or []),
    }
    return json.dumps({"output": text or "(子代理未返回正文)", "display": display},
                      ensure_ascii=False)


def _ensure_team_tool(specs: List[Dict[str, Any]]) -> None:
    """对话绑定团队后让委派工具立即可用（就地改本轮工具清单）。

    `delegate` 合并了子代理与团队成员，本身不分包下发：有工具包时它已经作为
    plan 包的 delegate op 存在；没有包时它在独立工具里（gated=False）恒在下发。
    """
    from services.agent_zero.tools import registry as tool_reg

    names = {(s.get("function") or {}).get("name") for s in specs}
    if "delegate" in names or "call_team" in names or "plan" in names:
        return
    d = tool_reg.get_tool("delegate")
    if d is not None:
        specs.append(d.schema())


def _drop_team_tool(specs: List[Dict[str, Any]]) -> None:
    for i, s in enumerate(specs):
        if (s.get("function") or {}).get("name") == "call_team":
            specs.pop(i)
            return


def _role_envelope(tools: Any) -> Dict[str, Any]:
    """角色能力包络：给了 tools 就收窄，否则默认全集。"""
    from services.agent_zero.stores import zero_store
    from services.agent_zero.tools import registry as _reg

    env = dict(zero_store.DEFAULT_ENVELOPE)
    picked = [str(t).strip() for t in (tools or []) if str(t).strip()]
    if picked:
        known = set(_reg.known_codes())
        env["tools"] = [t for t in dict.fromkeys(_reg.canonical(x) for x in picked)
                        if t in known]
    return env


def _role_public(role: Dict[str, Any]) -> Dict[str, Any]:
    env = role.get("envelope") if isinstance(role.get("envelope"), dict) else {}
    dept_id = str(role.get("department_id") or "")
    return {
        "id": role.get("id"), "name": role.get("name"),
        "title": role.get("title") or "",
        "department_id": dept_id,
        "department": zero_store.department_path(dept_id) if dept_id else "",
        "workspace_id": str(role.get("group_id") or ""),
        "expertise": role.get("expertise") or "",
        "persona": role.get("persona") or "",
        "avatar": role.get("avatar") or "",
        "scope": role.get("scope") or "persistent",
        "source": role.get("source") or "user",
        "session_id": role.get("session_id") or "",
        "model_code": role.get("model_code") or "",
        "tools": [str(t) for t in (env.get("tools") or [])],
        "status": str(env.get("status") or "online"),
    }


def _exec_app_manage(conv: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """工单助手 专用：跨会话处理授权 / 通知 / 权限（代替用户管理 App）。"""
    from services.agent_zero.services import ask_service, attention_service
    from services.agent_zero.task_layer import permissions, run_service

    a = args or {}
    action = str(a.get("action") or "").strip().lower()
    snap = attention_service.snapshot()
    if action in ("status", "list"):
        return {
            "counts": snap["counts"],
            "approvals": [
                {k: x.get(k) for k in ("id", "conversation_id", "conversation_title",
                                        "tool", "risk", "note", "role_id", "role_name",
                                        "team_id", "team_name")}
                for x in snap["approvals"][:20]
            ],
            "questions": [
                {k: x.get(k) for k in ("id", "conversation_id", "question", "options")}
                for x in snap["questions"][:20]
            ],
            "unread": [
                {k: x.get(k) for k in ("id", "title", "last_message")}
                for x in snap["unread"][:20]
            ],
            "running": [
                {k: x.get(k) for k in ("id", "conversation_id", "title", "status")}
                for x in snap["running"][:20]
            ],
            "sessions": [
                {k: x.get(k) for k in ("id", "title", "permission_mode")}
                for x in snap["sessions"][:30]
            ],
            "policy": snap["policy"],
        }
    if action == "approvals":
        return {"items": snap["approvals"]}
    if action == "questions":
        return {"items": snap["questions"]}
    if action == "approve":
        approval_id = str(a.get("approval_id") or "").strip()
        decision = str(a.get("decision") or "approve_once").strip()
        if not approval_id:
            return {"error": "缺少 approval_id"}
        try:
            out = permissions.decide_approval(approval_id, decision)
        except ValueError as e:
            return {"error": str(e)}
        return {"ok": True, "approval": out}
    if action == "answer":
        qid = str(a.get("question_id") or "").strip()
        if not qid:
            return {"error": "缺少 question_id"}
        out = ask_service.answer(qid, {
            "selected": a.get("selected") or [],
            "other": a.get("other") or "",
            "ignored": bool(a.get("ignored")),
        })
        if out is None:
            return {"error": "问题不存在或已结束"}
        return {"ok": True, "answer": out}
    if action == "mark_read":
        if a.get("all"):
            ids = [str(x.get("id")) for x in snap["unread"]]
        else:
            cid = str(a.get("conversation_id") or "").strip()
            ids = [cid] if cid else []
        if not ids:
            return {"ok": True, "marked": 0}
        from services.agent_zero.task_layer import conversation as conv_mod

        for cid in ids:
            try:
                conv_mod.mark_conversation_read(cid)
            except Exception:
                logger.debug("标记已读失败: %s", cid, exc_info=True)
        return {"ok": True, "marked": len(ids)}
    if action == "set_permission":
        cid = str(a.get("conversation_id") or "").strip()
        mode = str(a.get("mode") or "").strip()
        if not cid or not mode:
            return {"error": "缺少 conversation_id / mode"}
        try:
            mode = permissions.set_conversation_mode(cid, mode)
        except ValueError as e:
            return {"error": str(e)}
        return {"ok": True, "conversation_id": cid, "mode": mode}
    if action == "set_default_permission":
        mode = str(a.get("mode") or "").strip()
        if not mode:
            return {"error": "缺少 mode"}
        try:
            policy = permissions.set_policy({"default": mode})
        except ValueError as e:
            return {"error": str(e)}
        return {"ok": True, "policy": policy}
    if action == "proactive_status":
        from services.agent_zero.services import schedule_service

        cid = str(a.get("conversation_id") or "").strip() or None
        return {
            "policy": schedule_service.get_proactive_policy(),
            "items": [
                {k: item.get(k) for k in ("id", "conversation_id", "title",
                                           "next_run_at", "last_run_at", "status")}
                for item in schedule_service.proactive_tasks(cid, limit=50)
            ],
        }
    if action == "set_proactive":
        from services.agent_zero.services import schedule_service

        patch = {key: a.get(key) for key in (
            "enabled", "max_per_day", "min_gap_minutes", "quiet_start", "quiet_end",
        ) if key in a}
        return {"ok": True, "policy": schedule_service.save_proactive_policy(patch)}
    if action == "cancel_proactive":
        from services.agent_zero.services import schedule_service

        task_id = str(a.get("task_id") or "").strip()
        if not task_id:
            return {"error": "缺少 task_id"}
        return {"ok": schedule_service.cancel_proactive(task_id), "task_id": task_id}
    if action == "stop_run":
        rid = str(a.get("run_id") or "").strip()
        cid = str(a.get("conversation_id") or "").strip()
        if not rid and cid:
            run = run_service.running_run(cid)
            rid = str((run or {}).get("id") or "")
        if not rid:
            return {"error": "缺少 run_id / conversation_id，或该会话没有运行中的 run"}
        stopped = run_service.request_stop(rid)
        forced = False
        try:
            from services.agent_zero.task_layer import conv_worker

            forced = bool(conv_worker.get_worker().force_stop(rid))
        except Exception:
            logger.debug("强制停止失败（已请求停止）: %s", rid, exc_info=True)
        return {"ok": True, "run_id": rid, "stop_requested": bool(stopped),
                "forced": forced}
    return {"error": f"非法 action: {action}"}


# 由界面执行的会话指令（与前端斜杠命令共用同一套处理路径）。
# `clear` 不走这里：它可能发生在运行中途，必须由后端立即落清空标记，
# 否则界面重置实时流会把正在输出的这一轮擦掉。
SESSION_UI_ACTIONS = frozenset({
    "compact", "new", "rename", "delete", "clean", "stop", "work", "read",
    "model", "attach", "conv", "settings", "about", "theme",
})


def _exec_session_manage(conv: Dict[str, Any], args: Dict[str, Any],
                         *, user: str = "") -> Dict[str, Any]:
    """会话指令：把用户用自然语言提出的操作落到具体动作。

    - `clear`：后端立即执行（插一条清空标记，历史消息保留）；
    - 其余动作返回 `done=False`，由前端用斜杠命令同一套逻辑执行
      （新建 / 重命名 / 删除 / 打开设置等本来就是界面状态，只有界面知道怎么做）。

    机器人来源（`user=bot:*`）只能清当前栏目：清空全部会话会连带别的会话，
    不属于机器人可自动执行的安全指令。
    """
    a = args or {}
    action = str(a.get("action") or "").strip().lower()
    title = str(a.get("title") or "").strip()
    scope = str(a.get("scope") or "current").strip().lower()
    cid = str(conv.get("id") or "")
    if not cid:
        return {"error": "缺少会话 id"}
    if action == "clear":
        if scope not in ("current", "all"):
            scope = "current"
        if scope == "all":
            if str(user or "").startswith("bot:"):
                return {"error": "机器人只能清空当前栏目；清空全部会话请在 AtlasX 里操作"}
            count = clear_all_contexts()
            return {"ok": True, "action": "clear", "scope": scope, "done": True,
                    "cleared": count,
                    "note": f"已清空 {count} 个会话的模型上下文（历史消息保留）"}
        ok = clear_context(cid)
        if not ok:
            return {"error": "清空上下文失败（会话不存在或已被删除）"}
        return {"ok": True, "action": "clear", "scope": scope, "done": True,
                "cleared": 1,
                "note": "当前会话上下文已清空（历史消息保留）"}
    if action == "rename" and not title:
        return {"error": "rename 需要 title（新的会话标题）"}
    if action in SESSION_UI_ACTIONS:
        return {"ok": True, "action": action, "done": False, "arg": title,
                "note": "已把指令发给界面执行；用一句话确认即可，不要重复操作。"}
    return {"error": f"未知会话指令: {action}（可选：clear / compact / new / rename / "
                     "delete / clean / stop / work / read / model / attach / conv / "
                     "settings / about / theme）"}


def _exec_department_manage(args: Dict[str, Any]) -> Dict[str, Any]:
    """组织部门工具：list / create / update / delete（多级，可移动）。"""
    from services.agent_zero.services import department_service as _dsvc

    a = args or {}
    action = str(a.get("action") or "").strip().lower()
    try:
        if action == "list":
            return {"departments": _dsvc.list_departments()}
        if action == "create":
            dept = _dsvc.create_department(
                str(a.get("name") or ""),
                parent_id=a.get("parent_id"),
                sort=int(a.get("sort") or 0),
                avatar=str(a.get("avatar") or ""),
            )
            return {"ok": True, "department": dept}
        dept_id = str(a.get("department_id") or "").strip()
        if action in ("update", "delete") and not dept_id:
            return {"error": "缺少 department_id"}
        if action == "update":
            kwargs: Dict[str, Any] = {
                "name": a.get("name"),
                "sort": a.get("sort"),
            }
            if "parent_id" in a:
                kwargs["parent_id"] = a.get("parent_id")
            if "avatar" in a:
                kwargs["avatar"] = a.get("avatar") or ""
            dept = _dsvc.update_department(dept_id, **kwargs)
            return {"ok": True, "department": dept}
        if action == "delete":
            out = _dsvc.delete_department(dept_id)
            return {"ok": True, **out}
    except _dsvc.DepartmentError as e:
        return {"error": str(e)}
    return {"error": f"非法 action: {action}"}


def _exec_proactive_send(conv: Dict[str, Any], role: Dict[str, Any],
                         args: Dict[str, Any]) -> Dict[str, Any]:
    """安排一条会话内主动消息；全局 Agent 的策略在这里统一兜底。"""
    from services.agent_zero.services import schedule_service

    a = args or {}
    try:
        task = schedule_service.create_proactive(
            conv, role,
            str(a.get("message") or ""),
            delay_minutes=a.get("delay_minutes") or 1,
            reason=str(a.get("reason") or ""),
        )
    except ValueError as e:
        return {"error": str(e)}
    return {
        "ok": True,
        "task_id": task.get("id"),
        "conversation_id": task.get("conversation_id"),
        "next_run_at": task.get("next_run_at"),
        "note": "已安排主动消息；到点后会在本会话里由你直接生成回复。",
    }


async def _exec_schedule_manage(args: Dict[str, Any]) -> Dict[str, Any]:
    """定时 / 自由触发任务工具：list / create / update / delete / run / pause / resume。"""
    from services.agent_zero.services import schedule_service as _ssvc

    a = args or {}
    action = str(a.get("action") or "").strip().lower()
    try:
        if action == "list":
            return {"tasks": _ssvc.list_tasks(limit=50)}
        if action == "create":
            payload = {
                key: a.get(key) for key in (
                    "title", "prompt", "trigger_mode", "schedule_kind", "schedule",
                    "condition", "role_id", "workspace_id", "conversation_id",
                    "run_mode", "auto_execute", "max_runs",
                ) if key in a
            }
            return {"ok": True, "task": _ssvc.create_task(payload)}
        task_id = str(a.get("task_id") or "").strip()
        if action in ("update", "delete", "run", "pause", "resume") and not task_id:
            return {"error": "缺少 task_id"}
        if action == "update":
            patch = {
                key: a.get(key) for key in (
                    "title", "prompt", "trigger_mode", "schedule_kind", "schedule",
                    "condition", "role_id", "workspace_id", "conversation_id",
                    "run_mode", "auto_execute", "max_runs", "status",
                ) if key in a
            }
            return {"ok": True, "task": _ssvc.update_task(task_id, patch)}
        if action == "delete":
            return {"ok": _ssvc.delete_task(task_id), "task_id": task_id}
        if action == "run":
            return {"ok": True, "task": await _ssvc.run_task(task_id, trigger="manual")}
        if action in ("pause", "resume"):
            return {"ok": True,
                    "task": _ssvc.pause_task(task_id, paused=(action == "pause"))}
    except Exception as e:
        return {"error": str(e)}
    return {"error": f"非法 action: {action}"}


def _role_workspace_value(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """角色绑定的工作区：返回 (ws_id, error)。空串 = 不绑定。"""
    from services.agent_zero.stores import zero_store

    ws_id = str(raw or "").strip()
    if not ws_id:
        return "", None
    ws = zero_store.get_workspace(ws_id)
    if not ws or ws.get("archived"):
        return None, f"工作区不存在或已归档: {ws_id}"
    return ws_id, None


def _exec_role_manage(conv: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """角色库工具：list / create / update / promote / delete。

    LLM 创建默认临时角色（属于当前会话），并自动加入当前群；用户想复用时 promote。
    """
    from services.agent_zero.services import team_service as _tsvc
    from services.agent_zero.stores import zero_store

    a = args or {}
    action = str(a.get("action") or "").strip().lower()
    conv_id = str(conv.get("id") or "")
    if action == "list":
        scope = "temporary" if a.get("scope") == "temporary" else None
        rows = zero_store.list_roles(limit=100)
        items = [_role_public(r) for r in rows
                 if str(r.get("id") or "") != zero_store.GLOBAL_ROLE_ID
                 and ((r.get("scope") or "persistent") == "persistent"
                      or (r.get("session_id") == conv_id))]
        if scope == "temporary":
            items = [x for x in items if x["scope"] == "temporary"]
        return {"roles": items}

    if action == "create":
        name = str(a.get("name") or "").strip()
        if not name:
            return {"error": "角色名不能为空"}
        explicit_scope = str(a.get("scope") or "").strip()
        scope = explicit_scope or "temporary"
        if scope not in ("temporary", "persistent"):
            return {"error": f"非法 scope: {scope}"}
        dept_id = str(a.get("department_id") or "").strip()
        if dept_id and not zero_store.get_department(dept_id):
            return {"error": f"部门不存在: {dept_id}"}
        ws_id, ws_err = _role_workspace_value(a.get("workspace_id"))
        if ws_err:
            return {"error": ws_err}
        # 未显式要求临时角色时，优先复用同名持久角色：模型经常把
        # 「把 X 加入群组」误走成 create，不能再造一个无头像的临时角色。
        reused = False
        role = None
        if not explicit_scope:
            key = name.lower()
            matches = [
                row for row in zero_store.list_roles(limit=2000)
                if (str(row.get("name") or "").strip().lower() == key
                    and str(row.get("scope") or "persistent") == "persistent"
                    and str(row.get("id") or "") != zero_store.GLOBAL_ROLE_ID)
            ]
            if matches:
                matches.sort(key=lambda row: (
                    0 if str(row.get("avatar") or "").strip() else 1,
                    str(row.get("created_at") or ""),
                    str(row.get("id") or ""),
                ))
                role = matches[0]
                reused = True
                # 显式指定工作区时，即使复用同名角色也按请求切换。
                if "workspace_id" in a and str(role.get("group_id") or "") != str(ws_id or ""):
                    role = zero_store.upsert_role({
                        **role,
                        "group_id": ws_id or None,
                    })
        if role is None:
            role = zero_store.upsert_role({
                "id": zero_store.new_id("role"),
                "group_id": ws_id or None,
                "name": name,
                "title": a.get("title") or "",
                "department_id": dept_id,
                "persona": a.get("persona") or "",
                "expertise": a.get("expertise") or "",
                "envelope": _role_envelope(a.get("tools")),
                "model_code": a.get("model_code"),
                "scope": scope,
                "source": "llm",
                "session_id": conv_id if scope == "temporary" else "",
            })
        added = False
        team_id = str(conv.get("team_id") or "").strip()
        if team_id and scope != "single" and a.get("add_to_team", True):
            team = _tsvc.get_team(team_id)
            if team:
                members = [{k: m.get(k) for k in ("id", "name", "expertise")
                            if m.get(k)} for m in (team.get("members") or [])]
                members.append({"id": role["id"], "name": role["name"],
                                "expertise": role.get("expertise") or ""})
                _tsvc.update_team(team_id, members=members)
                added = True
        out = {"ok": True, "role": _role_public(role), "added_to_team": added}
        if reused:
            out["reused"] = True
        return out

    role_id = str(a.get("role_id") or "").strip()
    current = zero_store.get_role(role_id) if role_id else None
    if action in ("update", "promote", "delete") and not current:
        return {"error": f"角色不存在: {role_id}"}
    if action == "update":
        status = a.get("status")
        if status is not None and status not in ("online", "offline"):
            return {"error": "状态必须是 online 或 offline"}
        dept_id = current.get("department_id")
        if "department_id" in a:
            dept_id = str(a.get("department_id") or "").strip()
            if dept_id and not zero_store.get_department(dept_id):
                return {"error": f"部门不存在: {dept_id}"}
        ws_id = current.get("group_id")
        ws_changed = "workspace_id" in a
        if ws_changed:
            ws_id, ws_err = _role_workspace_value(a.get("workspace_id"))
            if ws_err:
                return {"error": ws_err}
        merged = {
            "id": role_id,
            "group_id": ws_id or None,
            "name": a.get("name") or current.get("name"),
            "title": (a.get("title") if "title" in a and a.get("title") is not None
                      else current.get("title")),
            "department_id": dept_id,
            "persona": a.get("persona") if a.get("persona") is not None else current.get("persona"),
            "expertise": (a.get("expertise") if a.get("expertise") is not None
                          else current.get("expertise")),
            "envelope": (_role_envelope(a.get("tools")) if a.get("tools") is not None
                         else current.get("envelope") or {}),
            "model_code": (a.get("model_code") if a.get("model_code") is not None
                           else current.get("model_code")),
            "scope": current.get("scope") or "persistent",
            "source": current.get("source") or "llm",
            "session_id": current.get("session_id"),
        }
        if a.get("tools") is not None:
            merged["envelope"]["status"] = (current.get("envelope") or {}).get("status", "online")
        if status is not None:
            merged["envelope"] = dict(merged["envelope"])
            merged["envelope"]["status"] = status
        role = zero_store.upsert_role(merged)
        return {"ok": True, "role": _role_public(role)}
    if action == "promote":
        merged = dict(current)
        merged.update({"scope": "persistent", "session_id": ""})
        role = zero_store.upsert_role(merged)
        return {"ok": True, "role": _role_public(role)}
    if action == "delete":
        if role_id in zero_store.SYSTEM_ROLE_IDS:
            return {"error": "系统角色不能删除"}
        zero_store.delete_role(role_id)
        return {"ok": True, "deleted": role_id}
    return {"error": f"非法 action: {action}"}


def _exec_team_manage(conv: Dict[str, Any], ws_id: str,
                      args: Dict[str, Any],
                      specs: List[Dict[str, Any]]) -> str:
    """Agent 团队**完全动态**定义：没有界面配置入口，团队由模型按任务需要
    创建 / 调整 / 删除，并绑定到当前对话用于 call_team 委派。"""
    from services.agent_zero.services import team_service as _tsvc

    action = str((args or {}).get("action") or "").strip().lower()
    # 全局 Agent 只处理审批，本身不是群聊：建群后不能把它自己绑进群，
    # 否则群组页「打开群聊」会复用它，群聊被显示成全局 Agent。
    is_global = str(conv.get("zone") or "") == GLOBAL_AGENT_ZONE
    try:
        if action == "list":
            # 只列真正的群组；participants-* 是会话私有参与角色，不属于用户群组。
            return json.dumps({"teams": _tsvc.list_teams(ws_id, kind="group")},
                              ensure_ascii=False)
        if action == "create":
            team = _tsvc.create_team(
                ws_id, str(args.get("name") or ""),
                str(args.get("description") or ""),
                args.get("leader") or {"name": "主持"},
                args.get("members") or [],
                mode=args.get("mode"),
                max_speakers=args.get("max_speakers"))
            bound = (bool(args.get("bind", True)) and bool(team.get("id"))
                     and not is_global)
            if bound:
                _tsvc.bind_team(conv["id"], team["id"])
                conv["team_id"] = team["id"]
                _ensure_team_tool(specs)
            payload = {"ok": True, "team": team, "bound": bound,
                       "roster": _tsvc.roster_text(team) if team else ""}
            if is_global:
                payload["note"] = ("全局 Agent 不加入群组；群组已创建，"
                                   "请在「群组」页打开群聊")
            return json.dumps(payload, ensure_ascii=False)
        if action == "update":
            tid = str(args.get("team_id") or "")
            target = _tsvc.get_team(tid) or {}
            if str(target.get("kind") or "group") == "participants":
                return json.dumps({"error": "会话私有参与角色不是群组，不能在这里修改"},
                                  ensure_ascii=False)
            team = _tsvc.update_team(tid,
                                     name=args.get("name"),
                                     description=args.get("description"),
                                     leader=args.get("leader"),
                                     members=args.get("members"),
                                     mode=args.get("mode"),
                                     max_speakers=args.get("max_speakers"))
            return json.dumps({"ok": True, "team": team}, ensure_ascii=False)
        if action == "delete":
            tid = str(args.get("team_id") or "")
            target = _tsvc.get_team(tid) or {}
            if str(target.get("kind") or "group") == "participants":
                return json.dumps({"error": "会话私有参与角色不是群组，不能在这里删除"},
                                  ensure_ascii=False)
            ok = _tsvc.delete_team(tid)
            if ok and str(conv.get("team_id") or "") == tid:
                conv["team_id"] = None
                _drop_team_tool(specs)
            return json.dumps({"ok": ok}, ensure_ascii=False)
        if action in ("bind", "unbind"):
            tid = None if action == "unbind" else str(args.get("team_id") or "")
            if action == "bind" and is_global:
                return json.dumps(
                    {"error": "全局 Agent 不能加入群组；请在「群组」页打开群聊"},
                    ensure_ascii=False)
            if tid:
                target = _tsvc.get_team(tid) or {}
                if not target:
                    return json.dumps({"error": f"团队不存在: {tid}"}, ensure_ascii=False)
                if str(target.get("kind") or "group") == "participants":
                    return json.dumps(
                        {"error": "会话私有参与角色不是群组，不能用 team_manage 绑定"},
                        ensure_ascii=False)
            _tsvc.bind_team(
                conv["id"], tid,
                notice=(None if tid else "你已退出群聊"))
            conv["team_id"] = tid
            if tid:
                _ensure_team_tool(specs)
            else:
                _drop_team_tool(specs)
            return json.dumps({"ok": True, "bound": bool(tid)}, ensure_ascii=False)
        return json.dumps({"error": f"未知 action: {action}"}, ensure_ascii=False)
    except Exception as e:
        logger.warning("team_manage 失败: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def _exec_call_team(conv: Dict[str, Any], user: str, role: Dict[str, Any],
                          args: Dict[str, Any], depth: int,
                          call_id: str = "") -> str:
    """把子任务委派给**本对话绑定的团队成员**（角色受限子循环执行）。

    后台任务的成员 run 只能派生**子代理**，不能再嵌套派发团队任务（否则会
    无限套娃、并绕过任务归属）。这里按当前 run 的 team_task_id 判断，避免
    把「是不是任务成员」一路透传到每个调用点。
    """
    try:
        from services.agent_zero.services import ask_service
        from services.agent_zero.task_layer import run_service as _rs

        _current_run = ask_service.current_run_id()
        if _current_run and str((_rs.get_run(_current_run) or {})
                                 .get("team_task_id") or "").strip():
            return json.dumps({
                "error": "后台任务成员只能派生子代理（delegate target=subagent），"
                         "不能再嵌套派发团队任务",
            }, ensure_ascii=False)
    except Exception:
        logger.debug("读取当前 run 的任务归属失败（按允许处理）", exc_info=True)
    from services.agent_zero.services import team_service

    team = team_service.team_for_conv(conv)
    if not team:
        return json.dumps({"error": "本对话未绑定 Agent 团队，不能委派团队成员"},
                          ensure_ascii=False)

    def _member_role(mid: str, mname: str) -> Optional[Dict[str, Any]]:
        for m in (team.get("members") or []):
            if (mid and m.get("id") == mid) or (mname and m.get("name") == mname):
                role = zero_store.get_role(str(m.get("id") or ""))
                if role and str(role.get("id") or "") != zero_store.GLOBAL_ROLE_ID:
                    return role
        return None

    a = args or {}
    raw = a.get("assignments")
    items: List[Dict[str, Any]] = []
    if isinstance(raw, list) and raw:
        for spec in raw:
            if not isinstance(spec, dict):
                continue
            items.append({
                "member_id": str(spec.get("member_id") or spec.get("id") or "").strip(),
                "member_name": str(spec.get("member_name") or spec.get("name") or "").strip(),
                "goal": str(spec.get("goal") or spec.get("task") or "").strip(),
                "context": str(spec.get("context") or "").strip(),
            })
    else:
        items.append({
            "member_id": str(a.get("member_id") or "").strip(),
            "member_name": str(a.get("member_name") or "").strip(),
            "goal": str(a.get("goal") or "").strip(),
            "context": str(a.get("context") or "").strip(),
        })

    # 不再同步等待成员：主持在群里 @ 成员，成员作为**独立后台 run**执行。
    # 多个 assignment 会同时派发，由 worker 并行执行（见 run_service 的并行认领）。
    assignments: List[Dict[str, Any]] = []
    errors: List[str] = []
    for item in items:
        child = _member_role(item["member_id"], item["member_name"])
        if not child:
            errors.append(f"没有成员 {item['member_id'] or item['member_name'] or '（未指定）'}")
            continue
        goal = item["goal"]
        if not goal:
            errors.append(f"成员「{child.get('name')}」缺少 goal")
            continue
        assignments.append({
            "role_id": str(child.get("id") or ""),
            "role_name": str(child.get("name") or ""),
            "goal": goal,
            "context": item["context"],
            "role": child,
        })

    if not assignments:
        avail = ", ".join(str(m.get("name") or m.get("id"))
                          for m in (team.get("members") or [])) or "（无）"
        detail = "；".join(errors) if errors else "缺少成员 / goal"
        return json.dumps({
            "error": f"团队「{team.get('name')}」没有可委派的成员：{detail}；可用: {avail}",
        }, ensure_ascii=False)

    # 单轮参与上限：模型有时会为了「覆盖」一次派发全员，既贵又慢。
    # 超出上限的 assignment 不静默丢弃，而是明确告知模型（它可在下一轮补派）。
    try:
        participant_limit = max(1, int(team.get("max_speakers") or 3))
    except (TypeError, ValueError):
        participant_limit = 3
    skipped: List[Dict[str, Any]] = []
    if len(assignments) > participant_limit:
        skipped = [{"role_id": a["role_id"], "role_name": a["role_name"]}
                   for a in assignments[participant_limit:]]
        assignments = assignments[:participant_limit]
        errors.append(
            f"本轮参与上限为 {participant_limit} 人，已优先派发前 {participant_limit} 个成员；"
            f"未派发：{'、'.join(x['role_name'] for x in skipped)}。"
            "如确有需要，下一轮再补派，或让用户调高群组的单轮参与上限。")

    from services.agent_zero.services import team_task_service
    from services.agent_zero.task_layer import conv_worker

    try:
        persist = bool(get_conversation(str(conv.get("id") or "")))
    except Exception:
        persist = False
    try:
        task = team_task_service.create_task(
            conv, team, role, assignments, user, persist=persist)
    except Exception as e:
        logger.exception("创建群组后台任务失败")
        return json.dumps({"error": f"创建后台任务失败: {e}"}, ensure_ascii=False)

    dispatched = [{
        "member": {"id": m.get("role_id"), "name": m.get("role_name")},
        "run_id": m.get("run_id"),
    } for m in (task.get("members") or [])]
    for item in dispatched:
        run_id = str(item.get("run_id") or "")
        if not run_id:
            continue
        try:
            await conv_worker.request_run(run_id)
        except Exception:
            logger.debug("后台成员 run 唤醒失败（worker 会轮询拾取）", exc_info=True)

    task_id = str(task.get("id") or "")
    if len(dispatched) == 1:
        first = dispatched[0]
        return json.dumps({
            "status": "dispatched",
            "task_id": task_id,
            "member": first["member"],
            "run_id": first["run_id"],
            "runs": dispatched,
            "errors": errors,
            "skipped": skipped,
            "note": "已创建后台任务并 @ 该成员，任务在后台自动执行；完成后结果会回到群聊。"
                    "本轮不要再自己重复执行同一任务：用一两句话说明分工即可收尾，"
                    "等用户在会话里点「继续」再整合结果。",
        }, ensure_ascii=False)
    return json.dumps({
        "status": "dispatched",
        "task_id": task_id,
        "count": len(dispatched),
        "runs": dispatched,
        "members": [x["member"] for x in dispatched],
        "errors": errors,
        "skipped": skipped,
        "note": f"已创建后台任务并并行派发给 {len(dispatched)} 个成员；"
                "各成员在后台同时执行，可在执行画布查看实时过程 / 暂停 / 继续 / 结束。"
                "本轮不要再自己重复执行同一任务：用一两句话说明分工即可收尾，"
                "等用户在会话里点「继续」再整合结果。",
    }, ensure_ascii=False)


_SUBAGENT_MAX_PARALLEL = 8


def _subagent_specs(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 `delegate(target=subagent)` 参数归一成一组子代理规格。

    - 旧调用：顶层 `goal` / `name` / `instructions` / `context` / `tools`；
    - 并行调用：`assignments: [{goal, name?, instructions?, context?, tools?}]`。
    一次最多派生 `_SUBAGENT_MAX_PARALLEL` 个，避免模型一口气建出几十个。
    """
    out: List[Dict[str, Any]] = []
    raw = (args or {}).get("assignments")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            goal = str(item.get("goal") or "").strip()
            if not goal:
                continue
            out.append({
                "goal": goal,
                "name": str(item.get("name") or "").strip()[:128] or "子代理",
                "instructions": str(item.get("instructions") or "").strip()[:2000],
                "context": str(item.get("context") or "").strip()[:8000],
                "tools": item.get("tools"),
            })
    if not out:
        goal = str((args or {}).get("goal") or "").strip()
        if goal:
            out.append({
                "goal": goal,
                "name": str((args or {}).get("name") or "").strip()[:128] or "子代理",
                "instructions": str((args or {}).get("instructions")
                                     or "").strip()[:2000],
                "context": str((args or {}).get("context") or "").strip()[:8000],
                "tools": (args or {}).get("tools"),
            })
    return out[:_SUBAGENT_MAX_PARALLEL]


def _subagent_child(role: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """按父角色收窄出子代理角色（临时、不落库）。"""
    parent_env = role.get("envelope") or {}

    def _narrow(parent_list: Any, req: Any) -> List[str]:
        parent = [str(x) for x in (parent_list or [])]
        if not isinstance(req, list) or not [x for x in req if str(x).strip()]:
            return parent                      # 缺省继承父
        allowed = set(parent)
        return [str(x) for x in req if str(x).strip() and str(x) in allowed]

    return {
        "id": f"{role.get('id')}:sub",
        "name": str(spec.get("name") or "子代理"),
        "persona": str(role.get("persona") or ""),
        "expertise": str(role.get("expertise") or ""),
        "avatar": role.get("avatar") or "",
        "envelope": {
            "tools": _narrow(parent_env.get("tools"), spec.get("tools")),
            "models": dict(parent_env.get("models") or {}),
            "network": parent_env.get("network") or "allow",
            "data_scope": parent_env.get("data_scope") or "camp",
            "write_paths": ["workspace"], "egress_allowlist": [],
            "db": {"allowed": [], "read_only": True, "table_whitelist": []},
        },
    }


async def _run_one_subagent(conv: Dict[str, Any], user: str,
                            role: Dict[str, Any], spec: Dict[str, Any],
                            depth: int, *, call_id: str = "") -> Dict[str, Any]:
    """跑一个子代理，返回 {output, display}；失败也返回可回灌的错误信息。"""
    goal = str(spec.get("goal") or "").strip()
    child = _subagent_child(role, spec)
    child_sink: Dict[str, Any] = {}
    try:
        from services.agent_zero.services import ask_service
        from services.agent_zero.task_layer import prompt_builder

        run_id = ask_service.current_run_id()
        fstate: Dict[str, Any] = {}
        async for ev in _stream_role(
                conv, user, child, child_sink, autonomous=False,
                _depth=depth + 1, _max_rounds=_SUBAGENT_MAX_ROUNDS,
                isolated=True,
                context_messages=_subagent_messages(
                    goal, instructions=str(spec.get("instructions") or ""),
                    context=str(spec.get("context") or "")),
                system_addon=prompt_builder.SUBAGENT):
            _forward_subagent_event(
                run_id, call_id or f"sub-{id(child_sink)}",
                str(child.get("name") or "子代理"), goal, ev, fstate)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # 子代理失败不阻断父循环
        logger.warning("子代理执行失败: %s", e)
        return {
            "output": "",
            "error": f"子代理执行失败: {e}",
            "display": {
                "type": "subagent",
                "agent": {"id": child["id"], "name": child["name"],
                          "avatar": child.get("avatar") or ""},
                "goal": goal,
                "trace": [],
                "error": str(e),
            },
        }
    text = str(child_sink.get("text") or "").strip()
    return {
        "output": text,
        "display": {
            "type": "subagent",
            "agent": {"id": child["id"], "name": child["name"],
                      "avatar": child.get("avatar") or ""},
            "goal": goal,
            "trace": (child_sink.get("trace") or []),
        },
    }


async def _exec_call_subagent(conv: Dict[str, Any], user: str, role: Dict[str, Any],
                              args: Dict[str, Any], depth: int,
                              call_id: str = "") -> str:
    """派生**自身子代理**（父角色的收窄副本，临时、不落库）执行一个或多个子目标。

    - 继承父角色的模型与工具白名单；`tools` 只取父工具的子集（缺省=父全部）；
    - `name` / `instructions` 可定制其名与补充指令；
    - `assignments` 一次派生多个，用 gather 并行执行，各自的事件仍写进父 run；
    - 不限轮次 / 不限嵌套，结论回灌父模型，子轨迹内联展示。
    """
    if _SUBAGENT_MAX_DEPTH and depth >= _SUBAGENT_MAX_DEPTH:
        return json.dumps({"error": f"子代理嵌套过深（最多 {_SUBAGENT_MAX_DEPTH} 层）"}, ensure_ascii=False)
    specs = _subagent_specs(args or {})
    if not specs:
        return json.dumps({"error": "缺少 goal"}, ensure_ascii=False)
    base_call = str(call_id or "").strip()
    if len(specs) == 1:
        result = await _run_one_subagent(conv, user, role, specs[0], depth,
                                         call_id=base_call)
        if result.get("error"):
            return json.dumps({"error": result["error"],
                               "display": result.get("display")},
                              ensure_ascii=False)
        return json.dumps({
            "output": result["output"] or "(子代理未返回正文)",
            "display": result["display"],
        }, ensure_ascii=False)

    tasks = [
        _run_one_subagent(
            conv, user, role, spec, depth,
            call_id=f"{base_call or 'sub'}#{index}")
        for index, spec in enumerate(specs)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: List[Dict[str, Any]] = []
    displays: List[Dict[str, Any]] = []
    blocks: List[str] = []
    for index, (spec, result) in enumerate(zip(specs, results)):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            outputs.append({"name": spec.get("name"), "goal": spec.get("goal"),
                            "output": "", "error": str(result)})
            blocks.append(f"### {spec.get('name')}\n（失败：{result}）")
            continue
        display = result.get("display") or {}
        displays.append(display)
        output = str(result.get("output") or "").strip()
        outputs.append({
            "name": (display.get("agent") or {}).get("name") or spec.get("name"),
            "goal": spec.get("goal"),
            "output": output,
            "error": str(result.get("error") or ""),
        })
        blocks.append(
            f"### {outputs[-1]['name']}（{spec.get('goal')}）\n"
            + (output or f"（未返回正文：{result.get('error')}）"))
    combined_display = {
        "type": "subagent",
        "agent": {
            "id": f"{role.get('id')}:sub-group",
            "name": f"并行子代理 ×{len(displays)}",
            "avatar": role.get("avatar") or "",
        },
        "goal": "；".join(str(s.get("goal") or "") for s in specs)[:1200],
        "trace": [],
        "agents": displays,
    }
    return json.dumps({
        "output": "\n\n".join(blocks),
        "outputs": outputs,
        "display": combined_display,
    }, ensure_ascii=False)


def _discussion_role_tokens(args: Dict[str, Any]) -> List[str]:
    raw = ((args or {}).get("roles") or (args or {}).get("role_ids")
           or (args or {}).get("participants") or [])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _resolve_discussion_roles(
    args: Dict[str, Any], current_role: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], str]:
    """把角色 id / 名字解析成参与者（2-3 个，去重，保留调用顺序）。"""
    tokens = _discussion_role_tokens(args or {})
    if not tokens:
        return [], "roles 不能为空：请提供 2-3 个角色 id 或名字"
    from services.agent_zero.stores import zero_store

    pool = zero_store.list_roles(limit=500)
    by_id = {str(role.get("id") or ""): role for role in pool}
    current_id = str(current_role.get("id") or "")
    if current_id and current_id not in by_id:
        pool.append(current_role)
        by_id[current_id] = current_role
    by_name = {
        str(role.get("name") or "").strip().casefold(): role
        for role in pool if str(role.get("name") or "").strip()
    }
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for token in tokens:
        role = by_id.get(token) or by_name.get(token.casefold())
        if not role:
            return [], f"找不到角色：{token}"
        rid = str(role.get("id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(role)
    if len(out) < 2:
        return [], "至少需要 2 个不同角色参与讨论"
    if len(out) > MAX_DISCUSSION_ROLES:
        out = out[:MAX_DISCUSSION_ROLES]
    return out, ""


def _discussion_prompt(topic: str, mode: str, participants: List[Dict[str, Any]],
                       transcript: List[Dict[str, Any]], speaker: Dict[str, Any],
                       round_no: int, rounds: int, context: str) -> str:
    mode_label = "交互验证 / 交叉审查" if mode == "verify" else "头脑风暴"
    lines = [
        "<role-discussion>",
        f"主题：{topic}",
        f"模式：{mode_label}",
        f"参与者：{'、'.join(str(p.get('name') or p.get('id')) for p in participants)}",
        f"你的角色：{speaker.get('name') or speaker.get('id')}"
        f"（{speaker.get('title') or '未填写岗位'}）",
        f"轮次：第 {round_no} 轮 / 共 {rounds} 轮",
    ]
    if context.strip():
        lines += ["", "补充上下文：", context.strip()[:6000]]
    if transcript:
        lines += ["", "此前讨论记录："]
        for item in transcript:
            lines.append(f"## 第 {item.get('round')} 轮")
            for message in item.get("messages") or []:
                lines.append(f"### {message.get('role_name')}")
                lines.append(str(message.get("output") or "")[:6000])
    if mode == "verify":
        lines += [
            "",
            "本轮要求：",
            "- 检查此前发言中的事实、假设与推理漏洞；不同意就明确说，并给出依据。",
            "- 补充你验证过的证据（文件 / 命令 / 测试结果）；没有验证就标注未验证。",
            "- 最后一轮给出可执行结论、分歧点与仍待验证项。",
        ]
    else:
        lines += [
            "",
            "本轮要求：",
            "- 结合此前发言提出新观点，不要复述别人已经说过的内容。",
            "- 可以对别人的观点补充、反驳或组合；给出具体可执行的建议。",
            "- 最后一轮收敛成可执行结论与待办。",
        ]
    lines.append("</role-discussion>")
    return "\n".join(lines)


async def _exec_role_discussion(conv: Dict[str, Any], user: str,
                                role: Dict[str, Any], args: Dict[str, Any],
                                depth: int, *, call_id: str = "") -> str:
    """2-3 个角色直接多轮讨论（不经过主持转述）。"""
    if _SUBAGENT_MAX_DEPTH and depth >= _SUBAGENT_MAX_DEPTH:
        return json.dumps(
            {"error": f"角色讨论嵌套过深（最多 {_SUBAGENT_MAX_DEPTH} 层）"},
            ensure_ascii=False)
    topic = str((args or {}).get("topic") or "").strip()
    if not topic:
        return json.dumps({"error": "topic 不能为空"}, ensure_ascii=False)
    mode = str((args or {}).get("mode") or "brainstorm").strip().lower()
    if mode not in ("brainstorm", "verify"):
        mode = "brainstorm"
    try:
        rounds = int((args or {}).get("rounds") or 2)
    except (TypeError, ValueError):
        rounds = 2
    rounds = max(1, min(MAX_DISCUSSION_ROUNDS, rounds))
    context = str((args or {}).get("context") or "").strip()[:8000]
    participants, error = _resolve_discussion_roles(args or {}, role)
    if error:
        return json.dumps({"error": error}, ensure_ascii=False)

    from services.agent_zero.services import ask_service
    from services.agent_zero.task_layer import prompt_builder

    run_id = ask_service.current_run_id()
    base_call = str(call_id or "discussion").strip()
    transcript: List[Dict[str, Any]] = []
    for round_no in range(1, rounds + 1):
        messages: List[Dict[str, Any]] = []
        for speaker in participants:
            goal = f"{mode}: {topic}"
            sink: Dict[str, Any] = {}
            state: Dict[str, Any] = {}
            prompt = _discussion_prompt(
                topic, mode, participants, transcript, speaker,
                round_no, rounds, context)
            try:
                async for ev in _stream_role(
                        conv, user, speaker, sink, autonomous=False,
                        _depth=depth + 1, _max_rounds=_SUBAGENT_MAX_ROUNDS,
                        isolated=True,
                        context_messages=[{"role": "user", "content": prompt}],
                        system_addon=prompt_builder.ROLE_DISCUSSION):
                    _forward_subagent_event(
                        run_id, f"{base_call}#r{round_no}#{speaker.get('id')}",
                        str(speaker.get("name") or "角色"), goal, ev, state)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("角色讨论参与者 %s 失败: %s", speaker.get("name"), e)
                messages.append({
                    "role_id": str(speaker.get("id") or ""),
                    "role_name": str(speaker.get("name") or speaker.get("id") or ""),
                    "output": f"（失败：{e}）",
                    "error": str(e),
                })
                continue
            text = str(sink.get("text") or "").strip()
            if not text:
                err = str(sink.get("error") or "").strip()
                text = f"（未返回正文：{err}）" if err else "（未返回正文）"
            messages.append({
                "role_id": str(speaker.get("id") or ""),
                "role_name": str(speaker.get("name") or speaker.get("id") or ""),
                "output": text[:8000],
            })
        transcript.append({"round": round_no, "messages": messages})

    final_round = transcript[-1]["messages"] if transcript else []
    summary = "\n\n".join(
        f"### {item.get('role_name')}\n{item.get('output')}" for item in final_round)
    return json.dumps({
        "status": "done",
        "topic": topic,
        "mode": mode,
        "rounds": rounds,
        "participants": [
            {"id": str(p.get("id") or ""), "name": str(p.get("name") or "")}
            for p in participants
        ],
        "transcript": transcript,
        "output": summary,
    }, ensure_ascii=False)


def _workspace_has_dir(workspace_id: str) -> bool:
    from services.agent_zero.services import workspace_service as wsvc

    return bool(workspace_id) and wsvc.has_working_dir(workspace_id)


_RULE_FILES = ("AGENTS.md", "CLAUDE.md", "ATLAS.md", ".atlas/rules.md")


def _project_rules(workspace_id: str) -> str:
    """工作区约定文件（AGENTS.md / CLAUDE.md 等）→ 注入系统提示的正文。

    对齐 Claude Code / OpenCode / Codex：项目约定文件自动进上下文，
    模型不用每轮自己去读；找不到返回空串。
    """
    from services.agent_zero.services import workspace_service as wsvc

    for rel in _RULE_FILES:
        try:
            data = wsvc.read_file(workspace_id, rel)
            content = str(data.get("content") or "").strip()
            if content:
                return content
        except Exception:
            continue
    return ""


def _workspace_snapshot(workspace_id: str) -> str:
    """当前工作区快照：有用户文件夹时列出其内容；纯聊天工作区说明用的是托管目录。"""
    lines: List[str] = []
    has_dir = _workspace_has_dir(workspace_id)
    try:
        ws = zero_store.get_workspace(workspace_id)
        if ws:
            root = str(ws.get("root_path") or "").strip()
            if root:
                lines.append(f"工作区：{ws.get('name') or workspace_id}"
                             f"（id={workspace_id}，工作目录={root}）")
            else:
                from services.agent_zero.services import workspace_service as _ws

                try:
                    sandbox = str(_ws.exec_root(workspace_id))
                except Exception:
                    sandbox = ""
                lines.append(f"工作区：{ws.get('name') or workspace_id}"
                             f"（id={workspace_id}，纯聊天工作区，无用户文件夹；"
                             f"shell / 文件工具在托管目录 {sandbox or '(不可用)'} 内执行）")
    except Exception:
        pass
    if has_dir:
        try:
            from services.agent_zero.services import codemap

            m = codemap.render_map(workspace_id, max_chars=1800)
            if m:
                lines.append("代码图谱（代码定位参考，自动增量索引；需要更细的符号 / 依赖时可用 codegraph）：\n" + m)
        except Exception as e:
            logger.debug("代码地图生成失败（不影响对话）: %s", e)
    if not has_dir:
        return "\n".join(lines)
    try:
        from services.agent_zero.services import workspace_service as wsvc

        items = wsvc.list_dir(workspace_id, ".")
        files = [i for i in items if i.get("type") == "file"]
        dirs = [i for i in items if i.get("type") == "dir"]
        lines.append(f"工作目录内容：{len(files)} 个文件 / {len(dirs)} 个目录")
        for i in (files + dirs)[:30]:
            lines.append(f"- {i.get('type')}: {i.get('path')}")
    except Exception:
        lines.append("工作目录内容：暂无或不可读")
    return "\n".join(lines)


# ── 文件类工具：读 / 改 / 写（呈现层见 runtime/zero/file_ops.py）────────
#
# 三个都返回同一个信封：
#   {output, display}  → output 给模型（带行号 / diff / 续读提示）
#                        display 给前端（渲染文件视图 / diff 视图）
# 之前 read 只回裸 content[:20000] 且被外层再截一次，模型不知道自己读到哪、
# 人也只能在折叠区里读一段没头没尾的 JSON。


def _attachment_text_block(conv_id: str, attachments: List[Dict[str, Any]],
                           budget: int = 160_000) -> str:
    """把文档 / 代码 / PDF 附件的抽取文本拼进模型上下文（有总预算）。"""
    if not attachments:
        return ""
    from services.agent_zero.services import attachment_service

    blocks: List[str] = []
    used = 0
    for att in attachments:
        if not isinstance(att, dict) or att.get("kind") == "image":
            continue
        room = max(0, budget - used)
        if room <= 0:
            blocks.append("[其余附件文本因上下文预算已省略]")
            break
        text = attachment_service.read_attachment_text(
            conv_id, att, max_chars=min(attachment_service.MAX_CONTEXT_TEXT_CHARS, room))
        name = str(att.get("name") or "附件")
        if not text.strip():
            blocks.append(f"[附件: {name}（未能提取文本）]")
            continue
        blocks.append(f"[附件: {name}]\n{text}\n[附件结束: {name}]")
        used += len(text)
    return "\n\n".join(blocks)


def _build_message_content(conv_id: str, text: str,
                           attachments: Optional[List[Dict[str, Any]]],
                           *, include_images: bool) -> Any:
    """拼出用户消息的模型内容：文本 + 文档全文 + 可选图片（多模态数组）。"""
    from services.agent_zero.services import attachment_service

    atts = [a for a in (attachments or []) if isinstance(a, dict)]
    body = str(text or "").strip()
    doc_text = _attachment_text_block(conv_id, atts)
    if doc_text:
        body = f"{body}\n\n{doc_text}" if body else doc_text

    images: List[Dict[str, Any]] = []
    image_names: List[str] = []
    image_bytes = 0
    for att in atts:
        if att.get("kind") != "image":
            continue
        name = str(att.get("name") or "图片")
        if include_images:
            url = attachment_service.image_data_url(conv_id, att)
            if url and image_bytes + int(att.get("size") or 0) <= (
                    attachment_service.MAX_IMAGE_BYTES_TOTAL):
                images.append({"type": "image_url", "image_url": {"url": url}})
                image_bytes += int(att.get("size") or 0)
                continue
        image_names.append(name)

    if image_names:
        marker = "、".join(image_names)
        body = f"{body}\n\n[用户上传的图片: {marker}]" if body else f"[用户上传的图片: {marker}]"
    if not images:
        return body
    return [{"type": "text", "text": body}, *images]


def _trace_digest(trace: Optional[List[Dict[str, Any]]], limit: int = 1400) -> str:
    """把中断轮次的轨迹压成一段给模型看的摘要，避免下一轮重复已做步骤。"""
    bits: List[str] = []
    for step in trace or []:
        if not isinstance(step, dict):
            continue
        kind = step.get("kind")
        if kind == "tool":
            name = str(step.get("name") or "")
            if not name:
                continue
            detail = str(step.get("summary") or "").strip()
            if not detail and step.get("args") is not None:
                try:
                    detail = json.dumps(step.get("args"), ensure_ascii=False)
                except Exception:
                    detail = str(step.get("args"))
            bits.append(f"工具 {name}" + (f"：{detail[:180]}" if detail else ""))
        elif kind == "thinking":
            text = str(step.get("text") or "").strip()
            if text:
                bits.append("思考：" + text[-360:])
        elif kind == "text":
            text = str(step.get("text") or "").strip()
            if text:
                bits.append("正文：" + text[-240:])
    text = ""
    for bit in bits:
        if len(text) + len(bit) + 2 > limit:
            break
        text = f"{text}\n- {bit}" if text else f"- {bit}"
    return text


async def _history_limits(models: Optional[Dict[str, Any]] = None) -> Tuple[int, int]:
    """跨轮历史的 (条数上限, token 预算)。

    配置 `llm.history.max_messages` / `max_tokens`；`max_tokens: 0` = 自动，
    取当前模型窗口的 20%（夹在 8k～60k），窗口未知时退 30k。auto 口径保证
    小窗口模型不会被历史撑爆，大窗口模型也不会只留十来轮。
    """
    from services.agent_zero.runtime.zero import llm as llm_mod

    try:
        policy = await llm_mod.get_context_policy() or {}
    except Exception:
        policy = {}
    hist = policy.get("history") or {}
    try:
        max_messages = max(2, int(hist.get("max_messages") or CHAT_HISTORY))
    except (TypeError, ValueError):
        max_messages = CHAT_HISTORY
    try:
        max_tokens = max(0, int(hist.get("max_tokens") or 0))
    except (TypeError, ValueError):
        max_tokens = 0
    if max_tokens <= 0:
        window = 0
        try:
            cfg = await llm_mod.get_llm_config(
                str((models or {}).get("loop") or "default")) or {}
            window = int(cfg.get("context_window") or 0)
        except Exception:
            window = 0
        if window > 0:
            max_tokens = max(CHAT_HISTORY_MIN_TOKENS,
                             min(int(window * CHAT_HISTORY_SHARE),
                                 CHAT_HISTORY_MAX_TOKENS))
        else:
            max_tokens = CHAT_HISTORY_FALLBACK_TOKENS
    return max_messages, max_tokens


def _trim_history(messages: List[Dict[str, Any]],
                  budget: int) -> List[Dict[str, Any]]:
    """从最旧开始丢到 token 预算内；起点对齐 user，最新 user 及其后永不丢。

    跨轮取历史不能等摘要模型（那会把每轮开头拖慢），所以这里只做确定性裁剪：
    丢整条消息、不切断问答对。真正的「旧内容折叠成摘要」发生在 run 内压缩。
    """
    if not messages or budget <= 0:
        return messages
    try:
        from services.agent_zero.runtime.zero.compress import message_tokens
    except Exception:                                    # pragma: no cover - 环境损坏
        return messages

    total = sum(message_tokens(m) for m in messages)
    if total <= budget:
        return messages
    last_user = max((i for i, m in enumerate(messages)
                     if m.get("role") == "user"), default=0)
    start = 0
    while start < last_user and total > budget:
        total -= message_tokens(messages[start])
        start += 1
    while start < last_user and messages[start].get("role") != "user":
        start += 1
    return messages[start:]


def _latest_user_prompt(conv_id: str, fallback: str = "") -> str:
    """最近一条用户消息的模型侧正文（斜杠命令取展开后的 prompt）。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q, decode_row

    try:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            row = db.execute(
                text(f"SELECT props FROM {_q('gdag_nodes')} "
                     f"WHERE type = 'fact' "
                     f"AND json_extract(props, '$.conversation_id') = :cid "
                     f"AND json_extract(props, '$.stage') = 'exploration' "
                     f"AND COALESCE(json_extract(props, '$.by'), '') <> 'assistant' "
                     f"AND COALESCE(json_extract(props, '$.kind'), '') <> 'checkpoint' "
                     f"AND COALESCE(json_extract(props, '$.kind'), '') <> :clear_kind "
                     f"AND COALESCE(CAST(json_extract(props, '$.queued') AS INTEGER), 0) <> 1 "
                     f"ORDER BY created_at DESC, rowid DESC LIMIT 1"),
                {"cid": conv_id, "clear_kind": CLEAR_KIND},
            ).first()
        if not row:
            return fallback
        props = decode_row({"props": row[0]}).get("props") or {}
        return str(props.get("prompt") or props.get("content") or fallback)
    except Exception:
        return fallback


def _history_messages(conv_id: str, limit: int = CHAT_HISTORY,
                      *, vision: bool = False,
                      max_tokens: int = 0) -> List[Dict[str, Any]]:
    """最近 N 条探索态消息 → OpenAI messages（assistant 按 by='assistant' 识别）。

    附件：文档 / 代码 / PDF 的抽取文本随消息进入上下文；图片只在本轮**最新一条
    用户消息**里作为多模态内容发送，避免历史图片反复占用上下文（旧图片仅留名称）。

    **压缩检查点**：如果这个会话落过检查点（上一轮压缩的成果），它作为第一条
    user 消息放在 system 之后——这样跨轮也能带着摘要，而不是每轮都从最近 N 条原文
    重新蒸馏。检查点本身是 GDAG 节点，但在转录 / 搜索 / 预览里都会被过滤掉。

    `max_tokens` > 0 时，条数取够之后再按 token 预算从旧到新裁（检查点也算在内），
    短消息多留几轮、大附件消息少留，避免跨轮历史把窗口吃掉。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    clear_rowid = clear_marker_rowid(conv_id)
    checkpoint = latest_checkpoint(conv_id, after_rowid=clear_rowid)
    # 还有排队 run 等着的消息一律不进上下文：`queued` 标记可能已被认领 / 编辑 /
    # 异常路径清掉，只靠它过滤会让当前 run 提前看到「下一轮」的内容（等于替下一轮
    # 执行了一遍）。以 run 表为准兜底；显式「立即插入」（inject=1）的除外。
    try:
        _still_queued = _queued_message_ids(conv_id)
    except Exception:
        _still_queued = set()
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, props FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' "
                 f"AND json_extract(props, '$.conversation_id') = :cid "
                 f"AND json_extract(props, '$.stage') = 'exploration' "
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> 'checkpoint' "
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> :clear_kind "
                 f"AND rowid > :clear_rowid "
                 f"AND COALESCE(json_extract(props, '$.by'), '') <> 'system' "
                 f"AND COALESCE(CAST(json_extract(props, '$.queued') AS INTEGER), 0) <> 1 "
                 f"ORDER BY created_at DESC LIMIT :n"),
            {"cid": conv_id, "n": max(1, int(limit)),
             "clear_kind": CLEAR_KIND, "clear_rowid": clear_rowid},
        ).fetchall()
    from services.agent_zero.stores.zero_store import decode_row

    items: List[Dict[str, Any]] = []
    for node_id, props in reversed(rows):
        props = decode_row({"props": props}).get("props") or {}
        # 有排队 run 等着的消息不进本轮上下文（inject=1 的立即插入除外）
        if str(node_id or "") in _still_queued and not props.get("inject"):
            continue
        content = str(props.get("content") or "").strip()
        if str(props.get("by") or "") != "assistant" and props.get("prompt"):
            # 斜杠命令：转录显示 /review，模型侧用展开后的完整指令
            content = str(props.get("prompt") or content).strip()
        trace = props.get("trace") or []
        interrupted = bool(props.get("interrupted"))
        has_attachments = bool(props.get("attachments"))
        if not content:
            if interrupted and trace:
                digest = _trace_digest(trace)
                content = ("[上一轮输出被中断，已完成的过程如下，请不要重复这些步骤]\n"
                           + (digest or "（无可用步骤）"))
            elif not has_attachments:
                continue
        elif interrupted:
            digest = _trace_digest(trace)
            if digest:
                content = (content + "\n\n[本轮输出被中断；中断前已完成的过程，请勿重复]\n"
                           + digest)
        role = "assistant" if str(props.get("by") or "") == "assistant" else "user"
        items.append({
            "role": role,
            "content": content,
            "refs": props.get("refs") or [],
            "attachments": props.get("attachments") or [],
            "ts": str(props.get("ts") or "").strip(),
        })

    last_user = max((i for i, m in enumerate(items) if m["role"] == "user"), default=-1)
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        content = item["content"]
        refs = item["refs"]
        if refs:
            content = f"{content}\n[引用文件: {', '.join(str(x) for x in refs)}]"
        if item["role"] == "user":
            # 用户消息自动带上**发送时间（精确到秒）**：模型据此理解「刚才 / 今天」等
            # 相对时间，也避免把历史对话里的时间误当成现在（当前时间另在系统提示注入）。
            if item["ts"]:
                content = f"[{item['ts']}] {content}"
            content = _build_message_content(
                conv_id, content, item["attachments"],
                include_images=bool(vision and idx == last_user),
            )
        out.append({"role": item["role"], "content": content})

    if max_tokens > 0:
        from services.agent_zero.runtime.zero.compress import (
            message_tokens as _msg_tokens)

        budget = max(1, int(max_tokens))
        if checkpoint:
            # 检查点也占历史预算（它就在 system 之后，跨轮一直在）
            budget = max(1, budget - _msg_tokens(
                {"role": "user", "content": checkpoint["content"]}))
        out = _trim_history(out, budget)

    if checkpoint:
        # 检查点放最前（system 之后的第一条 user），保证「首个非 system 是 user」
        out.insert(0, {"role": "user", "content": checkpoint["content"]})
    return out


# ── 压缩检查点（跨轮保留压缩成果）───────────────────────────────────────
#
# 压缩产物不只在当轮有效：检查点写成一条 GDAG fact 节点（kind=checkpoint），
# 下一轮 _history_messages 会把它放到 system 之后，压缩成果得以跨轮保留。
# 它是「上下文的一部分」，不是聊天消息：转录、搜索、会话预览都会过滤掉。

CHECKPOINT_KIND = "checkpoint"


def latest_checkpoint(conv_id: str, *, after_rowid: int = 0) -> Optional[Dict[str, Any]]:
    """该会话最新的一条压缩检查点（没有返回 None）。

    `after_rowid` 用于清空上下文后忽略旧的压缩检查点。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT id, props, created_at FROM {_q('gdag_nodes')} "
                 f"INDEXED BY idx_gdag_conv "
                 f"WHERE type = 'fact' "
                 f"AND json_extract(props, '$.conversation_id') = :cid "
                 f"AND json_extract(props, '$.kind') = :kind "
                 f"AND rowid > :after "
                 f"ORDER BY created_at DESC, rowid DESC LIMIT 1"),
            {"cid": conv_id, "kind": CHECKPOINT_KIND,
             "after": int(after_rowid or 0)},
        ).mappings().first()
    if not row:
        return None
    from services.agent_zero.stores.zero_store import decode_row

    props = decode_row(dict(row)).get("props") or {}
    content = str(props.get("content") or "").strip()
    if not content:
        return None
    return {"id": str(row["id"]), "content": content,
            "summary": str(props.get("summary") or ""),
            "source": str(props.get("source") or ""),
            "created_at": str(row.get("created_at") or "")}


def persist_checkpoint(conv: Dict[str, Any], summary: str,
                       source: str = "") -> Optional[str]:
    """落一条检查点，并清掉同一会话更早的检查点（只留最新的）。"""
    text = str(summary or "").strip()
    if not text:
        return None
    from services.agent_zero.runtime.zero.compress import checkpoint_content
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node_id = zero_store.new_id("chk")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default",
        "props": {
            "conversation_id": conv["id"],
            "workspace_id": conv.get("workspace_id"),
            "kind": CHECKPOINT_KIND,
            "stage": "exploration",
            "by": "system",
            "content": checkpoint_content(text),
            "summary": text,
            "source": str(source or ""),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "confidence": 0.5,
    })
    clear_checkpoints(conv["id"], keep=node_id)
    return node_id


def clear_checkpoints(conv_id: str, *, keep: Optional[str] = None) -> int:
    """删掉会话的检查点（keep 指定的那条除外）。

    对话被截断 / 重编辑后摘要可能已经过时，调用方会清空重来；
    新检查点落库后则只保留最新一条，避免节点无限增长。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        sql = (f"DELETE FROM {_q('gdag_nodes')} "
               f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
               f"AND props->>'kind' = :kind")
        params: Dict[str, Any] = {"cid": conv_id, "kind": CHECKPOINT_KIND}
        if keep:
            sql += " AND id <> :keep"
            params["keep"] = keep
        result = db.execute(text(sql), params)
        db.commit()
        return int(result.rowcount or 0)


CLEAR_KIND = "clear_context"


def clear_marker_rowid(conv_id: str) -> int:
    """最近一次「清空上下文」标记的 rowid；没有返回 0。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT rowid FROM {_q('gdag_nodes')} "
                 f"INDEXED BY idx_gdag_conv "
                 f"WHERE type = 'fact' "
                 f"AND json_extract(props, '$.conversation_id') = :cid "
                 f"AND json_extract(props, '$.kind') = :kind "
                 f"ORDER BY rowid DESC LIMIT 1"),
            {"cid": conv_id, "kind": CLEAR_KIND},
        ).first()
    return int(row[0]) if row else 0


def clear_context(conv_id: str) -> bool:
    """清空一个会话的**模型上下文**（历史消息不删除，转录仍可回看）。

    实现为一条 `clear_context` 标记节点：`_history_messages` / 干预读取只取
    标记之后的节点；`list_messages` 会过滤掉标记本身。这样界面历史不变，
    但下一轮模型不再看到清空前的上下文。
    """
    conv = get_conversation(conv_id)
    if not conv:
        return False
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node_id = zero_store.new_id("clr")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default", "confidence": 0.5,
        "props": {
            "conversation_id": conv_id,
            "workspace_id": conv.get("workspace_id"),
            "kind": CLEAR_KIND,
            "stage": "exploration",
            "by": "system",
            "content": "",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    })
    zero_store.touch_conversation(conv_id)
    return True


async def compact_context(conv_id: str) -> Dict[str, Any]:
    """手动压缩一个会话的上下文（供界面 /compact 与机器人指令共用）。

    运行中的会话拒绝压缩，避免和 run 抢同一份上下文。
    """
    from services.agent_zero.runtime.zero import compress
    from services.agent_zero.task_layer import run_service

    conv = get_conversation(conv_id)
    if not conv:
        raise ValueError(f"对话不存在: {conv_id}")
    if run_service.running_run(conv_id):
        raise RuntimeError("当前会话正在运行，先停止再压缩")
    role = zero_store.get_role(str(conv.get("role_id") or "")) or {}
    models = _chat_models(role) or {"loop": "default", "fallback": "default"}
    messages = _history_messages(conv_id)
    decision = await compress.decide(messages, models=models, force=True, reason="manual")
    if not decision.get("should_compress"):
        return {
            "compressed": False,
            "reason": decision.get("reason") or "nothing",
            "before_tokens": int(decision.get("current") or 0),
            "after_tokens": int(decision.get("current") or 0),
        }
    result = await compress.apply(
        messages, models=models, decision=decision, force_summary=True)
    if result.get("summary"):
        persist_checkpoint(conv, str(result.get("summary") or ""),
                           str(result.get("source") or "manual"))
    return {
        "compressed": bool(result.get("compressed")),
        "source": result.get("source") or "",
        "before_tokens": int(result.get("original_tokens") or 0),
        "after_tokens": int(result.get("tokens") or 0),
        "pruned_tool_outputs": int(result.get("pruned_tool_outputs") or 0),
        "dropped_exchanges": int(result.get("dropped_exchanges") or 0),
    }


def clear_all_contexts() -> int:
    """清空所有会话的模型上下文（历史消息保留）。返回处理的会话数。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(text(f"SELECT id FROM {_q('conversations')}")).all()
    count = 0
    for row in rows:
        if clear_context(str(row[0])):
            count += 1
    return count


def persist_compaction_result(conv: Dict[str, Any], res: Dict[str, Any]) -> None:
    """把一次压缩的摘要落成检查点；prune（无摘要）与失败不落。"""
    try:
        if str(res.get("source") or "") not in ("llm", "heuristic"):
            return
        summary = str(res.get("summary") or "").strip()
        if not summary:
            return
        persist_checkpoint(conv, summary, str(res.get("source") or ""))
    except Exception:
        logger.debug("压缩检查点落库失败（不影响本轮）", exc_info=True)


def _compress_start_event(decision: Dict[str, Any], round_no: int,
                          rid: Any, rname: Any) -> Dict[str, Any]:
    """压缩开始事件：前端据此立刻显示「正在压缩」，而不是静默等摘要模型。"""
    return {
        "before": decision.get("current"),
        "window": decision.get("window"),
        "budget": decision.get("budget"),
        "trigger": decision.get("trigger"),
        "keep": decision.get("keep"),
        "reason": decision.get("reason"),
        "mode": decision.get("mode"),
        "count_source": decision.get("count_source"),
        "breakdown": decision.get("breakdown") or {},
        "round": round_no,
        "role_id": rid,
        "role_name": rname,
    }


def _compress_done_event(res: Dict[str, Any], round_no: int,
                         rid: Any, rname: Any,
                         duration_ms: int = 0) -> Dict[str, Any]:
    """压缩结束事件：前端把「正在压缩」原地更新为结果。"""
    decision = res.get("decision") or {}
    return {
        "before": res.get("original_tokens"),
        "after": res.get("tokens"),
        "source": res.get("source"),
        "keep": res.get("keep"),
        "kept_recent": res.get("kept_recent"),
        "pruned_tool_outputs": res.get("pruned_tool_outputs"),
        "deduped_tool_outputs": res.get("deduped_tool_outputs"),
        "dropped_exchanges": res.get("dropped_exchanges"),
        "hard_trimmed": res.get("hard_trimmed"),
        "reason": res.get("reason") or decision.get("reason"),
        "duration_ms": int(duration_ms or 0),
        "round": round_no,
        "role_id": rid,
        "role_name": rname,
    }


async def generate_reply_stream(conv_id: str, user: str, content: str = "",
                                mentions: Optional[List[str]] = None,
                                target_role_id: Optional[str] = None,
                                autonomous: bool = False,
                                auto_execute: bool = False,
                                run_id: Optional[str] = None) -> AsyncIterator[Dict[str, Any]]:
    """探索态 LLM **流式**回复（SSE 事件）。

    事件：{start, role_id, role_name} → {delta, role_id, role_name}* →
    {done, id, role_id, role_name, duration_ms}；出错 {error}。

    - 群组工作区：支持**多角色**——用户 `@角色` 或传入 mentions 指定应答角色；
      未指定则由**主持**应答；任一角色回复中 `@成员` 会**通知该成员自行回复**
      （主持不得转发，各角色只产出自己的内容）。
    - 角色独立工作区：单角色回复。
    - ``autonomous=True``：自主执行模式——系统提示要求模型**动态规划**（先给简要
      步骤，再逐步执行、每步以结果决定下一步），把长任务在同一个 run 内推进到完成，
      而不是转成独立的「任务」实体。
    """
    conv = get_conversation(conv_id)
    if not conv:
        yield {"error": f"对话不存在: {conv_id}"}
        return

    # ── UserPromptSubmit 钩子（对齐 Claude Code）──────────────────────────
    # 可以在模型看到消息前注入额外上下文，也可以直接拒绝本轮（如敏感词 / 预算守卫）。
    extra_context = ""
    try:
        from services.agent_zero.services import hooks_service

        _prompt_text = _latest_user_prompt(conv_id, content or "")
        _hook = await hooks_service.user_prompt_submit(
            str(conv.get("workspace_id") or ""), _prompt_text,
            run_id=run_id or "", session_id=conv_id)
        for _hr in _hook.get("results") or []:
            yield {"hook": {
                "phase": "prompt", "event": "UserPromptSubmit",
                "command": _hr.get("command"), "decision": _hr.get("decision"),
                "reason": _hr.get("reason"), "output": _hr.get("output"),
                "ok": _hr.get("ok"), "duration_ms": _hr.get("duration_ms"),
            }}
        if _hook.get("blocked"):
            yield {"error": "UserPromptSubmit 钩子拒绝了这条消息"
                             + (f"：{_hook['reason']}" if _hook.get("reason") else "")}
            return
        extra_context = str(_hook.get("context") or "")
    except Exception as e:
        logger.warning("UserPromptSubmit 钩子执行失败（已忽略）: %s", e)

    # ── 团队 / 群聊路由（V3：多角色只认显式绑定的 agent_teams）──────────
    # 1) 会话绑定了有成员的团队：按 team.mode 执行；
    # 2) 没绑定 / 团队没有成员：单聊，角色由 _resolve_chat_role 决定。
    # 不再从工作区的旧群组列「惰性归一」：那会让一条单聊凭空变成「默认群聊」。
    team: Optional[Dict[str, Any]] = None
    try:
        from services.agent_zero.services import team_service as _tsvc

        # 全局 Agent 永远不是群聊；旧数据残留的 team_id 也不参与路由。
        if str(conv.get("zone") or "") != GLOBAL_AGENT_ZONE:
            team = _tsvc.team_for_conv(conv)
    except Exception as e:
        logger.debug("团队路由跳过（按单角色继续）: %s", e)

    # 主持把子任务 @ 给某个群成员：这是一条只由该成员执行的后台 run。
    target_id = str(target_role_id or "").strip()
    if team and target_id:
        role = zero_store.get_role(target_id) or {}
        if role:
            # 后台任务成员 run：产出写 stage='task'，不进前台转录；
            # 普通被 @ 的成员回复仍然写进对话。
            stage = "exploration"
            if run_id:
                try:
                    from services.agent_zero.task_layer import run_service as _rsvc

                    if (_rsvc.get_run(run_id) or {}).get("team_task_id"):
                        stage = "task"
                except Exception:
                    logger.debug("读取 run 的后台任务标记失败（按对话回复处理）",
                                 exc_info=True)
            async for evt in _single_reply_stream(
                    conv, user, role, autonomous=autonomous,
                    auto_execute=auto_execute, run_id=run_id,
                    extra_context=extra_context, stage=stage):
                yield evt
            return
    if team:
        use_group, leader, members, _addressed = _team_reply_plan(
            team, mentions, content)
        if use_group:
            async for evt in _group_reply_stream(
                    conv, user, content, mentions, leader, members,
                    autonomous=autonomous, auto_execute=auto_execute,
                    run_id=run_id, extra_context=extra_context,
                    max_speakers=int(team.get("max_speakers") or GROUP_MAX_SPEAKERS)):
                yield evt
            return
        if leader:
            # orchestrate / single（以及 roundtable 没有成员时）：主持单角色运行；
            # 委派能力由 _single_reply_stream 按 team_bound 决定。
            # 回复里 @ 成员时，成员作为独立后台 run 接活——主会话不等待。
            _leader_text = ""
            async for evt in _single_reply_stream(conv, user, leader,
                                                 autonomous=autonomous,
                                                 auto_execute=auto_execute,
                                                 run_id=run_id,
                                                 extra_context=extra_context):
                if evt.get("done"):
                    _leader_text = str(evt.get("text") or "")
                yield evt
            await _dispatch_reply_mentions(
                conv, leader, _leader_text, user, run_id=run_id or "")
            return

    role = _resolve_chat_role(conv)
    if not role:
        yield {"error": "该对话暂无可用角色：请为群组指定主持/成员角色，"
                        "或在角色区创建并配置角色。"}
        return
    async for evt in _single_reply_stream(conv, user, role, autonomous=autonomous,
                                         auto_execute=auto_execute, run_id=run_id,
                                         extra_context=extra_context):
        yield evt


def _recent_exploration_rows(conv_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    """该对话最近的探索态消息（含 id / by / content），升序。

    用于执行循环的「对话内干预」：每轮开始前取用户新消息注入上下文。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, props FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' "
                 f"AND json_extract(props, '$.conversation_id') = :cid "
                 f"AND json_extract(props, '$.stage') = 'exploration' "
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> 'task_proposal' "
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> 'checkpoint' "
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> :clear_kind "
                 f"AND rowid > :clear_rowid "
                 f"AND COALESCE(CAST(json_extract(props, '$.queued') AS INTEGER), 0) <> 1 "
                 f"ORDER BY created_at DESC, id DESC LIMIT :n"),
            {"cid": conv_id, "n": max(1, min(int(limit), 100)),
             "clear_kind": CLEAR_KIND, "clear_rowid": clear_marker_rowid(conv_id)},
        ).fetchall()
    from services.agent_zero.stores.zero_store import decode_row

    out = []
    for nid, props in reversed(rows):          # 回到升序
        p = decode_row({"props": props}).get("props") or {}
        out.append({"id": nid, "by": p.get("by") or "",
                    "kind": p.get("kind") or "",
                    "display_by": p.get("display_by") or "",
                    "content": str(p.get("content") or ""),
                    "prompt": str(p.get("prompt") or ""),
                    # inject=1：用户点了「立即插入」，这条就该被当前 run 当干预取走
                    # （见 run_service.inject_run），不能按「排队中」跳过。
                    "inject": bool(p.get("inject")),
                    "attachments": p.get("attachments") or []})
    return out


def _queued_message_ids(conv_id: str) -> set:
    """该对话「还有排队 run 等着执行」的消息 id。

    干预判定必须以 **run 表**为准，不能只看消息上的 `queued` 标记：那个标记会被
    认领、编辑、异常路径清成 0，一旦它为 0，排队消息就会被正在执行的 run 当成
    「对话内干预」取走，它的排队 run 随即被 `cancel_run_by_message` 删掉——结果
    这条消息既没单独跑一轮，也没有任何回复（实测踩到：run-53a8db3e5eb6 就这么
    从库里消失，用户看到的是「排队消息不自动处理」）。

    只算 `queued`（还没开始跑）的 run；已经 running 的那条，它的内容就是本轮
    目标本身（run.content），不需要再从历史里注入一遍。
    """
    from services.agent_zero.task_layer import run_service

    try:
        items = run_service.conversation_queue(conv_id)
    except Exception:
        logger.debug("读取排队 run 失败（干预判定退化）", exc_info=True)
        return set()
    return {str((r or {}).get("queued_msg_id") or "") for r in items} - {""}


def _drain_new_interventions(conv_id: str, seen: set,
                            run_id: str = "") -> List[Dict[str, Any]]:
    """取走「新到的用户消息」作为干预（一次一去）；`seen` 就地更新，避免重复注入。

    只取用户消息：助手回复也会出现在同一节点流里，但它们不是干预。

    **排队消息不算干预**：队列模式下，用户在上一条回复执行期间发的消息会先落
    转录（立即可见）并各有一条排队 run，等当前 run 结束再按顺序执行。这类消息
    必须留给 worker 单独跑——被正在执行的 run 顺手取走就等于「吞掉」。

    `run_id` = 取走它的那条 run。取走时把归属写到消息上（`injected_into_run`），
    转录排序据此把它放回**这一轮回复之后**：注入消息的排队 run 会被删掉，若没有
    这个归属，它就只能按自己的 created_at 排，重新落回「夹在当前回答上面」的位置
    （用户反馈的「立即插入也是一样」）。
    """
    out: List[Dict[str, Any]] = []
    still_queued = _queued_message_ids(conv_id)
    for m in _recent_exploration_rows(conv_id):
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        if m["by"] == "assistant" or not m["content"].strip():
            continue
        # 团队 @ 委派消息只用于成员 run 的任务上下文（_history_messages 已读取），
        # 不能再被当成「用户干预」注入到主持 / 成员正在跑的循环里。
        if m.get("kind") == "team_mention" or m.get("display_by") == "assistant":
            continue
        # 还有排队 run 等它执行：这是队列里的下一条，不是对当前 run 的干预。
        # 以 run 表为准（而不是消息上的 queued 标记）——标记会被认领 / 编辑 /
        # 异常路径改写，run 行才是「这条消息还没被处理」的事实依据。
        # 例外：用户点了「立即插入」（inject=1），那它本来就该现在被取走。
        if m["id"] in still_queued and not m.get("inject"):
            continue
        # 这条消息已被当作干预取走：删掉它对应的排队 run，避免之后再执行一遍
        try:
            from services.agent_zero.task_layer import run_service

            run_service.cancel_run_by_message(m["id"])
        except Exception:
            logger.debug("干预消息对应排队 run 清理失败: %s", m["id"])
        # 记下「被哪一轮取走」并清掉排队 / 插入标记：
        #   - 排队 run 已删，消息不再有待执行的回合，`queued` 留着会让前端一直显示
        #     「排队中」徽标（实测：立即插入后徽标不消失）；
        #   - `injected_into_run` 是转录排序的锚点（见 list_messages）；
        #   - 残留 inject=1 会让它以后被重复注入一次。
        if run_id:
            try:
                mark_message_injected(m["id"], run_id)
            except Exception:
                logger.debug("干预消息归属写回失败: %s", m["id"])
        out.append(m)
    return out


def mark_message_injected(msg_id: str, run_id: str) -> bool:
    """干预消息被某轮 run 取走：清 queued / inject，记下归属 run（排序锚点）。

    为什么需要归属：这条消息的排队 run 已被删除，转录里它就**没有任何 run 可锚**，
    排序只能退回自己的 created_at —— 而它落库早于当前回复，于是又显示在当前回答
    **上面**（用户反馈「立即插入也是一样」）。把「被哪一轮取走」写下来，排序就能把
    它放回那一回合里（与 handoff 的 queued 消息同一套锚点逻辑）。
    """
    if not msg_id:
        return False
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text("UPDATE gdag_nodes SET props = json_set(props, '$.queued', 0) "
                 "WHERE id = :id"),
            {"id": msg_id},
        )
        db.execute(
            text("UPDATE gdag_nodes SET props = json_set(props, '$.inject', 0) "
                 "WHERE id = :id"),
            {"id": msg_id},
        )
        if run_id:
            db.execute(
                text("UPDATE gdag_nodes SET props = json_set("
                     "props, '$.injected_into_run', :rid) "
                     "WHERE id = :id"),
                {"id": msg_id, "rid": run_id},
            )
        db.commit()
        return bool(res.rowcount)


def mark_queued_message_active(msg_id: str) -> bool:
    """排队 run 开始执行：解除消息的排队标记（此后进入历史 / 干预）。

    排队期间消息在转录里可见，但不进上下文——否则后面的排队消息会被前面的
    run 提前看到、重复执行。
    """
    if not msg_id:
        return False
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text("UPDATE gdag_nodes SET props = json_set(props, '$.queued', 0) "
                 "WHERE id = :id"),
            {"id": msg_id},
        )
        # 排队标记与「立即插入」标记一起清掉：这条消息马上就要作为本轮目标执行，
        # 残留 inject=1 会让它以后再被当成干预重复注入一次。
        db.execute(
            text("UPDATE gdag_nodes SET props = json_set(props, '$.inject', 0) "
                 "WHERE id = :id"),
            {"id": msg_id},
        )
        db.commit()
        return bool(res.rowcount)


def delete_message(msg_id: str) -> bool:
    """删除一条探索态消息节点（不限于排队状态）。"""
    if not msg_id:
        return False
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import decode_row

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT props FROM gdag_nodes "
                 "WHERE id = :id AND props->>'stage' = 'exploration'"),
            {"id": msg_id},
        ).first()
        res = db.execute(
            text("DELETE FROM gdag_nodes "
                 "WHERE id = :id AND props->>'stage' = 'exploration'"),
            {"id": msg_id},
        )
        db.commit()
        deleted = bool(res.rowcount)
    if deleted and row:
        props = decode_row({"props": row[0]}).get("props") or {}
        conv_id = str(props.get("conversation_id") or "")
        if conv_id:
            attachment_service.remove_attachments(conv_id, props.get("attachments") or [])
    return deleted


def set_message_queued(msg_id: str, queued: bool) -> bool:
    """设置 / 清除消息的排队标记（排队消息不进上下文，直到自己的 run 开始）。"""
    if not msg_id:
        return False
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text("UPDATE gdag_nodes SET props = json_set("
                 "props, '$.queued', CAST(:v AS INTEGER)) "
                 "WHERE id = :id"),
            {"id": msg_id, "v": "1" if queued else "0"},
        )
        db.commit()
        return bool(res.rowcount)


def get_message_node(conv_id: str, msg_id: str) -> Optional[Dict[str, Any]]:
    """取一条探索态消息节点（含 props / created_at）；不属于该对话返回 None。"""
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    node = GdagStore().get_node(msg_id)
    if not node:
        return None
    props = node.get("props") or {}
    if str(props.get("conversation_id") or "") != conv_id:
        return None
    if str(props.get("stage") or "") != "exploration":
        return None
    if str(props.get("kind") or "") == "task_proposal":
        return None
    return node


def update_message_content(conv_id: str, msg_id: str,
                           content: str,
                           prompt: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """改写一条用户消息的正文（保留 refs / queued 等其它属性）。

    ``prompt`` 是斜杠命令展开后的模型侧正文；传空字符串表示清除（回到普通消息）。
    """
    node = get_message_node(conv_id, msg_id)
    if not node:
        return None
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    props = dict(node.get("props") or {})
    props["content"] = content
    if prompt and prompt.strip() != content:
        props["prompt"] = prompt.strip()
    else:
        props.pop("prompt", None)
    props["edited_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    GdagStore().upsert_node({**node, "props": props})
    zero_store.touch_conversation(conv_id)
    return props


def truncate_messages(conv_id: str, msg_id: str, *,
                      include_self: bool = True) -> int:
    """删除该消息及其后的所有探索态消息（编辑 / 撤回的对话截断）。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, props FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
                 f"AND props->>'stage' = 'exploration' "
                 f"ORDER BY created_at ASC, rowid ASC"),
            {"cid": conv_id},
        ).fetchall()
        ids = [str(r[0]) for r in rows]
        if msg_id not in ids:
            return 0
        cut = ids[ids.index(msg_id):] if include_self else ids[ids.index(msg_id) + 1:]
        if not cut:
            return 0
        from services.agent_zero.stores.zero_store import decode_row

        for row in rows:
            if str(row[0]) not in cut:
                continue
            props = decode_row({"props": row[1]}).get("props") or {}
            attachment_service.remove_attachments(conv_id, props.get("attachments") or [])
        db.execute(
            text(f"DELETE FROM {_q('gdag_nodes')} "
                 f"WHERE id IN (SELECT value FROM json_each(:ids))"),
            {"ids": json.dumps(cut, ensure_ascii=False)},
        )
        # 截断后旧摘要可能覆盖了已删除的内容：一并清掉，下一轮重新压
        db.execute(
            text(f"DELETE FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
                 f"AND props->>'kind' = 'checkpoint'"),
            {"cid": conv_id},
        )
        db.commit()
    zero_store.touch_conversation(conv_id)
    # 文件检查点绑定在消息上：消息被截断删除后，对应检查点也应清理。
    try:
        from services.agent_zero.services import file_checkpoint_service

        file_checkpoint_service.prune_for_conversation(conv_id)
    except Exception:
        logger.debug("清理文件检查点失败 conv=%s", conv_id, exc_info=True)
    return len(cut)


def edit_message(conv_id: str, msg_id: str, content: str,
                 prompt: Optional[str] = None) -> Dict[str, Any]:
    """编辑用户消息。

    - 排队中：更新正文并**换一条新的排队 run**（原排队 run 停止；位置/优先级保留）。
    - 已执行：截断该消息之后的对话并重新发起。
    - 正在执行：拒绝（先停止运行）。

    ``prompt`` = 斜杠命令展开后的模型侧正文（不传则按普通消息处理）。
    """
    from services.agent_zero.task_layer import run_service

    node = get_message_node(conv_id, msg_id)
    if not node:
        raise ValueError(f"消息不存在: {msg_id}")
    props = node.get("props") or {}
    if str(props.get("by") or "") == "assistant":
        raise ValueError("只能编辑用户消息")
    content = (content or "").strip()
    if not content:
        raise ValueError("消息为空")
    conv = get_conversation(conv_id)
    if not conv:
        raise ValueError(f"对话不存在: {conv_id}")

    run = run_service.run_for_message(conv_id, msg_id)
    if run and run.get("status") == "running":
        raise ValueError("消息正在执行，无法编辑（请先停止运行）")

    update_message_content(conv_id, msg_id, content, prompt=prompt)
    zero_store.append_audit("local", "message_edit",
                            args={"conversation_id": conv_id, "message_id": msg_id,
                                  "content_chars": len(content),
                                  "was_queued": bool(
                                      (run or {}).get("status") == "queued"
                                      or int(props.get("queued") or 0) == 1)})

    # 排队中（或排队的孤儿消息）：替换排队 run，位置不变
    if (run and run.get("status") == "queued") or (
            not run and int(props.get("queued") or 0) == 1):
        # 保留原排队位置：同一个 queue_pos 写回，新 run 仍排在同一处
        queue_pos = (run or {}).get("queue_pos")
        set_message_queued(msg_id, True)
        if run:
            run_service.discard_run(run["id"])
        new_run = run_service.create_run(
            conv_id, (run or {}).get("created_by") or "local",
            role_id=(run or {}).get("role_id") or conv.get("role_id"),
            content=content,
            mode=(run or {}).get("mode") or "autonomous",
            auto_execute=bool((run or {}).get("auto_execute")),
            queue_pos=queue_pos,
            queued_msg_id=msg_id,
            thinking_level=(run or {}).get("thinking_level"),
        )
        return {"message": {"id": msg_id, "content": content, "queued": True},
                "run": new_run, "replaced": True, "truncated": 0}

    # 已执行：截断这条消息之后的对话并重新发起
    if run_service.running_run(conv_id):
        raise ValueError("当前有运行正在执行，请先停止后再编辑")
    removed = truncate_messages(conv_id, msg_id, include_self=False)
    run_service.cancel_queued_runs(conv_id)
    mode = (run or {}).get("mode") or (
        "chat" if conv.get("run_mode") == "chat" else "autonomous")
    new_run = run_service.create_run(
        conv_id, "local", role_id=conv.get("role_id"),
        content=content, mode=mode,
        auto_execute=bool((run or {}).get("auto_execute")),
        queued_msg_id=msg_id,
        thinking_level=(run or {}).get("thinking_level"),
    )
    return {"message": {"id": msg_id, "content": content, "queued": False},
            "run": new_run, "replaced": False, "truncated": removed}


def recall_message(conv_id: str, msg_id: str) -> Dict[str, Any]:
    """撤回用户消息。

    - 排队中：直接删除排队 run + 消息（队列撤回）。
    - 已执行：直接截断该消息及其后的对话。
    """
    from services.agent_zero.task_layer import run_service

    node = get_message_node(conv_id, msg_id)
    if not node:
        raise ValueError(f"消息不存在: {msg_id}")
    props = node.get("props") or {}
    if str(props.get("by") or "") == "assistant":
        raise ValueError("只能撤回用户消息")

    run = run_service.run_for_message(conv_id, msg_id)
    if run and run.get("status") == "running":
        raise ValueError("消息正在执行，请先停止运行后再撤回")
    if run and run.get("status") == "queued":
        run_service.cancel_run(run["id"])       # 同时删除消息
        return {"mode": "queue", "messages_removed": 1}

    if run_service.running_run(conv_id):
        raise ValueError("当前有运行正在执行，请先停止后再撤回")

    removed = truncate_messages(conv_id, msg_id, include_self=True)
    run_service.cancel_queued_runs(conv_id)
    result = {
        "mode": "truncate",
        "messages_removed": removed,
    }
    zero_store.append_audit("local", "message_recall",
                            args={"conversation_id": conv_id, "message_id": msg_id,
                                  "run_id": (run or {}).get("id", "")},
                            result=result)
    return result


def _trace_from_run_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把已落库的运行事件还原成可展示的部分时间线。

    用于停止 / 崩溃 / 超时：模型没来得及产出 done，但已经想过、说过、调过工具，
    这些内容必须保留，下次继续时也有据可查。
    """
    trace: List[Dict[str, Any]] = []
    open_text: Optional[Dict[str, Any]] = None
    tools: Dict[str, Dict[str, Any]] = {}
    thinking_parts: List[str] = []

    def close_text() -> None:
        nonlocal open_text
        open_text = None

    for evt in events or []:
        if not isinstance(evt, dict) or evt.get("ping"):
            continue
        if "delta" in evt:
            piece = str(evt.get("delta") or "")
            if not piece:
                continue
            if open_text is None:
                open_text = {"kind": "text", "round": evt.get("round"), "text": ""}
                trace.append(open_text)
            open_text["text"] += piece
        elif "thinking" in evt:
            piece = str(evt.get("thinking") or "")
            if not piece:
                continue
            close_text()
            thinking_parts.append(piece)
            if trace and trace[-1].get("kind") == "thinking" \
                    and trace[-1].get("round") == evt.get("round"):
                trace[-1]["text"] = str(trace[-1].get("text") or "") + piece
            else:
                trace.append({"kind": "thinking", "round": evt.get("round"),
                              "text": piece})
        elif "tool_call" in evt:
            c = evt.get("tool_call") or {}
            cid = str(c.get("id") or "")
            if not c.get("name"):
                continue
            close_text()
            hit = tools.get(cid) if cid else None
            if hit is None and c.get("status") != "preparing":
                hit = next((s for s in trace
                            if s.get("kind") == "tool" and s.get("status") == "preparing"
                            and not s.get("id") and s.get("name") == c.get("name")
                            and s.get("round") == c.get("round")), None)
                if hit is not None:
                    hit["id"] = cid
                    if cid:
                        tools[cid] = hit
            if hit is not None:
                hit.update({k: v for k, v in c.items() if k != "id"})
                hit["kind"] = "tool"
                hit["status"] = c.get("status") or hit.get("status") or "running"
            else:
                step = {"kind": "tool", "id": cid, "name": c.get("name"),
                        "args": c.get("args"), "round": c.get("round"),
                        "status": c.get("status") or "running"}
                trace.append(step)
                if cid:
                    tools[cid] = step
        elif "tool_result" in evt:
            r = evt.get("tool_result") or {}
            rid = str(r.get("id") or "")
            close_text()
            hit = tools.get(rid) if rid else None
            if hit is not None:
                hit.update({k: v for k, v in r.items() if k != "id"})
                hit["kind"] = "tool"
                hit["status"] = "done"
            else:
                step = {"kind": "tool", "id": rid, "name": r.get("name"),
                        "args": r.get("args"), "ok": r.get("ok"),
                        "summary": r.get("summary"), "display": r.get("display"),
                        "duration_ms": r.get("duration_ms"),
                        "round": r.get("round"), "status": "done"}
                trace.append(step)
                if rid:
                    tools[rid] = step
        elif "retry" in evt:
            # 网络抖动自动重试：记录「曾经重试过」这件事（刷新后仍在），
            # 同一轮多次重试合并成一条 —— 20 次重试不该在时间线里刷 20 条。
            r = evt.get("retry") or {}
            # 注意：不 close_text() —— 重试是「从断点续写」，后面的正文仍属于
            # 同一段话，切成两条会在时间线上凭空多出一段。
            hit = trace[-1] if trace and trace[-1].get("kind") == "retry" \
                and trace[-1].get("round") == evt.get("round") else None
            if hit is not None:
                hit["attempt"] = r.get("attempt")
                hit["max_attempts"] = r.get("max_attempts")
                hit["text"] = r.get("message") or hit.get("text")
            else:
                trace.append({"kind": "retry", "round": evt.get("round"),
                              "attempt": r.get("attempt"),
                              "max_attempts": r.get("max_attempts"),
                              "text": r.get("message") or ""})
        elif "task_proposal" in evt:
            p = evt.get("task_proposal") or {}
            close_text()
            trace.append({"kind": "proposal", "status": "pending",
                          "proposal_id": p.get("id"), "goal": p.get("goal"),
                          "reason": p.get("reason")})
        elif "tool_approval" in evt:
            a = evt.get("tool_approval") or {}
            close_text()
            trace.append({"kind": "approval", "id": a.get("id"),
                          "name": a.get("tool"), "risk": a.get("risk") or "medium",
                          "reasons": a.get("reasons") or [], "note": a.get("note") or "",
                          "args": a.get("args"), "preview": a.get("preview"),
                          "status": "pending", "round": a.get("round")})
        elif "approval_decided" in evt:
            d = evt.get("approval_decided") or {}
            hit = next((s for s in trace if s.get("kind") == "approval"
                        and s.get("id") == d.get("id")), None)
            if hit is not None:
                hit["status"] = d.get("status") or "denied"

    last_text = next((s for s in reversed(trace)
                      if s.get("kind") == "text" and str(s.get("text") or "").strip()),
                     None)
    return {
        "trace": trace,
        "content": str((last_text or {}).get("text") or "").strip(),
        "reasoning": "".join(thinking_parts),
        "tool_trace": [s for s in trace if s.get("kind") == "tool"],
    }


def persist_interrupted_run(run_id: str, *, outcome: str = "failed",
                            error: str = "", silent: bool = False) -> Optional[str]:
    """把停止 / 超时 / 崩溃的运行保留为一条助手消息（含部分正文与轨迹）。

    `silent=True`（「立即插入 = 停止并继续」）：正文与轨迹照常保留，但不给消息
    打 `interrupted` 标记——这轮是被正常流程接续的，界面不该出现「已中断」红条。
    """
    from services.agent_zero.task_layer import run_service

    run = run_service.get_run(run_id)
    if not run:
        return None
    conv_id = str(run.get("conversation_id") or "")
    conv = get_conversation(conv_id) if conv_id else None
    if not conv:
        return None
    events = run_service.list_all_events(run_id)
    partial = _trace_from_run_events(events)
    if not partial["trace"] and not partial["content"]:
        return None
    # 同一 run 已经落过助手消息（例如进程在收尾前崩溃）就不再重复。
    try:
        from sqlalchemy import text
        from services.agent_zero.database import get_session_local
        from services.agent_zero.stores.zero_store import _q

        SessionLocal = get_session_local()
        with SessionLocal() as db:
            exists = db.execute(
                text(f"SELECT 1 FROM {_q('gdag_nodes')} WHERE type = 'fact' "
                     f"AND props->>'run_id' = :rid AND props->>'by' = 'assistant' LIMIT 1"),
                {"rid": run_id},
            ).first()
        if exists:
            return None
    except Exception:
        pass
    role_id = str(run.get("target_role_id") or conv.get("role_id") or "")
    role = zero_store.get_role(role_id) if role_id else None
    if not role:
        role = {"id": role_id or None, "name": APP_NAME}
    model = ""
    try:
        from services.agent_zero.runtime.zero import llm as llm_mod

        code = _resolved_chat_models(conv, role).get("loop") or "default"
        model = llm_mod.get_model_name(code)
    except Exception:
        model = ""
    usage: Dict[str, Any] = {}
    for evt in reversed(events):
        if isinstance(evt, dict) and isinstance(evt.get("usage"), dict):
            usage = evt["usage"]
            break
    return _persist_assistant(
        conv, role, partial["content"], model, 0,
        partial["reasoning"], partial["tool_trace"], partial["trace"],
        usage=usage, outcome=outcome, error=error,
        interrupted=not silent, run_id=run_id,
        # 后台任务的成员 run：部分产出也不进前台转录（任务完成由触发器通知）。
        stage="task" if run.get("team_task_id") else "exploration")


def _usage_snapshot(totals: Dict[str, int], cached_tokens: int,
                    prompt_sum: int, llm_ms: int, rounds: int) -> Dict[str, Any]:
    """某一时刻的累计用量快照（每轮模型请求后推送，前端实时显示）。"""
    return {
        **totals,
        "cached_tokens": int(cached_tokens),
        "cache_rate": round(cached_tokens / prompt_sum * 100, 1) if prompt_sum else 0,
        "llm_ms": int(llm_ms),
        "rounds": int(rounds),
    }


# LLM 流式等待：上游迟迟不回第一个 token 时，前端看起来就是「卡死」。
# 每 `stream_keepalive_s`（默认 20s）发一次心跳（同时让 worker 有机会检查停止标记）。
#
# **默认不做「无输出就中止」**（`llm.stream_idle_timeout_s` 默认 0 = 不限）：
# 长思考、长工具参数（write_file 全文）、上游排队、网关整段缓冲都可能长时间
# 没有一个流事件，而这些都是**正常**的——过去 120s 一刀切把它判成「上游无响应」
# 并中止整轮，误判极多、体验极差。现在改成：一直等 + 自动重试（网络抖动由
# llm.py 的 20 次 × 2.5s 重试与断流续写兜住），需要硬超时时再显式配置。
LLM_KEEPALIVE_SECONDS = 20.0
LLM_IDLE_TIMEOUT_SECONDS = 0.0


def _stream_watchdog() -> Tuple[float, float]:
    """(心跳间隔, 空闲中止秒)；0 秒 = 不中止。配置改动即时生效。"""
    try:
        from services.agent_zero.runtime.zero import llm as llm_mod

        return llm_mod.get_stream_watchdog_config()
    except Exception:
        return LLM_KEEPALIVE_SECONDS, LLM_IDLE_TIMEOUT_SECONDS


async def _iter_stream_with_keepalive(
    stream: AsyncIterator[Dict[str, Any]],
) -> AsyncIterator[Dict[str, Any]]:
    """消费上游流，并在等待期间产出 keepalive（必要时按配置超时失败）。

    把上游放进独立 task，消费者用 queue + 超时取事件：这样即使上游阻塞在
    HTTP 读取上，也能规律地发心跳、检查停止——**心跳本身让运行级看门狗知道
    这一轮还活着**，因此不需要再用「多少个字都没吐就掐掉」来兜时间。

    `LLM_IDLE_TIMEOUT_SECONDS`（`llm.stream_idle_timeout_s`）默认为 0：不限时。
    只有显式配置 > 0 时才会在长时间无任何流事件后抛错中止。
    """
    keepalive_s, idle_timeout_s = _stream_watchdog()
    queue: asyncio.Queue = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for ev in stream:
                await queue.put(("evt", ev))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 原样交给消费者
            await queue.put(("err", e))
        finally:
            try:
                queue.put_nowait(("end", None))
            except Exception:
                pass

    task = asyncio.create_task(_pump())
    idle_since = time.monotonic()
    produced = False        # 已经产出过内容？（心跳文案据此区分「等首个 token」与「输出中断」）
    try:
        while True:
            try:
                kind, item = await asyncio.wait_for(
                    queue.get(), timeout=keepalive_s)
            except asyncio.TimeoutError:
                waited = time.monotonic() - idle_since
                if idle_timeout_s > 0 and waited >= idle_timeout_s:
                    from services.agent_zero.runtime.zero import llm as _llm

                    raise _llm.LlmError(
                        f"模型 {int(waited)}s 没有任何输出，已中止本轮"
                        f"（上游无响应；可用 llm.stream_idle_timeout_s 调大或设 0 关闭）",
                        kind="timeout")
                yield {"type": "keepalive", "elapsed_s": int(waited),
                       "phase": "mid_output" if produced else "first_token"}
                continue
            if kind == "evt":
                idle_since = time.monotonic()
                if item.get("type") in ("delta", "thinking", "tool_preparing"):
                    produced = True
                yield item
            elif kind == "err":
                raise item
            else:
                return
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


async def _await_tool_approval(appr_id: str, run_id: Optional[str], name: str,
                               rid: Any, rname: Any, out: Dict[str, Any]
                               ) -> AsyncIterator[Dict[str, Any]]:
    """等待一次工具审批：期间发心跳，结束时把决定写进 ``out`` 并推 decided 事件。

    审批等待在主循环里出现两次（team_manage 的强制确认、普通危险操作），
    抽出来避免重复，也让「等待期间必须心跳、收到停止要能醒」只有一处实现。
    """
    from services.agent_zero.task_layer import permissions

    dec_task = asyncio.ensure_future(
        permissions.await_decision(appr_id, run_id))
    waited = 0.0
    while not dec_task.done():
        if await _await_uncancelled(dec_task, TOOL_KEEPALIVE_SECONDS):
            break
        waited += TOOL_KEEPALIVE_SECONDS
        yield {"ping": True, "elapsed_s": int(waited),
               "waiting_approval": appr_id, "name": name,
               "role_id": rid, "role_name": rname}
    out.update(dec_task.result() or {})
    permissions.forget(appr_id)
    yield {"approval_decided": {
        "id": appr_id,
        "status": out.get("status", "denied"),
        "scope": out.get("scope", ""),
        "role_id": rid, "role_name": rname}}


GLOBAL_AGENT_ADDON = """你是 AtlasX 的**全局 Agent（工单助手 / 管理入口）**，只负责需要审批的权限与跨会话管理。

你的职责：
- 查看跨会话待审批的工具授权；
- 根据用户指示允许一次 / 本会话允许 / 角色永久 / 群组永久 / 拒绝；
- 管理角色在线 / 离线状态、组织部门与角色库；
- 管理定时任务与自由触发任务（schedule_manage）：可以创建按时间执行的任务，
  也可以创建由模型判断时机的主动任务；
- 管理模型主动消息（app_manage 的 proactive_status / set_proactive /
  cancel_proactive）：默认每个会话每天最多 6 条、间隔 10 分钟，可随时关闭；
- 回答要短，先结论后动作；执行后说明结果；
- 不要展示普通会话、未读消息或运行中的任务，除非用户明确追问；
- 你不是代码执行代理，不要为了处理审批去读写用户项目文件。
"""


def _global_agent_addon() -> str:
    """工单助手 的系统提示附加段：职责 + 当前 App 快照。"""
    try:
        from services.agent_zero.services import attention_service

        snap = attention_service.snapshot(50)
        compact = {
            "counts": {"approvals": int((snap.get("counts") or {}).get("approvals") or 0)},
            "approvals": [
                {k: x.get(k) for k in ("id", "conversation_id", "conversation_title",
                                        "tool", "risk", "note")}
                for x in (snap.get("approvals") or [])[:50]
            ],
            "policy": snap.get("policy"),
        }
        return (GLOBAL_AGENT_ADDON
                + "\n\n## 当前 App 快照（JSON）\n```json\n"
                + json.dumps(compact, ensure_ascii=False)
                + "\n```")
    except Exception as e:
        logger.debug("工单助手 快照失败: %s", e)
        return GLOBAL_AGENT_ADDON


async def _prepare_role_run(
    conv: Dict[str, Any], role: Dict[str, Any], user: str, *,
    autonomous: bool = False,
    auto_execute: bool = False,
    isolated: bool = False,
    context_messages: Optional[List[Dict[str, Any]]] = None,
    system_addon: str = "",
    extra_context: str = "",
) -> Dict[str, Any]:
    """准备一次单角色运行所需的模型 / 团队 / 提示词 / 历史 / 工具清单。

    从 `_stream_role` 里拆出来的第一块纯组装逻辑（无 yield、无工具执行）：
    只解析配置、读团队与目标、拼系统提示与历史消息。抽出来后 `_stream_role`
    负责「循环 + 事件」，这里负责「本轮从哪来」，便于单测与后续继续拆分。
    """
    from services.agent_zero.runtime.zero import llm as llm_mod
    from services.agent_zero.runtime.zero.dispatch import ToolDispatcher
    models = _resolved_chat_models(conv, role)
    display_model = llm_mod.get_model_name(models.get("loop") or "default")
    try:
        _run_cfg = await llm_mod.get_llm_config(models.get("loop") or "default")
        logger.debug("对话模型 conv=%s code=%s model=%s base=%s",
                     conv.get("id"), models.get("loop"), display_model,
                     _run_cfg.get("base_url") or "")
    except Exception:
        pass
    ws_id = str(conv.get("workspace_id") or "")
    camp = "default"
    envelope = _chat_envelope(role)
    is_global = (str(conv.get("zone") or "") == GLOBAL_AGENT_ZONE) and not isolated
    prompt_role = role
    # Agent 团队（可空；失败不阻断对话）；子代理隔离时不继承父团队
    team: Optional[Dict[str, Any]] = None
    if not isolated and not is_global:
        try:
            from services.agent_zero.services import team_service as _tsvc

            team = _tsvc.team_for_conv(conv)
        except Exception as e:
            logger.debug("团队加载跳过: %s", e)
    # 目标模式：当前对话的进行中目标（可空）；子代理隔离时不继承
    goal: Optional[Dict[str, Any]] = None
    if not isolated:
        try:
            from services.agent_zero.task_layer import goal_service as _gsvc

            goal = _gsvc.active_goal(conv["id"])
        except Exception as e:
            logger.debug("目标读取跳过: %s", e)
    # MCP：把已启用服务的工具并入本轮工具清单（失败不阻断对话）
    mcp_specs: List[Dict[str, Any]] = []
    try:
        from services.agent_zero.services import mcp_service

        mcp_specs = await mcp_service.enabled_tool_specs(
            conversation_id=str(conv.get("id") or ""),
            role_id="" if is_global else str(role.get("id") or ""),
            team_id=str((team or {}).get("id") or ""),
        )
    except Exception as e:
        logger.warning("MCP 工具列表获取失败: %s", e)
    # single 模式的团队只作角色预设：不下发委派工具，避免把「单角」跑成群聊。
    team_bound = bool(team) and str((team or {}).get("mode") or "") != "single"
    specs = _chat_tool_specs(envelope, None, autonomous=autonomous,
                             workspace_id=ws_id, mcp_specs=mcp_specs,
                             team_bound=team_bound)
    if isolated:
        # 会话指令操作的是用户眼前的这个会话：子代理 / 团队成员不能替用户点界面。
        specs = [s for s in specs
                 if (s.get("function") or {}).get("name") != "session_manage"]
    if is_global:
        from services.agent_zero.tools import registry as tool_reg

        # 工单助手 只拿「管理 App」需要的工具，不继承默认角色的 shell / 文件能力。
        specs = []
        global_tools = ["app_manage", "session_manage", "ask_user",
                        "department_manage", "role_manage",
                        "schedule_manage", "team_manage"]
        for code in global_tools:
            tool = tool_reg.get_tool(code)
            if tool is not None:
                specs.append(tool.schema())
        envelope = {**envelope, "tools": global_tools}
        prompt_role = {**role, "envelope": envelope}
        system_addon = (system_addon + "\n\n" + _global_agent_addon()).strip()
    # 复用任务分发器所需的「伪任务 / 伪节点」上下文（workspace / camp / envelope）；
    # 关闭幂等键，故 id 稳定即可。
    config: Dict[str, Any] = {
        "code": role.get("id"), "name": role.get("name"),
        "zero": {"enabled": True, "envelope": envelope, "models": models,
                 "budget": {}, "gdag": {"camp": camp}},
    }
    task_ctx: Dict[str, Any] = {
        "id": f"conv:{conv['id']}", "workspace_id": ws_id, "camp": camp,
        "submitter": user, "conversation_id": conv["id"],
        "role_id": "" if is_global else str(role.get("id") or ""),
    }
    node: Dict[str, Any] = {"id": f"conv:{conv['id']}", "props": {"envelope": envelope}}
    loop = ToolDispatcher()
    # 组件/能力层已移除（本地单用户版）；保留 None 以复用中央分发器的签名。
    caps: Any = None

    vision = llm_mod.model_supports_vision(models.get("loop") or "default")
    _hist_limit, _hist_tokens = await _history_limits(models)
    if context_messages is not None:
        # 子代理 / 团队成员：只用显式给出的子任务上下文，不读父对话历史
        history = list(context_messages)
    else:
        history = _history_messages(conv["id"], limit=_hist_limit, vision=vision,
                                    max_tokens=_hist_tokens)
    # 自动记忆沉淀与召回的触发文本：最后一条用户消息
    user_query = next((str(m.get("content") or "") for m in reversed(history)
                       if m.get("role") == "user"), "")
    system_content = _chat_system_prompt(
        prompt_role, ws_id, autonomous=autonomous, auto_execute=auto_execute,
        conv_id=conv["id"], user_query=user_query, team=team, goal=goal,
        skills_role_id=None if is_global else str(role.get("id") or ""))
    if system_addon.strip():
        system_content = system_content + "\n\n" + system_addon.strip()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_content},
    ]
    if extra_context:
        messages[0]["content"] = (str(messages[0]["content"])
                                   + "\n\n## 用户提交钩子注入的上下文（UserPromptSubmit）\n"
                                   + extra_context[:20_000])
    messages.extend(history)

    return {
        "models": models,
        "display_model": display_model,
        "ws_id": ws_id,
        "team": team,
        "goal": goal,
        "specs": specs,
        "config": config,
        "task_ctx": task_ctx,
        "node": node,
        "loop": loop,
        "caps": caps,
        "vision": vision,
        "messages": messages,
        "user_query": user_query,
    }


async def _stream_role(conv: Dict[str, Any], user: str, role: Dict[str, Any],
                       sink: Dict[str, Any],
                       autonomous: bool = False,
                       auto_execute: bool = False,
                       _depth: int = 0,
                       _max_rounds: Optional[int] = None,
                       run_id: Optional[str] = None,
                       extra_context: str = "",
                       isolated: bool = False,
                       context_messages: Optional[List[Dict[str, Any]]] = None,
                       system_addon: str = "") -> AsyncIterator[Dict[str, Any]]:
    """单角色流式回复（含工具循环：**按角色包络执行全部工具** + LLM 发起转任务）。

    yield {start} / {delta} / {task_proposal}；成功时把最终文本与耗时写入 sink，
    失败写 sink['error']。

    对话阶段与任务阶段共用中央工具底座：`_chat_tool_specs` 按能力包络放开全部工具，
    执行复用 ToolDispatcher；长任务由 propose_task 触发本对话内的自主执行。
    ``autonomous=True``：系统提示切换为「动态规划自主执行」。

    ``isolated=True``（子代理 / 团队成员）：不读取父对话历史、不继承父团队与目标，
    只使用 ``context_messages`` 里显式给出的子任务上下文；``system_addon`` 追加到
    系统提示末尾（子代理约束）。父对话不会因为子代理的中间过程而膨胀。
    """
    from services.agent_zero.runtime.zero import llm as llm_mod

    # **先报身份，再做准备**：发言角色在这里就已经确定，而
    # `_prepare_role_run` 要读团队 / 目标 / 历史、拼系统提示（代码图谱、记忆、
    # 技能、项目约定）、拉 MCP 工具清单——大工作区里这一段可能要好几秒。
    # 以前 start 事件放在准备之后，前端只能先用兜底的「助手」占位，等准备完
    # 才换成真实角色，看起来就是「先显示助手，再变成指定 / 通用角色」。
    # 现在身份立刻下发，准备耗时只影响正文，不再影响「谁在说话」。
    rid, rname = role.get("id"), role.get("name")
    yield {"start": True, "role_id": rid, "role_name": rname}

    _t_prepare = time.perf_counter()
    setup = await _prepare_role_run(
        conv, role, user, autonomous=autonomous, auto_execute=auto_execute,
        isolated=isolated, context_messages=context_messages,
        system_addon=system_addon, extra_context=extra_context)
    _prepare_ms = int((time.perf_counter() - _t_prepare) * 1000)
    # 5s 的准备时间足够让用户以为「卡住了」：慢的时候留一条可检索的日志
    # （模块默认 INFO，正常速度只写 DEBUG，不刷屏）。
    _log_prepare = logger.info if _prepare_ms >= 2000 else logger.debug
    _log_prepare("角色准备耗时 %dms role=%s conv=%s", _prepare_ms, rid,
                 (conv or {}).get("id"))
    models = setup["models"]
    display_model = setup["display_model"]
    ws_id = setup["ws_id"]
    team = setup["team"]
    goal = setup["goal"]
    specs = setup["specs"]
    # 单角色会话与群组会话都支持「对话 + 后台任务」分离：一个角色可以同时
    # 并行派发多项任务。子代理（isolated）与后台任务的成员 run 不再嵌套派发任务，
    # 避免无限套娃；任务成员仍可派生**子代理**（delegate target=subagent）。
    _is_task_member = False
    if run_id:
        try:
            from services.agent_zero.task_layer import run_service as _rsvc0

            _is_task_member = bool(
                (_rsvc0.get_run(run_id) or {}).get("team_task_id"))
        except Exception:
            _is_task_member = False
    if not isolated:
        if not _is_task_member:
            if not team:
                _drop_team_tool(specs)
            _append_background_task_tool(specs)
            _append_proactive_tool(specs)
        _append_role_discussion_tool(specs)
    if _is_task_member:
        # 后台任务在会话里单独跑，但界面动作属于用户当前这篇对话，任务成员不碰。
        specs = [s for s in specs
                 if (s.get("function") or {}).get("name") != "session_manage"]
    if _is_task_member and not isolated:
        from services.agent_zero.task_layer import prompt_builder as _pb

        setup["messages"][0]["content"] = (str(setup["messages"][0]["content"])
                                           + "\n\n" + _pb.TASK_MEMBER)
    config = setup["config"]
    task_ctx = setup["task_ctx"]
    node = setup["node"]
    loop = setup["loop"]
    caps = setup["caps"]
    vision = setup["vision"]
    messages = setup["messages"]
    sink["user_query"] = setup["user_query"]

    # ── 上下文占用的实时估算（顶部「上下文 Xk」）─────────────────────────
    # 锚点 + 增量模型见 `_CtxMeter`；开局锚点由它自己按 system + 历史估一次。
    _ctx = _CtxMeter(messages)
    _ctx_add = _ctx.add
    _ctx_anchor = _ctx.anchor
    _ctx_tokens = _ctx.tokens

    # 入口压缩：ceiling 判定 → 推「开始」→ 摘要（期间每 20s 心跳）→ 推「结束」。
    try:
        from services.agent_zero.runtime.zero import compress as _cz0

        _decision0 = await _cz0.decide(messages, models=models)
        if _decision0.get("should_compress"):
            _res0 = None
            async for _ev in _apply_compaction(
                    messages, models, _decision0, 0, rid, rname, run_id or ""):
                if "_compressed" in _ev:
                    _res0 = _ev["_compressed"]
                    continue
                yield _ev
            if _res0:
                messages = list(_res0.get("messages") or messages)
                persist_compaction_result(conv, _res0)
                # 压缩刚完成：以压缩结果的实测值作为新锚点，读数立刻回落。
                _ctx_anchor(int(_res0.get("tokens") or 0))
    except Exception as _ce0:
        logger.debug("入口上下文压缩跳过: %s", _ce0)

    parts: List[str] = []
    thinking: List[str] = []          # 深度思考全文（落库 + done 回传）
    tool_trace: List[Dict[str, Any]] = []   # 工具调用情况（落库 + done 回传）
    # 本条消息已用掉的展示载荷预算（字符数）。**每条消息重置**——它是单条消息
    # 的上限，不是会话累计上限（否则长会话后半段一个文件都展示不出来）。
    _display_used = 0
    # 有序轨迹：把「思考 / 工具 / 正文」按发生顺序串成一条时间线。
    # 分开存（thinking / tool_trace）会丢失交错关系——模型真实的过程是
    # 思考→工具→思考→工具→…→正文，前端要照这个顺序渲染才看得出它在干什么。
    #
    # `ts` = 距本轮开始的毫秒数，**每条都有**。排序只认 `ts`（其次 `seq`），
    # 不认 kind：任何"把正文挪到末尾""把思考并到一处"的整理都是伪造顺序。
    trace_timeline: List[Dict[str, Any]] = []
    _seq = 0
    _rounds_used = -1        # 实际跑到第几轮（末轮正文才可能是最终答复）
    started = time.time()
    # 可观测：LLM 往返总耗时 / 工具执行总耗时，用于定位"慢在哪"（轮数 vs 工具）。
    _llm_ms_total = 0
    _tool_ms_total = 0
    # 时间线里的"当前正文步"：同一轮中途说的话合并进同一条，
    # 但**一旦插入了思考或工具就另起一条**，否则会把时间点不同的两段话
    # 并成一条，顺序又被抹平。
    _open_text: Optional[Dict[str, Any]] = None
    # 上游真实 prompt token 数（对齐 OpenCode overflow.isOverflow：优先用真实
    # tokens.total 而非本地字符估算），用于主动判断是否该压缩，而不是等 400。
    _observed_prompt_tokens = 0
    # token 用量累计：输入 = 各轮 prompt 之和（多轮循环每轮都重发上下文，
    # 累加才是真实开销），max_context_tokens = 最大上下文规模；输出累加。
    # last_prompt_tokens = **最近一轮**真实 prompt（= 当前上下文占用）：
    # max 是历史峰值，压缩后不会回落，不能拿来当「现在用了多少」。
    _usage_totals: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0,
                                     "last_prompt_tokens": 0}
    # 上游回传的 prompt 缓存命中（prompt_tokens_details.cached_tokens）：按各轮求和，
    # 供每轮「时间 / 速度 / 缓存率」统计条展示。
    _cached_tokens = 0
    _prompt_sum = 0
    # 对话内干预游标：启动时已有的消息视为「已见」，之后新到的用户消息注入本轮。
    try:
        _seen_msgs = {m["id"] for m in _recent_exploration_rows(conv["id"])}
    except Exception:
        _seen_msgs = set()

    def _now_ms() -> int:
        """距本轮开始的毫秒数——时间线的唯一排序依据。"""
        return int((time.time() - started) * 1000)

    def _stamp(entry: Dict[str, Any]) -> Dict[str, Any]:
        """给一条时间线记录盖上时间戳与序号（就地返回）。"""
        entry["ts"] = _now_ms()
        entry["seq"] = _seq
        return entry

    try:
        # 轮次不设上限：模型自己不再返回 tool_calls 时才收尾（break）。
        # 曾用 MAX_CHAT_TOOL_ROUNDS=6 截断，导致「分析整个仓库」这类长任务在第 6 轮
        # 被砍断——循环自然走完时最后一轮仍是 tool_calls，没有任何纯文字收尾，
        # 最终 text 为空，前端只剩「模型未单独给出结论」（2026-10-01 线上现象）。
        _round = -1
        _any_work_tool_called = False
        # 「模型输出被截断 / 只思考不说话」时的续跑预算：第一次续跑自动把
        # max_tokens 翻倍，避免长思考把工具调用挤掉后整轮直接失败。
        _empty_retries = 0
        _chat_max_tokens = CHAT_MAX_TOKENS
        # 已把任务派给团队成员：本轮不再自己动手做同一件事（避免「任务已分配、
        # 主持还在前台跑工具」的重复消耗）。派发后只允许一轮纯文字收尾。
        _team_delegated = False
        _delegated_wrapped = False

        while True:
            _round += 1
            _rounds_used = _round        # 记下实际跑到第几轮
            # 子代理和主循环都不设轮次上限；只有显式配置了正数上限才会停。
            _cap = _max_rounds if _max_rounds is not None else _MAX_TURN_ROUNDS
            if _cap and _round >= _cap:
                logger.warning("对话轮次达到上限 %s（conv=%s），停止自动推进",
                               _cap, conv["id"])
                trace_timeline.append(_stamp({
                    "kind": "text", "round": _round,
                    "text": f"（系统）本轮达到 { _cap } 轮上限，已停止自动推进；"
                            "如需继续，请再发一条消息。"}))
                break
            # 上下文压缩（对齐 OpenCode V2）：用上游实测 prompt 与 ceiling 比较，
            # 到点才压。压缩跑在独立 task 里并推心跳——摘要模型慢/挂住时界面显示
            # 「正在压缩上下文」，不会看起来像卡死。
            if _round > 0 and _observed_prompt_tokens:
                try:
                    from services.agent_zero.runtime.zero import compress as _cz

                    _dec = await _cz.decide(
                        messages, models=models,
                        measured_tokens=_observed_prompt_tokens,
                    )
                    if _dec.get("should_compress"):
                        _res = None
                        async for _ev in _apply_compaction(
                                messages, models, _dec, _round, rid, rname,
                                run_id or ""):
                            if "_compressed" in _ev:
                                _res = _ev["_compressed"]
                                continue
                            yield _ev
                        if _res:
                            messages = list(_res.get("messages") or messages)
                            persist_compaction_result(conv, _res)
                            _observed_prompt_tokens = 0   # 已按新上下文重算
                            # 压缩刚完成：以压缩结果的实测值作为新锚点，读数立刻回落。
                            _ctx_anchor(int(_res.get("tokens") or 0))
                except Exception as _ce:
                    logger.debug("轮次内上下文压缩跳过: %s", _ce)
            # 对话内干预：本轮 LLM 调用前，把用户新消息注入上下文（一次一去）。
            try:
                for _m in _drain_new_interventions(conv["id"], _seen_msgs, run_id or ""):
                    _m_text = str(_m.get("prompt") or _m["content"])
                    _text = (f"（用户在对话中补充 / 干预：{_m_text}）\n"
                             "请据此调整接下来的执行；若与原目标冲突，"
                             "先说明取舍与影响。")
                    messages.append({
                        "role": "user",
                        "content": _build_message_content(
                            conv["id"], _text, _m.get("attachments"),
                            include_images=vision),
                    })
                    _ctx_add(_text)
                    yield {"intervention": {"id": _m["id"], "content": _m["content"]}}
            except Exception as _ie:
                logger.debug("对话干预读取跳过: %s", _ie)
            round_text: List[str] = []
            calls: List[Dict[str, Any]] = []
            _round_finish = ""
            _llm_t0 = time.time()
            async for ev in _iter_stream_with_keepalive(
                llm_mod.ZeroLlm(models).chat_stream_full(
                    "loop", messages, tools=specs,
                    temperature=0.4, max_tokens=_chat_max_tokens,
                )
            ):
                if ev.get("type") == "keepalive":
                    # 上游还没吐字：发心跳，让前端知道仍在等待且可被停止。
                    yield {"ping": True, "elapsed_s": ev.get("elapsed_s", 0),
                           "waiting": "llm", "phase": ev.get("phase") or "first_token",
                           "round": _round,
                           "role_id": rid, "role_name": rname}
                    continue
                if ev.get("type") == "delta":
                    piece = str(ev.get("content") or "")
                    if piece:
                        round_text.append(piece)
                        parts.append(piece)
                        # 正文**边收边记**（不是等 done 才补）：
                        # 模型可能"想两句→说一句→调工具→再说"，只在 done 时
                        # 补记会把正文统一推到该轮末尾，真实先后就丢了。
                        # 同一轮连续输出合并成一条，避免每个字一条刷屏；
                        # 但一旦中间插入了思考/工具，_open_text 会被清掉，
                        # 之后的话另起一条（时间点不同，不该并）。
                        if _open_text is None:
                            _open_text = _stamp({
                                "kind": "text", "round": _round, "text": ""})
                            trace_timeline.append(_open_text)
                            _seq += 1
                        _open_text["text"] += piece
                        yield {"delta": piece, "round": _round,
                               "role_id": rid, "role_name": rname}
                elif ev.get("type") == "thinking":
                    # 深度思考：与正文分流，前端折叠展示（与智能生成同款）。
                    # 同一轮的思考**合并成一条**时间线条目（流式追加），
                    # 免得几百个碎片把时间线刷屏。
                    piece = str(ev.get("content") or "")
                    if piece:
                        thinking.append(piece)
                        # 思考插进来，说明前面那段正文已经结束——封口，
                        # 之后再有正文就是新的一条（时间点不同）。
                        _open_text = None
                        if trace_timeline and trace_timeline[-1]["kind"] == "thinking" \
                                and trace_timeline[-1]["round"] == _round:
                            trace_timeline[-1]["text"] += piece
                        else:
                            trace_timeline.append(_stamp({
                                "kind": "thinking", "round": _round, "text": piece}))
                            _seq += 1
                        yield {"thinking": piece, "round": _round,
                               "role_id": rid, "role_name": rname}
                elif ev.get("type") == "retry":
                    # 网络抖动自动重试：把「正在重试」变成可见状态。
                    # 之前这条事件在对话层没有分支，被静默丢弃 —— 用户只看到
                    # 一片空白，以为卡死；后台任务也看不出是在恢复。
                    _kind = str(ev.get("kind") or "")
                    _label = {
                        "network": "网络中断", "timeout": "请求超时",
                        "rate_limit": "上游限流", "upstream": "上游服务异常",
                    }.get(_kind, "调用失败")
                    _attempt = int(ev.get("attempt") or 0)
                    _max = int(ev.get("max_attempts") or 0)
                    _resume = bool(ev.get("resume"))
                    yield {"retry": {
                        "kind": _kind, "label": _label,
                        "attempt": _attempt, "max_attempts": _max,
                        "resume": _resume,
                        "round": _round,
                        "message": (f"{_label}，正在自动重试"
                                    f"（{_attempt}/{_max}）"
                                    + ("，从断点续写" if _resume else "")),
                    }, "round": _round, "role_id": rid, "role_name": rname}
                elif ev.get("type") == "tool_preparing":
                    # 模型开始生成工具调用 JSON（参数可能很长，如 write_file
                    # 的全文）：先推一条「准备中」的 tool_call，UI 立刻出现
                    # 工具条目，不再静默等待——消除询问权限前的卡顿感。
                    _pname = str(ev.get("name") or "")
                    if _pname:
                        yield {"tool_call": {
                            "id": str(ev.get("id") or ""), "name": _pname,
                            "status": "preparing", "round": _round,
                            "role_id": rid, "role_name": rname}}
                elif ev.get("type") == "tool_progress":
                    # 上游仍在生成本次工具调用的参数 JSON（write_file 全文动辄
                    # 几十秒）。**过去这个分支不存在，事件被静默丢弃**：上游每 2s
                    # 产一条 tool_progress，`_iter_stream_with_keepalive` 因此认为
                    # 「有动静」而不发心跳，对话层又把它丢掉 —— 两边都不产出，整条
                    # run 就彻底静默（实测 2026-10-29 run-55f1fa8dd611 静默 110s，
                    # 直到用户手动停止）。这里把它转成标准 ping：
                    #   1. 前端活动条立刻变成「模型仍在工作」；
                    #   2. worker 拿到事件间隙，能及时看到 stop_requested / deadline。
                    yield {"ping": True, "waiting": "tool", "phase": "tool_args",
                           "name": str(ev.get("name") or ""), "round": _round,
                           "role_id": rid, "role_name": rname}
                elif ev.get("type") == "done":
                    calls = ev.get("tool_calls") or []
                    _round_finish = str(ev.get("finish_reason") or "")
                    _u = ev.get("usage") or {}
                    if isinstance(_u, dict):
                        _pt = _u.get("prompt_tokens") or _u.get("input_tokens")
                        _ct = _u.get("completion_tokens") or _u.get("output_tokens")
                        try:
                            if int(_pt or 0) > 0:
                                # 当前上下文规模 = 最近一轮真实 prompt（压缩决策与
                                # 顶部读数都以它为准；压缩后下一轮自然变小）。
                                _observed_prompt_tokens = int(_pt)
                                _usage_totals["last_prompt_tokens"] = int(_pt)
                                # 真实值到手：实时估算的锚点归位。
                                _ctx_anchor(int(_pt))
                            # 各轮 prompt 之和 = 这一轮对话真实的输入开销
                            # （多轮工具循环每轮都重新发送上下文，只取最大值会低估）。
                            _usage_totals["prompt_tokens"] += int(_pt or 0)
                            _usage_totals["max_context_tokens"] = max(
                                _usage_totals.get("max_context_tokens", 0), int(_pt or 0))
                        except (TypeError, ValueError):
                            pass
                        try:
                            _usage_totals["completion_tokens"] += int(_ct or 0)
                        except (TypeError, ValueError):
                            pass
                        # 缓存命中：llama.cpp / OpenAI 兼容端点在 prompt_tokens_details
                        # 里回 cached_tokens（有的端点直接放在 usage 顶层）——两者都收。
                        _details = _u.get("prompt_tokens_details") or _u.get("input_tokens_details") or {}
                        try:
                            _cached_tokens += int(_details.get("cached_tokens")
                                                if isinstance(_details, dict) else 0)
                            _prompt_sum += int(_pt or 0)
                        except (TypeError, ValueError):
                            pass
                    # 本轮正文收尾：不是"挪到末尾"，只是关闭当前这条记录，
                    # 它仍留在**首次产出时**插入的位置上。
                    _open_text = None
                    # 每次模型请求结束立刻推一次累计用量：不必等整个 run 结束，
                    # 输入 / 输出 / 缓存 / 速度在运行中就能看到。
                    yield {
                        "usage": _usage_snapshot(
                            _usage_totals, _cached_tokens, _prompt_sum,
                            _llm_ms_total + int((time.time() - _llm_t0) * 1000),
                            _round + 1),
                        # 当前上下文占用：真实 prompt（刚拿到）+ 本轮之前累计的估算增量。
                        "context_tokens": _ctx_tokens(),
                        "round": _round, "model": display_model,
                        "role_id": rid, "role_name": rname,
                    }
            _llm_ms_total += int((time.time() - _llm_t0) * 1000)
            if not calls:
                # 模型不再返回 tool_calls = 这一轮自然收尾。
                # 但如果连正文都没有（只思考 / 被输出上限截断），不要直接失败：
                # 扩大输出预算并请它继续，避免「模型未返回正文」把整轮判死。
                if not "".join(round_text).strip() and _empty_retries < 2:
                    _empty_retries += 1
                    _chat_max_tokens = max(8192, int(_chat_max_tokens or 0) * 2)
                    _why = ("输出达到上限被截断" if _round_finish == "length"
                            else "没有返回正文")
                    logger.warning(
                        "对话空输出，扩大预算续跑（第 %d 次，max_tokens=%s，reason=%s，conv=%s）",
                        _empty_retries, _chat_max_tokens, _round_finish or "stop",
                        conv.get("id"))
                    yield {"retry": {
                        "kind": "truncated",
                        "label": "输出被截断",
                        "attempt": _empty_retries,
                        "max_attempts": 2,
                        "resume": False,
                        "round": _round,
                        "message": f"模型{_why}，正在扩大输出预算继续…",
                    }, "round": _round, "role_id": rid, "role_name": rname}
                    _nudge = ("（系统）上一条回复没有返回正文，也没有工具调用。"
                              "请继续完成刚才的任务；需要工具时直接调用可用工具。")
                    messages.append({"role": "user", "content": _nudge})
                    _ctx_add(_nudge)
                    continue
                break
            # 记录「已经真正开工」：整轮收尾时会用它区分「只写了清单」与「真干过活」。
            _any_work_tool_called = _any_work_tool_called or any(
                (c.get("name") or "") in ("todo_write", "todo")
                for c in calls)
            # 回填 assistant(tool_calls) + 工具结果，进入下一轮
            messages.append({
                "role": "assistant",
                "content": "".join(round_text) or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"],
                                  "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                    for c in calls
                ],
            })
            # 这一轮的正文 / 工具调用参数会原样回到下一次 prompt 里：计入上下文增量。
            _ctx_add("".join(round_text))
            _ctx_add(json.dumps([c.get("arguments") or {} for c in calls], ensure_ascii=False))
            # ── 工具包解析：模型可能调「包 + op」，执行/权限/事件统一用具体工具名；
            #    回填给模型的 assistant(tool_calls) 仍用它自己的原始名字。
            from services.agent_zero.tools import packs as _packs

            _resolved = [
                _packs.resolve_call(c.get("name") or "", c.get("arguments") or {})
                for c in calls
            ]
            # ── PreToolUse 钩子（对齐 Claude Code）────────────────────────
            # 必须在权限判定与并行预执行之前：钩子可以拒绝调用、替换参数，
            # 也可以要求人工确认（permissionDecision=ask）。
            _hook_pre: List[Dict[str, Any]] = [{} for _ in calls]
            try:
                from services.agent_zero.services import hooks_service as _hsvc

                if _hsvc.has_hooks(ws_id):
                    for _hi, _c in enumerate(calls):
                        _hn, _ha, _hpe = _resolved[_hi]
                        if _hpe:
                            continue
                        _hp = await _hsvc.pre_tool_use(
                            ws_id, _hn, _ha, run_id=run_id or "",
                            session_id=str(conv.get("id") or ""),
                            tool_pack=(str(_c.get("name") or "")
                                       if str(_c.get("name") or "") != _hn
                                       and _packs.is_pack(str(_c.get("name") or ""))
                                       else ""))
                        _hook_pre[_hi] = _hp
                        for _hr in _hp.get("results") or []:
                            _notable = (bool(_hr.get("output")) or bool(_hr.get("stderr"))
                                        or bool(_hr.get("decision"))
                                        or not _hr.get("ok"))
                            if _notable:
                                yield {"hook": {
                                    "phase": "pre", "event": "PreToolUse",
                                    "tool": _hn, "command": _hr.get("command"),
                                    "decision": _hr.get("decision"),
                                    "reason": _hr.get("reason"),
                                    "output": _hr.get("output"),
                                    "stderr": _hr.get("stderr"),
                                    "ok": _hr.get("ok"),
                                    "timed_out": _hr.get("timed_out"),
                                    "duration_ms": _hr.get("duration_ms"),
                                    "role_id": rid, "role_name": rname,
                                }}
                        if _hp.get("decision") == "deny":
                            logger.info("PreToolUse 钩子拒绝 %s: %s", _hn,
                                        _hp.get("reason"))
                        elif _hp.get("tool_input") not in (None, _ha):
                            _resolved[_hi] = (_hn, dict(_hp["tool_input"]), _hpe)
            except Exception as _he:
                logger.warning("PreToolUse 钩子执行失败（已忽略）: %s", _he)
            # ── 工具并行：一轮里若**全是通用工具**（无计划/建议/子代理等顺序类调用），
            #    先并发执行底层工具（联网 / 网页 / shell 等慢工具收益最大）；下面的事件
            #    回放仍按**原顺序**，前端顺序与计划步骤归属都不受影响。含顺序类调用时
            #    走原串行路径（行为完全不变）。
            # 权限判定先做：需要用户批准的调用**不能**预执行——危险操作必须在
            # 批准之后才开始，所以这一轮退回串行路径。
            from services.agent_zero.task_layer import permissions

            _decisions = [permissions.decide(
                              conv["id"], _resolved[i][0], _resolved[i][1],
                              auto_execute,
                              role_id=str(role.get("id") or ""),
                              team_id=str(conv.get("team_id") or ""))
                          for i, c in enumerate(calls)]
            # 钩子要求确认：覆盖规则判定（但钩子已拒绝的调用下面不会再执行）
            for _hi, _hp in enumerate(_hook_pre):
                if _hp.get("decision") == "ask" and not _decisions[_hi].get("ask"):
                    _decisions[_hi] = {
                        "ask": True, "risk": "medium",
                        "reasons": [str(_hp.get("reason")
                                        or "PreToolUse 钩子要求人工确认")[:300]],
                    }
            _pre_results: Optional[List[Any]] = None
            _pre_started: Optional[float] = None
            if len(calls) >= 2 and all(
                    _resolved[i][0] not in _SEQ_CALLS
                    and not _decisions[i].get("ask")
                    and not _resolved[i][2]
                    for i, c in enumerate(calls)):
                _jobs = [asyncio.ensure_future(_exec_chat_tool(
                    loop, task_ctx, config, node, caps, conv, user, role,
                    _resolved[i][0], _resolved[i][1])) for i, c in enumerate(calls)]
                _pre_started = time.time()
                _pending = set(_jobs)
                # 并行路径同样受前台预算约束：过去这里只有心跳、没有预算，
                # 一批慢工具能把整轮 run 无限期挂住（与串行路径同一个病灶）。
                _pre_budget = 0.0
                try:
                    _pre_budget = float(loop.foreground_budget())
                except Exception:
                    _pre_budget = 0.0
                while _pending:
                    if TOOL_MAX_SECONDS > 0:
                        _budget = TOOL_MAX_SECONDS - (time.time() - _pre_started)
                        if _budget <= 0:
                            break
                        _wait = min(TOOL_KEEPALIVE_SECONDS, _budget)
                    else:
                        _wait = TOOL_KEEPALIVE_SECONDS
                    if _pre_budget > 0:
                        _left = _pre_budget - (time.time() - _pre_started)
                        if _left <= 0:
                            break
                        _wait = min(_wait, max(0.2, _left))
                    _done, _pending = await asyncio.wait(
                        _pending, timeout=_wait)
                    if _pending:
                        yield {"ping": True,
                               "elapsed_s": int(time.time() - _pre_started),
                               "name": "并行工具", "role_id": rid, "role_name": rname}
                # 预算用尽仍在跑的：转后台托管，结果位置留 background 回执。
                _pre_jobs: Dict[int, Dict[str, Any]] = {}
                if _pending:
                    from services.agent_zero.task_layer import tool_jobs as _tjobs

                    for _i, _t in enumerate(_jobs):
                        if _t.done():
                            continue
                        _n, _a, _ = _resolved[_i]
                        _pj = _tjobs.adopt(
                            conversation_id=str(conv.get("id") or ""),
                            run_id=run_id or "", name=_n, args=_a,
                            task=_t, call_id=(calls[_i].get("id") or ""),
                            started_at=_pre_started)
                        _pre_jobs[_i] = _pj
                        logger.info("并行工具超过前台预算，转后台: %s job=%s conv=%s",
                                    _n, _pj["id"], conv.get("id"))
                _pre_results = []
                for _i, _t in enumerate(_jobs):
                    if _i in _pre_jobs:
                        _pj = _pre_jobs[_i]
                        _pre_results.append(json.dumps({
                            "status": "background", "job_id": _pj["id"],
                            "tool": _pj["name"],
                            "elapsed_s": int(time.time() - _pre_started),
                            "note": ("超过前台预算已转后台；用 "
                                     f"tool_job(action='result', job_id='{_pj['id']}') 查看，"
                                     "或 action='kill' 终止。不要重跑同一条命令。"),
                        }, ensure_ascii=False))
                        continue
                    if not _t.done():
                        _t.cancel()
                        logger.warning("对话工具并行超时中止: conv=%s", conv["id"])
                        _pre_results.append(json.dumps(
                            {"error": f"工具执行超过 {int(TOOL_MAX_SECONDS)}s 未返回，已中止"},
                            ensure_ascii=False))
                    elif _t.cancelled():
                        _pre_results.append(json.dumps({"error": "工具已取消"},
                                                       ensure_ascii=False))
                    else:
                        _exc = _t.exception()
                        if _exc is not None:
                            logger.warning("对话工具并行执行抛错: %s", _exc)
                            _pre_results.append(json.dumps(
                                {"error": f"工具执行异常: {type(_exc).__name__}"},
                                ensure_ascii=False))
                        else:
                            _pre_results.append(_t.result())
            for _ci, c in enumerate(calls):
                name, args, pack_error = _resolved[_ci]
                _canon_name = (tool_reg_canonical(name) if name else "")
                raw_name = c.get("name") or ""
                pack_label = raw_name if _packs.is_pack(raw_name) else ""
                # 只认该包已知的动作名：模型有时把整条命令塞进 op（现场见过
                # `code` + `op="cd ... && python3 - <<'PY' ..."`），照抄会把
                # 整条命令渲染成时间线徽标，把对话区顶出容器。
                pack_op = _packs.pack_op_label(raw_name, c.get("arguments") or {})
                call_id = c.get("id") or ""
                shown_args = args if _args_previewable(args) else _args_skeleton(args)
                # 本条调用是否被转后台托管。必须在分支之前初始化：不少工具
                # （todo_write / goal_report / 子代理等）
                # 走的是独立分支，不经过下面的「转后台」逻辑；若只在 else 分支里
                # 赋值，收尾处读 `_adopted` 会抛 UnboundLocalError 直接中断整轮。
                _adopted = False
                _job: Dict[str, Any] = {}
                # **先发"开始"再执行**：工具可能跑很久（联网/派生子 agent），
                # 只在执行完才推事件的话，用户会盯着空白等，然后凭空蹦出一条——
                # 那正是「工具为什么结束后才显示」的成因。开始事件让 UI 立刻
                # 出现"正在调用…"，跑完再补耗时与结果。
                # `round` / `seq` 让前端能按**真实发生顺序**渲染：
                # 一轮里先思考、再调工具、再思考……而不是把思考堆一处、工具堆另一处。
                # 注意：这里**不**占用 seq。运行时事件只是给前端一个"立刻出现"的
                # 占位，最终以 tool_result 的 trace 条目为准；两处都取号会让
                # seq 出现空洞（前端按 seq 排序时看着像丢步）。
                yield {"tool_call": {
                    "id": call_id, "name": name,
                    "pack": pack_label, "op": pack_op,
                    "args": shown_args,
                    "status": "running", "round": _round,
                    "role_id": rid, "role_name": rname,
                }}
                tool_started = _pre_started if _pre_started is not None else time.time()
                # 登记到「进程」面板：前台等待中的 shell / 子代理也要可见。
                # 只是可观测数据，失败绝不能影响本轮执行。
                _tact = None
                _act_id = ""
                try:
                    from services.agent_zero.task_layer import tool_activity as _tact

                    _act_id = _tact.begin(
                        conversation_id=str(conv.get("id") or ""),
                        run_id=run_id or "", name=name, args=shown_args,
                        call_id=call_id, started_at=tool_started)
                except Exception:
                    logger.debug("登记前台工具活动失败（忽略）", exc_info=True)
                    _tact = None
                    _act_id = ""
                _hp = _hook_pre[_ci] if _ci < len(_hook_pre) else {}
                if _hp.get("decision") == "deny":
                    # 钩子拒绝：不执行、不询问，把原因回给模型（它下一轮可换做法）
                    _reason = str(_hp.get("reason") or "被 PreToolUse 钩子拒绝")
                    result = json.dumps(
                        {"error": f"PreToolUse 钩子拒绝执行 {name}：{_reason}"},
                        ensure_ascii=False)
                elif pack_error:
                    # 工具包调用参数不完整：把可读的错误回给模型，它下一轮能自我修正
                    result = json.dumps({"error": pack_error}, ensure_ascii=False)
                elif name == "propose_task":
                    # LLM 提出转任务建议（待用户点击确认后才真正创建任务）
                    goal = str(args.get("goal") or "").strip()
                    reason = str(args.get("reason") or "").strip()
                    if not goal:
                        result = json.dumps({"error": "goal 为空"}, ensure_ascii=False)
                    else:
                        node_id = _persist_proposal(conv, role, goal, reason)
                        result = json.dumps(
                            {"status": "proposed", "proposal_id": node_id,
                             "note": "已提出转任务建议，等待用户在界面确认后再执行"},
                            ensure_ascii=False)
                        # 建议**作为本次回复的一个步骤**进时间线（不是独立消息）：
                        # 它本来就是这一轮里的一个动作，单独落一条消息会被排成
                        # "另一个回合"，看起来跑到正文前面去了（实测踩到）。
                        # 作为 step 则天然跟在它发生的时刻，且与思考/工具同构。
                        _open_text = None
                        trace_timeline.append(_stamp({
                            "kind": "proposal", "round": _round,
                            "status": "pending", "proposal_id": node_id,
                            "goal": goal, "reason": reason}))
                        _seq += 1
                        yield {"task_proposal": {"id": node_id, "goal": goal,
                                                 "reason": reason,
                                                 "role_id": rid, "role_name": rname}}
                elif name in ("call_subagent", "spawn_subagent"):
                    # 自身子代理：派生父角色的收窄副本，临时执行子目标。
                    _open_text = None
                    trace_timeline.append(_stamp({
                        "kind": "tool", "phase": "start", "round": _round,
                        "id": call_id, "name": name,
                        "pack": pack_label, "op": pack_op,
                        "args": shown_args}))
                    _seq += 1
                    yield {"tool_call": {
                        "id": call_id, "name": name, "args": shown_args,
                        "status": "running", "round": _round,
                        "role_id": rid, "role_name": rname,
                    }}
                    _sub_task = asyncio.ensure_future(
                        _exec_call_subagent(conv, user, role, args, _depth, call_id=call_id))
                    _bind_activity(_tact, _act_id, _sub_task)
                    async for _ping in _ping_until_done(
                            _sub_task, run_id, label=name, rid=rid, rname=rname):
                        yield _ping
                    result = await _await_subagent_result(_sub_task, name, run_id)
                elif name in ("call_agent", "call_role", "spawn_agent"):
                    # 子代理：受限子循环跑工作区内的另一个角色，结论作为工具结果回灌；
                    # 先推「开始」事件让界面立刻显示「正在委派…」，完成后给 handoff 卡片。
                    _open_text = None
                    trace_timeline.append(_stamp({
                        "kind": "tool", "phase": "start", "round": _round,
                        "id": call_id, "name": name, "args": shown_args}))
                    _seq += 1
                    yield {"tool_call": {
                        "id": call_id, "name": name, "args": shown_args,
                        "status": "running", "round": _round,
                        "role_id": rid, "role_name": rname,
                    }}
                    _sub_task = asyncio.ensure_future(
                        _exec_call_agent(conv, user, role, args, _depth, call_id=call_id))
                    _bind_activity(_tact, _act_id, _sub_task)
                    async for _ping in _ping_until_done(
                            _sub_task, run_id, label=name, rid=rid, rname=rname):
                        yield _ping
                    result = await _await_subagent_result(_sub_task, name, run_id)
                elif name == "call_team":
                    # 团队成员委派：受限子循环跑成员角色（persona/专长在系统名单里），
                    # 流程图把该步归属到委派成员（worker=team）。
                    _open_text = None
                    trace_timeline.append(_stamp({
                        "kind": "tool", "phase": "start", "round": _round,
                        "id": call_id, "name": name, "args": shown_args}))
                    _seq += 1
                    yield {"tool_call": {
                        "id": call_id, "name": name, "args": shown_args,
                        "status": "running", "round": _round,
                        "role_id": rid, "role_name": rname,
                    }}
                    _member = str(args.get("member_name") or args.get("member_id")
                                  or "团队成员").strip()[:64]
                    _sub_task = asyncio.ensure_future(
                        _exec_call_team(conv, user, role, args, _depth,
                                        call_id=call_id))
                    _bind_activity(_tact, _act_id, _sub_task)
                    async for _ping in _ping_until_done(
                            _sub_task, run_id, label=name, rid=rid, rname=rname,
                            waiting="team"):
                        yield _ping
                    result = await _await_subagent_result(_sub_task, name, run_id)
                    try:
                        _team_out = json.loads(result)
                        if isinstance(_team_out, dict) \
                                and _team_out.get("status") == "dispatched":
                            _team_delegated = True
                    except Exception:
                        logger.debug("解析团队派发结果失败（忽略）", exc_info=True)
                elif name == "background_task":
                    # 单角色会话：把自己的一段长工作放到后台任务（对话与任务分离）。
                    _open_text = None
                    trace_timeline.append(_stamp({
                        "kind": "tool", "phase": "start", "round": _round,
                        "id": call_id, "name": name, "args": shown_args}))
                    _seq += 1
                    yield {"tool_call": {
                        "id": call_id, "name": name, "args": shown_args,
                        "status": "running", "round": _round,
                        "role_id": rid, "role_name": rname,
                    }}
                    _items = _background_task_items(args or {})
                    if not _items:
                        result = json.dumps({"error": "goal 不能为空"},
                                            ensure_ascii=False)
                    else:
                        try:
                            from services.agent_zero.services import (
                                team_task_service as _tts)

                            _created: List[Dict[str, Any]] = []
                            for _item in _items:
                                _task = _tts.create_task(
                                    conv, team or {}, role,
                                    [{
                                        "role_id": str(role.get("id") or ""),
                                        "role_name": str(role.get("name") or ""),
                                        "goal": _item["goal"],
                                        "context": _item["context"],
                                        "role": role,
                                    }],
                                    user,
                                    title=_item["title"] or None,
                                    mention=False)
                                _created.append({
                                    "task_id": str(_task.get("id") or ""),
                                    "title": str(_task.get("title")
                                                 or _item["title"] or ""),
                                    "goal": _item["goal"],
                                    "run_id": str(((_task.get("members") or [{}])[0])
                                                  .get("run_id") or ""),
                                })
                            _team_delegated = True
                            _parallel = len(_created) > 1
                            result = json.dumps({
                                "status": "dispatched",
                                "task_id": _created[0]["task_id"],
                                "tasks": _created,
                                "count": len(_created),
                                "note": (
                                    f"已并行创建 {len(_created)} 条后台任务；本轮用一两句话"
                                    "说明分工即可收尾，不要自己重复执行。"
                                    if _parallel else
                                    "已创建后台任务；本轮用一两句话说明即可收尾，"
                                    "不要自己重复执行。完成后会有触发器回到对话。"),
                            }, ensure_ascii=False)
                        except Exception as _te:
                            logger.exception("创建后台任务失败")
                            result = json.dumps(
                                {"error": f"创建后台任务失败: {_te}"},
                                ensure_ascii=False)
                elif name == "role_discussion":
                    # 2-3 个角色直接多轮讨论（不经过主持）；每个角色的流事件
                    # 以子代理轨迹回放，最终记录回灌给调用者。
                    _open_text = None
                    trace_timeline.append(_stamp({
                        "kind": "tool", "phase": "start", "round": _round,
                        "id": call_id, "name": name, "args": shown_args}))
                    _seq += 1
                    yield {"tool_call": {
                        "id": call_id, "name": name, "args": shown_args,
                        "status": "running", "round": _round,
                        "role_id": rid, "role_name": rname,
                    }}
                    _disc_task = asyncio.ensure_future(
                        _exec_role_discussion(conv, user, role, args, _depth,
                                              call_id=call_id))
                    _bind_activity(_tact, _act_id, _disc_task)
                    async for _ping in _ping_until_done(
                            _disc_task, run_id, label=name, rid=rid, rname=rname,
                            waiting="discussion"):
                        yield _ping
                    result = await _await_subagent_result(_disc_task, name, run_id)
                elif name in ("department_manage", "department_admin", "org_manage"):
                    _open_text = None
                    _org_out = _exec_department_manage(args)
                    result = json.dumps(_org_out, ensure_ascii=False)
                    if not _org_out.get("error"):
                        yield {"org_update": _org_out}
                elif name in ("role_manage", "role_admin"):
                    _open_text = None
                    _role_out = _exec_role_manage(conv, args)
                    result = json.dumps(_role_out, ensure_ascii=False)
                    if not _role_out.get("error"):
                        yield {"role_update": _role_out}
                elif name == "proactive_send":
                    _open_text = None
                    _pro_out = _exec_proactive_send(conv, role, args)
                    result = json.dumps(_pro_out, ensure_ascii=False)
                    if not _pro_out.get("error"):
                        yield {"schedule_update": _pro_out}
                elif name in ("schedule_manage", "schedule_admin"):
                    _open_text = None
                    _sch_out = await _exec_schedule_manage(args)
                    result = json.dumps(_sch_out, ensure_ascii=False)
                    if not _sch_out.get("error"):
                        yield {"schedule_update": _sch_out}
                elif name == "app_manage":
                    _open_text = None
                    _app_out = _exec_app_manage(conv, args)
                    result = json.dumps(_app_out, ensure_ascii=False)
                    if not _app_out.get("error"):
                        yield {"app_update": _app_out}
                elif name == "team_manage":
                    # 团队完全动态：模型自己创建/绑定，绑定后 call_team 立即可用。
                    # 但创建团队必须**强制人工确认**（不受权限档位影响，见 permissions
                    # FORCED_CONFIRM_ACTIONS）：批准后才执行。
                    if _decisions[_ci].get("ask"):
                        _note = "".join(parts).strip()[-900:]
                        _appr = permissions.create_approval(
                            conv["id"], run_id, name, shown_args,
                            _decisions[_ci]["risk"], _decisions[_ci]["reasons"], _note,
                            role_id=str(role.get("id") or ""),
                            team_id=str(conv.get("team_id") or ""))
                        trace_timeline.append(_stamp({
                            "kind": "approval", "round": _round, "id": _appr["id"],
                            "name": name, "risk": _appr["risk"],
                            "reasons": _appr["reasons"], "note": _appr["note"],
                            "preview": None, "status": "pending"}))
                        _seq += 1
                        yield {"tool_approval": {
                            **_appr, "round": _round,
                            "role_id": rid, "role_name": rname,
                            "team_id": str(conv.get("team_id") or ""),
                            "team_name": str((team or {}).get("name") or "")}}
                        _tm_holder: Dict[str, Any] = {}
                        async for _ev in _await_tool_approval(
                                _appr["id"], run_id, name, rid, rname, _tm_holder):
                            yield _ev
                        _tm_out = _tm_holder
                        for _e in reversed(trace_timeline):
                            if _e.get("kind") == "approval" and _e.get("id") == _appr["id"]:
                                _e["status"] = _tm_out.get("status", "denied")
                                break
                        if _tm_out.get("status") and _tm_out.get("status") != "approved":
                            reason = {"timeout": "（确认超时）", "stopped": "（运行已停止）"}.get(
                                _tm_out.get("reason", ""), "")
                            result = json.dumps(
                                {"error": f"用户未批准创建 Agent 团队{reason}",
                                 "approval_id": _appr["id"]}, ensure_ascii=False)
                        else:
                            result = _exec_team_manage(conv, ws_id, args, specs)
                    else:
                        result = _exec_team_manage(conv, ws_id, args, specs)
                elif name in ("session_manage", "session_command"):
                    # 会话指令：清空上下文在后台立即执行；界面动作（新建 / 重命名 /
                    # 主题…）通过事件交给前端，复用斜杠命令的同一套处理逻辑。
                    _open_text = None
                    _sess_out = ({"error": "会话指令只能由主对话执行，子代理 / "
                                           "后台任务成员不能调用"}
                                 if (isolated or _is_task_member)
                                 else _exec_session_manage(conv, args, user=user))
                    result = json.dumps(_sess_out, ensure_ascii=False)
                    if _sess_out.get("ok"):
                        yield {"session_command": {
                            "name": _sess_out.get("action"),
                            "arg": _sess_out.get("arg") or "",
                            "done": bool(_sess_out.get("done")),
                            "note": _sess_out.get("note") or "",
                            "role_id": rid, "role_name": rname,
                        }}
                elif name in ("todo_write", "todo"):
                    # 任务清单：整份覆盖；用户与模型改的是同一份（界面对话内卡片）
                    from services.agent_zero.services import todo_service as _tdsvc

                    _open_text = None
                    out = _tdsvc.handle_tool(conv["id"], args)
                    result = json.dumps(out, ensure_ascii=False)
                    if "items" in out:
                        yield {"todo_update": {"items": out["items"],
                                               "counts": out.get("counts"),
                                               "role_id": rid, "role_name": rname}}
                elif name == "goal_report":
                    # 目标模式：记录进展 / 宣布完成 / 标记阻塞
                    from services.agent_zero.task_layer import goal_service as _gsvc

                    _open_text = None
                    out = _gsvc.report_progress(
                        conv["id"], str(args.get("status") or ""),
                        str(args.get("note") or ""),
                        progress_pct=args.get("progress_pct"),
                        next_steps=args.get("next_steps"),
                        blockers=args.get("blockers"),
                        evidence=args.get("evidence"))
                    result = json.dumps(out, ensure_ascii=False)
                    if not out.get("error"):
                        yield {"goal_update": {**out, "role_id": rid,
                                               "role_name": rname}}
                elif name == "goal_manage":
                    # 目标的查看 / 调整 / 暂停 / 恢复 / 结束
                    from services.agent_zero.task_layer import goal_service as _gsvc

                    _open_text = None
                    out = _gsvc.manage(conv["id"], args)
                    result = json.dumps(out, ensure_ascii=False)
                    _goal = out.get("goal") if isinstance(out, dict) else None
                    if _goal and not out.get("error"):
                        yield {"goal_update": {**_goal, "role_id": rid,
                                               "role_name": rname}}
                else:
                    # 时间线：工具调用的**开始**阶段（独立步骤，带参数）。
                    # 与「结束」阶段各占一条，前端渲染成「开始 · X」「结束 · X」。
                    _open_text = None
                    trace_timeline.append(_stamp({
                        "kind": "tool", "phase": "start", "round": _round,
                        "id": call_id, "name": name,
                        "pack": pack_label, "op": pack_op,
                        "args": shown_args}))
                    _seq += 1
                    # 工具在本轮内执行，主协程周期性 yield 心跳。
                    # 没有心跳的话，慢工具会让 SSE 静默超过前端 60s 空闲超时，
                    # 请求被前端 abort —— 表现为"执行到一半停住"，服务端却还在跑。
                    # ── 权限：危险操作先请求用户批准，批准后才执行 ───────────
                    # 规则决定「要不要问」（permissions.decide），模型此刻的正文
                    # 作为「它打算干什么」的说明一起送给用户——规则拦，模型解释。
                    _outcome: Dict[str, Any] = {}
                    if _decisions[_ci].get("ask"):
                        _note = "".join(parts).strip()[-900:]
                        # opencode 式预览：写/改文件类工具在批准前给出将要发生的
                        # diff，用户看「改什么」而不是猜。
                        _preview = permissions.approval_preview(ws_id, name, args)
                        _appr = permissions.create_approval(
                            conv["id"], run_id, name, shown_args,
                            _decisions[_ci]["risk"], _decisions[_ci]["reasons"], _note,
                            preview=_preview,
                            role_id=str(role.get("id") or ""),
                            team_id=str(conv.get("team_id") or ""))
                        trace_timeline.append(_stamp({
                            "kind": "approval", "round": _round, "id": _appr["id"],
                            "name": name, "risk": _appr["risk"],
                            "reasons": _appr["reasons"], "note": _appr["note"],
                            "preview": _preview, "status": "pending"}))
                        _seq += 1
                        yield {"tool_approval": {
                            **_appr, "round": _round,
                            "role_id": rid, "role_name": rname,
                            "team_id": str(conv.get("team_id") or ""),
                            "team_name": str((team or {}).get("name") or "")}}
                        # 等待用户决定期间也要心跳：主协程若整段阻塞，
                        # SSE 长时间静默会被中间层/前端掐断（表现为卡顿）。
                        async for _ev in _await_tool_approval(
                                _appr["id"], run_id, name, rid, rname, _outcome):
                            yield _ev
                        for _e in reversed(trace_timeline):
                            if _e.get("kind") == "approval" and _e.get("id") == _appr["id"]:
                                _e["status"] = _outcome.get("status", "denied")
                                break

                    if _outcome.get("status") and _outcome.get("status") != "approved":
                        reason = {"timeout": "（确认超时）", "stopped": "（运行已停止）"}.get(
                            _outcome.get("reason", ""), "")
                        tool_task = asyncio.ensure_future(_completed(json.dumps(
                            {"error": f"用户未批准该操作{reason}",
                             "approval_id": _appr["id"]}, ensure_ascii=False)))
                    else:
                        if _pre_results is not None:
                            # 已在前面并发跑完，包成已完成协程，心跳逻辑照常收结果。
                            tool_task = asyncio.ensure_future(_completed(_pre_results[_ci]))
                        else:
                            tool_task = asyncio.ensure_future(_exec_chat_tool(
                                loop, task_ctx, config, node, caps, conv, user, role,
                                name, args))
                    _bind_activity(_tact, _act_id, tool_task)
                    waited = 0.0
                    timed_out = False
                    # ── 前台预算：**不再无条件等工具结束** ────────────────────
                    # 过去这里 `while not tool_task.done()` 一直等：一条挂住的
                    # 长命令能把整轮 run 静止到用户手动停止（2026-10-29 现场：
                    # `bash build.sh | tail -25` 挂了 40 分钟）。现在只等
                    # `_fg_budget` 秒，仍未结束就**转后台托管**，立刻把
                    # {"status":"background","job_id":...} 交给模型——模型继续
                    # 干别的，需要结果时用 tool_job 主动监督。
                    _fg_budget = 0.0
                    # 等用户 / 监督类工具不设前台预算：它们的「久等」是语义本身。
                    if _canon_name not in NO_ADOPT_TOOLS:
                        try:
                            _fg_budget = float(loop.foreground_budget())
                        except Exception:
                            _fg_budget = 0.0
                    # 用 wait_for(shield(task)) 而不是 sleep：工具提前结束会**立即**
                    # 唤醒，不必干等到下一个心跳点。否则一个 1s 就完成的工具也要
                    # 被硬等一个心跳周期才推结果——那是纯粹的卡顿。
                    # 外层收到取消时连带取消 tool_task（见 _await_uncancelled）：
                    # 否则强制停止只停住对话循环，工具还在后台继续干活。
                    while not tool_task.done():
                        if TOOL_MAX_SECONDS > 0:
                            budget = TOOL_MAX_SECONDS - waited
                            if budget <= 0:
                                timed_out = True
                                break
                            step = min(TOOL_KEEPALIVE_SECONDS, budget)
                        else:
                            step = TOOL_KEEPALIVE_SECONDS
                        if _fg_budget > 0:
                            step = min(step, max(0.2, _fg_budget - waited))
                        if await _await_uncancelled(tool_task, step):
                            break
                        waited += step
                        if _fg_budget > 0 and waited >= _fg_budget:
                            # 预算用尽仍未结束：转后台，交回决策权给模型。
                            from services.agent_zero.task_layer import tool_jobs

                            _job = tool_jobs.adopt(
                                conversation_id=str(conv.get("id") or ""),
                                run_id=run_id or "", name=name, args=shown_args,
                                task=tool_task, call_id=call_id,
                                started_at=tool_started)
                            _adopted = True
                            result = json.dumps({
                                "status": "background",
                                "job_id": _job["id"],
                                "tool": name,
                                "elapsed_s": int(waited),
                                "note": ("这个工具超过 " f"{int(_fg_budget)}s 还没结束，已转到后台继续跑。"
                                         "**不要重跑同一条命令、也不要干等**：先做别的"
                                         "（读文件、分析、别的工具），需要结果时调用 "
                                         f"tool_job(action='result', job_id='{_job['id']}')；"
                                         "如果判断它已经挂死，用 "
                                         f"tool_job(action='kill', job_id='{_job['id']}') 终止。"
                                         "它跑完后系统会自动推一条结果事件。"),
                            }, ensure_ascii=False)
                            logger.info("工具超过前台预算 %ss，转后台: %s job=%s conv=%s",
                                        int(_fg_budget), name, _job["id"], conv.get("id"))
                            break
                        # 心跳：压住 nginx 等中间层的读超时（未知事件被忽略）
                        yield {"ping": True, "elapsed_s": int(waited),
                               "name": name, "role_id": rid, "role_name": rname}
                    if _adopted:
                        pass                      # result 已设为 background 回执
                    elif timed_out:
                        tool_task.cancel()
                        logger.warning("对话工具 %s 超时中止: conv=%s", name, conv["id"])
                        result = json.dumps(
                            {"error": f"工具执行超过 {int(TOOL_MAX_SECONDS)}s 未返回，已中止"},
                            ensure_ascii=False)
                    elif tool_task.cancelled():
                        result = json.dumps({"error": "工具已取消"}, ensure_ascii=False)
                    else:
                        exc = tool_task.exception()
                        if exc is not None:
                            # 工具自己抛错：如实报失败，不要中断整轮对话
                            logger.warning("对话工具 %s 抛错: %s", name, exc)
                            result = json.dumps(
                                {"error": f"工具执行异常: {type(exc).__name__}"},
                                ensure_ascii=False)
                        else:
                            result = tool_task.result()
                if _act_id and _tact is not None:
                    try:
                        _tact.end(_act_id)
                    except Exception:
                        logger.debug("注销前台工具活动失败（忽略）", exc_info=True)
                if not isinstance(result, str):
                    result = json.dumps(result, ensure_ascii=False)
                # ── PostToolUse 钩子：可把额外上下文 / 反馈追加给模型（不改原始结果）──
                _run_post = not _adopted and _hp.get("decision") != "deny"
                if _run_post:
                    try:
                        _rj0 = json.loads(result)
                        if isinstance(_rj0, dict) and _rj0.get("status") == "background":
                            _run_post = False
                    except Exception:
                        pass
                if _run_post:
                    try:
                        from services.agent_zero.services import hooks_service as _hsvc

                        _post = await _hsvc.post_tool_use(
                            ws_id, name, args, result, not _result_is_error(result),
                            run_id=run_id or "",
                            session_id=str(conv.get("id") or ""),
                            tool_pack=pack_label)
                        for _hr in _post.get("results") or []:
                            _notable = (bool(_hr.get("output")) or bool(_hr.get("stderr"))
                                        or bool(_hr.get("decision"))
                                        or not _hr.get("ok"))
                            if _notable:
                                yield {"hook": {
                                    "phase": "post", "event": "PostToolUse",
                                    "tool": name, "command": _hr.get("command"),
                                    "decision": _hr.get("decision"),
                                    "reason": _hr.get("reason"),
                                    "output": _hr.get("output"),
                                    "stderr": _hr.get("stderr"),
                                    "ok": _hr.get("ok"),
                                    "timed_out": _hr.get("timed_out"),
                                    "duration_ms": _hr.get("duration_ms"),
                                    "role_id": rid, "role_name": rname,
                                }}
                        _notes: List[str] = []
                        if _post.get("context"):
                            _notes.append(str(_post["context"]))
                        if _post.get("decision") == "deny":
                            _notes.append("钩子要求阻止继续："
                                          + str(_post.get("reason") or "（无说明）"))
                        if _notes:
                            result = (result + "\n\n[PostToolUse 钩子反馈]\n"
                                      + "\n\n".join(_notes))
                    except Exception as _pe:
                        logger.warning("PostToolUse 钩子执行失败（已忽略）: %s", _pe)
                # 工具调用「情况分析」：耗时 + 成功与否 + 结果摘要，供前端可观测。
                # 用户此前完全看不到模型在背后做了什么，这里把关键信号送上去。
                tool_ms = int((time.time() - tool_started) * 1000)
                _tool_ms_total += tool_ms
                trace = {
                    "id": call_id,
                    "name": name,
                    "pack": pack_label,
                    "op": pack_op,
                    "args": shown_args,
                    "duration_ms": tool_ms,
                    "ok": not _result_is_error(result),
                    "summary": _result_summary(result),
                }
                # 转后台的工具（串行超预算 / 并行超预算）都不是 done：标 background，
                # 前端保持「进行中」语义（后台 job 跑完会补一条 tool_result 就地更新）。
                _bg_id = str(_job.get("id") or "") if _adopted else ""
                if not _bg_id:
                    try:
                        _rj = json.loads(result)
                        if isinstance(_rj, dict) and _rj.get("status") == "background":
                            _bg_id = str(_rj.get("job_id") or "")
                    except Exception:
                        _bg_id = ""
                if _bg_id:
                    trace["status"] = "background"
                    trace["job_id"] = _bg_id
                # 文件类工具带结构化展示载荷（随 trace 落库，刷新后仍可渲染文件视图）
                _disp, _disp_used = _fit_display(_result_display(result),
                                                 _display_used)
                _display_used = _disp_used
                if _disp:
                    trace["display"] = _disp
                tool_trace.append(trace)
                # 时间线里记成一条 tool「**结束**」阶段（含最终耗时/成败/结果），
                # 与开始阶段各占一条；位置上与 thinking/text 交错。
                # 显式带 `status: done`：前端靠它判断"是否已完成"来决定收起/展开。
                #
                # 工具开始时刻就封掉当前正文：工具是在正文**之后**发起的，
                # 之后模型若再说话属于新的一条（时间点不同）。
                _open_text = None
                trace_timeline.append(_stamp({
                    "kind": "tool", "phase": "end", "round": _round,
                    "status": "done", **trace}))
                _seq += 1
                # 工具结果同样会整段回到下一次 prompt：先计入增量，再带着最新的
                # 上下文读数推给前端（长任务里工具输出才是上下文涨得最快的一块）。
                _ctx_add(_model_facing_text(result))
                # 补发"结果"事件：前端按 id 就地更新那一条（开始时已在列表里）
                yield {"tool_result": {**trace, "role_id": rid, "role_name": rname},
                       "context_tokens": _ctx_tokens()}
                # 不做字符截断：截断会把 read 的「续读提示（还有多少 / 从第几行继续）」
                # 拦腰砍掉，模型于是以为已经读到文件末尾——这比给长一点更糟。
                # 真正的量级控制放在读取层：单文件 50 KB / 2000 行，靠 offset 分段。
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": _model_facing_text(result)})

            # ── 派发后收尾：不再让主持继续在前台做同一件事 ─────────────
            # 团队后台任务已经接管执行；这里撤掉下一轮的工具清单，并明确要求
            # 用一两句话收尾。否则主会话会继续 shell / read_file 重复干活，
            # 既烧 token 又和成员的工作重复（用户反馈的现象）。
            if _team_delegated and not _delegated_wrapped:
                _delegated_wrapped = True
                messages.append({
                    "role": "user",
                    "content": "（系统提示）你已经把这段工作放到后台执行"
                               "（团队成员 / 后台任务）。本轮不要再调用工具重复执行同一件事；"
                               "用一两句话说明分工与下一步，然后结束本轮。"
                               "完成后等用户在会话里点「继续」再整合结果。",
                })
                specs = []
    except llm_mod.LlmError as e:
        logger.warning("对话 LLM 流式调用失败: %s", e)
        sink["error"] = f"LLM 调用失败：{e}"
        return
    finally:
        # 后台工具（前台超预算转过去的）**不**随本轮收尾杀掉：
        # 长构建 / 长测试本来就要跑几分钟，杀掉等于白跑。它们继续跑，
        # 跑完后自己把 tool_result 补进事件流（下一轮模型也能用 tool_job 取结果）。
        # 真正需要终止的只有两种情况：用户点停止（conv_worker.force_stop 会杀），
        # 或模型主动 tool_job(action='kill')。这里只记一条日志便于排查。
        try:
            from services.agent_zero.task_layer import tool_jobs as _tjobs

            _left = _tjobs.running_count(str(conv.get("id") or ""))
            if _left:
                logger.info("本轮结束，%d 个后台工具继续运行 conv=%s",
                            _left, conv.get("id"))
        except Exception:
            logger.debug("统计后台工具失败（忽略）", exc_info=True)
        # 前台工具的登记只在本轮有效：正常路径已在每次调用结束时注销，
        # 这里兜底异常 / 强制停止留下的残项（后台 job 不在此表）。
        try:
            from services.agent_zero.task_layer import tool_activity as _tact

            _tact.clear_conversation(str(conv.get("id") or ""))
        except Exception:
            logger.debug("清理前台工具活动失败（忽略）", exc_info=True)

    text = "".join(parts).strip()
    # 正文区 vs 时间线的边界规则见 `_extract_final_text`（含两条踩过的坑）。
    final_text = ""
    if trace_timeline:
        final_text = _extract_final_text(trace_timeline, _rounds_used)
    # 收尾那一轮的正文不属于任何一次请求，但下一轮就会整段进入上下文：
    # 计入增量，顶部读数才不会在答完之后少掉这一块。
    _ctx_add(final_text)
    # 先把可观测数据挂上，**再**判断正文是否为空：模型可能只思考+调工具、
    # 一句正文都没说。若因为 text 为空提前 return，思考与工具轨迹就全丢了
    # （用户看到"未返回内容"，但其实它干了活）。
    sink["reasoning"] = "".join(thinking)
    sink["tool_trace"] = tool_trace
    sink["trace"] = trace_timeline
    sink["duration_ms"] = int((time.time() - started) * 1000)
    # 可观测：轮数 / LLM 与工具的耗时拆分（定位"慢在哪"）。
    sink["rounds"] = _rounds_used + 1
    sink["llm_ms"] = _llm_ms_total
    sink["tool_ms"] = _tool_ms_total
    sink["model"] = display_model or models.get("loop")
    sink["usage"] = _usage_snapshot(
        _usage_totals, _cached_tokens, _prompt_sum,
        _llm_ms_total, _rounds_used + 1)
    # 收尾时的上下文占用（真实锚点 + 本轮正文 / 工具结果的增量）：done 事件带回前端。
    sink["context_tokens"] = _ctx_tokens()
    # 服务端可见的每轮汇总：一眼看出"慢在哪"（轮数 / LLM 往返 / 工具 / 催促），
    # 以及**上下文都花在哪**（system / 对话 / 工具输出），调压缩参数时先看这行。
    try:
        from services.agent_zero.runtime.zero import compress as _czb

        _bd = _czb.token_breakdown(messages)
    except Exception:
        _bd = {}
    logger.info(
        "对话轮次统计 conv=%s model=%s rounds=%d llm_ms=%d tool_ms=%d "
        "total_ms=%d ctx=%d breakdown=%s",
        conv.get("id"), display_model, _rounds_used + 1, _llm_ms_total, _tool_ms_total,
        sink["duration_ms"], int(_bd.get("total") or 0),
        {k: int(_bd.get(k) or 0) for k in
         ("system", "user", "assistant", "tool", "image")} if _bd else {})
    # 兜底：末轮没单独说话时，用最后一条时间线正文或全文，
    # 保证正文区不空着；同时**不与时间线重复**。
    # 关键：兜底也只在"它确实是收尾那句"时摘出——即它后面**再没有步骤**。
    # 若它后面还有 tool（"说一句→调工具→结束"），那它是过程正文，
    # 摘掉就等于删掉真实过程（实测踩到）。
    if not final_text and trace_timeline:
        tail = trace_timeline[-1]
        if (tail.get("kind") == "text"
                and str(tail.get("text") or "").strip() == text):
            final_text = str(tail.get("text") or "").strip()
            trace_timeline.pop()
    if not final_text and not trace_timeline:
        final_text = text          # 一句话答完，没有过程
    sink["text"] = final_text
    if not sink["text"]:
        sink["error"] = "模型未返回正文"
        return


async def _apply_compaction(messages, models, decision, round_no, rid, rname, run_id):
    """跑一次上下文压缩，边推事件边等：开始 → 20s 心跳 → 结束。

    压缩跑在独立 task 里并推心跳——摘要模型慢 / 挂住时界面显示「正在压缩上下文」，
    不会看起来像卡死。结果通过内部事件 `{"_compressed": res}` 交回调用方
    （async generator 没有返回值），调用方必须消费它并从 yield 里过滤掉。
    """
    from services.agent_zero.runtime.zero import compress as _cz

    yield {"context_compress_start": _compress_start_event(
        decision, round_no, rid, rname)}
    _t0 = time.monotonic()
    task = asyncio.create_task(_cz.apply(messages, models=models, decision=decision))
    async for _ev in _ping_until_done(task, run_id, label="上下文压缩",
                                      rid=rid, rname=rname, waiting="compact"):
        yield _ev
    res = task.result()
    yield {"_compressed": res}
    yield {"context_compress_done": _compress_done_event(
        res, round_no, rid, rname, int((time.monotonic() - _t0) * 1000))}


def _extract_final_text(timeline: List[Dict[str, Any]], last_round: int) -> str:
    """从时间线里摘出「最终答复」并从时间线移除（就地 pop）。

    判据不是"最后一条 text"（那可能是过程里的一句话），而是
    **它属于最后一轮、且之后没有工具**——那才是收尾的答复。
    早前无条件 pop 末条 text，遇到"边调工具边说完就结束"的模型会把过程正文删掉、
    正文区变空（实测踩到）。它后面可以有思考 / 重试 / 压缩等非工具步骤，
    但**不能再有工具**："说一句→调工具→结束"里的那句属于过程。
    """
    for idx in range(len(timeline) - 1, -1, -1):
        step = timeline[idx]
        if (step.get("kind") == "text"
                and step.get("round") == last_round
                and str(step.get("text") or "").strip()):
            if any(s.get("kind") == "tool" for s in timeline[idx + 1:]):
                return ""
            text = str(step.get("text") or "").strip()
            timeline.pop(idx)
            return text
    return ""


class _CtxMeter:
    """上下文占用估算：锚点（最近一次真实 prompt）+ 增量（之后新写内容的估算）。

    真实 token 只有上游返回的 prompt_tokens 说了算，但那是**每轮只有一次**：
    一整轮长生成、一串工具跑下来，读数会长时间钉在旧数字上。所以用「锚点 + 增量」，
    下一轮真实值回来时整体校正——估算只负责两次请求之间的连续性，不负责精度。
    """

    def __init__(self, messages: List[Dict[str, Any]]) -> None:
        try:
            from services.agent_zero.runtime.zero.compress import (
                count_tokens, estimate_tokens)
        except Exception:                                # pragma: no cover - 极端兜底
            count_tokens = _zero_text                         # noqa: F811
            estimate_tokens = _zero_messages                  # noqa: F811
        self._count = count_tokens
        self._anchor = 0
        self._delta = 0
        # 开局锚点 = system + 历史的估算值：第一次真实 usage 回来前，读数也得有个
        # 合理起点（否则会从 0 或「只算本轮新增」开始，看起来像上下文很小）。
        self.anchor(int(estimate_tokens(messages) or 0))

    def add(self, text: Any) -> None:
        """把刚写进上下文的一段内容计入增量（不改变锚点）。"""
        if text:
            self._delta += int(self._count(str(text)))

    def anchor(self, tokens: int) -> None:
        """拿到真实值：锚点归位，之前的估算增量作废。"""
        try:
            real = max(0, int(tokens or 0))
        except (TypeError, ValueError):
            real = 0
        if not real:
            return                      # 没拿到真实值就别把锚点清零
        self._anchor = real
        self._delta = 0

    def tokens(self) -> int:
        """当前上下文占用（锚点 + 估算增量）。"""
        return max(0, int(self._anchor) + int(self._delta))


def _zero_text(_text: str = "") -> int:
    """compress 模块不可用时的兜底：估不出 token 就按 0 算。"""
    return 0


def _zero_messages(_messages: Any = None) -> int:
    return 0


def _stamp_entry(entry: Dict[str, Any], started: float, seq: int) -> Dict[str, Any]:
    """给一条时间线记录盖上时间戳与序号（就地返回）。

    `ts` = 距本轮开始的毫秒数，是时间线的唯一排序依据；`seq` 只做同毫秒时的稳定次序。
    """
    entry["ts"] = int((time.time() - started) * 1000)
    entry["seq"] = seq
    return entry


async def _single_reply_stream(conv: Dict[str, Any], user: str,
                               role: Dict[str, Any],
                               autonomous: bool = False,
                               auto_execute: bool = False,
                               run_id: Optional[str] = None,
                               extra_context: str = "",
                               stage: str = "exploration") -> AsyncIterator[Dict[str, Any]]:
    """单角色流式回复（角色独立工作区 / 无群组 / 后台任务成员）。"""
    sink: Dict[str, Any] = {}
    async for evt in _stream_role(conv, user, role, sink, autonomous=autonomous,
                                  auto_execute=auto_execute, run_id=run_id,
                                  extra_context=extra_context):
        yield evt
    # 失败必须显式上报：事件里已有思考 / 工具轨迹，worker 会把它们落成一条
    # interrupted 回复；不能因为「有轨迹」就当成成功，否则超时 / 断网会被静默吞掉。
    if sink.get("error"):
        yield {"error": sink["error"]}
        return
    rid, rname = role.get("id"), role.get("name")
    node_id = _persist_assistant(conv, role, sink.get("text") or "",
                                 sink.get("model"),
                                 sink.get("duration_ms", 0),
                                 sink.get("reasoning", ""), sink.get("tool_trace"),
                                 sink.get("trace"), usage=sink.get("usage"),
                                 run_id=run_id or "", stage=stage)
    _capture_run_memory(conv, role, sink)
    zero_store.append_audit(
        user, "conversation_reply",
        args={"conversation_id": conv["id"], "role_id": rid,
              "model": sink.get("model"), "stream": True},
    )
    yield {"done": True, "id": node_id, "role_id": rid, "role_name": rname,
           "model": sink.get("model"), "duration_ms": sink.get("duration_ms", 0),
           "usage": sink.get("usage") or {},
           # 收尾时的上下文占用（锚点 + 本轮正文/工具结果的增量）。
           "context_tokens": sink.get("context_tokens") or 0,
           "reasoning": sink.get("reasoning", ""),
           # 正文由后端定稿（它才是"最终答复"的权威）：
           # 前端自己拼的 content 会把过程段也带上，与时间线重复。
           "text": sink.get("text") or "",
           "tool_trace": sink.get("tool_trace") or [],
           "trace": sink.get("trace") or []}


async def _group_reply_stream(conv: Dict[str, Any], user: str, content: str,
                              mentions: Optional[List[str]],
                              host: Optional[Dict[str, Any]],
                              members: List[Dict[str, Any]],
                              autonomous: bool = False,
                              auto_execute: bool = False,
                              run_id: Optional[str] = None,
                              extra_context: str = "",
                              max_speakers: int = GROUP_MAX_SPEAKERS
                              ) -> AsyncIterator[Dict[str, Any]]:
    """群聊圆桌回复：主持/成员各自应答，`@成员` 触发其自身回复。

    ``max_speakers`` 来自 `agent_teams.max_speakers`（默认 4，上限 12）：
    一条用户消息最多触发这么多个角色，避免多角色对话把成本放大到不可控。
    """
    try:
        limit = max(1, min(int(max_speakers or GROUP_MAX_SPEAKERS), 12))
    except (TypeError, ValueError):
        limit = GROUP_MAX_SPEAKERS
    all_roles = ([host] if host else []) + members
    if not all_roles:
        yield {"error": "该群组暂无可用角色：请先加入成员角色。"}
        return

    # 目标角色：显式 mentions(id 或名字) > 内容 @ 提及 > 主持（否则首个成员）
    targets = _mention_targets(mentions, content, all_roles)
    if not targets:
        targets = [host] if host else [members[0]]

    queue = list(targets)
    spoken: set = set()
    said: Dict[str, str] = {}
    while queue and len(spoken) < limit:
        role = queue.pop(0)
        rid = role["id"]
        if rid in spoken:
            continue
        spoken.add(rid)

        sink: Dict[str, Any] = {}
        async for evt in _stream_role(conv, user, role, sink, autonomous=autonomous,
                                      auto_execute=auto_execute, run_id=run_id,
                                      extra_context=extra_context):
            yield evt
        # 群组同理：没有正文但有轨迹也要落库，别丢模型的工作痕迹
        if sink.get("error") and not sink.get("trace"):
            yield {"error": f"「{role.get('name')}」{sink['error']}"}
            continue

        rname = role.get("name")
        node_id = _persist_assistant(conv, role, sink.get("text") or "",
                                     sink.get("model"),
                                     sink.get("duration_ms", 0),
                                     sink.get("reasoning", ""), sink.get("tool_trace"),
                                     sink.get("trace"), usage=sink.get("usage"),
                                     run_id=run_id or "")
        _capture_run_memory(conv, role, sink)
        zero_store.append_audit(
            user, "conversation_reply",
            args={"conversation_id": conv["id"], "role_id": rid, "role_name": rname,
                  "model": sink.get("model"), "stream": True},
        )
        yield {"done": True, "id": node_id, "role_id": rid, "role_name": rname,
               "model": sink.get("model"), "duration_ms": sink.get("duration_ms", 0),
               "usage": sink.get("usage") or {},
               "context_tokens": sink.get("context_tokens") or 0,
               "reasoning": sink.get("reasoning", ""),
               # 正文由后端定稿：前端自拼的 content 会把过程段也带上，与时间线重复
               "text": sink.get("text") or "",
               "tool_trace": sink.get("tool_trace") or [],
               "trace": sink.get("trace") or []}

        # 回复中 @成员 → 通知该成员自行回复（主持不得转发；各角色只产出自己的内容）
        said[rid] = sink.get("text") or ""
        for r in _roles_mentioned(sink["text"], all_roles):
            if r["id"] not in spoken and r["id"] not in [x["id"] for x in queue]:
                queue.append(r)

    # 超出本轮发言上限 / 被 @ 但没来得及接话的成员：不能静默丢掉，
    # 交给后台 run 继续回复（主会话不等待，发送按钮保持可用）。
    for sid, stext in said.items():
        speaker = next((r for r in all_roles if r["id"] == sid), None)
        if speaker and stext:
            await _dispatch_reply_mentions(
                conv, speaker, stext, user, run_id=run_id or "", skip_ids=spoken)



# ── 列表 / 读取（对话界面）────────────────────────────────

ARCHIVE_AFTER_DAYS = 30
_archive_sweep_at = 0.0


def archive_stale_conversations(days: int = ARCHIVE_AFTER_DAYS) -> int:
    """把 `days` 天内没有活动（updated_at）的对话标记为 archived。幂等、可反复调用。"""
    import time

    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    now = time.time()
    if now - _archive_sweep_at < 600:  # 每 10 分钟最多扫一次
        return 0
    globals()["_archive_sweep_at"] = now
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} SET archived = 1 "
                 f"WHERE archived = 0 "
                 f"AND COALESCE(zone, '') <> '{GLOBAL_AGENT_ZONE}' "
                 f"AND id NOT IN (SELECT conversation_id FROM {_q('bot_routes')}) "
                 f"AND updated_at < datetime('now','localtime','-{int(days)} days')")
        )
        db.commit()
        return res.rowcount or 0


def _page_message_stats(db: Any, items: list, *, by_session: bool = False) -> None:
    """给一页会话就地补上 unread / unread_count / message_count / last_message。

    `by_session=True` 时把一条会话根下的全部栏目一起统计（会话中心的一张卡 =
    一个会话，未读 / 预览取整棵会话）。

    列表页一次最多 50 条，逐条跑「消息数 + 最后一条 + 未读」会变成 150 次查询
    （模板里最贵的接口之一）。这里改成按页两次聚合：

    1. 统计：每个会话的最近助手消息时间 + 未读条数（未读要跟各自的 last_read_at
       比较，所以把这一页的 (id, last_read_at) 作为 CTE 传进去）；
    2. 预览：窗口函数按会话分组取最后一条消息，顺带拿到该会话消息总数。

    两条 SQL 都显式走 gdag_nodes 的 (conversation_id, stage) 表达式索引，
    只扫本页会话的消息，不随总会话数线性变慢。
    """
    if not items:
        return
    from sqlalchemy import text
    from services.agent_zero.stores.zero_store import _q

    values = []
    params: Dict[str, Any] = {}
    for i, item in enumerate(items):
        params[f"id{i}"] = str(item.get("id") or "")
        params[f"lr{i}"] = str(item.get("last_read_at") or "")
        values.append(f"(:id{i}, :lr{i})")
    page = f"WITH page(id, last_read) AS (VALUES {', '.join(values)})"
    if by_session:
        scope = (f", threads(root_id, tid, last_read) AS ("
                 f"SELECT p.id, c.id, c.last_read_at FROM page p "
                 f"JOIN {_q('conversations')} c "
                 f"ON c.id = p.id OR c.session_id = p.id)")
        source = (f"page p JOIN threads t ON t.root_id = p.id "
                  f"JOIN {_q('gdag_nodes')} g INDEXED BY idx_gdag_conv "
                  f"ON json_extract(g.props, '$.conversation_id') = t.tid")
        group = "p.id"
        unread_time = "t.last_read"
        preview_partition = "t.root_id"
    else:
        scope = ""
        source = (f"page p JOIN {_q('gdag_nodes')} g INDEXED BY idx_gdag_conv "
                  f"ON json_extract(g.props, '$.conversation_id') = p.id")
        group = "p.id"
        unread_time = "p.last_read"
        preview_partition = "json_extract(g.props, '$.conversation_id')"

    stats_sql = (
        f"{page}{scope} "
        f"SELECT {group} AS cid, "
        f"  MAX(CASE WHEN json_extract(g.props, '$.by') = 'assistant' "
        f"           THEN g.created_at END) AS last_assistant_at, "
        f"  SUM(CASE WHEN json_extract(g.props, '$.by') = 'assistant' "
        f"           AND json_extract(g.props, '$.stage') = 'exploration' "
        f"           AND COALESCE(json_extract(g.props, '$.kind'), '') <> 'checkpoint' "
        f"           AND g.created_at > COALESCE({unread_time}, '') "
        f"           THEN 1 ELSE 0 END) AS unread_count "
        f"FROM {source} "
        f"WHERE g.type = 'fact' "
        f"GROUP BY {group}"
    )
    preview_sql = (
        f"{page}{scope} "
        f"SELECT cid, content, n FROM ("
        f"  SELECT {preview_partition} AS cid, "
        f"    json_extract(g.props, '$.content') AS content, "
        f"    COUNT(*) OVER (PARTITION BY {preview_partition}) AS n, "
        f"    ROW_NUMBER() OVER (PARTITION BY {preview_partition} "
        f"      ORDER BY g.created_at DESC, g.rowid DESC) AS rn "
        f"  FROM {source} "
        f"  WHERE g.type = 'fact' "
        f"    AND json_extract(g.props, '$.stage') = 'exploration' "
        f"    AND COALESCE(json_extract(g.props, '$.kind'), '') "
        f"        NOT IN ('checkpoint', :clear_kind)"
        f") WHERE rn = 1"
    )

    stats = {str(r["cid"]): r for r in
             db.execute(text(stats_sql), params).mappings().all()}
    previews = {str(r["cid"]): r for r in
                db.execute(text(preview_sql),
                           {**params, "clear_kind": CLEAR_KIND}).mappings().all()}

    for item in items:
        cid = str(item.get("id") or "")
        st = stats.get(cid)
        last_at = str(st["last_assistant_at"] or "") if st else ""
        item["unread"] = bool(last_at) and last_at > str(item.get("last_read_at") or "")
        item["unread_count"] = int(st["unread_count"] or 0) if st else 0
        pv = previews.get(cid)
        item["message_count"] = int(pv["n"] or 0) if pv else 0
        item["last_message"] = str(pv["content"] or "")[:120] if pv else ""


def list_conversations(workspace_id: Optional[str] = None,
                       role_id: Optional[str] = None,
                       team_id: Optional[str] = None,
                       limit: int = 50, offset: int = 0,
                       keyword: Optional[str] = None,
                       source: Optional[str] = None,
                       forked_from_id: Optional[str] = None,
                       include_archived: bool = False,
                       session_id: Optional[str] = None,
                       roots_only: bool = False) -> list:
    """对话列表（工作区 / 角色 / 关键字 / 来源过滤；附消息数 + 最后一条预览）。

    **排序 = 进行中优先，两组各自按时间倒序**：有活跃 run（queued/running/waiting）
    或 active 目标的会话算「进行中」，排在前面；其余算「完结」，排在后面。
    同一组内按 updated_at 倒序（最近活动在最上）。

    每条带 `active`（进行中）/ `has_active_goal` / `run_status` 字段，前端据此分组。

    `roots_only` = 只列会话根（会话中心 / 角色详情用）；`session_id` = 列某个根
    会话下的全部栏目（窗口左侧栏目栏用）。根的统计（运行 / 未读 / 更新时间）
    按整棵会话聚合，栏目则保持自己的口径。

    source='task_result' = 后台任务结果会话（收件箱）。
    默认排除自动归档（30 天无活动）的对话；include_archived=True 时包含。
    """
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    archive_stale_conversations()
    scope_expr = "COALESCE(NULLIF(c.session_id, ''), c.id)"
    # live = 正在跑（有活跃 run）或正在追（active 目标）。用子查询算在 SQL 里，
    # 这样分页仍然正确：不能取完一页再在 Python 里排序。
    if roots_only:
        live_expr = (
            f"CASE WHEN EXISTS (SELECT 1 FROM {_q('conversation_runs')} r "
            f"  JOIN {_q('conversations')} tc ON tc.id = r.conversation_id "
            f"  WHERE COALESCE(NULLIF(tc.session_id, ''), tc.id) = {scope_expr} "
            f"  AND r.status IN ('queued','running','waiting')) "
            f"OR EXISTS (SELECT 1 FROM {_q('goals')} gg "
            f"  JOIN {_q('conversations')} tg ON tg.id = gg.conversation_id "
            f"  WHERE COALESCE(NULLIF(tg.session_id, ''), tg.id) = {scope_expr} "
            f"  AND gg.status = 'active') "
            f"THEN 1 ELSE 0 END"
        )
        running_count_expr = (
            f"(SELECT COUNT(*) FROM {_q('conversation_runs')} r "
            f"  JOIN {_q('conversations')} tc ON tc.id = r.conversation_id "
            f"  WHERE COALESCE(NULLIF(tc.session_id, ''), tc.id) = {scope_expr} "
            f"  AND r.status IN ('queued','running','waiting'))"
        )
        sort_expr = (
            f"(SELECT MAX(tc.updated_at) FROM {_q('conversations')} tc "
            f"  WHERE COALESCE(NULLIF(tc.session_id, ''), tc.id) = {scope_expr})"
        )
    else:
        live_expr = (
            f"CASE WHEN EXISTS (SELECT 1 FROM {_q('conversation_runs')} r "
            f"  WHERE r.conversation_id = c.id "
            f"  AND r.status IN ('queued','running','waiting')) "
            f"OR EXISTS (SELECT 1 FROM {_q('goals')} gg "
            f"  WHERE gg.conversation_id = c.id AND gg.status = 'active') "
            f"THEN 1 ELSE 0 END"
        )
        running_count_expr = (
            f"(SELECT COUNT(*) FROM {_q('conversation_runs')} r "
            f"  WHERE r.conversation_id = c.id "
            f"  AND r.status IN ('queued','running','waiting'))"
        )
        sort_expr = "c.updated_at"
    # 未读 / 消息数不在主查询里做相关子查询：那是「每个会话各扫一遍 gdag_nodes」，
    # 300 个会话就要扫 300 次全表（实测 3.6s）。主查询只取分页需要的最小列，
    # 未读与预览在拿到这一页 id 之后按页批量算，见 _page_message_stats。
    base = (f"SELECT c.id, c.workspace_id, c.bound_workspace_id, c.workspace_custom, "
            f"c.role_id, c.team_id, c.session_id, "
            f"{scope_expr} AS session_root_id, "
            f"(SELECT COUNT(*) FROM {_q('conversations')} tc "
            f"  WHERE tc.session_id = {scope_expr} OR tc.id = {scope_expr}) AS thread_count, "
            f"c.zone, c.source, c.title, "
            f"c.archived, c.forked_from_id, c.forked_from_message_id, "
            f"c.created_by, c.created_at, {sort_expr} AS sort_at, c.last_read_at, "
            f"{running_count_expr} AS running_count, "
            f"(SELECT r2.status FROM {_q('conversation_runs')} r2 "
            f"  WHERE r2.conversation_id = c.id "
            f"  AND r2.status IN ('queued','running','waiting') "
            f"  ORDER BY r2.created_at DESC, r2.id DESC LIMIT 1) AS run_status, "
            f"(SELECT gg2.status FROM {_q('goals')} gg2 "
            f"  WHERE gg2.conversation_id = c.id ORDER BY gg2.created_at DESC, "
            f"  gg2.id DESC LIMIT 1) AS goal_status, "
            f"{live_expr} AS is_live "
            f"FROM {_q('conversations')} c WHERE 1=1")
    params: Dict[str, Any] = {"limit": max(1, min(int(limit), 200)),
                              "offset": max(0, int(offset))}
    if not include_archived:
        base += " AND c.archived = 0"
    if roots_only:
        base += " AND COALESCE(c.session_id, '') = ''"
    if session_id:
        base += f" AND {scope_expr} = :sid"
        params["sid"] = str(session_id)
    if workspace_id:
        base += " AND c.workspace_id = :wid"
        params["wid"] = workspace_id
    if role_id:
        base += " AND c.role_id = :rid"
        params["rid"] = role_id
    if team_id:
        base += " AND c.team_id = :tid"
        params["tid"] = team_id
    if source:
        base += " AND c.source = :src"
        params["src"] = source
    if forked_from_id:
        # 分支树：查某条会话（两跳内的直接子分支）。
        base += " AND c.forked_from_id = :fork"
        params["fork"] = forked_from_id
    if keyword:
        # 用 json_extract 而不是 ->>：表达式索引建在 json_extract 上，->> 命中不了。
        base += (f" AND EXISTS (SELECT 1 FROM {_q('gdag_nodes')} g "
                 f"WHERE g.type = 'fact' "
                 f"AND json_extract(g.props, '$.conversation_id') = c.id "
                 f"AND json_extract(g.props, '$.stage') = 'exploration' "
                 f"AND COALESCE(json_extract(g.props, '$.kind'), '') <> 'checkpoint' "
                 f"AND json_extract(g.props, '$.content') LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    # 进行中在前（is_live DESC），组内时间倒序。
    sql = (f"SELECT * FROM ({base}) t "
           f"ORDER BY t.is_live DESC, t.sort_at DESC, t.created_at DESC, t.id "
           f"LIMIT :limit OFFSET :offset")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(text(sql), params).mappings().all()
        out = []
        for r in rows:
            item = dict(r)
            root = str(item.get("session_root_id") or item.get("id") or "")
            item["created_at"] = str(item.get("created_at") or "")
            item["updated_at"] = str(item.get("sort_at") or item.get("created_at") or "")
            item["session_id"] = root
            item["session_root_id"] = root
            item["thread_count"] = int(item.get("thread_count") or 1)
            item.pop("sort_at", None)
            item["active"] = bool(item.pop("is_live", 0))
            item["running_count"] = int(item.get("running_count") or 0)
            item["last_read_at"] = str(item.get("last_read_at") or "")
            item["run_status"] = str(item.pop("run_status", "") or "")
            item["workspace_id"] = zero_store.public_workspace_id(item.get("workspace_id"))
            item["workspace_custom"] = bool(item["workspace_id"])
            item["has_active_goal"] = item.pop("goal_status", None) == "active"
            out.append(item)
        # 未读 / 消息数 / 最后一条预览：整页两次聚合查询，不再逐条 N+1。
        # 根会话按整棵会话（含栏目）统计；列栏目时按各自的会话口径。
        _page_message_stats(db, out, by_session=roots_only)
    # token 用量：整页一次聚合（输入 / 输出 / 速度 / 缓存），列表卡片直接展示。
    from services.agent_zero.services import usage_service

    usage_map = usage_service.usage_by_conversation(
        [item["id"] for item in out], stage="exploration")
    for item in out:
        item["usage"] = usage_map.get(item["id"]) or usage_service.empty_usage()
    return out


def mark_conversation_read(conv_id: str) -> bool:
    """只把指定栏目（对话）的已读水位推到当前时间。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} "
                 f"SET last_read_at = datetime('now','localtime') "
                 f"WHERE id = :cid"),
            {"cid": conv_id},
        )
        db.commit()
        return bool(res.rowcount)


def mark_all_conversations_read() -> int:
    """一键已读：把全部未归档普通会话的已读水位推到当前时间。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} "
                 f"SET last_read_at = datetime('now','localtime') "
                 f"WHERE archived = 0 AND COALESCE(zone, '') <> 'global-agent'")
        )
        db.commit()
        return int(res.rowcount or 0)


def count_conversations(workspace_id: Optional[str] = None,
                        role_id: Optional[str] = None,
                        team_id: Optional[str] = None,
                        keyword: Optional[str] = None,
                        source: Optional[str] = None,
                        forked_from_id: Optional[str] = None,
                        include_archived: bool = False,
                        session_id: Optional[str] = None,
                        roots_only: bool = False) -> int:
    """对话总数（与 list_conversations 同过滤条件）。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    sql = f"SELECT COUNT(*) FROM {_q('conversations')} WHERE 1=1"
    if not include_archived:
        sql += " AND archived = 0"
    if roots_only:
        sql += " AND COALESCE(session_id, '') = ''"
    if session_id:
        sql += " AND COALESCE(NULLIF(session_id, ''), id) = :sid"
    params: Dict[str, Any] = {}
    if session_id:
        params["sid"] = str(session_id)
    if workspace_id:
        sql += " AND workspace_id = :wid"
        params["wid"] = workspace_id
    if role_id:
        sql += " AND role_id = :rid"
        params["rid"] = role_id
    if team_id:
        sql += " AND team_id = :tid"
        params["tid"] = team_id
    if keyword:
        sql += (f" AND EXISTS (SELECT 1 FROM {_q('gdag_nodes')} g "
                f"WHERE g.type = 'fact' "
                f"AND json_extract(g.props, '$.conversation_id') = {_q('conversations')}.id "
                f"AND json_extract(g.props, '$.stage') = 'exploration' "
                f"AND COALESCE(json_extract(g.props, '$.kind'), '') <> 'checkpoint' "
                f"AND json_extract(g.props, '$.content') LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    if source:
        sql += " AND source = :src"
        params["src"] = source
    if forked_from_id:
        sql += " AND forked_from_id = :fork"
        params["fork"] = forked_from_id
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        n = db.execute(text(sql), params).scalar()
    return int(n or 0)


def _last_message(db: Any, conv_id: str) -> str:
    from sqlalchemy import text
    from services.agent_zero.stores.zero_store import _q

    row = db.execute(
        text(f"SELECT props->>'content' AS c FROM {_q('gdag_nodes')} "
             f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
             f"AND props->>'stage' = 'exploration' "
             f"AND COALESCE(props->>'kind', '') <> 'checkpoint' "
             f"AND COALESCE(props->>'kind', '') <> :clear_kind "
             f"ORDER BY created_at DESC, rowid DESC LIMIT 1"),
        {"cid": conv_id, "clear_kind": CLEAR_KIND},
    ).scalar()
    return str(row or "")[:120]


def _count_messages(db: Any, conv_id: str) -> int:
    from sqlalchemy import text
    from services.agent_zero.stores.zero_store import _q

    n = db.execute(
        text(f"SELECT COUNT(*) FROM {_q('gdag_nodes')} "
             f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
             f"AND props->>'stage' = 'exploration' "
             f"AND COALESCE(props->>'kind', '') <> 'checkpoint' "
             f"AND COALESCE(props->>'kind', '') <> :clear_kind"),
        {"cid": conv_id, "clear_kind": CLEAR_KIND},
    ).scalar()
    return int(n or 0)


def _usage_of_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从一条 gdag 行里取出 props.usage（props / usage 可能是 JSON 文本）。"""
    if not row:
        return {}
    props = row.get("props")
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except Exception:
            return {}
    if not isinstance(props, dict):
        return {}
    usage = props.get("usage")
    if isinstance(usage, str):
        try:
            usage = json.loads(usage)
        except Exception:
            return {}
    return usage if isinstance(usage, dict) else {}


def usage_total(conv_id: str) -> Dict[str, Any]:
    """当前会话的用量总览：输入/输出为合计，缓存命中与速度按总量重算（即加权平均）。

    直接聚合库里的助手回复（不只当前加载的 200 条转录），所以长会话也准确。
    运行中的那一轮还没落库，由前端把实时 usage 叠加上去。

    `last_prompt_tokens` = 最近一轮的真实 prompt（当前上下文占用），
    `max_context_tokens` = 历史峰值（仅供「峰值 / 压缩前」对照）。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(
                f"SELECT "
                f"SUM(CAST(json_extract(props, '$.usage.prompt_tokens') AS INTEGER)) AS prompt_tokens, "
                f"SUM(CAST(json_extract(props, '$.usage.completion_tokens') AS INTEGER)) AS completion_tokens, "
                f"SUM(CAST(json_extract(props, '$.usage.cached_tokens') AS INTEGER)) AS cached_tokens, "
                f"SUM(CAST(json_extract(props, '$.usage.llm_ms') AS INTEGER)) AS llm_ms, "
                f"SUM(CAST(json_extract(props, '$.usage.rounds') AS INTEGER)) AS rounds, "
                f"MAX(CAST(json_extract(props, '$.usage.max_context_tokens') AS INTEGER)) AS max_context_tokens, "
                f"SUM(CASE WHEN json_extract(props, '$.usage') IS NOT NULL THEN 1 ELSE 0 END) AS turns "
                f"FROM {_q('gdag_nodes')} "
                f"WHERE type = 'fact' "
                f"  AND json_extract(props, '$.conversation_id') = :cid "
                f"  AND json_extract(props, '$.stage') = 'exploration' "
                f"  AND json_extract(props, '$.by') = 'assistant'"
            ),
            {"cid": conv_id},
        ).mappings().first()
        # 「当前上下文占用」取自**最近一轮有真实 prompt 的回复**，而不是会话峰值：
        # 压缩之后 prompt 会变小，取 MAX 的话顶部百分比永远停在压缩前的高位。
        # 老数据没有 last_prompt_tokens 字段时退回该轮的 max_context_tokens。
        last_row = db.execute(
            text(f"SELECT props FROM {_q('gdag_nodes')} "
                 f"INDEXED BY idx_gdag_conv_stage "
                 f"WHERE type = 'fact' "
                 f"  AND json_extract(props, '$.conversation_id') = :cid "
                 f"  AND json_extract(props, '$.stage') = 'exploration' "
                 f"  AND json_extract(props, '$.by') = 'assistant' "
                 f"  AND COALESCE(CAST(json_extract(props, "
                 f"      '$.usage.prompt_tokens') AS INTEGER), 0) > 0 "
                 f"ORDER BY created_at DESC, rowid DESC LIMIT 1"),
            {"cid": conv_id},
        ).mappings().first()
    last_usage = _usage_of_row(last_row)
    last_prompt = (int(last_usage.get("last_prompt_tokens") or 0)
                   or int(last_usage.get("max_context_tokens") or 0))
    data = dict(row or {})
    prompt = int(data.get("prompt_tokens") or 0)
    completion = int(data.get("completion_tokens") or 0)
    cached = int(data.get("cached_tokens") or 0)
    llm_ms = int(data.get("llm_ms") or 0)
    total = prompt + completion
    turns = int(data.get("turns") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
        "llm_ms": llm_ms,
        "rounds": int(data.get("rounds") or 0),
        "max_context_tokens": int(data.get("max_context_tokens") or 0),
        "last_prompt_tokens": last_prompt,
        "turns": turns,
        "cache_rate": round(cached / prompt * 100, 1) if prompt else 0,
        "speed": round(completion / (llm_ms / 1000), 1) if llm_ms else 0,
        "avg_tokens_per_turn": round(total / turns, 1) if turns else 0,
    }


def list_messages(conv_id: str, limit: int = 200) -> list:
    """探索态消息（GDAG fact 节点，按时间升序）。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, props, created_at FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' "
                 f"AND json_extract(props, '$.conversation_id') = :cid "
                 f"AND json_extract(props, '$.stage') = 'exploration' "
                 # 转任务建议节点不单独成条消息：它现在是那次回复轨迹里的
                 # 一个 step（kind='proposal'），单独列出来会变成一个"额外回合"，
                 # 看起来跑到正文前面去了（实测踩到）。
                 # 节点本身仍保留——确认/忽略要靠它的 status/task_id。
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> 'task_proposal' "
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> 'checkpoint' "
                 f"ORDER BY created_at ASC, rowid ASC LIMIT :n"),
            {"cid": conv_id, "n": max(1, min(int(limit), 500))},
        ).mappings().all()
        # 建议的状态只存在节点上；消息轨迹里的 step 是生成时的快照，
        # 这里以节点为准回填，否则刷新后已忽略/已接受会重新变回 pending。
        proposal_rows = db.execute(
            text(f"SELECT id, props FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' "
                 f"AND json_extract(props, '$.conversation_id') = :cid "
                 f"AND json_extract(props, '$.kind') = 'task_proposal'"),
            {"cid": conv_id},
        ).mappings().all()
        # 消息所属 run：排队消息的**真实回合位置**由 run 决定，而不是它的发送时刻。
        # 队列模式下用户在上一轮回复还在跑时发消息，消息节点立刻落库（created_at 早于
        # 那条回复），但它的 run 要等当前 run 收尾才启动。按 created_at 排就会把它插到
        # 上一轮回复**前面**（实测：u2 排在了 a1 之前），看起来像回复答错了对象。
        # rowid 作次级键：时间只到秒，同一秒内建的多条 run 靠它区分先后。
        run_rows = db.execute(
            text(f"SELECT rowid AS seq, id, queued_msg_id, target_role_id, status, "
                 f"started_at, created_at "
                 f"FROM {_q('conversation_runs')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        ).mappings().all()
        # 历史数据兜底：「立即插入」被取走时的归属字段是后加的，老消息上没有。
        # 但「取走」这件事本身就落在事件日志里（intervention 事件带 run_id + 消息 id），
        # 据此还原归属，老会话刷新后位置也正确，不必单独跑一次数据迁移。
        injected_rows = db.execute(
            text(f"SELECT json_extract(e.data, '$.intervention.id') AS mid, e.run_id AS rid "
                 f"FROM {_q('conversation_run_events')} e "
                 f"JOIN {_q('conversation_runs')} r ON r.id = e.run_id "
                 f"WHERE r.conversation_id = :cid "
                 f"AND json_extract(e.data, '$.intervention.id') IS NOT NULL"),
            {"cid": conv_id},
        ).mappings().all()
    from services.agent_zero.stores.zero_store import decode_row

    proposal_status: dict = {}
    for pr in proposal_rows:
        pp = decode_row({"props": pr["props"]}).get("props") or {}
        proposal_status[str(pr["id"])] = {
            "status": pp.get("status") or "pending",
            "run_id": pp.get("run_id") or "",
        }

    # 消息 id → run 的排序锚点 (时刻, run 行序号)。时刻用 run 的**实际启动时刻**；
    # 尚未启动的排队 run（started_at 为空）用它自己的 created_at。
    # 只到秒的时间戳不足以区分同一秒内建的 run，所以带上 rowid 作次级键。
    msg_run_anchor: dict = {}
    run_anchor_by_id: dict = {}
    # 老数据兜底：`task_ack` 的 anchor_run_id 是后加的。历史确认消息没有这个字段，
    # 但它的 run 与它由同一次派发几乎同时创建（同一秒），且 run 的 target_role_id
    # 就是确认消息的 role_id。据此还原锚点，老会话刷新后顺序也正确，不必迁移数据。
    ack_run_by_role_created: dict = {}
    for rr in run_rows:
        rid = str(rr["id"] or "")
        seq = int(rr["seq"] or 0)
        anchor = (str(rr["started_at"] or rr["created_at"] or ""), seq)
        if rid:
            run_anchor_by_id[rid] = anchor
        trid = str(rr["target_role_id"] or "")
        created = str(rr["created_at"] or "")
        if trid and created:
            prev = ack_run_by_role_created.get((trid, created))
            if prev is None or seq > prev[0]:
                ack_run_by_role_created[(trid, created)] = (seq, rid)
        mid = str(rr["queued_msg_id"] or "")
        if not mid:
            continue
        prev = msg_run_anchor.get(mid)
        if prev is None or anchor < prev:
            msg_run_anchor[mid] = anchor

    # 「立即插入」被哪一轮取走：新数据落在 props.injected_into_run 上；
    # 老数据没有这个字段，但从事件日志能还原（intervention 事件带 run_id + 消息 id）。
    # 不回填的老消息刷新后会退回自己的 created_at，位置仍然夹在当前回答上面。
    injected_run_by_msg: dict = {}
    for ir in injected_rows:
        mid = str(ir["mid"] or "")
        rid = str(ir["rid"] or "")
        if mid and rid:
            injected_run_by_msg.setdefault(mid, rid)

    out = []
    for r in rows:
        props = decode_row({"props": r["props"]}).get("props") or {}
        trace = props.get("trace") or []
        tool_trace = props.get("tool_trace") or []
        for steps in (trace, tool_trace):
            for step in steps:
                if step.get("kind") != "proposal":
                    continue
                live = proposal_status.get(str(step.get("proposal_id") or ""))
                if live:
                    step["status"] = live["status"]
                    if live["run_id"]:
                        step["run_id"] = live["run_id"]
        kind = str(props.get("kind") or "")
        anchor_run_id = str(props.get("anchor_run_id") or "")
        if not anchor_run_id and kind == "task_ack":
            role_id = str(props.get("role_id") or "")
            created = str(r["created_at"] or "")
            if role_id and created:
                anchor_run_id = (ack_run_by_role_created.get((role_id, created))
                                 or (0, ""))[1]
        out.append({
            "id": r["id"],
            "content": props.get("content") or "",
            "by": props.get("by") or "",
            "display_by": props.get("display_by") or "",
            "role_id": props.get("role_id") or "",
            "role_name": props.get("role_name") or "",
            "role_avatar": props.get("role_avatar") or "",
            "role_title": props.get("role_title") or "",
            "role_department": props.get("role_department") or "",
            "duration_ms": int(props.get("duration_ms") or 0),
            # 深度思考 + 工具调用情况：刷新后仍可回看（与流式一致）
            "reasoning": props.get("reasoning") or "",
            "tool_trace": tool_trace,
            # 有序轨迹：思考 / 工具 / 中间正文按发生顺序（前端渲染时间线的唯一依据）
            "trace": trace,
            # token 用量（输入/输出）：轨迹面板指标条
            "usage": props.get("usage") or {},
            "refs": props.get("refs") or [],
            "attachments": attachment_service.public_attachments(
                conv_id, props.get("attachments") or []),
            "kind": kind,
            "goal": props.get("goal") or "",
            "reason": props.get("reason") or "",
            "status": props.get("status") or "",
            # 任务完成通知要靠它区分 成功 / 失败 / 已取消（前端按它上色）
            "outcome": props.get("outcome") or "",
            "error": props.get("error") or "",
            "interrupted": bool(props.get("interrupted")),
            "task_id": props.get("task_id") or "",
            "task_title": props.get("task_title") or "",
            "task_status": props.get("task_status") or "",
            # 完成触发器是否已交回对话（自动继续 / 手动点「继续对话」都会置位）
            "task_continued": bool(props.get("continued")),
            "summary": props.get("summary") or "",
            # 该条回复所属的运行：转录排序要以这一轮 run 的启动时刻为锚点
            "run_id": props.get("run_id") or "",
            # 「立即插入」被哪一轮取走：它的排队 run 已被删除，没有这个归属就只能按
            # 自己的 created_at 排（会显示在当前回答上面）。见 mark_message_injected。
            # 老数据从事件日志还原，见 injected_run_by_msg。
            "injected_into_run": (props.get("injected_into_run")
                                  or injected_run_by_msg.get(str(r["id"] or "")) or ""),
            # 确认消息（task_ack）属于哪一轮：只用于转录排序。新数据写在
            # props.anchor_run_id；老数据按 (role_id, 创建秒) 反查成员 run 还原。
            "anchor_run_id": anchor_run_id,
            "queued": int(props.get("queued") or 0) == 1,
            "edited": bool(props.get("edited_at")),
            "ts": props.get("ts") or str(r["created_at"] or ""),
        })
    return _order_messages_by_turn(out, msg_run_anchor, run_anchor_by_id)


def _order_messages_by_turn(messages: list, msg_run_anchor: dict,
                            run_anchor_by_id: Optional[dict] = None) -> list:
    """按「真实回合顺序」重排消息，返回重排后的列表。

    排序锚点 = (该消息所属 run 的启动时刻, run 行序号)：
      - 助手回复：用 `props.run_id` 找那一轮 run；
      - 用户消息：用 `queued_msg_id` 反查（见 msg_run_anchor）；「立即插入」被取走的
        消息用它自己的 `injected_into_run`；
      - 只服务于某个回合、本身不产出内容的助手消息（如成员「收到，我在后台执行」
        的 task_ack）：用 `anchor_run_id` 归到那一轮，排在回合内的 @ 消息之后；
      - 尚未启动的排队 run（started_at 为空）→ 用 run 自己的 created_at；
      - 没有 run 的消息（闲聊 / 历史遗留）→ 自己的 ts。

    为什么必须这样：队列模式下用户消息落库时刻早于它所属 run 的启动时刻。
    只按 created_at 排，排队消息会出现在上一轮回复**之前**，看起来像是回复答错了
    对象（实测：u2 排在 a1 前）。按 run 锚点重排后，排队消息落到它真正执行的位置。

    锚点相同时：用户消息（回合发起）在前、助手回复在后；「立即插入」的消息排在最后
    （它是插进那一轮回复里的补充，用户期望看到的位置是**那条回答之后**）；再相同则
    保持原始顺序。
    """
    if not messages:
        return messages
    run_anchor_by_id = run_anchor_by_id or {}

    def _norm(stamp: Any) -> str:
        """统一时间格式：props.ts 是 ISO（`2026-10-30T16:48:27`），而库里
        created_at / started_at 是 `2026-10-30 16:48:27`（空格分隔）。
        两者直接做字符串比较会错位——`T`(0x54) 比空格(0x20) 大，同一分钟内的
        ISO 时间会被判成「更晚」，顺序整个反过来。这里统一成空格分隔。"""
        return str(stamp or "").replace("T", " ")

    keyed = []
    for idx, m in enumerate(messages):
        mid = str(m.get("id") or "")
        anchor = None
        # 助手回复按它自己的 run 取锚点（最准确）
        if str(m.get("by") or "") == "assistant":
            anchor = run_anchor_by_id.get(str(m.get("run_id") or ""))
        if anchor is None:
            anchor = msg_run_anchor.get(mid)
        if anchor is None:
            # 「立即插入」被某轮取走：它的排队 run 已删，只能靠这个归属回锚，
            # 否则会退回自己的 created_at 而夹到当前回答**上面**。
            anchor = run_anchor_by_id.get(str(m.get("injected_into_run") or ""))
        if anchor is None:
            # 确认消息等「不产出内容、只代表这一回合开始」的助手消息：归到那一轮，
            # 与 @ 消息共用锚点，再靠下面的 role_rank 排在其后。
            anchor = run_anchor_by_id.get(str(m.get("anchor_run_id") or ""))
        if anchor is not None:
            anchor = (_norm(anchor[0]), anchor[1])
        else:
            # 没有 run：退化成按自己的时间戳排（run 行序号取 0，最早）
            anchor = (_norm(m.get("ts")), 0)
        # 同一锚点内：用户消息（发起本回合）在前、助手回复居中、立即插入的最后
        by = str(m.get("by") or "")
        if by == "assistant":
            role_rank = 1
        elif m.get("injected_into_run"):
            role_rank = 2
        else:
            role_rank = 0
        keyed.append(((anchor, role_rank, idx), m))
    keyed.sort(key=lambda x: x[0])
    return [m for _k, m in keyed]


def list_conversation_ids(workspace_id: Optional[str] = None,
                          role_id: Optional[str] = None,
                          include_archived: bool = True) -> List[str]:
    """按工作区 / 角色列出**所有**对话 id（不分页）。

    批量删除用：对话列表是分页的，前端只有当前一页的 id，全删必须由后端取全量。
    默认包含已归档对话——「删除全部」的语义就是清空，归档的不该漏下。
    """
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    sql = f"SELECT id FROM {_q('conversations')} WHERE 1=1"
    params: Dict[str, Any] = {}
    if not include_archived:
        sql += " AND archived = 0"
    if workspace_id:
        sql += " AND workspace_id = :wid"
        params["wid"] = workspace_id
    if role_id:
        sql += " AND role_id = :rid"
        params["rid"] = role_id
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(text(sql), params).fetchall()
    return [str(r[0]) for r in rows]


def clean_conversation(conv_id: str, user: str = "local") -> Dict[str, int]:
    """彻底清空一个会话：上下文 + 历史消息 + 运行 / 后台任务 / 审批痕迹。

    与 ``clear_context``（只让模型看不到旧上下文，转录仍保留）不同：这里会把
    **所有**消息节点、run 与事件、任务、目标、待办、审批、附件都删掉，会话本身
    （标题 / 群组 / 工作区 / 临时角色）保留，相当于把会话重置成一个新会话。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    conv = get_conversation(conv_id)
    if not conv:
        raise ValueError(f"对话不存在: {conv_id}")

    # 1) 先停掉还在跑的 run：否则清空后 worker 收尾时又会写回一条「幽灵回复」。
    try:
        from services.agent_zero.task_layer import conv_worker, run_service

        worker = None
        for run in run_service.list_runs(conv_id, limit=100):
            status = str(run.get("status") or "")
            rid = str(run.get("id") or "")
            if not rid:
                continue
            if status == run_service.STATUS_QUEUED:
                run_service.cancel_run(rid)
            elif status == run_service.STATUS_RUNNING:
                run_service.request_stop(rid, silent=True)
                worker = worker or conv_worker.get_worker()
                if not worker.force_stop(rid):
                    worker._force_finalize(rid)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not run_service.active_run(conv_id):
                break
            time.sleep(0.05)
    except Exception:
        logger.debug("清空会话前停止运行失败（继续清理）", exc_info=True)

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        # 2) 图边先删：它外键指向节点，节点没了会留下悬空边。
        db.execute(
            text(f"DELETE FROM {_q('gdag_edges')} "
                 f"WHERE from_id IN (SELECT id FROM {_q('gdag_nodes')} "
                 f"  WHERE json_extract(props, '$.conversation_id') = :cid) "
                 f"OR to_id IN (SELECT id FROM {_q('gdag_nodes')} "
                 f"  WHERE json_extract(props, '$.conversation_id') = :cid)"),
            {"cid": conv_id},
        )
        nodes = db.execute(
            text(f"DELETE FROM {_q('gdag_nodes')} "
                 f"WHERE json_extract(props, '$.conversation_id') = :cid"),
            {"cid": conv_id},
        ).rowcount or 0
        db.execute(
            text(f"DELETE FROM {_q('conversation_run_events')} WHERE run_id IN "
                 f"(SELECT id FROM {_q('conversation_runs')} WHERE conversation_id = :cid)"),
            {"cid": conv_id},
        )
        runs = db.execute(
            text(f"DELETE FROM {_q('conversation_runs')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        ).rowcount or 0
        tasks = db.execute(
            text(f"DELETE FROM {_q('team_tasks')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        ).rowcount or 0
        goals = db.execute(
            text(f"DELETE FROM {_q('goals')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        ).rowcount or 0
        todos = db.execute(
            text(f"DELETE FROM {_q('todo_items')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        ).rowcount or 0
        approvals = db.execute(
            text(f"DELETE FROM {_q('tool_approvals')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        ).rowcount or 0
        grants = db.execute(
            text(f"DELETE FROM {_q('permission_grants')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        ).rowcount or 0
        # 工单是用户资产：只解除关联，不跟着会话一起删。
        db.execute(
            text(f"UPDATE {_q('work_items')} SET conversation_id = NULL, run_id = NULL "
                 f"WHERE conversation_id = :cid"),
            {"cid": conv_id},
        )
        db.execute(
            text(f"UPDATE {_q('conversations')} SET last_read_at = NULL, "
                 f"updated_at = datetime('now','localtime') WHERE id = :cid"),
            {"cid": conv_id},
        )
        db.commit()

    # 3) 附件 / 文件检查点 / 后台工具：会话级副作用一并清掉。
    try:
        attachment_service.remove_conversation(conv_id)
    except Exception:
        logger.debug("清理会话附件失败（忽略）", exc_info=True)
    try:
        from services.agent_zero.services import file_checkpoint_service

        ws_id = str(conv.get("workspace_id") or "")
        if ws_id:
            file_checkpoint_service._prune_conversation(ws_id, conv_id, keep=0)
            file_checkpoint_service._gc_blobs(ws_id)
    except Exception:
        logger.debug("清理会话文件检查点失败（忽略）", exc_info=True)
    try:
        from services.agent_zero.task_layer import tool_jobs

        tool_jobs.kill_conversation(conv_id, reason="会话已清空")
    except Exception:
        logger.debug("终止会话后台工具失败（忽略）", exc_info=True)

    zero_store.append_audit(user, "conversation_clean",
                            args={"conversation_id": conv_id, "nodes": nodes,
                                  "runs": runs, "tasks": tasks, "goals": goals,
                                  "todos": todos, "approvals": approvals,
                                  "grants": grants})
    return {"nodes": int(nodes), "runs": int(runs), "tasks": int(tasks),
            "goals": int(goals), "todos": int(todos),
            "approvals": int(approvals), "grants": int(grants)}


def delete_conversations(conv_ids: List[str]) -> int:
    """批量删除对话，返回实际删掉的条数（逐条走 delete_conversation，保语义一致）。"""
    deleted = 0
    for cid in conv_ids or []:
        try:
            if delete_conversation(cid):
                deleted += 1
        except Exception:
            logger.exception("批量删除对话失败 conv=%s", cid)
    return deleted


def delete_conversation(conv_id: str) -> bool:
    """删除对话（连同其探索态消息节点与临时角色）。

    删除会话根时把整棵会话的栏目一起删掉；删除单个栏目只影响该栏目。
    """
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        # Decide from the stored parent link, not the normalized session_root_id
        # returned by get_conversation(): deleting a child lane must never fan out
        # into deletion of its siblings (or its root).
        row = db.execute(
            text(f"SELECT session_id FROM {_q('conversations')} WHERE id = :cid"),
            {"cid": conv_id},
        ).first()
        is_root = row is not None and not str(row[0] or "").strip()
        child_ids = [str(child[0]) for child in db.execute(
            text(f"SELECT id FROM {_q('conversations')} WHERE session_id = :cid"),
            {"cid": conv_id},
        ).all()] if is_root else []
    if is_root:
        for child_id in child_ids:
            try:
                delete_conversation(child_id)
            except Exception:
                logger.exception("删除栏目失败 conv=%s", child_id)

    old_team_id = ""
    try:
        old_team_id = str((get_conversation(conv_id) or {}).get("team_id") or "")
    except Exception:
        old_team_id = ""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        # 会话内的临时角色：会话没了就销毁（持久角色不受影响）
        db.execute(
            text(f"DELETE FROM {_q('roles')} "
                 f"WHERE scope = 'temporary' AND session_id = :cid"),
            {"cid": conv_id},
        )
        # 先清该对话的运行（事件表随 run 级联）
        db.execute(
            text(f"DELETE FROM {_q('conversation_run_events')} WHERE run_id IN "
                 f"(SELECT id FROM {_q('conversation_runs')} WHERE conversation_id = :cid)"),
            {"cid": conv_id},
        )
        db.execute(
            text(f"DELETE FROM {_q('conversation_runs')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        )
        db.execute(
            text(f"DELETE FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
                 f"AND props->>'stage' = 'exploration'"),
            {"cid": conv_id},
        )
        res = db.execute(
            text(f"DELETE FROM {_q('conversations')} WHERE id = :id"), {"id": conv_id}
        )
        db.commit()
        if res.rowcount:
            from services.agent_zero.task_layer import tool_jobs

            tool_jobs.kill_conversation(conv_id, reason="会话已删除")
        attachment_service.remove_conversation(conv_id)
        if old_team_id:
            try:
                from services.agent_zero.services import team_service

                team_service.cleanup_orphan_participants(old_team_id)
            except Exception:
                logger.debug("清理会话私有参与角色失败（忽略）", exc_info=True)
        return bool(res.rowcount)

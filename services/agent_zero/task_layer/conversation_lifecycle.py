"""Conversation cleanup and deletion lifecycle operations."""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from services.agent_zero.services import attachment_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation

logger = logging.getLogger(__name__)


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
        session_root = conversation.get_conversation(session_id)
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
    if zone == conversation.GLOBAL_AGENT_ZONE:
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

    workspace_id = conversation.validate_conversation_workspace(bound_workspace_id or workspace_id)
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
        run_mode = conversation.last_run_mode()

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
        conversation.append_system_message(conv_id, f"你已加入群聊「{(team or {}).get('name') or team_id}」")
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


def fork_conversation(conv_id: str, *, message_id: Optional[str] = None,
                      user: str = "local",
                      title: Optional[str] = None) -> Dict[str, Any]:
    """从某条消息处分叉出一个新会话。

    - `message_id` 为空：复制整段对话；否则复制到该消息（含）为止。
    - 新会话继承工作区 / 角色 / 团队 / 模型 / 权限 / 运行模式，消息节点换新 id；
    - 附件文件一起复制，删除任一分叉不会影响另一边；
    - 文件检查点不复制：工作区是共享的，文件状态由当前工作区决定。
    """
    src = conversation.get_conversation(conv_id)
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
    new = conversation.create_conversation(
        str(src.get("workspace_id") or ""), user,
        role_id=src.get("role_id"),
        zone=None if src.get("zone") == conversation.GLOBAL_CHAT_ZONE else src.get("zone"),
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
            conversation.delete_conversation(new_id)
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
    return conversation.get_conversation(new_id) or new


def clean_conversation(conv_id: str, user: str = "local") -> Dict[str, int]:
    """彻底清空一个会话：上下文 + 历史消息 + 运行 / 后台任务 / 审批痕迹。

    与 ``clear_context``（只让模型看不到旧上下文，转录仍保留）不同：这里会把
    **所有**消息节点、run 与事件、任务、目标、待办、审批、附件都删掉，会话本身
    （标题 / 群组 / 工作区 / 临时角色）保留，相当于把会话重置成一个新会话。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    conv = conversation.get_conversation(conv_id)
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
        # returned by conversation.get_conversation(): deleting a child lane must never fan out
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
        old_team_id = str((conversation.get_conversation(conv_id) or {}).get("team_id") or "")
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

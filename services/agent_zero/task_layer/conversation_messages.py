"""GDAG-backed conversation message mutations and lookup helpers."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from services.agent_zero.services import attachment_service
from services.agent_zero.stores import zero_store

logger = logging.getLogger(__name__)


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
    from services.agent_zero.task_layer.conversation import get_conversation

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

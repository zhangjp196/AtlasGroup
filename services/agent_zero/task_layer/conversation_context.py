"""Conversation history, context markers, and compaction checkpoints."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer.conversation_constants import (
    CHAT_HISTORY,
    CHAT_HISTORY_FALLBACK_TOKENS,
    CHAT_HISTORY_MAX_TOKENS,
    CHAT_HISTORY_MIN_TOKENS,
    CHAT_HISTORY_SHARE,
    CHECKPOINT_KIND,
    CLEAR_KIND,
)

logger = logging.getLogger(__name__)


def _conversation():
    """Resolve the compatibility facade at call time to avoid an import cycle."""
    from services.agent_zero.task_layer import conversation

    return conversation


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

    conv = _conversation()
    clear_rowid = conv.clear_marker_rowid(conv_id)
    checkpoint = conv.latest_checkpoint(conv_id, after_rowid=clear_rowid)
    # 还有排队 run 等着的消息一律不进上下文：`queued` 标记可能已被认领 / 编辑 /
    # 异常路径清掉，只靠它过滤会让当前 run 提前看到「下一轮」的内容（等于替下一轮
    # 执行了一遍）。以 run 表为准兜底；显式「立即插入」（inject=1）的除外。
    try:
        _still_queued = conv._queued_message_ids(conv_id)
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
            content = conv._build_message_content(
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
    conversation = _conversation()
    conv = conversation.get_conversation(conv_id)
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

    conversation = _conversation()
    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise ValueError(f"对话不存在: {conv_id}")
    if run_service.running_run(conv_id):
        raise RuntimeError("当前会话正在运行，先停止再压缩")
    role = zero_store.get_role(str(conv.get("role_id") or "")) or {}
    models = conversation._chat_models(role) or {"loop": "default", "fallback": "default"}
    messages = conversation._history_messages(conv_id)
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
        conversation.persist_checkpoint(conv, str(result.get("summary") or ""),
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
    conversation = _conversation()
    for row in rows:
        if conversation.clear_context(str(row[0])):
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
        _conversation().persist_checkpoint(conv, summary, str(res.get("source") or ""))
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

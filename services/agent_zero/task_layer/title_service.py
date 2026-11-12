"""会话标题：首轮回复后异步生成，支持手动重命名与重新生成。

参考 OpenCode 的隐藏 title agent：
- 用一个**便宜的模型**、无工具、极小输出（只回一行标题）生成；
- 只读取首条用户消息（重新生成时读取有长度上限的近期对话）；
- 先取第一行非空文本，清理引号 / Markdown，再截到 100 字符；
- 生成过程完全异步，绝不阻塞对话正文；
- 写回前做竞态检查：用户中途手动改名（title_source=user）就放弃覆盖。

标题来源（conversations.title_source）：
  fallback —— 首条用户消息截断出的占位标题
  auto     —— AI 生成
  user     —— 用户手动重命名（自动生成永不覆盖）
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores.zero_store import _q

logger = logging.getLogger(__name__)

MAX_TITLE_LEN = 100
MAX_FIRST_MESSAGE = 2_000
MAX_CONTEXT_CHARS = 8_000
TITLE_TIMEOUT_S = 20.0

SYSTEM_PROMPT = (
    "You generate short, specific session titles. Use the same language as the "
    "conversation. For Chinese aim for 8-20 characters; for other languages "
    "3-10 words. Reply with only the title: no quotes, no Markdown, no "
    "explanation, no trailing punctuation."
)

_TASKS: set = set()
_RUNNING: set = set()


# ── 数据读取 ────────────────────────────────────────────────────────────

def _conv_row(conv_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT id, title, title_source, role_id, model_code "
                 f"FROM {_q('conversations')} WHERE id = :id"),
            {"id": conv_id},
        ).mappings().first()
    return dict(row) if row else None


def get_title(conv_id: str) -> Dict[str, Any]:
    row = _conv_row(conv_id) or {}
    return {"id": conv_id, "title": row.get("title") or "",
            "title_source": row.get("title_source") or ""}


def _messages(conv_id: str, limit: int = 50) -> List[Dict[str, str]]:
    """按时间正序取对话消息（排除 checkpoint / 空正文）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT props->>'content' AS content, props->>'by' AS by, "
                 f"props->>'role_name' AS role_name "
                 f"FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
                 f"AND props->>'stage' = 'exploration' "
                 f"AND COALESCE(props->>'kind', '') <> 'checkpoint' "
                 f"AND COALESCE(props->>'content', '') <> '' "
                 f"ORDER BY created_at DESC, rowid DESC LIMIT :n"),
            {"cid": conv_id, "n": max(1, int(limit))},
        ).mappings().all()
    out = [{"content": str(r["content"] or ""), "by": str(r["by"] or ""),
            "role_name": str(r["role_name"] or "")} for r in rows]
    out.reverse()
    return out


def _first_user(messages: List[Dict[str, str]]) -> str:
    for m in messages:
        if m.get("by") not in ("assistant", "system") and m.get("content"):
            return m["content"]
    return ""


def _first_assistant(messages: List[Dict[str, str]]) -> str:
    for m in messages:
        if m.get("by") == "assistant" and m.get("content"):
            return m["content"]
    return ""


# ── 标题清理 / 提示词 ───────────────────────────────────────────────────

def clean_title(raw: str) -> str:
    """模型输出 → 一行标题（参考 OpenCode：取第一行非空文本并截断）。"""
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:title|标题)\s*[:：]\s*", "", line, flags=re.I)
        line = line.lstrip("#*-• ").strip()
        line = line.strip("`\"'“”‘’「」『』").strip()
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if len(line) > MAX_TITLE_LEN:
            line = line[: MAX_TITLE_LEN - 3].rstrip() + "..."
        return line
    return ""


def _prompt(first_user: str, assistant: str = "") -> str:
    text = (
        "Here is the first user message of the conversation:\n\n"
        f"<message>\n{first_user[:MAX_FIRST_MESSAGE]}\n</message>\n\n"
    )
    if assistant:
        text += (
            "Here is the assistant's first reply:\n\n"
            f"<reply>\n{assistant[:MAX_FIRST_MESSAGE]}\n</reply>\n\n"
        )
    return text + "Reply with only the title (Chinese: 8-20 characters; other languages: 3-10 words)."


def _regenerate_prompt(messages: List[Dict[str, str]]) -> str:
    first = _first_user(messages)
    parts: List[str] = []
    if first:
        parts.append("Original request:\n" + first[:MAX_FIRST_MESSAGE])
    recent: List[str] = []
    for m in messages[-8:]:
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        who = "Assistant" if m.get("by") == "assistant" else "User"
        recent.append(f"{who}: {content[:1_200]}")
    body = "\n\n".join(parts)
    if recent:
        body += "\n\nRecent conversation:\n" + "\n\n".join(recent)
    body = body[-(MAX_CONTEXT_CHARS - 400):]
    return (f"Here is the conversation so far:\n\n<conversation>\n{body}\n"
            "</conversation>\n\nReply with only the title "
            "(Chinese: 8-20 characters; other languages: 3-10 words).")


def _model_code(conv: Dict[str, Any]) -> str:
    """使用角色指定的摘要模型，其他情况沿用对话实际模型。"""
    role_id = str(conv.get("role_id") or "")
    if role_id:
        try:
            from services.agent_zero.stores import zero_store as _zs

            role = _zs.get_role(role_id) or {}
            from services.agent_zero.task_layer.conversation import role_chat_model_code

            summary = str(((role.get("envelope") or {}).get("models") or {}).get("summary") or "").strip()
            code = summary or role_chat_model_code(role)
            if code:
                return code
        except Exception:
            logger.debug("读取标题模型失败（忽略）", exc_info=True)
    code = str(conv.get("model_code") or "").strip()
    if code:
        return code
    try:
        from services.agent_zero.runtime.zero import llm as _llm

        return str(_llm.get_active_model_code() or "default")
    except Exception:
        return "default"


async def _call_model(prompt: str, conv: Dict[str, Any]) -> str:
    from services.agent_zero.runtime.zero import llm as llm_mod

    code = _model_code(conv)
    # zero 模型分层：summary 角色存在时用它；不存在时 ZeroLlm 会回落到 fallback。
    models = {"summary": code, "loop": code, "fallback": code}
    result = await asyncio.wait_for(
        llm_mod.ZeroLlm(models).chat_once(
            "summary",
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=64, timeout=TITLE_TIMEOUT_S),
        timeout=TITLE_TIMEOUT_S + 5.0)
    return str(getattr(result, "content", "") or "")


# ── 写回 ────────────────────────────────────────────────────────────────

def _write_title(conv_id: str, title: str, *, source: str,
                 expected: str, allow_user: bool) -> bool:
    SessionLocal = get_session_local()
    where = "id = :id AND COALESCE(title, '') = :expected"
    if not allow_user:
        where += " AND COALESCE(title_source, '') <> 'user'"
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} SET title = :title, "
                 f"title_source = :source, updated_at = datetime('now','localtime') "
                 f"WHERE {where}"),
            {"id": conv_id, "title": title, "source": source,
             "expected": expected or ""},
        )
        db.commit()
        return bool(res.rowcount)


def rename(conv_id: str, title: str) -> Optional[Dict[str, Any]]:
    """用户手动重命名：标记 user，后续自动生成不再覆盖。"""
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    if not value:
        return None
    value = value[:200]
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} SET title = :title, "
                 f"title_source = 'user', updated_at = datetime('now','localtime') WHERE id = :id"),
            {"title": value, "id": conv_id},
        )
        db.commit()
        if not res.rowcount:
            return None
    return get_title(conv_id)


async def generate(conv_id: str, *, force: bool = False) -> Optional[str]:
    """生成一次标题；成功写回返回标题，任何失败返回 None（静默保留原名）。"""
    conv = _conv_row(conv_id)
    if not conv:
        return None
    source = str(conv.get("title_source") or "")
    current = str(conv.get("title") or "")
    if not force and source != "fallback":
        return None
    messages = _messages(conv_id)
    first = _first_user(messages)
    if not first:
        return None
    prompt = (_regenerate_prompt(messages) if force
              else _prompt(first, _first_assistant(messages)))
    try:
        raw = await _call_model(prompt, conv)
    except Exception as exc:
        logger.info("会话标题生成失败 conv=%s: %s", conv_id, exc)
        return None
    title = clean_title(raw)
    if not title:
        return None
    ok = _write_title(conv_id, title, source="auto", expected=current,
                      allow_user=force)
    if not ok:
        logger.info("会话标题已被用户修改，放弃覆盖 conv=%s", conv_id)
        return None
    logger.info("会话标题已生成 conv=%s title=%s", conv_id, title)
    return title


def schedule(conv_id: str) -> None:
    """首轮完成后异步排一个标题生成任务（不阻塞、不重复）。"""
    if not conv_id or conv_id in _RUNNING:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _RUNNING.add(conv_id)
    task = loop.create_task(_background(conv_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _background(conv_id: str) -> None:
    try:
        await generate(conv_id)
    except Exception:
        logger.debug("后台标题生成失败 conv=%s", conv_id, exc_info=True)
    finally:
        _RUNNING.discard(conv_id)

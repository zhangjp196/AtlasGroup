"""上下文压缩：对齐 OpenCode V2 compaction（packages/core/src/session/compaction.ts）。

和旧实现的差异（旧实现整体删除，不保留双口径）：

1. **一个触发口径**：`ceiling = window - max(15% 窗口, 16000)`。只有上游实测
   prompt 达到 ceiling 才压，不再用「最近几轮增速」预测下一轮——预测把触发点
   在 47%～92% 之间乱甩，而且每轮都可能调一次摘要模型，本身就是卡顿来源。
2. **压缩产物是检查点**：`<conversation-checkpoint><summary>…</summary>` 作为
   一条 user 消息放在 system 之后，后面跟 `keep`（默认 15000）tokens 的原文尾巴。
   摘要不再是 system 消息，也不再和近期原文混在一起。
3. **摘要请求发文本转录**，不重发整段对话。装不下时按「整段 → 截断工具输出 →
   去掉工具输出 → 只留用户文本 → 省略」由旧到新逐级缩短，最新的对话永不省略。
4. **摘要调用的时间有界**：单次硬超时 90s、**不走 llm_chat 的 20 次重试**，模板
   没命中只补一次提醒。调用方在等待期间每 20s 推一次心跳，界面不会假死。
5. **溢出恢复按比例收缩**：400 之后依次按 0.85 / 0.7 / 0.5 / 0.35 缩小目标，
   而不是把「假定窗口」一刀切减半。

失败永远不中断 run：摘要模型不可用 → 确定性摘要（heuristic）；仍然装不下 →
算法硬裁。区别只是丢多少信息，不是能不能继续。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── 常量（对齐压测过的 OpenCode 默认值）──────────────────────────────────
RESERVE_MIN = 16_000            # 窗口太小时至少留 16k（且要求窗口 >= 32k）
RESERVE_SHARE = 0.15            # 否则留 15% 窗口
KEEP_DEFAULT = 15_000           # 检查点旁保留的近期原文
SHRINK_STEPS = (0.85, 0.70, 0.50, 0.35)   # 400 后逐次缩小目标
TRANSCRIPT_CLIP = 2_000         # 转录里被截断的工具输入/输出、用户文本长度
IMAGE_TOKENS = 1_500            # 一张图片按 1.5k token 估
SUMMARY_MAX_TOKENS = 4_096      # 摘要输出上限
SUMMARY_TIMEOUT_S = 90.0        # 单次摘要调用硬超时（无重试）
SUMMARY_OUTPUT_RESERVE = 2_048  # 给摘要正文留的 token
SUMMARY_MAX_CHARS = 8_000
MIN_GAIN_TOKENS = 2_000         # 折叠不到这么多 token 就不值得调摘要模型

# 一级压缩（microcompact）：工具输出是上下文里最容易膨胀、也最可再生的部分。
# 先把保留窗口之外的旧工具输出就地清成一行摘要——不调模型、不打断 prompt cache
# 的前缀语义之外的东西。Claude Code 的 microcompact / OpenCode 的 prune 同思路。
PRUNE_CLIP_CHARS = 400          # 旧工具输出保留的头部字符数
PRUNE_HEADROOM = 0.05           # 清理后至少要比上限低 5%，避免下一轮立刻再压

# 转录缩短级别：整段 / 截断工具输出 / 去掉工具输出 / 只留用户文本 / 省略
OUTPUTS_CLIPPED = 1
OUTPUTS_REMOVED = 2
USER_ONLY = 3
OMITTED = 4
TRANSCRIPT_LEVELS = (0, OUTPUTS_CLIPPED, OUTPUTS_REMOVED, USER_ONLY, OMITTED)

CHECKPOINT_MARKER = "<conversation-checkpoint>"
CHECKPOINT_TAG = "conversation-checkpoint"
LEGACY_MARKERS = ("## 进展摘要（自动压缩）", "## 上下文压缩摘要")

SUMMARY_SYSTEM = (
    "你是上下文压缩器。你的输出会作为检查点交给另一个 agent，让它接着完成工作。"
    "只输出摘要正文，不要解释、不要调用工具、不要续写任务。"
)

SUMMARY_TEMPLATE = """你必须严格按以下格式输出（不适用的条目可省略；不要输出 <template> 标签）：
<template>
## 目标
- [一到两句话：用户想完成什么]

## 要求
- [用户提出的约束、偏好、范围边界；没有就写“（无）”]

## 决定
- [已经做出的决定及原因；没有就写“（无）”]

## 工作状态
### 已完成
- [已完成的子目标；没有就写“（无）”]

### 进行中
- [正在做的子目标；没有就写“（无）”]

### 受阻
- [阻塞点及原因；没有就写“（无）”]

## 下一步
1. [按顺序列出接下来的动作；没有就写“（无）”]

## 相关文件
最多 15 个，只列另一个 agent 继续工作**必须打开**的文件或目录，不要罗列所有读过/改过的文件。没有就写“（无）”。
- `[路径]`：[为什么重要]

## 重要上下文
- [下一个 agent 无法自行恢复的事实；没有就写“（无）”]
</template>"""

SUMMARY_RULES = """规则：
- 每节都要短：单行要点，不要整段复述，不要嵌套列表。
- 保留精确的路径、符号、命令、错误串、URL 与标识符。
- 只保留仍然未解决的问题或请求；已被后续历史回答/解决的不再重复。
- 保留有后果的工作状态：改动是否未提交 / 已提交 / 已推送 / 在评审 / 已合并。
- 不要提及“压缩”“摘要”这件事本身。"""

# 摘要是否“填进模板”：命中任意一个小节标题即算（中英都收，避免模型换语言被判失败）
SUMMARY_HEADINGS = (
    "## 目标", "## 要求", "## 决定", "## 工作状态", "## 下一步", "## 相关文件", "## 重要上下文",
    "## Objective", "## Requirements", "## Decisions", "## Work State", "## Next Move",
    "## Relevant Files", "## Important Context",
)

BACKFILL_NUDGE = (
    "上面的回复没有套用要求的摘要模板。不要调用工具，按模板的小节标题原样输出摘要正文。"
)


# ── 估算 ────────────────────────────────────────────────────────────────

def count_tokens(text: str) -> int:
    """粗略 token 估算：CJK 按字计，其余按 4 字符 ≈ 1 token。"""
    if not text:
        return 0
    cjk = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            cjk += 1
    return cjk + (len(text) - cjk) // 4 + 1


def _message_text(content: Any) -> str:
    """list 型 content（多模态）只取文本部分。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(p.get("text") or "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content or "")


def _image_count(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")


def message_tokens(message: Dict[str, Any]) -> int:
    """单条消息的估算 token（含 tool_calls 参数、图片开销）。"""
    total = 4 + count_tokens(_message_text(message.get("content")))
    total += IMAGE_TOKENS * _image_count(message.get("content"))
    calls = message.get("tool_calls")
    if calls:
        try:
            total += count_tokens(json.dumps(calls, ensure_ascii=False))
        except Exception:
            total += 64
    if message.get("name"):
        total += 4
    return total


def estimate_tokens(messages: Sequence[Dict[str, Any]]) -> int:
    """一组消息的估算 token；与 `message_tokens` 同口径。"""
    return sum(message_tokens(m) for m in (messages or []))


# 上下文都花在哪：调参 / 排查时先看这个，再决定压哪一块。
BREAKDOWN_ROLES = {"system": "system", "user": "user",
                   "assistant": "assistant", "tool": "tool"}


def token_breakdown(messages: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """按角色拆 token：system / user / assistant / tool / image / other / total。

    工具输出通常是长任务里的大头；这个分解就是用来判断「该 prune 还是该压正文」。
    """
    out: Dict[str, int] = {"system": 0, "user": 0, "assistant": 0,
                           "tool": 0, "image": 0, "other": 0}
    for message in messages or []:
        total = message_tokens(message)
        images = IMAGE_TOKENS * _image_count(message.get("content"))
        key = BREAKDOWN_ROLES.get(str(message.get("role") or ""), "other")
        out[key] += max(0, total - images)
        out["image"] += images
    out["total"] = sum(out[k] for k in ("system", "user", "assistant",
                                        "tool", "image", "other"))
    return out


# ── 触发口径 ────────────────────────────────────────────────────────────

def ceiling(window: int, buffer: int = 0) -> int:
    """请求最多能有多大（给回复留出余量）。对齐 OpenCode `calculateCeiling`。

    buffer > 0 且小于窗口时按它留；否则留 `max(15% 窗口, 16000)`——窗口不足
    32k 时只留 15%，否则 16k 会把小窗口吃光。
    """
    window = int(window or 0)
    if window <= 0:
        return 0
    if buffer and 0 < buffer < window:
        return max(1, window - buffer)
    reserve = max(int(window * RESERVE_SHARE),
                  RESERVE_MIN if window >= 2 * RESERVE_MIN else 0)
    return max(1, window - reserve)


async def _policy() -> Dict[str, Any]:
    """读 config.yaml 的压缩策略，缺省值即 OpenCode 默认。"""
    try:
        from services.agent_zero.runtime.zero import llm as llm_mod

        raw = await llm_mod.get_context_policy() or {}
    except Exception:
        raw = {}
    comp = raw.get("compaction") or {}
    out = {
        "auto": bool(comp.get("auto", True)),
        "keep": int(comp.get("keep_tokens") or KEEP_DEFAULT),
        "buffer": int(comp.get("buffer_tokens") or 0),
        "percent": raw.get("context_compress_percent"),
    }
    return out


async def _model_context(models: Optional[Dict[str, Any]]) -> Tuple[Optional[int], int]:
    """模型配置的 (context_window, max_tokens)：一切以本地 LLM 配置为准。"""
    if not isinstance(models, dict) or not models:
        return None, 0
    ordered = [str(models.get(r) or "").strip() for r in ("loop", "fallback", "plan", "summary")]
    ordered += [str(v or "").strip() for v in models.values()]
    for code in ordered:
        if not code:
            continue
        try:
            from services.agent_zero.runtime.zero import llm as llm_mod

            cfg = await llm_mod.get_llm_config(code)
        except Exception:
            continue
        cfg = cfg or {}
        try:
            window = int(cfg.get("context_window") or 0)
        except (TypeError, ValueError):
            continue
        if window > 0:
            try:
                max_tokens = max(0, int(cfg.get("max_tokens") or 0))
            except (TypeError, ValueError):
                max_tokens = 0
            return window, max_tokens
    return None, 0


# ── 检查点 ──────────────────────────────────────────────────────────────

def checkpoint_content(summary: str) -> str:
    """检查点正文：一条 user 消息，带标签，便于识别与增量更新。"""
    body = (summary or "").strip()
    return (f"<{CHECKPOINT_TAG}>\n<summary>\n{body}\n</summary>\n"
            f"</{CHECKPOINT_TAG}>")


def _is_checkpoint(message: Dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    content = str(message.get("content") or "").lstrip()
    return content.startswith(CHECKPOINT_MARKER) or any(
        content.startswith(m) for m in LEGACY_MARKERS)


def checkpoint_summary(message: Dict[str, Any]) -> Optional[str]:
    """从检查点消息里取回摘要正文（兼容旧格式）。"""
    if not _is_checkpoint(message):
        return None
    text = str(message.get("content") or "")
    if "<summary>" in text and "</summary>" in text:
        start = text.index("<summary>") + len("<summary>")
        end = text.index("</summary>", start)
        body = text[start:end].strip()
        if body:
            return body
    for marker in LEGACY_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            return text[idx + len(marker):].strip() or None
    return None


# ── 切分：谁进摘要、谁留原文 ────────────────────────────────────────────

def _recent_start(entries: Sequence[Dict[str, Any]], keep: int) -> int:
    """保留窗口从哪条开始（对齐 OpenCode `recentStart`）。

    1. 从尾部往前填，填到 keep 为止；至少保留最新一条；
    2. 起点对齐到**用户消息**，避免把 assistant 的 tool_calls 与 tool 结果拆开；
    3. 整段都装得下时只保留最后一个 exchange，不把整段对话再抄一遍到摘要旁边。

    返回 0 表示「整段就是一个 exchange（或没有 user）」——由调用方决定是否
    保留首条 user 再折叠其后的执行历史。
    """
    if not entries:
        return 0
    sizes = [message_tokens(m) for m in entries]
    total = 0
    start = len(entries)
    while start > 0 and total + sizes[start - 1] <= keep:
        total += sizes[start - 1]
        start -= 1
    dropped = min(start, len(entries) - 1)

    boundary = -1
    for idx in range(dropped, -1, -1):
        if entries[idx].get("role") == "user":
            boundary = idx
            break
    if boundary > 0:
        return boundary

    latest_user = -1
    for idx in range(len(entries) - 1, -1, -1):
        if entries[idx].get("role") == "user":
            latest_user = idx
            break
    if latest_user > 0:
        return latest_user
    return 0


def _tail_start(entries: Sequence[Dict[str, Any]], keep: int) -> int:
    """纯按 keep 预算从尾部往前填，返回保留窗口的起点。"""
    total = 0
    start = len(entries)
    while start > 0 and total + message_tokens(entries[start - 1]) <= keep:
        total += message_tokens(entries[start - 1])
        start -= 1
    return start


def split_conversation(
    messages: Sequence[Dict[str, Any]], keep: int,
) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                   List[Dict[str, Any]], Optional[str]]]:
    """拆为 (头部 system, 要折叠的历史, 保留的原文尾巴, 既有摘要)。

    返回 None 表示「没有可折叠的历史」（都在 keep 窗口内，或只有一条消息）。
    """
    head: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(messages) and messages[idx].get("role") == "system":
        head.append(messages[idx])
        idx += 1
    body = list(messages[idx:])
    if not body:
        return None

    previous: Optional[str] = None
    if _is_checkpoint(body[0]):
        previous = checkpoint_summary(body[0])
        body = body[1:]
        if not body:
            return None

    keep = max(1, int(keep))
    start = _recent_start(body, keep)
    if start > 0:
        return head, body[:start], body[start:], previous

    # 整段只有一个 exchange（长任务里常见：一条 user + 几十轮工具）：
    # 必须**保留这条 user**（它是当前请求），折叠它之后的执行历史。
    first_user = next((i for i, m in enumerate(body) if m.get("role") == "user"), -1)
    base = first_user if first_user >= 0 else 0
    rest = body[base + 1:]
    cut = _tail_start(rest, keep)
    older = rest[:cut]
    if not older:
        return None
    tail = ([body[base]] if first_user >= 0 else []) + rest[cut:]
    return head, older, tail, previous


# ── 转录：把历史摊成文本（逐级缩短）─────────────────────────────────────

def _clip(value: str, limit: int = TRANSCRIPT_CLIP) -> str:
    if len(value) <= limit:
        return value
    half = max(1, limit // 2)
    omitted = len(value) - limit
    return f"{value[:half]}\n[… 省略 {omitted} 字符 …]\n{value[-half:]}"


def _exchanges(messages: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """按用户消息分组：一条 user + 它之后的 assistant / tool 属于同一个 exchange。"""
    groups: List[List[Dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "user" or not groups:
            groups.append([message])
        else:
            groups[-1].append(message)
    return groups


def _flatten(group: Sequence[Dict[str, Any]], level: int) -> str:
    """一个 exchange 在某个缩短级别下的文本；该级别下整组省略时返回空串。"""
    if level >= OMITTED:
        return ""
    lines: List[str] = []
    for message in group:
        role = str(message.get("role") or "")
        if role == "system":
            continue
        if level >= USER_ONLY and role != "user":
            continue
        text = _message_text(message.get("content")).strip()
        if text:
            if level >= USER_ONLY:
                text = _clip(text)
            speaker = "User" if role == "user" else "Assistant"
            lines.append(f"[{speaker}]: {text}")
        if _image_count(message.get("content")):
            if level < USER_ONLY:
                lines.append("[图片已省略]")
        if role == "assistant" and level < USER_ONLY:
            for call in message.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call, dict) else None
                name = str((fn or {}).get("name") or (call or {}).get("name") or "tool")
                args = (fn or {}).get("arguments", (call or {}).get("arguments", ""))
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args, ensure_ascii=False)
                    except Exception:
                        args = str(args)
                if level >= OUTPUTS_REMOVED:
                    args = _clip(args)
                lines.append(f"[Assistant tool call]: {name}({args})")
        if role == "tool" and level < USER_ONLY:
            label = "Tool error" if message.get("is_error") else "Tool result"
            if level >= OUTPUTS_REMOVED:
                lines.append(f"[{label} omitted]")
            else:
                output = _message_text(message.get("content"))
                if level >= OUTPUTS_CLIPPED:
                    output = _clip(output)
                lines.append(f"[{label}]: {output}")
    return "\n".join(lines)


def build_transcript(messages: Sequence[Dict[str, Any]], room: int) -> str:
    """把要折叠的历史摊成文本，装进 `room` tokens。

    由旧到新逐级缩短：每一轮只把「需要的」最旧那一截降一级，直到装下；最新的
    exchange 允许缩短但永不省略。仍装不下就硬截断（信息必然有损，但请求可发）。
    整段在 level 0 就装得下时只渲染一次（长历史下这能省掉 5 倍的无谓计算）。
    """
    groups = _exchanges(messages)
    if not groups:
        return ""
    renders = [_flatten(g, 0) for g in groups]
    sizes = [count_tokens(r) for r in renders]
    total = sum(sizes)
    newest = len(groups) - 1
    for level in TRANSCRIPT_LEVELS[1:]:
        if total <= room:
            break
        for idx in range(len(groups)):
            if total <= room:
                break
            text = _flatten(groups[idx], level)
            size = count_tokens(text)
            if idx == newest and not text:
                continue
            if not text and level < OMITTED:
                continue
            total += size - sizes[idx]
            sizes[idx] = size
            renders[idx] = text
    text = "\n\n".join(r for r in renders if r)
    if not text:
        # 全被省略（极端输入）：至少给摘要模型一句“历史为空”的交代
        return "（更早的历史过长，已全部省略；请依据既有检查点与近期原文继续。）"
    if count_tokens(text) > room:
        text = _shrink_to_fit(text, room, "\n…[历史转录已截断]")
    return text


# ── 摘要提示词 ──────────────────────────────────────────────────────────

def build_summary_prompt(update: bool) -> str:
    """摘要提示词：首次生成 / 更新既有检查点（对齐 OpenCode buildPrompt）。"""
    shared = [
        "只总结用户和助手**说过、做过**的内容。助手被告知的仓库规范、AGENTS.md、"
        "环境信息等一律不要写——下一个 agent 会另行拿到它们的最新版本。",
        SUMMARY_TEMPLATE,
        SUMMARY_RULES,
        "不要继续任务，不要调用工具。",
        "只输出结构化摘要正文；不要前言、解释或其它评论。",
    ]
    if not update:
        head = ["把上面的对话压缩成一份结构化摘要，交给另一个 agent 接着完成工作。"]
        return "\n\n".join(head + shared)
    head = [
        "把下面“既有检查点”和“新增历史”合并成一份更新后的摘要。",
        "新历史优先于既有检查点：只有明确矛盾、替代、解决或已过时，才删掉旧信息。",
        "把已完成的工作从“进行中”移走；删掉已解决的阻塞与已回答的问题；保留未解决/待办事项。",
        "只输出更新后的 Markdown 小节，不要重复 <conversation-checkpoint> / <summary> "
        "这类包装标签。",
    ]
    return "\n\n".join(head + shared)


def summary_is_filled(text: str) -> bool:
    """摘要是否套用了模板（命中任意小节标题）。"""
    body = (text or "").strip()
    if len(body) < 16:
        return False
    return any(line.strip() in SUMMARY_HEADINGS for line in body.splitlines())


# ── 摘要调用（有界、无重试）─────────────────────────────────────────────

async def _summarize(
    transcript: str,
    previous: Optional[str],
    models: Optional[Dict[str, Any]],
    max_tokens: int,
) -> Tuple[Optional[str], str]:
    """调摘要模型；模板没命中补一次提醒；失败返回 (None, 原因)。

    **不走 `llm_chat` 的重试与降级**：压缩是主循环里的同步等待，20 次重试 × 超时
    会把界面拖成假死。这里单次硬超时，失败立刻交回调用方降级。
    """
    if not models:
        return None, "no_model"
    prompt = build_summary_prompt(previous is not None)
    blocks: List[str] = []
    if previous:
        blocks.append("【既有检查点】\n" + previous[:SUMMARY_MAX_CHARS])
    blocks.append("【新增历史】\n" + (transcript or "（无）"))
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": "\n\n".join(blocks) + "\n\n" + prompt},
    ]
    try:
        from services.agent_zero.runtime.zero import llm as llm_mod

        llm = llm_mod.ZeroLlm(models)
    except Exception as e:  # pragma: no cover - 导入失败只可能是环境损坏
        logger.warning("上下文摘要无法初始化模型: %s", e)
        return None, "error"

    for attempt in range(2):
        try:
            res = await llm.chat_once(
                "summary", messages, temperature=0.0,
                max_tokens=max(512, int(max_tokens)), timeout=SUMMARY_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("上下文摘要调用失败（降级为确定性摘要）: %s", e)
            return None, "error"
        text = str(getattr(res, "content", "") or "").strip()
        if summary_is_filled(text):
            return text[:SUMMARY_MAX_CHARS], "llm"
        if attempt == 0:
            logger.info("上下文摘要未套用模板，补一次提醒")
            messages = [
                *messages,
                {"role": "assistant", "content": text or "（空回复）"},
                {"role": "user", "content": BACKFILL_NUDGE},
            ]
    return None, "invalid"


def _digest(older: Sequence[Dict[str, Any]], previous: Optional[str]) -> str:
    """确定性摘要（摘要模型不可用时）：既有摘要 + 历史要点，有界。"""
    lines: List[str] = []
    if previous:
        lines.append("## 既有检查点")
        lines.append(previous.strip()[:SUMMARY_MAX_CHARS // 2])
    lines.append("## 工作状态")
    lines.append("- 摘要模型不可用；以下为该段历史的要点摘录（较早细节已丢失）。")
    lines.append("")
    lines.append("## 历史要点")
    for message in older[-80:]:
        role = str(message.get("role") or "?")
        text = _message_text(message.get("content")).strip().replace("\n", " ")
        if not text and message.get("tool_calls"):
            try:
                text = json.dumps(message["tool_calls"], ensure_ascii=False)
            except Exception:
                text = "(tool call)"
        if not text:
            continue
        lines.append(f"- {role}: {_clip(text, 400)}")
    return "\n".join(lines)[:SUMMARY_MAX_CHARS]


# ── 组装与硬保证 ────────────────────────────────────────────────────────

def _merge_system(head: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """上游契约：只允许一个 system 且在开头。"""
    sys_msgs = [m for m in head if m.get("role") == "system"]
    if not sys_msgs:
        return []
    return [{"role": "system",
             "content": "\n\n".join(str(m.get("content") or "") for m in sys_msgs)}]


def _shrink_to_fit(text: str, room: int, marker: str) -> str:
    """把 text 缩到 `room` token 以内（末尾追加 marker），按实测比例收敛。"""
    body = text or ""
    if count_tokens(body) <= room:
        return body + marker
    limit = max(1, room - count_tokens(marker) - 4)
    for _ in range(12):
        cur = count_tokens(body[:limit]) if limit < len(body) else count_tokens(body)
        if cur <= limit:
            break
        ratio = limit / max(1, cur)
        limit = max(1, min(len(body), int(limit * ratio * 0.9) if ratio < 1 else int(limit * 1.1)))
    return body[:limit] + marker


def _truncate_oversized(
    messages: Sequence[Dict[str, Any]], budget: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """最后一道保险：**宁可丢内容也必须把请求发出去**。

    从尾部累积到超 budget 即停（最后一条必留）；仍超则截断最后一条正文；
    结果必须含 user 消息。返回 (裁后消息, 被截断条数)。
    """
    msgs = list(messages or [])
    while msgs and msgs[0].get("role") == "tool":
        msgs = msgs[1:]

    head: List[Dict[str, Any]] = []
    body: List[Dict[str, Any]] = []
    for i, m in enumerate(msgs):
        if m.get("role") == "system" and not body and i == 0:
            head.append(m)
        else:
            body.append(m)

    kept: List[Dict[str, Any]] = []
    used = 0
    truncated = 0
    for idx in range(len(body) - 1, -1, -1):
        m = body[idx]
        t = message_tokens(m)
        if not kept or used + t <= budget:
            kept.append(m)
            used += t
        if used >= budget:
            break
    kept.reverse()

    # 保留段开头如果是 tool 结果，说明它的 assistant 调用已被裁掉——悬空的
    # tool 结果会被上游判成「tool result not found」，必须一起丢掉。
    while kept and kept[0].get("role") == "tool":
        kept.pop(0)

    if used > budget and kept:
        last = dict(kept[-1])
        room = max(1, budget - (used - message_tokens(last)))
        content = last.get("content")
        marker = "…[已截断：超出上下文预算]"
        if isinstance(content, str):
            last["content"] = _shrink_to_fit(content, room, marker)
            truncated = 1
        elif isinstance(content, list):
            images = [p for p in content if isinstance(p, dict) and p.get("type") == "image_url"]
            last["content"] = [{"type": "text",
                                "text": _shrink_to_fit(_message_text(content), room, marker)}] + images
            truncated = 1
        kept[-1] = last

    out = [*head, *kept]
    if not out or not any(m.get("role") == "user" for m in out):
        out = [*head, {"role": "user", "content": "（上下文已裁剪至预算上限，请继续。）"}]
        truncated += 1
    return out, truncated


def _assemble(
    head: Sequence[Dict[str, Any]], summary: str,
    tail: Sequence[Dict[str, Any]], budget: int,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """组回消息，并保证装进 budget。返回 (消息, 丢弃的 exchange 数, 硬裁条数)。"""
    merged_head = _merge_system(head)
    checkpoint = {"role": "user", "content": checkpoint_content(summary)}

    groups = _exchanges(tail)
    dropped = 0
    out = [*merged_head, checkpoint, *[m for g in groups for m in g]]
    # 1) 从最旧的 exchange 开始丢，直到装得下（最新的 exchange 永不丢；
    #    当前那条 user 请求也不能丢——它是这一轮要回答的东西）
    while len(groups) > 1 and estimate_tokens(out) > budget:
        rest = groups[1:]
        if (any(m.get("role") == "user" for m in groups[0])
                and not any(m.get("role") == "user" for g in rest for m in g)):
            break
        groups = rest
        dropped += 1
        out = [*merged_head, checkpoint, *[m for g in groups for m in g]]

    # 2) 摘要本身太长 → 收缩检查点正文
    if estimate_tokens(out) > budget:
        others = estimate_tokens(out) - message_tokens(checkpoint)
        room = max(200, budget - others)
        shrunk = _shrink_to_fit(summary, room, "\n…[摘要已截断]")
        checkpoint = {"role": "user", "content": checkpoint_content(shrunk)}
        out = [*merged_head, checkpoint, *[m for g in groups for m in g]]

    # 3) 兜底硬裁（单条消息本身就超窗时才会走到这里）
    hard = 0
    if estimate_tokens(out) > budget:
        before = estimate_tokens(out)
        out, hard = _truncate_oversized(out, max(1, budget))
        logger.warning(
            "上下文压缩后仍超预算（%d > %d），已算法硬裁至 %d tokens（截断 %d 条）",
            before, budget, estimate_tokens(out), hard,
        )
    return out, dropped, hard


# ── 决策 ────────────────────────────────────────────────────────────────

async def decide(
    messages: List[Dict[str, Any]],
    *,
    models: Optional[Dict[str, Any]] = None,
    measured_tokens: Optional[int] = None,
    window_tokens: Optional[int] = None,
    max_tokens: Optional[int] = None,
    budget_tokens: Optional[int] = None,
    policy: Optional[Dict[str, Any]] = None,
    keep_tokens: Optional[int] = None,
    buffer_tokens: Optional[int] = None,
    force: bool = False,
    reason: str = "auto",
) -> Dict[str, Any]:
    """压缩决策（不真正压缩）。

    触发：上游实测 prompt（没有则本地估算）>= `ceiling`。`force=True` 跳过该判断
    （400 溢出恢复、手动压缩）；即便如此，没有可折叠的历史也会拒绝。
    """
    settings = dict(policy) if policy is not None else await _policy()
    keep = max(1, int(keep_tokens if keep_tokens is not None else settings.get("keep") or KEEP_DEFAULT))
    buffer = int(buffer_tokens if buffer_tokens is not None else settings.get("buffer") or 0)
    auto = bool(settings.get("auto", True))

    model_window, model_max = await _model_context(models)
    window = int(window_tokens or model_window or 0)
    current = int(measured_tokens) if measured_tokens and int(measured_tokens) > 0 \
        else estimate_tokens(messages)
    count_source = "usage" if measured_tokens and int(measured_tokens) > 0 else "estimate"

    out: Dict[str, Any] = {
        "should_compress": False,
        "mode": "ceiling",
        "auto": auto,
        "current": current,
        "window": window,
        "max_tokens": int(max_tokens or model_max or 0),
        "budget": 0,
        "buffer": 0,
        "keep": keep,
        "keep_tokens": keep,
        "trigger": 0,
        "growth": 0,
        "projected": current,
        "headroom": 0,
        "count_source": count_source,
        "reason": "",
        "tokens": current,
        "breakdown": token_breakdown(messages),
    }

    if window <= 0:
        out["reason"] = "no_window"
        return out
    if not auto and not force:
        out["reason"] = "disabled"
        return out

    if budget_tokens is not None and int(budget_tokens) > 0:
        cap = int(budget_tokens)
        mode = "budget"
    elif settings.get("percent") is not None:
        # 旧口径：显式百分比仍是「窗口的百分之多少」触发（保留兼容）
        pct = max(0, min(100, int(settings["percent"])))
        cap = max(1, int(window * pct / 100.0))
        mode = "percent"
    else:
        cap = ceiling(window, buffer)
        mode = "ceiling"

    out.update(mode=mode, budget=cap, trigger=cap, buffer=max(0, window - cap),
               headroom=max(0, cap - current))

    if mode != "budget" and not force and current < cap:
        out["reason"] = "under"
        return out

    split = split_conversation(messages, keep)
    if split is None:
        out["reason"] = "nothing"
        return out
    _head, older, _tail, _previous = split
    older_tokens = estimate_tokens(older)
    if not force and older_tokens < MIN_GAIN_TOKENS:
        out["reason"] = "no_gain"
        return out

    out.update(should_compress=True,
               reason=reason if force else ("percent" if mode == "percent" else "auto"),
               older_tokens=older_tokens, min_reclaim=MIN_GAIN_TOKENS)
    return out


# ── 执行 ────────────────────────────────────────────────────────────────

def _tool_fingerprint(message: Dict[str, Any]) -> str:
    content = message.get("content")
    text = content if isinstance(content, str) else _message_text(content)
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def prune_tool_outputs(
    messages: Sequence[Dict[str, Any]], *, clip_chars: int = PRUNE_CLIP_CHARS,
) -> Tuple[List[Dict[str, Any]], int, int, int]:
    """一级压缩：把旧工具输出就地清成一行摘要（不调模型）。

    两步，都只动 `role=tool` 的正文，保持消息条数与 tool_call 配对关系不变：
    1. **重复输出去重**：同一段输出在历史里出现多次时，只保留**最后一次**原文，
       更早的换成一行说明（Agent 循环里重复读同一个文件很常见）；
    2. **截断**：剩下的长输出只留头部 + 原长度。

    返回 (新消息, 省下的 token, 清理条数, 去重条数)。
    """
    last_seen: Dict[str, int] = {}
    for idx, message in enumerate(messages):
        if message.get("role") == "tool":
            last_seen[_tool_fingerprint(message)] = idx

    out: List[Dict[str, Any]] = []
    saved = 0
    count = 0
    deduped = 0
    for idx, message in enumerate(messages):
        if message.get("role") != "tool":
            out.append(message)
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else _message_text(content)
        fingerprint = _tool_fingerprint(message)
        if last_seen.get(fingerprint, idx) > idx:
            pruned = dict(message)
            pruned["content"] = "[与更靠后的一次工具输出完全相同，已省略]"
            saved += message_tokens(message) - message_tokens(pruned)
            deduped += 1
            out.append(pruned)
            continue
        if len(text) <= clip_chars:
            out.append(message)
            continue
        pruned = dict(message)
        pruned["content"] = (
            f"{text[:clip_chars]}\n…[旧工具输出已清理：原 {len(text)} 字符]")
        saved += message_tokens(message) - message_tokens(pruned)
        count += 1
        out.append(pruned)
    return out, saved, count, deduped


async def apply(
    messages: List[Dict[str, Any]],
    *,
    models: Optional[Dict[str, Any]] = None,
    decision: Optional[Dict[str, Any]] = None,
    force_summary: bool = False,
) -> Dict[str, Any]:
    """按决策执行一次压缩；**永不抛错**，任何异常都退化为确定性摘要或硬裁。

    两级：
    1. **prune**：清掉保留窗口之外的旧工具输出。多数长任务到这一步就够
       （工具输出通常是上下文的大头），不调摘要模型、不丢对话结构；
    2. **checkpoint**：prune 之后仍超上限才调摘要模型，生成检查点。
    """
    original = estimate_tokens(messages)
    decision = decision or await decide(messages, models=models)
    budget = max(1, int(decision.get("budget") or 0))
    keep = max(1, int(decision.get("keep") or KEEP_DEFAULT))
    summary_chars = 0
    summary_text = ""
    source = "none"
    kept_recent = 0
    dropped = 0
    hard = 0
    pruned = 0
    deduped = 0

    out: List[Dict[str, Any]] = list(messages)
    changed = False
    try:
        split = split_conversation(messages, keep)
        if split is None:
            decision = {**decision, "reason": decision.get("reason") or "nothing"}
        else:
            head, older, tail, previous = split
            kept_recent = len(tail)
            older, _saved, pruned, deduped = prune_tool_outputs(older)
            pruned_messages = [*_merge_system(head), *older, *tail]
            if (pruned and not force_summary
                    and estimate_tokens(pruned_messages) <= budget * (1.0 - PRUNE_HEADROOM)):
                # 一级压缩就够了：保留全部对话结构，不产生摘要
                out = pruned_messages
                source = "prune"
                changed = True
            else:
                prompt = build_summary_prompt(previous is not None)
                room = max(600, budget - count_tokens(prompt) - SUMMARY_OUTPUT_RESERVE)
                transcript = build_transcript(older, room)
                max_out = min(SUMMARY_MAX_TOKENS,
                              max(512, int(decision.get("max_tokens") or SUMMARY_MAX_TOKENS)))
                summary, source = await _summarize(transcript, previous, models, max_out)
                if not summary:
                    summary, source = _digest(older, previous), "heuristic"
                summary_text = summary
                summary_chars = len(summary)
                out, dropped, hard = _assemble(head, summary, tail, budget)
                changed = True
    except Exception as e:  # 最后一道兜底：压缩本身出错也必须让 run 继续
        logger.warning("上下文压缩异常，降级为硬裁: %s", e)
        out, hard = _truncate_oversized(list(messages), max(1, budget))
        source = "error"
        summary_chars = 0
        changed = True

    result: Dict[str, Any] = {
        "messages": out,
        "compressed": changed,
        "source": source,
        "summary": summary_text,
        "summary_chars": summary_chars,
        "pruned_tool_outputs": pruned,
        "deduped_tool_outputs": deduped,
        "hard_trimmed": hard,
        "dropped_exchanges": dropped,
        "kept_recent": kept_recent,
        "original_tokens": original,
        "tokens": estimate_tokens(out),
        "keep": keep,
        "budget": budget,
        "window": decision.get("window", 0),
        "current": decision.get("current", original),
        "count_source": decision.get("count_source", "estimate"),
        "mode": decision.get("mode", "ceiling"),
        "reason": decision.get("reason", ""),
        "decision": decision,
    }
    return result


async def compress(
    messages: List[Dict[str, Any]],
    *,
    models: Optional[Dict[str, Any]] = None,
    budget: int = 0,
    keep_tokens: Optional[int] = None,
    force: bool = True,
    reason: str = "auto",
) -> Dict[str, Any]:
    """非流式入口：决定 + 压缩一次（溢出恢复等直接用显式 budget）。"""
    decision = await decide(
        messages, models=models, budget_tokens=budget or None,
        keep_tokens=keep_tokens, force=force, reason=reason,
    )
    if not decision.get("should_compress"):
        return {
            "messages": messages, "compressed": False, "source": "none",
            "summary_chars": 0, "hard_trimmed": 0, "dropped_exchanges": 0,
            "kept_recent": 0, "original_tokens": estimate_tokens(messages),
            "tokens": estimate_tokens(messages), "keep": decision.get("keep"),
            "budget": decision.get("budget", 0), "window": decision.get("window", 0),
            "reason": decision.get("reason", ""), "decision": decision,
        }
    return await apply(messages, models=models, decision=decision)




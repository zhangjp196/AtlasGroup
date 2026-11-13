"""机器人安全指令：斜杠命令与纯文字指令的统一解析。

机器人只执行「不影响整个工作台」的会话内动作：

- `/new`：在当前会话下新建一个栏目，并把后续消息切过去；
- `/clear`：只清当前栏目的模型上下文，历史消息保留；
- `/compact`：压缩当前栏目上下文；
- `/stop`：停止当前栏目正在执行的一轮；
- `/help` / `/status`：只读提示。

不能删除会话 / 栏目、不能清空历史、不能切换工作台设置——这些操作只允许在
AtlasX 界面里做。没有斜杠时，短句「新建栏目」「清空上下文」等也会被识别，
并且不限次数；带「请 / 帮我 / 一下」等口语词也能命中。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SAFE_COMMANDS = {"new", "clear", "compact", "stop", "help", "status"}

_ALIASES = {
    "new": (
        "新建栏目", "新栏目", "新话题", "新建话题", "开个栏目", "开个新栏目",
        "加个栏目", "添加栏目", "新建一个栏目", "新建会话", "新会话", "换个话题",
    ),
    "clear": (
        "清空上下文", "清除上下文", "清空记忆", "重置上下文", "忘掉上文",
        "清空当前上下文", "清除当前上下文",
    ),
    "compact": (
        "压缩上下文", "压缩历史", "压缩一下", "压缩当前上下文",
    ),
    "stop": (
        "停止", "停下", "停止执行", "停止当前任务", "取消当前任务", "取消执行",
    ),
    "help": (
        "帮助", "指令", "命令", "支持哪些指令", "有哪些指令", "怎么用",
    ),
    "status": (
        "状态", "栏目状态", "当前栏目", "当前状态",
    ),
}
_EXACT = {word: name for name, words in _ALIASES.items() for word in words}
_POLITE = re.compile(r"^(?:请|请帮我|帮我|麻烦|劳驾|能不能|可以|我想|我要)+")
_TAIL = re.compile(r"(?:吧|啊|呀|呢|哦|谢谢|多谢|一下)+$")
_STRIP = re.compile(r"[\s。！？!?，,、：:；;…~～.]+")


@dataclass(frozen=True)
class BotCommand:
    name: str
    arg: str = ""


def parse(text: str) -> BotCommand | None:
    """把机器人收到的文字解析成安全指令；不是指令时返回 None。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    match = re.match(r"^/([A-Za-z0-9_-]+)(?:\s+([\s\S]*))?$", raw)
    if match:
        name = match.group(1).lower()
        if name not in SAFE_COMMANDS:
            return None
        return BotCommand(name, str(match.group(2) or "").strip()[:200])
    if len(raw) > 60:
        return None
    plain = _STRIP.sub("", raw)
    name = _EXACT.get(plain)
    if not name:
        softened = _TAIL.sub("", _POLITE.sub("", plain))
        softened = _TAIL.sub("", softened)
        name = _EXACT.get(softened)
    if not name:
        return None
    return BotCommand(name)


def help_text() -> str:
    return (
        "可用指令（斜杠或直接发文字都行）：\n"
        "· /new — 在当前会话下新建一个栏目（可带标题：/new 方案讨论）\n"
        "· /clear — 清空当前栏目的上下文（历史消息保留）\n"
        "· /compact — 压缩当前栏目上下文\n"
        "· /stop — 停止当前栏目正在执行的一轮\n"
        "· /status — 查看当前栏目状态\n"
        "· /help — 显示这条说明\n"
        "也可以直接发「新建栏目」「清空上下文」「停止」等短句。"
    )

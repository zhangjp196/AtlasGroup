"""DeepSeek DSML 工具调用修复。

DeepSeek V4 / V3.2 有时把工具调用写成 DSML（DeepSeek Markup Language）文本：

    <｜DSML｜tool_calls>
    <｜DSML｜invoke name="session_manage">
    <｜DSML｜parameter name="action" string="true">clear</｜DSML｜parameter>
    </｜DSML｜invoke>
    </｜DSML｜tool_calls>

OpenAI 兼容网关若不解析它，这段标记会直接出现在正文里，工具也不会执行。
这里做两件事：

- `DsmlStreamFilter`：流式过滤，标记永远不进入可见正文；
- `extract_dsml_calls`：从原始输出里还原成结构化工具调用。

兼容全角 `｜`、ASCII `|`、退化的双竖线 `||DSML||`，以及有 / 无
`tool_calls` 外壳、参数 XML 与直接 JSON 两种写法。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List

# DSML 分隔符：全角 ｜ 或 ASCII |，允许退化成一个或两个竖线。
_TOKEN = r"(?:[|｜]\s*){1,2}DSML(?:\s*[|｜]){1,2}"
_OPEN = re.compile(rf"<\s*{_TOKEN}", re.IGNORECASE)
_CLOSE = re.compile(rf"<\s*/\s*{_TOKEN}", re.IGNORECASE)

# <｜DSML｜invoke name="x"> / <｜DSML｜invoke name="x"/>；name 后允许空白。
_INVOKE_OPEN = re.compile(
    rf"<\s*{_TOKEN}\s*invoke\s+name\s*=\s*(['\"])(.*?)\1\s*/?>",
    re.IGNORECASE | re.DOTALL,
)
# <｜DSML｜parameter name="x" string="true">value</｜DSML｜parameter>
_PARAM = re.compile(
    rf"<\s*{_TOKEN}\s*parameter\s+name\s*=\s*(['\"])(.*?)\1([^>]*)>"
    rf"(.*?)<\s*/\s*{_TOKEN}\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_CLOSE_WRAPPER = re.compile(
    rf"<\s*/\s*{_TOKEN}\s*(?:[A-Za-z_][\w-]*)?calls\s*>", re.IGNORECASE
)
_CLOSE_INVOKE = re.compile(
    rf"<\s*/\s*{_TOKEN}\s*invoke\s*>", re.IGNORECASE
)
_TAG_NAME = re.compile(r"\s*([A-Za-z_][\w-]*)")
# 可能还没收全的标记尾巴：只由 < / | ｜ 和 DSML 字母组成。
_PARTIAL_MARK = re.compile(
    r"<\s*/?\s*(?:[|｜]\s*){0,2}(?:[Dd]\s*)?(?:[Ss]\s*)?(?:[Mm]\s*)?(?:[Ll]\s*)?\s*$"
)


def _tag_after(text: str, pos: int) -> str:
    """标记后面紧跟的标签名（小写）；用于区分 tool_calls / invoke。"""
    match = _TAG_NAME.match(text, pos)
    return (match.group(1) if match else "").lower()


def _param_value(raw: str, attrs: str) -> Any:
    """string="false" 按 JSON 解析；其余按原始字符串（去掉外层空白）。"""
    flag = re.search(r"string\s*=\s*(['\"]?)(true|false)\1", attrs or "", re.IGNORECASE)
    value = raw.strip()
    if flag and flag.group(2).lower() == "false":
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def extract_dsml_calls(text: str) -> List[Dict[str, Any]]:
    """从模型原始输出里提取 DSML 工具调用；没有则返回空列表。"""
    if not text or "DSML" not in text.upper():
        return []
    opens = list(_INVOKE_OPEN.finditer(text))
    calls: List[Dict[str, Any]] = []
    for index, match in enumerate(opens):
        name = match.group(2).strip()
        if not name:
            continue
        end = opens[index + 1].start() if index + 1 < len(opens) else len(text)
        body = text[match.end():end]
        invoke_close = _CLOSE_INVOKE.search(body)
        if invoke_close:
            body = body[:invoke_close.start()]
        wrapper_close = _CLOSE_WRAPPER.search(body)
        if wrapper_close:
            body = body[:wrapper_close.start()]
        args: Dict[str, Any] = {}
        params = list(_PARAM.finditer(body))
        if params:
            for param in params:
                key = param.group(2).strip()
                if key:
                    args[key] = _param_value(param.group(4), param.group(3))
        else:
            stripped = body.strip()
            if stripped.startswith("{"):
                try:
                    parsed = json.loads(stripped)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    args = parsed
        calls.append({
            "id": f"dsml_{uuid.uuid4().hex[:16]}",
            "name": name,
            "arguments": args,
        })
    return calls


class DsmlStreamFilter:
    """把 DSML 标记从流式正文里扣掉，其余文字原样返回。

    标记可能被拆成任意粒度的增量（甚至一个字符一段），所以看到 `<` 时先
    按住可能构成标记的尾巴，确认是完整标记后再整块吞掉。
    """

    def __init__(self) -> None:
        self.pending = ""
        self.in_block = False
        self.in_wrapper = False

    def feed(self, piece: str) -> str:
        if piece:
            self.pending += piece
        out: List[str] = []
        while self.pending:
            if not self.in_block:
                open_match = _OPEN.search(self.pending)
                close_match = _CLOSE.search(self.pending)
                # 没有开头的孤立闭合标记（模型漏了 tool_calls 外壳）直接丢掉。
                if close_match and (open_match is None or close_match.start() < open_match.start()):
                    tag_end = self.pending.find(">", close_match.end())
                    if tag_end < 0:
                        break
                    self.pending = self.pending[tag_end + 1:]
                    continue
                if open_match is None:
                    keep = self._hold_back()
                    if keep:
                        out.append(self.pending[:-keep])
                        self.pending = self.pending[-keep:]
                    else:
                        out.append(self.pending)
                        self.pending = ""
                    break
                # 标记后可能只到了「｜DSML｜」，标签名（tool_calls / invoke）
                # 还没收全：先别决定这是外壳还是单个调用。
                rest = self.pending[open_match.end():]
                if ">" not in rest:
                    break
                if open_match.start() > 0:
                    out.append(self.pending[:open_match.start()])
                self.pending = self.pending[open_match.start():]
                tag = _tag_after(rest, 0)
                self.in_block = True
                self.in_wrapper = tag.endswith("calls")
                continue

            close_re = _CLOSE_WRAPPER if self.in_wrapper else _CLOSE_INVOKE
            close_match = close_re.search(self.pending)
            if close_match is None:
                break
            self.pending = self.pending[close_match.end():]
            self.in_block = False
            self.in_wrapper = False
        return "".join(out)

    def finish(self) -> str:
        """流结束：未闭合的标记按坏输出丢弃，剩余普通文字照常返回。"""
        if self.in_block:
            self.pending = ""
            self.in_block = False
            self.in_wrapper = False
            return ""
        tail, self.pending = self.pending, ""
        return tail

    def _hold_back(self) -> int:
        """返回尾部需要继续等待的字符数：可能是半个 DSML 标记。"""
        index = self.pending.rfind("<")
        if index < 0:
            return 0
        tail = self.pending[index:]
        if len(tail) > 32:
            return 0
        return len(tail) if _PARTIAL_MARK.fullmatch(tail) else 0


def clean_dsml_text(text: str) -> str:
    """非流式场景：去掉正文里的 DSML 标记。"""
    if not text or "DSML" not in text.upper():
        return text
    stream = DsmlStreamFilter()
    return stream.feed(text) + stream.finish()

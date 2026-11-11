"""Magpie：在请求离开本机前做脱敏，回复 / 工具参数在本机还原。

模型只能看到占位符（如 `⟦MG_EMAIL_1⟧`），真实密钥、邮箱、手机号、身份证、
IP 与本机绝对路径留在本机。回复与 function calling 参数在交给 UI / 工具执行
前还原，所以功能行为不变；占位符只是「模型视角的替身」。

实现是**正则 + 可逆映射**，不是不可逆哈希：还原需要原文，映射只存在于本次
LLM 调用的内存里（不落库）。它是尽力而为的本机隐私层，不宣称能识别所有敏感
信息；需要更强保证时应关闭联网或用隔离环境。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from services.agent_zero.stores import settings_store

logger = logging.getLogger(__name__)

SETTINGS_KEY = "magpie"
DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    # 三类开关：密钥 / 个人信息 / 本机路径
    "secrets": True,
    "pii": True,
    "paths": True,
}

# 占位符：⟦MG_<KIND>_<n>⟧。用少见的括号，避免与正常文本冲突。
PLACEHOLDER_RE = re.compile(r"⟦MG_([A-Z]+)_(\d+)⟧")
PLACEHOLDER_OPEN = "⟦MG_"
SKIP_KEYS = frozenset({
    "id", "tool_call_id", "call_id", "role", "type", "name", "image_url",
    "url", "data", "base64", "mime", "mime_type", "filename",
})

NOTE = (
    "隐私说明：本机 magpie 已把敏感信息替换成 ⟦MG_<类型>_<编号>⟧ 占位符。"
    "请把占位符当作原文使用，调用工具时原样传递，不要猜测、改写或展开；"
    "不要输出占位符之外的敏感信息。"
)

_SINCE = time.strftime("%Y-%m-%d %H:%M:%S")
_STATS_LOCK = threading.Lock()
_STATS: Dict[str, Any] = {"redacted_total": 0, "by_kind": {}}


def get_settings() -> Dict[str, Any]:
    saved = settings_store.get(SETTINGS_KEY, {}) or {}
    out = dict(DEFAULT_SETTINGS)
    if isinstance(saved, dict):
        out.update({k: v for k, v in saved.items() if v is not None})
    for key in ("enabled", "secrets", "pii", "paths"):
        out[key] = bool(out.get(key, True))
    return out


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    current = get_settings()
    for key in DEFAULT_SETTINGS:
        if key in patch:
            current[key] = bool(patch[key])
    settings_store.put(SETTINGS_KEY, current)
    return current


def stats() -> Dict[str, Any]:
    with _STATS_LOCK:
        return {
            "redacted_total": int(_STATS["redacted_total"]),
            "by_kind": dict(_STATS["by_kind"]),
            "since": _SINCE,
        }


def _count(kind: str, amount: int = 1) -> None:
    with _STATS_LOCK:
        _STATS["redacted_total"] = int(_STATS["redacted_total"]) + amount
        by_kind = _STATS["by_kind"]
        by_kind[kind] = int(by_kind.get(kind, 0)) + amount


# ── 规则 ────────────────────────────────────────────────────────────────

_SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("PRIVATEKEY", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.S)),
    ("SECRET", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b")),
    ("SECRET", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("SECRET", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("SECRET", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("SECRET", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SECRET", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("SECRET", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
)
# key=value 只替换值，保留变量名（否则模型改配置文件时看不懂字段）。
_SECRET_ASSIGN = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd)\b"
    r"\s*[:=]\s*)([\"']?)([^\s\"',;]{8,})\2")

_PII_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("EMAIL", re.compile(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("PHONE", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("PHONE", re.compile(r"(?<![\w+])\+\d{7,15}(?!\d)")),
    ("ID", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("IP", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")),
)

_PATH_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"(?<![\w])/(?:Users|home)/[^\s\"'`<>|，。；：、！？（）【】「」]+"),
    re.compile(r"(?i)\b[A-Z]:\\{1,2}(?:Users|Documents and Settings)\\{1,2}"
               r"[^\s\"'`<>|，。；：、！？（）【】「」]+"),
)


class Redactor:
    """一次 LLM 调用的可逆映射。"""

    def __init__(self, settings: Optional[Dict[str, Any]] = None,
                 *, count_stats: bool = True):
        self.settings = settings or get_settings()
        self.enabled = bool(self.settings.get("enabled"))
        self.count_stats = count_stats
        self._tokens: Dict[Tuple[str, str], str] = {}
        self._values: Dict[str, str] = {}
        self._seq: Dict[str, int] = {}
        self.count = 0
        self.by_kind: Dict[str, int] = {}

    def _token(self, kind: str, value: str) -> str:
        key = (kind, value)
        existing = self._tokens.get(key)
        if existing:
            return existing
        seq = self._seq.get(kind, 0) + 1
        self._seq[kind] = seq
        token = f"⟦MG_{kind}_{seq}⟧"
        self._tokens[key] = token
        self._values[token] = value
        return token

    def _record(self, kind: str) -> None:
        self.count += 1
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        if self.count_stats:
            _count(kind)

    def redact_text(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        if text.startswith(PLACEHOLDER_OPEN) and len(text) < 48:
            return text
        out = text
        if self.settings.get("secrets"):
            for kind, pattern in _SECRET_PATTERNS:
                out = pattern.sub(lambda m, k=kind: self._hit(k, m.group(0)), out)

            def _assign(m: re.Match) -> str:
                value = m.group(3)
                if value.startswith(PLACEHOLDER_OPEN):
                    return m.group(0)
                self._record("SECRET")
                return f"{m.group(1)}{m.group(2)}{self._token('SECRET', value)}{m.group(2)}"

            out = _SECRET_ASSIGN.sub(_assign, out)
        if self.settings.get("paths"):
            for pattern in _PATH_PATTERNS:
                out = pattern.sub(lambda m: self._hit("PATH", m.group(0)), out)
        if self.settings.get("pii"):
            for kind, pattern in _PII_PATTERNS:
                out = pattern.sub(lambda m, k=kind: self._hit(k, m.group(0)), out)
        return out

    def _hit(self, kind: str, value: str) -> str:
        # 已经是占位符的值不再套娃（否则还原只解一层）。
        if value.startswith(PLACEHOLDER_OPEN):
            return value
        self._record(kind)
        return self._token(kind, value)

    def redact_obj(self, value: Any, key: str = "") -> Any:
        if not self.enabled:
            return value
        if isinstance(value, str):
            if key in SKIP_KEYS or value.startswith("data:"):
                return value
            return self.redact_text(value)
        if isinstance(value, list):
            return [self.redact_obj(item, key) for item in value]
        if isinstance(value, dict):
            return {k: self.redact_obj(v, str(k)) for k, v in value.items()}
        return value

    def restore_text(self, text: str) -> str:
        if not self._values or not text:
            return text
        return PLACEHOLDER_RE.sub(
            lambda m: self._values.get(m.group(0), m.group(0)), text)

    def restore_obj(self, value: Any) -> Any:
        if not self._values:
            return value
        if isinstance(value, str):
            return self.restore_text(value)
        if isinstance(value, list):
            return [self.restore_obj(item) for item in value]
        if isinstance(value, dict):
            return {k: self.restore_obj(v) for k, v in value.items()}
        return value

    def stream(self) -> "StreamRestorer":
        return StreamRestorer(self)


class StreamRestorer:
    """流式增量还原：占位符可能被切成多个 delta，先缓冲到闭合再替换。"""

    MAX_PENDING = 160

    def __init__(self, redactor: Redactor):
        self.redactor = redactor
        self.buf = ""

    def feed(self, piece: str) -> str:
        if not piece or not self.redactor._values:
            return piece
        self.buf += piece
        out: List[str] = []
        while True:
            pos = self.buf.find(PLACEHOLDER_OPEN)
            if pos < 0:
                keep = self._tail_prefix()
                safe = self.buf[:len(self.buf) - keep]
                self.buf = self.buf[len(self.buf) - keep:]
                out.append(self.redactor.restore_text(safe))
                break
            if pos > 0:
                out.append(self.redactor.restore_text(self.buf[:pos]))
                self.buf = self.buf[pos:]
            match = PLACEHOLDER_RE.match(self.buf)
            if match:
                out.append(self.redactor.restore_text(match.group(0)))
                self.buf = self.buf[match.end():]
                continue
            if len(self.buf) > self.MAX_PENDING:
                out.append(self.buf)
                self.buf = ""
            break
        return "".join(out)

    def flush(self) -> str:
        rest, self.buf = self.buf, ""
        return self.redactor.restore_text(rest)

    def _tail_prefix(self) -> int:
        for size in range(min(len(self.buf), len(PLACEHOLDER_OPEN) - 1), 0, -1):
            if self.buf.endswith(PLACEHOLDER_OPEN[:size]):
                return size
        return 0


def redact_messages(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Redactor]:
    """返回（发给模型的脱敏消息, 本次调用的还原器）。"""
    redactor = Redactor()
    if not redactor.enabled or not messages:
        return messages, redactor
    try:
        masked = redactor.redact_obj(messages)
    except Exception:
        logger.warning("magpie 脱敏失败，本次不脱敏", exc_info=True)
        return messages, Redactor({"enabled": False}, count_stats=False)
    if not redactor.count:
        return messages, redactor
    note = NOTE
    for item in masked:
        if str(item.get("role") or "") != "system":
            continue
        content = item.get("content")
        if isinstance(content, list):
            content.append({"type": "text", "text": f"\n\n{note}"})
        else:
            item["content"] = f"{content or ''}\n\n{note}".strip()
        break
    else:
        masked = [{"role": "system", "content": note}] + masked
    return masked, redactor


def test_redact(text: str) -> Dict[str, Any]:
    """界面预览：不写入全局统计。"""
    redactor = Redactor(count_stats=False)
    masked = redactor.redact_text(str(text or ""))
    return {
        "masked": masked,
        "redacted": redactor.count,
        "by_kind": dict(redactor.by_kind),
    }


def status() -> Dict[str, Any]:
    return {
        "enabled": bool(get_settings().get("enabled")),
        "settings": get_settings(),
        "stats": stats(),
        "categories": [
            {"key": "secrets", "label": "密钥 / Token"},
            {"key": "pii", "label": "邮箱 / 手机号 / 身份证 / IP"},
            {"key": "paths", "label": "本机绝对路径"},
        ],
    }

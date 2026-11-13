"""AtlasX 运行时 LLM 客户端（零预设执行模式的模型通道）。

- LLM 配置：项目内 config/agent_zero/config.yaml 的 `llm` 段（独立部署，
  不再经平台网关读取模型库）；可用环境变量
  `AGENT_ZERO_LLM_BASE_URL` / `AGENT_ZERO_LLM_API_KEY` / `AGENT_ZERO_LLM_MODEL`
  / `AGENT_ZERO_LLM_PROTOCOL` 覆盖。
- 调用：经 pydantic-ai v2（OpenAIChatModel / OpenAIResponsesModel /
  AnthropicModel）访问兼容端点，协议可选：`openai-chat`（默认，
  /chat/completions）、`openai-response`（/responses）或 `anthropic`
  （Anthropic Messages，/v1/messages）；均支持 tools function calling；
- 模型分层与降级：zero.models {plan, loop, summary, fallback}，调用失败降级 fallback。
- 多模型：llm.models 以 code 为键保存多个配置档案，llm.active_model 指定
  默认使用的档案（空 / default 表示顶层默认模型）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from services.agent_zero.version import APP_NAME, APP_VERSION
from services.agent_zero.runtime.zero.dsml import (
    DsmlStreamFilter,
    clean_dsml_text,
    extract_dsml_calls,
)
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from openai import AsyncOpenAI
from pydantic_ai.messages import (
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    SystemPromptPart,
    TextContent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition

try:  # pydantic-ai v2 默认 HTTP 客户端；缺失时退回项目自带的 httpx
    import httpx2 as _httpx2
except ImportError:  # pragma: no cover
    _httpx2 = None

logger = logging.getLogger(__name__)


class LlmError(RuntimeError):
    """LLM 调用失败。

    ``status_code`` 供上下文超长判定使用；``kind`` 给重试策略分类：
    network / timeout / rate_limit / upstream / overflow / auth / http / unknown。
    """

    def __init__(self, message: str, status_code: Optional[int] = None,
                 kind: str = "", retry_after: Optional[float] = None):
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind or "unknown"
        self.retry_after = retry_after


# 上下文窗口溢出的标记：压缩本身要调 summary 模型，若那次也 400，
# 再进压缩就是无限递归。用 contextvar 而非模块变量——并发任务间必须隔离。
_in_overflow_recovery: ContextVar[bool] = ContextVar("zero_in_overflow_recovery", default=False)

# 进程内 LLM 错误计数（/app/metrics 与诊断包使用；重启归零）
_error_stats: Dict[str, Any] = {"total": 0, "retries": 0, "by_kind": {}}
_error_lock = threading.Lock()

# LLM 配置快照缓存：每次 LLM 请求都会解析配置，避免重复 YAML 解析 + sqlite 查询
_SECTION_TTL_S = 0.5
_SECTION_CACHE: Dict[str, Any] = {"key": None, "value": {}, "at": 0.0}

# 进程内 HTTP 连接池：按 provider（base_url + 代理设置）复用连接。
# 旧实现每次请求 new AsyncClient（DNS + TCP + TLS 全重来），多轮工具循环里非常贵。
_http_pool: Dict[Any, Any] = {}
_http_pool_lock = threading.Lock()


def error_stats() -> Dict[str, Any]:
    with _error_lock:
        return {
            "total": int(_error_stats["total"]),
            "retries": int(_error_stats["retries"]),
            "by_kind": dict(_error_stats["by_kind"]),
        }


def _record_error(kind: str) -> None:
    with _error_lock:
        _error_stats["total"] += 1
        by = _error_stats["by_kind"]
        by[kind] = int(by.get(kind, 0)) + 1


def _record_retry() -> None:
    with _error_lock:
        _error_stats["retries"] += 1

# 本轮运行选择的思考级别（OpenAI-compatible reasoning effort）。
# 由 worker 在 run 开始前设置；同一 task 内的主循环 / 子代理 / 压缩都会继承。
_reasoning_effort: ContextVar[str] = ContextVar("zero_reasoning_effort", default="")

# 本轮对话的稳定会话 ID：OpenCode Go 建议客户端带 x-opencode-session，
# 便于它做路由与 prompt cache。其它服务不收这个头也没影响。
_session_id: ContextVar[str] = ContextVar("zero_session_id", default="")


def set_session_id(value: Any) -> None:
    _session_id.set(str(value or "").strip())


def _request_headers(base_url: str = "") -> Dict[str, str]:
    """额外请求头（OpenCode 端点带 UA / 会话 ID）；鉴权头由 OpenAI SDK 负责。"""
    headers: Dict[str, str] = {}
    base = str(base_url or "").lower()
    if "opencode.ai" in base:
        headers["User-Agent"] = f"{APP_NAME}/{APP_VERSION}"
        sid = _session_id.get()
        if sid:
            headers["x-opencode-session"] = sid
    return headers


def set_reasoning_effort(level: Any) -> None:
    """设置当前运行任务的思考级别（空字符串 = 使用模型默认）。"""
    _reasoning_effort.set(str(level or "").strip())


def get_reasoning_effort() -> str:
    return str(_reasoning_effort.get() or "").strip()


def _llm_section() -> Dict[str, Any]:
    """读取 config.yaml 的 `llm` 段，并合并 sqlite 里的本地设置覆盖。

    结果按（配置文件 mtime + 设置版本号）缓存 0.5s：LLM 每次请求都会读配置，
    过去这里是「YAML 解析 + sqlite 查询」——多会话并发时既慢，又可能被库锁
    拖住。缓存同时让同一次请求里的多处解析看到**同一份快照**，不会出现
    「取模型名时是 A、真正调用时是 B」的撕裂。
    """
    from services.shared.project_paths import get_project_root
    from services.shared.yaml_config import load_service_config

    root = str(get_project_root())
    cfg_path = os.environ.get("AI_PLATFORM_CONFIG_YAML") or os.path.join(
        root, "config", "agent_zero", "config.yaml"
    )
    try:
        mtime = os.stat(cfg_path).st_mtime_ns
    except OSError:
        mtime = 0
    version = 0
    try:
        from services.agent_zero.stores import settings_store

        version = settings_store.llm_version()
    except Exception:
        pass
    key = (cfg_path, mtime, version)
    now = time.monotonic()
    cached = _SECTION_CACHE
    if cached.get("key") == key and now - float(cached.get("at") or 0) < _SECTION_TTL_S:
        return dict(cached.get("value") or {})

    try:
        data = load_service_config(cfg_path) or {}
    except Exception as e:  # pragma: no cover
        logger.warning("读取 LLM 配置失败 %s: %s", cfg_path, e)
        data = {}
    sec = dict(data.get("llm") or {})
    try:
        override = settings_store.llm_override()
        for name, value in override.items():
            if value not in (None, ""):
                sec[name] = value
    except Exception:
        logger.debug("读取本地 LLM 设置失败（使用配置文件）", exc_info=True)
    _SECTION_CACHE.update(key=key, value=sec, at=now)
    return dict(sec)


def _models_map(sec: Dict[str, Any]) -> Dict[str, Any]:
    """llm.models 规范化：只接受按 code 索引的映射，忽略其它形状。"""
    models = sec.get("models") or {}
    return models if isinstance(models, dict) else {}


def _subscriptions_map(sec: Dict[str, Any]) -> Dict[str, Any]:
    """llm.subscriptions 规范化：订阅按 id 索引，保存共享的端点与密钥。"""
    subs = sec.get("subscriptions") or {}
    return subs if isinstance(subs, dict) else {}


# 订阅可以提供的共享配置。模型档案里的同名字段仍然优先。
_SUBSCRIPTION_CONFIG_FIELDS = (
    "base_url", "api_base", "api_key", "protocol", "context_window",
    "max_tokens", "vision", "thinking_levels", "trust_env",
)


def get_active_model_code(sec: Optional[Dict[str, Any]] = None) -> str:
    """当前选中的模型档案 code；无效或未配置时返回 ``default``。"""
    sec = _llm_section() if sec is None else sec
    code = str(sec.get("active_model") or "").strip()
    if code and code != "default" and code not in _models_map(sec):
        return "default"
    return code or "default"


def _resolve_llm_config(llm_code: str, *, use_active: bool = True) -> Dict[str, Any]:
    """按 llm_code 解析最终 LLM 配置（默认段 + llm.models[code] 覆盖 + 环境变量）。

    ``use_active`` 仅在 code 为空 / ``default`` 时生效：请求默认模型时使用
    ``llm.active_model`` 选中的档案。列出 default 档案本身时传 False。
    """
    sec = _llm_section()
    base = dict(sec)
    models = _models_map(base)
    subscriptions = _subscriptions_map(base)
    base.pop("models", None)
    base.pop("subscriptions", None)
    active = str(base.pop("active_model", "") or "").strip()

    env_base = (os.environ.get("AGENT_ZERO_LLM_BASE_URL") or "").strip()
    env_key = os.environ.get("AGENT_ZERO_LLM_API_KEY")
    env_model = os.environ.get("AGENT_ZERO_LLM_MODEL")
    env_protocol = (os.environ.get("AGENT_ZERO_LLM_PROTOCOL") or "").strip()
    if env_base:
        base["base_url"] = env_base
    if env_key is not None:
        base["api_key"] = env_key
    if env_model:
        base["model"] = env_model
    if env_protocol:
        base["protocol"] = env_protocol

    code = (llm_code or "").strip()
    if code == "default":
        code = ""
    if use_active and not code and active and active != "default" and active in models:
        code = active
    entry = models.get(code) if code else None
    if isinstance(entry, str):
        base["model"] = entry
    elif isinstance(entry, dict):
        # 订阅是共享的 Base URL / Key：先铺订阅配置，再让模型档案覆盖。
        # 模型档案里的空字符串表示「沿用」，不能把订阅配置抹掉。
        sub_id = str(entry.get("subscription_id") or entry.get("channel_id") or "").strip()
        sub = _entry_dict(subscriptions.get(sub_id)) if sub_id else {}
        protocol_hint = _normalize_protocol(
            entry.get("protocol") or sub.get("protocol") or base.get("protocol"))
        sub_base = _subscription_base_url(sub, protocol_hint)
        sub_values: Dict[str, Any] = {}
        for name in _SUBSCRIPTION_CONFIG_FIELDS:
            if name in ("base_url", "api_base"):
                value = sub_base or sub.get(name)
            else:
                value = sub.get(name)
            sub_values[name] = value
            if value not in (None, ""):
                base[name] = value
        for name, value in entry.items():
            if value in (None, "") and name in _SUBSCRIPTION_CONFIG_FIELDS and sub_values.get(name):
                continue
            # 密钥只有一个来源：挂了订阅的模型，密钥永远取订阅上的。
            # 老版本的档案可能各自留着一份 key（当时模型卡片有密钥输入框），
            # 若让它覆盖订阅，用户改订阅 key 就会「看起来没生效」。
            if (name == "api_key" and sub_id
                    and str(sub_values.get("api_key") or "").strip()):
                continue
            base[name] = value
    if not code:
        # 默认模型也可以挂到订阅下；订阅只补齐默认段没写的字段。
        sub_id = str(base.get("subscription_id") or base.get("channel_id") or "").strip()
        sub = _entry_dict(subscriptions.get(sub_id)) if sub_id else {}
        protocol_hint = _normalize_protocol(
            base.get("protocol") or sub.get("protocol"))
        sub_base = _subscription_base_url(sub, protocol_hint)
        if sub_base:
            # 默认段的 base_url 可能是旧保存值（OpenAI 端点）；订阅指定了
            # Anthropic 端点时必须以订阅为准，否则 Claude 会打到错误路径。
            base["base_url"] = sub_base
            base["api_base"] = sub_base
        sub_key = str(sub.get("api_key") or "").strip()
        if sub_key:
            # 与模型档案同理：默认段历史遗留的 top-level key 不能盖住订阅 key，
            # 否则「改订阅密钥」对当前默认模型不生效。
            base["api_key"] = sub_key
        for name in _SUBSCRIPTION_CONFIG_FIELDS:
            if name in ("base_url", "api_base", "api_key"):
                continue
            if base.get(name) in (None, "") and sub.get(name) not in (None, ""):
                base[name] = sub[name]
    if not code and base.get("hidden_default"):
        # 用户删除了默认模型/渠道：不再回落到 config.yaml 的 base 配置。
        return {
            "code": code or "default",
            "concurrency": _int0(base.get("concurrency")) or 2,
            "model": "",
            "base_url": "",
            "api_base": "",
            "api_key": "EMPTY",
            "context_window": 0,
            "max_tokens": 0,
            "protocol": _normalize_protocol(base.get("protocol")),
            "vision": False,
            "thinking_levels": [],
            "trust_env": False,
        }
    base_url = str(base.get("base_url") or base.get("api_base") or "").rstrip("/")
    return {
        # 并发限流按「解析后的档案 code」分桶：同一档案的主模型 / 摘要 / 压缩
        # 共用同一个槽位池；切换默认模型后自然落到新档案的池子。
        # 不存在的 code 实际用的是默认段配置，归到 default 池，避免同一模型
        # 因为别名不同而绕开并发上限。
        "code": code if (code and entry is not None) else "default",
        "concurrency": _int0(base.get("concurrency")) or 2,
        "model": str(base.get("model") or ""),
        "base_url": base_url,
        "api_base": base_url,
        "api_key": str(base.get("api_key") or "EMPTY"),
        "context_window": int(base.get("context_window") or 0),
        "max_tokens": int(base.get("max_tokens") or 0),
        "protocol": _normalize_protocol(base.get("protocol")),
        "vision": bool(base.get("vision")),
        "thinking_levels": normalize_thinking_levels(base.get("thinking_levels")),
        "trust_env": bool(base.get("trust_env")),
        # 每个模型档案可单独覆盖重试 / 超时 / 连接池：同一台机器上不同渠道
        # （慢推理 vs 快模型）互不牵连。未填时回落到 llm 段全局值。
        "retry_attempts": base.get("retry_attempts"),
        "retry_backoff_s": base.get("retry_backoff_s"),
        "retry_deadline_s": base.get("retry_deadline_s"),
        "connect_timeout_s": base.get("connect_timeout_s"),
        "request_timeout_s": base.get("request_timeout_s"),
    }


def _entry_dict(entry: Any) -> Dict[str, Any]:
    if isinstance(entry, str):
        return {"model": entry}
    return dict(entry) if isinstance(entry, dict) else {}


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_api_key(value: Any) -> bool:
    key = str(value or "").strip()
    return bool(key and key != "EMPTY")


# 模型协议：openai-chat（默认，/chat/completions）、openai-response（/responses）
# 与 anthropic（Anthropic Messages，/v1/messages）。
_PROTOCOL_RESPONSE = "openai-response"
_PROTOCOL_CHAT = "openai-chat"
_PROTOCOL_ANTHROPIC = "anthropic"

def _normalize_protocol(value: Any) -> str:
    """归一化协议名：openai-chat / openai-response / anthropic，其它回落 chat。"""
    s = str(value or "").strip().lower().replace("_", "-")
    s = "-".join(s.split())
    if s in ("anthropic", "anthropic-messages", "messages", "message",
             "claude", "claude-messages", "anthropic-api"):
        return _PROTOCOL_ANTHROPIC
    if s in ("openai-response", "openai-responses", "responses", "response",
            "responses-api", "responses-api-chat", "openai-responses-api"):
        return _PROTOCOL_RESPONSE
    return _PROTOCOL_CHAT

def normalize_thinking_levels(value: Any) -> List[str]:
    """思考级别列表：去空白、去重、限制数量与长度；非列表返回空。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    for raw in value:
        level = str(raw or "").strip()
        if not level or len(level) > 40 or level in out:
            continue
        out.append(level)
        if len(out) >= 16:
            break
    return out

def _protocol_of(cfg: Dict[str, Any]) -> str:
    return _normalize_protocol(cfg.get("protocol"))


def _normalize_anthropic_base_url(value: Any) -> str:
    """Anthropic SDK 会自己拼 `/v1/messages`，这里把用户常见写法归一化。

    用户经常直接粘贴完整的 `.../v1` 或 `.../v1/messages`（和 OpenAI 的
    Base URL 习惯一致）；原样交给 Anthropic SDK 会变成 `/v1/v1/messages`。
    因此先去掉末尾的版本 / 资源路径，再让 SDK 追加标准路径。
    """
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    lower = base.lower()
    for suffix in ("/v1/messages", "/messages", "/v1"):
        if lower.endswith(suffix):
            return base[:-len(suffix)].rstrip("/")
    return base


def _infer_anthropic_base_url(base_url: Any) -> str:
    """把 OpenCode 的 OpenAI 兼容 Base URL 换成对应的 Anthropic 端点。

    OpenCode 的同一订阅提供多套协议端点：
      .../inference/openai/v1      → .../inference/anthropic
      .../inference/go/openai/v1   → .../inference/go/anthropic
    其它服务商不做猜测，由订阅里的 `anthropic_base_url` 显式指定。
    """
    base = str(base_url or "").strip().rstrip("/")
    lower = base.lower()
    if "opencode.ai/inference/go/openai" in lower:
        return "https://opencode.ai/inference/go/anthropic"
    if "opencode.ai/inference/openai" in lower:
        return "https://opencode.ai/inference/anthropic"
    return ""


def _subscription_base_url(sub: Dict[str, Any], protocol: str) -> str:
    """按协议选择订阅端点；Anthropic 独立端点优先于通用 Base URL。"""
    base = str(sub.get("base_url") or "").rstrip("/")
    if protocol == _PROTOCOL_ANTHROPIC:
        own = str(sub.get("anthropic_base_url") or "").strip()
        if not own:
            own = _infer_anthropic_base_url(base)
        if own:
            return _normalize_anthropic_base_url(own)
    return base


async def get_llm_config(llm_code: str) -> Dict[str, Any]:
    """按 code 解析 LLM 配置（项目内配置，无网络请求）。"""
    cfg = _resolve_llm_config(llm_code)
    if not cfg.get("model") or not cfg.get("base_url"):
        raise LlmError(
            "LLM 配置不完整：请在 config/agent_zero/config.yaml 的 llm 段配置 "
            "base_url 与 model（或用 AGENT_ZERO_LLM_BASE_URL / AGENT_ZERO_LLM_MODEL 覆盖）"
        )
    return cfg


def get_model_name(llm_code: str) -> str:
    """档案实际调用的模型名（用于 UI 展示），解析失败时返回空串。"""
    try:
        return str(_resolve_llm_config(llm_code).get("model") or "")
    except Exception:
        return ""


def model_supports_vision(llm_code: str) -> bool:
    """该模型档案是否开启图片支持（解析失败按不支持处理）。"""
    try:
        return bool(_resolve_llm_config(llm_code).get("vision"))
    except Exception:
        return False


def get_active_vision() -> bool:
    """当前选中模型是否支持图片。"""
    try:
        return model_supports_vision(get_active_model_code())
    except Exception:
        return False


def get_thinking_levels_for(llm_code: str) -> List[str]:
    """指定模型档案的思考级别列表（会话级模型切换用）。"""
    try:
        return normalize_thinking_levels(
            _resolve_llm_config(llm_code or get_active_model_code()).get("thinking_levels"))
    except Exception:
        return []


def list_model_profiles() -> List[Dict[str, Any]]:
    """列出所有模型档案（默认模型 + llm.models），不返回明文密钥。

    ``model`` / ``base_url`` / ``context_window`` / ``max_tokens`` 是档案自己
    配置的值（空表示继承默认），``effective_*`` 是实际调用时解析出的值。
    """
    sec = _llm_section()
    models = _models_map(sec)
    subscriptions = _subscriptions_map(sec)
    active_code = get_active_model_code(sec)

    top_base = str(sec.get("base_url") or sec.get("api_base") or "").rstrip("/")
    top_model = str(sec.get("model") or "")
    top_key = str(sec.get("api_key") or "")
    top_cw = _int0(sec.get("context_window"))
    top_mt = _int0(sec.get("max_tokens"))
    top_vision = bool(sec.get("vision"))
    top_thinking = normalize_thinking_levels(sec.get("thinking_levels"))
    top_concurrency = _int0(sec.get("concurrency")) or 2

    out: List[Dict[str, Any]] = []

    top_hidden = bool(sec.get("hidden_default"))
    default_sid = str(sec.get("subscription_id") or sec.get("channel_id") or "")
    default_sub = _entry_dict(subscriptions.get(default_sid)) if default_sid else {}
    if not top_hidden:
        default_cfg = _resolve_llm_config("default", use_active=False)
        out.append({
            "code": "default",
            "name": str(sec.get("name") or ""),
            "model": top_model,
            "base_url": top_base,
            "context_window": top_cw,
            "max_tokens": top_mt,
            "concurrency": top_concurrency,
            "vision": top_vision,
            "thinking_levels": top_thinking,
            "channel_id": str(sec.get("channel_id") or ""),
            "channel_name": str(sec.get("channel_name") or ""),
            "subscription_id": default_sid,
            "subscription_name": str(
                default_sub.get("name") or sec.get("channel_name") or ""),
            "has_api_key": _has_api_key(top_key),
            "inherits_api_key": False,
            "effective_model": str(default_cfg.get("model") or ""),
            "effective_base_url": str(default_cfg.get("base_url") or ""),
            "effective_context_window": _int0(default_cfg.get("context_window")),
            "effective_max_tokens": _int0(default_cfg.get("max_tokens")),
            "effective_concurrency": _int0(default_cfg.get("concurrency")) or 2,
            "effective_vision": bool(default_cfg.get("vision")),
            "effective_thinking_levels": normalize_thinking_levels(
                default_cfg.get("thinking_levels")),
            "effective_has_api_key": _has_api_key(default_cfg.get("api_key")),
            "protocol": _normalize_protocol(sec.get("protocol")),
            "effective_protocol": _normalize_protocol(default_cfg.get("protocol")),
            "configured": bool(default_cfg.get("model") and default_cfg.get("base_url")),
            "active": active_code == "default",
            "deletable": False,
        })

    for raw_code, raw_entry in models.items():
        code = str(raw_code or "").strip()
        if not code or code == "default":
            continue
        entry = _entry_dict(raw_entry)
        cfg = _resolve_llm_config(code, use_active=False)
        own_key = entry.get("api_key")
        own_has_key = _has_api_key(own_key)
        eff_has_key = _has_api_key(cfg.get("api_key"))
        sub_id = str(entry.get("subscription_id") or entry.get("channel_id") or "")
        sub = _entry_dict(subscriptions.get(sub_id)) if sub_id else {}
        out.append({
            "code": code,
            "name": str(entry.get("name") or code),
            "model": str(entry.get("model") or ""),
            "base_url": str(entry.get("base_url") or "").rstrip("/"),
            "context_window": _int0(entry.get("context_window")),
            "max_tokens": _int0(entry.get("max_tokens")),
            "concurrency": _int0(entry.get("concurrency")) or top_concurrency,
            "vision": bool(entry.get("vision", top_vision)),
            "thinking_levels": normalize_thinking_levels(
                entry["thinking_levels"] if "thinking_levels" in entry else top_thinking),
            "channel_id": str(entry.get("channel_id") or ""),
            "channel_name": str(entry.get("channel_name") or ""),
            "subscription_id": sub_id,
            "subscription_name": str(
                sub.get("name") or entry.get("channel_name") or ""),
            "has_api_key": own_has_key,
            "inherits_api_key": bool(not own_has_key and eff_has_key),
            "effective_model": str(cfg.get("model") or ""),
            "effective_base_url": str(cfg.get("base_url") or ""),
            "effective_context_window": _int0(cfg.get("context_window")),
            "effective_max_tokens": _int0(cfg.get("max_tokens")),
            "effective_concurrency": _int0(cfg.get("concurrency")) or 2,
            "effective_vision": bool(cfg.get("vision")),
            "effective_thinking_levels": normalize_thinking_levels(
                cfg.get("thinking_levels")),
            "effective_has_api_key": eff_has_key,
            "protocol": _normalize_protocol(entry.get("protocol")),
            "effective_protocol": _normalize_protocol(cfg.get("protocol")),
            "configured": bool(cfg.get("model") and cfg.get("base_url")),
            "active": code == active_code,
            "deletable": True,
        })
    return out


# 上下文预算参数（独立部署：从 config.yaml 的 llm 段读取）
_DEFAULT_OVERFLOW_ATTEMPTS = 3


async def get_context_policy() -> Optional[Dict[str, Any]]:
    """上下文策略（独立部署）：取 config.yaml llm 段的 compaction /
    context_compress_percent / overflow_recovery_attempts。

    compaction 命名对齐 OpenCode V2：
      compaction:
        auto: true
        keep: { tokens: 15000 }   # 或 keep_tokens（检查点旁保留的原文）
        buffer: 16000             # 或 buffer_tokens；0 = 自动（15% 窗口，至少 16k）
    `adaptive` / `cooldown_rounds` 是旧口径，已不再使用（仍容忍旧配置存在）。
    未配置任何项返回 None，由调用方回落到本服务配置/默认值。
    """
    sec = _llm_section()
    policy: Dict[str, Any] = {}
    pct = sec.get("context_compress_percent")
    if pct is not None:
        try:
            pct = int(pct)
            if 0 <= pct <= 100:
                policy["context_compress_percent"] = pct
        except (TypeError, ValueError):
            pass
    def _tok(value: Any) -> Optional[int]:
        if isinstance(value, dict):
            value = value.get("tokens")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    comp = sec.get("compaction")
    if isinstance(comp, dict):
        normalized: Dict[str, Any] = {}
        if "auto" in comp:
            normalized["auto"] = bool(comp["auto"])

        keep = comp.get("keep", comp.get("keep_tokens"))
        keep = _tok(keep)
        if keep is not None:
            normalized["keep_tokens"] = max(1, keep)
        buffer = comp.get("buffer", comp.get("buffer_tokens"))
        buffer = _tok(buffer)
        if buffer is not None:
            normalized["buffer_tokens"] = max(0, buffer)
        if "adaptive" in comp:
            normalized["adaptive"] = bool(comp["adaptive"])
        if "cooldown_rounds" in comp:
            try:
                normalized["cooldown_rounds"] = max(0, int(comp["cooldown_rounds"]))
            except (TypeError, ValueError):
                pass
        if normalized:
            policy["compaction"] = normalized

    # 跨轮历史（每轮从库里取多少条对话）：条数上限 + token 预算。
    hist = sec.get("history")
    if isinstance(hist, dict):
        normalized_hist: Dict[str, int] = {}
        msgs = _tok(hist.get("max_messages"))
        if msgs is not None:
            normalized_hist["max_messages"] = max(2, msgs)
        tokens = _tok(hist.get("max_tokens"))
        if tokens is not None:
            normalized_hist["max_tokens"] = max(0, tokens)
        if normalized_hist:
            policy["history"] = normalized_hist

    attempts = sec.get("overflow_recovery_attempts")
    if attempts is not None:
        try:
            policy["overflow_recovery_attempts"] = max(1, int(attempts))
        except (TypeError, ValueError):
            pass
    return policy or None


async def get_overflow_recovery_attempts() -> int:
    """上下文过长（400）后的压缩重试次数；读不到用代码默认 3。"""
    policy = await get_context_policy()
    if policy and policy.get("overflow_recovery_attempts"):
        return int(policy["overflow_recovery_attempts"])
    return _DEFAULT_OVERFLOW_ATTEMPTS


async def _compact_with_guard(
    llm_code: str, messages: List[Dict[str, Any]], attempt: int
) -> Optional[List[Dict[str, Any]]]:
    """在「禁止再次进入恢复」的标记下执行压缩（防 summary 调用 400 造成递归）。"""
    token = _in_overflow_recovery.set(True)
    try:
        return await compact_for_overflow(llm_code, messages, attempt)
    finally:
        _in_overflow_recovery.reset(token)


def is_context_too_long_error(exc: BaseException) -> bool:
    """判定是否因上下文过长被上游拒绝（400 / prompt too long / 最大上下文长度）。"""
    if getattr(exc, "status_code", None) != 400:
        return False
    s = str(exc).lower()
    return any(k in s for k in (
        "too long", "context", "token", "exceed", "maximum", "length", "超出", "过长",
    ))


async def compact_for_overflow(
    llm_code: str,
    messages: List[Dict[str, Any]],
    attempt: int,
) -> Optional[List[Dict[str, Any]]]:
    """400 后的强制压缩：按 OpenCode 的 `SHRINK_STEPS` 逐次收缩目标。

    首次 400 说明配置的 `context_window` 大于模型真实窗口（配置填错 / 模型换血）。
    以**被拒请求的大小**为基准，依次按 0.85 / 0.7 / 0.5 / 0.35 缩小目标，
    既保证单调收敛，又不会像「窗口减半」那样一次丢太多信息。
    无可压缩内容时返回 None（由调用方放弃重试）。
    """
    from services.agent_zero.runtime.zero import compress as compress_mod

    try:
        cfg = await get_llm_config(llm_code)
    except Exception as e:
        logger.debug("overflow 压缩读取模型配置失败: %s", e)
        return None
    try:
        window = int(cfg.get("context_window") or 0)
    except (TypeError, ValueError):
        window = 0
    if window <= 0:
        return None

    before = compress_mod.estimate_tokens(messages)
    step = compress_mod.SHRINK_STEPS[min(max(0, int(attempt)),
                                         len(compress_mod.SHRINK_STEPS) - 1)]
    budget = max(1024, min(compress_mod.ceiling(window), int(before * step)))
    token = _in_overflow_recovery.set(True)
    try:
        res = await compress_mod.compress(
            messages, models={"loop": llm_code}, budget=budget, force=True,
            reason="overflow",
        )
    except Exception as e:
        logger.debug("overflow 压缩失败: %s", e)
        return None
    finally:
        _in_overflow_recovery.reset(token)
    after = res.get("tokens", before)
    if not res.get("compressed") or after >= before:
        return None
    logger.warning(
        "上下文超长(400)：目标收缩到 %.0f%% 后压缩（attempt=%d, llm=%s, %d→%d tokens, "
        "保留 %s 条近期原文%s）",
        step * 100, attempt + 1, llm_code, before, after, res.get("kept_recent"),
        "，含算法硬裁" if res.get("hard_trimmed") else "",
    )
    return res["messages"]


@dataclass
class LlmResult:
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # [{id, name, arguments: dict}]
    usage: Dict[str, int] = field(default_factory=dict)  # {input_tokens, output_tokens}
    model: str = ""


# ── 传输层：pydantic-ai v2 ────────────────────────────────────────────────────
# 请求/响应交给 pydantic-ai 的 OpenAIChatModel / OpenAIResponsesModel（协议仍由
# 档案的 `protocol` 决定）；本模块保留消息转换、流式事件语义（delta / thinking /
# tool_preparing / done）、上下文溢出压缩重试与模型降级。鉴权、重试、SSE 解析
# 由 pydantic-ai + OpenAI SDK 负责。

# 统一模型档案：目标端点多为 OpenAI 兼容网关，保持旧实现的线上请求形状。
_MODEL_PROFILE: Dict[str, Any] = {
    # 旧实现发 `max_tokens`；档案推断为 OpenAI 型号时默认会改发 max_completion_tokens
    "openai_chat_supports_max_completion_tokens": False,
    # 旧实现始终发送 temperature：不让档案推断把推理模型的采样参数丢掉
    "openai_supports_reasoning": False,
}

# 结束原因：pydantic-ai 用单数 tool_call，运行时旧事件语义用复数
_FINISH_REASONS = {
    "tool_call": "tool_calls",   # pydantic-ai 用单数，旧事件语义用复数
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
    "error": "error",
}


def _new_http_client(trust_env: bool = False) -> Any:
    """新建一个有连接上限的 HTTP 客户端（由 `_get_http_client` 按 provider 复用）。

    默认 trust_env=False（不读系统代理，与旧实现一致）；桌面用户需要走系统
    代理时在 config.yaml 设 `llm.trust_env: true`。
    """
    flag = bool(trust_env)
    sec = _llm_section()
    try:
        max_conn = int(sec.get("max_connections") or 32)
    except (TypeError, ValueError):
        max_conn = 32
    try:
        max_keep = int(sec.get("max_keepalive") or max(8, max_conn // 2))
    except (TypeError, ValueError):
        max_keep = max(8, max_conn // 2)
    limit_cls = _httpx2.Limits if _httpx2 is not None else httpx.Limits
    limits = limit_cls(max_connections=max(4, max_conn),
                       max_keepalive_connections=max(1, min(max_keep, max_conn)))
    client_cls = _httpx2.AsyncClient if _httpx2 is not None else httpx.AsyncClient
    return client_cls(trust_env=flag, limits=limits)


def _get_http_client(trust_env: bool, base_url: str = "") -> Any:
    """取（或建立）该 provider 的复用连接池。

    键 = (base_url, trust_env)。不同模型档案指向不同渠道时天然隔离；同一渠道
    下的多个模型/会话共用连接，鉴权头逐请求携带，不会串 key。

    httpx 的 AsyncClient 绑定事件循环，所以池里记录建立它的 loop：换循环
    （测试里的 asyncio.run、桌面端重启循环）时重建，绝不跨循环复用。

    建客户端要读配置（可能碰数据库），所以**不在锁里做**：先查、再建、再查一次。
    """
    loop = asyncio.get_running_loop()
    key = (str(base_url or "").rstrip("/"), bool(trust_env))
    with _http_pool_lock:
        entry = _http_pool.get(key)
        if entry is not None and entry[0] is loop and not entry[1].is_closed:
            return entry[1]
    client = _new_http_client(bool(trust_env))
    with _http_pool_lock:
        entry = _http_pool.get(key)
        if entry is not None and entry[0] is loop and not entry[1].is_closed:
            # 并发下别的任务刚建好：用它的（未使用的 client 不占连接，交给 GC）
            return entry[1]
        _http_pool[key] = (loop, client)
        return client


async def close_llm_clients() -> None:
    """关闭当前事件循环上的连接池（应用退出时调用；测试也可用来复位）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    with _http_pool_lock:
        stale = [(k, v) for k, v in _http_pool.items() if v[0] is loop]
        for key, _entry in stale:
            _http_pool.pop(key, None)
    for _key, (_entry_loop, client) in stale:
        try:
            await client.aclose()
        except Exception:  # pragma: no cover - 关闭失败不影响退出
            logger.debug("关闭 LLM HTTP 连接池失败", exc_info=True)


# 每个模型档案的并发槽位。同一 code 的主对话 / 摘要 / 压缩 / 团队成员 / 子代理
# 共用一池；配置改了就换一个新闸门（在途请求继续用旧池跑完）。
_MODEL_SLOTS: Dict[str, Dict[str, Any]] = {}
_MODEL_SLOTS_LOCK = threading.Lock()
MODEL_CONCURRENCY_DEFAULT = 2
MODEL_CONCURRENCY_MAX = 64


def model_concurrency(cfg: Dict[str, Any]) -> int:
    try:
        value = int(cfg.get("concurrency") or MODEL_CONCURRENCY_DEFAULT)
    except (TypeError, ValueError):
        value = MODEL_CONCURRENCY_DEFAULT
    if value <= 0:
        value = MODEL_CONCURRENCY_DEFAULT
    return max(1, min(value, MODEL_CONCURRENCY_MAX))


def _wake_future(fut: "asyncio.Future[None]") -> None:
    """线程安全地唤醒等待者；等待任务已被取消时安静忽略。"""
    if not fut.done():
        fut.set_result(None)


class _ModelGate:
    """跨事件循环的模型并发闸门。

    只用 `asyncio.Semaphore` 时，每个事件循环各拿一个实例：桌面端的记忆重建
    线程、测试里的新循环、以及主服务循环会各自看到一份「空池」，配置看似
    失效（总并发 = 上限 × 循环数）。这里用线程锁维护全局计数，把空闲槽位
    直接转交给下一个等待者，既跨循环生效，也不会阻塞事件循环线程。
    """

    def __init__(self, limit: int) -> None:
        self.limit = int(limit)
        self._lock = threading.Lock()
        self._active = 0
        # [(事件循环, Future)]；future 完成 = 槽位已转交给它，计数不变。
        self._waiters: List[tuple] = []

    def locked(self) -> bool:
        with self._lock:
            return self._active >= self.limit

    def _prune_locked(self) -> None:
        self._waiters = [(loop, fut) for loop, fut in self._waiters
                         if not fut.done()]

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._prune_locked()
            if self._active < self.limit:
                self._active += 1
                return
            fut: "asyncio.Future[None]" = loop.create_future()
            self._waiters.append((loop, fut))
        try:
            await fut
        except asyncio.CancelledError:
            with self._lock:
                for idx, (wait_loop, wait_fut) in enumerate(self._waiters):
                    if wait_fut is fut:
                        # 还在排队：直接退出，不占槽位。
                        self._waiters.pop(idx)
                        break
                else:
                    # 槽位已经转给我了（future 已完成）但任务被取消：
                    # 把槽位让给下一个等待者，避免并发上限被永久吞掉。
                    self._wake_one_locked()
            raise

    def release(self) -> None:
        with self._lock:
            self._wake_one_locked()

    def _wake_one_locked(self) -> None:
        while self._waiters:
            loop, fut = self._waiters.pop(0)
            if fut.done():
                continue
            try:
                loop.call_soon_threadsafe(_wake_future, fut)
            except RuntimeError:  # 事件循环已关闭：跳过这个等待者
                continue
            # 槽位从当前持有者直接转交，_active 不变。
            return
        self._active = max(0, self._active - 1)


def _model_semaphore(cfg: Dict[str, Any]) -> _ModelGate:
    code = str(cfg.get("code") or "default")
    limit = model_concurrency(cfg)
    with _MODEL_SLOTS_LOCK:
        entry = _MODEL_SLOTS.get(code)
        if entry and entry["limit"] == limit:
            return entry["gate"]
        gate = _ModelGate(limit)
        _MODEL_SLOTS[code] = {"limit": limit, "gate": gate}
        return gate


@asynccontextmanager
async def _model_slot(cfg: Dict[str, Any]) -> AsyncIterator[None]:
    """占用模型档案的一个并发槽位；等待期间可被 run 的取消打断。"""
    code = str(cfg.get("code") or "default")
    gate = _model_semaphore(cfg)
    if gate.locked():
        logger.debug("模型 %s 达到并发上限 %d，等待空闲槽位",
                     code, model_concurrency(cfg))
    await gate.acquire()
    try:
        yield
    finally:
        gate.release()


@asynccontextmanager
async def _open_model(cfg: Dict[str, Any]) -> AsyncIterator[Any]:
    """按档案构造 pydantic-ai 模型（OpenAI chat / responses / Anthropic）。

    HTTP 客户端按 provider 复用（见 `_get_http_client`），这里**不再关闭**它：
    每次请求新建 = 每次重新握手，多轮工具循环里延迟和端口占用都很明显。
    """
    protocol = _protocol_of(cfg)
    base_url = str(cfg.get("api_base") or cfg.get("base_url") or "").rstrip("/")
    api_key = str(cfg.get("api_key") or "EMPTY")
    trust_env = bool(cfg.get("trust_env"))
    if protocol == _PROTOCOL_ANTHROPIC:
        # Anthropic SDK 自己会拼 `/v1/messages`，用户粘贴带 /v1 的地址时先归一化。
        anthropic_base = _normalize_anthropic_base_url(base_url)
        http_client = _get_http_client(trust_env, anthropic_base or base_url)
        try:
            from anthropic import AsyncAnthropic
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider
        except ImportError as e:  # pragma: no cover - 依赖缺失时给出可操作提示
            raise LlmError(
                "Anthropic 协议需要 anthropic SDK：请重新安装依赖 "
                "（pip install -r services/agent_zero/requirements.txt）"
            ) from e
        sdk = AsyncAnthropic(
            api_key=api_key,
            base_url=anthropic_base or None,
            http_client=http_client,
            # 与 OpenAI 分支一致：SDK 级重试关闭，重试交给运行时统一策略。
            max_retries=0,
            # 兼容不同中转：原生 Anthropic 与 OpenCode 用 x-api-key，部分
            # Anthropic 兼容网关只认 Authorization Bearer（SDK 默认会带）。
            # 两个头同时发送；anthropic-version 固定到最通用的稳定版本。
            default_headers={
                "x-api-key": api_key,
                "Authorization": f"Bearer {api_key}",
                "anthropic-version": "2023-06-01",
            },
        )
        provider = AnthropicProvider(anthropic_client=sdk)
        yield AnthropicModel(str(cfg.get("model") or ""), provider=provider)
        return
    http_client = _get_http_client(trust_env, base_url)
    sdk = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        http_client=http_client,
        # 旧实现不做 SDK 级重试：失败重试只走上下文压缩与 fallback 两条明确路径
        max_retries=0,
    )
    provider = OpenAIProvider(openai_client=sdk)
    model_cls = (OpenAIResponsesModel if protocol == _PROTOCOL_RESPONSE
                 else OpenAIChatModel)
    yield model_cls(
        str(cfg.get("model") or ""),
        provider=provider,
        profile=dict(_MODEL_PROFILE),
    )


def _text_of(content: Any) -> str:
    """把 chat 格式 content（str / parts 列表）压成纯文本。"""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, dict) and str(part.get("type") or "") in (
                    "text", "input_text", "output_text"):
                chunks.append(str(part.get("text") or ""))
        if chunks:
            return "".join(chunks)
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _user_content(content: Any) -> Any:
    """chat 格式用户内容 → pydantic-ai UserContent（文本 + 图片）。"""
    if isinstance(content, str) or content is None:
        return str(content or "")
    if not isinstance(content, list):
        return _text_of(content)
    parts: List[Any] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = str(part.get("type") or "")
        if ptype in ("text", "input_text"):
            parts.append(TextContent(content=str(part.get("text") or "")))
        elif ptype in ("image_url", "input_image"):
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if url:
                parts.append(ImageUrl(url=str(url)))
    return parts or ""


def _assistant_tool_names(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    """tool_call_id → 工具名（tool 结果消息里没有名字，需要回填）。"""
    names: Dict[str, str] = {}
    for m in messages or []:
        if str(m.get("role") or "") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            cid = str(tc.get("id") or "")
            if cid:
                names[cid] = str(fn.get("name") or "")
    return names


def _to_model_messages(messages: List[Dict[str, Any]]) -> List[ModelMessage]:
    """chat 格式消息 → pydantic-ai ModelMessage（逐条一一对应）。"""
    names = _assistant_tool_names(messages)
    out: List[ModelMessage] = []
    for m in messages or []:
        role = str(m.get("role") or "user")
        content = m.get("content")
        if role == "system":
            out.append(ModelRequest(parts=[SystemPromptPart(content=_text_of(content))]))
        elif role == "assistant":
            parts: List[Any] = []
            text = _text_of(content)
            if text:
                parts.append(TextPart(content=text))
            for tc in m.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                raw = fn.get("arguments")
                if not isinstance(raw, str):
                    raw = json.dumps(raw if raw is not None else {}, ensure_ascii=False)
                parts.append(ToolCallPart(
                    tool_name=str(fn.get("name") or ""),
                    args=raw,
                    tool_call_id=str(tc.get("id") or ""),
                ))
            # 空 assistant 轮次（只有元数据/被压缩标记）也要占位，否则角色交替被打乱
            out.append(ModelResponse(parts=parts or [TextPart(content="")]))
        elif role == "tool":
            cid = str(m.get("tool_call_id") or m.get("call_id") or "")
            part = ToolReturnPart(
                tool_name=names.get(cid) or "tool",
                content=_text_of(content),
                tool_call_id=cid,
            )
            # 并行工具调用会产生连续多条 tool 结果。Anthropic Messages 要求
            # 这些 tool_result 放在同一条 user 消息里；合并后 OpenAI 链路也
            # 仍然是一条合法的工具结果消息。
            if (out and isinstance(out[-1], ModelRequest)
                    and out[-1].parts
                    and all(isinstance(p, ToolReturnPart) for p in out[-1].parts)):
                out[-1] = ModelRequest(parts=[*out[-1].parts, part])
            else:
                out.append(ModelRequest(parts=[part]))
        else:
            out.append(ModelRequest(parts=[UserPromptPart(content=_user_content(content))]))
    return out


def _tool_definitions(tools: Optional[List[Dict[str, Any]]]) -> List[ToolDefinition]:
    """chat 格式 tools → pydantic-ai ToolDefinition（兼容已拆包与 function 包裹两种写法）。"""
    out: List[ToolDefinition] = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        name = str(fn.get("name") or "")
        if not name:
            continue
        out.append(ToolDefinition(
            name=name,
            description=str(fn.get("description") or "") or None,
            parameters_json_schema=fn.get("parameters") or {"type": "object", "properties": {}},
        ))
    return out


# 历史默认的「请求超时」哨兵值：调用方不显式指定时就用它，实际生效的读超时
# 由配置 `llm.request_timeout_s` 决定（见 `_request_timeout_setting`）。
DEFAULT_REQUEST_TIMEOUT = 300.0


def _request_timeout_setting(timeout: float,
                             cfg: Optional[Dict[str, Any]] = None) -> Any:
    """构造 HTTP 超时对象：连接快速失败、读超时按配置。

    过去的实现只传一个 float，openai SDK 会把它当作**所有**阶段的超时——
    包括 connect。于是网络抖动、网关抽风时，程序要干等 300s 才知道「连不上」，
    才开始重试；而真正长的生成（长思考 / 长工具参数）又可能被 read 超时打断。

    现在分开：
    - connect / pool = `llm.connect_timeout_s`（默认 10s）：连不上就快速失败，
      交给上层 20 次 × 2.5s 的重试；
    - read = `llm.request_timeout_s`（默认 600s）：正常生成跑多久都在范围内；
    - `timeout <= 0` 或不配置 read=0：该阶段不设超时。
    显式传了别的小值（如压缩 / 预规划用的 60s）时按调用方语义：读=写=该值。
    `cfg` 是解析后的**模型档案**配置：档案里填了 connect/request_timeout_s
    就只影响这个模型，别的会话/别的模型不受牵连。
    """
    sec = _llm_section()

    def _pick(name: str, default: float) -> float:
        source = cfg if cfg is not None else {}
        value = source.get(name)
        if value is None:
            value = sec.get(name)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    connect = _pick("connect_timeout_s", 10.0)
    connect = max(1.0, min(connect, 120.0))
    explicit = float(timeout or 0.0)
    read_cfg = _pick("request_timeout_s", 600.0)
    read_cfg = max(0.0, min(read_cfg, 86400.0))
    read = read_cfg if explicit in (0.0, DEFAULT_REQUEST_TIMEOUT) else explicit
    timeout_cls = _httpx2.Timeout if _httpx2 is not None else httpx.Timeout
    try:
        if read <= 0:
            return timeout_cls(connect=connect, read=None, write=None, pool=connect)
        return timeout_cls(connect=connect, read=read, write=read, pool=connect)
    except Exception:  # pragma: no cover - 老版本 httpx 不支持 None
        return read if read > 0 else None


def _model_settings(cfg: Dict[str, Any], temperature: float,
                    max_tokens: Optional[int], timeout: float,
                    has_tools: bool) -> Dict[str, Any]:
    """请求参数（pydantic-ai ModelSettings；带鉴权以外的额外请求头）。"""
    protocol = _protocol_of(cfg)
    settings: Dict[str, Any] = {}
    _t = _request_timeout_setting(timeout, cfg)
    if _t is not None:
        settings["timeout"] = _t
    # 调用方传 0 表示「不限」，但多数 OpenAI 兼容端点会套一个很低的默认值
    # （实测 DeepSeek 网关默认 ~2048，思考还没写完就被截断，最终没有正文）。
    # 不限时回落到模型档案的 max_tokens（设置 → 模型），把上限交给用户配置。
    budget = int(max_tokens or 0) or int(cfg.get("max_tokens") or 0)
    if budget:
        settings["max_tokens"] = budget
    effort = get_reasoning_effort()
    if effort:
        if protocol == _PROTOCOL_ANTHROPIC:
            # Anthropic 的 extended thinking 与自定义 temperature / top_p 互斥，
            # 这里只透传它认识的思考级别；不认识的级别宁可不发，避免 400。
            level = _anthropic_thinking_level(effort)
            if level:
                settings["thinking"] = level
                think_budget = _ANTHROPIC_THINKING_BUDGETS.get(level, 0)
                # 显式 max_tokens 小于思考预算时，Anthropic 会直接 400。
                if (think_budget and settings.get("max_tokens")
                        and int(settings["max_tokens"]) <= think_budget):
                    settings["max_tokens"] = think_budget + 1024
            else:
                logger.warning("忽略 Anthropic 不支持的思考级别: %s", effort)
        else:
            # 直接透传档案里配置的思考级别（low / medium / high / minimal / none …），
            # 不走 pydantic-ai 的统一 thinking 映射，保持旧实现的原样透传。
            settings["openai_reasoning_effort"] = effort
    if not (protocol == _PROTOCOL_ANTHROPIC and effort):
        settings["temperature"] = float(temperature)
    if has_tools:
        settings["tool_choice"] = "auto"
    headers = _request_headers(str(cfg.get("api_base") or cfg.get("base_url") or ""))
    if headers:
        settings["extra_headers"] = headers
    return settings


def _anthropic_thinking_level(effort: Any) -> str:
    """Anthropic 支持的统一思考级别；自定义值返回空串（不发送）。"""
    value = str(effort or "").strip().lower()
    if value in ("minimal", "low", "medium", "high", "xhigh", "max"):
        return value
    return ""


# Anthropic extended thinking 的 budget_tokens 必须小于 max_tokens；这里与
# pydantic-ai 的映射保持一致，并在用户配置的 max_tokens 太小时自动抬高。
_ANTHROPIC_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 2048,
    "medium": 10000,
    "high": 16384,
    "xhigh": 32768,
    "max": 32768,
}


def _args_dict(raw: Any) -> Dict[str, Any]:
    """工具参数宽松解析：非法 JSON 记日志并置空（与旧实现一致）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (list, tuple)):
        return {"_raw": raw}
    try:
        args = json.loads(raw or "{}")
        if isinstance(args, dict):
            return args
        return {"_raw": raw}
    except Exception:
        logger.warning("tool_call arguments 非合法 JSON，置空: %.120s", raw)
        return {}


def _tool_calls_of(parts: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for part in parts or []:
        if isinstance(part, ToolCallPart):
            out.append({
                "id": str(part.tool_call_id or ""),
                "name": str(part.tool_name or ""),
                "arguments": _args_dict(part.args),
            })
    return out


def _dsml_calls_of(text: str, tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
    """正文里的 DSML 调用：本会话级指令始终执行，其它工具须在可用清单里。

    部分兼容端点（实测 OpenCode Go 的 deepseek-v4.1-flash）会把 DSML 原文
    当正文流回来；即使这一轮没有下发工具清单，`session_manage` 也是对话层
    自己处理的会话指令，应当照常执行，避免用户只看到一串标记。
    """
    if not text:
        return []
    allowed = {str(tool.name) for tool in tool_defs} | {"session_manage"}
    calls = [call for call in extract_dsml_calls(text) if call["name"] in allowed]
    if calls:
        logger.info("正文 DSML 工具调用已还原: %s",
                    ", ".join(str(call.get("name") or "") for call in calls))
    return calls


def _text_of_parts(parts: Any, *, kind: Any = TextPart) -> str:
    return "".join(str(p.content) for p in parts or [] if isinstance(p, kind))


def _usage_pair(usage: Any) -> Dict[str, int]:
    """非流式返回的用量（沿用 input_tokens / output_tokens 键）。"""
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def _usage_event(usage: Any) -> Dict[str, Any]:
    """流式 done 事件的用量：同时给出 OpenAI 原生键，兼容既有统计口径。"""
    if usage is None:
        return {}
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    if not prompt and not completion:
        return {}
    data: Dict[str, Any] = {
        "input_tokens": prompt,
        "output_tokens": completion,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    cached = (int(getattr(usage, "cache_read_tokens", 0) or 0)
              or int(getattr(usage, "cache_read_input_tokens", 0) or 0))
    if cached:
        data["prompt_tokens_details"] = {"cached_tokens": cached}
    return data


def _finish_reason(value: Any) -> str:
    return _FINISH_REASONS.get(str(value or ""), str(value or ""))


def _retry_after_of(exc: BaseException) -> Optional[float]:
    """解析 Retry-After（秒）；没有或不可解析返回 None。"""
    headers = getattr(exc, "headers", None)
    raw = None
    try:
        if headers:
            for key in ("retry-after", "Retry-After"):
                raw = headers.get(key)
                if raw is not None:
                    break
    except Exception:
        raw = None
    if raw is None:
        return None
    try:
        return max(0.0, min(float(str(raw).strip()), 120.0))
    except (TypeError, ValueError):
        return None


def _request_error(exc: BaseException, llm_code: str, api_base: str) -> LlmError:
    """pydantic-ai / OpenAI SDK 异常 → LlmError（保留状态码与可重试分类）。"""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None
    name = type(exc).__name__.lower()
    rate_limited = _retry_after_of(exc)

    if status is None:
        if "timeout" in name or isinstance(exc, asyncio.TimeoutError):
            kind = "timeout"
        elif "connect" in name or "network" in name or "connection" in name:
            kind = "network"
        else:
            kind = "unknown"
        _record_error(kind)
        return LlmError(
            f"LLM 调用失败 ({llm_code}@{api_base}): {type(exc).__name__}: {exc}",
            None, kind, rate_limited)

    body = getattr(exc, "body", None)
    detail = body if body is not None else exc
    if status == 400 and is_context_too_long_error(exc):
        kind = "overflow"
    elif status in (401, 403):
        kind = "auth"
    elif status == 429:
        kind = "rate_limit"
    elif status in (408, 504):
        kind = "timeout"
    elif status >= 500:
        kind = "upstream"
    else:
        kind = "http"
    _record_error(kind)
    return LlmError(
        f"LLM HTTP {status} ({llm_code}@{api_base}): {str(detail)[:300]}",
        status, kind, rate_limited)


def _retry_policy(cfg: Optional[Dict[str, Any]] = None) -> tuple[int, float]:
    """可重试错误的次数与间隔（config.yaml llm.retry_*，默认 20 次 / 2.5s）。

    网络抖动是常态：默认给足重试预算（20 次 × 2.5s ≈ 50s 之后才向用户报错），
    不再用「几次就放弃」把抖动直接甩成一次失败的对话。

    `cfg` 为解析后的模型档案：档案里可单独填 `retry_attempts` /
    `retry_backoff_s`（比如本地小模型重试 3 次就够，而线上渠道要 20 次），
    只影响该模型，其它会话照旧。
    """
    sec = _llm_section()

    def _pick(name: str) -> Any:
        value = (cfg or {}).get(name)
        return sec.get(name) if value is None else value

    try:
        attempts = int(_pick("retry_attempts"))
    except (TypeError, ValueError):
        attempts = 20
    attempts = max(0, min(attempts, 50))
    try:
        backoff = float(_pick("retry_backoff_s"))
    except (TypeError, ValueError):
        backoff = 2.5
    backoff = max(0.0, min(backoff, 30.0))
    return attempts, backoff


def _retry_deadline_s(cfg: Optional[Dict[str, Any]] = None) -> float:
    """连续失败多久就放弃重试（秒；0 = 不限）。

    重试次数 × 单次超时可能非常长（20 次 × 600s = 3 小时）：上游整体挂掉时，
    run 会一直「运行中」。默认 15 分钟后放弃并报错，用户能立刻看到失败原因；
    需要更长的恢复窗口时在 `llm.retry_deadline_s`（或模型档案里）调大 / 设 0。
    """
    sec = _llm_section()
    value = (cfg or {}).get("retry_deadline_s")
    if value is None:
        value = sec.get("retry_deadline_s")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = 900.0
    return max(0.0, min(seconds, 86400.0))


def _retry_expired(deadline: float, failing_since: Optional[float],
                   err: LlmError) -> bool:
    """连续失败是否已超过重试总时长；超过时保留原错误向上抛。"""
    if deadline <= 0 or failing_since is None:
        return False
    waited = time.monotonic() - failing_since
    if waited <= deadline:
        return False
    logger.warning("LLM 连续失败 %.0fs，超过重试上限 %.0fs，放弃重试: %s",
                   waited, deadline, err)
    return True


def _is_retryable(err: LlmError) -> bool:
    return err.kind in ("network", "timeout", "rate_limit", "upstream")


async def _retry_wait(err: LlmError, attempt: int, base: float) -> None:
    """**固定间隔**重试等待（不再指数退避）。

    配置的 `retry_backoff_s`（默认 2.5s）就是每次重试的间隔；唯一例外是上游明确
    给了 Retry-After（429 限流）——那时按它等，避免把限流打成更长的封禁。
    """
    delay = max(float(base or 0.0), float(err.retry_after or 0.0))
    delay = max(0.0, min(delay, 30.0))
    if delay > 0:
        await asyncio.sleep(delay)


# 已输出正文后断流的「续写」提示：把已发出的内容回填成 assistant 消息，
# 让模型从中断处接着说，而不是整轮重来（重来会让前端看到重复文本）。
_RESUME_PROMPT = (
    "（系统提示：你上一条回复在传输中断处被截断了，那不是输出的结束。"
    "请**从中断处继续**输出剩余内容：不要重复已经输出过的部分，不要重新开头，"
    "也不要复述这段提示。）"
)


def _resume_split(sent: str, full: str) -> tuple[str, bool]:
    """续写重试时切掉模型重复输出的已发前缀。

    返回 ``(可投递的新内容, 是否已对齐)``。尚未确定是「重复前缀」还是「真的
    接着往下写」时返回 ``("", False)``，让调用方再等一点内容再判断——宁可晚
    一小段，也不要把重复的头几句再给用户看一遍。
    """
    if not sent:
        return full, True
    if len(full) <= len(sent):
        return ("", False) if sent.startswith(full) else _tail_overlap(sent, full)
    if full.startswith(sent):
        return full[len(sent):], True
    return _tail_overlap(sent, full)


def _tail_overlap(sent: str, full: str) -> tuple[str, bool]:
    """sent 的后缀与 full 的前缀取最长重叠，切掉它。"""
    max_k = min(len(sent), len(full))
    for k in range(max_k, 0, -1):
        if sent.endswith(full[:k]):
            return full[k:], True
    return full, True


def get_stream_watchdog_config() -> tuple[float, float]:
    """流式看门狗参数：(心跳间隔秒, 空闲中止秒)。

    - `llm.stream_keepalive_s`（默认 20s）：等待上游期间多久推一次心跳 ping；
    - `llm.stream_idle_timeout_s`（默认 0 = **不中止**）：连续多少秒没有任何流
      事件才判定上游无响应并中止本轮。0 表示一直等下去，靠自动重试恢复，
      由用户手动停止兜底——不再用「掐掉整轮」处理网络慢。
    """
    sec = _llm_section()
    try:
        keepalive = float(sec.get("stream_keepalive_s"))
    except (TypeError, ValueError):
        keepalive = 20.0
    keepalive = max(2.0, min(keepalive, 120.0))
    try:
        idle = float(sec.get("stream_idle_timeout_s"))
    except (TypeError, ValueError):
        idle = 0.0
    idle = max(0.0, min(idle, 86400.0))
    return keepalive, idle


def get_run_stall_timeout() -> float:
    """整条 run 的事件静默上限（秒）；0 = 不中止（默认）。

    对应 `llm.run_event_stall_s`。默认 0：后台长任务可能跑很久，只有显式配置
    才启用「静默多久判卡死」的硬兜底。
    """
    sec = _llm_section()
    try:
        value = float(sec.get("run_event_stall_s"))
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(value, 86400.0))


def _magpie_prepare(messages: List[Dict[str, Any]]):
    """本机脱敏：返回（发给模型的消息, 本次调用的还原器或 None）。"""
    try:
        from services.agent_zero.services import magpie_service

        return magpie_service.redact_messages(messages)
    except Exception as e:  # 隐私层故障不应阻断对话；失败时明确记录
        logger.warning("magpie 脱敏不可用，本次按原文发送: %s", e)
        return messages, None


async def _llm_chat_once(
    llm_code: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    timeout: float = 300.0,
) -> LlmResult:
    """单次非流式调用（无重试）；协议派发由模型档案决定。"""
    cfg = await get_llm_config(llm_code)
    api_base = str(cfg.get("api_base") or "")
    masked_messages, magpie = _magpie_prepare(messages)
    tool_defs = _tool_definitions(tools)
    settings = _model_settings(cfg, temperature, max_tokens, timeout, bool(tool_defs))
    params = ModelRequestParameters(function_tools=tool_defs)
    try:
        async with _model_slot(cfg):
            async with _open_model(cfg) as model:
                response = await model.request(
                    _to_model_messages(masked_messages), settings, params)
    except LlmError:
        raise
    except Exception as e:
        raise _request_error(e, llm_code, api_base) from e
    content = _text_of_parts(response.parts)
    calls = _tool_calls_of(response.parts)
    # 部分 DeepSeek 兼容端点会把 DSML 工具调用写进正文：还原成结构化调用，
    # 并从正文里抹掉标记，避免前端看到 <｜DSML｜…> 原文。
    if not calls:
        calls = _dsml_calls_of(content, tool_defs)
    content = clean_dsml_text(content)
    if magpie is not None:
        content = magpie.restore_text(content)
        calls = magpie.restore_obj(calls)
    return LlmResult(
        content=content,
        tool_calls=calls,
        usage=_usage_pair(response.usage),
        model=str(response.model_name or cfg.get("model") or ""),
    )


async def llm_chat(
    llm_code: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    timeout: float = 300.0,
) -> LlmResult:
    """OpenAI 兼容模型单次调用（tools = function calling）。

    上下文过长（400）时逐次把「假定窗口」减半强制压缩后重试（统一压缩口径）。
    """
    current = list(messages)
    overflow_attempt = 0
    retry_attempt = 0
    max_overflow = await get_overflow_recovery_attempts()
    # 该模型档案自己的重试策略（未配置时回落全局）；配置读不到不在这里报错，
    # 交给真正的请求去抛（错误信息更准确，也不会改变既有错误路径）
    try:
        _cfg: Optional[Dict[str, Any]] = await get_llm_config(llm_code)
    except Exception:
        _cfg = None
    max_retries, backoff = _retry_policy(_cfg)
    deadline = _retry_deadline_s(_cfg)
    failing_since: Optional[float] = None
    while True:
        try:
            return await _llm_chat_once(llm_code, current, tools, temperature, max_tokens, timeout)
        except LlmError as e:
            if (not _in_overflow_recovery.get() and overflow_attempt < max_overflow
                    and is_context_too_long_error(e)):
                compacted = await _compact_with_guard(llm_code, current, overflow_attempt)
                if compacted is not None:
                    current = compacted
                    overflow_attempt += 1
                    continue
            if (not _in_overflow_recovery.get() and retry_attempt < max_retries
                    and _is_retryable(e)):
                failing_since = failing_since or time.monotonic()
                if _retry_expired(deadline, failing_since, e):
                    raise
                retry_attempt += 1
                _record_retry()
                logger.warning("LLM %s 错误，第 %d/%d 次重试: %s",
                               e.kind, retry_attempt, max_retries, e)
                try:
                    from services.agent_zero.services.events import log_event

                    log_event("llm.retry", kind=e.kind, attempt=retry_attempt,
                              max_attempts=max_retries, llm_code=llm_code,
                              conv_id=_session_id.get())
                except Exception:
                    pass
                await _retry_wait(e, retry_attempt - 1, backoff)
                continue
            raise


async def llm_chat_stream(
    llm_code: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    timeout: float = 300.0,
) -> AsyncIterator[str]:
    """流式直连，逐段 yield 文本增量（不消费思考与工具调用）。

    上下文过长（400）时压缩重试；**已向消费者投递过增量则不重试**
    （重放会导致前端出现重复文本）。
    """
    async for ev in llm_chat_stream_full(
        llm_code, messages, tools=tools,
        temperature=temperature, max_tokens=max_tokens, timeout=timeout,
    ):
        if ev.get("type") == "delta":
            piece = str(ev.get("content") or "")
            if piece:
                yield piece


async def llm_chat_stream_full(
    llm_code: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    timeout: float = 300.0,
) -> AsyncIterator[Dict[str, Any]]:
    """流式调用（含 function calling）。

    逐段 yield {type:delta|thinking|tool_preparing, ...}；结束时 yield
    {type:done, content, reasoning, tool_calls, usage, finish_reason}。

    失败处理（网络抖动是常态，能续就续，不轻易把整轮甩给用户）：
    - 上下文过长（400）→ 压缩后重试（尚未输出内容时）；
    - 网络 / 超时 / 429 / 5xx → 按 `llm.retry_attempts`（默认 20）固定间隔
      重试，见 `_retry_wait`；
    - **已经输出过内容后断流** → 不再直接报错，也不整轮重放（那会让前端看到
      重复文本）：把已发内容回填成 assistant 消息 + 续写提示，让模型从断点
      接着说，投递时按已发前缀去重。
    """
    current = list(messages)
    overflow_attempt = 0
    retry_attempt = 0
    max_overflow = await get_overflow_recovery_attempts()
    # 该模型档案自己的重试策略（未配置时回落全局）；配置读不到不在这里报错，
    # 交给真正的请求去抛（错误信息更准确，也不会改变既有错误路径）
    try:
        _cfg: Optional[Dict[str, Any]] = await get_llm_config(llm_code)
    except Exception:
        _cfg = None
    max_retries, backoff = _retry_policy(_cfg)
    deadline = _retry_deadline_s(_cfg)
    failing_since: Optional[float] = None
    # 已投递内容（用来做断流续写的前缀去重）
    sent_text = ""
    sent_think = ""
    sent_tools: set[str] = set()
    while True:
        emitted = False
        resume = bool(sent_text or sent_think)
        attempt_messages = current if not resume else current + [
            {"role": "assistant", "content": sent_text},
            {"role": "user", "content": _RESUME_PROMPT},
        ]
        # 本次尝试的累积缓冲与已投递游标（续写模式下要越过已发前缀才投递）
        buf_text: List[str] = []
        buf_think: List[str] = []
        pushed_text = 0
        pushed_think = 0
        new_text: List[str] = []
        new_think: List[str] = []
        try:
            async for ev in _llm_chat_stream_full_once(
                llm_code, attempt_messages, tools, temperature, max_tokens, timeout
            ):
                kind = ev.get("type")
                if kind == "delta":
                    piece = str(ev.get("content") or "")
                    if not piece:
                        continue
                    buf_text.append(piece)
                    rest, ok = _resume_split(sent_text, "".join(buf_text))
                    if not ok:
                        continue          # 还在重复已发过的部分，先不投递
                    if len(rest) <= pushed_text:
                        continue
                    out = rest[pushed_text:]
                    pushed_text = len(rest)
                    new_text.append(out)
                    emitted = True
                    yield {"type": "delta", "content": out}
                elif kind == "thinking":
                    piece = str(ev.get("content") or "")
                    if not piece:
                        continue
                    buf_think.append(piece)
                    rest, ok = _resume_split(sent_think, "".join(buf_think))
                    if not ok:
                        continue
                    if len(rest) <= pushed_think:
                        continue
                    out = rest[pushed_think:]
                    pushed_think = len(rest)
                    new_think.append(out)
                    emitted = True
                    yield {"type": "thinking", "content": out}
                elif kind == "tool_preparing":
                    tid = str(ev.get("id") or "")
                    if tid and tid in sent_tools:
                        continue          # 续写重试后重复上报同一个工具调用
                    if tid:
                        sent_tools.add(tid)
                    yield ev
                elif kind == "tool_progress":
                    # 纯心跳（参数 JSON 生成期的活跃信号），不做去重：前端只是
                    # 据此知道「上游还在写」，重复一条无害，丢掉反而像卡住。
                    yield ev
                elif kind == "done":
                    # done 里的正文按「本次调用以来的全量」给，避免调用方
                    # 因续写而只看到后半段。
                    total_text = sent_text + "".join(new_text)
                    total_think = sent_think + "".join(new_think)
                    sent_text, sent_think = total_text, total_think
                    yield {**ev, "content": total_text, "reasoning": total_think}
                    return
                else:
                    yield ev
            return
        except LlmError as e:
            emitted_any = emitted or resume
            if _in_overflow_recovery.get():
                raise
            if (not emitted_any and overflow_attempt < max_overflow
                    and is_context_too_long_error(e)):
                compacted = await _compact_with_guard(llm_code, current, overflow_attempt)
                if compacted is not None:
                    current = compacted
                    overflow_attempt += 1
                    continue
            if retry_attempt < max_retries and _is_retryable(e):
                failing_since = failing_since or time.monotonic()
                if _retry_expired(deadline, failing_since, e):
                    raise
                retry_attempt += 1
                # 已经投递过内容的失败：下次按「续写」处理（不整轮重放）。
                sent_text += "".join(new_text)
                sent_think += "".join(new_think)
                _record_retry()
                _resuming = bool(sent_text or sent_think)
                logger.warning(
                    "LLM 流式 %s 错误，第 %d/%d 次重试（%s）: %s",
                    e.kind, retry_attempt, max_retries,
                    "续写" if _resuming else "重放", e)
                try:
                    from services.agent_zero.services.events import log_event

                    log_event("llm.retry", kind=e.kind, attempt=retry_attempt,
                              max_attempts=max_retries, llm_code=llm_code,
                              conv_id=_session_id.get(), stream=True,
                              resume=_resuming)
                except Exception:
                    pass
                # 让上层（对话流）把「正在重试」变成可见状态：重试期间用户
                # 不该只看到一片空白，得知道是网络在恢复而不是卡死了。
                yield {"type": "retry", "kind": e.kind,
                       "attempt": retry_attempt, "max_attempts": max_retries,
                       "resume": _resuming, "error": str(e)[:300]}
                await _retry_wait(e, retry_attempt - 1, backoff)
                continue
            raise


async def _llm_chat_stream_full_once(
    llm_code: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    timeout: float = 300.0,
) -> AsyncIterator[Dict[str, Any]]:
    """单次流式调用（无重试）：把 pydantic-ai 事件翻成运行时既有事件语义。"""
    cfg = await get_llm_config(llm_code)
    api_base = str(cfg.get("api_base") or "")
    masked_messages, magpie = _magpie_prepare(messages)
    text_stream = magpie.stream() if magpie is not None else None
    think_stream = magpie.stream() if magpie is not None else None
    tool_defs = _tool_definitions(tools)
    settings = _model_settings(cfg, temperature, max_tokens, timeout, bool(tool_defs))
    params = ModelRequestParameters(function_tools=tool_defs)
    acc_text: List[str] = []
    acc_think: List[str] = []
    # DeepSeek 兼容端点可能把工具调用写成 DSML 正文：无论这一轮是否下发工具，
    # 标记都不能进入可见正文（见 runtime/zero/dsml.py）。
    dsml_filter = DsmlStreamFilter()
    shown_text: List[str] = []
    prepared: set[int] = set()
    names: Dict[int, str] = {}
    usage: Dict[str, Any] = {}
    finish_reason = ""
    response: Any = None
    # 工具参数（尤其 write_file 的全文）可能连续生成几十秒且没有正文/思考增量：
    # 定期发一个内部 progress 事件，让 run 的「无输出」看门狗知道上游还活着，
    # 不要把正常的长参数生成误判成卡死。
    last_progress = 0.0
    try:
        async with _model_slot(cfg):
            async with _open_model(cfg) as model:
                async with model.request_stream(
                    _to_model_messages(masked_messages), settings, params
                ) as stream:
                    async for event in stream:
                        if isinstance(event, PartStartEvent):
                            part = event.part
                            if isinstance(part, TextPart):
                                if part.content:
                                    piece = str(part.content)
                                    acc_text.append(piece)
                                    shown = dsml_filter.feed(piece) if dsml_filter else piece
                                    if shown:
                                        shown_text.append(shown)
                                        out = text_stream.feed(shown) if text_stream else shown
                                        if out:
                                            yield {"type": "delta", "content": out}
                            elif isinstance(part, ThinkingPart):
                                if part.content:
                                    piece = str(part.content)
                                    acc_think.append(piece)
                                    out = think_stream.feed(piece) if think_stream else piece
                                    if out:
                                        yield {"type": "thinking", "content": out}
                            elif isinstance(part, ToolCallPart):
                                if part.tool_name:
                                    names[event.index] = str(part.tool_name)
                                    if event.index not in prepared:
                                        prepared.add(event.index)
                                        # 工具名一出现就上报「准备调用」：参数 JSON（尤其
                                        # write_file 全文）生成期间前端不能只剩空白。
                                        yield {"type": "tool_preparing",
                                               "id": str(part.tool_call_id or ""),
                                               "name": str(part.tool_name)}
                        elif isinstance(event, PartDeltaEvent):
                            delta = event.delta
                            if isinstance(delta, TextPartDelta):
                                if delta.content_delta:
                                    piece = str(delta.content_delta)
                                    acc_text.append(piece)
                                    shown = dsml_filter.feed(piece) if dsml_filter else piece
                                    if shown:
                                        shown_text.append(shown)
                                        out = text_stream.feed(shown) if text_stream else shown
                                        if out:
                                            yield {"type": "delta", "content": out}
                            elif isinstance(delta, ThinkingPartDelta):
                                if delta.content_delta:
                                    piece = str(delta.content_delta)
                                    acc_think.append(piece)
                                    out = think_stream.feed(piece) if think_stream else piece
                                    if out:
                                        yield {"type": "thinking", "content": out}
                            elif isinstance(delta, ToolCallPartDelta):
                                piece = delta.tool_name_delta
                                if piece:
                                    names[event.index] = names.get(event.index, "") + str(piece)
                                    if event.index not in prepared:
                                        prepared.add(event.index)
                                        yield {"type": "tool_preparing",
                                               "id": str(delta.tool_call_id or ""),
                                               "name": names[event.index]}
                                now = time.monotonic()
                                if now - last_progress >= 2.0:
                                    last_progress = now
                                    yield {"type": "tool_progress",
                                           "id": str(delta.tool_call_id or ""),
                                           "index": event.index}
                    if dsml_filter is not None:
                        tail = dsml_filter.finish()
                        if tail:
                            shown_text.append(tail)
                            out = text_stream.feed(tail) if text_stream else tail
                            if out:
                                yield {"type": "delta", "content": out}
                    response = stream.get()
                    usage = _usage_event(stream.usage)
                    finish_reason = _finish_reason(stream.finish_reason)
    except LlmError:
        raise
    except Exception as e:
        raise _request_error(e, llm_code, api_base) from e

    calls = _tool_calls_of(getattr(response, "parts", []))
    if not calls:
        calls = _dsml_calls_of("".join(acc_text), tool_defs)
    finish_reason = "tool_calls" if calls else (finish_reason or "stop")
    content = "".join(shown_text) if dsml_filter is not None else "".join(acc_text)
    reasoning = "".join(acc_think)
    if magpie is not None:
        content = magpie.restore_text(content)
        reasoning = magpie.restore_text(reasoning)
        calls = magpie.restore_obj(calls)
    yield {"type": "done", "content": content,
           "reasoning": reasoning, "tool_calls": calls,
           "usage": usage, "finish_reason": finish_reason}


class ZeroLlm:
    """模型分层与降级（zero.models：plan / loop / summary / fallback）。"""

    def __init__(self, models: Dict[str, str]):
        self.models = dict(models)
        self.fallback = self.models.get("fallback") or self.models.get("loop")

    def _code(self, role: str) -> str:
        return self.models.get(role) or self.fallback or "default"

    async def chat_once(self, role: str, messages: List[Dict[str, Any]], **kw) -> LlmResult:
        """单次调用：**不重试、不降级**（压缩摘要用，时间必须有界）。

        `chat` 走的是 `llm_chat`：可重试错误默认重试 20 次、再降级 fallback，
        对压缩这种「主循环里的同步等待」来说等于把界面拖成假死。
        """
        return await _llm_chat_once(self._code(role), messages, **kw)

    async def chat(self, role: str, messages: List[Dict[str, Any]], **kw) -> LlmResult:
        """按角色取模型；失败降级 fallback 重试一次；再失败抛 LlmError。"""
        code = self._code(role)
        try:
            return await llm_chat(code, messages, **kw)
        except LlmError:
            if not self.fallback or self.fallback == code:
                raise
            logger.warning("LLM 角色 %s(%s) 失败，降级 fallback(%s)", role, code, self.fallback)
            return await llm_chat(self.fallback, messages, **kw)

    async def chat_stream(self, role: str, messages: List[Dict[str, Any]],
                          **kw) -> AsyncIterator[str]:
        """流式按角色取模型；主模型未产出任何内容即失败时降级 fallback 重试。"""
        code = self._code(role)
        got = False
        try:
            async for piece in llm_chat_stream(code, messages, **kw):
                got = True
                yield piece
        except LlmError:
            if got or not self.fallback or self.fallback == code:
                raise
            logger.warning("LLM 流式角色 %s(%s) 失败，降级 fallback(%s)",
                           role, code, self.fallback)
            async for piece in llm_chat_stream(self.fallback, messages, **kw):
                yield piece

    async def chat_stream_full(self, role: str, messages: List[Dict[str, Any]],
                               **kw) -> AsyncIterator[Dict[str, Any]]:
        """流式（含 function calling）：yield {type:delta,content} / {type:done,tool_calls}。

        主模型未产出任何内容即失败时降级 fallback 重试。
        """
        code = self._code(role)
        got = False
        try:
            async for ev in llm_chat_stream_full(code, messages, **kw):
                if ev.get("type") == "delta":
                    got = True
                yield ev
        except LlmError:
            if got or not self.fallback or self.fallback == code:
                raise
            logger.warning("LLM 流式角色 %s(%s) 失败，降级 fallback(%s)",
                           role, code, self.fallback)
            async for ev in llm_chat_stream_full(self.fallback, messages, **kw):
                yield ev

    def usage_total(self, *results: LlmResult) -> Dict[str, int]:
        out = {"input_tokens": 0, "output_tokens": 0}
        for r in results:
            out["input_tokens"] += r.usage.get("input_tokens", 0)
            out["output_tokens"] += r.usage.get("output_tokens", 0)
        return out



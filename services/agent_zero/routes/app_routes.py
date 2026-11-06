"""应用元信息 / 本地设置 / 模型目录。"""

from __future__ import annotations

import logging
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from services.agent_zero.config import get_settings
from services.agent_zero.database import get_db_path
from services.agent_zero.services import backup_service, diagnostics_service, metrics_service
from services.agent_zero.stores import settings_store, zero_store
from services.agent_zero.version import APP_NAME, APP_VERSION
from services.shared.project_paths import get_project_root

logger = logging.getLogger(__name__)

router = APIRouter()

MASK = "••••••••"


def _user_agent() -> str:
    """对外请求的统一 User-Agent：品牌名 + 真实版本号。"""
    return f"{APP_NAME}/{APP_VERSION}"

# OpenCode Console（Zen / Go）的 OpenAI 兼容端点。
# 注意：模型列表与推理端点不同——推理在 .../openai/v1，列表在 .../v1/models。
OPENCODE_ZEN_BASE = "https://opencode.ai/inference/openai/v1"
OPENCODE_ZEN_MODELS = "https://opencode.ai/inference/v1/models"
OPENCODE_GO_BASE = "https://opencode.ai/inference/go/openai/v1"
OPENCODE_GO_MODELS = "https://opencode.ai/inference/go/v1/models"
OPENCODE_GO_USAGE = "https://opencode.ai/zen/go/v1/usage"

OPENCODE_PRESETS = [
    {
        "code": "opencode-go",
        "name": "OpenCode Go",
        "group": "opencode",
        "base_url": OPENCODE_GO_BASE,
        "anthropic_base_url": "https://opencode.ai/inference/go/anthropic",
        "models_url": OPENCODE_GO_MODELS,
        "protocol": "openai-chat",
        "provider": "opencode-go",
        "supports_quota": True,
    },
    {
        "code": "opencode-zen",
        "name": "OpenCode Zen",
        "group": "opencode",
        "base_url": OPENCODE_ZEN_BASE,
        "anthropic_base_url": "https://opencode.ai/inference/anthropic",
        "models_url": OPENCODE_ZEN_MODELS,
        "protocol": "openai-chat",
        "provider": "opencode-zen",
        "supports_quota": False,
    },
]


# 订阅预设：官方 API、聚合平台、自建 / 本地网关。
# 编程订阅类的反代预设（WorkBuddy / Trae / Qoder / CodeBuddy 等）已下线，
# 不再内置也不再展示：这类端点依赖第三方本地转换器与账号登录态，不稳定且难验证。
PLATFORM_PRESETS = OPENCODE_PRESETS + [
    # ── 聚合平台 / 自建网关 ─────────────────────────────────────────────
    {
        "code": "openrouter",
        "name": "OpenRouter",
        "group": "aggregator",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "siliconflow",
        "name": "SiliconFlow 硅基流动",
        "group": "aggregator",
        "provider": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "modelscope",
        "name": "ModelScope 魔搭",
        "group": "aggregator",
        "provider": "modelscope",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "qcode",
        "name": "QCode",
        "group": "aggregator",
        "provider": "qcode",
        "base_url": "https://api.qcode.cc/openai/v1",
        "anthropic_base_url": "https://api.qcode.cc/api",
        "protocol": "openai-chat",
    },
    {
        "code": "newapi",
        "name": "New API / One API",
        "group": "local",
        "provider": "newapi",
        "base_url": "http://127.0.0.1:3000/v1",
        "protocol": "openai-chat",
        "requires_gateway": True,
        "note": "自建 New API / One API 的默认本地端口；请按实际部署修改地址。",
    },
    {
        "code": "tenrouter",
        "name": "10Router",
        "group": "local",
        "provider": "tenrouter",
        "base_url": "",
        "protocol": "openai-chat",
        "requires_gateway": True,
        "note": "填写 10Router 的 OpenAI / Anthropic 兼容入口；不同部署端口与路径可能不同。",
    },
    {
        "code": "ollama",
        "name": "Ollama",
        "group": "local",
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "protocol": "openai-chat",
        "api_key": "ollama",
    },
    {
        "code": "lmstudio",
        "name": "LM Studio",
        "group": "local",
        "provider": "lmstudio",
        "base_url": "http://127.0.0.1:1234/v1",
        "protocol": "openai-chat",
        "api_key": "lm-studio",
    },
    # ── 官方 API ────────────────────────────────────────────────────────
    {
        "code": "openai",
        "name": "OpenAI",
        "group": "official",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "anthropic",
        "name": "Anthropic",
        "group": "official",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "protocol": "anthropic",
    },
    {
        "code": "gemini",
        "name": "Google Gemini",
        "group": "official",
        "provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "protocol": "openai-chat",
    },
    {
        "code": "deepseek",
        "name": "DeepSeek",
        "group": "official",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "anthropic_base_url": "https://api.deepseek.com/anthropic",
        "protocol": "openai-chat",
    },
    {
        "code": "moonshot",
        "name": "Moonshot Kimi",
        "group": "official",
        "provider": "moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "zhipu",
        "name": "智谱 GLM",
        "group": "official",
        "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "protocol": "openai-chat",
    },
    {
        "code": "zai",
        "name": "Z.ai",
        "group": "official",
        "provider": "zai",
        "base_url": "https://api.z.ai/api/paas/v4",
        "protocol": "openai-chat",
    },
    {
        "code": "minimax",
        "name": "MiniMax",
        "group": "official",
        "provider": "minimax",
        "base_url": "https://api.minimaxi.com/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "dashscope",
        "name": "阿里云 DashScope",
        "group": "official",
        "provider": "dashscope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "ark",
        "name": "火山方舟 Ark",
        "group": "official",
        "provider": "ark",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "protocol": "openai-chat",
    },
    {
        "code": "qianfan",
        "name": "百度千帆",
        "group": "official",
        "provider": "qianfan",
        "base_url": "https://qianfan.baidubce.com/v2",
        "protocol": "openai-chat",
    },
    {
        "code": "hunyuan",
        "name": "腾讯混元",
        "group": "official",
        "provider": "hunyuan",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "xai",
        "name": "xAI Grok",
        "group": "official",
        "provider": "xai",
        "base_url": "https://api.x.ai/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "mistral",
        "name": "Mistral",
        "group": "official",
        "provider": "mistral",
        "base_url": "https://api.mistral.ai/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "groq",
        "name": "Groq",
        "group": "official",
        "provider": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "cerebras",
        "name": "Cerebras",
        "group": "official",
        "provider": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "together",
        "name": "Together AI",
        "group": "official",
        "provider": "together",
        "base_url": "https://api.together.xyz/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "fireworks",
        "name": "Fireworks AI",
        "group": "official",
        "provider": "fireworks",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "deepinfra",
        "name": "DeepInfra",
        "group": "official",
        "provider": "deepinfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "protocol": "openai-chat",
    },
    {
        "code": "novita",
        "name": "Novita AI",
        "group": "official",
        "provider": "novita",
        "base_url": "https://api.novita.ai/v3/openai",
        "protocol": "openai-chat",
    },
    {
        "code": "nebius",
        "name": "Nebius",
        "group": "official",
        "provider": "nebius",
        "base_url": "https://api.studio.nebius.com/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "hyperbolic",
        "name": "Hyperbolic",
        "group": "official",
        "provider": "hyperbolic",
        "base_url": "https://api.hyperbolic.xyz/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "chutes",
        "name": "Chutes",
        "group": "official",
        "provider": "chutes",
        "base_url": "https://llm.chutes.ai/v1",
        "protocol": "openai-chat",
    },
    {
        "code": "github-models",
        "name": "GitHub Models",
        "group": "official",
        "provider": "github-models",
        "base_url": "https://models.github.ai/inference",
        "protocol": "openai-chat",
    },
]


# OpenCode Go 额度缓存：60s 内复用，避免每次打开设置都打上游。
_QUOTA_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_QUOTA_CACHE_TTL = 60.0
_QUOTA_RETRIES = 3


def _opencode_family(base_url: str) -> str:
    """返回 opencode-go / opencode-zen / ''。"""
    base = str(base_url or "").strip().rstrip("/").lower()
    if base.startswith("https://opencode.ai/inference/go/") or base.startswith(
            "https://opencode.ai/inference/go"):
        return "opencode-go"
    if base.startswith("https://opencode.ai/inference/"):
        return "opencode-zen"
    return ""


def _models_url_for(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    family = _opencode_family(base)
    if family == "opencode-go":
        return OPENCODE_GO_MODELS
    if family == "opencode-zen":
        return OPENCODE_ZEN_MODELS
    return f"{base}/models"


def _opencode_protocol(model_id: str, family: str) -> tuple[str, bool, str]:
    """按 OpenCode 文档推断模型的 API 协议。

    现在 OpenAI Chat / Responses 与 Anthropic Messages 都已支持；返回
    (protocol, supported, note)。Claude 与部分 Qwen / MiniMax 走 Anthropic
    Messages；Gemini 仍需要 Google 原生协议，暂时标记为不可用。
    """
    mid = str(model_id or "").strip().lower()
    if not mid:
        return "openai-chat", False, "缺少模型 ID"
    if mid.startswith("gemini-"):
        return "google", False, "该模型走 Google Gemini API，AtlasX 暂不支持"
    if mid.startswith("claude-"):
        return "anthropic", True, ""
    if family == "opencode-go":
        if mid in {"minimax-m3"} or mid.startswith(
                ("qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus")):
            return "anthropic", True, ""
    elif family == "opencode-zen":
        if mid.startswith(("qwen3.8-flash", "qwen3.6-plus", "qwen3.5-plus")):
            return "anthropic", True, ""
    if mid.startswith(("gpt-", "grok-", "muse-")):
        return "openai-response", True, ""
    return "openai-chat", True, ""


def _config_path() -> str:
    root = str(get_project_root())
    return os.environ.get("AI_PLATFORM_CONFIG_YAML") or os.path.join(
        root, "config", "agent_zero", "config.yaml"
    )


def _public_model_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """API 视图：密钥只返回掩码，不返回明文。"""
    out = dict(profile)
    out["api_key"] = MASK if out.pop("has_api_key", False) else ""
    out["effective_api_key"] = MASK if out.pop("effective_has_api_key", False) else ""
    return out


def _public_subscription(sub_id: str, sub: Dict[str, Any]) -> Dict[str, Any]:
    """订阅视图：同样不把明文密钥回传给前端。"""
    out = dict(sub)
    has_key = bool(str(out.pop("api_key", "") or "").strip())
    out["id"] = sub_id
    out["has_api_key"] = has_key
    out["api_key"] = MASK if has_key else ""
    return out


def _llm_subscriptions() -> List[Dict[str, Any]]:
    from services.agent_zero.runtime.zero import llm as llm_mod

    raw = llm_mod._subscriptions_map(llm_mod._llm_section())  # noqa: SLF001
    return [_public_subscription(str(sid), sub) for sid, sub in raw.items()
            if isinstance(sub, dict)]


def _llm_view() -> Dict[str, Any]:
    from services.agent_zero.runtime.zero import llm as llm_mod

    profiles = llm_mod.list_model_profiles()
    active_code = llm_mod.get_active_model_code()
    active = next((p for p in profiles if p.get("code") == active_code), None)
    if active is None and profiles:
        active = profiles[0]
    if active is None:
        return {
            "base_url": "",
            "model": "",
            "api_key": "",
            "context_window": 0,
            "max_tokens": 0,
            "protocol": "openai-chat",
            "vision": False,
            "thinking_levels": [],
            "configured": False,
            "active_model": "",
            "models": [],
            "subscriptions": _llm_subscriptions(),
        }
    return {
        "base_url": str(active.get("effective_base_url") or ""),
        "model": str(active.get("effective_model") or ""),
        "api_key": MASK if active.get("effective_has_api_key") else "",
        "context_window": int(active.get("effective_context_window") or 0),
        "max_tokens": int(active.get("effective_max_tokens") or 0),
        "protocol": str(active.get("effective_protocol") or "openai-chat"),
        "vision": bool(active.get("effective_vision")),
        "thinking_levels": list(active.get("effective_thinking_levels") or []),
        "configured": bool(active.get("configured")),
        "active_model": str(active.get("code") or active_code or ""),
        "models": [_public_model_profile(p) for p in profiles],
        "subscriptions": _llm_subscriptions(),
    }


@router.get("/app/info")
def app_info(request: Request):
    settings = get_settings()
    root = str(get_project_root())
    ws = zero_store.get_workspace(zero_store.DEFAULT_WORKSPACE_ID) or {}
    default_root = str(Path(ws.get("root_path")).expanduser().resolve()) if ws.get("root_path") else ""
    chat_only = not default_root
    return {"code": 0, "data": {
        "name": APP_NAME,
        "version": APP_VERSION,
        "mode": "local",
        "port": int(request.url.port or 0),
        "url": str(request.base_url).rstrip("/"),
        "config_path": os.path.relpath(_config_path(), root),
        "db_path": os.path.relpath(get_db_path(), root),
        "workspace": {
            "id": ws.get("id") or zero_store.DEFAULT_WORKSPACE_ID,
            "name": ws.get("name") or "Workspace",
            "root_path": default_root,
            "chat_only": chat_only,
            "managed": chat_only,
            "archived": bool(ws.get("archived")),
            "is_default": True,
            "conversation_count": 0,
        },
        "llm": _llm_view(),
        "limits": {
            "max_concurrent_runs": settings.max_concurrent_conversation_runs,
            "context_compress_percent": settings.context_compress_percent,
            "autonomous_run_max_seconds": settings.autonomous_run_max_seconds,
        },
        "tools": _tool_names(has_working_dir=not chat_only),
        "tool_catalog": _tool_catalog(has_working_dir=not chat_only),
    }}


@router.get("/app/models")
def app_models():
    from services.agent_zero.runtime.zero import llm as llm_mod

    try:
        profiles = llm_mod.list_model_profiles()
        return {"code": 0, "data": {
            "items": [_public_model_profile(p) for p in profiles],
            "active": llm_mod.get_active_model_code(),
            "subscriptions": _llm_subscriptions(),
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/app/settings")
def get_app_settings():
    return {"code": 0, "data": _llm_view()}


@router.get("/app/ui")
def get_ui_prefs():
    """全局 UI 偏好（主题、布局、收缩状态等），保存在 app_settings 表。"""
    prefs = settings_store.get("ui", {})
    return {"code": 0, "data": prefs if isinstance(prefs, dict) else {}}


class UiPrefsBody(BaseModel):
    prefs: Dict[str, Any] = Field(default_factory=dict)


@router.put("/app/ui")
def put_ui_prefs(body: UiPrefsBody):
    prefs = body.prefs or {}
    if not isinstance(prefs, dict):
        raise HTTPException(status_code=400, detail="prefs must be an object")
    # 限制单条大小，防止异常数据撑爆表
    if len(json.dumps(prefs, ensure_ascii=False)) > 20000:
        raise HTTPException(status_code=400, detail="prefs too large")
    settings_store.put("ui", prefs)
    return {"code": 0, "data": prefs}


# 主题单独一条：V3 每次启动的端口可能变，localStorage 按 origin 隔离，
# 只靠 localStorage 会看起来「主题没保存」。服务器是跨端口 / 跨重启的持久源。
_THEME_IDS = {"dark", "light", "midnight", "slate", "ocean", "forest", "sepia", "violet"}


class ThemePrefsBody(BaseModel):
    theme: str = Field("dark", max_length=40)
    themeSchedule: Dict[str, Any] = Field(default_factory=dict)


@router.get("/app/theme")
def get_theme_prefs():
    prefs = settings_store.get("ui", {})
    if not isinstance(prefs, dict):
        prefs = {}
    return {"code": 0, "data": {
        "theme": prefs.get("theme") or "dark",
        "themeSchedule": prefs.get("themeSchedule") or {},
    }}


@router.put("/app/theme")
def put_theme_prefs(body: ThemePrefsBody):
    theme = str(body.theme or "").strip()
    if theme not in _THEME_IDS:
        theme = "dark"
    prefs = settings_store.get("ui", {})
    if not isinstance(prefs, dict):
        prefs = {}
    next_prefs = dict(prefs)
    next_prefs["theme"] = theme
    next_prefs["themeSchedule"] = body.themeSchedule or {}
    if len(json.dumps(next_prefs, ensure_ascii=False)) > 20000:
        raise HTTPException(status_code=400, detail="prefs too large")
    settings_store.put("ui", next_prefs)
    return {"code": 0, "data": {
        "theme": next_prefs["theme"],
        "themeSchedule": next_prefs["themeSchedule"],
    }}


class ModelProfileBody(BaseModel):
    code: str = Field(..., min_length=1, max_length=80)
    name: Optional[str] = Field(None, max_length=120)
    model: Optional[str] = Field(None, max_length=200)
    base_url: Optional[str] = Field(None, max_length=500)
    api_key: Optional[str] = Field(None, max_length=1000)
    context_window: Optional[int] = Field(None, ge=0, le=10_000_000)
    max_tokens: Optional[int] = Field(None, ge=0, le=1_000_000)
    protocol: Optional[str] = Field(None, max_length=40)
    vision: Optional[bool] = None
    thinking_levels: Optional[List[str]] = Field(None, max_length=16)
    channel_id: Optional[str] = Field(None, max_length=80)
    channel_name: Optional[str] = Field(None, max_length=120)
    subscription_id: Optional[str] = Field(None, max_length=80)


class SubscriptionBody(BaseModel):
    """一个订阅 = 共享 Base URL / Key 的账号；下面可以挂多个模型。"""

    id: str = Field(..., min_length=1, max_length=80)
    name: Optional[str] = Field(None, max_length=120)
    provider: Optional[str] = Field(None, max_length=80)
    protocol: Optional[str] = Field(None, max_length=40)
    base_url: Optional[str] = Field(None, max_length=500)
    anthropic_base_url: Optional[str] = Field(None, max_length=500)
    api_key: Optional[str] = Field(None, max_length=1000)
    models_url: Optional[str] = Field(None, max_length=500)
    note: Optional[str] = Field(None, max_length=500)


class SettingsBody(BaseModel):
    base_url: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = Field(None, max_length=200)
    api_key: Optional[str] = Field(None, max_length=1000)
    context_window: Optional[int] = Field(None, ge=0, le=10_000_000)
    max_tokens: Optional[int] = Field(None, ge=0, le=1_000_000)
    protocol: Optional[str] = Field(None, max_length=40)
    vision: Optional[bool] = None
    thinking_levels: Optional[List[str]] = Field(None, max_length=16)
    channel_id: Optional[str] = Field(None, max_length=80)
    channel_name: Optional[str] = Field(None, max_length=120)
    subscription_id: Optional[str] = Field(None, max_length=80)
    remove_default: bool = False
    models: Optional[List[ModelProfileBody]] = None
    subscriptions: Optional[List[SubscriptionBody]] = None
    active_model: Optional[str] = Field(None, max_length=80)


class ActivateModelBody(BaseModel):
    code: Optional[str] = Field(None, max_length=80)


class DiscoverModelsBody(BaseModel):
    """读取 OpenAI 兼容端点的 /models 列表。

    卡片里可能还没保存（没有 code），所以允许直接传 base_url / api_key；
    api_key 传掩码或空时，回落到该 code（或默认模型）已保存的密钥。
    """

    code: Optional[str] = Field(None, max_length=80)
    base_url: Optional[str] = Field(None, max_length=500)
    api_key: Optional[str] = Field(None, max_length=1000)
    protocol: Optional[str] = Field(None, max_length=40)


def _new_subscription_map(body: SettingsBody,
                          existing: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """把前端订阅列表转成按 id 索引的存储结构；空字段沿用已保存值。"""
    out: Dict[str, Dict[str, Any]] = {}
    for sub in body.subscriptions or []:
        sub_id = str(sub.id or "").strip()
        if not sub_id:
            continue
        if sub_id in out:
            raise HTTPException(status_code=400, detail=f"订阅 id 重复：{sub_id}")
        prev = existing.get(sub_id) if isinstance(existing.get(sub_id), dict) else {}
        item: Dict[str, Any] = {}
        for key in ("name", "provider", "protocol", "base_url",
                    "anthropic_base_url", "models_url", "note"):
            value = str(getattr(sub, key, "") or "").strip()
            if value:
                item[key] = value
            elif prev.get(key):
                item[key] = prev[key]
        key = str(sub.api_key or "").strip()
        if key and key != MASK:
            item["api_key"] = key
        elif prev.get("api_key"):
            item["api_key"] = prev["api_key"]
        out[sub_id] = item
    return out


def _new_model_map(body: SettingsBody, existing: Dict[str, Any],
                   subscriptions: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """把前端档案列表转成存储结构；空字段表示继承默认配置。

    ``subscriptions`` 是**本次保存后**生效的订阅表：挂了订阅且订阅上有密钥的
    模型不再保存自己的 api_key（密钥只有一个来源，见 llm._resolve_llm_config）。
    """
    from services.agent_zero.runtime.zero import llm as llm_mod

    subs = subscriptions or {}
    out: Dict[str, Dict[str, Any]] = {}
    for profile in body.models or []:
        code = str(profile.code or "").strip()
        if not code or code == "default":
            continue
        if code in out:
            raise HTTPException(status_code=400, detail=f"模型 code 重复：{code}")
        prev = llm_mod._entry_dict(existing.get(code))  # noqa: SLF001
        item: Dict[str, Any] = {}

        name = str(profile.name or "").strip()
        model = str(profile.model or "").strip()
        base_url = str(profile.base_url or "").strip().rstrip("/")
        key = str(profile.api_key or "").strip()
        channel_id = str(profile.channel_id or "").strip()
        channel_name = str(profile.channel_name or "").strip()
        subscription_id = str(profile.subscription_id or "").strip() or channel_id
        if subscription_id and not channel_id:
            channel_id = subscription_id
        if name:
            item["name"] = name
        if model:
            item["model"] = model
        if base_url:
            item["base_url"] = base_url
        if channel_id:
            item["channel_id"] = channel_id
        if channel_name:
            item["channel_name"] = channel_name
        if subscription_id:
            item["subscription_id"] = subscription_id
        # 订阅上的密钥优先：模型档案里历史遗留的 key 不再落库（否则改订阅 key
        # 不生效）；订阅存在但没填 key 时，仍允许模型档案/默认段提供密钥。
        sub_entry = subs.get(subscription_id) if subscription_id else None
        sub_key = (str(sub_entry.get("api_key") or "").strip()
                   if isinstance(sub_entry, dict) else "")
        if sub_key:
            pass
        elif key and key != MASK:
            item["api_key"] = key
        elif prev.get("api_key"):
            # 前端只拿到掩码；未改密钥时保留原值，也保留「继承默认密钥」语义。
            item["api_key"] = prev["api_key"]
        elif channel_id and not subscription_id:
            # 同一渠道下新增的模型：密钥在渠道级维护，前端拿到的是掩码，
            # 从该渠道已有档案里复制真实密钥（不回传给前端）。
            for raw_other in (existing or {}).values():
                other = llm_mod._entry_dict(raw_other)  # noqa: SLF001
                if (str(other.get("channel_id") or "") == channel_id
                        and other.get("api_key")):
                    item["api_key"] = other["api_key"]
                    break
        if profile.context_window:
            item["context_window"] = profile.context_window
        if profile.max_tokens:
            item["max_tokens"] = profile.max_tokens
        proto = str(profile.protocol or "").strip()
        if proto:
            item["protocol"] = llm_mod._normalize_protocol(proto)  # noqa: SLF001
        elif prev.get("protocol"):
            item["protocol"] = prev["protocol"]
        if profile.vision is not None:
            item["vision"] = bool(profile.vision)
        elif "vision" in prev:
            item["vision"] = bool(prev.get("vision"))
        if profile.thinking_levels is not None:
            item["thinking_levels"] = llm_mod.normalize_thinking_levels(
                profile.thinking_levels)
        elif "thinking_levels" in prev:
            item["thinking_levels"] = llm_mod.normalize_thinking_levels(
                prev.get("thinking_levels"))
        # 前端不认识的进阶项（重试 / 超时 / 连接池）原样保留：在「设置 → 模型」
        # 里改个名字，不该把 config.yaml 里调好的这些参数抹掉。
        for extra in ("retry_attempts", "retry_backoff_s", "retry_deadline_s",
                      "connect_timeout_s", "request_timeout_s",
                      "max_connections", "max_keepalive"):
            if extra not in item and prev.get(extra) is not None:
                item[extra] = prev[extra]
        out[code] = item
    return out


@router.put("/app/settings")
def update_app_settings(body: SettingsBody):
    from services.agent_zero.runtime.zero import llm as llm_mod

    # 基线取**存储原值**（api_key 还是 keychain: 占位符），不是解析后的配置：
    # 否则 Keychain 暂时读不到时，这里会把解析出的空串写回去，等于静默清空密钥。
    stored = settings_store.get("llm", {})
    current = dict(stored) if isinstance(stored, dict) else {}
    patch: Dict[str, Any] = {}
    for key, value in body.model_dump(
            exclude={"models", "subscriptions", "active_model", "remove_default"}).items():
        if value is None:
            continue
        if key == "api_key" and (not value or value == MASK):
            continue
        if isinstance(value, str):
            value = value.strip()
        if isinstance(value, str) and not value:
            continue
        patch[key] = value
    if "protocol" in patch:
        patch["protocol"] = llm_mod._normalize_protocol(patch["protocol"])  # noqa: SLF001
    if "thinking_levels" in patch:
        patch["thinking_levels"] = llm_mod.normalize_thinking_levels(
            patch["thinking_levels"])

    # 先算订阅，再算模型：模型是否需要保存自己的 key 取决于订阅上有没有 key。
    subs_map: Optional[Dict[str, Any]] = None
    if body.subscriptions is not None:
        existing_subs = llm_mod._subscriptions_map(  # noqa: SLF001
            llm_mod._llm_section())
        subs_map = _new_subscription_map(body, existing_subs)
        patch["subscriptions"] = subs_map

    if body.models is not None:
        existing = llm_mod._models_map(llm_mod._llm_section())  # noqa: SLF001
        effective_subs = (subs_map if subs_map is not None
                          else llm_mod._subscriptions_map(  # noqa: SLF001
                              llm_mod._llm_section()))
        patch["models"] = _new_model_map(body, existing, effective_subs)

    if body.remove_default:
        patch["hidden_default"] = True

    if body.active_model is not None:
        patch["active_model"] = str(body.active_model or "").strip()

    if "models" in patch or "active_model" in patch or "hidden_default" in patch:
        effective_models = (
            patch["models"] if "models" in patch
            else llm_mod._models_map(llm_mod._llm_section())  # noqa: SLF001
        )
        hidden = bool(patch.get("hidden_default")
                      or current.get("hidden_default"))
        non_default = [c for c in effective_models if c != "default"]
        active = str(patch.get(
            "active_model",
            current.get("active_model")
            or llm_mod._llm_section().get("active_model")  # noqa: SLF001
            or "",
        )).strip()
        if hidden and (not active or active == "default"):
            active = non_default[0] if non_default else ""
        if active and active != "default" and active not in effective_models:
            if body.active_model is not None:
                raise HTTPException(status_code=400,
                                    detail=f"模型不存在：{active}")
            active = non_default[0] if non_default else ""
        if not active and not hidden:
            active = "default"
        patch["active_model"] = active

    if not patch:
        return {"code": 0, "data": _llm_view()}
    current.update(patch)
    settings_store.put("llm", current)
    return {"code": 0, "data": _llm_view()}


@router.post("/app/models/discover")
async def discover_models(body: DiscoverModelsBody):
    """拉取 OpenAI 兼容端点的模型列表（GET {base_url}/models）。"""
    import httpx

    from services.agent_zero.runtime.zero import llm as llm_mod

    base_url = str(body.base_url or "").strip().rstrip("/")
    api_key = str(body.api_key or "").strip()
    if api_key == MASK:
        api_key = ""
    code = str(body.code or "").strip()

    if (not base_url or not api_key) and code:
        try:
            cfg = await llm_mod.get_llm_config(code) or {}
        except Exception:
            cfg = {}
        cfg_base = str(cfg.get("base_url") or "").rstrip("/")
        if not base_url:
            base_url = cfg_base
        # 只有端点一致时才复用已保存的密钥；换了 Base URL 还发旧 key，
        # 会把本地密钥泄露给另一个服务（也会导致 Invalid credential）。
        if not api_key and base_url and base_url == cfg_base:
            api_key = str(cfg.get("api_key") or "")
    if not base_url:
        raise HTTPException(status_code=400,
                            detail="缺少 Base URL：先填写端点地址，或先保存一个模型档案")
    if api_key == "EMPTY":
        api_key = ""

    family = _opencode_family(base_url)
    models_url = _models_url_for(base_url)
    headers = {"Accept": "application/json", "User-Agent": _user_agent()}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(models_url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = (e.response.text or "")[:300]
        except Exception:
            pass
        raise HTTPException(
            status_code=502,
            detail=f"模型列表请求失败（HTTP {e.response.status_code}）：{detail}",
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"模型列表请求失败：{type(e).__name__}: {e}",
        ) from e

    raw_items: Any = payload
    if isinstance(payload, dict):
        raw_items = (payload.get("data") if "data" in payload
                     else payload.get("models"))
    if not isinstance(raw_items, list):
        raw_items = []

    models: List[Dict[str, Any]] = []
    seen = set()
    for item in raw_items:
        if isinstance(item, str):
            mid, owned = item.strip(), ""
        elif isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
            owned = str(item.get("owned_by") or item.get("owner") or "")
        else:
            continue
        if not mid or mid in seen:
            continue
        seen.add(mid)
        entry = {"id": mid, "owned_by": owned}
        if family:
            protocol, supported, note = _opencode_protocol(mid, family)
            entry.update({"protocol": protocol, "supported": supported})
            if note:
                entry["note"] = note
        models.append(entry)
    models.sort(key=lambda m: m["id"].lower())
    return {"code": 0, "data": {
        "base_url": base_url,
        "models_url": models_url,
        "provider": family,
        "count": len(models),
        "models": models,
    }}


@router.get("/app/models/presets")
def model_presets():
    """内置的订阅平台预设（官方 API、聚合平台、本地网关、OpenCode）。"""
    return {"code": 0, "data": {"items": PLATFORM_PRESETS}}


_QUOTA_WINDOWS = {
    "five_hour": ("rolling", "rolling5h", "rolling5hours", "rolling5hrs",
                  "fivehour", "fivehours", "5h", "5hour", "rolling5"),
    "weekly": ("weekly", "week", "oneweek", "1w", "weeklyusage", "weekusage"),
    "monthly": ("monthly", "month", "onemonth", "1m", "monthlyusage", "monthusage"),
}


def _quota_norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _quota_num(obj: Dict[str, Any], *names: str) -> Optional[float]:
    for name in names:
        if name not in obj:
            continue
        value = obj.get(name)
        if isinstance(value, dict):
            for inner in ("dollars", "amount", "value", "usd", "total"):
                if inner in value:
                    value = value[inner]
                    break
            else:
                continue
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(str(value).replace("$", "").replace(",", "").strip())
        except (TypeError, ValueError):
            continue
    return None


def _quota_window(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    used = _quota_num(raw, "usageDollars", "usage_dollars", "usedDollars", "used",
                      "usage", "spent", "cost")
    limit = _quota_num(raw, "limitDollars", "limit_dollars", "limit", "total",
                       "allowance", "cap")
    percent = _quota_num(raw, "usagePercent", "usage_percent", "percent", "usedPercent")
    remaining = _quota_num(raw, "remainingDollars", "remaining_dollars", "remaining")
    reset_in = _quota_num(raw, "resetInSec", "reset_in_sec", "resetInSeconds",
                          "reset_in", "resetIn", "secondsUntilReset")
    if remaining is None and used is not None and limit is not None:
        remaining = limit - used
    if used is None and remaining is not None and limit is not None:
        used = limit - remaining
    if percent is None and used is not None and limit:
        percent = used / limit * 100.0
    if percent is not None:
        percent = float(percent)
        if percent <= 1.0 and limit and used is not None and percent < 1.0:
            percent *= 100.0
        percent = max(0.0, min(100.0, percent))
    if reset_in is None:
        reset_at = raw.get("resetAt") or raw.get("reset_at") or raw.get("resetsAt")
        if reset_at:
            try:
                from datetime import datetime, timezone

                text = str(reset_at).replace("Z", "+00:00")
                when = datetime.fromisoformat(text)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                reset_in = max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
            except Exception:
                pass
    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "percent": percent,
        "reset_in_sec": int(reset_in) if reset_in is not None else None,
        "status": str(raw.get("status") or ""),
    }


def _quota_find_windows(payload: Any) -> Dict[str, Dict[str, Any]]:
    """在任意嵌套的 JSON 里找 5h / week / month 三个窗口。"""
    found: Dict[str, Dict[str, Any]] = {}

    def _visit(obj: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                norm = _quota_norm(key)
                for name, aliases in _QUOTA_WINDOWS.items():
                    if name not in found and norm in aliases and isinstance(value, dict):
                        found[name] = value
                _visit(value, depth + 1)
            return
        if isinstance(obj, list):
            for item in obj:
                if not isinstance(item, dict):
                    continue
                label = (item.get("window") or item.get("period") or item.get("key")
                         or item.get("name") or item.get("type") or "")
                norm = _quota_norm(label)
                for name, aliases in _QUOTA_WINDOWS.items():
                    if name not in found and norm in aliases:
                        found[name] = item
                _visit(item, depth + 1)

    _visit(payload)
    return found


@router.get("/app/models/usage")
async def model_usage(code: str = "", refresh: bool = False):
    """OpenCode Go 的滚动额度：5 小时 / 1 周 / 1 月。

    OpenCode 实际返回的是百分比 + 重置时间（不返回美元金额），所以前端按
    percent 画进度条；如果以后返回 usageDollars / limitDollars，也会一并展示。
    """
    import asyncio
    import time

    import httpx

    from services.agent_zero.runtime.zero import llm as llm_mod

    resolved = str(code or "").strip() or llm_mod.get_active_model_code()
    cache_key = resolved
    if not refresh:
        cached = _QUOTA_CACHE.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < _QUOTA_CACHE_TTL:
            return {"code": 0, "data": cached[1]}
    try:
        cfg = await llm_mod.get_llm_config(resolved) or {}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"模型配置读取失败：{e}") from e
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    api_key = str(cfg.get("api_key") or "")
    if _opencode_family(base_url) != "opencode-go":
        raise HTTPException(status_code=400,
                            detail="该模型不是 OpenCode Go 端点")
    if not api_key or api_key == "EMPTY":
        raise HTTPException(status_code=400,
                            detail="需要 OpenCode Go API key 才能查询额度")

    headers = {
        "Accept": "application/json",
        "User-Agent": _user_agent(),
        "Authorization": f"Bearer {api_key}",
    }
    payload: Optional[Dict[str, Any]] = None
    last_detail = ""
    for attempt in range(_QUOTA_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(OPENCODE_GO_USAGE, headers=headers)
                if resp.status_code in (401, 403):
                    raise HTTPException(
                        status_code=400,
                        detail="OpenCode Go API key 无效，或该 Key 未开通 Go 套餐",
                    )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    payload = data
                    break
                last_detail = "额度接口返回的不是 JSON 对象"
        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = (e.response.text or "")[:300]
            except Exception:
                pass
            last_detail = f"HTTP {e.response.status_code}: {detail}"
            # 4xx（除限流）不重试
            if e.response.status_code not in (408, 429) and e.response.status_code < 500:
                raise HTTPException(
                    status_code=502,
                    detail=f"OpenCode Go 额度查询失败（{last_detail}）",
                ) from e
        except Exception as e:
            last_detail = f"{type(e).__name__}: {e}"
        if attempt < _QUOTA_RETRIES - 1:
            await asyncio.sleep(0.8 * (attempt + 1))

    if payload is None:
        raise HTTPException(
            status_code=502,
            detail=f"OpenCode Go 额度查询失败（已自动重试 {_QUOTA_RETRIES} 次）：{last_detail}",
        )

    raw_windows = _quota_find_windows(payload)
    windows: Dict[str, Dict[str, Any]] = {}
    for name, raw in raw_windows.items():
        window = _quota_window(raw)
        if window is not None:
            windows[name] = window
    if not windows:
        keys = ", ".join(str(k) for k in list(payload.keys())[:20])
        raise HTTPException(
            status_code=502,
            detail=f"额度接口未返回 5h/week/month 数据（顶层字段：{keys or '空'}）",
        )

    subscribed = payload.get("subscribedAt") or payload.get("subscribed_at")
    result = {
        "provider": "opencode-go",
        "windows": windows,
        "subscribed_at": subscribed,
    }
    _QUOTA_CACHE[cache_key] = (time.monotonic(), result)
    return {"code": 0, "data": result}


@router.post("/app/models/active")
def activate_app_model(body: ActivateModelBody):
    from services.agent_zero.runtime.zero import llm as llm_mod

    code = str(body.code or "default").strip() or "default"
    available = {p["code"] for p in llm_mod.list_model_profiles()}
    if code not in available:
        raise HTTPException(status_code=404, detail=f"模型不存在：{code}")
    current = settings_store.llm_override()
    current["active_model"] = code
    settings_store.put("llm", current)
    return {"code": 0, "data": _llm_view()}


@router.get("/app/usage/stats")
def usage_stats():
    from services.agent_zero.services import usage_service

    return {"code": 0, "data": usage_service.build_stats()}


@router.get("/app/metrics")
def app_metrics(hours: int = 24):
    """本地可观测指标：24h 运行成功率/耗时/错误分类/队列/备份。"""
    return {"code": 0, "data": metrics_service.run_metrics(hours)}


@router.get("/app/diagnostics")
def app_diagnostics():
    """导出诊断包（zip）：脱敏设置 + 健康/指标 + 最近运行 + 最近日志。"""
    data = diagnostics_service.build_bundle()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{diagnostics_service.bundle_name()}"'},
    )


@router.get("/app/backups")
def app_backups():
    return {"code": 0, "data": backup_service.stats(),
            "items": backup_service.list_backups()}


@router.post("/app/backups")
def app_backup_now():
    try:
        info = backup_service.create_backup("manual")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"备份失败: {e}")
    return {"code": 0, "data": info}


@router.get("/app/tools")
def app_tools():
    from services.agent_zero.tools import registry

    return {"code": 0, "data": {"items": registry.list_tools()}}


def _tool_catalog(has_working_dir: bool = True) -> Dict[str, Any]:
    """设置 → 工具页的数据（包 + 独立工具）。"""
    try:
        from services.agent_zero.tools import packs

        return packs.catalog(has_working_dir=has_working_dir)
    except Exception:
        return {"packs": [], "standalone": [], "chat_only": not has_working_dir}


def _tool_names(has_working_dir: bool = True) -> list:
    """设置 → 运行时里展示的「工具」。

    开了工具包就展示**包名**（与模型实际看到的一致），而不是逐个工具——
    否则界面上永远是一长串 read_file / shell，看起来像没用上工具包。
    """
    try:
        from services.agent_zero.tools import packs, registry

        if not packs.enabled():
            return [t["code"] for t in registry.list_tools()]
        names: list = []
        for pack in packs.PACKS:
            usable = any(
                has_working_dir or code not in registry.WORKSPACE_REQUIRED_TOOLS
                for code in pack.ops.values())
            if usable:
                names.append(pack.code)
        names.extend(packs.unpacked_codes(
            registry.known_codes(), has_working_dir=has_working_dir))
        return names
    except Exception:
        return []


@router.get("/app/runtime")
async def runtime_status():
    settings = get_settings()
    stats: Dict[str, Any] = {}
    try:
        from services.agent_zero.task_layer.conv_worker import get_worker

        stats = get_worker().stats()
    except Exception:
        logger.debug("读取对话运行容量失败", exc_info=True)
    running = int(stats.get("running") or 0)
    queued = int(stats.get("queued") or 0)
    live = int(stats.get("live") or 0)
    slots_max = int(stats.get("slots_max") or settings.max_concurrent_conversation_runs)
    return {"code": 0, "data": {
        "scheduler": {
            "backend": "in-process",
            "queued": queued,
            "running": running,
            "slots_max": slots_max,
            "slots_available": max(0, slots_max - max(running, live)),
        },
        "worker": {"is_running": bool(stats.get("is_running"))},
        "sessions": _session_counts(),
    }}


def _session_counts() -> dict:
    out = {"conversations": None, "runs": None, "runs_active": None}
    try:
        from services.agent_zero.task_layer import conversation as conv_mod
        from services.agent_zero.task_layer import run_service

        out["conversations"] = conv_mod.count_conversations()
        out["runs"] = run_service.count_runs()
        out["runs_active"] = run_service.count_runs(status=run_service.STATUS_RUNNING)
    except Exception:
        logger.debug("会话计数读取失败", exc_info=True)
    return out


# ── 权限（会话级危险操作档位）────────────────────────────────────────────


class PermissionBody(BaseModel):
    default: Optional[str] = None
    run_mode: Optional[str] = None
    approval_timeout_s: Optional[float] = None


@router.get("/app/permissions")
def get_permissions():
    """全局策略（默认档位/运行模式 = 上一条会话的值）；会话级值见 conversations 表。"""
    from services.agent_zero.task_layer import conversation, permissions

    policy = permissions.get_policy()
    policy["run_mode"] = conversation.last_run_mode()
    return {"code": 0, "data": {"policy": policy}}


@router.put("/app/permissions")
def put_permissions(body: PermissionBody):
    """设置默认档位 / 默认运行模式（无当前会话时生效，新会话继承）/ 审批超时。"""
    from services.agent_zero.task_layer import conversation, permissions

    patch = body.model_dump(exclude_none=True)
    run_mode = patch.pop("run_mode", None)
    try:
        policy = permissions.set_policy(patch)
        if run_mode:
            policy["run_mode"] = conversation.set_run_mode_default(run_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": {"policy": policy}}


# ── Superagent：跨会话待授权 / 未读 / 运行 / 授权策略 ──────────────────


@router.post("/app/global-agent")
def ensure_global_agent():
    """获取唯一的全局 Agent 会话（不存在则创建，归档则恢复，重复则收敛）。"""
    from services.agent_zero.task_layer import conversation

    return {"code": 0, "data": conversation.ensure_global_agent_conversation()}


@router.get("/app/attention")
def app_attention(limit: int = 100):
    """Superagent 面板数据：跨会话的待审批、待回答、未读、运行与授权策略。"""
    from services.agent_zero.services import attention_service

    return {"code": 0, "data": attention_service.snapshot(limit)}


class CodegraphBody(BaseModel):
    workspace_id: str = ""


class CodegraphActionBody(BaseModel):
    workspace_id: str = ""
    action: str = "update"


@router.get("/app/codegraph")
def get_codegraph(workspace_id: str = "", entries: bool = False):
    from services.agent_zero.services import codemap

    ws_id = str(workspace_id or "").strip()
    if not ws_id:
        raise HTTPException(status_code=400, detail="缺少 workspace_id")
    data = codemap.status(ws_id)
    if entries:
        data["entries"] = [
            {
                "path": e.get("path") or "",
                "symbols": (e.get("symbols") or [])[:8],
                "lines": {s: (e.get("symbol_lines") or {}).get(s, 1)
                          for s in (e.get("symbols") or [])[:8]},
                "imports": (e.get("imports") or [])[:8],
            }
            for e in codemap.get_map(ws_id)
        ]
    return {"code": 0, "data": data}


@router.post("/app/codegraph/action")
def codegraph_action(body: CodegraphActionBody):
    from services.agent_zero.services import codemap

    ws_id = str(body.workspace_id or "").strip()
    if not ws_id:
        raise HTTPException(status_code=400, detail="缺少 workspace_id")
    action = str(body.action or "update").strip().lower()
    try:
        if action == "init":
            codemap.get_map(ws_id)
        elif action == "rebuild":
            codemap.refresh(ws_id)
        else:
            codemap.invalidate(ws_id)
            codemap.get_map(ws_id)
        return {"code": 0, "data": codemap.status(ws_id)}
    except Exception as e:
        logger.exception("CodeGraph 操作失败: %s", action)
        raise HTTPException(status_code=500, detail=f"CodeGraph 操作失败: {e}")


@router.post("/app/codegraph/refresh")
def refresh_codegraph(body: CodegraphBody):
    from services.agent_zero.services import codemap

    ws_id = str(body.workspace_id or "").strip()
    if not ws_id:
        raise HTTPException(status_code=400, detail="缺少 workspace_id")
    codemap.refresh(ws_id)
    return {"code": 0, "data": codemap.status(ws_id)}


# ── MCP 服务 ────────────────────────────────────────────────────────────

class McpServerBody(BaseModel):
    name: str = ""
    type: str = "stdio"
    package: str = ""
    command: str = ""
    args: List[str] = Field(default_factory=list)
    install_command: str = ""
    env: Dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    note: str = ""


@router.get("/app/mcp")
def list_mcp():
    from services.agent_zero.services import mcp_service

    servers = []
    for s in mcp_service.list_servers():
        cached = mcp_service._tool_cache.get(s["id"], {})  # noqa: SLF001
        servers.append({
            "id": s["id"], "name": s["name"], "type": s["type"],
            "package": s.get("package") or "", "command": s.get("command") or "",
            "args": s.get("args") or [], "url": s.get("url") or "",
            "install_command": s.get("install_command") or "",
            "env": s.get("env") or {}, "headers": s.get("headers") or {},
            "enabled": s.get("enabled", True), "installed": s.get("installed", False),
            "note": s.get("note") or "", "tools": len(cached.get("tools") or []),
            "tool_names": [t.get("name") for t in (cached.get("tools") or [])][:40],
            "created_at": s.get("created_at") or "",
        })
    return {"code": 0, "data": {"servers": servers, "npm": mcp_service.npm_available()}}


@router.post("/app/mcp/servers")
def add_mcp_server(body: McpServerBody):
    from services.agent_zero.services import mcp_service

    try:
        server = mcp_service.add_server(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": server}


@router.put("/app/mcp/servers/{server_id}")
def update_mcp_server(server_id: str, body: McpServerBody):
    from services.agent_zero.services import mcp_service

    try:
        server = mcp_service.update_server(server_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": server}


@router.delete("/app/mcp/servers/{server_id}")
def delete_mcp_server(server_id: str):
    from services.agent_zero.services import mcp_service

    if not mcp_service.delete_server(server_id):
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    return {"code": 0, "data": {"deleted": server_id}}


@router.post("/app/mcp/servers/{server_id}/install")
async def install_mcp_server(server_id: str):
    from services.agent_zero.services import mcp_service

    server = mcp_service.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    if not server.get("package") and not server.get("install_command"):
        raise HTTPException(status_code=400,
                            detail="该服务没有 npm 包或安装命令，无法自动安装")
    result = await mcp_service.install_package(
        server.get("package") or "",
        command=server.get("install_command") or "",
    )
    if result.get("ok"):
        mcp_service.mark_installed(server_id, True)
    return {"code": 0 if result.get("ok") else 1, "data": result}


@router.post("/app/mcp/servers/{server_id}/probe")
async def probe_mcp_server(server_id: str):
    from services.agent_zero.services import mcp_service

    return {"code": 0, "data": await mcp_service.probe(server_id)}


@router.get("/app/mcp/export")
def export_mcp():
    from services.agent_zero.services import mcp_service

    return {"code": 0, "data": mcp_service.export_servers()}


@router.post("/app/mcp/import")
def import_mcp(payload: Dict[str, Any]):
    from services.agent_zero.services import mcp_service

    try:
        return {"code": 0, "data": mcp_service.import_servers(payload)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 技能（skills）───────────────────────────────────────────────────────

class SkillBody(BaseModel):
    name: str = ""
    description: str = ""
    content: str = ""
    role_id: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/app/skills")
def list_skills(role_id: str = Query(""), include_global: bool = Query(True)):
    from services.agent_zero.services import skill_service

    return {"code": 0, "data": {"items": skill_service.list_skills(
        role_id=role_id.strip() or None, include_global=include_global)}}


@router.post("/app/skills")
def create_skill(body: SkillBody):
    from services.agent_zero.services import skill_service

    try:
        return {"code": 0, "data": skill_service.create_skill(
            body.name, body.description, body.content, role_id=body.role_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/app/skills/merge")
def merge_skills(body: Dict[str, Any]):
    from services.agent_zero.services import skill_service

    try:
        return {"code": 0, "data": skill_service.merge_skills(
            str(body.get("role_id") or ""), body.get("skill_ids") or [],
            str(body.get("name") or ""), str(body.get("description") or ""))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/app/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillBody):
    from services.agent_zero.services import skill_service

    try:
        return {"code": 0, "data": skill_service.update_skill(
            skill_id, body.model_dump(exclude_none=True))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/app/skills/{skill_id}")
def delete_skill(skill_id: str):
    from services.agent_zero.services import skill_service

    if not skill_service.delete_skill(skill_id):
        raise HTTPException(status_code=404, detail="技能不存在")
    return {"code": 0, "data": {"deleted": skill_id}}


@router.get("/app/skills/{skill_id}/download")
def download_skill(skill_id: str):
    from fastapi.responses import Response

    from services.agent_zero.services import skill_service

    skill = skill_service.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    return Response(
        content=skill_service.zip_skill(skill_id),
        media_type="application/zip",
        headers={"Content-Disposition":
                 f"attachment; filename={skill['name'] or skill_id}.zip"},
    )


@router.post("/app/skills/upload")
def upload_skill(body: Dict[str, Any]):
    import base64

    from services.agent_zero.services import skill_service

    try:
        data = base64.b64decode(str(body.get("data") or ""))
    except Exception:
        raise HTTPException(status_code=400, detail="上传数据无效")
    try:
        return {"code": 0, "data": skill_service.import_upload(
            str(body.get("filename") or "skill"), data,
            role_id=str(body.get("role_id") or "") or None)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/app/skills/install")
async def install_skill(body: Dict[str, Any]):
    from services.agent_zero.services import skill_service

    result = await skill_service.install_from_npm(
        str(body.get("package") or ""), str(body.get("name") or ""))
    return {"code": 0 if result.get("ok") else 1, "data": result}


@router.post("/app/skills/url")
def import_skill_url(body: Dict[str, Any]):
    from services.agent_zero.services import skill_service

    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="缺少 url")
    try:
        return {"code": 0, "data": skill_service.import_url(
            url, str(body.get("name") or ""),
            role_id=str(body.get("role_id") or "") or None)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"抓取失败: {e}")


@router.post("/app/skills/npx")
async def install_skill_npx(body: Dict[str, Any]):
    """npx skills add <owner/repo> —— 安装 Agent Skills 到技能库。"""
    from services.agent_zero.services import skill_service

    result = await skill_service.install_from_skills_cli(
        str(body.get("source") or ""), body.get("skills") or [])
    return {"code": 0 if result.get("ok") else 1, "data": result}


@router.post("/app/skills/npx/list")
async def list_skills_npx(body: Dict[str, Any]):
    from services.agent_zero.services import skill_service

    result = await skill_service.list_skills_cli(str(body.get("source") or ""))
    return {"code": 0 if result.get("ok") else 1, "data": result}


@router.post("/app/skills/npx/search")
async def search_skills_npx(body: Dict[str, Any]):
    from services.agent_zero.services import skill_service

    result = await skill_service.search_skills_registry(str(body.get("query") or ""))
    return {"code": 0 if result.get("ok") else 1, "data": result}


# ── 扩展机制：自定义斜杠命令 / 工作区钩子 ──────────────────────────────────

@router.get("/app/commands")
def list_slash_commands(workspace_id: str = ""):
    """当前工作区可用的斜杠命令（工作区 > 内置，含来源与路径）。"""
    from services.agent_zero.services import command_service

    return {"code": 0, "data": command_service.summarize(workspace_id or None)}


class SlashCommandBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    body: str = Field(..., min_length=1)
    description: str = ""
    argument_hint: str = ""
    workspace_id: str = ""


@router.post("/app/commands")
def create_slash_command(body: SlashCommandBody):
    from services.agent_zero.services import command_service

    try:
        data = command_service.create_command(
            body.workspace_id or None, body.name, body.body,
            description=body.description, argument_hint=body.argument_hint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}


@router.delete("/app/commands/{name}")
def delete_slash_command(name: str, workspace_id: str = ""):
    from services.agent_zero.services import command_service

    try:
        data = command_service.delete_command(workspace_id or None, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}


@router.get("/app/hooks")
def list_hooks(workspace_id: str = ""):
    """工作区钩子配置（只读摘要；命令不会被执行）。"""
    from services.agent_zero.services import hooks_service

    return {"code": 0, "data": hooks_service.summarize(workspace_id or None)}


class HookBody(BaseModel):
    event: str = Field(..., min_length=1, max_length=32)
    command: str = Field(..., min_length=1, max_length=4000)
    matcher: str = "*"
    timeout: float = 10.0
    workspace_id: str = ""


@router.post("/app/hooks")
def add_hook(body: HookBody):
    from services.agent_zero.services import hooks_service

    try:
        data = hooks_service.add_workspace_hook(
            body.workspace_id or None, body.event, body.command,
            matcher=body.matcher, timeout=body.timeout)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}


@router.delete("/app/hooks")
def delete_hook(event: str, index: int, workspace_id: str = ""):
    from services.agent_zero.services import hooks_service

    try:
        data = hooks_service.remove_workspace_hook(workspace_id or None, event, index)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}


@router.post("/app/hooks/template")
def create_hooks_template(body: Dict[str, Any]):
    from services.agent_zero.services import hooks_service

    try:
        data = hooks_service.ensure_template(body.get("workspace_id") or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}

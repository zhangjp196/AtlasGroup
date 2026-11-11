"""订阅平台预设目录：要求完整、唯一；编程订阅 / 反代预设已下线。"""
from __future__ import annotations


def test_platform_preset_catalog_is_valid():
    from services.agent_zero.routes.app_routes import PLATFORM_PRESETS

    codes = [item["code"] for item in PLATFORM_PRESETS]
    assert len(codes) == len(set(codes))
    assert len(codes) >= 30
    # 官方 API / 聚合平台 / 本地网关 / OpenCode / Command Code 仍在
    assert {"command-code", "opencode-go", "opencode-zen", "openrouter",
            "siliconflow", "ollama", "lmstudio", "deepseek", "anthropic",
            "openai"} <= set(codes)

    for item in PLATFORM_PRESETS:
        assert item["name"]
        assert item["group"] in {"commandcode", "opencode", "aggregator",
                                 "local", "official"}
        assert item.get("protocol") in {"openai-chat", "openai-response", "anthropic"}
        if item.get("requires_gateway"):
            assert item.get("note")


def test_coding_subscription_presets_are_removed():
    """编程订阅 / 反代（WorkBuddy、Trae、Qoder、CodeBuddy 等）不再内置、不再展示。

    这类端点依赖第三方本地转换器与账号登录态，无法稳定验证；用户仍可自定义
    订阅并手填 Base URL，所以这里只要求预设目录里彻底没有它们。
    """
    from services.agent_zero.routes.app_routes import PLATFORM_PRESETS

    groups = {item.get("group") for item in PLATFORM_PRESETS}
    assert "coding" not in groups

    codes = {item["code"] for item in PLATFORM_PRESETS}
    banned = {
        "workbuddy", "workbuddy-proxy", "workbuddy-gateway", "workbuddy2api",
        "workbuddy-api-console", "qoder-api-console", "cline-api-console",
        "grok-build-api-console", "codebuddy", "proxy-hub", "trae",
        "trae-local-api", "trae2api-web", "trae-api-proxy", "zcode",
        "minimax-code", "qoder", "catpaw", "iflow", "marscode", "comate",
        "lingma",
    }
    assert not (banned & codes), sorted(banned & codes)


def test_commandcode_preset_uses_provider_api():
    """Command Code 走官方 Provider API：一个 Key 同时支持三套协议。"""
    from services.agent_zero.routes.app_routes import (
        COMMANDCODE_ANTHROPIC_BASE,
        COMMANDCODE_BASE,
        COMMANDCODE_MODELS,
        PLATFORM_PRESETS,
        _endpoint_protocol,
    )

    preset = next(item for item in PLATFORM_PRESETS
                  if item["code"] == "command-code")
    assert preset["group"] == "commandcode"
    assert preset["base_url"] == COMMANDCODE_BASE
    assert preset["anthropic_base_url"] == COMMANDCODE_ANTHROPIC_BASE
    assert preset["models_url"] == COMMANDCODE_MODELS
    assert preset["base_url"] == "https://api.commandcode.ai/provider/v1"
    assert preset["anthropic_base_url"] == "https://api.commandcode.ai/provider"
    assert preset["models_url"] == "https://api.commandcode.ai/provider/v1/models"

    # 模型列表里的 supported_endpoints 决定协议。
    assert _endpoint_protocol(["/messages"])[0] == "anthropic"
    assert _endpoint_protocol(["/chat/completions", "/responses"])[0] == "openai-chat"
    assert _endpoint_protocol(["/responses"])[0] == "openai-response"
    assert _endpoint_protocol([])[0] == "openai-chat"
    assert _endpoint_protocol(None)[0] == "openai-chat"


def test_commandcode_model_discovery_uses_supported_endpoints():
    """Command Code 的 /models 返回 name / context_length / supported_endpoints。"""
    from services.agent_zero.routes.app_routes import (
        COMMANDCODE_MODELS,
        _discover_model_entry,
        _is_commandcode,
        _models_url_for,
    )

    assert _is_commandcode("https://api.commandcode.ai/provider/v1")
    assert _is_commandcode("https://api.commandcode.ai/provider")
    assert not _is_commandcode("https://opencode.ai/inference/openai/v1")
    assert _models_url_for("https://api.commandcode.ai/provider/v1") == COMMANDCODE_MODELS
    assert _models_url_for("https://api.commandcode.ai/provider") == COMMANDCODE_MODELS

    claude = _discover_model_entry({
        "id": "claude-sonnet-5-5",
        "name": "Claude Sonnet 5.5",
        "owned_by": "command-code",
        "context_length": 1_000_000,
        "supported_endpoints": ["/messages"],
    }, commandcode=True)
    assert claude == {
        "id": "claude-sonnet-5-5",
        "owned_by": "command-code",
        "name": "Claude Sonnet 5.5",
        "context_window": 1_000_000,
        "protocol": "anthropic",
        "supported": True,
    }

    gpt = _discover_model_entry({
        "id": "gpt-6-astra",
        "context_length": 1_050_000,
        "supported_endpoints": ["/chat/completions", "/responses"],
    }, commandcode=True)
    assert gpt and gpt["protocol"] == "openai-chat"
    assert gpt["context_window"] == 1_050_000

    plain = _discover_model_entry("gpt-4o-mini")
    assert plain == {"id": "gpt-4o-mini", "owned_by": ""}
    assert _discover_model_entry({"name": ""}) is None


def test_presets_api_exposes_commandcode(client):
    res = client.get("/api/agent-zero/app/models/presets")
    assert res.status_code == 200
    items = res.json()["data"]["items"]
    preset = next(item for item in items if item["code"] == "command-code")
    assert preset["group"] == "commandcode"
    assert preset["base_url"] == "https://api.commandcode.ai/provider/v1"
    assert preset["anthropic_base_url"] == "https://api.commandcode.ai/provider"

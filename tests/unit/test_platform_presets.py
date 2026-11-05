"""订阅平台预设目录：要求完整、唯一；编程订阅 / 反代预设已下线。"""
from __future__ import annotations


def test_platform_preset_catalog_is_valid():
    from services.agent_zero.routes.app_routes import PLATFORM_PRESETS

    codes = [item["code"] for item in PLATFORM_PRESETS]
    assert len(codes) == len(set(codes))
    assert len(codes) >= 30
    # 官方 API / 聚合平台 / 本地网关 / OpenCode 仍在
    assert {"opencode-go", "opencode-zen", "openrouter", "siliconflow",
            "ollama", "lmstudio", "deepseek", "anthropic", "openai"} <= set(codes)

    for item in PLATFORM_PRESETS:
        assert item["name"]
        assert item["group"] in {"opencode", "aggregator", "local", "official"}
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

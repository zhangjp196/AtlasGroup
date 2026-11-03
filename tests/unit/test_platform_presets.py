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


def test_frontend_has_no_coding_subscription_group():
    """前端不再有「编程订阅 / 反代」分组与文案（含中英文）。"""
    from pathlib import Path

    js = Path("web/assets/app.js").read_text(encoding="utf-8")
    html = Path("web/index.html").read_text(encoding="utf-8")
    assert "platform.group.coding" not in js
    assert "编程订阅" not in js
    assert "reverse proxies for coding subscriptions" not in js
    assert "reverse proxies for coding subscriptions" not in html

    # 分组渲染顺序里也去掉了 coding，否则该分组会被重新渲染出来
    assert "['custom', 'opencode', 'aggregator', 'local', 'official']" in js


def test_frontend_group_order_covers_every_preset_group():
    """前端 renderPlatformPicker 的分组顺序必须覆盖后端所有预设分组。

    前端是按固定数组分组渲染的：后端新增一个分组、前端忘了加进数组时，
    那批预设会被静默丢掉（用户看不到、也没有报错），所以这里做交叉校验。
    """
    import re
    from pathlib import Path

    from services.agent_zero.routes.app_routes import PLATFORM_PRESETS

    js = Path("web/assets/app.js").read_text(encoding="utf-8")
    fn = js.index("function renderPlatformPicker(")
    m = re.search(r"const groups = \[([^\]]*)\];", js[fn:])
    assert m, "未找到 renderPlatformPicker 的分组顺序定义"
    rendered = set(re.findall(r"'([^']+)'", m.group(1)))

    backend = {item.get("group") or "official" for item in PLATFORM_PRESETS}
    assert backend <= rendered, sorted(backend - rendered)

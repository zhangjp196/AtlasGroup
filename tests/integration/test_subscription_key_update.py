"""订阅密钥：改订阅的 API Key 必须立刻作用到该订阅下的所有模型。

历史问题：老版本的「模型卡片」可以单独填 API Key，于是模型 JSON 里留着一份
旧 key；改订阅 key 后，模型自己的 key 仍然覆盖订阅 → 界面显示已保存，实际请求
还在用旧 key（用户视角就是「改了没生效」）。现在口径固定为：

- 订阅是密钥的唯一来源：模型挂了订阅且订阅上有 key 时，模型档案不再保存 key；
- 解析时订阅 key 优先级最高（清掉历史遗留 key 的影响）；
- 默认模型同理：顶层的旧 api_key 不能盖住订阅 key。
"""
from __future__ import annotations

import asyncio

import yaml

from services.agent_zero.runtime.zero import llm

API = "/api/agent-zero"
MASK = "••••••••"


def _reset_llm(app_env) -> None:
    """清空 config.yaml 的 llm 段，保证每个用例从干净的默认段开始。"""
    cfg = yaml.safe_load(app_env["config"].read_text(encoding="utf-8"))
    cfg["llm"] = {"base_url": "", "api_key": "", "model": "",
                  "active_model": "", "models": {}, "subscriptions": {}}
    app_env["config"].write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    llm._SECTION_CACHE.update(key=None, value={}, at=0.0)


def _effective(code: str) -> dict:
    llm._SECTION_CACHE.update(key=None, value={}, at=0.0)
    return asyncio.run(llm.get_llm_config(code))


def _put(client, *, sub_key: str, models: list, active: str = "m1",
         extra: dict | None = None) -> dict:
    payload = {
        "subscriptions": [{
            "id": "sub-1",
            "name": "Sub",
            "provider": "custom",
            "protocol": "openai-chat",
            "base_url": "https://api.example.com/v1",
            "api_key": sub_key,
        }],
        "models": models,
        "active_model": active,
    }
    payload.update(extra or {})
    res = client.put(f"{API}/app/settings", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_subscription_key_change_reaches_every_model(client, app_env):
    _reset_llm(app_env)
    models = [
        {"code": "m1", "name": "M1", "model": "gpt-1", "subscription_id": "sub-1"},
        {"code": "m2", "name": "M2", "model": "gpt-2", "subscription_id": "sub-1"},
    ]
    _put(client, sub_key="key-old", models=models)
    assert _effective("m1")["api_key"] == "key-old"
    assert _effective("m2")["api_key"] == "key-old"

    _put(client, sub_key="key-new", models=models)
    assert _effective("m1")["api_key"] == "key-new"
    assert _effective("m2")["api_key"] == "key-new"


def test_legacy_per_model_key_does_not_shadow_subscription(client, app_env):
    """历史数据里模型自带 key：改订阅 key 后必须改用订阅的 key。"""
    _reset_llm(app_env)
    models = [{
        "code": "m1", "name": "M1", "model": "gpt-1",
        "subscription_id": "sub-1",
        # 老版本模型卡片留下的独立密钥
        "api_key": "legacy-model-key",
    }]
    _put(client, sub_key="key-old", models=models)
    assert _effective("m1")["api_key"] == "key-old"

    view = _put(client, sub_key="key-new", models=[
        {"code": "m1", "name": "M1", "model": "gpt-1", "subscription_id": "sub-1"}])
    # 保存后模型档案不应再留着自己的 key（掩码也不该出现真实值）
    stored = next(m for m in view["models"] if m["code"] == "m1")
    assert not stored.get("api_key") or stored["api_key"] == MASK
    assert _effective("m1")["api_key"] == "key-new"


def test_default_model_uses_subscription_key_over_stale_top_level(client, app_env):
    """默认模型挂订阅时，顶层遗留 api_key 不能盖住订阅密钥。"""
    _reset_llm(app_env)
    models = [{"code": "m1", "name": "M1", "model": "gpt-1",
               "subscription_id": "sub-1"}]
    _put(client, sub_key="key-old", models=models,
         extra={"api_key": "stale-top-level"})
    assert _effective("m1")["api_key"] == "key-old"

    _put(client, sub_key="key-new", models=models,
         extra={"api_key": "stale-top-level"})
    assert _effective("m1")["api_key"] == "key-new"
    assert _effective("default")["api_key"] == "key-new"


def test_unchanged_subscription_saves_keep_existing_key(client, app_env):
    """前端提交订阅时不回传真实密钥（只回掩码/空），保存不能把 key 抹掉。"""
    _reset_llm(app_env)
    models = [{"code": "m1", "name": "M1", "model": "gpt-1",
               "subscription_id": "sub-1"}]
    _put(client, sub_key="keep-me", models=models)
    view = client.get(f"{API}/app/settings").json()["data"]
    assert view["subscriptions"][0]["api_key"] == MASK
    assert view["subscriptions"][0]["has_api_key"] is True

    # 模拟前端：订阅 api_key 传掩码（未改密码时的实际行为）
    res = client.put(f"{API}/app/settings", json={
        "subscriptions": [{
            "id": "sub-1", "name": "Sub", "provider": "custom",
            "protocol": "openai-chat", "base_url": "https://api.example.com/v1",
            "api_key": MASK,
        }],
        "models": models,
        "active_model": "m1",
    })
    assert res.status_code == 200, res.text
    assert _effective("m1")["api_key"] == "keep-me"


def test_model_without_subscription_keeps_own_key(client, app_env):
    """没有订阅的模型（本地网关）仍可用自己的 key，不受订阅逻辑影响。"""
    _reset_llm(app_env)
    res = client.put(f"{API}/app/settings", json={
        "subscriptions": [],
        "models": [{
            "code": "local-1", "name": "Local", "model": "llama",
            "base_url": "http://127.0.0.1:11434/v1", "api_key": "local-key",
        }],
        "active_model": "local-1",
    })
    assert res.status_code == 200, res.text
    assert _effective("local-1")["api_key"] == "local-key"

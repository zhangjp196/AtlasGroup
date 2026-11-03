"""订阅级配置：一个订阅（Base URL + Key）下挂多个模型。"""
from __future__ import annotations

import asyncio

import yaml

from services.agent_zero.runtime.zero import llm

API = "/api/agent-zero"


def _clean_llm(app_env) -> None:
    cfg = yaml.safe_load(app_env["config"].read_text(encoding="utf-8"))
    cfg["llm"] = {
        "base_url": "",
        "api_key": "",
        "model": "",
        "active_model": "",
        "models": {},
    }
    app_env["config"].write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    llm._SECTION_CACHE.update(key=None, value={}, at=0.0)


def test_subscription_supplies_base_url_and_key(client, app_env):
    _clean_llm(app_env)
    payload = {
        "subscriptions": [{
            "id": "opencode-go-1",
            "name": "OpenCode Go",
            "provider": "opencode-go",
            "base_url": "https://opencode.ai/inference/go/openai/v1",
            "anthropic_base_url": "https://opencode.ai/inference/go/anthropic",
            "protocol": "openai-chat",
            "note": "local proxy",
            "api_key": "go-secret",
        }],
        "models": [
            {"code": "go-a", "name": "Go A", "model": "gpt-a",
             "subscription_id": "opencode-go-1"},
            {"code": "go-b", "name": "Go B", "model": "gpt-b",
             "subscription_id": "opencode-go-1"},
            {"code": "claude", "name": "Claude", "model": "claude-sonnet-4-5",
             "protocol": "anthropic", "subscription_id": "opencode-go-1"},
        ],
        "active_model": "go-a",
    }
    res = client.put(f"{API}/app/settings", json=payload)
    assert res.status_code == 200, res.text
    view = res.json()["data"]
    assert [s["id"] for s in view["subscriptions"]] == ["opencode-go-1"]
    assert view["subscriptions"][0]["api_key"] != "go-secret"
    assert view["subscriptions"][0]["has_api_key"] is True
    assert (view["subscriptions"][0]["anthropic_base_url"]
            == "https://opencode.ai/inference/go/anthropic")
    assert view["subscriptions"][0]["note"] == "local proxy"
    assert view["subscriptions"][0]["protocol"] == "openai-chat"

    rows = {m["code"]: m for m in client.get(f"{API}/app/models").json()["data"]["items"]}
    assert rows["go-a"]["subscription_id"] == "opencode-go-1"
    assert rows["go-a"]["subscription_name"] == "OpenCode Go"
    assert rows["go-a"]["effective_base_url"] == payload["subscriptions"][0]["base_url"]
    assert rows["go-a"]["effective_api_key"]
    assert rows["go-b"]["effective_base_url"] == payload["subscriptions"][0]["base_url"]
    assert rows["claude"]["effective_protocol"] == "anthropic"
    assert (rows["claude"]["effective_base_url"]
            == "https://opencode.ai/inference/go/anthropic")

    cfg = asyncio.run(llm.get_llm_config("go-a"))
    assert cfg["base_url"] == payload["subscriptions"][0]["base_url"]
    assert cfg["api_key"] == "go-secret"
    assert cfg["model"] == "gpt-a"


def test_model_inherits_subscription_default_protocol(client, app_env):
    _clean_llm(app_env)
    res = client.put(f"{API}/app/settings", json={
        "subscriptions": [{
            "id": "trae",
            "name": "Trae",
            "provider": "trae",
            "protocol": "anthropic",
            "base_url": "http://127.0.0.1:47860/gateway/trae",
            "api_key": "local",
            "note": "CreditDaddy gateway",
        }],
        "models": [{
            "code": "trae-model",
            "name": "Trae Model",
            "model": "Doubao-Seed-Code",
            "subscription_id": "trae",
        }],
        "active_model": "trae-model",
    })
    assert res.status_code == 200, res.text
    rows = {m["code"]: m for m in client.get(f"{API}/app/models").json()["data"]["items"]}
    row = rows["trae-model"]
    assert row["effective_protocol"] == "anthropic"
    assert row["effective_base_url"] == "http://127.0.0.1:47860/gateway/trae"

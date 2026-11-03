"""LLM 运行时（配置快照 / 连接池 / 每模型策略）回归测试。

锁定的三点：
1. 配置不再每次请求都解析 YAML + 查 sqlite，但设置一改就立即失效；
2. HTTP 客户端按 provider 复用，换事件循环不会跨循环复用；
3. 每个模型档案可以有自己的重试次数与超时，互不牵连。
"""
from __future__ import annotations

import asyncio

import pytest

from services.agent_zero.runtime.zero import llm
from services.shared import yaml_config


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(llm, "_SECTION_CACHE", {"key": None, "value": {}, "at": 0.0})
    yield
    llm._SECTION_CACHE.update(key=None, value={}, at=0.0)


def _patch_section(monkeypatch, section):
    monkeypatch.setattr(llm, "_llm_section", lambda: dict(section))


# ── 配置快照 ────────────────────────────────────────────────────────────

def test_llm_section_is_cached_between_calls(monkeypatch):
    calls = []

    def fake_load(path):
        calls.append(path)
        return {"llm": {"model": "m", "base_url": "http://x/v1"}}

    monkeypatch.setattr(yaml_config, "load_service_config", fake_load)
    from services.agent_zero.stores import settings_store

    monkeypatch.setattr(settings_store, "llm_version", lambda: 0)
    monkeypatch.setattr(settings_store, "llm_override", lambda: {})

    first = llm._llm_section()
    second = llm._llm_section()
    assert first == second
    assert len(calls) == 1, "同一份配置被重复解析"


def test_llm_section_cache_invalidates_on_settings_version(monkeypatch):
    calls = []
    version = {"n": 0}

    def fake_load(path):
        calls.append(path)
        return {"llm": {"model": "m", "base_url": "http://x/v1"}}

    monkeypatch.setattr(yaml_config, "load_service_config", fake_load)
    from services.agent_zero.stores import settings_store

    monkeypatch.setattr(settings_store, "llm_version", lambda: version["n"])
    monkeypatch.setattr(settings_store, "llm_override", lambda: {})

    llm._llm_section()
    version["n"] = 1          # 相当于 settings_store.put("llm", ...)
    llm._llm_section()
    assert len(calls) == 2, "设置改动后缓存必须立即失效"


def test_settings_put_bumps_llm_version(client):
    from services.agent_zero.stores import settings_store

    before = settings_store.llm_version()
    settings_store.put("llm", {"model": "m", "base_url": "http://x/v1"})
    assert settings_store.llm_version() == before + 1


# ── 每模型重试 / 超时 ───────────────────────────────────────────────────

SECTION = {
    "model": "top",
    "base_url": "http://top/v1",
    "retry_attempts": 20,
    "retry_backoff_s": 1.0,
    "request_timeout_s": 600,
    "connect_timeout_s": 10,
    "models": {
        "slow": {
            "model": "slow-model",
            "base_url": "http://slow/v1",
            "retry_attempts": 2,
            "retry_backoff_s": 0.5,
            "request_timeout_s": 2400,
            "connect_timeout_s": 4,
        },
    },
}


def test_profile_overrides_retry_and_timeout(monkeypatch):
    _patch_section(monkeypatch, SECTION)

    cfg = llm._resolve_llm_config("slow", use_active=False)
    assert cfg["retry_attempts"] == 2
    assert cfg["request_timeout_s"] == 2400

    assert llm._retry_policy(cfg) == (2, 0.5)
    assert llm._retry_policy(llm._resolve_llm_config("default", use_active=False)) == (20, 1.0)

    timeout = llm._request_timeout_setting(llm.DEFAULT_REQUEST_TIMEOUT, cfg)
    assert timeout.read == 2400
    assert timeout.connect == 4


def test_profile_timeout_does_not_leak_to_other_models(monkeypatch):
    _patch_section(monkeypatch, SECTION)
    other = llm._resolve_llm_config("default", use_active=False)
    timeout = llm._request_timeout_setting(llm.DEFAULT_REQUEST_TIMEOUT, other)
    assert timeout.read == 600
    assert timeout.connect == 10


# ── 重试总时长上限 ──────────────────────────────────────────────────────

def test_retry_deadline_defaults(monkeypatch):
    _patch_section(monkeypatch, {"retry_deadline_s": 30})
    assert llm._retry_deadline_s() == 30
    assert llm._retry_deadline_s({"retry_deadline_s": 5}) == 5
    assert llm._retry_deadline_s({"retry_deadline_s": 0}) == 0
    _patch_section(monkeypatch, {})
    assert llm._retry_deadline_s() == 900


def test_retry_expired_only_counts_failing_time():
    err = llm.LlmError("boom", kind="timeout")
    now = 1_000.0
    assert llm._retry_expired(0, now - 10_000, err) is False   # 0 = 不限
    assert llm._retry_expired(10, None, err) is False          # 还没开始失败


def test_chat_stops_retrying_after_deadline(monkeypatch):
    """上游整体挂掉时不能无限重试：到总时长上限就报错，让 run 结束。"""
    calls = {"n": 0}

    async def always_timeout(*_a, **_k):
        calls["n"] += 1
        await asyncio.sleep(0.04)
        raise llm.LlmError("timed out", kind="timeout")

    async def no_wait(*_a, **_k):
        return None

    monkeypatch.setattr(llm, "_llm_chat_once", always_timeout)
    monkeypatch.setattr(llm, "_retry_policy", lambda *_a, **_k: (50, 0.0))
    monkeypatch.setattr(llm, "_retry_deadline_s", lambda *_a, **_k: 0.1)
    monkeypatch.setattr(llm, "_retry_wait", no_wait)

    with pytest.raises(llm.LlmError):
        asyncio.run(llm.llm_chat("default", [{"role": "user", "content": "hi"}]))
    assert 1 <= calls["n"] <= 4, calls


# ── 连接池 ──────────────────────────────────────────────────────────────

def test_http_client_is_reused_per_provider():
    async def run():
        a1 = llm._get_http_client(False, "http://a.local/v1")
        a2 = llm._get_http_client(False, "http://a.local/v1")
        b1 = llm._get_http_client(False, "http://b.local/v1")
        proxy = llm._get_http_client(True, "http://a.local/v1")
        assert a1 is a2, "同一 provider 必须复用连接池"
        assert b1 is not a1
        assert proxy is not a1
        await llm.close_llm_clients()
        assert a1.is_closed and b1.is_closed and proxy.is_closed

    asyncio.run(run())


def test_http_client_is_not_reused_across_event_loops():
    async def make():
        return llm._get_http_client(False, "http://a.local/v1")

    first = asyncio.run(make())
    second = asyncio.run(make())
    assert first is not second, "httpx 客户端绑定事件循环，不能跨循环复用"


# ── 设置界面不抹掉进阶调参 ──────────────────────────────────────────────

def test_settings_save_keeps_advanced_profile_keys():
    from services.agent_zero.routes.app_routes import (
        ModelProfileBody,
        SettingsBody,
        _new_model_map,
    )

    body = SettingsBody(models=[ModelProfileBody(
        code="smart", name="Smart", model="m", base_url="http://x/v1")])
    existing = {"smart": {"model": "old", "retry_attempts": 3,
                          "request_timeout_s": 1800, "api_key": "k"}}
    out = _new_model_map(body, existing)
    assert out["smart"]["retry_attempts"] == 3
    assert out["smart"]["request_timeout_s"] == 1800
    assert out["smart"]["api_key"] == "k"

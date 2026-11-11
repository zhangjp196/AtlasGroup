"""Command Code 套餐额度解析：内部 /alpha billing 字段 → 前端统一结构。"""
from __future__ import annotations

import time

import pytest


def test_commandcode_quota_snapshot_maps_credits_windows_and_plan():
    from services.agent_zero.routes.app_routes import _commandcode_quota_snapshot

    now = time.time()
    credits = {
        "credits": {
            "monthlyCredits": 12.5,
            "purchasedCredits": 2,
            "freeCredits": 1,
        },
        "windowLimits": {
            "fiveHour": {
                "used": 1.5, "cap": 5, "exceeded": False,
                "resetAt": int((now + 3600) * 1000),
            },
            "weekly": {
                "used": 10, "cap": 20, "exceeded": True,
                "resetAt": int((now + 7200) * 1000),
            },
        },
    }
    subscription = {"success": True, "data": {
        "planId": "individual-provider",
        "status": "active",
        "currentPeriodStart": "2026-11-01T00:00:00Z",
        "currentPeriodEnd": "2026-12-01T00:00:00Z",
    }}
    summary = {
        "totalCost": 3.25,
        "totalCount": 42,
        "totalTokens": 123456,
        "totalTokensIn": 100000,
        "totalTokensOut": 23456,
        "periodBasis": "billing-cycle",
    }

    snap = _commandcode_quota_snapshot(credits, subscription, summary)
    assert snap is not None
    assert snap["provider"] == "command-code"
    assert snap["plan"]["name"] == "Provider"
    assert snap["plan"]["period_start"] == "2026-11-01T00:00:00Z"
    assert snap["credits"]["monthly"] == 12.5
    assert snap["credits"]["remaining"] == 15.5
    # 套餐额度 15 + 额外购买 2 + 赠送 1
    assert snap["credits"]["limit"] == 18.0
    assert snap["credits"]["percent"] == pytest.approx((18 - 15.5) / 18 * 100)
    assert snap["windows"]["five_hour"]["percent"] == pytest.approx(30)
    assert snap["windows"]["five_hour"]["reset_in_sec"] == pytest.approx(3600, abs=5)
    assert snap["windows"]["weekly"]["status"] == "exceeded"
    assert snap["summary"]["cost"] == 3.25
    assert snap["summary"]["tokens"] == 123456
    assert snap["summary"]["requests"] == 42


def test_commandcode_quota_expired_window_counts_as_reset():
    from services.agent_zero.routes.app_routes import _commandcode_quota_snapshot

    snap = _commandcode_quota_snapshot({
        "credits": {"monthlyCredits": 10},
        "windowLimits": {"fiveHour": {
            "used": 4, "cap": 5, "resetAt": int((time.time() - 60) * 1000),
        }},
    })
    assert snap is not None
    window = snap["windows"]["five_hour"]
    assert window["used"] == 0
    assert window["percent"] == 0
    assert window["reset_in_sec"] == 0


def test_commandcode_quota_without_windows_returns_credits():
    """Provider 按量付费没有 5h/week 窗口，但额度余额仍然要展示。"""
    from services.agent_zero.routes.app_routes import _commandcode_quota_snapshot

    snap = _commandcode_quota_snapshot({
        "credits": {"monthlyCredits": 3, "purchasedCredits": 0, "freeCredits": 0},
        "windowLimits": None,
    })
    assert snap is not None
    assert snap["windows"] == {}
    assert snap["credits"]["remaining"] == 3


def test_commandcode_quota_requires_credits_payload():
    from services.agent_zero.routes.app_routes import _commandcode_quota_snapshot

    assert _commandcode_quota_snapshot({"windowLimits": {}}) is None
    assert _commandcode_quota_snapshot({"credits": None}) is None


def test_fetch_commandcode_quota_passes_org_and_parses(monkeypatch):
    """走一遍完整请求：whoami 取 orgId，billing/summary 带 orgId 并解析。"""
    import asyncio
    import time

    import httpx

    from services.agent_zero.routes import app_routes

    now_ms = int((time.time() + 1800) * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer user_test"
        path = request.url.path
        if path == "/alpha/whoami":
            assert request.url.params.get("limits") == "1"
            return httpx.Response(200, json={
                "success": True,
                "user": {"id": "u1", "email": "a@example.com"},
                "org": {"id": "org_1", "name": "Team"},
            })
        assert request.url.params.get("orgId") == "org_1"
        if path == "/alpha/billing/subscriptions":
            return httpx.Response(200, json={"success": True, "data": {
                "planId": "individual-provider", "status": "active",
                "currentPeriodStart": "2026-11-01T00:00:00Z",
            }})
        if path == "/alpha/billing/credits":
            return httpx.Response(200, json={
                "credits": {"monthlyCredits": 9, "purchasedCredits": 0, "freeCredits": 0},
                "windowLimits": {"fiveHour": {
                    "used": 1, "cap": 5, "resetAt": now_ms,
                }},
            })
        if path == "/alpha/usage/summary":
            return httpx.Response(200, json={"totalCost": 1.5, "totalCount": 3})
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.inner = real_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            await self.inner.aclose()

        async def get(self, url, **kwargs):
            return await self.inner.get(url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(app_routes._fetch_commandcode_quota("user_test"))
    assert result["plan"]["name"] == "Provider"
    assert result["credits"]["remaining"] == 9
    assert result["credits"]["percent"] == pytest.approx((15 - 9) / 15 * 100)
    assert result["windows"]["five_hour"]["percent"] == pytest.approx(20)
    assert result["summary"]["cost"] == 1.5


def test_model_usage_endpoint_queries_by_subscription(client, monkeypatch):
    """额度按订阅查：不需要先添加模型，前端传 subscription_id 即可。"""
    from services.agent_zero.routes import app_routes

    async def fake_quota(api_key: str):
        assert api_key == "user_test"
        return {
            "provider": "command-code",
            "plan": {"id": "individual-provider", "name": "Provider"},
            "credits": {"remaining": 15, "limit": 15, "percent": 0},
            "windows": {"five_hour": {"percent": 10, "reset_in_sec": 60}},
            "summary": {"cost": 0.2, "requests": 3},
        }

    monkeypatch.setattr(app_routes, "_fetch_commandcode_quota", fake_quota)
    saved = client.put("/api/agent-zero/app/settings", json={"subscriptions": [{
        "id": "cc-sub",
        "name": "Command Code",
        "provider": "command-code",
        "protocol": "openai-chat",
        "base_url": "https://api.commandcode.ai/provider/v1",
        "anthropic_base_url": "https://api.commandcode.ai/provider",
        "api_key": "user_test",
    }]})
    assert saved.status_code == 200, saved.text

    res = client.get(
        "/api/agent-zero/app/models/usage?subscription_id=cc-sub&refresh=true")
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["provider"] == "command-code"
    assert data["credits"]["remaining"] == 15
    assert data["windows"]["five_hour"]["percent"] == 10

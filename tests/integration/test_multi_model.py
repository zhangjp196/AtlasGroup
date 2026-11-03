"""多会话多模型互不干扰（真实链路，假上游）。

两个并发会话分别绑定 alpha / beta 两个模型档案：
- 每个请求必须发到**自己档案**的 base_url、带上自己的 model 与 api_key；
- 两条请求可以同时在飞（不是一条等另一条），说明连接/配置没有互相排队；
- 全部跑完不串味：alpha 的请求里不会出现 beta 的模型名或密钥。

这覆盖了「多个会话支持多个不同模型，相互不干扰」的核心语义。
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx2
import yaml

from services.agent_zero.runtime.zero import llm
from services.agent_zero.task_layer import conversation

API = "/api/agent-zero"


def _stream(text: str) -> str:
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": text}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 4,
                                  "total_tokens": 104}},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _write_profiles(path) -> None:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["llm"] = {
        "base_url": "http://alpha.local/v1",
        "api_key": "alpha-key",
        "model": "alpha-model",
        "context_window": 128_000,
        "max_tokens": 4_096,
        "active_model": "alpha",
        "models": {
            "alpha": {
                "name": "Alpha",
                "base_url": "http://alpha.local/v1",
                "model": "alpha-model",
                "api_key": "alpha-key",
            },
            "beta": {
                "name": "Beta",
                "base_url": "http://beta.local/v1",
                "model": "beta-model",
                "api_key": "beta-key",
            },
        },
    }
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")


def test_two_conversations_keep_their_own_models(client, app_env, monkeypatch):
    _write_profiles(app_env["config"])
    llm._SECTION_CACHE.update(key=None, value={}, at=0.0)

    seen: list = []
    active = {"now": 0, "max": 0}

    async def handler(request):
        body = json.loads(request.content)
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        seen.append({
            "host": request.url.host,
            "model": body.get("model"),
            "auth": request.headers.get("authorization") or "",
            "at": time.monotonic(),
        })
        try:
            # 让两条请求真正重叠：谁先到谁等一下，另一条必须能同时进来
            await asyncio.sleep(0.08 if body.get("model") == "alpha-model" else 0.02)
        finally:
            active["now"] -= 1
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text=_stream("ok"))

    monkeypatch.setattr(
        llm, "_new_http_client",
        lambda trust_env=False: httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False))
    # 每条会话有一句用户输入（模型路由与历史无关，这里只求请求形状完整）
    monkeypatch.setattr(
        conversation, "_history_messages",
        lambda cid, limit=20, vision=False, max_tokens=0: [
            {"role": "user", "content": f"会话 {cid} 的任务"}])

    conv_a = client.post(f"{API}/conversations", json={}).json()["data"]
    conv_b = client.post(f"{API}/conversations", json={}).json()["data"]
    conversation.update_conversation_preferences(conv_a["id"], model_code="alpha")
    conversation.update_conversation_preferences(conv_b["id"], model_code="beta")

    async def collect(cid: str):
        return [e async for e in conversation.generate_reply_stream(cid, "开始")]

    async def main():
        return await asyncio.gather(collect(conv_a["id"]), collect(conv_b["id"]))

    events_a, events_b = asyncio.run(main())

    assert any("done" in e for e in events_a)
    assert any("done" in e for e in events_b)

    by_host = {row["host"]: row for row in seen}
    assert set(by_host) == {"alpha.local", "beta.local"}, seen
    assert by_host["alpha.local"]["model"] == "alpha-model"
    assert by_host["beta.local"]["model"] == "beta-model"
    assert "alpha-key" in by_host["alpha.local"]["auth"]
    assert "beta-key" in by_host["beta.local"]["auth"]
    # 没有串味：alpha 的请求里不出现 beta 的模型/密钥
    assert "beta" not in json.dumps(by_host["alpha.local"])
    assert "alpha" not in json.dumps(by_host["beta.local"])
    # 两条请求同时在空中（各自独立的连接与配置，没有互相排队）
    assert active["max"] >= 2, active

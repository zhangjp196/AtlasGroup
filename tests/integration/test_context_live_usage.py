"""顶部「当前上下文 Xk」的数据链路：真实锚点 + 工具/正文增量。

口径：
1. 每次模型请求结束的 usage 事件里，`context_tokens` 必须等于真实的 prompt
   （锚点归位，之前的估算作废）；
2. 工具结果进上下文后，`context_tokens` 必须**变大**（否则长任务里读数会钉住）；
3. done 事件带着收尾时的读数，前端收尾不会往回跳。
"""
from __future__ import annotations

import asyncio
import json

import httpx2
import pytest

from services.agent_zero.runtime.zero import llm
from services.agent_zero.task_layer import conversation

API = "/api/agent-zero"


def _tool_stream() -> str:
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "net", "arguments": "{\"op\": \"time\"}"}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 200, "completion_tokens": 8,
                                  "total_tokens": 208}},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _text_stream(text: str) -> str:
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": text}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 300, "completion_tokens": 6,
                                  "total_tokens": 306}},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


@pytest.fixture(autouse=True)
def llm_env(monkeypatch):
    monkeypatch.setenv("AGENT_ZERO_LLM_BASE_URL", "http://gw.local/v1")
    monkeypatch.setenv("AGENT_ZERO_LLM_API_KEY", "EMPTY")
    monkeypatch.setenv("AGENT_ZERO_LLM_MODEL", "fake-model")
    monkeypatch.delenv("AGENT_ZERO_LLM_PROTOCOL", raising=False)
    monkeypatch.setattr(llm, "_retry_policy", lambda *_a, **_k: (0, 0.0))


def test_context_tokens_track_real_prompt_and_growth(client, monkeypatch):
    bodies = []

    async def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        text = _tool_stream() if len(bodies) == 1 else _text_stream("现在是 10:00。")
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text=text)

    monkeypatch.setattr(
        llm, "_new_http_client",
        lambda trust_env=False: httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False))
    monkeypatch.setattr(
        conversation, "_history_messages",
        lambda cid, limit=20, vision=False, max_tokens=0: [{"role": "user", "content": "几点了？"}])

    conv = client.post(f"{API}/conversations", json={}).json()["data"]

    async def collect():
        return [e async for e in conversation.generate_reply_stream(conv["id"], "几点了？")]

    events = asyncio.run(collect())

    usages = [e for e in events
              if isinstance(e.get("usage"), dict) and not e.get("done")]
    assert len(usages) == 2, [sorted(e) for e in events]

    # 1) 真实值到手：读数 = 该轮真实 prompt（锚点归位）
    for e in usages:
        assert e["context_tokens"] == e["usage"]["last_prompt_tokens"], e["context_tokens"]

    # 2) 工具结果进上下文后必须涨（超长任务里读数不能停在同一格）
    tool_result = next(e for e in events if e.get("tool_result"))
    assert tool_result["context_tokens"] > usages[0]["context_tokens"], (
        tool_result["context_tokens"], usages[0]["context_tokens"])

    # 3) 第二轮真实 prompt 已经包含工具结果；done 带着收尾读数
    assert usages[1]["context_tokens"] == 300
    done = next(e for e in events if e.get("done"))
    assert done["context_tokens"] >= usages[1]["context_tokens"]

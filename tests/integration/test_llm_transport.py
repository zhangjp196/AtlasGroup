from __future__ import annotations

import asyncio
import json

import httpx2
import pytest

from services.agent_zero.runtime.zero import llm


@pytest.fixture(autouse=True)
def llm_env(monkeypatch):
    monkeypatch.setenv("AGENT_ZERO_LLM_BASE_URL", "http://gw.local/v1")
    monkeypatch.setenv("AGENT_ZERO_LLM_API_KEY", "EMPTY")
    monkeypatch.setenv("AGENT_ZERO_LLM_MODEL", "fake-model")
    monkeypatch.delenv("AGENT_ZERO_LLM_PROTOCOL", raising=False)
    monkeypatch.setattr(llm, "_retry_policy", lambda *_a, **_k: (0, 0.0))


def _install(monkeypatch, handler):
    monkeypatch.setattr(
        llm, "_new_http_client",
        lambda trust_env=False: httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False))


def _chat_response(body):
    return {
        "id": "x", "object": "chat.completion", "created": 1, "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "hi",
            "reasoning_content": "think",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7,
                  "prompt_tokens_details": {"cached_tokens": 3}},
    }


def _chat_stream():
    chunks = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "think"}}]},
        {"choices": [{"index": 0, "delta": {"content": "hello "}}]},
        {"choices": [{"index": 0, "delta": {"content": "world"}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"path": "a.txt"}'}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13,
                                  "prompt_tokens_details": {"cached_tokens": 2}}},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def test_chat_request_and_stream_events(monkeypatch):
    async def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 77
        assert body["tool_choice"] == "auto"
        if body.get("stream"):
            return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                   text=_chat_stream())
        return httpx2.Response(200, json=_chat_response(body))

    _install(monkeypatch, handler)
    tools = [{"type": "function", "function": {
        "name": "read_file", "description": "read",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]

    async def flow():
        res = await llm.llm_chat("default", [{"role": "user", "content": "hi"}],
                                 tools=tools, max_tokens=77)
        assert res.content == "hi"
        assert res.tool_calls[0]["name"] == "read_file"
        assert res.usage == {"input_tokens": 5, "output_tokens": 2}

        events = [e async for e in llm.llm_chat_stream_full(
            "default", [{"role": "user", "content": "hi"}], tools=tools, max_tokens=77)]
        assert any(e["type"] == "thinking" for e in events)
        assert any(e["type"] == "tool_preparing" for e in events)
        assert any(e["type"] == "tool_progress" for e in events)
        done = events[-1]
        assert done["content"] == "hello world"
        assert done["finish_reason"] == "tool_calls"
        assert done["usage"]["prompt_tokens"] == 9

    asyncio.run(flow())


def test_retry_then_success(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(llm, "_retry_policy", lambda *_a, **_k: (2, 0.0))

    async def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx2.Response(502, json={"error": {"message": "bad gateway"}})
        return httpx2.Response(200, json=_chat_response(json.loads(request.content)))

    _install(monkeypatch, handler)
    res = asyncio.run(llm.llm_chat("default", [{"role": "user", "content": "hi"}]))
    assert res.content == "hi" and calls["n"] == 3


def test_overflow_compaction_retry(monkeypatch):
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx2.Response(400, json={"error": {
                "message": "maximum context length exceeded"}})
        return httpx2.Response(200, json=_chat_response(json.loads(request.content)))

    async def compact(code, messages, attempt):
        return list(messages) + [{"role": "user", "content": "summary"}]

    async def attempts():
        return 1

    monkeypatch.setattr(llm, "_compact_with_guard", compact)
    monkeypatch.setattr(llm, "get_overflow_recovery_attempts", attempts)
    _install(monkeypatch, handler)
    res = asyncio.run(llm.llm_chat("default", [{"role": "user", "content": "hi"}]))
    assert res.content == "hi" and calls["n"] == 2

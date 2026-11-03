"""Anthropic Messages 协议：真实 SDK 解析、鉴权头与 URL 归一化。"""
from __future__ import annotations

import asyncio
import json

import httpx2

from services.agent_zero.runtime.zero import llm


def _sse_text(text: str = "Hello") -> str:
    events = [
        ("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_1", "type": "message", "role": "assistant",
                "model": "claude-test", "content": [], "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        }),
        ("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text},
        }),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        }),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    )


def test_anthropic_stream_uses_messages_endpoint_and_both_auth_headers(monkeypatch):
    seen: list[dict] = []

    async def handler(request):
        seen.append({
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": json.loads(request.content),
        })
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text=_sse_text())

    monkeypatch.setattr(llm, "_llm_section", lambda: {
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "anthropic-secret",
        "model": "claude-test",
        "protocol": "anthropic",
        "thinking_levels": ["medium"],
    })
    monkeypatch.setattr(
        llm, "_new_http_client",
        lambda trust_env=False: httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False))

    async def run():
        llm.set_reasoning_effort("medium")
        return [ev async for ev in llm.llm_chat_stream_full(
            "default", [{"role": "user", "content": "hi"}])]

    events = asyncio.run(run())
    assert [e["content"] for e in events if e["type"] == "delta"] == ["Hello"]
    done = next(e for e in events if e["type"] == "done")
    assert done["content"] == "Hello"
    assert done["usage"]["input_tokens"] == 10

    call = seen[0]
    # 用户填了 /v1，SDK 仍只拼一次 /v1/messages
    assert call["url"].startswith("https://api.anthropic.com/v1/messages")
    assert call["headers"]["x-api-key"] == "anthropic-secret"
    assert call["headers"]["authorization"] == "Bearer anthropic-secret"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["body"]["thinking"] == {"type": "enabled", "budget_tokens": 10000}
    assert "temperature" not in call["body"]


def _sse_tool() -> str:
    events = [
        ("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_2", "type": "message", "role": "assistant",
                "model": "claude-test", "content": [], "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 4, "output_tokens": 1},
            },
        }),
        ("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_1",
                              "name": "read_file", "input": {}},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '"a.txt"}'},
        }),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 2},
        }),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    )


def test_anthropic_tool_use_stream(monkeypatch):
    sent: list[dict] = []

    async def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text=_sse_tool())

    monkeypatch.setattr(llm, "_llm_section", lambda: {
        "base_url": "https://api.anthropic.com",
        "api_key": "anthropic-secret",
        "model": "claude-test",
        "protocol": "anthropic",
    })
    monkeypatch.setattr(
        llm, "_new_http_client",
        lambda trust_env=False: httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False))

    async def run():
        llm.set_reasoning_effort("")
        return [ev async for ev in llm.llm_chat_stream_full(
            "default",
            [{"role": "user", "content": "read a.txt"}],
            tools=[{"type": "function", "function": {
                "name": "read_file", "description": "read",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }}],
        )]

    events = asyncio.run(run())
    done = next(e for e in events if e["type"] == "done")
    assert done["finish_reason"] == "tool_calls"
    assert done["tool_calls"] == [{
        "id": "toolu_1", "name": "read_file", "arguments": {"path": "a.txt"},
    }]
    assert sent[0]["tools"][0]["name"] == "read_file"

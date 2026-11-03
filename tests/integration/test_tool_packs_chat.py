"""对话链路里的工具包：模型调 `net{op:time}` → 执行 `time` → 历史里仍是 `net`。

这条链路最容易出错的三个点：
1. 发给模型的工具清单里只有包（`net`），没有逐个工具；
2. 执行/权限/trace 用具体工具名（`time`），UI 才能正常显示；
3. 回填给模型的 assistant(tool_calls) 保留**它自己调用的名字**（`net`），
   否则模型会看到一个自己没调过的函数名。
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


def test_model_calls_pack_and_loop_records_real_tool(client, monkeypatch):
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

    assert len(bodies) == 2, bodies

    # 1) 工具清单里是包，不是零散工具
    tools = [t["function"]["name"] for t in bodies[0].get("tools") or []]
    assert "net" in tools
    assert "time" not in tools

    # 2) 回填给模型的历史保留它自己的调用名，tool 结果对得上 id
    second = bodies[1]["messages"]
    call = next(m for m in second if m.get("role") == "assistant" and m.get("tool_calls"))
    assert call["tool_calls"][0]["function"]["name"] == "net"
    assert json.loads(call["tool_calls"][0]["function"]["arguments"]) == {"op": "time"}
    assert call["tool_calls"][0]["id"] == "call_1"
    tool_msg = next(m for m in second if m.get("role") == "tool")
    assert tool_msg["tool_call_id"] == "call_1"
    assert "now" in tool_msg["content"]

    # 3) 执行结果与 trace 用具体工具名（UI 才能显示成「当前时间」）
    done = next(e for e in events if e.get("done"))
    trace = done.get("trace") or []
    tool_steps = [s for s in trace if s.get("kind") == "tool"]
    assert tool_steps and tool_steps[0]["name"] == "time"
    # 开始 + 结束两条都标着「模型调的是哪个包/哪个 op」，UI 才能显示成工具包调用
    assert all(s.get("pack") == "net" and s.get("op") == "time" for s in tool_steps), json.dumps(tool_steps, ensure_ascii=False)
    end = next(s for s in tool_steps if s.get("phase") == "end")
    assert end.get("ok") is not False

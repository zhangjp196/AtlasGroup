"""对话链路里的任务清单：模型调 `plan{op:todo}` → 落库 → 推 `todo_update`。

锁定 DSH 口径的三件事：
1. 工具清单里只有 `plan` 包，`todo_write` 不是独立 function；
2. trace 用具体工具名 `todo_write`（带 pack=plan / op=todo），界面能显示成任务清单；
3. 事件与 REST 返回的是 `content` + `pending/in_progress/completed` 三态。
"""
from __future__ import annotations

import asyncio
import json

import httpx2
import pytest

from services.agent_zero.runtime.zero import llm
from services.agent_zero.task_layer import conversation

API = "/api/agent-zero"

_TODOS = [
    {"content": "读需求", "status": "completed"},
    {"content": "改代码", "status": "in_progress"},
    {"content": "跑测试", "status": "pending"},
]


def _todo_stream() -> str:
    args = json.dumps({"op": "todo", "todos": _TODOS}, ensure_ascii=False)
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_todo", "type": "function",
             "function": {"name": "plan", "arguments": args}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 200, "completion_tokens": 8,
                                  "total_tokens": 208}},
    ]
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _text_stream(text: str) -> str:
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": text}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 300, "completion_tokens": 6,
                                  "total_tokens": 306}},
    ]
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"


@pytest.fixture(autouse=True)
def llm_env(monkeypatch):
    monkeypatch.setenv("AGENT_ZERO_LLM_BASE_URL", "http://gw.local/v1")
    monkeypatch.setenv("AGENT_ZERO_LLM_API_KEY", "EMPTY")
    monkeypatch.setenv("AGENT_ZERO_LLM_MODEL", "fake-model")
    monkeypatch.delenv("AGENT_ZERO_LLM_PROTOCOL", raising=False)
    monkeypatch.setattr(llm, "_retry_policy", lambda *_a, **_k: (0, 0.0))


def test_model_writes_todo_list_and_event_carries_dsh_statuses(client, monkeypatch):
    bodies = []

    async def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        text = _todo_stream() if len(bodies) == 1 else _text_stream("清单已更新。")
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text=text)

    monkeypatch.setattr(
        llm, "_new_http_client",
        lambda trust_env=False: httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False))
    monkeypatch.setattr(
        conversation, "_history_messages",
        lambda cid, limit=20, vision=False, max_tokens=0: [
            {"role": "user", "content": "把这次改动列个清单"}])

    conv = client.post(f"{API}/conversations", json={}).json()["data"]

    async def collect():
        return [e async for e in conversation.generate_reply_stream(conv["id"], "列个清单")]

    events = asyncio.run(collect())

    # 1) 工具清单只有包：plan 有 todos 参数，todo_write 不是独立 function
    tools = {t["function"]["name"]: t for t in bodies[0].get("tools") or []}
    assert "plan" in tools
    assert "todo_write" not in tools
    plan_props = tools["plan"]["function"]["parameters"]["properties"]
    assert plan_props["todos"]["type"] == "array"
    assert set(plan_props["todos"]["items"]["properties"]["status"]["enum"]) == {
        "pending", "in_progress", "completed"}

    # 2) todo_update 事件带三态与 counts
    update = next(e for e in events if e.get("todo_update"))
    items = update["todo_update"]["items"]
    assert [it["content"] for it in items] == ["读需求", "改代码", "跑测试"]
    assert [it["status"] for it in items] == ["completed", "in_progress", "pending"]
    assert update["todo_update"]["counts"]["in_progress"] == 1

    # 3) trace 用具体工具名，并标出包 / op
    done = next(e for e in events if e.get("done"))
    steps = [s for s in (done.get("trace") or []) if s.get("kind") == "tool"]
    assert steps and steps[0]["name"] == "todo_write"
    assert steps[0].get("pack") == "plan" and steps[0].get("op") == "todo"

    # 4) REST 读回来是同一份（用户界面刷新后一致）
    rest = client.get(f"{API}/conversations/{conv['id']}/todos").json()["data"]
    assert [it["content"] for it in rest] == ["读需求", "改代码", "跑测试"]
    assert [it["status"] for it in rest] == ["completed", "in_progress", "pending"]

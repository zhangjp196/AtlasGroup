from __future__ import annotations

import asyncio

from services.agent_zero.task_layer import conversation
from services.agent_zero.task_layer import run_service


def test_ping_until_done_emits_heartbeats(monkeypatch):
    monkeypatch.setattr(conversation, "TOOL_KEEPALIVE_SECONDS", 0.05)

    async def flow():
        task = asyncio.ensure_future(asyncio.sleep(0.22))
        pings = []
        async for ev in conversation._ping_until_done(
                task, "", label="call_subagent", rid="r", rname="n"):
            pings.append(ev)
        await task
        return pings

    pings = asyncio.run(flow())
    assert pings, "长时间子代理必须产生 ping，否则会被运行级看门狗误杀"
    assert all(p["ping"] for p in pings)
    assert pings[0]["name"] == "call_subagent"


def test_forward_subagent_events(monkeypatch):
    captured = []
    monkeypatch.setattr(run_service, "append_event",
                        lambda rid, kind, data: captured.append((rid, kind, data)))
    state = {}
    conversation._forward_subagent_event(
        "run1", "call1", "研究员", "调研", {"tool_call": {"id": "t1", "name": "web"}}, state)
    conversation._forward_subagent_event(
        "run1", "call1", "研究员", "调研", {"delta": "第一段"}, state)
    conversation._forward_subagent_event(
        "run1", "call1", "研究员", "调研", {"delta": "第二段"}, state)
    assert [c[1] for c in captured] == ["subagent_event", "subagent_event"]
    assert captured[0][2]["subagent_event"]["event"] == "tool_call"
    assert captured[1][2]["subagent_event"]["event"] == "progress"
    assert captured[1][2]["subagent_event"]["text"] == "第一段"

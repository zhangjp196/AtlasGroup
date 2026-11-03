from __future__ import annotations

import asyncio

from services.agent_zero.services import ask_service


def test_ask_answer_single_and_multi():
    async def flow():
        q = ask_service.create("conv", "run", {
            "question": "pick", "options": ["A", {"label": "B", "description": "stable"}],
            "multi": True,
        })
        task = asyncio.create_task(ask_service.wait(q["id"], "run", timeout=2))
        await asyncio.sleep(0.05)
        ask_service.answer(q["id"], {"selected": ["A", "B"]})
        return await task

    res = asyncio.run(flow())
    assert res["selected"] == ["A", "B"] and not res["ignored"]


def test_ask_ignore_and_other():
    async def flow():
        q = ask_service.create("conv", "run", {"question": "q", "options": ["x"]})
        task = asyncio.create_task(ask_service.wait(q["id"], "run", timeout=2))
        await asyncio.sleep(0.05)
        ask_service.answer(q["id"], {"other": "custom"})
        return await task

    res = asyncio.run(flow())
    assert res["ignored"] is False and res["other"] == "custom"


def test_ask_timeout_defaults_to_ignored():
    async def flow():
        q = ask_service.create("conv", "run", {"question": "q"})
        return await ask_service.wait(q["id"], "run", timeout=0.05)

    res = asyncio.run(flow())
    assert res["ignored"] is True and res.get("reason") == "timeout"

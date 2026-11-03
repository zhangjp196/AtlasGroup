from __future__ import annotations

import asyncio
import time

API = "/api/agent-zero"


def _run_id(resp):
    return (resp.get("data") or {}).get("id")


def test_run_stall_watchdog_disabled_by_default(client, monkeypatch):
    """默认不中止：长时间没有任何事件的 run 会继续等待，而不是被判卡死。

    后台任务可能跑一小时以上（长思考 / 长工具参数 / 上游排队），固定秒数
    一刀切误判太多；现在只有显式配置 `llm.run_event_stall_s` 才启用硬兜底。
    """
    from services.agent_zero.task_layer import conversation, conv_worker

    assert conv_worker._stall_timeout() == 0.0

    async def fake_stream(*args, **kwargs):
        yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
        # 静默 2.5s（远超旧的 180s 之外的任何小值），期间**不产任何事件**
        await asyncio.sleep(2.5)
        yield {"delta": {"delta": "ok", "round": 0}}
        yield {"done": {"done": True, "content": "ok", "usage": {}}}

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                 json={"content": "慢任务", "mode": "chat"}).json())

    deadline = time.time() + 15
    status = ""
    while time.time() < deadline:
        status = (client.get(f"{API}/runs/{run_id}").json()["data"]["run"] or {}).get("status", "")
        if status in ("done", "failed", "stopped"):
            break
        time.sleep(0.1)
    assert status == "done", status


def test_run_stall_watchdog_fails_when_explicitly_configured(client, monkeypatch):
    """显式配置静默上限时仍然生效（保留可选的硬兜底），且队列继续推进。"""
    from services.agent_zero.task_layer import conversation, conv_worker

    monkeypatch.setattr(conv_worker, "_stall_timeout", lambda: 0.4)

    mode = {"stall": True}

    async def fake_stream(*args, **kwargs):
        yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
        if mode["stall"]:
            await asyncio.sleep(60)
        yield {"delta": {"delta": "ok", "round": 0}}
        yield {"done": {"done": True, "content": "ok", "usage": {}}}

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                 json={"content": "卡死", "mode": "chat"}).json())

    deadline = time.time() + 8
    status = ""
    while time.time() < deadline:
        status = (client.get(f"{API}/runs/{run_id}").json()["data"]["run"] or {}).get("status", "")
        if status in ("done", "failed", "stopped"):
            break
        time.sleep(0.1)
    assert status == "failed", status

    mode["stall"] = False
    run2 = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                               json={"content": "继续", "mode": "chat"}).json())
    deadline = time.time() + 8
    status2 = ""
    while time.time() < deadline:
        status2 = (client.get(f"{API}/runs/{run2}").json()["data"]["run"] or {}).get("status", "")
        if status2 in ("done", "failed", "stopped"):
            break
        time.sleep(0.1)
    assert status2 == "done", status2


def test_llm_stream_keepalive_never_times_out_by_default():
    """上游长时间（超过旧的 120s 判定点，用压缩后的时间尺度模拟）不发事件时，
    只发心跳、不抛错；心跳事件带 phase 供前端区分「等首个 token」与「输出中断」。"""
    from services.agent_zero.task_layer import conversation

    monkeypatch_keepalive = 0.05
    __import__("services.agent_zero.runtime.zero.llm", fromlist=["x"])
    from services.agent_zero.runtime.zero import llm as llm_mod

    orig = llm_mod.get_stream_watchdog_config
    llm_mod.get_stream_watchdog_config = lambda: (monkeypatch_keepalive, 0.0)
    try:
        async def slow():
            await asyncio.sleep(0.35)          # 相当于 7 个心跳周期，远超 0（不中止）
            yield {"type": "delta", "content": "hi"}
            yield {"type": "done", "content": "hi"}

        async def run():
            out = []
            async for ev in conversation._iter_stream_with_keepalive(slow()):
                out.append(ev)
                if ev.get("type") == "delta":
                    break
            return out

        events = asyncio.run(run())
    finally:
        llm_mod.get_stream_watchdog_config = orig

    pings = [e for e in events if e.get("type") == "keepalive"]
    assert len(pings) >= 4, events
    assert all(p["phase"] == "first_token" for p in pings)
    assert not any(isinstance(e.get("type"), type(None)) for e in events)


def test_llm_stream_mid_output_phase_flag():
    """已经产出过内容后再静默：心跳标记为 mid_output（不是首 token 等待）。"""
    from services.agent_zero.task_layer import conversation
    from services.agent_zero.runtime.zero import llm as llm_mod

    orig = llm_mod.get_stream_watchdog_config
    llm_mod.get_stream_watchdog_config = lambda: (0.05, 0.0)
    try:
        async def slow():
            yield {"type": "delta", "content": "a"}
            await asyncio.sleep(0.18)
            yield {"type": "done", "content": "a"}

        async def run():
            out = []
            async for ev in conversation._iter_stream_with_keepalive(slow()):
                out.append(ev)
            return out

        events = asyncio.run(run())
    finally:
        llm_mod.get_stream_watchdog_config = orig

    pings = [e for e in events if e.get("type") == "keepalive"]
    assert pings and all(p["phase"] == "mid_output" for p in pings), events

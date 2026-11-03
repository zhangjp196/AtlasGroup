"""「运行卡住、必须停止再发起」的三条静默路径回归测试。

现场证据（2026-10-29，data/agent_zero/agent_zero.db）：

- ``run-55f1fa8dd611``：10:00:03 落了一条 ``tool_call(status=preparing)``，之后
  **整整 110 秒零事件**，直到用户点停止才产生 ``interrupted``。原因是上游在生成
  工具参数 JSON 期间每 2s 发一次 ``tool_progress``，而对话层没有该分支、
  `_iter_stream_with_keepalive` 又因「有动静」不发心跳 —— 两边都不产出事件。
- 附带缺口：worker 直接 ``await 生成器``，静默期间既看不见 ``stop_requested``，
  也没法按墙钟 / 静默阈值收尾。

本文件锁定：静默期必须有存活信号、静默期停止必须立刻生效、SSE 必须以
``run_end`` 收尾（前端据此结束「处理中」）。
"""
from __future__ import annotations

import asyncio
import json
import time

API = "/api/agent-zero"


def _run_id(resp):
    return (resp.get("data") or {}).get("id")


def _wait_status(client, run_id, wanted, timeout=10.0):
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = (client.get(f"{API}/runs/{run_id}").json()["data"]["run"] or {}).get(
            "status", "")
        if status in wanted:
            return status
        time.sleep(0.05)
    return status


def test_long_tool_args_keep_run_alive(client, monkeypatch):
    """工具参数 JSON 长时间生成期间，整条 run 不得静默（现场 bug 的复现）。

    真实链路：LLM 层在参数生成期**只发 `tool_progress`**（每 2s 一条），不发
    delta / thinking。修复前对话层没有 `tool_progress` 分支 → 事件被丢弃；
    `_iter_stream_with_keepalive` 又因「收到事件」不断重置空闲计时 → 心跳也不发。
    结果是 run 事件表里出现一条「preparing 之后长时间零事件」的空洞，前端只能
    一直转圈，直到用户手动点停止。

    这里把 LLM 层替换成「准备 1.2s 才吐出工具调用」的慢上游：
      - 修复后：期间有 ping 落库（用户看得到「仍在处理」）；
      - 修复前：整段零事件，本用例失败。
    """
    from services.agent_zero.runtime.zero import llm as llm_mod

    ARG_GEN_S = 1.2          # 参数生成耗时（远超心跳阈值）

    async def slow_stream_full(self, role, messages, **kw):
        # 第一轮：慢慢生成一次 tool_call 的参数；第二轮：正常收尾
        calls = sum(1 for m in messages if m.get("role") == "tool")
        if calls == 0:
            yield {"type": "tool_preparing", "id": "t1", "name": "time"}
            waited = 0.0
            while waited < ARG_GEN_S:
                await asyncio.sleep(0.2)
                waited += 0.2
                # 只有「上游还在写」这个信号，没有正文/思考增量
                yield {"type": "tool_progress", "id": "t1", "index": 0}
            yield {"type": "done", "content": "",
                   "tool_calls": [{"id": "t1", "name": "time", "arguments": {}}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                   "finish_reason": "tool_calls"}
        else:
            yield {"type": "done", "content": "好",
                   "tool_calls": [],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                   "finish_reason": "stop"}

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", slow_stream_full)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                 json={"content": "调用 time", "mode": "chat"}).json())

    status = _wait_status(client, run_id, ("done", "failed", "stopped"), timeout=20)
    assert status in ("done", "failed"), status

    events = client.get(f"{API}/runs/{run_id}").json()["data"]["events"]
    pings = [e for e in events if e["_kind"] == "ping"]
    assert pings, f"参数生成期完全没有事件（现场 bug）：{[(e['_seq'], e['_kind']) for e in events]}"
    assert any(p.get("waiting") == "tool" or p.get("waiting") == "worker" for p in pings), pings


def test_worker_emits_heartbeat_when_generator_is_silent(client, monkeypatch):
    """生成器静默（既不产事件也不报错）时，worker 必须自己补心跳，
    并保证 run 最终仍正常收尾 —— 前端不会永远转圈。"""
    from services.agent_zero.task_layer import conversation, conv_worker

    monkeypatch.setattr(conv_worker, "_HEARTBEAT_S", 0.3)

    async def fake_stream(*args, **kwargs):
        yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
        await asyncio.sleep(1.2)          # 远超心跳阈值，期间不产任何事件
        yield {"done": {"done": True, "content": "ok", "usage": {}}}

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                 json={"content": "静默任务", "mode": "chat"}).json())

    status = _wait_status(client, run_id, ("done", "failed", "stopped"), timeout=15)
    assert status == "done", status

    events = client.get(f"{API}/runs/{run_id}").json()["data"]["events"]
    pings = [e for e in events if e.get("_kind") == "ping"]
    assert pings, events
    assert any(p.get("waiting") == "worker" for p in pings), pings


def test_stop_takes_effect_during_silence(client, monkeypatch):
    """生成器静默期间点停止，必须立刻收尾（不能等下一个事件才生效）。"""
    from services.agent_zero.task_layer import conversation

    entered = {"silent": False}

    async def fake_stream(*args, **kwargs):
        yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
        entered["silent"] = True
        await asyncio.sleep(60)           # 长时间静默，只能靠 stop 打断
        yield {"done": {"done": True, "content": "never", "usage": {}}}

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                 json={"content": "静默后停止", "mode": "chat"}).json())

    deadline = time.time() + 5
    while time.time() < deadline and not entered["silent"]:
        time.sleep(0.05)
    assert entered["silent"], "生成器没进入静默段"

    t0 = time.time()
    client.post(f"{API}/runs/{run_id}/stop")
    status = _wait_status(client, run_id, ("stopped", "failed", "done"), timeout=10)
    assert status == "stopped", status
    assert time.time() - t0 < 5.0, "静默期间的停止没有及时生效"


def test_stream_ends_immediately_when_run_already_terminal(client, monkeypatch):
    """run 已终态时订阅事件流：必须立刻收到 run_end。

    这是**加固**（不是本次现场的直接成因）：修前这条路径会在一个 interval 后
    补到 run_end；若该轮 DB 读失败就退化成 ping 到 idle 上限。断言收紧到 3s，
    防止以后回归成「靠 idle 上限兜底」。
    """
    from services.agent_zero.task_layer import conversation

    async def fake_stream(*args, **kwargs):
        yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
        yield {"done": {"done": True, "content": "ok", "usage": {}}}

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                 json={"content": "快速完成", "mode": "chat"}).json())
    status = _wait_status(client, run_id, ("done", "failed", "stopped"), timeout=10)
    assert status == "done", status

    t0 = time.time()
    got_end = False
    with client.stream("GET", f"{API}/runs/{run_id}/stream?since=0") as resp:
        for line in resp.iter_lines():
            if line.startswith("event: run_end"):
                got_end = True
                break
            if time.time() - t0 > 6:
                break
    assert got_end, "终态 run 的 SSE 没有发 run_end"
    assert time.time() - t0 < 3.0, "终态 run 的 SSE 拖了太久才结束"


def test_stream_closes_with_run_end_when_stop_requested_mid_stream(client, monkeypatch):
    """中途停止：SSE 必须以 run_end 收尾（前端据此结束「处理中」）。"""
    from services.agent_zero.task_layer import conversation

    async def fake_stream(*args, **kwargs):
        yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
        i = 0
        while True:
            i += 1
            yield {"delta": {"delta": f"c{i} ", "round": 1,
                             "role_id": "default", "role_name": "Atlas"}}
            await asyncio.sleep(0.1)

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                 json={"content": "长流", "mode": "chat"}).json())
    time.sleep(0.6)

    lines = []

    def _consume():
        with client.stream("GET", f"{API}/runs/{run_id}/stream?since=0") as resp:
            for line in resp.iter_lines():
                lines.append(line)
                if line.startswith("event: run_end"):
                    break

    import threading

    t = threading.Thread(target=_consume, daemon=True)
    t.start()
    time.sleep(0.5)
    client.post(f"{API}/runs/{run_id}/stop")
    t.join(timeout=15)

    assert any(ln.startswith("event: run_end") for ln in lines), lines[-8:]
    payload = [ln for ln in lines if ln.startswith("data:")][-1]
    run = json.loads(payload[len("data:"):].strip())["run"]
    assert run and run.get("status") in ("stopped", "failed", "done"), run

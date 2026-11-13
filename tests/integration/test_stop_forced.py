"""停止必须是真的强制停止（集成测试）。

覆盖以前会「停不下来」的路径：

1. **强制停止立即生效**：长运行中调用 stop，run 很快落 stopped，且生成器关闭。
2. **已产出的正文不丢**：停止时缓冲里的 delta 已经落库。
3. **失联 / 排队 run 也能停**：不再干等 30s 失联回收。
4. **同步工具不阻塞事件循环**：否则 task.cancel() 投递不进去，停止无效。
"""
from __future__ import annotations

import asyncio
import threading
import time

API = "/api/agent-zero"


def _run_id(resp):
    return (resp.get("data") or {}).get("id")


def _wait_status(client, run_id, wanted, timeout=10.0):
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = (client.get(f"{API}/runs/{run_id}").json()["data"]["run"] or {}).get("status", "")
        if status in wanted:
            return status
        time.sleep(0.05)
    return status


def test_stop_watchdog_thread_finalizes_without_event_loop(client, monkeypatch):
    """事件循环被同步代码钉住时，点停止仍然必须收尾（线程级兜底）。

    现场（2026-10-29 及之后多次）：停止请求投递到事件循环，但某段同步代码把
    循环钉住，`loop.call_later` 的兜底永远等不到执行 —— run 一直挂在 running，
    界面停在「处理中」，用户只能重发消息。这里直接验证线程兜底本身：
    不经事件循环，到点就按库里状态收尾。
    """
    from services.agent_zero.task_layer import conv_worker, run_service

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = run_service.create_run(cid, "local", content="卡住的运行",
                                   mode="chat")["id"]
    assert run_service.claim_run(run_id) is True
    assert run_service.request_stop(run_id) is True

    # 线程兜底：直接调用「到点执行」的那个函数（等价于 Timer 到期）
    conv_worker._stop_watchdog_fire(run_id)

    run = run_service.get_run(run_id)
    assert run["status"] in ("stopped", "done", "failed"), run["status"]
    assert run["status"] == "stopped", run["status"]
    # 幂等：再触发一次不改状态、不重复落消息
    conv_worker._stop_watchdog_fire(run_id)
    assert run_service.get_run(run_id)["status"] == "stopped"


def test_stop_watchdog_is_disarmed_after_run_finishes(client, monkeypatch):
    """run 正常收尾后兜底要解除：否则线程会在结束后还去查库 / 误报。"""
    from services.agent_zero.task_layer import conv_worker, run_service

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = run_service.create_run(cid, "local", content="即将结束",
                                   mode="chat")["id"]
    run_service.claim_run(run_id)
    conv_worker._arm_stop_watchdog(run_id, delay=30)
    assert run_id in conv_worker._WATCHDOG_THREADS
    conv_worker._disarm_stop_watchdog(run_id)
    assert run_id not in conv_worker._WATCHDOG_THREADS


def test_stop_is_forced_and_keeps_partial_text(client, monkeypatch):
    from services.agent_zero.task_layer import conversation

    started = threading.Event()
    closed = threading.Event()

    async def fake_stream(*args, **kwargs):
        try:
            yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
            i = 0
            while True:
                i += 1
                yield {"delta": {"delta": f"chunk{i} ", "round": 1,
                                 "role_id": "default", "role_name": "Atlas"}}
                started.set()
                await asyncio.sleep(0.1)
        finally:
            closed.set()

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                 json={"content": "长任务", "mode": "chat"}).json())
    assert run_id

    assert started.wait(5), "生成器未开始输出"
    resp = client.post(f"{API}/runs/{run_id}/stop").json()
    assert resp["code"] == 0
    assert resp["data"]["forced"] is True, resp

    status = _wait_status(client, run_id, {"stopped"}, timeout=8.0)
    assert status == "stopped", status

    assert closed.wait(2), "停止后生成器仍未关闭"

    data = client.get(f"{API}/runs/{run_id}?since=0").json()["data"]
    text = "".join(str(e.get("delta") or "") for e in data["events"]
                   if e.get("_kind") == "delta")
    assert "chunk1" in text, text[:200]   # 已产出的正文不能丢


def test_stop_orphan_running_immediately(client):
    """DB 里 running 但没有存活任务的 run：stop 必须立即收尾。"""
    from services.agent_zero.task_layer import run_service

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = run_service.create_run(cid, "system", content="假运行", mode="chat")["id"]
    assert run_service.claim_run(run_id)

    resp = client.post(f"{API}/runs/{run_id}/stop").json()
    assert resp["code"] == 0
    assert resp["data"]["forced"] is False

    run = client.get(f"{API}/runs/{run_id}").json()["data"]["run"]
    assert run["status"] == "stopped", run


def test_stop_queued_run_finishes_it(client):
    from services.agent_zero.task_layer import run_service

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = run_service.create_run(cid, "system", content="排队", mode="chat")["id"]

    client.post(f"{API}/runs/{run_id}/stop")
    run = client.get(f"{API}/runs/{run_id}").json()["data"]["run"]
    assert run["status"] == "stopped", run


class _BlockingRegistry:
    """把 read_file 换成一个「纯同步、阻塞 0.3s」的工具，模拟旧行为。"""

    def __init__(self, real):
        self._real = real

    def canonical(self, name):
        return name

    def get_tool(self, canon):
        return self._real.get_tool(canon)


def test_sync_tool_runs_off_event_loop(client, monkeypatch):
    """同步工具必须走线程池。

    否则事件循环被一个阻塞工具钉死：这段时间里 task.cancel() 投递不进去、
    SSE 也推不出帧，用户点「停止」就是没反应。这里用一个阻塞 0.3s 的假
    read_file 验证：工具执行期间 ticker 仍能持续拿到调度。
    """
    from services.agent_zero.runtime.zero import dispatch, file_tools

    def blocking_exec_tool(code, ws_id, args):
        time.sleep(0.3)
        return {"content": "ok"}

    monkeypatch.setattr(file_tools, "exec_tool", blocking_exec_tool)

    async def main():
        d = dispatch.ToolDispatcher()
        ticks = {"n": 0}

        async def ticker():
            while True:
                ticks["n"] += 1
                await asyncio.sleep(0.01)

        t = asyncio.ensure_future(ticker())
        await asyncio.sleep(0.01)
        baseline = ticks["n"]
        try:
            result = await d._invoke("read_file", {"workspace_id": "ws"}, {}, {},
                                     {"path": "a.txt"})
        finally:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        return result, ticks["n"] - baseline

    result, advanced = asyncio.run(main())
    assert result == {"content": "ok"}
    # 阻塞 0.3s、ticker 每 10ms 一次 → 线程池下应推进 ~20+ 次；
    # 同步直调时事件循环被钉住，这里只会有 0~1 次。
    assert advanced >= 5, f"事件循环被同步工具阻塞了（ticker 只推进 {advanced} 次）"



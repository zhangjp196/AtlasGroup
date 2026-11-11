"""模型档案并发上限：默认 2，按配置生效并可热更新。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from services.agent_zero.runtime.zero import llm


def _peak_for(concurrency: int, jobs: int = 6) -> int:
    async def run() -> int:
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def job() -> None:
            nonlocal active, peak
            async with llm._model_slot({"code": "m-test", "concurrency": concurrency}):
                async with lock:
                    active += 1
                    peak = max(peak, active)
                await asyncio.sleep(0.02)
                async with lock:
                    active -= 1

        await asyncio.gather(*(job() for _ in range(jobs)))
        return peak

    return asyncio.run(run())


def test_default_and_clamped_concurrency():
    assert llm.model_concurrency({}) == 2
    assert llm.model_concurrency({"concurrency": 0}) == 2
    assert llm.model_concurrency({"concurrency": -3}) == 2
    assert llm.model_concurrency({"concurrency": "bad"}) == 2
    assert llm.model_concurrency({"concurrency": 3}) == 3
    assert llm.model_concurrency({"concurrency": 999}) == llm.MODEL_CONCURRENCY_MAX


def test_slots_are_limited_per_model():
    assert _peak_for(1) == 1
    assert _peak_for(2) == 2
    assert _peak_for(4) == 4


def test_changing_limit_rebuilds_semaphore():
    async def run():
        cfg = {"code": "m-rebuild", "concurrency": 2}
        first = llm._model_semaphore(cfg)
        cfg["concurrency"] = 5
        second = llm._model_semaphore(cfg)
        assert first is not second

    asyncio.run(run())


def test_slots_work_across_event_loops():
    # 测试 / 重启会新建事件循环：旧 Semaphore 不能把新循环里的调用卡死。
    assert _peak_for(2) == 2
    assert _peak_for(2) == 2


def test_slots_are_global_across_event_loops():
    """并发上限是「整个进程」的，不是每个事件循环各一份。

    记忆重建会在独立线程里 `asyncio.run`，如果闸门只存在于某个循环的
    `asyncio.Semaphore` 里，配置的并发上限就会被循环数放大（看起来没生效）。
    """
    import threading

    state = {"active": 0, "peak": 0}
    lock = threading.Lock()
    cfg = {"code": "m-cross-loop", "concurrency": 2}

    async def job() -> None:
        async with llm._model_slot(cfg):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            await asyncio.sleep(0.05)
            with lock:
                state["active"] -= 1

    def worker() -> None:
        async def run() -> None:
            await asyncio.gather(*(job() for _ in range(3)))

        asyncio.run(run())

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["peak"] == 2


def test_model_concurrency_saved_and_exposed(client):
    res = client.put("/api/agent-zero/app/settings", json={"models": [
        {"code": "fast", "name": "Fast", "model": "gpt-fast",
         "base_url": "https://api.example.com/v1", "concurrency": 3},
        {"code": "slow", "name": "Slow", "model": "gpt-slow",
         "base_url": "https://api.example.com/v1"},
    ]})
    assert res.status_code == 200, res.text
    models = {m["code"]: m for m in res.json()["data"]["models"]}
    assert models["fast"]["concurrency"] == 3
    assert models["fast"]["effective_concurrency"] == 3
    # 没填 = 默认 2
    assert models["slow"]["concurrency"] == 2
    assert models["slow"]["effective_concurrency"] == 2
    assert models["default"]["concurrency"] == 2


def test_model_concurrency_rejects_out_of_range(client):
    res = client.put("/api/agent-zero/app/settings", json={"models": [
        {"code": "bad", "model": "x", "base_url": "https://x/v1", "concurrency": 0},
    ]})
    assert res.status_code == 422


def test_stream_request_holds_slot_for_whole_stream(monkeypatch):
    """不是只限「建立连接」：整个流式请求期间都占一个槽位。"""
    state = {"active": 0, "peak": 0}

    class FakeStream:
        usage = None
        finish_reason = "stop"

        async def __aenter__(self):
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            return self

        async def __aexit__(self, *exc):
            state["active"] -= 1
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(0.03)
            raise StopAsyncIteration

        def get(self):
            return SimpleNamespace(parts=[])

    class FakeModel:
        def request_stream(self, *args, **kwargs):
            return FakeStream()

    @asynccontextmanager
    async def fake_open(cfg):
        yield FakeModel()

    async def fake_config(code):
        return {"code": "m-stream", "concurrency": 2, "model": "x",
                "base_url": "https://x/v1"}

    monkeypatch.setattr(llm, "_open_model", fake_open)
    monkeypatch.setattr(llm, "get_llm_config", fake_config)
    monkeypatch.setattr(llm, "_model_settings", lambda *a, **k: {})

    async def job():
        async for _ in llm._llm_chat_stream_full_once(
            "m-stream", [{"role": "user", "content": "hi"}]
        ):
            pass

    async def run() -> int:
        await asyncio.gather(*(job() for _ in range(4)))
        return state["peak"]

    assert asyncio.run(run()) == 2

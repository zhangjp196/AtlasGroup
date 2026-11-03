"""自动重试的「可见性」回归测试。

背景：`llm.py` 会 yield `{"type": "retry", ...}`（网络抖动自动重试，默认
20 次 × 2.5s），但对话层曾经**没有对应分支**，事件被静默丢弃 —— 用户只看到
一片空白，以为卡死；这正是「误判 / 体验极差」的来源之一。

这里守住两件事：
1. `_stream_role` 把 retry 翻成前端可渲染的 `{"retry": {...}}`（带中文文案）；
2. worker 的 `_evt_kind` 能识别 retry，事件不被归成泛化的 "event"。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List


from services.agent_zero.task_layer import conv_worker
from services.agent_zero.task_layer import conversation


def _retry_evt(attempt: int = 3, max_attempts: int = 20,
               kind: str = "network", resume: bool = False) -> Dict[str, Any]:
    return {"type": "retry", "kind": kind, "attempt": attempt,
            "max_attempts": max_attempts, "resume": resume,
            "error": "connection reset"}


def test_worker_recognizes_retry_event_kind():
    """retry 必须被当作一等事件类型，否则落库/推送会被泛化成 "event"。"""
    assert conv_worker._evt_kind({"retry": {"attempt": 1}}) == "retry"


def test_stream_role_surfaces_retry_event(monkeypatch):
    """LLM 层的 retry 事件必须能到达前端（不被静默丢弃）。"""

    async def fake_chat_stream_full(self, role, messages, **kw):
        yield {"type": "delta", "content": "部分"}
        yield _retry_evt(kind="network", attempt=3, resume=True)
        yield {"type": "done", "content": "部分完成", "tool_calls": [],
               "usage": {}, "finish_reason": "stop"}

    from services.agent_zero.runtime.zero import llm as llm_mod

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_chat_stream_full)
    # 心跳不需要真等 20s
    monkeypatch.setattr(llm_mod, "get_stream_watchdog_config", lambda: (0.05, 0.0))

    conv = {"id": "conv-test", "model_code": ""}
    role = {"id": "r1", "name": "Atlas", "envelope": {}, "system_prompt": ""}
    sink: Dict[str, Any] = {}

    async def run() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        gen = conversation._stream_role(
            conv, "你好", role, sink, autonomous=False, auto_execute=False)
        async for ev in gen:
            out.append(ev)
        return out

    events = asyncio.run(run())
    retries = [e for e in events if e.get("retry")]
    assert retries, events
    r = retries[0]["retry"]
    assert r["kind"] == "network"
    assert r["label"] == "网络中断"
    assert r["attempt"] == 3 and r["max_attempts"] == 20
    assert r["resume"] is True
    assert "自动重试" in r["message"]
    assert "3/20" in r["message"]


def test_trace_from_run_events_keeps_retry_and_continuous_text():
    """重试事件要进时间线（刷新后仍在），且不切断同一段正文。"""
    events = [
        {"delta": "第一段", "round": 1},
        {"retry": {"attempt": 1, "max_attempts": 20, "message": "网络中断，正在自动重试（1/20）"},
         "round": 1},
        {"delta": "接着说", "round": 1},
        {"retry": {"attempt": 2, "max_attempts": 20, "message": "网络中断，正在自动重试（2/20）"},
         "round": 1},
        {"delta": "最后", "round": 1},
    ]
    out = conversation._trace_from_run_events(events)
    trace = out["trace"]
    texts = [s for s in trace if s.get("kind") == "text"]
    retries = [s for s in trace if s.get("kind") == "retry"]
    # 正文没被重试切断：只有一条 text，且内容完整
    assert len(texts) == 1, trace
    assert texts[0]["text"] == "第一段接着说最后"
    # 同一轮多次重试合并成一条，取最新进度
    assert len(retries) == 1, trace
    assert retries[0]["attempt"] == 2
    assert out["content"] == "第一段接着说最后"


def test_stream_role_labels_timeout_retry(monkeypatch):
    """不同失败类型给出可读标签（超时 vs 网络 vs 限流）。"""

    async def fake_chat_stream_full(self, role, messages, **kw):
        yield _retry_evt(kind="timeout", attempt=1)
        yield {"type": "done", "content": "", "tool_calls": [], "usage": {},
               "finish_reason": "stop"}

    from services.agent_zero.runtime.zero import llm as llm_mod

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_chat_stream_full)
    monkeypatch.setattr(llm_mod, "get_stream_watchdog_config", lambda: (0.05, 0.0))

    sink: Dict[str, Any] = {}
    role = {"id": "r1", "name": "Atlas", "envelope": {}, "system_prompt": ""}

    async def run():
        out = []
        async for ev in conversation._stream_role(
                {"id": "c", "model_code": ""}, "hi", role, sink):
            out.append(ev)
        return out

    events = asyncio.run(run())
    r = [e for e in events if e.get("retry")][0]["retry"]
    assert r["label"] == "请求超时"

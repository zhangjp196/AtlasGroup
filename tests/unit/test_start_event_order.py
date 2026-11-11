"""流式回复的第一帧必须是「谁在说话」。

群聊里一轮会依次让主持 / 成员开口；只要 start 事件排在提示词准备之后，
前端就只能先用兜底的「助手」占位，等服务端把角色算完才换名字——观感就是
「先显示助手，再变成指定 / 通用角色」。这里锁住顺序：准备再慢，start 也先出。
"""
from __future__ import annotations

import asyncio
import time

from services.agent_zero.task_layer import conversation


def test_start_event_precedes_role_preparation(monkeypatch):
    prepared: list[int] = []

    async def slow_prepare(*args, **kwargs):
        prepared.append(1)
        await asyncio.sleep(5)
        raise AssertionError("start 事件不应该等到准备结束才下发")

    monkeypatch.setattr(conversation, "_prepare_role_run", slow_prepare)

    async def run():
        gen = conversation._stream_role(
            {"id": "conv-1"}, "local", {"id": "role-host", "name": "主持人"}, {})
        started = time.monotonic()
        first = await gen.__anext__()
        elapsed = time.monotonic() - started
        await gen.aclose()
        return first, elapsed

    first, elapsed = asyncio.run(run())

    assert first == {"start": True, "role_id": "role-host", "role_name": "主持人"}
    assert elapsed < 0.5, "start 事件被准备阶段拖住了"
    assert prepared == []


def test_worker_sends_start_before_slow_checkpoint(app_env, monkeypatch):
    """检查点慢 1.5s 时，start（谁在说话）仍要立刻落库。

    前端在收到 start 之前只能显示预期角色 / 兜底占位；如果 start 被文件检查点
    挡住，用户就会盯着一个没有身份的气泡等好几秒。
    """
    from services.agent_zero import database
    from services.agent_zero.services import file_checkpoint_service
    from services.agent_zero.stores import zero_store
    from services.agent_zero.task_layer import conv_worker, run_service, title_service

    database.init_database()
    zero_store.ensure_defaults()
    conv = conversation.create_conversation(zero_store.DEFAULT_WORKSPACE_ID, "local")
    msg = conversation.send_message(conv["id"], "你好", "local")
    run = run_service.create_run(conv["id"], "local", content="你好", mode="chat",
                                 queued_msg_id=msg["id"])

    monkeypatch.setattr(
        file_checkpoint_service, "create_for_run",
        lambda *a, **kw: (time.sleep(1.5), {"id": "ckpt-test"})[1])
    monkeypatch.setattr(title_service, "schedule", lambda *_: None)

    async def fake_stream(conv_id, user, content="", **kwargs):
        yield {"start": True, "role_id": "default", "role_name": "通用助手"}
        yield {"delta": "好的"}
        yield {"done": True, "id": "node-1", "text": "好的", "usage": {}}

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    stamps: dict[str, float] = {}
    original_append = run_service.append_event

    def record_append(run_id, kind, data):
        stamps.setdefault(kind, time.monotonic())
        return original_append(run_id, kind, data)

    monkeypatch.setattr(run_service, "append_event", record_append)
    monkeypatch.setattr(conv_worker.run_service, "append_event", record_append)

    async def run_it():
        worker = conv_worker.ConversationRunWorker()
        started = time.monotonic()
        await worker._execute(run["id"])
        return started

    started = asyncio.run(run_it())

    assert "start" in stamps
    assert "done" in stamps
    # start 不等检查点：远小于 1.5s 的检查点耗时。
    assert stamps["start"] - started < 0.5
    # 后续事件仍然等检查点收尾：说明快照语义没被破坏。
    assert stamps["done"] - started >= 1.0

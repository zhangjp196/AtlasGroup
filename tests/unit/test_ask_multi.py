"""ask_user：多个会话同时提问时互不干扰。"""

from __future__ import annotations

import asyncio


def test_two_sessions_pending_questions_are_independent():
    from services.agent_zero.services import ask_service

    try:
        qa = ask_service.create("conv-a", "run-a", {
            "question": "A 选哪个？", "options": ["1", "2"]})
        qb = ask_service.create("conv-b", "run-b", {
            "question": "B 选哪个？", "options": ["x", "y"]})

        assert [q["id"] for q in ask_service.pending_for("conv-a")] == [qa["id"]]
        assert [q["id"] for q in ask_service.pending_for("conv-b")] == [qb["id"]]

        # 回答 A 不影响 B
        out = ask_service.answer(qa["id"], {"selected": ["1"]})
        assert out["selected"] == ["1"]
        assert ask_service.pending_for("conv-a") == []
        assert [q["id"] for q in ask_service.pending_for("conv-b")] == [qb["id"]]

        # 停止 B 的 run：只取消 B 的问题
        assert ask_service.cancel_run("run-b") == 1
        slot = ask_service.get(qb["id"])
        assert slot and slot["answer"]["ignored"] is True
        assert slot["answer"]["reason"] == "stopped"
        assert ask_service.pending_for("conv-b") == []
    finally:
        ask_service._pending.clear()


def test_active_runs_expose_pending_question(monkeypatch):
    """左侧「运行中」需要拿到 pending_question 才能显示闪烁的回答按钮。"""
    from services.agent_zero.routes import task_routes
    from services.agent_zero.services import ask_service
    from services.agent_zero.task_layer import run_service

    q = ask_service.create("conv-run", "run-x", {"question": "要继续吗？"})
    try:
        monkeypatch.setattr(run_service, "list_active_runs",
                            lambda limit=30: [{"id": "run-x",
                                               "conversation_id": "conv-run",
                                               "status": "running"}])
        data = task_routes.active_runs(limit=30)["data"]
        assert data[0]["pending_question"]["id"] == q["id"]
        assert data[0]["pending_question"]["question"] == "要继续吗？"
    finally:
        ask_service._pending.clear()


def test_active_runs_expose_pending_approval(monkeypatch):
    """权限授权也要让左侧运行中显示闪烁按钮。"""
    from services.agent_zero.routes import task_routes
    from services.agent_zero.task_layer import permissions, run_service

    monkeypatch.setattr(run_service, "list_active_runs",
                        lambda limit=30: [{"id": "run-a", "conversation_id": "conv-a",
                                           "status": "running"}])
    monkeypatch.setattr(permissions, "list_pending",
                        lambda cid: [{"id": "ap-1", "run_id": "run-a",
                                      "tool": "shell", "risk": "high",
                                      "note": "准备删除旧构建目录"}])
    data = task_routes.active_runs(limit=30)["data"]
    assert data[0]["pending_approval"]["id"] == "ap-1"
    assert data[0]["pending_approval"]["tool"] == "shell"
    assert data[0]["pending_approval"]["risk"] == "high"


def test_wait_returns_only_its_own_answer():
    from services.agent_zero.services import ask_service

    async def scenario():
        qa = ask_service.create("conv-a", "run-a", {"question": "A?"})
        qb = ask_service.create("conv-b", "run-b", {"question": "B?"})
        ask_service.answer(qa["id"], {"other": "A 的答案"})
        ask_service.answer(qb["id"], {"other": "B 的答案"})
        a = await ask_service.wait(qa["id"], "run-a", timeout=1)
        b = await ask_service.wait(qb["id"], "run-b", timeout=1)
        assert a["other"] == "A 的答案"
        assert b["other"] == "B 的答案"

    try:
        asyncio.run(scenario())
    finally:
        ask_service._pending.clear()

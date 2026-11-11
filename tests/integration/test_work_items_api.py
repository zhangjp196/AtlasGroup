"""Superagent 事项：CRUD、准入策略、GitHub 链接解析与派活隔离。"""
from __future__ import annotations

import asyncio

from services.agent_zero.services import work_service


def test_work_item_crud(client):
    created = client.post("/api/agent-zero/work-items", json={
        "title": "修复登录超时",
        "body": "复现步骤……",
        "priority": 2,
    }).json()["data"]
    assert created["status"] == "inbox"
    assert created["source"] == "manual"

    listing = client.get("/api/agent-zero/work-items").json()["data"]
    assert listing["stats"]["inbox"] == 1
    assert any(x["id"] == created["id"] for x in listing["items"])

    patched = client.patch(
        f"/api/agent-zero/work-items/{created['id']}",
        json={"status": "done", "result_summary": "已修复"},
    ).json()["data"]
    assert patched["status"] == "done"
    assert patched["result_summary"] == "已修复"

    assert client.delete(f"/api/agent-zero/work-items/{created['id']}").status_code == 200
    assert client.get(f"/api/agent-zero/work-items/{created['id']}").status_code == 404


def test_policy_blocks_unauthorized_source(client):
    policy = client.put("/api/agent-zero/work-items/settings", json={
        "mode": "observe",
        "allowed_sources": ["manual"],
        "allowed_actors": [],
    }).json()["data"]
    assert policy["mode"] == "observe"

    item = client.post("/api/agent-zero/work-items", json={
        "title": "来自外部的 issue",
        "source": "agent",
        "source_actor": "stranger",
    }).json()["data"]
    assert item["status"] == "needs_attention"
    assert "未授权" in (item.get("attention_reason") or "")


def test_dispatch_creates_isolated_conversation(client, monkeypatch):
    item = client.post("/api/agent-zero/work-items", json={
        "title": "整理发布说明",
        "body": "把最近的 commit 整理成 release notes",
    }).json()["data"]

    async def noop_request(run_id):
        return None

    monkeypatch.setattr(work_service.conv_worker, "request_run", noop_request)
    monkeypatch.setattr(work_service.conversation, "create_conversation", lambda *a, **k: {"id": "conv-work-test"})
    monkeypatch.setattr(work_service.conversation, "send_message", lambda *a, **k: {"id": "msg-work-test"})
    monkeypatch.setattr(work_service.run_service, "create_run", lambda *a, **k: {"id": "run-work-test"})
    monkeypatch.setattr(
        work_service.run_service, "get_run",
        lambda run_id: {"id": run_id, "status": "running"},
    )

    dispatched = asyncio.run(work_service.dispatch_item(item["id"]))
    assert dispatched["status"] == "working"
    assert dispatched["conversation_id"] == "conv-work-test"
    assert dispatched["run_id"] == "run-work-test"


def test_done_item_cannot_dispatch(client):
    item = client.post("/api/agent-zero/work-items", json={"title": "已经完成"}).json()["data"]
    client.patch(f"/api/agent-zero/work-items/{item['id']}", json={"status": "done"})
    res = client.post(f"/api/agent-zero/work-items/{item['id']}/dispatch")
    assert res.status_code == 400
    assert "已完成" in res.json()["detail"]


def _noop_worker(monkeypatch):
    async def noop(_run_id):
        return None

    monkeypatch.setattr(work_service.conv_worker, "request_run", noop)


def test_dispatch_creates_goal_and_keeps_working_until_achieved(client, monkeypatch):
    """自动继续：派活即建目标，一轮 run 结束不算完成，目标达成才收敛为 done。"""
    from services.agent_zero.services.work_service import WORK_ITEM_MAX_RUNS
    from services.agent_zero.task_layer import goal_service

    item = client.post("/api/agent-zero/work-items", json={
        "title": "整理发布说明",
        "body": "把最近的 commit 整理成 release notes",
    }).json()["data"]

    _noop_worker(monkeypatch)
    monkeypatch.setattr(work_service.run_service, "create_run",
                        lambda *a, **k: {"id": "run-goal"})
    monkeypatch.setattr(work_service.run_service, "get_run",
                        lambda rid: {"id": rid, "status": "done"})

    dispatched = asyncio.run(work_service.dispatch_item(item["id"]))
    conv_id = dispatched["conversation_id"]
    goal = goal_service.get_goal(conv_id)
    assert goal and goal["status"] == "active"
    assert int(goal["max_runs"]) == WORK_ITEM_MAX_RUNS
    assert "整理发布说明" in goal["objective"]

    # 这一轮跑完但目标没达成：工单保持进行中，等系统自动续跑下一轮。
    assert work_service.get_item(item["id"])["status"] == "working"

    goal_service.report_progress(conv_id, "achieved", "发布说明已整理完成")
    assert work_service.get_item(item["id"])["status"] == "done"


def test_goal_failure_enqueues_retry(client, monkeypatch):
    """失败也自动继续：目标 active 时失败 run 会入队一条诊断 / 重试 run，工单保持进行中。"""
    from services.agent_zero.task_layer import goal_service

    item = client.post("/api/agent-zero/work-items",
                       json={"title": "修构建", "body": "CI 挂了"}).json()["data"]

    _noop_worker(monkeypatch)
    monkeypatch.setattr(work_service.run_service, "create_run",
                        lambda *a, **k: {"id": "run-fail"})
    monkeypatch.setattr(work_service.run_service, "get_run",
                        lambda rid: {"id": rid, "status": "failed", "error": "boom"})

    dispatched = asyncio.run(work_service.dispatch_item(item["id"]))
    conv_id = dispatched["conversation_id"]

    nxt = goal_service.after_run_failed(conv_id, "run-fail", "boom")
    assert nxt and nxt.get("id")
    goal = goal_service.get_goal(conv_id)
    assert goal["status"] == "active"
    assert int(goal["runs_used"]) == 1
    # 等重试，不是停在「需要你」。
    assert work_service.get_item(item["id"])["status"] == "working"

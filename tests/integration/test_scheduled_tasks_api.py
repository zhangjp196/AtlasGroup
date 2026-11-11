"""定时任务 / 自由触发任务 API。"""

from __future__ import annotations

import time

from services.agent_zero.services import schedule_service


API = "/api/agent-zero"


def _create(client, **overrides):
    body = {
        "title": "每日巡检",
        "prompt": "检查今天新增的错误日志并总结",
        "trigger_mode": "scheduled",
        "schedule_kind": "daily",
        "schedule": {"time": "09:00"},
        **overrides,
    }
    out = client.post(f"{API}/schedules", json=body)
    assert out.status_code == 200, out.text
    return out.json()["data"]


def test_schedule_crud_and_next_run(client):
    task = _create(client)
    assert task["id"].startswith("sch")
    assert task["trigger_mode"] == "scheduled"
    assert task["status"] == "active"
    assert task["next_run_at"]

    listed = client.get(f"{API}/schedules").json()["data"]
    assert any(item["id"] == task["id"] for item in listed["items"])
    assert listed["stats"]["active"] >= 1

    paused = client.post(f"{API}/schedules/{task['id']}/pause",
                         json={"paused": True}).json()["data"]
    assert paused["status"] == "paused"
    assert paused["next_run_at"] is None

    resumed = client.post(f"{API}/schedules/{task['id']}/pause",
                          json={"paused": False}).json()["data"]
    assert resumed["status"] == "active"
    assert resumed["next_run_at"]

    updated = client.patch(f"{API}/schedules/{task['id']}",
                           json={"title": "每日巡检（改）"}).json()["data"]
    assert updated["title"] == "每日巡检（改）"

    deleted = client.delete(f"{API}/schedules/{task['id']}")
    assert deleted.status_code == 200
    assert client.get(f"{API}/schedules/{task['id']}").status_code == 404


def test_smart_task_is_free_trigger(client):
    task = _create(
        client,
        title="观察线上错误",
        prompt="排查线上错误并给结论",
        trigger_mode="smart",
        schedule_kind="smart",
        schedule={"every_minutes": 15},
        condition="出现新的错误堆栈时才触发",
    )
    assert task["trigger_mode"] == "smart"
    assert task["schedule_kind"] == "smart"
    assert task["schedule"]["every_minutes"] == 15
    assert task["next_run_at"]


def test_schedule_validation(client):
    bad = client.post(f"{API}/schedules", json={
        "title": "坏任务", "prompt": "x", "schedule_kind": "daily",
        "schedule": {"time": "99:99"},
    })
    assert bad.status_code == 400
    bad = client.post(f"{API}/schedules", json={
        "title": "", "prompt": "x",
    })
    assert bad.status_code == 422


def test_compute_next_for_all_kinds(app_env):
    from services.agent_zero import database
    from services.agent_zero.stores import zero_store

    database.init_database()
    zero_store.ensure_defaults()
    now = schedule_service._now_dt()

    once = {"trigger_mode": "scheduled", "schedule_kind": "once",
            "schedule": {"at": "2030-01-02 03:04"}}
    assert schedule_service.compute_next(once, now).startswith("2030-01-02 03:04")

    interval = {"trigger_mode": "scheduled", "schedule_kind": "interval",
                "schedule": {"every_minutes": 10}}
    got = schedule_service.compute_next(interval, now)
    assert got is not None

    weekly = {"trigger_mode": "scheduled", "schedule_kind": "weekly",
              "schedule": {"weekday": 0, "time": "09:00"}}
    got = schedule_service.compute_next(weekly, now)
    assert got is not None

    manual = {"trigger_mode": "manual", "schedule_kind": "daily",
              "schedule": {"time": "09:00"}}
    assert schedule_service.compute_next(manual, now) is None


def test_global_agent_has_schedule_tool():
    from services.agent_zero.tools import packs, registry

    assert "schedule_manage" in packs.GLOBAL_ONLY_TOOLS
    tool = registry.get_tool("schedule_manage")
    assert tool is not None
    assert "schedule" in (tool.parameters.get("properties") or {})


def test_tick_dispatches_due_scheduled_task(client, monkeypatch):
    import asyncio

    task = _create(client, schedule_kind="interval", schedule={"every_minutes": 5})
    calls = []

    async def fake_run(task_id, trigger="manual"):
        calls.append((task_id, trigger))
        return schedule_service.get_task(task_id)

    monkeypatch.setattr(schedule_service, "run_task", fake_run)
    schedule_service._patch(task["id"], next_run_at="2000-01-01 00:00:00")
    handled = asyncio.run(schedule_service.tick())
    assert handled >= 1
    assert (task["id"], "scheduled") in calls


def test_tick_uses_model_decision_for_smart_task(client, monkeypatch):
    import asyncio

    task = _create(
        client,
        trigger_mode="smart",
        schedule_kind="smart",
        schedule={"every_minutes": 5},
        condition="有新事件才执行",
    )
    calls = []

    async def fake_eval(item):
        return True, "有新事件"

    async def fake_run(task_id, trigger="manual"):
        calls.append((task_id, trigger))
        return schedule_service.get_task(task_id)

    monkeypatch.setattr(schedule_service, "evaluate", fake_eval)
    monkeypatch.setattr(schedule_service, "run_task", fake_run)
    schedule_service._patch(task["id"], next_run_at="2000-01-01 00:00:00")
    asyncio.run(schedule_service.tick())
    assert (task["id"], "smart") in calls


def test_smart_evaluation_parses_json(app_env, monkeypatch):
    from services.agent_zero.runtime.zero import llm as llm_mod

    class _Result:
        content = '{"trigger": true, "reason": "有新错误"}'

    class _Llm:
        def __init__(self, models):  # noqa: ANN001
            pass

        async def chat_once(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _Result()

    monkeypatch.setattr(llm_mod, "ZeroLlm", _Llm)
    trigger, reason = __import__("asyncio").run(
        schedule_service.evaluate({"id": "sch_x", "title": "观察", "prompt": "看日志"}))
    assert trigger is True
    assert "新错误" in reason


def test_proactive_message_is_silent_and_managed_by_policy(client):
    from services.agent_zero.task_layer import conversation

    role = client.post(f"{API}/roles", json={"name": "主动测试角色"}).json()["data"]
    conv = client.post(f"{API}/conversations", json={"role_id": role["id"]}).json()["data"]
    conv_row = conversation.get_conversation(conv["id"])
    role_row = __import__("services.agent_zero.stores.zero_store",
                          fromlist=["zero_store"]).get_role(role["id"])

    out = conversation._exec_proactive_send(conv_row, role_row, {
        "message": "后台任务已完成，向用户汇报结果",
        "delay_minutes": 3,
        "reason": "任务完成",
    })
    assert out.get("ok") is True
    task = schedule_service.get_task(out["task_id"])
    assert task is not None
    assert (task["meta"] or {}).get("kind") == "proactive"
    assert (task["meta"] or {}).get("silent") is True
    assert task["conversation_id"] == conv["id"]
    assert task["next_run_at"]

    # 全局 Agent 可以关闭主动消息；关闭后普通角色不能再安排。
    managed = conversation._exec_app_manage(conv_row, {
        "action": "set_proactive", "enabled": False,
    })
    assert managed["ok"] is True
    closed = conversation._exec_proactive_send(conv_row, role_row, {"message": "再来一条"})
    assert "关闭" in closed.get("error", "")
    status = conversation._exec_app_manage(conv_row, {"action": "proactive_status"})
    assert status["policy"]["enabled"] is False

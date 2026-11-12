"""`/clean`：彻底清空会话（上下文 + 历史 + 运行痕迹），会话本身保留。"""
from __future__ import annotations

from services.agent_zero.services import team_service, todo_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation, goal_service, run_service


def _env(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_WORKSPACE_ID


def test_clean_removes_history_runs_todos_and_goals(app_env, monkeypatch):
    _env(app_env)
    ws = "ws-clean"
    zero_store.create_workspace("group", ws, "清理测试", "local")
    conv = conversation.create_conversation(ws, "local")
    conversation.send_message(conv["id"], "你好", "local")
    run = run_service.create_run(conv["id"], "local", content="你好", mode="chat")
    todo_service.add_todo(conv["id"], "整理索引")
    goal_service.set_goal(conv["id"], "完成迁移")

    assert conversation.list_messages(conv["id"])
    assert run_service.list_runs(conv["id"])

    out = conversation.clean_conversation(conv["id"])
    assert out["nodes"] >= 1
    assert conversation.list_messages(conv["id"]) == []
    assert run_service.list_runs(conv["id"]) == []
    assert todo_service.list_todos(conv["id"]) == []
    assert goal_service.active_goal(conv["id"]) is None

    # 会话本身还在（标题 / 群组 / 工作区保留）
    still = conversation.get_conversation(conv["id"])
    assert still and still["id"] == conv["id"]
    assert still["workspace_id"] == ws
    assert run_service.get_run(run["id"]) is None


def test_clean_removes_team_tasks(app_env, monkeypatch):
    from services.agent_zero.task_layer import conv_worker

    ws = _env(app_env)
    team = team_service.create_team(
        ws, "研发群", leader={"name": "主持"},
        members=[{"name": "后端"}])
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    leader = team["leader"]
    member = team["members"][0]

    async def noop(_run_id):  # pragma: no cover - 只是别真跑模型
        return None

    monkeypatch.setattr(conv_worker, "request_run", noop)
    from services.agent_zero.services import team_task_service

    task = team_task_service.create_task(
        conv, team, leader,
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "补索引", "role": member}],
        "local")
    assert team_task_service.get_task(task["id"])

    conversation.clean_conversation(conv["id"])
    assert team_task_service.get_task(task["id"]) is None
    assert conversation.get_conversation(conv["id"]) is not None


def test_clean_rejects_unknown_conversation(app_env):
    _env(app_env)
    try:
        conversation.clean_conversation("conv-not-exist")
    except ValueError as e:
        assert "对话不存在" in str(e)
    else:  # pragma: no cover
        raise AssertionError("不存在的会话应报错")

"""`_stream_role` 拆分回归：本轮准备逻辑独立可测。

P4 的第一阶段：把「模型 / 团队 / 目标 / 提示词 / 历史 / 工具清单」的组装从
`_stream_role` 抽成 `_prepare_role_run`，循环仍留在原函数里。这里锁定拆分后的
行为：系统提示包含角色身份与团队名单、工具清单非空、隔离模式只用显式上下文。
"""
from __future__ import annotations

import asyncio

from services.agent_zero.services import team_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation


def _bootstrap(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_ROLE_ID, zero_store.DEFAULT_WORKSPACE_ID


def test_prepare_role_run_builds_prompt_team_and_tools(app_env):
    role_id, ws = _bootstrap(app_env)
    team = team_service.create_team(ws, "准备群", mode="roundtable",
                                    leader={"name": "主持"},
                                    members=[{"name": "后端", "expertise": "API"}])
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    role = zero_store.get_role(role_id)

    setup = asyncio.run(conversation._prepare_role_run(conv, role, "local"))

    assert setup["messages"][0]["role"] == "system"
    system = setup["messages"][0]["content"]
    assert "你是「" in system
    assert "准备群" in system and "后端" in system      # 团队名单进提示
    assert setup["models"] and setup["models"].get("loop")
    assert setup["specs"], "工具清单不能为空"
    assert setup["team"]["id"] == team["id"]
    assert setup["ws_id"] == ws
    assert setup["user_query"] == ""


def test_prepare_role_run_isolated_uses_only_given_context(app_env):
    role_id, ws = _bootstrap(app_env)
    conv = conversation.create_conversation(ws, "local")
    role = zero_store.get_role(role_id)

    setup = asyncio.run(conversation._prepare_role_run(
        conv, role, "local", isolated=True,
        context_messages=[{"role": "user", "content": "只做这个子任务"}],
        system_addon="子代理约束",
    ))
    users = [m for m in setup["messages"] if m["role"] == "user"]
    assert [m["content"] for m in users] == ["只做这个子任务"]
    assert setup["team"] is None                         # 隔离运行不继承团队
    assert "子代理约束" in setup["messages"][0]["content"]


def test_await_tool_approval_writes_decision_and_event(app_env, monkeypatch):
    """审批等待拆成 helper 后：决定写回、decided 事件照常发出。"""
    from services.agent_zero.task_layer import permissions

    async def fake_await(appr_id, run_id):
        return {"status": "approved", "scope": "once"}

    monkeypatch.setattr(permissions, "await_decision", fake_await)
    monkeypatch.setattr(permissions, "forget", lambda _id: None)

    out: dict = {}

    async def collect():
        return [ev async for ev in conversation._await_tool_approval(
            "appr-1", "run-1", "shell", "rid", "R", out)]

    events = asyncio.run(collect())
    assert out == {"status": "approved", "scope": "once"}
    decided = next(e for e in events if e.get("approval_decided"))
    assert decided["approval_decided"]["status"] == "approved"
    assert decided["approval_decided"]["id"] == "appr-1"

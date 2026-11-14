"""子代理上下文隔离：只看到显式给出的任务上下文，不继承父对话历史。"""
from __future__ import annotations

import asyncio
import json

from services.agent_zero.task_layer import conversation
from services.agent_zero.task_layer import prompt_builder
from services.agent_zero.task_layer import run_service
from services.agent_zero.stores import zero_store


def test_subagent_messages_only_include_explicit_context():
    messages = conversation._subagent_messages(
        "修复登录超时", instructions="先跑相关测试", context="报错：read timeout")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    text = messages[0]["content"]
    assert "<subagent-task>" in text
    assert "修复登录超时" in text
    assert "先跑相关测试" in text
    assert "read timeout" in text
    assert "看不到父对话历史" in text


def test_subagent_prompt_marks_isolation():
    assert "子代理模式" in prompt_builder.SUBAGENT
    assert "看不到父对话历史" in prompt_builder.SUBAGENT


def test_exec_subagent_runs_isolated(app_env, monkeypatch):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    conv = conversation.create_conversation(zero_store.DEFAULT_WORKSPACE_ID, "local")
    parent = run_service.create_run(conv["id"], "local", content="父任务")
    assert run_service.claim_run(parent["id"]) is True
    captured = {}

    async def fake_stream(conv, user, role, sink, **kwargs):
        captured.update(kwargs)
        sink["text"] = "子代理结论"
        if False:      # pragma: no cover - 让它成为异步生成器
            yield {}

    monkeypatch.setattr(conversation, "_stream_role", fake_stream)
    role = {"id": "r1", "name": "Atlas",
            "envelope": {"tools": ["read_file"]}}
    out = asyncio.run(conversation._exec_call_subagent(
        conv, "local", role,
        {"goal": "检查配置", "instructions": "只看只读文件",
         "context": "配置文件在 config.yaml"}, 0,
        parent_run_id=parent["id"]))
    payload = json.loads(out)
    assert payload["output"] == "子代理结论"
    assert captured["isolated"] is True
    assert captured["system_addon"] == prompt_builder.SUBAGENT
    text = captured["context_messages"][0]["content"]
    assert "检查配置" in text
    assert "只看只读文件" in text
    assert "配置文件在 config.yaml" in text


def test_call_agent_uses_and_releases_shared_capacity(app_env, monkeypatch):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    conv = conversation.create_conversation(zero_store.DEFAULT_WORKSPACE_ID, "local")
    parent = run_service.create_run(conv["id"], "local", content="父任务")
    assert run_service.claim_run(parent["id"]) is True
    captured = {}

    async def fake_stream(_conv, _user, _role, sink, **kwargs):
        captured.update(kwargs)
        sink["text"] = "角色结论"
        if False:  # pragma: no cover
            yield {}

    monkeypatch.setattr(conversation, "_stream_role", fake_stream)
    out = asyncio.run(conversation._exec_call_agent(
        conv, "local", {"id": "r1", "name": "父角色", "envelope": {}},
        {"goal": "检查问题"}, 0, parent_run_id=parent["id"]))
    assert json.loads(out)["output"] == "角色结论"
    assert captured["run_id"] == parent["id"]
    # 子代理结束后槽位已经归还。
    slot = run_service.reserve_subagent_slot(parent["id"])
    assert slot
    assert run_service.release_subagent_slot(slot)


def test_call_agent_waits_when_shared_capacity_is_full(app_env, monkeypatch):
    from types import SimpleNamespace

    from services.agent_zero import config, database

    database.init_database()
    zero_store.ensure_defaults()
    conv = conversation.create_conversation(zero_store.DEFAULT_WORKSPACE_ID, "local")
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        max_concurrent_conversation_runs=1,
        max_parallel_member_runs=4,
    ))
    parent = run_service.create_run(conv["id"], "local", content="父任务")
    assert run_service.claim_run(parent["id"]) is True
    called = False

    async def fake_stream(*_args, **_kwargs):
        nonlocal called
        called = True
        yield {}

    monkeypatch.setattr(conversation, "_stream_role", fake_stream)
    out = asyncio.run(conversation._exec_call_agent(
        conv, "local", {"id": "r1", "name": "父角色", "envelope": {}},
        {"goal": "检查问题"}, 0, parent_run_id=parent["id"]))
    assert "全局并发额度已满" in json.loads(out)["error"]
    assert called is False


def test_exec_team_member_dispatches_background_run(monkeypatch):
    """call_team 不再同步跑成员：主持 @ 成员，成员进入独立后台 run。"""
    captured = {}

    def fake_create_run(conv_id, user, **kwargs):
        captured.update(kwargs)
        return {"id": "run-member-1"}

    async def fake_request_run(_run_id):
        return None

    monkeypatch.setattr(conversation, "append_mention_message",
                        lambda conv_id, role, member, goal: {"id": "msg-mention-1"})
    monkeypatch.setattr("services.agent_zero.task_layer.run_service.create_run",
                        fake_create_run)
    monkeypatch.setattr("services.agent_zero.task_layer.conv_worker.request_run",
                        fake_request_run)
    team = {"leader": {"id": "r1"}, "members": [{"id": "m1", "name": "Reviewer"}]}
    member = {"id": "m1", "name": "Reviewer", "group_id": "ws1", "envelope": {}}

    monkeypatch.setattr("services.agent_zero.services.team_service.team_for_conv",
                        lambda _conv: team)
    monkeypatch.setattr(conversation.zero_store, "get_role",
                        lambda rid: member if rid == "m1" else None)

    out = asyncio.run(conversation._exec_call_team(
        {"id": "conv1", "workspace_id": "ws1"}, "local", {"id": "r1"},
        {"member_id": "m1", "goal": "审查改动", "context": "只看 diff"}, 0))
    payload = json.loads(out)
    assert payload["status"] == "dispatched"
    assert payload["run_id"] == "run-member-1"
    assert captured["target_role_id"] == "m1"
    assert captured["queued_msg_id"] == "msg-mention-1"
    assert "审查改动" in captured["content"]
    assert "只看 diff" in captured["content"]

"""角色子代理支持：一个角色并行多项任务，单个任务内再派子代理。

覆盖三件事：
1. 单角色会话用 `background_task.tasks` 一次创建多条任务，run 并行执行；
2. 群组会话的主持也能派后台任务；任务成员可以派子代理，但不能嵌套派团队任务；
3. `delegate(target=subagent, assignments=...)` 并行派生多个子代理。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from services.agent_zero.services import team_service, team_task_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation, run_service


def _env(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_WORKSPACE_ID


def _quiet_dispatch(monkeypatch):
    from services.agent_zero.task_layer import conv_worker

    async def noop(_run_id: str) -> None:
        return None

    monkeypatch.setattr(conv_worker, "request_run", noop)


def _fake_llm(monkeypatch, tool_calls: List[Dict[str, Any]],
              final_text: str = "已并行派发"):
    """让模型第一轮发起指定工具，第二轮收尾。"""
    rounds = {"n": 0}

    async def fake_stream(self, role, messages, **kw):
        rounds["n"] += 1
        if rounds["n"] == 1:
            yield {"type": "done", "content": "", "usage": {},
                   "finish_reason": "tool_calls", "tool_calls": tool_calls}
            return
        yield {"type": "delta", "content": final_text}
        yield {"type": "done", "content": "", "tool_calls": [],
               "usage": {}, "finish_reason": "stop"}

    from services.agent_zero.runtime.zero import llm as llm_mod

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_stream)
    monkeypatch.setattr(conversation, "_capture_run_memory", lambda *a, **k: None)


def _role(role_id: str | None = None) -> Dict[str, Any]:
    return zero_store.get_role(role_id or zero_store.DEFAULT_ROLE_ID) or {
        "id": role_id or "role", "name": "角色", "envelope": {},
    }


def test_background_task_tasks_array_creates_parallel_runs(app_env, monkeypatch):
    ws = _env(app_env)
    _quiet_dispatch(monkeypatch)
    conv = conversation.create_conversation(ws, "local")
    role = _role()
    _fake_llm(monkeypatch, [{
        "id": "call-bg",
        "name": "background_task",
        "arguments": {"tasks": [
            {"goal": "并行任务 A", "title": "A"},
            {"goal": "并行任务 B", "title": "B"},
        ]},
    }])

    events: List[Dict[str, Any]] = []

    async def run():
        async for ev in conversation._stream_role(
                conv, "把 A / B 一起放到后台", role, {}, autonomous=False,
                auto_execute=True):
            events.append(ev)

    asyncio.run(run())

    result = next(ev["tool_result"] for ev in events
                  if isinstance(ev.get("tool_result"), dict))
    assert result["ok"] is True
    # tool_result 的 summary 只给摘要；直接看任务表更可靠。
    tasks = team_task_service.list_tasks(conv["id"])
    assert len(tasks) == 2, tasks
    assert {t["title"] for t in tasks} == {"A", "B"}
    assert all(t["total_members"] == 1 for t in tasks)
    assert all(t["leader_role_id"] == role["id"] for t in tasks)
    assert all(t["members"][0]["role_id"] == role["id"] for t in tasks)

    # 同一角色可以同时认领多条任务（互不依赖，不串行等待）。
    first, second = tasks
    assert run_service.claim_run(first["members"][0]["run_id"]) is True
    assert run_service.claim_run(second["members"][0]["run_id"]) is True


def test_group_same_member_can_take_multiple_parallel_assignments(app_env, monkeypatch):
    """群组里同一个角色可以同时接多件互不依赖的活（多条 assignment）。"""
    ws = _env(app_env)
    _quiet_dispatch(monkeypatch)
    team = team_service.create_team(
        ws, "多任务群", leader={"name": "主持"},
        members=[{"name": "执行者"}],
    )
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    member = team["members"][0]
    out = json.loads(asyncio.run(conversation._exec_call_team(
        conv, "local", _role(team["leader"]["id"]),
        {"assignments": [
            {"member_id": member["id"], "goal": "任务 A"},
            {"member_id": member["id"], "goal": "任务 B"},
        ]}, 0)))
    assert out["status"] == "dispatched"
    assert out["count"] == 2
    task = team_task_service.get_task(out["task_id"])
    assert task["total_members"] == 2
    assert {m["role_id"] for m in task["members"]} == {member["id"]}
    # 两条 run 属于同一角色，但可以并行认领。
    run_ids = [m["run_id"] for m in task["members"]]
    assert len(set(run_ids)) == 2
    assert all(run_service.claim_run(rid) is True for rid in run_ids)


def test_task_member_can_dispatch_subagent_but_not_team(app_env, monkeypatch):
    ws = _env(app_env)
    _quiet_dispatch(monkeypatch)
    team = team_service.create_team(
        ws, "任务群", leader={"name": "主持"},
        members=[{"name": "执行者", "expertise": "实现"}],
    )
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    member = team["members"][0]
    role = _role(member["id"])
    run = run_service.create_run(
        conv["id"], "local", role_id=member["id"], target_role_id=member["id"],
        team_task_id="task-test", content="执行子任务")

    captured: Dict[str, Any] = {}

    async def fake_stream(self, role_arg, messages, **kw):
        captured["tools"] = [(s.get("function") or {}).get("name")
                             for s in (kw.get("tools") or [])]
        captured["system"] = messages[0]["content"]
        yield {"type": "done", "content": "", "tool_calls": [],
               "usage": {}, "finish_reason": "stop"}

    from services.agent_zero.runtime.zero import llm as llm_mod

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_stream)
    monkeypatch.setattr(conversation, "_capture_run_memory", lambda *a, **k: None)

    async def run_stream():
        async for _ev in conversation._stream_role(
                conv, "执行", role, {}, autonomous=True,
                auto_execute=True, run_id=run["id"]):
            pass

    asyncio.run(run_stream())

    # 不能嵌套派后台任务 / 团队任务；子代理（plan.delegate）仍然可用。
    assert "background_task" not in captured["tools"]
    assert "plan" in captured["tools"]
    assert "后台任务成员模式" in captured["system"]
    assert "不要再创建新的后台任务" in captured["system"]


def test_group_leader_exposes_background_task_tool(app_env, monkeypatch):
    ws = _env(app_env)
    _quiet_dispatch(monkeypatch)
    team = team_service.create_team(
        ws, "主持群", leader={"name": "主持"},
        members=[{"name": "成员"}],
    )
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    leader = _role(team["leader"]["id"])

    captured: Dict[str, Any] = {}

    async def fake_stream(self, role_arg, messages, **kw):
        captured["tools"] = [(s.get("function") or {}).get("name")
                             for s in (kw.get("tools") or [])]
        yield {"type": "done", "content": "", "tool_calls": [],
               "usage": {}, "finish_reason": "stop"}

    from services.agent_zero.runtime.zero import llm as llm_mod

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_stream)
    monkeypatch.setattr(conversation, "_capture_run_memory", lambda *a, **k: None)

    async def run_stream():
        async for _ev in conversation._stream_role(
                conv, "拆任务", leader, {}, autonomous=True,
                auto_execute=True):
            pass

    asyncio.run(run_stream())
    assert "background_task" in captured["tools"]
    assert "plan" in captured["tools"]


def test_delegate_subagent_assignments_resolve_to_call_subagent():
    from services.agent_zero.tools import packs

    code, args, error = packs.resolve_call("delegate", {
        "target": "subagent",
        "assignments": [{"goal": "调研 A", "name": "调研员"}],
    })
    assert error is None
    assert code == "call_subagent"
    assert args["assignments"][0]["goal"] == "调研 A"

    # 没有 target 的 assignments 仍按旧的团队成员语义走。
    code, _args, error = packs.resolve_call("delegate", {
        "assignments": [{"member_name": "后端", "goal": "改 API"}],
    })
    assert error is None
    assert code == "call_team"


def test_exec_subagent_assignments_run_in_parallel(monkeypatch):
    active = 0
    peak = 0

    async def fake_stream(conv, user, role, sink, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.05)
            sink["text"] = f"完成 {role.get('name')}"
            yield {"done": True, "text": sink["text"]}
        finally:
            active -= 1

    monkeypatch.setattr(conversation, "_stream_role", fake_stream)
    role = {"id": "r1", "name": "父角色",
            "envelope": {"tools": ["read_file"]}}
    raw = asyncio.run(conversation._exec_call_subagent(
        {"id": "conv1", "workspace_id": "ws1"}, "local", role,
        {"assignments": [
            {"goal": "检查 A", "name": "子代理 A"},
            {"goal": "检查 B", "name": "子代理 B"},
        ]}, 0))
    payload = json.loads(raw)
    assert peak >= 2, f"子代理没有并行执行（peak={peak}）"
    assert len(payload["outputs"]) == 2
    assert payload["display"]["type"] == "subagent"
    assert len(payload["display"]["agents"]) == 2
    assert "完成 子代理 A" in payload["output"]
    assert "完成 子代理 B" in payload["output"]


def test_task_subagent_nodes_hang_under_member(app_env, monkeypatch):
    from services.agent_zero.services import graph_service

    ws = _env(app_env)
    _quiet_dispatch(monkeypatch)
    team = team_service.create_team(
        ws, "画布群", leader={"name": "主持"},
        members=[{"name": "执行者"}],
    )
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    member = team["members"][0]
    task = team_task_service.create_task(
        conv, team, team["leader"],
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "实现功能", "role": member}], "local")
    run_id = str(task["members"][0]["run_id"])
    run_service.append_event(run_id, "subagent_event", {
        "subagent_event": {
            "call_id": "call-sub-1", "agent": "子代理 A",
            "goal": "查资料", "event": "done",
        },
    })

    graph = graph_service.conversation_graph(conv["id"])
    sub = next(n for n in graph["nodes"] if n.get("kind") == "subagent")
    member_node = next(n for n in graph["nodes"] if n.get("kind") == "member")
    assert sub["parent_id"] == member_node["id"]
    assert sub["parent_run_id"] == run_id

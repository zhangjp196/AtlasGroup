"""对话与后台任务分离：前台只留确认 / 完成触发器，任务过程不进对话。

用户方向：角色与群组、对话与任务分开。
- 派发任务：成员在前台回复一句「收到」，任务在后台跑；
- 任务过程不写进对话转录；
- 任务结束 → 对话里一条完成触发器（用户点「继续对话」再整合）；
- 单角色会话也支持后台任务（`background_task` 工具）。
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


def _quiet(monkeypatch):
    from services.agent_zero.task_layer import conv_worker

    async def noop(_run_id: str) -> None:
        return None

    monkeypatch.setattr(conv_worker, "request_run", noop)


def _team_conv(ws: str):
    team = team_service.create_team(
        ws, "研发群", leader={"name": "主持"},
        members=[{"name": "后端"}])
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    return team, conv


def test_dispatch_writes_ack_and_task_output_stays_out_of_chat(app_env, monkeypatch):
    ws = _env(app_env)
    team, conv = _team_conv(ws)
    leader, member = team["leader"], team["members"][0]
    _quiet(monkeypatch)

    task = team_task_service.create_task(
        conv, team, leader,
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "补齐索引", "role": member}],
        "local")

    msgs = conversation.list_messages(conv["id"])
    acks = [m for m in msgs if m.get("kind") == "task_ack"]
    assert acks and acks[0]["role_id"] == member["id"]
    assert "后台执行" in acks[0]["content"]

    # 成员 run 的产出落 stage='task'：对话转录里看不到，但任务结果能取到。
    from services.agent_zero.runtime.zero.gdag.store import GdagStore

    GdagStore().upsert_node({
        "id": zero_store.new_id("msg"), "type": "fact", "camp": "default",
        "props": {
            "conversation_id": conv["id"], "workspace_id": ws,
            "role_id": member["id"], "role_name": member["name"],
            "content": "索引已补齐，结果见 diff。", "stage": "task",
            "by": "assistant", "run_id": task["members"][0]["run_id"],
        },
    })
    contents = [m["content"] for m in conversation.list_messages(conv["id"])]
    assert all("索引已补齐" not in c for c in contents)
    assert "索引已补齐" in team_task_service.results_text(
        team_task_service._fetch(task["id"]))


def test_task_completion_writes_one_trigger(app_env, monkeypatch):
    ws = _env(app_env)
    team, conv = _team_conv(ws)
    leader, member = team["leader"], team["members"][0]
    _quiet(monkeypatch)

    task = team_task_service.create_task(
        conv, team, leader,
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "补索引", "role": member}], "local")
    run_id = str(task["members"][0]["run_id"])
    run_service.finish_run(run_id, run_service.STATUS_DONE)

    team_task_service.on_run_finished(run_id)
    team_task_service.on_run_finished(run_id)   # 幂等

    triggers = [m for m in conversation.list_messages(conv["id"])
                if m.get("kind") == "task_done"]
    assert len(triggers) == 1
    assert triggers[0]["task_id"] == task["id"]
    assert triggers[0]["task_status"] == "done"

    # 触发器本身不自动唤醒主持；继续对话才创建主会话 run。
    assert run_service.running_run(conv["id"], include_member=False) is None
    out = team_task_service.continue_task(task["id"])
    run = run_service.get_run(out["run"]["id"])
    assert run and not run.get("target_role_id")


def test_task_creates_and_finishes_work_item(app_env, monkeypatch):
    """角色 / 群组的后台任务自动建工单，任务结束收敛状态。"""
    from services.agent_zero.services import work_service

    ws = _env(app_env)
    team, conv = _team_conv(ws)
    leader, member = team["leader"], team["members"][0]
    _quiet(monkeypatch)

    task = team_task_service.create_task(
        conv, team, leader,
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "补索引", "role": member}], "local")

    items = work_service.list_items(conversation_id=conv["id"])["items"]
    item = next((x for x in items
                 if x.get("source_ref") == f"team_task:{task['id']}"), None)
    assert item, items
    assert item["status"] == "working"
    assert item["conversation_id"] == conv["id"]
    assert item["team_id"] == team["id"]

    run_id = str(task["members"][0]["run_id"])
    run_service.finish_run(run_id, run_service.STATUS_DONE)
    team_task_service.on_run_finished(run_id)

    done = work_service.get_item(item["id"])
    assert done["status"] == "done"
    # 自动建单由任务维护：列表同步不会把它按成员 run 覆盖回去。
    listed = work_service.list_items(conversation_id=conv["id"])["items"]
    assert next(x for x in listed if x["id"] == item["id"])["status"] == "done"


def test_single_role_run_exposes_background_task_tool(monkeypatch):
    """没有团队时补 background_task，并用不到 call_team。"""
    captured: List[List[str]] = []

    async def fake_stream(self, role, messages, **kw):
        captured.append([(s.get("function") or {}).get("name")
                         for s in (kw.get("tools") or [])])
        yield {"type": "done", "content": "", "tool_calls": [],
               "usage": {}, "finish_reason": "stop"}

    from services.agent_zero.runtime.zero import llm as llm_mod

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_stream)
    monkeypatch.setattr(conversation, "_capture_run_memory", lambda *a, **k: None)

    conv = {"id": "conv-solo", "model_code": "", "workspace_id": "ws",
            "team_id": "", "role_id": "r1"}
    role = {"id": "r1", "name": "单人", "envelope": {}, "system_prompt": ""}

    async def run():
        async for _ev in conversation._stream_role(
                conv, "跑个长任务", role, {}, autonomous=False,
                auto_execute=True):
            pass

    asyncio.run(run())
    names = captured[0]
    assert "background_task" in names
    assert "call_team" not in names

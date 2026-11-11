"""团队成员并行 run：调度、批量派发与执行画布聚合。"""
from __future__ import annotations

import asyncio
import json

from services.agent_zero.services import graph_service
from services.agent_zero.services import team_service as ts
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation, run_service


def _init(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_WORKSPACE_ID


def test_member_runs_claim_in_parallel_but_coordinator_stays_single(app_env):
    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    coordinator = run_service.create_run(conv["id"], "local", content="主持")
    assert run_service.claim_run(coordinator["id"]) is True

    member_a = run_service.create_run(
        conv["id"], "local", role_id="role-a", target_role_id="role-a",
        content="A 的任务")
    member_b = run_service.create_run(
        conv["id"], "local", role_id="role-b", target_role_id="role-b",
        content="B 的任务")
    assert run_service.claim_run(member_a["id"]) is True
    assert run_service.claim_run(member_b["id"]) is True

    # 同一对话只能有一条主持 run
    second_coordinator = run_service.create_run(conv["id"], "local", content="第二轮")
    assert run_service.claim_run(second_coordinator["id"]) is False

    # 成员 run 默认不出现在用户可见的排队条里
    queued_member = run_service.create_run(
        conv["id"], "local", role_id="role-c", target_role_id="role-c",
        content="C 的任务")
    queue = run_service.conversation_queue(conv["id"])
    assert queued_member["id"] not in [item["id"] for item in queue]
    assert queued_member["id"] in [
        item["id"] for item in run_service.conversation_queue(conv["id"], include_member=True)
    ]


def test_call_team_batch_dispatches_all_assignments(app_env):
    ws = _init(app_env)
    team = ts.create_team(
        ws, "并行群", leader={"name": "主持"},
        members=[{"name": "后端", "expertise": "API"},
                 {"name": "前端", "expertise": "UI"}],
    )
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    backend, frontend = team["members"][0], team["members"][1]
    out = json.loads(asyncio.run(conversation._exec_call_team(
        conv, "local", zero_store.get_role(team["leader"]["id"]),
        {"assignments": [
            {"member_id": backend["id"], "goal": "整理 API"},
            {"member_name": frontend["name"], "goal": "整理页面"},
        ]}, 0)))
    assert out["status"] == "dispatched"
    assert out["count"] == 2
    assert {item["member"]["id"] for item in out["runs"]} == {backend["id"], frontend["id"]}
    for item in out["runs"]:
        run = run_service.get_run(item["run_id"])
        assert run["target_role_id"] == item["member"]["id"]
        assert run["status"] in ("queued", "running")

    mentions = [m for m in conversation.list_messages(conv["id"])
                if m.get("kind") == "team_mention"]
    assert len(mentions) == 2


def test_call_team_respects_participant_limit(app_env):
    """单轮参与上限：不会因为模型一次列了全员就把所有人派上。"""
    ws = _init(app_env)
    team = ts.create_team(
        ws, "限流群", max_speakers=2, leader={"name": "主持"},
        members=[{"name": "后端", "expertise": "API"},
                 {"name": "前端", "expertise": "UI"},
                 {"name": "测试", "expertise": "QA"}],
    )
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    members = team["members"]
    assignments = [
        {"member_id": m["id"], "goal": f"{m['name']}任务"} for m in members
    ]
    out = json.loads(asyncio.run(conversation._exec_call_team(
        conv, "local", zero_store.get_role(team["leader"]["id"]),
        {"assignments": assignments}, 0)))
    assert out["status"] == "dispatched"
    assert out["count"] == 2
    assert len(out["skipped"]) == 1
    assert any("本轮参与上限为 2 人" in str(e) for e in out["errors"])


def test_team_task_pause_resume_stop(app_env):
    from services.agent_zero.services import team_task_service

    ws = _init(app_env)
    team = ts.create_team(
        ws, "任务群", leader={"name": "主持"},
        members=[{"name": "后端", "expertise": "API"},
                 {"name": "前端", "expertise": "UI"}],
    )
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    leader = zero_store.get_role(team["leader"]["id"])
    assignments = [
        {"role_id": m["id"], "role_name": m["name"], "goal": f"{m['name']}任务",
         "role": zero_store.get_role(m["id"])}
        for m in team["members"]
    ]
    task = team_task_service.create_task(conv, team, leader, assignments, "local")
    assert task["status"] == "running"
    assert task["total_members"] == 2
    assert task["active_runs"] == 2

    paused = team_task_service.pause_task(task["id"])
    assert paused["status"] == "paused"
    assert paused["can_resume"] is True
    assert paused["active_runs"] == 0

    resumed = team_task_service.resume_task(task["id"])
    assert resumed["status"] == "running"
    assert resumed["active_runs"] == 2

    stopped = team_task_service.stop_task(task["id"])
    assert stopped["status"] == "stopped"
    assert stopped["can_stop"] is False
    assert [t["id"] for t in team_task_service.list_tasks(conv["id"])] == [task["id"]]


def test_worker_executes_member_runs_concurrently(app_env, monkeypatch):
    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    coordinator = run_service.create_run(conv["id"], "local", content="主持")
    members = [
        run_service.create_run(
            conv["id"], "local", role_id=f"role-{i}", target_role_id=f"role-{i}",
            content=f"任务 {i}")
        for i in range(3)
    ]

    active = 0
    peak = 0

    async def fake_stream(conv_id, user, content="", mentions=None,
                          target_role_id=None, autonomous=False,
                          auto_execute=False, run_id=None):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.35)
            yield {"start": True, "role_id": target_role_id or "default",
                   "role_name": "成员" if target_role_id else "主持"}
            yield {"done": True, "text": "完成", "usage": {}}
        finally:
            active -= 1

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    from services.agent_zero.task_layer.conv_worker import ConversationRunWorker

    async def scenario():
        worker = ConversationRunWorker()
        worker._started = True
        worker._loop = asyncio.get_running_loop()
        cycle = asyncio.create_task(worker._run_forever())
        try:
            await asyncio.sleep(1.4)
        finally:
            cycle.cancel()
            for task in list(worker._live.values()):
                task.cancel()
            await asyncio.gather(cycle, *list(worker._live.values()),
                                 return_exceptions=True)

    asyncio.run(scenario())
    assert peak >= 2, f"成员 run 没有并行执行（peak={peak}）"
    assert run_service.get_run(coordinator["id"])["status"] in ("done", "running")
    assert all(run_service.get_run(run["id"]) for run in members)


def test_worker_starts_member_runs_in_same_cycle(app_env, monkeypatch):
    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    coordinator = run_service.create_run(conv["id"], "local", content="主持")
    assert run_service.claim_run(coordinator["id"]) is True
    members = [
        run_service.create_run(
            conv["id"], "local", role_id=f"role-{i}", target_role_id=f"role-{i}",
            content=f"任务 {i}")
        for i in range(3)
    ]

    from services.agent_zero.task_layer.conv_worker import ConversationRunWorker

    worker = ConversationRunWorker()
    started: list[str] = []
    monkeypatch.setattr(worker, "_start", lambda run_id: started.append(run_id))
    asyncio.run(worker._cycle())
    assert {run["id"] for run in members} <= set(started)


def test_delegate_with_assignments_resolves_to_call_team():
    from services.agent_zero.tools import packs

    code, args, error = packs.resolve_call("delegate", {
        "target": "team",
        "assignments": [{"member_name": "后端", "goal": "整理 API"}],
    })
    assert error is None
    assert code == "call_team"
    assert args["assignments"][0]["goal"] == "整理 API"


def test_graph_includes_parallel_member_run(app_env):
    ws = _init(app_env)
    team = ts.create_team(
        ws, "画布群", leader={"name": "主持"},
        members=[{"name": "研究员", "expertise": "查资料"}],
    )
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    member = team["members"][0]
    coordinator = run_service.create_run(conv["id"], "local", content="拆解任务")
    run_service.claim_run(coordinator["id"])
    member_run = run_service.create_run(
        conv["id"], "local", role_id=member["id"], target_role_id=member["id"],
        content="查资料")
    run_service.claim_run(member_run["id"])
    run_service.append_event(member_run["id"], "tool_call", {
        "tool_call": {"id": "t1", "name": "web", "status": "running"},
    })
    run_service.append_event(member_run["id"], "done", {
        "done": True, "text": "查到了三条资料",
    })

    data = graph_service.conversation_graph(conv["id"])
    assert data is not None
    node = next(n for n in data["nodes"] if n["role_id"] == member["id"])
    assert node["kind"] == "member"
    assert node["run_id"] == member_run["id"]
    assert node["tools"][0]["name"] == "web"
    assert "查到了三条资料" in node["text"]
    assert data["stats"]["member_runs"] >= 1
    assert any(edge["from"] == "root" and edge["to"] == node["id"]
               for edge in data["edges"])

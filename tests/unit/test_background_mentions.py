"""群组会话：角色 @成员 → 成员真正回复 / 后台任务 / 主会话不等待。

覆盖用户反馈的三点：
1. 角色回复里 `@成员` 不能石沉大海，被点名的成员要接活；
2. 需要动手做事时明确登记为后台任务（执行画布可见，可交回主会话）；
3. 背景任务不占主会话：`running_run(include_member=False)` 不把成员 run
   当成主会话在跑；任务结束只写通知，用户点「继续」才生成主持 run。
"""
from __future__ import annotations

import asyncio

import pytest

from services.agent_zero.services import team_task_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation, run_service


def _env(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_WORKSPACE_ID


def _team_conversation(ws_id: str, mode: str = "orchestrate"):
    from services.agent_zero.services import team_service

    team = team_service.create_team(
        ws_id, "研发群", mode=mode,
        leader={"name": "主持", "expertise": "编排"},
        members=[{"name": "后端", "expertise": "API"},
                 {"name": "测试", "expertise": "pytest"}],
    )
    conv = conversation.create_conversation(ws_id, "local", team_id=team["id"])
    return team, conv


def _quiet_dispatch(monkeypatch):
    """别真的把 worker 拉起来跑模型。"""
    from services.agent_zero.task_layer import conv_worker

    woke: list[str] = []

    async def fake_request(run_id: str) -> None:
        woke.append(run_id)

    monkeypatch.setattr(conv_worker, "request_run", fake_request)
    return woke


def test_mention_precedes_member_ack_in_transcript(app_env, monkeypatch):
    """转录顺序：先「@成员」，再成员的「收到，我在后台执行…」。

    确认消息落库早于成员 run 启动，只按自己的 created_at 排会跑到 @ 消息前面
    （用户反馈的「@ 在后、回复在前」）。它必须用 run 锚点回到这一回合里。
    """
    ws = _env(app_env)
    team, conv = _team_conversation(ws)
    leader = team["leader"]
    member = team["members"][0]
    _quiet_dispatch(monkeypatch)

    team_task_service.create_task(
        conv, team, leader,
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "整理接口文档", "role": member}],
        "local")
    task = team_task_service.list_tasks(conv["id"])[0]
    run_id = str(task["members"][0]["run_id"])

    messages = conversation.list_messages(conv["id"])
    kinds = [m.get("kind") for m in messages]
    mention_idx, ack_idx = kinds.index("team_mention"), kinds.index("task_ack")
    assert mention_idx < ack_idx
    assert member["name"] in messages[mention_idx]["content"]
    # 锚点落在成员 run 上；确认消息本身不是那一轮的产出（run_id 仍为空）
    assert messages[ack_idx]["anchor_run_id"] == run_id
    assert messages[ack_idx].get("run_id") == ""


def test_legacy_ack_without_anchor_still_follows_mention(app_env, monkeypatch):
    """老数据兜底：没有 anchor_run_id 的历史确认消息也要排在 @ 消息之后。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local

    ws = _env(app_env)
    team, conv = _team_conversation(ws)
    leader = team["leader"]
    member = team["members"][0]
    _quiet_dispatch(monkeypatch)

    task = team_task_service.create_task(
        conv, team, leader,
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "整理接口文档", "role": member}],
        "local")
    run_id = str(task["members"][0]["run_id"])
    before = conversation.list_messages(conv["id"])
    ack_id = next(m["id"] for m in before if m.get("kind") == "task_ack")

    # 模拟升级前的数据：去掉排序锚点，并把确认消息与 run 固定到同一秒，
    # 还原「确认先落库、run 后启动」的真实时间关系。
    stamp = "2026-01-01 00:00:00"
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(text("UPDATE conversation_runs SET created_at = :t "
                        "WHERE id = :rid"), {"t": stamp, "rid": run_id})
        db.execute(text("UPDATE gdag_nodes SET created_at = :t, "
                        "props = json_remove(props, '$.anchor_run_id') "
                        "WHERE id = :mid"), {"t": stamp, "mid": ack_id})
        db.commit()

    messages = conversation.list_messages(conv["id"])
    kinds = [m.get("kind") for m in messages]
    assert kinds.index("team_mention") < kinds.index("task_ack")


def test_role_mention_dispatches_member_reply(app_env, monkeypatch):
    ws = _env(app_env)
    team, conv = _team_conversation(ws)
    leader = team["leader"]
    member = next(m for m in team["members"] if m["name"] == "后端")

    woke = _quiet_dispatch(monkeypatch)

    dispatched = asyncio.run(conversation._dispatch_reply_mentions(
        conv, leader, "这轮接口先交给 @后端 看一下：确认 /users 的返回结构。",
        "local"))

    assert len(dispatched) == 1 and dispatched[0]["kind"] == "reply"
    run = run_service.get_run(dispatched[0]["run_id"])
    assert run and run["target_role_id"] == member["id"]
    assert "确认 /users 的返回结构" in (run.get("content") or "")
    assert woke == [dispatched[0]["run_id"]]

    # 系统消息让用户看到「已经交给谁」，而不是以为没反应
    messages = conversation.list_messages(conv["id"])
    assert any(m.get("by") == "system" and "后端" in str(m.get("content") or "")
               for m in messages)


def test_background_hint_creates_team_task(app_env, monkeypatch):
    ws = _env(app_env)
    team, conv = _team_conversation(ws)
    leader = team["leader"]

    _quiet_dispatch(monkeypatch)

    dispatched = asyncio.run(conversation._dispatch_reply_mentions(
        conv, leader,
        "@后端 这个放到后台任务里跑：把用户表的索引补齐，跑完把结果贴回来。",
        "local"))

    assert len(dispatched) == 1 and dispatched[0]["kind"] == "background"
    task_id = dispatched[0]["task_id"]
    assert task_id
    task = team_task_service.get_task(task_id)
    assert task and task["status"] in ("running", "queued")
    assert task["total_members"] == 1


def test_running_run_can_ignore_member_background_runs(app_env, monkeypatch):
    ws = _env(app_env)
    _team, conv = _team_conversation(ws)
    _quiet_dispatch(monkeypatch)

    member_run = run_service.create_run(
        conv["id"], "local", role_id="m1", target_role_id="m1",
        content="@后端 看下接口", mode="chat")
    # 成员 run 正在跑：主会话不认它，发送按钮不锁
    assert run_service.running_run(conv["id"], include_member=False) is None
    assert run_service.claim_run(member_run["id"]) is True
    assert run_service.running_run(conv["id"], include_member=False) is None
    # 兼容旧语义：需要「会话是否忙」时仍能看到成员 run
    assert run_service.running_run(conv["id"]) is not None


def test_continue_task_hands_results_back_once(app_env, monkeypatch):

    ws = _env(app_env)
    team, conv = _team_conversation(ws)
    leader = team["leader"]
    member = team["members"][0]
    _quiet_dispatch(monkeypatch)

    task = team_task_service.create_task(
        conv, team, leader,
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "补齐索引", "role": member}],
        "local")
    member_run = str(task["members"][0]["run_id"])
    run_service.finish_run(member_run, run_service.STATUS_DONE, "")

    public = team_task_service.get_task(task["id"])
    assert public["status"] == "done" and public["can_continue"] is True

    out = team_task_service.continue_task(task["id"])
    run = run_service.get_run(out["run"]["id"])
    assert run and not run.get("target_role_id")  # 主会话 run（主持）
    assert "补齐索引" in (run.get("content") or "")
    assert team_task_service.get_task(task["id"])["continued_at"]
    assert team_task_service.get_task(task["id"])["can_continue"] is False

    with pytest.raises(ValueError):
        team_task_service.continue_task(task["id"])


def test_continue_task_rejects_unfinished_task(app_env, monkeypatch):

    ws = _env(app_env)
    team, conv = _team_conversation(ws)
    leader = team["leader"]
    member = team["members"][0]
    _quiet_dispatch(monkeypatch)

    task = team_task_service.create_task(
        conv, team, leader,
        [{"role_id": member["id"], "role_name": member["name"],
          "goal": "还在跑", "role": member}],
        "local")
    with pytest.raises(ValueError):
        team_task_service.continue_task(task["id"])

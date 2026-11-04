"""V3 团队 / 群聊：模式、上限、角色能力与旧群组惰性归一。

对应路线图 P1：团队是唯一多角色机制，工作区只作容器；旧的
`workspaces.host_role_id` / `member_role_ids` 通过
`ensure_team_for_conversation` 惰性归一成默认团队，不再直接参与路由。
"""
from __future__ import annotations

import sqlite3

from services.agent_zero.services import team_service as ts
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation


def _init(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_WORKSPACE_ID


# ── 模式与上限 ──────────────────────────────────────────────────────────

def test_normalize_mode_and_speakers():
    assert ts.normalize_mode("ROUNDTABLE") == "roundtable"
    assert ts.normalize_mode("orchestrate") == "orchestrate"
    assert ts.normalize_mode("single") == "single"
    assert ts.normalize_mode("nope") == "orchestrate"
    assert ts.normalize_mode(None) == "orchestrate"

    assert ts.normalize_speakers(None) == 4
    assert ts.normalize_speakers("abc") == 4
    assert ts.normalize_speakers(0) == 1
    assert ts.normalize_speakers(99) == 12
    assert ts.normalize_speakers(6) == 6


def test_create_and_update_team_mode(app_env):
    ws = _init(app_env)
    team = ts.create_team(
        ws, "研发群", "实现 + 测试",
        leader={"name": "主持"},
        members=[{"name": "后端", "expertise": "API"},
                 {"name": "测试", "expertise": "pytest"}],
        mode="roundtable", max_speakers=6,
    )
    assert team["mode"] == "roundtable"
    assert team["max_speakers"] == 6
    assert [m["name"] for m in team["members"]] == ["后端", "测试"]

    updated = ts.update_team(team["id"], mode="single", max_speakers=2)
    assert updated["mode"] == "single"
    assert updated["max_speakers"] == 2

    # 非法 mode 走归一，不写坏数据；越界上限被夹住
    odd = ts.create_team(ws, "临时群", mode="what", max_speakers=99)
    assert odd["mode"] == "orchestrate"
    assert odd["max_speakers"] == 12


def test_member_envelope_and_model_are_configurable(app_env):
    ws = _init(app_env)
    team = ts.create_team(
        ws, "能力群",
        leader={"name": "主持"},
        members=[{"name": "测试", "expertise": "pytest",
                  "tools": ["shell", "read_file", "not_a_tool"],
                  "model_code": "fast"}],
    )
    member = team["members"][0]
    role = zero_store.get_role(member["id"])
    tools = role["envelope"]["tools"]
    assert "shell" in tools and "read_file" in tools
    assert "not_a_tool" not in tools            # 未知工具不落库
    assert "write_file" not in tools            # 显式收窄后不再有全集
    assert role["model_code"] == "fast"

    roster = ts.roster_text(team)
    assert "协作模式：`orchestrate`" in roster
    assert "工具：" in roster and "模型：fast" in roster


def test_roster_member_view(app_env):
    ws = _init(app_env)
    team = ts.create_team(ws, "圆桌群", mode="roundtable",
                          leader={"name": "主持"},
                          members=[{"name": "后端", "expertise": "API"}])
    member_role = zero_store.get_role(team["members"][0]["id"])
    text = ts.roster_text(team, role=member_role)
    assert "成员「后端」" in text
    assert "只讲你自己的判断" in text
    assert "**主持**" not in text


def test_roster_mode_hints(app_env):
    ws = _init(app_env)
    team = ts.create_team(ws, "圆桌群", mode="roundtable",
                          leader={"name": "主持"}, members=[{"name": "后端"}])
    text = ts.roster_text(team)
    assert "协作模式：`roundtable`" in text
    assert "@成员名" in text


# ── 旧群组惰性归一 ──────────────────────────────────────────────────────

def test_legacy_group_workspace_is_normalized_to_default_team(app_env):
    _init(app_env)
    extra = zero_store.upsert_role({
        "id": "role-fe", "group_id": "ws-g", "name": "前端",
        "persona": "", "expertise": "UI", "envelope": zero_store.DEFAULT_ENVELOPE,
    })
    assert extra["id"] == "role-fe"
    zero_store.create_workspace("group", "ws-g", "研发组", "local",
                                member_role_ids=["default", "role-fe"])
    zero_store.update_workspace("ws-g", {"host_role_id": "default"})

    team = ts.ensure_workspace_team("ws-g")
    assert team and team["id"] == ts.default_team_id("ws-g")
    assert team["leader"]["id"] == "default"
    assert [m["id"] for m in team["members"]] == ["role-fe"]
    assert team["mode"] == "orchestrate"

    # 幂等：第二次不重复建、也不覆盖
    again = ts.ensure_workspace_team("ws-g")
    assert again["id"] == team["id"]
    assert len(ts.list_teams("ws-g")) == 1

    conv = conversation.create_conversation("ws-g", "local")
    assert not str(conv.get("team_id") or "")
    team2 = ts.ensure_team_for_conversation(conv)
    assert team2["id"] == team["id"]
    assert conv["team_id"] == team["id"]
    assert conversation.get_conversation(conv["id"])["team_id"] == team["id"]


def test_workspace_without_group_signals_has_no_default_team(app_env):
    _init(app_env)
    zero_store.create_workspace("task", "ws-plain", "临时空间", "local")
    assert ts.ensure_workspace_team("ws-plain") is None


def test_single_role_chat_stays_single_even_if_workspace_has_legacy_group(app_env):
    """单聊不会被工作区的旧群组列自动变成「默认群聊」。"""
    _init(app_env)
    zero_store.upsert_role({
        "id": "role-fe", "group_id": "ws-g", "name": "前端",
        "persona": "", "expertise": "UI", "envelope": zero_store.DEFAULT_ENVELOPE,
    })
    zero_store.create_workspace("group", "ws-g", "研发组", "local",
                                member_role_ids=["default", "role-fe"])
    zero_store.update_workspace("ws-g", {"host_role_id": "default"})
    legacy = ts.ensure_workspace_team("ws-g")
    assert legacy is not None

    conv = conversation.create_conversation("ws-g", "local", role_id="role-fe")
    # 没显式绑定：仍然是单聊，角色就是 role-fe，而不是团队主持。
    assert ts.team_for_conv(conv) is None
    assert conversation._resolve_chat_role(conv)["id"] == "role-fe"

    # 只有用户 / 工具显式绑定群组时才进入群聊路由。
    ts.bind_team(conv["id"], legacy["id"])
    bound = conversation.get_conversation(conv["id"])
    assert ts.team_for_conv(bound)["id"] == legacy["id"]


def test_cleanup_removes_legacy_default_teams_with_members(app_env):
    """旧版自动归一出来的默认团队（即使有成员）也要解绑并删除。"""
    _init(app_env)
    zero_store.upsert_role({
        "id": "role-fe", "group_id": "ws-g", "name": "前端",
        "persona": "", "expertise": "UI", "envelope": zero_store.DEFAULT_ENVELOPE,
    })
    zero_store.create_workspace("group", "ws-g", "研发组", "local",
                                member_role_ids=["default", "role-fe"])
    zero_store.update_workspace("ws-g", {"host_role_id": "default"})
    legacy = ts.ensure_workspace_team("ws-g")
    conv = conversation.create_conversation("ws-g", "local")
    ts.bind_team(conv["id"], legacy["id"])

    assert ts.cleanup_phantom_default_teams() >= 1
    assert ts.get_team(legacy["id"]) is None
    assert not str(conversation.get_conversation(conv["id"])["team_id"] or "")


# ── 路由选择 ────────────────────────────────────────────────────────────

def test_team_route_picks_roundtable_only_with_members(app_env):
    ws = _init(app_env)
    round_team = ts.create_team(ws, "圆桌", mode="roundtable",
                                leader={"name": "主持"},
                                members=[{"name": "后端"}])
    route, leader, members = conversation._team_route(round_team)
    assert route == "roundtable" and leader["id"] and len(members) == 1

    empty = ts.create_team(ws, "空群", mode="roundtable", leader={"name": "主持"})
    route, leader, members = conversation._team_route(empty)
    assert route == "single" and leader["id"] and members == []

    single = ts.create_team(ws, "单角", mode="single", leader={"name": "主持"})
    route, _, _ = conversation._team_route(single)
    assert route == "single"


def test_delegate_tools_cannot_widen_member_envelope(app_env):
    """委派时传 tools 只能收窄；不在成员包络里的工具被忽略。"""
    ws = _init(app_env)
    team = ts.create_team(ws, "收窄群", leader={"name": "主持"},
                          members=[{"name": "测试", "tools": ["shell", "read_file"]}])
    member = zero_store.get_role(team["members"][0]["id"])

    narrowed = conversation._narrow_role_tools(member, ["shell", "write_file"])
    assert narrowed["envelope"]["tools"] == ["shell"]      # write_file 不在成员包络内
    # 没传/传空：保持成员自己的包络
    assert conversation._narrow_role_tools(member, None) is member
    assert conversation._narrow_role_tools(member, []) is member


def test_call_team_dispatches_background_member_run(app_env):
    """主持 call_team = 在群里 @ 成员，成员作为独立后台 run 执行，不再阻塞主持。"""
    import asyncio
    import json

    from services.agent_zero.task_layer import run_service

    ws = _init(app_env)
    team = ts.create_team(ws, "后台群", leader={"name": "主持"},
                          members=[{"name": "后端", "expertise": "API"}])
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    member = team["members"][0]
    out = json.loads(asyncio.run(conversation._exec_call_team(
        conv, "local", zero_store.get_role(team["leader"]["id"]),
        {"member_name": member["name"], "goal": "整理 API 文档"}, 0)))
    assert out["status"] == "dispatched"
    run = run_service.get_run(out["run_id"])
    assert run["target_role_id"] == member["id"]
    assert run["status"] in ("queued", "running")

    messages = conversation.list_messages(conv["id"])
    mention = next(m for m in messages if m.get("kind") == "team_mention")
    assert member["name"] in mention["content"]
    assert "整理 API 文档" in mention["content"]
    assert mention["queued"] is True


def test_team_manage_tool_creates_with_mode(app_env):
    """模型侧入口与 REST 写同一份数据：mode / max_speakers 都要落库并绑定会话。"""
    import json

    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    specs: list = []
    out = json.loads(conversation._exec_team_manage(conv, ws, {
        "action": "create", "name": "工具群", "mode": "roundtable",
        "max_speakers": 5, "members": [{"name": "后端"}],
    }, specs))
    assert out["team"]["mode"] == "roundtable"
    assert out["team"]["max_speakers"] == 5
    assert conv["team_id"] == out["team"]["id"]
    assert ts.get_team(out["team"]["id"])["mode"] == "roundtable"


def test_shared_role_survives_team_delete(app_env):
    """两个团队共用同一角色时，删一个团队不能把角色删掉。"""
    ws = _init(app_env)
    a = ts.create_team(ws, "A 群", leader={"name": "主持"},
                       members=[{"name": "共享", "expertise": "API"}])
    rid = a["members"][0]["id"]
    b = ts.create_team(ws, "B 群", leader={"name": "主持"},
                       members=[{"id": rid, "name": "共享", "expertise": "API"}])
    assert b["members"][0]["id"] == rid

    ts.delete_team(a["id"])
    assert zero_store.get_role(rid) is not None      # B 还在用

    ts.update_team(b["id"], members=[])
    assert zero_store.get_role(rid) is None          # 没人用了才删


def test_group_members_dedupe_by_id_and_name(app_env):
    """邀请 / 保存群成员时：主持不能重复、同 id / 同名成员只保留一个。"""
    ws = _init(app_env)
    team = ts.create_team(
        ws, "去重群",
        leader={"name": "主持"},
        members=[
            {"name": "后端", "expertise": "API"},
            {"name": "后端", "expertise": "重复"},
            {"id": "default", "name": "主持"},
        ],
    )
    assert team["leader"]["name"] == "主持"
    assert [m["name"] for m in team["members"]] == ["后端"]

    updated = ts.update_team(team["id"], members=[
        {"id": team["members"][0]["id"], "name": "后端"},
        {"name": "后端"},
        {"name": "测试"},
        {"name": "测试"},
    ])
    assert [m["name"] for m in updated["members"]] == ["后端", "测试"]



def test_system_message_is_display_only(app_env):
    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    conversation.append_system_message(conv["id"], "「后端」加入了群聊")
    msgs = conversation.list_messages(conv["id"])
    assert [m for m in msgs if m.get("by") == "system"]
    # 不进入模型上下文（否则模型会把 IM 通知当成用户指令）
    assert conversation._history_messages(conv["id"]) == []


def test_mentions_are_stored_on_run(app_env):
    """@成员 的显式点名要跟着 run 落库（队列重放 / 重连后仍知道点了谁）。"""
    from services.agent_zero.task_layer import run_service

    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    run = run_service.create_run(conv["id"], "local",
                                 content="@后端 看下接口", mentions=["后端"])
    got = run_service.get_run(run["id"])
    assert got["mentions"] == ["后端"]


# ── 点名直连：用户 / 角色 @ 谁，就由谁答 ────────────────────────────────

def test_mention_targets_resolve_id_name_and_text():
    roles = [{"id": "h", "name": "主持"},
             {"id": "m1", "name": "后端"},
             {"id": "m2", "name": "测试"}]
    # 显式 mentions 优先：传 id 或名字都能命中
    assert [r["id"] for r in conversation._mention_targets(["m1"], "", roles)] == ["m1"]
    assert [r["id"] for r in conversation._mention_targets(["后端"], "", roles)] == ["m1"]
    # 正文兜底；多个点名保持出现顺序
    assert [r["id"] for r in conversation._mention_targets(
        None, "@后端 和 @测试 一起看", roles)] == ["m1", "m2"]
    # 没有点名就是空，由调用方决定默认主持
    assert conversation._mention_targets(None, "看下接口", roles) == []


def test_direct_mention_overrides_orchestrate_route(app_env):
    """orchestrate（默认模式）下用户 @成员：直接交给成员，不经过主持。"""
    ws = _init(app_env)
    team = ts.create_team(ws, "编排群", mode="orchestrate",
                          leader={"name": "主持"},
                          members=[{"name": "后端", "expertise": "API"}])
    use_group, _leader, members, addressed = conversation._team_reply_plan(
        team, None, "@后端 看下接口")
    assert use_group is True
    assert [r["id"] for r in addressed] == [members[0]["id"]]

    # 没点名时 orchestrate 仍由主持单角色运行
    no_mention, _leader, _members, addressed = conversation._team_reply_plan(
        team, None, "看下接口")
    assert no_mention is False and addressed == []


def _fake_role_stream(monkeypatch, replies):
    spoken = []

    async def fake_stream(conv, user, role, sink, **kwargs):
        spoken.append(role["id"])
        sink["text"] = replies.get(role["id"], "")
        if False:      # pragma: no cover - 让它成为异步生成器
            yield {}

    monkeypatch.setattr(conversation, "_stream_role", fake_stream)
    monkeypatch.setattr(conversation, "_persist_assistant", lambda *a, **k: "msg")
    monkeypatch.setattr(conversation, "_capture_run_memory", lambda *a, **k: None)
    monkeypatch.setattr(conversation.zero_store, "append_audit", lambda *a, **k: None)
    return spoken


def test_group_reply_mention_answers_member_not_host(monkeypatch):
    import asyncio

    spoken = _fake_role_stream(monkeypatch, {"m1": "收到"})
    host = {"id": "h", "name": "主持"}
    member = {"id": "m1", "name": "后端"}

    async def collect():
        return [e async for e in conversation._group_reply_stream(
            {"id": "conv1"}, "local", "@后端 看下接口", ["后端"], host, [member])]

    events = asyncio.run(collect())
    assert spoken == ["m1"]
    done = [e for e in events if e.get("done")]
    assert [e["role_id"] for e in done] == ["m1"]


def test_group_reply_chains_mentions_from_any_role(monkeypatch):
    """任何角色回复里 @成员，都由该成员直接接话，不需要主持转述。"""
    import asyncio

    spoken = _fake_role_stream(monkeypatch, {
        "m1": "@主持 记一下，@测试 补用例",
        "h": "好",
        "m2": "用例补好了",
    })
    host = {"id": "h", "name": "主持"}
    members = [{"id": "m1", "name": "后端"}, {"id": "m2", "name": "测试"}]

    async def collect():
        return [e async for e in conversation._group_reply_stream(
            {"id": "conv1"}, "local", "@后端 看下接口", None, host, members,
            max_speakers=4)]

    events = asyncio.run(collect())
    assert spoken == ["m1", "h", "m2"]
    done = [e for e in events if e.get("done")]
    assert [e["role_id"] for e in done] == ["m1", "h", "m2"]


# ── 旧库升级 ────────────────────────────────────────────────────────────


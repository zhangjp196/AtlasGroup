"""群组角色头像同步：团队读取是实时的，保存群组不能把角色头像冲掉。

用户反馈「群组中的角色头像没有同步」的两条链路：
1. 群组成员是角色的精简视图，头像必须跟随角色当前值（而不是建群时拷贝一份）；
2. 保存群组 / 更新成员时，后端 `_ensure_role` 不能把角色已有头像清空。
"""
from __future__ import annotations

from services.agent_zero.services import team_service as ts
from services.agent_zero.stores import zero_store

AVATAR_A = "/api/agent-zero/avatars/role/avatar-aaaaaaaaaaaaaaaa.png"
AVATAR_B = "/api/agent-zero/avatars/role/avatar-bbbbbbbbbbbbbbbb.png"


def _env(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_WORKSPACE_ID


def _role(ws_id: str, name: str, avatar: str = "") -> dict:
    return zero_store.upsert_role({
        "id": zero_store.new_id("role"),
        "group_id": ws_id,
        "name": name,
        "persona": "",
        "expertise": "",
        "avatar": avatar,
        "envelope": {},
    })


def test_team_member_avatar_follows_role(app_env):
    ws = _env(app_env)
    role = _role(ws, "后端", AVATAR_A)
    team = ts.create_team(ws, "研发群", leader={"name": "主持"},
                          members=[{"id": role["id"], "name": "后端"}])
    member = ts.get_team(team["id"])["members"][0]
    assert member["avatar"] == AVATAR_A

    # 角色换头像 → 群组成员同一时刻就是新头像（不缓存旧值）
    zero_store.upsert_role({**role, "avatar": AVATAR_B})
    member = ts.get_team(team["id"])["members"][0]
    assert member["avatar"] == AVATAR_B


def test_saving_team_keeps_role_avatar(app_env):
    ws = _env(app_env)
    role = _role(ws, "测试", AVATAR_A)
    team = ts.create_team(ws, "研发群", leader={"name": "主持"},
                          members=[{"id": role["id"], "name": "测试"}])

    # 前端传的是成员精简视图（可能没有 avatar / 是旧值）：保存不能清空角色头像
    ts.update_team(team["id"], members=[{"id": role["id"], "name": "测试"}])
    assert zero_store.get_role(role["id"])["avatar"] == AVATAR_A

    ts.update_team(team["id"], members=[{"id": role["id"], "name": "测试",
                                         "avatar": ""}])
    assert zero_store.get_role(role["id"])["avatar"] == AVATAR_A


def test_removing_reused_library_role_keeps_it_in_library(app_env):
    """从群组移出复用的角色库角色时，不能把角色本身删掉。"""
    ws = _env(app_env)
    role = _role(ws, "后端", AVATAR_A)
    team = ts.create_team(ws, "研发群", leader={"name": "主持"},
                          members=[{"id": role["id"], "name": "后端"}])
    ts.update_team(team["id"], members=[])
    assert zero_store.get_role(role["id"]) is not None
    assert zero_store.get_role(role["id"])["avatar"] == AVATAR_A


def test_team_created_role_keeps_avatar(app_env):
    ws = _env(app_env)
    team = ts.create_team(
        ws, "研发群", leader={"name": "主持"},
        members=[{"name": "新成员", "avatar": AVATAR_A}])
    member = ts.get_team(team["id"])["members"][0]
    assert member["avatar"] == AVATAR_A

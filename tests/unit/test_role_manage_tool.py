"""LLM 角色库工具：临时角色 → 加入当前群 → 提升为持久。"""
from __future__ import annotations

from services.agent_zero.services import team_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation


def _setup(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    ws = zero_store.DEFAULT_WORKSPACE_ID
    team = team_service.create_team(ws, "研发群", leader={"name": "主持"},
                                    members=[{"name": "后端", "expertise": "API"}])
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])
    return conv, team


def test_create_temporary_role_joins_team(app_env):
    conv, team = _setup(app_env)
    out = conversation._exec_role_manage(conv, {
        "action": "create", "name": "调研员", "expertise": "只查资料",
        "tools": ["web", "not_a_tool"],
    })
    assert out["ok"] and out["added_to_team"]
    role = out["role"]
    assert role["scope"] == "temporary" and role["session_id"] == conv["id"]
    assert "web" in role["tools"] and "not_a_tool" not in role["tools"]

    fresh = team_service.get_team(team["id"])
    assert any(m["id"] == role["id"] for m in fresh["members"])

    listed = conversation._exec_role_manage(conv, {"action": "list"})
    assert any(r["id"] == role["id"] for r in listed["roles"])


def test_promote_and_delete_role(app_env):
    conv, _ = _setup(app_env)
    role = conversation._exec_role_manage(conv, {
        "action": "create", "name": "临时-提升用例"})["role"]
    promoted = conversation._exec_role_manage(conv, {
        "action": "promote", "role_id": role["id"]})["role"]
    assert promoted["scope"] == "persistent"
    assert promoted["session_id"] == ""
    deleted = conversation._exec_role_manage(conv, {
        "action": "delete", "role_id": role["id"]})
    assert deleted["ok"] and zero_store.get_role(role["id"]) is None


def test_default_role_cannot_be_deleted_and_bad_action_errors(app_env):
    conv, _ = _setup(app_env)
    out = conversation._exec_role_manage(conv, {
        "action": "delete", "role_id": zero_store.DEFAULT_ROLE_ID})
    assert out["error"]
    assert conversation._exec_role_manage(conv, {"action": "nope"})["error"]

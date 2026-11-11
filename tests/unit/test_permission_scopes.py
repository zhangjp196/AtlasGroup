"""群组 / 角色永久工具授权：审批动作、跨会话生效与撤销。"""
from __future__ import annotations


def _conversation(client) -> str:
    res = client.post("/api/agent-zero/conversations", json={})
    assert res.status_code == 200, res.text
    return str(res.json()["data"]["id"])


def test_role_permanent_grant_survives_new_conversations(client):
    from services.agent_zero.task_layer import permissions

    conv_id = _conversation(client)
    approval = permissions.create_approval(
        conv_id, None, "shell", {"cmd": "rm -rf build"}, "high",
        ["递归删除"], role_id="role-alpha", team_id="team-alpha")
    assert approval["can_grant_role"] is True
    assert approval["can_grant_team"] is True

    decided = permissions.decide_approval(approval["id"], "approve_role")
    assert decided["scope"] == "role"
    assert permissions._has_scope_grant("shell", role_id="role-alpha")

    # 同一角色在任意会话都不再询问；其它角色仍然要问。
    allowed = permissions.decide(
        conv_id, "shell", {"cmd": "rm -rf build"}, role_id="role-alpha")
    assert allowed["ask"] is False
    assert allowed["mode"] == "permanent"
    other = permissions.decide(
        conv_id, "shell", {"cmd": "rm -rf build"}, role_id="role-beta")
    assert other["ask"] is True


def test_team_permanent_grant_covers_members(client):
    from services.agent_zero.task_layer import permissions

    conv_id = _conversation(client)
    approval = permissions.create_approval(
        conv_id, None, "shell", {"cmd": "rm -rf build"}, "high",
        ["递归删除"], role_id="role-alpha", team_id="team-alpha")
    permissions.decide_approval(approval["id"], "approve_team")

    assert permissions._has_scope_grant("shell", team_id="team-alpha")
    # 群内其它成员走同一群组授权，不再询问。
    allowed = permissions.decide(
        conv_id, "shell", {"cmd": "rm -rf build"}, role_id="role-beta",
        team_id="team-alpha")
    assert allowed["ask"] is False


def test_permanent_grant_api_lists_and_revokes(client):
    from services.agent_zero.task_layer import permissions

    permissions.add_scope_grant("role", "shell", role_id="role-api")
    listed = client.get(
        "/api/agent-zero/app/permissions/grants?role_id=role-api")
    assert listed.status_code == 200
    items = listed.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["tool"] == "shell"

    deleted = client.delete(
        f"/api/agent-zero/app/permissions/grants/{items[0]['id']}")
    assert deleted.status_code == 200
    assert client.get(
        "/api/agent-zero/app/permissions/grants?role_id=role-api"
    ).json()["data"]["items"] == []


def test_permanent_grant_requires_matching_scope(client):
    import pytest

    from services.agent_zero.task_layer import permissions

    with pytest.raises(ValueError):
        permissions.add_scope_grant("role", "shell", team_id="team-only")
    with pytest.raises(ValueError):
        permissions.add_scope_grant("global", "shell", role_id="role-x")

    conv_id = _conversation(client)
    approval = permissions.create_approval(
        conv_id, None, "shell", {"cmd": "rm -rf build"}, "high", ["递归删除"])
    with pytest.raises(ValueError):
        permissions.decide_approval(approval["id"], "approve_role")

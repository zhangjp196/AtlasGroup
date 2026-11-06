"""会话管理：角色 / 群组切换与 AtlasX 的边界。"""
from __future__ import annotations

API = "/api/agent-zero"


def _roles(client):
    return client.get(f"{API}/roles").json()["data"]["items"]


def test_atlasx_only_exists_in_global_agent(client):
    from services.agent_zero.stores import zero_store

    conv = client.post(f"{API}/conversations", json={}).json()["data"]
    assert conv["role_id"] == zero_store.DEFAULT_ROLE_ID
    roles = {item["id"]: item for item in _roles(client)}
    assert zero_store.DEFAULT_ROLE_ID in roles
    assert roles[zero_store.DEFAULT_ROLE_ID]["name"] == zero_store.DEFAULT_ROLE_NAME
    # 全局 AtlasX 不进入角色库 / 选择器
    assert zero_store.GLOBAL_ROLE_ID not in roles

    global_conv = client.post(f"{API}/conversations", json={"zone": "global-agent"}).json()["data"]
    assert global_conv["role_id"] == zero_store.GLOBAL_ROLE_ID


def test_conversation_settings_switches_role_and_group(client):
    role = client.post(f"{API}/roles", json={"name": "管理角色"}).json()["data"]
    conv = client.post(f"{API}/conversations", json={}).json()["data"]

    out = client.patch(f"{API}/conversations/{conv['id']}/settings",
                       json={"title": "管理测试", "role_id": role["id"]}).json()["data"]
    assert out["title"] == "管理测试"
    assert out["role_id"] == role["id"]

    team = client.post(f"{API}/teams", json={
        "name": "管理群",
        "members": [{"name": "成员A"}, {"name": "成员B"}],
    }).json()["data"]
    out = client.patch(f"{API}/conversations/{conv['id']}/settings",
                       json={"team_id": team["id"]}).json()["data"]
    assert out["team_id"] == team["id"]
    assert out["role_id"] == team["leader"]["id"]

    out = client.patch(f"{API}/conversations/{conv['id']}/settings",
                       json={"team_id": "", "role_id": role["id"]}).json()["data"]
    assert not out["team_id"]
    assert out["role_id"] == role["id"]


def test_binding_empty_group_is_rejected(client):
    conv = client.post(f"{API}/conversations", json={}).json()["data"]
    team = client.post(f"{API}/teams", json={"name": "空群"}).json()["data"]
    res = client.patch(f"{API}/conversations/{conv['id']}/settings",
                       json={"team_id": team["id"]})
    assert res.status_code == 400
    assert "成员" in res.json()["detail"]


def test_global_agent_cannot_be_turned_into_group(client):
    """全局 Agent 会话不能绑定群组；否则群组页会把它显示成全局 Agent。"""
    global_conv = client.post(f"{API}/conversations",
                              json={"zone": "global-agent"}).json()["data"]
    team = client.post(f"{API}/teams", json={
        "name": "全局群",
        "members": [{"name": "成员A"}],
    }).json()["data"]

    res = client.patch(f"{API}/conversations/{global_conv['id']}/settings",
                       json={"team_id": team["id"]})
    assert res.status_code == 400
    assert "全局" in res.json()["detail"]

    # 即使创建请求同时带 zone 与 team_id，也不会把全局会话变成群聊。
    created = client.post(f"{API}/conversations", json={
        "zone": "global-agent", "team_id": team["id"],
    }).json()["data"]
    assert not created.get("team_id")
    fetched = client.get(f"{API}/conversations/{created['id']}").json()["data"]
    assert not fetched.get("team_id")


def test_team_task_controls(client):
    from services.agent_zero.services import team_task_service
    from services.agent_zero.stores import zero_store
    from services.agent_zero.task_layer import conversation

    team = client.post(f"{API}/teams", json={
        "name": "后台任务群",
        "members": [{"name": "后端"}, {"name": "前端"}],
    }).json()["data"]
    conv = client.post(f"{API}/conversations", json={"team_id": team["id"]}).json()["data"]
    leader = zero_store.get_role(team["leader"]["id"])
    assignments = [{
        "role_id": member["id"],
        "role_name": member["name"],
        "goal": f"{member['name']}任务",
        "role": zero_store.get_role(member["id"]),
    } for member in team["members"]]
    task = team_task_service.create_task(
        conversation.get_conversation(conv["id"]), team, leader, assignments, "local")

    items = client.get(f"{API}/conversations/{conv['id']}/team-tasks").json()["data"]["items"]
    assert [item["id"] for item in items] == [task["id"]]
    assert items[0]["total_members"] == 2

    paused = client.post(
        f"{API}/conversations/{conv['id']}/team-tasks/{task['id']}/pause").json()["data"]
    assert paused["status"] == "paused"
    resumed = client.post(
        f"{API}/conversations/{conv['id']}/team-tasks/{task['id']}/resume").json()["data"]
    assert resumed["status"] == "running"
    stopped = client.post(
        f"{API}/conversations/{conv['id']}/team-tasks/{task['id']}/stop").json()["data"]
    assert stopped["status"] == "stopped"

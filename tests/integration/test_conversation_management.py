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


def test_conversation_list_reports_unread_and_running_counts(client):
    """会话列表用数字角标展示未读消息数 / 进行中运行数。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.runtime.zero.gdag.store import GdagStore
    from services.agent_zero.stores import zero_store
    from services.agent_zero.task_layer import run_service

    conv = client.post(f"{API}/conversations", json={}).json()["data"]
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text("UPDATE conversations SET last_read_at = '2000-01-01 00:00:00' "
                 "WHERE id = :id"),
            {"id": conv["id"]},
        )
        db.commit()
    GdagStore().upsert_node({
        "id": zero_store.new_id("msg"),
        "type": "fact",
        "camp": "default",
        "props": {
            "conversation_id": conv["id"],
            "workspace_id": conv["workspace_id"],
            "content": "新的助手回复",
            "stage": "exploration",
            "by": "assistant",
        },
    })
    run_service.create_run(conv["id"], "local", content="运行中")

    items = client.get(f"{API}/conversations?limit=200").json()["data"]["items"]
    row = next(item for item in items if item["id"] == conv["id"])
    assert row["unread_count"] >= 1
    assert row["running_count"] >= 1

    out = client.post(f"{API}/app/read-all").json()["data"]
    assert out["marked"] >= 1
    items = client.get(f"{API}/conversations?limit=200").json()["data"]["items"]
    row = next(item for item in items if item["id"] == conv["id"])
    assert row["unread_count"] == 0


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


def _mk_workspace(client, tmp_path, name):
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    return client.post(f"{API}/workspaces", json={
        "name": name, "path": str(folder),
    }).json()["data"]


def test_conversation_workspace_can_be_switched_per_conversation(client, tmp_path):
    """同一角色的不同会话可以各自固定不同工作区，切换角色 / 群组不覆盖。"""
    ws_a = _mk_workspace(client, tmp_path, "conv-ws-a")
    ws_b = _mk_workspace(client, tmp_path, "conv-ws-b")
    role = client.post(f"{API}/roles", json={
        "name": "工作区角色", "workspace_id": ws_a["id"],
    }).json()["data"]

    conv1 = client.post(f"{API}/conversations", json={"role_id": role["id"]}).json()["data"]
    conv2 = client.post(f"{API}/conversations", json={
        "role_id": role["id"], "workspace_id": ws_a["id"],
    }).json()["data"]
    assert conv1["workspace_id"] == ""
    assert conv2["workspace_id"] == ws_a["id"]

    out = client.patch(f"{API}/conversations/{conv1['id']}/settings",
                       json={"workspace_id": ws_b["id"]}).json()["data"]
    assert out["workspace_id"] == ws_b["id"]

    client.patch(f"{API}/roles/{role['id']}", json={"workspace_id": ""})
    assert client.get(f"{API}/conversations/{conv1['id']}").json()["data"]["workspace_id"] == ws_b["id"]
    assert client.get(f"{API}/conversations/{conv2['id']}").json()["data"]["workspace_id"] == ws_a["id"]

    out = client.patch(f"{API}/conversations/{conv1['id']}/workspace",
                       json={"workspace_id": ""}).json()["data"]
    assert out["workspace_id"] == ""
    assert client.get(f"{API}/conversations/{conv1['id']}").json()["data"]["workspace_id"] == ""


def test_legacy_default_workspace_is_hidden(client):
    response = client.get(f"{API}/workspaces", params={"include_archived": True})
    assert response.status_code == 200
    assert all(ws["id"] != "default" for ws in response.json()["data"]["items"])
    conv = client.post(f"{API}/conversations", json={"workspace_id": "default"}).json()["data"]
    assert conv["workspace_id"] == ""


def test_conversation_create_can_override_role_workspace(client, tmp_path):
    ws_a = _mk_workspace(client, tmp_path, "create-ws-a")
    ws_b = _mk_workspace(client, tmp_path, "create-ws-b")
    role = client.post(f"{API}/roles", json={
        "name": "创建时选工作区", "workspace_id": ws_a["id"],
    }).json()["data"]
    conv = client.post(f"{API}/conversations", json={
        "role_id": role["id"], "workspace_id": ws_b["id"],
    }).json()["data"]
    assert conv["workspace_id"] == ws_b["id"]
    assert conv["workspace_custom"] is True

    missing = client.patch(f"{API}/conversations/{conv['id']}/settings",
                           json={"workspace_id": "ws-not-exist"})
    assert missing.status_code == 400


def test_group_workspace_change_keeps_custom_conversation_workspace(client, tmp_path):
    ws_a = _mk_workspace(client, tmp_path, "team-ws-a")
    ws_b = _mk_workspace(client, tmp_path, "team-ws-b")
    ws_c = _mk_workspace(client, tmp_path, "team-ws-c")
    team = client.post(f"{API}/teams", json={
        "name": "工作区群", "workspace_id": ws_a["id"],
        "leader": {"name": "主持"}, "members": [{"name": "成员"}],
    }).json()["data"]
    conv = client.post(f"{API}/conversations", json={"team_id": team["id"]}).json()["data"]
    assert conv["workspace_id"] == ""

    client.patch(f"{API}/conversations/{conv['id']}/settings",
                 json={"workspace_id": ws_b["id"]})
    client.patch(f"{API}/teams/{team['id']}", json={"workspace_id": ws_c["id"]})
    assert client.get(f"{API}/conversations/{conv['id']}").json()["data"]["workspace_id"] == ws_b["id"]

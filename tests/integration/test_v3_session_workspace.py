"""V3：群组去工作区化 + 会话级工作区绑定。"""
from __future__ import annotations

API = "/api/agent-zero"


def test_group_can_be_created_without_workspace(client):
    res = client.post(f"{API}/teams", json={"name": "独立群", "leader": {"name": "主持"}})
    assert res.status_code == 200, res.text
    team = res.json()["data"]
    all_teams = client.get(f"{API}/teams").json()["data"]["items"]
    assert any(t["id"] == team["id"] for t in all_teams)


def test_group_list_without_workspace_returns_all(client, tmp_path):
    folder = tmp_path / "ws-b"
    folder.mkdir()
    ws = client.post(f"{API}/workspaces", json={
        "name": "空间 B", "path": str(folder)}).json()["data"]["id"]
    team = client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "B 群", "leader": {"name": "主持"}}).json()["data"]
    all_ids = {t["id"] for t in client.get(f"{API}/teams").json()["data"]["items"]}
    assert team["id"] in all_ids


def test_conversation_workspace_comes_from_role(client, tmp_path):
    """工作区由角色绑定；会话不再持有 bound_workspace_id。"""
    folder = tmp_path / "ws-c"
    folder.mkdir()
    ws = client.post(f"{API}/workspaces", json={
        "name": "空间 C", "path": str(folder)}).json()["data"]["id"]
    role = client.post(f"{API}/roles", json={
        "name": "空间角色", "workspace_id": ws}).json()["data"]
    assert role["workspace_id"] == ws

    conv = client.post(f"{API}/conversations", json={
        "role_id": role["id"]}).json()["data"]
    assert conv["workspace_id"] == ws
    assert conv["bound_workspace_id"] == ""

    folder2 = tmp_path / "ws-d"
    folder2.mkdir()
    ws2 = client.post(f"{API}/workspaces", json={
        "name": "空间 D", "path": str(folder2)}).json()["data"]["id"]
    client.patch(f"{API}/roles/{role['id']}", json={"workspace_id": ws2})
    fetched = client.get(f"{API}/conversations/{conv['id']}").json()["data"]
    assert fetched["workspace_id"] == ws2


def test_conversation_workspace_comes_from_team(client, tmp_path):
    folder = tmp_path / "ws-e"
    folder.mkdir()
    ws = client.post(f"{API}/workspaces", json={
        "name": "空间 E", "path": str(folder)}).json()["data"]["id"]
    team = client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "空间群",
        "leader": {"name": "主持"}, "members": [{"name": "成员"}]}).json()["data"]

    conv = client.post(f"{API}/conversations", json={
        "team_id": team["id"]}).json()["data"]
    assert conv["workspace_id"] == ws

    folder2 = tmp_path / "ws-f"
    folder2.mkdir()
    ws2 = client.post(f"{API}/workspaces", json={
        "name": "空间 F", "path": str(folder2)}).json()["data"]["id"]
    client.patch(f"{API}/teams/{team['id']}", json={"workspace_id": ws2})
    fetched = client.get(f"{API}/conversations/{conv['id']}").json()["data"]
    assert fetched["workspace_id"] == ws2


def test_legacy_bound_workspace_migrates_to_owner(client, tmp_path):
    folder = tmp_path / "ws-g"
    folder.mkdir()
    ws = client.post(f"{API}/workspaces", json={
        "name": "空间 G", "path": str(folder)}).json()["data"]["id"]

    # 旧客户端仍传 bound_workspace_id：提升为会话工作区，不再保留绑定字段。
    conv = client.post(f"{API}/conversations", json={
        "bound_workspace_id": ws}).json()["data"]
    assert conv["workspace_id"] == ws
    assert conv["bound_workspace_id"] == ""

    rebound = client.patch(f"{API}/conversations/{conv['id']}/workspace",
                           json={"workspace_id": ws}).json()["data"]
    assert rebound["bound_workspace_id"] == ""
    fetched = client.get(f"{API}/conversations/{conv['id']}").json()["data"]
    assert fetched["workspace_id"] == ws

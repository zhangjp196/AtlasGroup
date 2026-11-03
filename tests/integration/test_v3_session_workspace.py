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


def test_conversation_bound_workspace_is_session_level(client, tmp_path):
    folder = tmp_path / "ws-c"
    folder.mkdir()
    ws = client.post(f"{API}/workspaces", json={
        "name": "空间 C", "path": str(folder)}).json()["data"]["id"]

    conv = client.post(f"{API}/conversations", json={
        "bound_workspace_id": ws}).json()["data"]
    assert conv["bound_workspace_id"] == ws

    fetched = client.get(f"{API}/conversations/{conv['id']}").json()["data"]
    assert fetched["bound_workspace_id"] == ws

    cleared = client.patch(f"{API}/conversations/{conv['id']}/workspace",
                           json={"workspace_id": None}).json()["data"]
    assert cleared["bound_workspace_id"] == ""

    rebound = client.patch(f"{API}/conversations/{conv['id']}/workspace",
                           json={"workspace_id": ws}).json()["data"]
    assert rebound["bound_workspace_id"] == ws

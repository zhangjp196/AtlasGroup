"""团队（群聊）REST：CRUD / 绑定 / 解绑 / 跨工作区校验。"""
from __future__ import annotations

API = "/api/agent-zero"


def _default_ws(client) -> str:
    items = client.get(f"{API}/workspaces").json()["data"]["items"]
    # 跨用例时列表顺序会变（按最近活动排序）：明确取默认工作区，别取 items[0]
    row = next((w for w in items if w.get("is_default")), None)
    return (row or items[0])["id"]


def _conversation(client, ws_id: str) -> str:
    res = client.post(f"{API}/conversations", json={"workspace_id": ws_id})
    assert res.status_code == 200, res.text
    return res.json()["data"]["id"]


def test_team_crud_bind_and_delete(client):
    ws = _default_ws(client)
    created = client.post(f"{API}/teams", json={
        "workspace_id": ws,
        "name": "研发群",
        "description": "实现 + 测试",
        "mode": "roundtable",
        "leader": {"name": "主持"},
        "members": [{"name": "后端", "expertise": "API", "tools": ["shell", "read_file"]},
                    {"name": "测试", "expertise": "pytest"}],
    })
    assert created.status_code == 200, created.text
    team = created.json()["data"]
    assert team["mode"] == "roundtable"
    assert team["max_speakers"] == 4
    assert [m["name"] for m in team["members"]] == ["后端", "测试"]
    tid = team["id"]

    # 列表里只有用户显式建的群；默认工作区不再自动归一出「默认群聊」
    listed = client.get(f"{API}/teams", params={"workspace_id": ws}).json()["data"]["items"]
    ids = {t["id"] for t in listed}
    assert tid in ids
    assert f"team-default-{ws}" not in ids

    detail = client.get(f"{API}/teams/{tid}").json()["data"]
    assert detail["description"] == "实现 + 测试"

    patched = client.patch(f"{API}/teams/{tid}", json={
        "mode": "single", "max_speakers": 3, "name": "研发群 2"}).json()["data"]
    assert patched["mode"] == "single"
    assert patched["max_speakers"] == 3
    assert patched["name"] == "研发群 2"

    cid = _conversation(client, ws)
    bound = client.post(f"{API}/teams/{tid}/bind", json={"conversation_id": cid})
    assert bound.status_code == 200, bound.text
    current = client.get(f"{API}/conversations/{cid}/team").json()["data"]
    assert current["team_id"] == tid and current["team"]["id"] == tid

    # IM 系统消息：进群 / 退群各一条，且不进入模型上下文
    msgs = client.get(f"{API}/conversations/{cid}/messages").json()["data"]
    notices = [m for m in msgs if m.get("by") == "system"]
    assert any("加入群聊" in m["content"] for m in notices), notices

    unbound = client.post(f"{API}/teams/{tid}/unbind", json={"conversation_id": cid})
    assert unbound.status_code == 200
    msgs = client.get(f"{API}/conversations/{cid}/messages").json()["data"]
    assert any("退出群聊" in m["content"] for m in msgs if m.get("by") == "system")
    assert client.get(f"{API}/conversations/{cid}/team").json()["data"]["team"] is None

    client.post(f"{API}/teams/{tid}/bind", json={"conversation_id": cid})
    assert client.delete(f"{API}/teams/{tid}").status_code == 200
    assert client.get(f"{API}/teams/{tid}").status_code == 404
    # 删除团队会解绑会话
    assert client.get(f"{API}/conversations/{cid}/team").json()["data"]["team"] is None


def test_team_payload_validation(client):
    ws = _default_ws(client)
    assert client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "x", "mode": "nope"}).status_code == 422
    assert client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "x", "max_speakers": 99}).status_code == 422
    assert client.post(f"{API}/teams", json={
        "workspace_id": "missing-ws", "name": "x"}).status_code == 404
    assert client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": ""}).status_code == 422


def test_bind_rejects_cross_workspace(client, tmp_path):
    folder = tmp_path / "other-ws"
    folder.mkdir()
    other = client.post(f"{API}/workspaces", json={
        "name": "另一个空间", "path": str(folder)})
    assert other.status_code == 200, other.text
    other_ws = other.json()["data"]["id"]

    team = client.post(f"{API}/teams", json={
        "workspace_id": other_ws, "name": "外部群",
        "leader": {"name": "主持"}}).json()["data"]
    cid = _conversation(client, _default_ws(client))
    res = client.post(f"{API}/teams/{team['id']}/bind",
                      json={"conversation_id": cid})
    assert res.status_code == 400, res.text
    assert "工作区" in res.json()["detail"]

"""群组（群聊）REST：CRUD / 绑定 / 解绑 / 跨工作区 / 共享成员。"""
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
    assert team["kind"] == "group"
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


def test_bind_allows_cross_workspace_group(client, tmp_path):
    """群组独立于工作区：绑定后会话跟随群组工作区。"""
    folder = tmp_path / "other-ws"
    folder.mkdir()
    other = client.post(f"{API}/workspaces", json={
        "name": "另一个空间", "path": str(folder)})
    assert other.status_code == 200, other.text
    other_ws = other.json()["data"]["id"]

    team = client.post(f"{API}/teams", json={
        "workspace_id": other_ws, "name": "外部群", "mode": "orchestrate",
        "leader": {"name": "主持"}, "members": [{"name": "成员"}]}).json()["data"]
    # 不显式指定工作区的会话才是「跟随」状态；手动指定的会话保留自己的选择。
    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    res = client.post(f"{API}/teams/{team['id']}/bind",
                      json={"conversation_id": cid})
    assert res.status_code == 200, res.text
    conv = client.get(f"{API}/conversations/{cid}").json()["data"]
    assert conv["team_id"] == team["id"]
    assert conv["workspace_id"] == other_ws


def test_group_edits_are_live_across_bound_conversations(client):
    """群组是共享实体：改成员后，所有绑定会话读到的是同一份花名册。"""
    ws = _default_ws(client)
    team = client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "共享群",
        "leader": {"name": "主持"}, "members": [{"name": "甲"}],
    }).json()["data"]
    first = client.post(f"{API}/conversations", json={"team_id": team["id"]}).json()["data"]
    second = client.post(f"{API}/conversations", json={"team_id": team["id"]}).json()["data"]

    added = client.post(f"{API}/roles", json={"name": "乙"}).json()["data"]
    detail = client.get(f"{API}/teams/{team['id']}").json()["data"]
    patched = client.patch(f"{API}/teams/{team['id']}", json={
        "members": [*detail["members"], {"id": added["id"], "name": added["name"]}],
    })
    assert patched.status_code == 200, patched.text
    names = [m["name"] for m in patched.json()["data"]["members"]]
    assert names == ["甲", "乙"]

    for cid in (first["id"], second["id"]):
        current = client.get(f"{API}/conversations/{cid}/team").json()["data"]["team"]
        assert [m["name"] for m in current["members"]] == ["甲", "乙"]


def test_private_participants_team_is_not_a_group(client):
    """participants-* 只服务会话，不出现在默认群组列表，也不能走群组路由。"""
    role = client.post(f"{API}/roles", json={"name": "私有成员"}).json()["data"]
    conv = client.post(f"{API}/conversations", json={}).json()["data"]
    result = client.put(f"{API}/conversations/{conv['id']}/participants", json={
        "role_ids": [role["id"]],
    }).json()["data"]
    private_id = result["team"]["id"]
    assert private_id.startswith("participants-")
    assert result["team"]["kind"] == "participants"

    groups = client.get(f"{API}/teams").json()["data"]["items"]
    assert private_id not in {t["id"] for t in groups}
    all_teams = client.get(f"{API}/teams?kind=all").json()["data"]["items"]
    assert private_id in {t["id"] for t in all_teams}
    assert client.get(f"{API}/teams/{private_id}").status_code == 404


def test_delete_group_unbinds_and_revokes_group_grants(client):
    ws = _default_ws(client)
    team = client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "授权群",
        "leader": {"name": "主持"}, "members": [{"name": "成员"}],
    }).json()["data"]
    cid = client.post(f"{API}/conversations", json={"team_id": team["id"]}).json()["data"]["id"]

    from services.agent_zero.task_layer import permissions

    permissions.add_scope_grant("team", "shell", team_id=team["id"])
    assert client.get(f"{API}/app/permissions/grants?team_id={team['id']}").json()["data"]["items"]

    assert client.delete(f"{API}/teams/{team['id']}").status_code == 200
    assert client.get(f"{API}/conversations/{cid}").json()["data"]["team_id"] in (None, "")
    assert client.get(f"{API}/app/permissions/grants?team_id={team['id']}").json()["data"]["items"] == []


def test_conversation_list_filters_by_team_and_role(client):
    """群组 / 角色卡片点击要复用已有会话：列表必须支持按 team_id / role_id 过滤。"""
    ws = _default_ws(client)
    role = client.post(f"{API}/roles", json={"name": "过滤角色"}).json()["data"]
    single = client.post(f"{API}/conversations", json={
        "role_id": role["id"], "workspace_id": ws,
    }).json()["data"]
    team = client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "过滤群",
        "leader": {"name": "主持"}, "members": [{"name": "成员"}],
    }).json()["data"]
    group = client.post(f"{API}/conversations", json={"team_id": team["id"]}).json()["data"]

    by_role = client.get(f"{API}/conversations", params={
        "role_id": role["id"], "limit": 50,
    }).json()["data"]["items"]
    assert single["id"] in {c["id"] for c in by_role}
    assert group["id"] not in {c["id"] for c in by_role}

    by_team = client.get(f"{API}/conversations", params={
        "team_id": team["id"], "limit": 20,
    }).json()["data"]["items"]
    assert [c["id"] for c in by_team] == [group["id"]]

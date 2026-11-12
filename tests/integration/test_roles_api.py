"""V3 角色库 API：持久 / 临时 / LLM 来源 / 会话删除清理。"""
from __future__ import annotations

API = "/api/agent-zero"


def _conv(client) -> str:
    return client.post(f"{API}/conversations", json={}).json()["data"]["id"]


def test_role_crud_and_tools(client):
    created = client.post(f"{API}/roles", json={
        "name": "审阅者", "expertise": "只读审阅",
        "tools": ["read_file", "shell", "not_a_tool"],
        "model_code": "fast",
    })
    assert created.status_code == 200, created.text
    role = created.json()["data"]
    assert role["scope"] == "persistent" and role["source"] == "user"
    assert "read_file" in role["tools"] and "shell" in role["tools"]
    assert "not_a_tool" not in role["tools"]

    listed = client.get(f"{API}/roles").json()["data"]["items"]
    assert any(r["id"] == role["id"] for r in listed)

    patched = client.patch(f"{API}/roles/{role['id']}", json={
        "name": "审阅者 2", "tools": ["read_file"]}).json()["data"]
    assert patched["name"] == "审阅者 2"
    assert patched["tools"] == ["read_file"]

    assert client.delete(f"{API}/roles/{role['id']}").status_code == 200
    assert client.patch(f"{API}/roles/{role['id']}", json={"name": "x"}).status_code == 404


def test_role_can_switch_workspace_independently_of_single_chat(client, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ws_a = client.post(f"{API}/workspaces",
                       json={"name": "A", "path": str(tmp_path / "a")}).json()["data"]
    ws_b = client.post(f"{API}/workspaces",
                       json={"name": "B", "path": str(tmp_path / "b")}).json()["data"]

    role = client.post(f"{API}/roles", json={
        "name": "换工作区", "workspace_id": ws_a["id"]}).json()["data"]
    assert role["workspace_id"] == ws_a["id"]

    conv = client.post(f"{API}/conversations", json={
        "role_id": role["id"], "workspace_id": ws_a["id"],
    }).json()["data"]
    assert conv["workspace_id"] == ws_a["id"]

    patched = client.patch(f"{API}/roles/{role['id']}",
                           json={"workspace_id": ws_b["id"]}).json()["data"]
    assert patched["workspace_id"] == ws_b["id"]
    moved = client.get(f"{API}/conversations/{conv['id']}").json()["data"]
    assert moved["workspace_id"] == ws_a["id"]

    # 空字符串 = 解除工作区绑定。
    cleared = client.patch(f"{API}/roles/{role['id']}",
                           json={"workspace_id": ""}).json()["data"]
    assert cleared["workspace_id"] == ""


def test_temporary_role_scope_and_promote(client):
    cid = _conv(client)
    temp = client.post(f"{API}/roles", json={
        "name": "调研员", "scope": "temporary", "session_id": cid,
        "source": "llm", "tools": ["web"]}).json()["data"]
    assert temp["scope"] == "temporary" and temp["session_id"] == cid

    default_list = client.get(f"{API}/roles").json()["data"]["items"]
    assert all(r["scope"] != "temporary" for r in default_list)

    scoped = client.get(f"{API}/roles",
                        params={"include_temporary": "true", "session_id": cid}
                        ).json()["data"]["items"]
    assert any(r["id"] == temp["id"] for r in scoped)

    promoted = client.post(f"{API}/roles/{temp['id']}/promote").json()["data"]
    assert promoted["scope"] == "persistent"
    assert promoted["session_id"] == ""


def test_temporary_role_is_removed_with_conversation(client):
    cid = _conv(client)
    temp = client.post(f"{API}/roles", json={
        "name": "临时-删除用例", "scope": "temporary", "session_id": cid}).json()["data"]
    assert client.delete(f"{API}/conversations/{cid}").status_code == 200
    listed = client.get(f"{API}/roles", params={
        "include_temporary": "true", "session_id": cid}).json()["data"]["items"]
    assert all(r["id"] != temp["id"] for r in listed)


def test_temporary_role_requires_session(client):
    res = client.post(f"{API}/roles", json={"name": "无归属", "scope": "temporary"})
    assert res.status_code == 400
    assert "session_id" in res.json()["detail"]


def test_default_role_cannot_be_deleted(client):
    assert client.delete(f"{API}/roles/default").status_code == 400


def test_fixed_role_model_overrides_without_replacing_conversation_preference(client):
    from services.agent_zero.task_layer.conversation import chat_model_code

    role = client.post(f"{API}/roles", json={
        "name": "固定模型角色", "model_code": "fast",
        "tools": ["read_file"],
    }).json()["data"]
    conv = client.post(f"{API}/conversations", json={"role_id": role["id"]}).json()["data"]
    assert not conv["model_code"]
    assert chat_model_code(conv) == "fast"

    response = client.put(f"{API}/conversations/{conv['id']}/preferences",
                          json={"model_code": "another"})
    assert response.status_code == 200
    selected = client.get(f"{API}/conversations/{conv['id']}").json()["data"]
    assert selected["model_code"] == "another"
    assert chat_model_code(selected) == "fast"

    response = client.patch(f"{API}/roles/{role['id']}", json={"model_code": ""})
    assert response.status_code == 200
    assert chat_model_code(selected) == "another"

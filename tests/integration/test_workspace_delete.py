from __future__ import annotations

API = "/api/agent-zero"


def test_delete_hides_and_readd_restores(client, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    created = client.post(f"{API}/workspaces", json={"path": str(folder)})
    assert created.status_code == 200, created.text
    ws = created.json()["data"]
    ws_id = ws["id"]

    deleted = client.delete(f"{API}/workspaces/{ws_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["archived"] is True

    listed = client.get(f"{API}/workspaces").json()["data"]["items"]
    assert all(w["id"] != ws_id for w in listed), "删除后不应出现在默认列表"

    again = client.post(f"{API}/workspaces", json={"path": str(folder)})
    assert again.status_code == 200, again.text
    restored = again.json()["data"]
    assert restored["id"] == ws_id
    assert restored.get("restored") is True
    assert restored["archived"] is False


def test_legacy_workspace_is_not_public(client):
    from services.agent_zero.stores import zero_store

    resp = client.delete(f"{API}/workspaces/{zero_store.DEFAULT_WORKSPACE_ID}")
    assert resp.status_code == 404


def test_workspace_list_includes_hidden_directories(client, tmp_path):
    folder = tmp_path / "hidden-demo"
    folder.mkdir()
    (folder / ".hidden_dir").mkdir()
    (folder / ".env").write_text("KEY=1\n", encoding="utf-8")
    (folder / "visible.txt").write_text("x\n", encoding="utf-8")
    ws = client.post(f"{API}/workspaces", json={"path": str(folder)}).json()["data"]

    listed = client.get(f"{API}/workspace/list",
                        params={"workspace_id": ws["id"], "path": "."}).json()["data"]
    names = {e["name"] for e in listed}
    assert ".hidden_dir" in names
    assert ".env" in names
    assert "visible.txt" in names

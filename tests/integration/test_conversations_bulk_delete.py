from __future__ import annotations

API = "/api/agent-zero"


def _make_conv(client, ws_id: str) -> str:
    resp = client.post(f"{API}/conversations", json={"workspace_id": ws_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_delete_all_conversations_requires_confirm(client):
    ws = client.get(f"{API}/workspaces").json()["data"]["items"][0]
    resp = client.delete(f"{API}/conversations", params={"workspace_id": ws["id"]})
    assert resp.status_code == 400
    assert "confirm" in resp.text


def test_delete_all_conversations_clears_workspace(client, tmp_path):
    folder = tmp_path / "bulk"
    folder.mkdir()
    ws = client.post(f"{API}/workspaces", json={"path": str(folder)}).json()["data"]
    ids = [_make_conv(client, ws["id"]) for _ in range(3)]

    listed = client.get(f"{API}/conversations",
                        params={"workspace_id": ws["id"]}).json()["data"]
    assert listed["total"] == 3

    resp = client.delete(f"{API}/conversations",
                         params={"workspace_id": ws["id"], "confirm": True})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["deleted"] == 3
    assert data["total"] == 3

    after = client.get(f"{API}/conversations",
                       params={"workspace_id": ws["id"]}).json()["data"]
    assert after["total"] == 0
    for cid in ids:
        assert client.get(f"{API}/conversations/{cid}").status_code == 404


def test_delete_all_conversations_is_scoped_to_workspace(client, tmp_path):
    keep_dir = tmp_path / "keep"
    keep_dir.mkdir()
    keep = client.post(f"{API}/workspaces", json={"path": str(keep_dir)}).json()["data"]
    drop_dir = tmp_path / "drop"
    drop_dir.mkdir()
    drop = client.post(f"{API}/workspaces", json={"path": str(drop_dir)}).json()["data"]

    keep_id = _make_conv(client, keep["id"])
    _make_conv(client, drop["id"])

    resp = client.delete(f"{API}/conversations",
                         params={"workspace_id": drop["id"], "confirm": True})
    assert resp.status_code == 200, resp.text

    assert client.get(f"{API}/conversations/{keep_id}").status_code == 200
    left = client.get(f"{API}/conversations",
                      params={"workspace_id": drop["id"]}).json()["data"]
    assert left["total"] == 0


def test_delete_all_conversations_with_empty_workspace(client, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    ws = client.post(f"{API}/workspaces", json={"path": str(folder)}).json()["data"]
    resp = client.delete(f"{API}/conversations",
                         params={"workspace_id": ws["id"], "confirm": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] == 0

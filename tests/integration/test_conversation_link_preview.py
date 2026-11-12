API = "/api/agent-zero"


def test_link_preview_is_scoped_to_the_conversation_workspace(client, tmp_path):
    folder = tmp_path / "workspace"
    folder.mkdir()
    report = folder / "报告.md"
    report.write_text("# 预览内容", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (folder / "escape.md").symlink_to(outside)

    ws = client.post(f"{API}/workspaces", json={"path": str(folder)}).json()["data"]
    conv = client.post(f"{API}/conversations", json={"workspace_id": ws["id"]}).json()["data"]
    endpoint = f"{API}/conversations/{conv['id']}/link-file"
    for path in (report.name, str(report)):
        response = client.get(endpoint + "/preview", params={"path": path})
        assert response.json()["data"]["text"] == "# 预览内容"
    for path in ("../outside.md", str(outside), "escape.md"):
        assert client.get(endpoint + "/preview", params={"path": path}).status_code == 404

    audio = folder / "preview.mp3"
    audio.write_bytes(b"test media")
    response = client.get(endpoint, params={"path": audio.name}, headers={"Range": "bytes=0-3"})
    assert response.status_code == 206
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"test"
    download = client.get(endpoint, params={"path": audio.name, "download": True})
    assert download.headers["content-disposition"].startswith("attachment")


def test_window_layout_persists_without_touching_other_conversations(client):
    one = client.post(f"{API}/conversations", json={}).json()["data"]
    two = client.post(f"{API}/conversations", json={}).json()["data"]
    url = f"{API}/conversations/{one['id']}/window-layout"
    popup = {"x": 80, "y": 44, "w": 600, "h": 460}

    assert client.patch(url, json={"popup": popup}).status_code == 200
    assert client.patch(url, json={"presentation": "drawer"}).status_code == 200
    assert client.get(url).json()["data"] == {"presentation": "drawer", "popup": popup}
    assert client.get(f"{API}/conversations/{two['id']}/window-layout").json()["data"] == {}
    assert client.patch(url, json={"presentation": "invalid"}).status_code == 422

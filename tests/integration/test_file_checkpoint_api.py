"""文件检查点 API：列表 / 差异 / 回退（文件 + 对话）。"""
from __future__ import annotations

import pytest

from services.agent_zero.services import file_checkpoint_service
from services.agent_zero.task_layer import conversation
from services.shared.auth import LOCAL_USER_ID

API = "/api/agent-zero"


@pytest.fixture()
def env(client, tmp_path, monkeypatch):
    monkeypatch.setattr(file_checkpoint_service, "storage_root",
                        lambda: tmp_path / "checkpoints")
    folder = tmp_path / "project"
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "app.py").write_text("print('one')\n", encoding="utf-8")
    res = client.post(f"{API}/workspaces", json={
        "name": "checkpoint-ws", "path": str(folder)})
    assert res.status_code == 200, res.text
    ws_id = res.json()["data"]["id"]
    conv = client.post(f"{API}/conversations",
                       json={"workspace_id": ws_id}).json()["data"]
    return {"client": client, "folder": folder, "ws_id": ws_id,
            "conv_id": conv["id"], "tmp": tmp_path}


def _messages(client, conv_id):
    res = client.get(f"{API}/conversations/{conv_id}/messages")
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_manual_checkpoint_endpoint(env):
    client, ws_id, conv_id = env["client"], env["ws_id"], env["conv_id"]
    msg = conversation.send_message(conv_id, "手动打一个检查点", LOCAL_USER_ID)
    res = client.post(f"{API}/conversations/{conv_id}/checkpoints", json={
        "message_id": msg["id"], "label": "手动检查点"})
    assert res.status_code == 200, res.text
    cp = res.json()["data"]
    assert cp["message_id"] == msg["id"]
    assert cp["label"] == "手动检查点"
    items = client.get(
        f"{API}/conversations/{conv_id}/checkpoints").json()["data"]["items"]
    assert [c["id"] for c in items] == [cp["id"]]
    assert (file_checkpoint_service.storage_root() / ws_id
            / "manifests" / f"{cp['id']}.json").exists()


def test_rewind_restores_files_and_truncates_conversation(env):
    client, folder = env["client"], env["folder"]
    conv_id, ws_id = env["conv_id"], env["ws_id"]

    first = conversation.send_message(conv_id, "改一下 app.py", LOCAL_USER_ID)
    later = conversation.send_message(conv_id, "再改一次", LOCAL_USER_ID)
    cp = file_checkpoint_service.snapshot(
        ws_id, conversation_id=conv_id, message_id=first["id"], label="改一下 app.py")

    (folder / "src" / "app.py").write_text("print('two')\n", encoding="utf-8")
    (folder / "created.txt").write_text("new\n", encoding="utf-8")

    res = client.get(f"{API}/conversations/{conv_id}/checkpoints")
    assert res.status_code == 200, res.text
    items = res.json()["data"]["items"]
    assert [c["id"] for c in items] == [cp["id"]]
    assert items[0]["message_present"] is True

    res = client.get(
        f"{API}/conversations/{conv_id}/checkpoints/{cp['id']}/diff")
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["counts"] == {"added": 1, "modified": 1, "deleted": 0}

    res = client.post(
        f"{API}/conversations/{conv_id}/checkpoints/{cp['id']}/rewind",
        json={"files": True, "conversation": True, "delete_added": True})
    assert res.status_code == 200, res.text
    result = res.json()["data"]
    assert result["files"]["restored"] == 1
    assert result["files"]["deleted"] == 1
    assert result["conversation"]["messages_removed"] == 2

    assert (folder / "src" / "app.py").read_text(encoding="utf-8") == "print('one')\n"
    assert not (folder / "created.txt").exists()

    remaining = _messages(client, conv_id)
    assert remaining == []
    assert client.get(
        f"{API}/conversations/{conv_id}/checkpoints").json()["data"]["items"] == []
    assert later["id"]


def test_file_only_rewind_keeps_conversation(env):
    client, folder = env["client"], env["folder"]
    conv_id, ws_id = env["conv_id"], env["ws_id"]
    msg = conversation.send_message(conv_id, "第一个问题", LOCAL_USER_ID)
    cp = file_checkpoint_service.snapshot(
        ws_id, conversation_id=conv_id, message_id=msg["id"], label="第一个问题")
    (folder / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")

    res = client.post(
        f"{API}/conversations/{conv_id}/checkpoints/{cp['id']}/rewind",
        json={"files": True, "conversation": False})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["conversation"] is None
    assert len(_messages(client, conv_id)) == 1
    assert (folder / "src" / "app.py").read_text(encoding="utf-8") == "print('one')\n"


def test_rewind_rejects_unknown_checkpoint(env):
    client, conv_id = env["client"], env["conv_id"]
    res = client.post(
        f"{API}/conversations/{conv_id}/checkpoints/ckpt-nope/rewind",
        json={"files": True, "conversation": True})
    assert res.status_code == 404

    msg = conversation.send_message(conv_id, "x", LOCAL_USER_ID)
    cp = file_checkpoint_service.snapshot(
        env["ws_id"], conversation_id=conv_id, message_id=msg["id"], label="x")
    res = client.post(
        f"{API}/conversations/{conv_id}/checkpoints/{cp['id']}/rewind",
        json={"files": False, "conversation": False})
    assert res.status_code == 400


def test_checkpoints_are_pruned_when_message_is_recalled(env):
    client, ws_id, conv_id = env["client"], env["ws_id"], env["conv_id"]
    msg = conversation.send_message(conv_id, "会被撤回", LOCAL_USER_ID)
    cp = file_checkpoint_service.snapshot(
        ws_id, conversation_id=conv_id, message_id=msg["id"], label="会被撤回")
    assert client.get(
        f"{API}/conversations/{conv_id}/checkpoints").json()["data"]["items"]

    res = client.post(
        f"{API}/conversations/{conv_id}/messages/{msg['id']}/recall")
    assert res.status_code == 200, res.text
    items = client.get(
        f"{API}/conversations/{conv_id}/checkpoints").json()["data"]["items"]
    assert items == []
    assert not (file_checkpoint_service.storage_root() / ws_id
                / "manifests" / f"{cp['id']}.json").exists()

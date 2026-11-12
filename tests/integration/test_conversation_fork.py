"""会话分支：从某条消息复制历史与附件，原会话不受影响。"""
from __future__ import annotations

import base64
import time

import pytest

from services.agent_zero.runtime.zero.gdag.store import GdagStore
from services.agent_zero.services import attachment_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation
from services.shared.auth import LOCAL_USER_ID

API = "/api/agent-zero"


@pytest.fixture()
def env(client, tmp_path):
    folder = tmp_path / "fork-ws"
    folder.mkdir()
    (folder / "README.md").write_text("hello\n", encoding="utf-8")
    res = client.post(f"{API}/workspaces", json={
        "name": "fork", "path": str(folder)})
    assert res.status_code == 200, res.text
    ws_id = res.json()["data"]["id"]
    conv = client.post(f"{API}/conversations",
                       json={"workspace_id": ws_id}).json()["data"]
    return {"client": client, "ws_id": ws_id, "conv_id": conv["id"]}


def _mk_message(conv_id: str, ws_id: str, content: str, by: str = "user") -> str:
    node_id = zero_store.new_id("msg")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default",
        "props": {
            "conversation_id": conv_id,
            "workspace_id": ws_id,
            "content": content,
            "stage": "exploration",
            "by": by,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "queued": 0,
        },
        "confidence": 0.5,
    })
    return node_id


def test_fork_at_message_copies_prefix_and_keeps_both_lists(env):
    client, conv_id, ws_id = env["client"], env["conv_id"], env["ws_id"]
    first = conversation.send_message(conv_id, "第一条", LOCAL_USER_ID)
    second = _mk_message(conv_id, ws_id, "第一轮回答", "assistant")
    conversation.send_message(conv_id, "第二条", LOCAL_USER_ID)

    res = client.post(f"{API}/conversations/{conv_id}/fork",
                      json={"message_id": second})
    assert res.status_code == 200, res.text
    forked = res.json()["data"]
    assert forked["id"] != conv_id
    assert forked["workspace_id"] == ws_id
    assert forked["forked_from_id"] == conv_id
    assert forked["forked_from_message_id"] == second

    new_id = forked["id"]
    copied = conversation.list_messages(new_id)
    assert [m["content"] for m in copied] == ["第一条", "第一轮回答"]
    assert [m["id"] for m in copied] != [first, second]   # 消息节点是新的
    assert len(conversation.list_messages(conv_id)) == 3

    # 分支里继续发消息不影响原会话
    conversation.send_message(new_id, "分支里的问题", LOCAL_USER_ID)
    assert len(conversation.list_messages(new_id)) == 3
    assert len(conversation.list_messages(conv_id)) == 3


def test_fork_without_message_copies_whole_conversation(env):
    client, conv_id, ws_id = env["client"], env["conv_id"], env["ws_id"]
    conversation.send_message(conv_id, "a", LOCAL_USER_ID)
    _mk_message(conv_id, ws_id, "b", "assistant")
    conversation.send_message(conv_id, "c", LOCAL_USER_ID)

    res = client.post(f"{API}/conversations/{conv_id}/fork", json={})
    assert res.status_code == 200, res.text
    forked = res.json()["data"]
    assert len(conversation.list_messages(forked["id"])) == 3
    assert forked["forked_from_message_id"] in (None, "")


def test_fork_copies_attachments(env):
    client, conv_id = env["client"], env["conv_id"]
    payload = base64.b64encode("附件正文".encode("utf-8")).decode("ascii")
    atts = attachment_service.process_uploads(
        conv_id, [{"name": "note.txt", "data": payload}], allow_images=True)
    msg = conversation.send_message(
        conv_id, "带附件的消息", LOCAL_USER_ID, attachments=atts)["id"]

    res = client.post(f"{API}/conversations/{conv_id}/fork",
                      json={"message_id": msg})
    assert res.status_code == 200, res.text
    new_id = res.json()["data"]["id"]
    assert attachment_service.read_attachment_text(new_id, atts[0]) == "附件正文"


def test_fork_rejects_unknown_message(env):
    client, conv_id = env["client"], env["conv_id"]
    conversation.send_message(conv_id, "x", LOCAL_USER_ID)
    res = client.post(f"{API}/conversations/{conv_id}/fork",
                      json={"message_id": "msg-nope"})
    assert res.status_code == 400
    assert "消息不存在" in res.text


def test_fork_rejects_empty_conversation(env):
    client, conv_id = env["client"], env["conv_id"]
    res = client.post(f"{API}/conversations/{conv_id}/fork", json={})
    assert res.status_code == 400
    assert "没有可复制" in res.text


def test_fork_copies_private_participants(env):
    """分支不能和原会话共用 participants-*：复制一份新的私有参与角色。"""
    client, ws_id, conv_id = env["client"], env["ws_id"], env["conv_id"]
    role = client.post(f"{API}/roles", json={"name": "分支成员"}).json()["data"]
    updated = client.put(f"{API}/conversations/{conv_id}/participants", json={
        "role_ids": [role["id"]],
    }).json()["data"]
    original_team = updated["team"]["id"]
    _mk_message(conv_id, ws_id, "用户消息")

    forked = client.post(f"{API}/conversations/{conv_id}/fork", json={}).json()["data"]
    assert forked["team_id"] and forked["team_id"] != original_team
    team = client.get(f"{API}/conversations/{forked['id']}/team").json()["data"]["team"]
    assert team["kind"] == "participants"
    assert [m["name"] for m in team["members"]] == ["分支成员"]

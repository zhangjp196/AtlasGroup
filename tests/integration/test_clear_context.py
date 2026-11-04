"""`/clear`：清空模型上下文，但历史消息仍保留、仍可回看。"""
from __future__ import annotations

from services.agent_zero.task_layer import conversation

API = "/api/agent-zero"


def _conv(client) -> str:
    return client.post(f"{API}/conversations", json={}).json()["data"]["id"]


def test_clear_current_context_keeps_transcript(client):
    cid = _conv(client)
    conversation.send_message(cid, "清空前的旧消息", "local")
    assert conversation._history_messages(cid)

    res = client.post(f"{API}/conversations/{cid}/clear", json={"scope": "current"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["cleared"] == 1
    assert conversation._history_messages(cid) == []

    # 历史消息仍在转录里，可以继续查看；清空标记本身不显示。
    messages = conversation.list_messages(cid)
    assert any(m["content"] == "清空前的旧消息" for m in messages)
    assert any(m.get("kind") == conversation.CLEAR_KIND for m in messages)

    # 清空后的新消息重新进入模型上下文。
    conversation.send_message(cid, "清空后的新消息", "local")
    history = conversation._history_messages(cid)
    assert len(history) == 1
    assert "清空后的新消息" in history[0]["content"]


def test_clear_all_conversations(client):
    a = _conv(client)
    b = _conv(client)
    conversation.send_message(a, "A 的旧消息", "local")
    conversation.send_message(b, "B 的旧消息", "local")

    res = client.post(f"{API}/conversations/{a}/clear", json={"scope": "all"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["cleared"] >= 2
    assert conversation._history_messages(a) == []
    assert conversation._history_messages(b) == []
    assert any(m["content"] == "A 的旧消息" for m in conversation.list_messages(a))
    assert any(m["content"] == "B 的旧消息" for m in conversation.list_messages(b))

"""会话指令（`session_manage`）：用户用自然语言触发的会话动作。

清空上下文由后端立即执行（历史消息保留）；新建 / 重命名 / 删除 / 主题等
界面动作返回 done=False，由前端复用斜杠命令的同一套逻辑执行。
"""
from __future__ import annotations

from services.agent_zero.task_layer import conversation
from services.agent_zero.tools import registry

API = "/api/agent-zero"


def _conv(client) -> dict:
    return client.post(f"{API}/conversations", json={}).json()["data"]


def test_tool_is_registered_and_resolvable():
    assert registry.get_tool("session_manage") is not None
    # 旧名 / 别名归一到同一个实现
    assert registry.canonical("session_command") == "session_manage"
    assert registry.canonical("conversation_command") == "session_manage"


def test_clear_current_runs_in_backend(client):
    conv = _conv(client)
    conversation.send_message(conv["id"], "清空前的旧消息", "local")
    assert conversation._history_messages(conv["id"])

    out = conversation._exec_session_manage(
        conversation.get_conversation(conv["id"]),
        {"action": "clear", "scope": "current"})
    assert out["ok"] is True and out["done"] is True
    assert out["cleared"] == 1

    # 模型上下文清空，但转录仍能回看。
    assert conversation._history_messages(conv["id"]) == []
    assert any(m["content"] == "清空前的旧消息"
               for m in conversation.list_messages(conv["id"]))


def test_clear_all_runs_in_backend(client):
    a = _conv(client)
    b = _conv(client)
    conversation.send_message(a["id"], "A 的旧消息", "local")
    conversation.send_message(b["id"], "B 的旧消息", "local")

    out = conversation._exec_session_manage(
        conversation.get_conversation(a["id"]),
        {"action": "clear", "scope": "all"})
    assert out["ok"] is True and out["done"] is True
    assert out["cleared"] >= 2
    assert conversation._history_messages(a["id"]) == []
    assert conversation._history_messages(b["id"]) == []


def test_ui_actions_are_forwarded_to_frontend(client):
    conv = conversation.get_conversation(_conv(client)["id"])
    for action in ("new", "compact", "delete", "clean", "stop", "theme",
                   "settings", "read"):
        out = conversation._exec_session_manage(conv, {"action": action})
        assert out["ok"] is True, out
        assert out["done"] is False, out
        assert out["action"] == action


def test_rename_passes_title_through(client):
    conv = conversation.get_conversation(_conv(client)["id"])
    out = conversation._exec_session_manage(
        conv, {"action": "rename", "title": "新的标题"})
    assert out["ok"] is True and out["done"] is False
    assert out["arg"] == "新的标题"

    missing = conversation._exec_session_manage(conv, {"action": "rename"})
    assert missing.get("error")


def test_unknown_action_is_rejected(client):
    conv = conversation.get_conversation(_conv(client)["id"])
    out = conversation._exec_session_manage(conv, {"action": "launch_missiles"})
    assert "未知会话指令" in out["error"]

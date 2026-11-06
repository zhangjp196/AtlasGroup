"""权限档位与审批按钮：session / always 必须真正生效。"""
from __future__ import annotations

from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation, permissions


def _init(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_WORKSPACE_ID


def test_session_mode_allows_all_tools_in_conversation(app_env):
    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    cid = conv["id"]

    permissions.set_conversation_mode(cid, "once")
    assert permissions.decide(cid, "shell", {"cmd": "rm -rf build"})["ask"] is True

    permissions.set_conversation_mode(cid, "session")
    assert permissions.decide(cid, "shell", {"cmd": "rm -rf build"})["ask"] is False
    assert permissions.decide(cid, "write_file", {"path": "a.txt"})["ask"] is False
    # 强制确认（建群）不受 session / always 档位影响。
    assert permissions.decide(cid, "team_manage", {"action": "create"})["ask"] is True


def test_always_mode_allows_high_risk_tools(app_env):
    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    cid = conv["id"]
    permissions.set_conversation_mode(cid, "always")
    assert permissions.decide(cid, "shell", {"cmd": "rm -rf build"})["ask"] is False


def test_approve_session_button_is_effective(app_env):
    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    cid = conv["id"]
    permissions.set_conversation_mode(cid, "once")

    approval = permissions.create_approval(
        cid, None, "shell", {"cmd": "rm -rf build"}, "high", ["删除文件"])
    permissions.decide_approval(approval["id"], "approve_session")
    # 只授权该工具：同工具不再问，其他工具仍按 once 询问。
    assert permissions.decide(cid, "shell", {"cmd": "rm -rf build"})["ask"] is False
    assert permissions.decide(cid, "write_file", {"path": "a.txt"})["ask"] is True


def test_approve_always_button_is_effective(app_env):
    ws = _init(app_env)
    conv = conversation.create_conversation(ws, "local")
    cid = conv["id"]
    permissions.set_conversation_mode(cid, "once")

    approval = permissions.create_approval(
        cid, None, "shell", {"cmd": "rm -rf build"}, "high", ["删除文件"])
    permissions.decide_approval(approval["id"], "approve_always")
    assert permissions.get_conversation_mode(cid) == "always"
    assert permissions.decide(cid, "write_file", {"path": "a.txt"})["ask"] is False

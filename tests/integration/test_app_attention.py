"""全局 Agent：跨会话待授权 / 未读 / 运行 / 授权策略。"""
from __future__ import annotations

API = "/api/agent-zero"


def test_attention_returns_global_shape(client):
    res = client.get(f"{API}/app/attention")
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    counts = data["counts"]
    for key in ("approvals", "questions", "authorizations", "unread", "running", "sessions"):
        assert key in counts
    assert isinstance(data["approvals"], list)
    assert isinstance(data["questions"], list)
    assert isinstance(data["sessions"], list)
    assert data["policy"]["default"] in ("auto", "once", "session", "always")


def test_attention_reflects_session_permission(client):
    conv = client.post(f"{API}/conversations", json={}).json()["data"]
    cid = conv["id"]
    updated = client.put(f"{API}/conversations/{cid}/permission",
                         json={"mode": "auto"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["mode"] == "auto"

    data = client.get(f"{API}/app/attention").json()["data"]
    row = next((s for s in data["sessions"] if s["id"] == cid), None)
    assert row is not None
    assert row["permission_mode"] == "auto"
    assert data["counts"]["sessions"] >= 1


def test_global_default_permission(client):
    res = client.put(f"{API}/app/permissions", json={"default": "auto"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["policy"]["default"] == "auto"
    attention = client.get(f"{API}/app/attention").json()["data"]
    assert attention["policy"]["default"] == "auto"


def test_global_default_applies_to_new_conversation(client):
    conv = client.post(f"{API}/conversations", json={}).json()["data"]
    client.put(f"{API}/conversations/{conv['id']}/permission", json={"mode": "auto"})
    # 显式改全局默认后，新会话跟着全局默认，而不是上一条会话
    client.put(f"{API}/app/permissions", json={"default": "once"})
    fresh = client.post(f"{API}/conversations", json={}).json()["data"]
    assert fresh["permission_mode"] == "once"
    attention = client.get(f"{API}/app/attention").json()["data"]
    assert attention["policy"]["default"] == "once"


def test_global_agent_is_a_real_conversation(client):
    conv = client.post(f"{API}/conversations", json={"zone": "global-agent"}).json()["data"]
    assert conv["zone"] == "global-agent"
    fetched = client.get(f"{API}/conversations/{conv['id']}").json()["data"]
    assert fetched["zone"] == "global-agent"
    # 全局 Agent 自己不出现在「会话授权 / 未读」列表里
    data = client.get(f"{API}/app/attention").json()["data"]
    assert all(s["id"] != conv["id"] for s in data["sessions"])


def test_global_agent_tool_is_registered_and_reads_status(client):
    from services.agent_zero.services import attention_service
    from services.agent_zero.task_layer import conversation
    from services.agent_zero.tools import registry

    tool = registry.get_tool("app_manage")
    assert tool is not None
    assert "status" in (tool.parameters.get("properties", {})
                        .get("action", {}).get("enum") or [])
    from services.agent_zero.tools import packs

    code, data, err = packs.resolve_call("app_manage", {"action": "status"})
    assert err is None and code == "app_manage" and data["action"] == "status"
    out = conversation._exec_app_manage({"id": "conv-test"}, {"action": "status"})
    assert "counts" in out
    assert out["counts"]["authorizations"] == 0
    assert "全局 Agent" in conversation._global_agent_addon()
    snap = attention_service.snapshot()
    assert set(("approvals", "questions", "unread", "running", "sessions", "policy")) <= set(snap)

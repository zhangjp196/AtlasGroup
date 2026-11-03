from __future__ import annotations

from services.agent_zero.services import analysis_service, ask_service
from services.agent_zero.stores import zero_store

API = "/api/agent-zero"


def test_default_role_has_new_tools(client):
    role = zero_store.get_role(zero_store.DEFAULT_ROLE_ID)
    tools = (role.get("envelope") or {}).get("tools") or []
    assert "ask_user" in tools
    assert "save_analysis" in tools


def test_ask_question_route(client):
    q = ask_service.create("conv-x", "", {
        "question": "继续吗？", "options": ["是", "否"], "multi": False,
    })
    resp = client.post(f"{API}/questions/{q['id']}",
                       json={"selected": ["是"], "other": "", "ignored": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["selected"] == ["是"]
    info = client.get(f"{API}/questions/{q['id']}").json()["data"]
    assert info["pending"] is False


def test_analysis_default_path():
    rel = analysis_service.default_relpath("通道状态分析", "report", "md")
    assert rel.startswith(".atlas/reports/") and rel.endswith(".md")
    assert len(rel.split("/")) == 4

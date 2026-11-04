"""设置 → MCP：列表要带上环境变量 / 请求头，编辑面板才能回填。"""
from __future__ import annotations


def _server(**overrides):
    body = {
        "name": "filesystem",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"DATA_DIR": "/tmp/data", "TOKEN": "secret"},
        "headers": {},
        "enabled": True,
    }
    body.update(overrides)
    return body


def test_mcp_list_returns_env_and_headers(client):
    created = client.post("/api/agent-zero/app/mcp/servers", json=_server()).json()["data"]
    listing = client.get("/api/agent-zero/app/mcp").json()["data"]
    row = next(s for s in listing["servers"] if s["id"] == created["id"])
    assert row["env"] == {"DATA_DIR": "/tmp/data", "TOKEN": "secret"}
    assert row["headers"] == {}
    assert row["note"] == ""


def test_mcp_edit_can_replace_env_and_headers(client):
    created = client.post("/api/agent-zero/app/mcp/servers", json=_server()).json()["data"]
    updated = client.put(
        f"/api/agent-zero/app/mcp/servers/{created['id']}",
        json={
            "name": "filesystem",
            "type": "http",
            "url": "https://example.test/mcp",
            "env": {},
            "headers": {"Authorization": "Bearer x"},
            "enabled": False,
            "note": "remote",
        },
    ).json()["data"]
    assert updated["type"] == "http"
    assert updated["env"] == {}
    assert updated["headers"] == {"Authorization": "Bearer x"}
    assert updated["enabled"] is False
    listing = client.get("/api/agent-zero/app/mcp").json()["data"]
    row = next(s for s in listing["servers"] if s["id"] == created["id"])
    assert row["headers"] == {"Authorization": "Bearer x"}
    assert row["enabled"] is False
    assert row["note"] == "remote"

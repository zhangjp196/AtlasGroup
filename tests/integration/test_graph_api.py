"""执行画布 API：run 事件里的子代理聚合成 DAG 节点。"""
from __future__ import annotations

import time


def test_graph_aggregates_subagent_events(client):
    conv = client.post("/api/agent-zero/conversations", json={}).json()["data"]
    from services.agent_zero.task_layer import run_service

    run = run_service.create_run(
        conv["id"], "local", role_id="default", content="研究一下",
        mode="autonomous", auto_execute=False,
    )
    now = int(time.time() * 1000)
    run_service.append_event(run["id"], "subagent_event", {"subagent_event": {
        "call_id": "sub-1", "agent": "研究员", "goal": "查资料",
        "ts": now, "event": "tool_call",
        "tool": {"id": "t1", "name": "web", "status": "running"},
    }})
    run_service.append_event(run["id"], "subagent_event", {"subagent_event": {
        "call_id": "sub-1", "agent": "研究员", "goal": "查资料",
        "ts": now + 1000, "event": "progress", "text": "正在读文档",
    }})
    run_service.append_event(run["id"], "subagent_event", {"subagent_event": {
        "call_id": "sub-1", "agent": "研究员", "goal": "查资料",
        "ts": now + 2000, "event": "done",
    }})

    data = client.get(f"/api/agent-zero/conversations/{conv['id']}/graph").json()["data"]
    assert data["root"]["status"] == "queued"
    assert data["stats"]["agents"] >= 2
    node = next(n for n in data["nodes"] if n["id"] == "sub-1")
    assert node["name"] == "研究员"
    assert node["goal"] == "查资料"
    assert node["status"] == "done"
    assert node["tools"][0]["name"] == "web"
    assert "正在读文档" in node["text"]
    assert any(a["agent_id"] == "sub-1" for a in data["activity"])
    assert data["edges"][0]["from"] == "root"

"""从会话 / 任务浮窗直接派发后台任务（POST /conversations/{id}/team-tasks）。"""
from __future__ import annotations

API = "/api/agent-zero"


def _ws(client) -> str:
    rows = client.get(f"{API}/workspaces").json()["data"]["items"]
    row = next((w for w in rows if w.get("is_default")), None)
    return (row or rows[0])["id"]


def test_dispatch_task_to_group_member(client):
    ws = _ws(client)
    team = client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "派发群", "mode": "orchestrate",
        "leader": {"name": "主持"}, "members": [{"name": "后端"}, {"name": "测试"}],
    }).json()["data"]
    cid = client.post(f"{API}/conversations", json={"workspace_id": ws}).json()["data"]["id"]
    client.post(f"{API}/teams/{team['id']}/bind", json={"conversation_id": cid})
    member_id = team["members"][0]["id"]

    res = client.post(f"{API}/conversations/{cid}/team-tasks", json={
        "goal": "补齐用户表索引", "member_id": member_id,
    })
    assert res.status_code == 200, res.text
    task = res.json()["data"]
    assert task["members"][0]["role_id"] == member_id
    assert task["status"] in ("running", "queued")

    # 前台有成员确认 + 工单自动建单
    msgs = client.get(f"{API}/conversations/{cid}/messages").json()["data"]
    assert any(m.get("kind") == "task_ack" for m in msgs)
    works = client.get(f"{API}/work-items", params={"conversation_id": cid}).json()["data"]["items"]
    assert any(w.get("source_ref") == f"team_task:{task['id']}" for w in works)


def test_dispatch_task_requires_member_in_group(client):
    ws = _ws(client)
    team = client.post(f"{API}/teams", json={
        "workspace_id": ws, "name": "缺成员群", "mode": "orchestrate",
        "leader": {"name": "主持"}, "members": [{"name": "后端"}],
    }).json()["data"]
    cid = client.post(f"{API}/conversations", json={"workspace_id": ws}).json()["data"]["id"]
    client.post(f"{API}/teams/{team['id']}/bind", json={"conversation_id": cid})
    res = client.post(f"{API}/conversations/{cid}/team-tasks", json={"goal": "随便做点"})
    assert res.status_code == 400


def test_dispatch_task_in_single_role_conversation(client):
    ws = _ws(client)
    conv = client.post(f"{API}/conversations", json={"workspace_id": ws}).json()["data"]
    res = client.post(f"{API}/conversations/{conv['id']}/team-tasks", json={
        "goal": "跑一个长任务",
    })
    assert res.status_code == 200, res.text
    task = res.json()["data"]
    assert task["members"][0]["role_id"] == str(conv.get("role_id") or "default")

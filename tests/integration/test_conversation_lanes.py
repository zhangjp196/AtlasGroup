"""会话栏目：一个会话根下面挂无限个独立栏目。

锁定三件事：
1. 栏目（session_id）不出现在会话中心（roots_only）里；
2. 按 session_id 能取回根 + 全部栏目；
3. 根会话的未读 / 栏目数按整棵会话聚合，删除根会级联删栏目。
"""
from __future__ import annotations

API = "/api/agent-zero"


def _add_assistant_message(conv_id: str, content: str = "已完成") -> None:
    from services.agent_zero.runtime.zero.gdag.store import GdagStore
    from services.agent_zero.stores import zero_store

    GdagStore().upsert_node({
        "id": zero_store.new_id("msg"),
        "type": "fact",
        "camp": "default",
        "props": {
            "conversation_id": conv_id,
            "content": content,
            "stage": "exploration",
            "by": "assistant",
        },
    })


def test_lanes_live_under_one_session_root(client):
    root = client.post(f"{API}/conversations", json={}).json()["data"]
    child = client.post(f"{API}/conversations", json={"session_id": root["id"]}).json()["data"]
    assert child["id"] != root["id"]
    assert child["session_id"] == root["id"]
    assert child["session_root_id"] == root["id"]

    roots = client.get(f"{API}/conversations?roots_only=1&limit=50").json()["data"]
    root_ids = [item["id"] for item in roots["items"]]
    assert root["id"] in root_ids
    assert child["id"] not in root_ids

    lanes = client.get(
        f"{API}/conversations?session_id={root['id']}&limit=50").json()["data"]
    lane_ids = {item["id"] for item in lanes["items"]}
    assert lane_ids == {root["id"], child["id"]}
    assert lanes["total"] == 2

    detail = client.get(f"{API}/conversations/{root['id']}").json()["data"]
    assert detail["session_root_id"] == root["id"]
    assert detail["thread_count"] == 2


def test_root_unread_counts_the_whole_session(client):
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    root = client.post(f"{API}/conversations", json={}).json()["data"]
    child = client.post(f"{API}/conversations", json={"session_id": root["id"]}).json()["data"]
    # 把栏目的已读水位放到过去，模拟「用户离开后助手才回复」。
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(text(f"UPDATE {_q('conversations')} "
                        f"SET last_read_at = '2000-01-01 00:00:00' WHERE id = :id"),
                   {"id": child["id"]})
        db.commit()
    _add_assistant_message(child["id"])

    rows = client.get(f"{API}/conversations?roots_only=1&limit=50").json()["data"]["items"]
    row = next(item for item in rows if item["id"] == root["id"])
    assert row["thread_count"] == 2
    assert row["unread_count"] >= 1

    client.post(f"{API}/conversations/{root['id']}/read")
    rows = client.get(f"{API}/conversations?roots_only=1&limit=50").json()["data"]["items"]
    row = next(item for item in rows if item["id"] == root["id"])
    assert row["unread_count"] == 0


def test_delete_root_removes_its_lanes(client):
    root = client.post(f"{API}/conversations", json={}).json()["data"]
    child = client.post(f"{API}/conversations", json={"session_id": root["id"]}).json()["data"]

    assert client.delete(f"{API}/conversations/{root['id']}").status_code == 200
    assert client.get(f"{API}/conversations/{child['id']}").status_code == 404

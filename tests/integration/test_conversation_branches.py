"""会话分支：fork 出的会话挂在来源会话下，可按 forked_from_id 反查。

角色 / 群组详情里的「历史会话」和会话管理的「子分支」都依赖这条查询，
所以这里锁定：fork 写入来源指针、列表按来源过滤、总数口径一致。
"""
from __future__ import annotations

API = "/api/agent-zero"


def _mk(client) -> list[str]:
    ids = []
    for _ in range(3):
        ids.append(client.post(f"{API}/conversations", json={}).json()["data"]["id"])
    return ids


def _add_message(conv: dict, content: str = "你好") -> None:
    """直接写一条 exploration 消息节点：不触发 run，测试只关心 fork 的复制。"""
    from services.agent_zero.runtime.zero.gdag.store import GdagStore
    from services.agent_zero.stores import zero_store

    GdagStore().upsert_node({
        "id": zero_store.new_id("msg"),
        "type": "fact",
        "camp": "default",
        "props": {
            "conversation_id": conv["id"],
            "workspace_id": conv.get("workspace_id"),
            "content": content,
            "stage": "exploration",
            "by": "local",
        },
    })


def test_fork_records_source_and_filters(client):
    source, other, _ = _mk(client)
    conv = client.get(f"{API}/conversations/{source}").json()["data"]
    _add_message(conv)

    branch = client.post(f"{API}/conversations/{source}/fork", json={}).json()["data"]
    assert branch["id"] and branch["id"] != source
    assert branch["forked_from_id"] == source

    # 只看该会话的直接子分支：other 不能被带进来。
    resp = client.get(f"{API}/conversations?forked_from_id={source}&limit=20").json()["data"]
    ids = [item["id"] for item in resp["items"]]
    assert ids == [branch["id"]], ids
    assert resp["total"] == 1

    # 没有任何子分支的会话返回空，而不是整张表。
    empty = client.get(f"{API}/conversations?forked_from_id={other}").json()["data"]
    assert empty["items"] == []
    assert empty["total"] == 0

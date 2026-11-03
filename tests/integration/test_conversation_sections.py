"""会话列表分组：进行中（按时间倒序）→ 完结（按时间倒序）。

用户要求：「会话的排序为 进行中（按照时间先后）-> 完结（按照时间先后）」，
组内时间倒序（最近活动在最上）。

这里锁定三件事：

1. `list_conversations` 的返回顺序 = 进行中在前，两组各自 updated_at 倒序；
2. 每条带 `active` / `run_status` / `has_active_goal`，前端据此分组；
3. 分页在**排序之后**做（否则「进行中」被挤到第 2 页就永远看不见）——
   这一条最容易写错：取一页再在 Python 里排序就会漏。
"""
from __future__ import annotations

import time

API = "/api/agent-zero"


def _mk(client) -> tuple:
    """建一个工作区 + 三条会话，返回 (ws_id, conv_ids)。"""
    from services.agent_zero.stores import zero_store

    ws_id = zero_store.DEFAULT_WORKSPACE_ID
    ids = []
    for _ in range(3):
        resp = client.post(f"{API}/conversations", json={}).json()
        ids.append(resp["data"]["id"])
    return ws_id, ids


def _age(conv_id: str, seconds: int) -> None:
    """把会话的 updated_at 往前拨，用来构造确定的时间先后。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET updated_at = datetime('now','localtime',:d) "
                 f"WHERE id = :id"),
            {"d": f"-{int(seconds)} seconds", "id": conv_id},
        )
        db.commit()


def _enqueue(conv_id: str) -> str:
    """给会话挂一条 queued run（= 进行中），返回 run id。

    直接走 run_service（不经 worker）：run 会稳定停在 queued，
    不像 HTTP 接口那样可能被 worker 立刻认领成 running，测试才好断言。
    """
    from services.agent_zero.task_layer import run_service

    run = run_service.create_run(conv_id, "tester", content="排队中的消息")
    return run["id"]


def test_active_conversations_come_first(client):
    ws_id, (old_done, newest_done, live) = _mk(client)

    # 时间线：live 最旧，newest_done 最新。若无分组，live 会排最后。
    _age(live, 300)
    _age(old_done, 120)
    _age(newest_done, 0)
    _enqueue(live)

    items = client.get(f"{API}/conversations?workspace_id={ws_id}").json()["data"]["items"]
    ids = [it["id"] for it in items]

    # live 必须排在最前 —— 尽管它 updated_at 最旧。
    assert ids[0] == live, f"进行中的会话没有排在最前：{ids}"
    # 完结组内部仍然是时间倒序。
    assert ids.index(newest_done) < ids.index(old_done), f"完结组内没有按时间倒序：{ids}"

    by_id = {it["id"]: it for it in items}
    assert by_id[live]["active"] is True
    assert by_id[live]["run_status"] == "queued"
    assert by_id[newest_done]["active"] is False
    assert by_id[newest_done]["run_status"] == ""


def test_active_goal_also_counts_as_in_progress(client):
    ws_id, (older, newer, _) = _mk(client)
    _age(older, 300)
    _age(newer, 0)

    resp = client.put(f"{API}/conversations/{older}/goal",
                      json={"objective": "把仓库里的 TODO 全部清掉"}).json()
    assert resp.get("code") == 0, resp

    items = client.get(f"{API}/conversations?workspace_id={ws_id}").json()["data"]["items"]
    by_id = {it["id"]: it for it in items}
    assert by_id[older]["has_active_goal"] is True
    assert by_id[older]["active"] is True
    assert [it["id"] for it in items][0] == older


def test_paging_happens_after_grouping(client):
    """进行中的会话即使最旧，也要出现在第一页。

    旧实现若「先 LIMIT 一页再排序」，最旧的进行中会话会被翻页切掉。
    """
    ws_id, (live, a, b) = _mk(client)
    _age(live, 600)
    _age(a, 300)
    _age(b, 0)
    _enqueue(live)

    page1 = client.get(f"{API}/conversations?workspace_id={ws_id}&limit=1&offset=0").json()
    assert page1["data"]["items"][0]["id"] == live, page1["data"]["items"]
    assert page1["data"]["total"] >= 3


def test_stopped_run_drops_back_to_completed(client):
    """run 走到终态后，会话回到「完结」组。"""
    ws_id, (conv, a, b) = _mk(client)
    _age(conv, 600)
    run_id = _enqueue(conv)

    def active_flag() -> bool:
        items = client.get(f"{API}/conversations?workspace_id={ws_id}").json()["data"]["items"]
        return next(it for it in items if it["id"] == conv)["active"]

    assert active_flag() is True
    from services.agent_zero.task_layer import run_service

    assert run_service.cancel_run(run_id) is True
    deadline = time.time() + 5
    while time.time() < deadline and active_flag():
        time.sleep(0.05)
    assert active_flag() is False

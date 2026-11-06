"""「立即插入」= 停止当前回复，紧接着执行这条消息（V2 语义）。

用户反馈（2026-11-02）：「立即插入 需要调整为 停止然后继续，目前跟队列一样，但
输出又在原来的位置，导致界面异常」。

旧实现把排队消息交给执行中的 run 当「干预」注入：正文继续在原位流式输出，消息却
被从队列里摘掉、转录里又按 `injected_into_run` 重新锚位——两边同时动，界面看起来
就是「输出没变、消息乱跳」。新实现不碰正在生成的这轮：

1. `inject_run` 只把排队 run 提到队首，消息保持 `queued`（不再写 inject=1）；
2. 路由随后 `request_stop` + `force_stop` 当前 running，走与「停止」完全同一条路径；
3. 当前 run 落 stopped 后，worker 立刻认领队首那条，用户看到「停 → 接着跑新消息」。

这里用假生成器验证 1~3，不依赖真实模型。
"""
from __future__ import annotations

import asyncio
import time

API = "/api/agent-zero"


def _wait_status(client, run_id, wanted, timeout=12.0):
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        run = (client.get(f"{API}/runs/{run_id}").json()["data"] or {}).get("run") or {}
        status = run.get("status", "")
        if status in wanted:
            return status
        time.sleep(0.05)
    return status


def _run_id(resp):
    return (resp.get("data") or {}).get("id")


def test_inject_stops_current_run_and_runs_message_next(client, monkeypatch):
    from services.agent_zero.task_layer import conversation

    seen: list = []

    async def fake_stream(*args, **kwargs):
        content = str(kwargs.get("content") or "")
        seen.append(content)
        yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
        if content == "第二条：立刻执行":
            yield {"delta": {"delta": "第二条跑完了", "round": 1,
                             "role_id": "default", "role_name": "Atlas"}}
            yield {"done": {"done": True, "content": "第二条跑完了", "usage": {}}}
            return
        # 第一条：一直吐字，直到被强制停止
        i = 0
        while True:
            i += 1
            yield {"delta": {"delta": f"第一条{i} ", "round": 1,
                             "role_id": "default", "role_name": "Atlas"}}
            await asyncio.sleep(0.1)

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    first_run = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                   json={"content": "第一条：慢慢跑", "mode": "chat"}).json())
    assert first_run
    assert _wait_status(client, first_run, {"running"}, timeout=8.0) == "running"

    # 第二条在第一条还在跑的时候发出 → 排队
    second_run = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                    json={"content": "第二条：立刻执行", "mode": "chat"}).json())
    assert second_run
    assert _wait_status(client, second_run, {"queued"}, timeout=4.0) == "queued"

    # 「立即插入」
    resp = client.post(f"{API}/conversations/{cid}/queue/{second_run}/inject").json()
    assert resp["code"] == 0, resp
    assert resp["data"]["stopped_run_id"] == first_run

    # 1) 当前回复被真的停下（不是继续在同一轮里插话）
    assert _wait_status(client, first_run, {"stopped"}, timeout=8.0) == "stopped", (
        "立即插入没有停掉正在执行的回复"
    )
    # 2) 插入的消息提到了队首并保持排队（等 worker 认领）
    queue = client.get(f"{API}/conversations/{cid}/queue").json()["data"]
    ids = [r["id"] for r in queue["items"]]
    if ids:
        assert ids[0] == second_run, ids
    # 3) 紧接着自己跑完（用户看到「停 → 接着跑新消息」）
    assert _wait_status(client, second_run, {"done", "running"}, timeout=10.0) in (
        "running", "done"), "插入的消息没有被接着执行"
    assert _wait_status(client, second_run, {"done"}, timeout=15.0) == "done"

    # 顺序：第一条先开始，第二条后开始；两条都在生成器里出现过
    assert seen[:2] == ["第一条：慢慢跑", "第二条：立刻执行"], seen[:2]


def test_inject_without_running_run_is_rejected(client, monkeypatch):
    """没有正在执行的 run：立即插入无意义，返回 409 而不是静默改状态。

    「排队 run 存在但没有 running」只在两条 run 交接的瞬间出现，直接构造会与
    worker 抢时序，所以这里显式把 `running_run` 打成 None 来锁这条守卫分支。
    """
    from services.agent_zero.task_layer import run_service

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    run_id = run_service.create_run(cid, "local", content="排队中",
                                    mode="chat")["id"]
    monkeypatch.setattr(run_service, "running_run", lambda _cid, **_kw: None)

    resp = client.post(f"{API}/conversations/{cid}/queue/{run_id}/inject")
    assert resp.status_code == 409, resp.text
    assert run_service.get_run(run_id)["status"] == "queued", "被拒时不应改动排队状态"


def test_inject_stop_is_silent(client, monkeypatch):
    """「停止并继续」是静默的：不写 interrupted 事件，助手消息也不打中断标记。

    用户要求（2026-11-02）：「立即插入的停止然后继续，是静默的」。停止是正常流程
    的一步，界面不该出现「运行已停止」提示与「本轮输出已中断」红条——只有用户
    自己点停止时才该有那些痕迹。
    """
    from services.agent_zero.task_layer import conversation, run_service

    async def fake_stream(*args, **kwargs):
        yield {"start": {"start": True, "role_id": "default", "role_name": "Atlas"}}
        i = 0
        while True:
            i += 1
            yield {"delta": {"delta": f"慢慢吐{i} ", "round": 1,
                             "role_id": "default", "role_name": "Atlas"}}
            await asyncio.sleep(0.1)

    monkeypatch.setattr(conversation, "generate_reply_stream", fake_stream)

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    first_run = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                   json={"content": "先跑着", "mode": "chat"}).json())
    assert _wait_status(client, first_run, {"running"}, timeout=8.0) == "running"
    second_run = _run_id(client.post(f"{API}/conversations/{cid}/messages/run",
                                    json={"content": "插队", "mode": "chat"}).json())
    assert _wait_status(client, second_run, {"queued"}, timeout=4.0) == "queued"

    client.post(f"{API}/conversations/{cid}/queue/{second_run}/inject")
    assert _wait_status(client, first_run, {"stopped"}, timeout=8.0) == "stopped"

    assert run_service.is_stop_silent(first_run) is True
    kinds = [e.get("_kind") for e in run_service.list_all_events(first_run)]
    assert "interrupted" not in kinds, kinds

    # 落库的助手消息保持「正常结束」的样子：没有 interrupted 标记，也没有错误文案
    msgs = conversation.list_messages(cid)
    partial = [m for m in msgs if m.get("by") == "assistant" and m.get("run_id") == first_run]
    assert partial, "停止时刻已产生的正文没有被落库"
    assert all(not m.get("interrupted") for m in partial), partial
    assert all(not (m.get("error") or "") for m in partial), partial

    # 用户自己点停止仍然要有「已中断」痕迹（静默只作用于「停止并继续」）。
    # 新开一个会话：上面那条插入的消息还在跑，用同一会话会排队拿不到 running。
    other = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    third = _run_id(client.post(f"{API}/conversations/{other}/messages/run",
                                json={"content": "再跑一轮", "mode": "chat"}).json())
    assert _wait_status(client, third, {"running"}, timeout=8.0) == "running"
    client.post(f"{API}/runs/{third}/stop")
    assert _wait_status(client, third, {"stopped"}, timeout=8.0) == "stopped"
    assert run_service.is_stop_silent(third) is False
    kinds3 = [e.get("_kind") for e in run_service.list_all_events(third)]
    assert "interrupted" in kinds3, kinds3

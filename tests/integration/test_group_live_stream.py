"""群聊流式输出：第一帧就是「谁在说话」，多角色各自成段。

现场观感（用户反馈）：
- 群组里先显示「助手」，等几秒才换成指定的 / 通用的角色；
- 之后内容「疯狂输出」——其实是主持、成员几轮回复挤在同一个气泡里。

根因：
1. 前端在 `streaming=true` 时先用兜底的「助手」画气泡，而服务端的 `start`
   事件排在提示词准备（团队 / 历史 / 代码图谱 / MCP）之后才发；
2. 群聊一轮里多个角色共用同一个 start/done 通道，前端只保留一个用户名，
   几个人的正文拼在一条流里。

这里锁住服务端契约：每个发言角色都先发 start（带 role_name），且 start 是
run 的第一条事件（seq=1），不被准备阶段拖到后面。
"""
from __future__ import annotations

import time

from services.agent_zero.services import team_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation

API = "/api/agent-zero"


def _wait_status(client, run_id, wanted, timeout=15.0):
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = (client.get(f"{API}/runs/{run_id}").json()["data"]["run"] or {}).get(
            "status", "")
        if status in wanted:
            return status
        time.sleep(0.05)
    return status


def test_group_stream_starts_with_real_speaker(client, app_env, monkeypatch):
    from services.agent_zero.runtime.zero import llm as llm_mod

    async def fake_stream_full(self, role, messages, **kw):
        yield {"type": "delta", "content": "收到"}
        yield {"type": "done", "content": "收到", "tool_calls": [],
               "usage": {"prompt_tokens": 8, "completion_tokens": 2},
               "finish_reason": "stop"}

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_stream_full)

    ws = zero_store.DEFAULT_WORKSPACE_ID
    team = team_service.create_team(
        ws, "直播群", mode="roundtable", max_speakers=2,
        leader={"name": "主持"}, members=[{"name": "后端"}])
    host, member = team["leader"], team["members"][0]
    conv = conversation.create_conversation(ws, "local", team_id=team["id"])

    resp = client.post(f"{API}/conversations/{conv['id']}/messages/run",
                       json={"content": "请 @主持 @后端 说说", "mode": "chat",
                             "mentions": ["主持", "后端"]}).json()
    run_id = resp["data"]["id"]
    assert _wait_status(client, run_id, ("done", "failed")) == "done"

    events = client.get(f"{API}/runs/{run_id}").json()["data"]["events"]
    starts = [e for e in events if e["_kind"] == "start"]
    assert [e["role_name"] for e in starts] == ["主持", "后端"], starts
    # start 是 run 的第一条事件：不被提示词准备挡在后面。
    assert events[0]["_kind"] == "start" and events[0]["_seq"] == 1, events[:3]
    # 每个角色的正文都落在自己的 start 之后、下一个 start 之前。
    first_start = events.index(starts[0])
    second_start = events.index(starts[1])
    assert any(e["_kind"] == "delta" for e in events[first_start:second_start])
    assert any(e["_kind"] == "delta" for e in events[second_start:])


def test_sse_replay_is_chunked(client, app_env):
    """重连 / 刷新时上千条历史事件分帧下发，不在一帧里糊满。

    群聊一轮里好几个角色轮流说，事件量很大；整段塞进一个 SSE 帧会让前端在
    同一个渲染周期里铺开上千段内容（「等半天然后疯狂输出」）。
    """
    import json

    from services.agent_zero.task_layer import run_service

    conv = conversation.create_conversation(zero_store.DEFAULT_WORKSPACE_ID, "local")
    run = run_service.create_run(conv["id"], "local", content="历史", mode="chat")
    run_service.finish_run(run["id"], run_service.STATUS_DONE)
    for i in range(300):
        run_service.append_event(run["id"], "delta", {"delta": f"第{i}段"})

    body = client.get(f"{API}/runs/{run['id']}/stream?since=0").text
    frames = [part for part in body.split("event: run\n") if "data:" in part]
    assert len(frames) >= 3, f"300 条事件只发了 {len(frames)} 帧"
    counts = []
    last_seqs = []
    for frame in frames:
        payload = json.loads(frame.split("data: ", 1)[1].split("\n\n", 1)[0])
        events = payload.get("events") or []
        counts.append(len(events))
        # 每帧的 last_seq 只推到本帧末尾；推到整段末尾会让前端跳过后续分帧。
        assert payload["last_seq"] == events[-1]["_seq"]
        last_seqs.append(payload["last_seq"])
    assert max(counts) <= 120, counts
    assert sum(counts) == 300, counts
    assert last_seqs == sorted(last_seqs), last_seqs
    assert "event: run_end" in body

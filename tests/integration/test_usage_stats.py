"""会话 / 工单 / 任务的 token 用量统计（输入 / 输出 / 速度 / 缓存）。

口径：
- 输入 = 各轮 prompt 之和；输出 = 各轮 completion 之和；
- 速度 = 输出 / LLM 总耗时；缓存命中率 = cached / prompt；
- 会话只算前台对话（stage=exploration），后台任务成员 run 归到任务 / 工单，
  避免同一个 token 在会话与任务里被重复计数。
"""
from __future__ import annotations

import asyncio

API = "/api/agent-zero"


def _conversation(client) -> dict:
    from services.agent_zero.task_layer import conversation

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    conv = conversation.get_conversation(cid)
    assert conv, "会话创建失败"
    return conv


def _turn(conv: dict, usage: dict, run_id: str = "",
          stage: str = "exploration") -> None:
    from services.agent_zero.task_layer import conversation

    conversation._persist_assistant(
        conv, {"id": "role-1", "name": "Atlas"}, "ok", "test-model", 10,
        usage=usage, run_id=run_id, stage=stage,
    )


def _usage(prompt: int, completion: int, cached: int, llm_ms: int,
           rounds: int = 1) -> dict:
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "cached_tokens": cached, "llm_ms": llm_ms, "rounds": rounds,
            "max_context_tokens": prompt, "last_prompt_tokens": prompt}


def test_conversation_list_and_usage_endpoint(client):
    conv = _conversation(client)
    _turn(conv, _usage(1000, 200, 600, 4000, 2))
    _turn(conv, _usage(2000, 300, 500, 6000, 3))

    items = client.get(f"{API}/conversations").json()["data"]["items"]
    row = next(item for item in items if item["id"] == conv["id"])
    usage = row["usage"]
    assert usage["prompt_tokens"] == 3000
    assert usage["completion_tokens"] == 500
    assert usage["total_tokens"] == 3500
    assert usage["cached_tokens"] == 1100
    assert usage["cache_rate"] == 36.7
    assert usage["speed"] == 50.0  # 500 tokens / 10s
    assert usage["turns"] == 2
    assert usage["rounds"] == 5

    detail = client.get(f"{API}/conversations/{conv['id']}/usage").json()["data"]
    assert detail["prompt_tokens"] == 3000
    assert detail["last_prompt_tokens"] == 2000
    assert detail["speed"] == 50.0

    full = client.get(f"{API}/conversations/{conv['id']}").json()["data"]
    assert full["usage"]["total_tokens"] == 3500
    assert full["usage_total"]["total_tokens"] == 3500


def test_conversation_usage_ignores_task_stage(client):
    conv = _conversation(client)
    _turn(conv, _usage(100, 10, 0, 1000))
    _turn(conv, _usage(900, 90, 0, 1000), run_id="run-bg", stage="task")

    usage = client.get(f"{API}/conversations/{conv['id']}/usage").json()["data"]
    assert usage["prompt_tokens"] == 100
    items = client.get(f"{API}/conversations").json()["data"]["items"]
    row = next(item for item in items if item["id"] == conv["id"])
    assert row["usage"]["prompt_tokens"] == 100


def test_work_item_usage_and_stats(client, monkeypatch):
    from services.agent_zero.services import work_service

    conv = _conversation(client)
    _turn(conv, _usage(1200, 300, 400, 3000))

    async def noop_request(run_id):
        return None

    monkeypatch.setattr(work_service.conv_worker, "request_run", noop_request)
    monkeypatch.setattr(work_service.conversation, "create_conversation",
                        lambda *a, **k: conv)
    monkeypatch.setattr(work_service.conversation, "send_message",
                        lambda *a, **k: {"id": "msg-work-usage"})
    monkeypatch.setattr(work_service.run_service, "create_run",
                        lambda *a, **k: {"id": "run-work-usage"})
    monkeypatch.setattr(work_service.run_service, "get_run",
                        lambda run_id: {"id": run_id, "status": "running"})

    item = client.post(f"{API}/work-items", json={"title": "统计用量"}).json()["data"]
    asyncio.run(work_service.dispatch_item(item["id"]))

    listing = client.get(f"{API}/work-items").json()["data"]
    row = next(entry for entry in listing["items"] if entry["id"] == item["id"])
    assert row["usage"]["prompt_tokens"] == 1200
    assert row["usage"]["completion_tokens"] == 300
    assert row["usage"]["cache_rate"] == 33.3
    assert listing["stats"]["tokens"] == 1500
    assert listing["stats"]["prompt_tokens"] == 1200
    assert listing["stats"]["rounds"] == 1
    assert listing["stats"]["turns"] == 1

    detail = client.get(f"{API}/work-items/{item['id']}").json()["data"]
    assert detail["usage"]["total_tokens"] == 1500


def test_team_task_usage_updates_from_live_run_events(client):
    """后台任务运行中：还没落助手消息时，用最新 usage 事件实时显示 tokens。"""
    from services.agent_zero.task_layer import run_service

    conv = client.post(f"{API}/conversations", json={}).json()["data"]
    task = client.post(f"{API}/conversations/{conv['id']}/team-tasks",
                       json={"goal": "实时用量"}).json()["data"]
    run_id = str(task["members"][0]["run_id"])
    run_service.append_event(run_id, "usage", {
        "usage": _usage(321, 45, 120, 1500),
        "context_tokens": 321, "round": 0,
    })

    items = client.get(
        f"{API}/conversations/{conv['id']}/team-tasks").json()["data"]["items"]
    row = next(entry for entry in items if entry["id"] == task["id"])
    assert row["members"][0]["usage"]["total_tokens"] == 366
    assert row["usage"]["prompt_tokens"] == 321
    assert row["usage"]["completion_tokens"] == 45
    assert row["usage"]["cache_rate"] == 37.4
    assert row["usage"]["speed"] == 30.0


def test_team_task_usage_from_member_runs(client):
    conv = client.post(f"{API}/conversations", json={}).json()["data"]
    task = client.post(f"{API}/conversations/{conv['id']}/team-tasks",
                       json={"goal": "跑一个后台任务"}).json()["data"]
    run_id = str(task["members"][0]["run_id"])
    assert run_id

    from services.agent_zero.task_layer import conversation

    full = conversation.get_conversation(conv["id"])
    _turn(full, _usage(700, 90, 210, 2000), run_id=run_id, stage="task")

    items = client.get(
        f"{API}/conversations/{conv['id']}/team-tasks").json()["data"]["items"]
    row = next(entry for entry in items if entry["id"] == task["id"])
    assert row["usage"]["prompt_tokens"] == 700
    assert row["usage"]["completion_tokens"] == 90
    assert row["usage"]["cache_rate"] == 30.0
    assert row["usage"]["speed"] == 45.0
    assert row["members"][0]["usage"]["total_tokens"] == 790

    # 任务用量不计入会话的前台用量（会话只算 exploration）。
    assert client.get(
        f"{API}/conversations/{conv['id']}/usage").json()["data"]["prompt_tokens"] == 0

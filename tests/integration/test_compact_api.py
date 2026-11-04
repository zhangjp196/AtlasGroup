"""手动上下文压缩：/compact 命令的后端入口。"""
from __future__ import annotations


def test_compact_writes_checkpoint(client, monkeypatch):
    conv = client.post("/api/agent-zero/conversations", json={}).json()["data"]

    from services.agent_zero.runtime.zero import compress
    from services.agent_zero.task_layer import conversation

    async def fake_decide(messages, **kwargs):
        return {"should_compress": True, "current": 12_000, "window": 32_000,
                "max_tokens": 4_000, "budget": 20_000, "keep": 4_000}

    async def fake_apply(messages, **kwargs):
        return {
            "compressed": True, "summary": "## 目标\n- 手动压缩测试",
            "source": "test", "original_tokens": 12_000, "tokens": 3_000,
            "pruned_tool_outputs": 2, "dropped_exchanges": 1,
        }

    monkeypatch.setattr(compress, "decide", fake_decide)
    monkeypatch.setattr(compress, "apply", fake_apply)

    res = client.post(f"/api/agent-zero/conversations/{conv['id']}/compact").json()["data"]
    assert res["compressed"] is True
    assert res["before_tokens"] == 12_000
    assert res["after_tokens"] == 3_000
    checkpoint = conversation.latest_checkpoint(conv["id"])
    assert checkpoint and "手动压缩测试" in checkpoint["summary"]


def test_compact_reports_nothing_without_history(client, monkeypatch):
    conv = client.post("/api/agent-zero/conversations", json={}).json()["data"]

    from services.agent_zero.runtime.zero import compress

    async def fake_decide(messages, **kwargs):
        return {"should_compress": False, "reason": "nothing", "current": 0}

    monkeypatch.setattr(compress, "decide", fake_decide)
    res = client.post(f"/api/agent-zero/conversations/{conv['id']}/compact").json()["data"]
    assert res["compressed"] is False
    assert res["reason"] == "nothing"

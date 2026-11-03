"""上下文压缩的端到端行为：决策 → 开始事件 → 摘要（单次调用）→ 结束事件。

这里用 MockTransport 假上游，验证三件真实链路里最容易出问题的事：

1. 入口压缩会在**发出任何 delta 之前**推 start，压缩完成后推 done；
2. 摘要请求走 `chat_once`（不带 tools、不是流式），并且只发一次；
3. 压缩后真正发给模型的首条 user 消息是 `<conversation-checkpoint>`，摘要正文
   在里面，近期原文保留在它后面。
"""
from __future__ import annotations

import asyncio
import json

import httpx2
import pytest

from services.agent_zero.runtime.zero import compress as compress_mod
from services.agent_zero.runtime.zero import llm
from services.agent_zero.task_layer import conversation

API = "/api/agent-zero"
SUMMARY = ("## 目标\n- 把手上的重构做完\n\n## 下一步\n1. 继续跑测试\n"
           "\n## 相关文件\n- `services/app.py`：主要改动点")


@pytest.fixture(autouse=True)
def llm_env(monkeypatch):
    monkeypatch.setenv("AGENT_ZERO_LLM_BASE_URL", "http://gw.local/v1")
    monkeypatch.setenv("AGENT_ZERO_LLM_API_KEY", "EMPTY")
    monkeypatch.setenv("AGENT_ZERO_LLM_MODEL", "fake-model")
    monkeypatch.delenv("AGENT_ZERO_LLM_PROTOCOL", raising=False)
    monkeypatch.setattr(llm, "_retry_policy", lambda *_a, **_k: (0, 0.0))


def _stream(text: str) -> str:
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": text}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 9000, "completion_tokens": 8,
                                  "total_tokens": 9008}},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _completion(text: str) -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 1, "model": "fake-model",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 120,
                  "total_tokens": 1320},
    }


def _history(rounds: int = 10) -> list:
    messages = []
    for i in range(rounds):
        messages.append({"role": "user", "content": f"第 {i} 轮问题 " + "甲" * 1500})
        messages.append({"role": "assistant", "content": f"第 {i} 轮答案 " + "乙" * 1500})
    return messages


def test_entry_compaction_replaces_history_with_checkpoint(client, monkeypatch):
    requests: list = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("stream"):
            return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                   text=_stream("好的，继续。"))
        # 摘要调用：非流式、不带 tools
        return httpx2.Response(200, json=_completion(SUMMARY))

    monkeypatch.setattr(
        llm, "_new_http_client",
        lambda trust_env=False: httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False))
    # 小窗口：让入口压缩必然触发（系统提示 + 10 轮历史远超 ceiling）
    async def fake_context(models):
        return 20_000, 4_096

    monkeypatch.setattr(compress_mod, "_model_context", fake_context)
    monkeypatch.setattr(conversation, "_history_messages",
                        lambda *args, **kwargs: _history())

    conv = client.post(f"{API}/conversations", json={}).json()["data"]

    async def collect():
        events = []
        async for evt in conversation.generate_reply_stream(conv["id"], "接着做"):
            events.append(evt)
        return events

    events = asyncio.run(collect())

    # 1) 事件顺序：start → done 出现在正文 delta 之前
    kinds = []
    for evt in events:
        for key in ("context_compress_start", "context_compress_done", "delta", "done"):
            if key in evt:
                kinds.append(key)
    assert "context_compress_start" in kinds
    assert kinds.index("context_compress_start") < kinds.index("context_compress_done")
    assert kinds.index("context_compress_done") < kinds.index("delta")

    done = next(e["context_compress_done"] for e in events if "context_compress_done" in e)
    assert done["source"] == "llm"
    assert done["after"] < done["before"]
    assert done["keep"] >= 1

    # 2) 摘要只调用一次：非流式、不带 tools、且带结构化模板
    summaries = [r for r in requests if not r.get("stream")]
    assert len(summaries) == 1, [r.get("stream") for r in requests]
    summary_body = summaries[0]
    assert not summary_body.get("tools")
    assert "【新增历史】" in summary_body["messages"][-1]["content"]
    assert "## 目标" in summary_body["messages"][-1]["content"]

    # 3) 压缩后的正式请求：system 之后第一条就是检查点
    loop_bodies = [r for r in requests if r.get("stream")]
    assert loop_bodies, "没有发起正式请求"
    sent = loop_bodies[0]["messages"]
    assert sent[0]["role"] == "system"
    checkpoint = next(m for m in sent if m["role"] != "system")
    assert str(checkpoint["content"]).startswith("<conversation-checkpoint>")
    assert "把手上的重构做完" in checkpoint["content"]
    # 近期原文仍在（不是只剩摘要）
    assert any("第 9 轮" in str(m.get("content") or "") for m in sent)


def test_compaction_sends_heartbeats_while_waiting(client, monkeypatch):
    """摘要调用慢时必须有 ping 顶住——这正是过去看起来「卡死」的那一刻。"""

    async def handler(request):
        json.loads(request.content)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text=_stream("好的。"))

    monkeypatch.setattr(
        llm, "_new_http_client",
        lambda trust_env=False: httpx2.AsyncClient(
            transport=httpx2.MockTransport(handler), trust_env=False))
    # 心跳间隔调小，测试不必真的等 20s
    monkeypatch.setattr(conversation, "TOOL_KEEPALIVE_SECONDS", 0.05)

    async def slow_decide(messages, **kwargs):
        return {"should_compress": True, "current": 90_000, "window": 128_000,
                "budget": 100_000, "keep": 15_000, "mode": "ceiling",
                "reason": "auto", "count_source": "usage", "max_tokens": 4_096}

    async def slow_apply(messages, **kwargs):
        await asyncio.sleep(0.3)
        out = [messages[0],
               {"role": "user", "content": compress_mod.checkpoint_content("## 目标\n- 继续")}]
        return {"messages": out, "compressed": True, "source": "llm",
                "summary": "## 目标\n- 继续", "summary_chars": 8, "keep": 15_000,
                "kept_recent": 0, "dropped_exchanges": 0, "hard_trimmed": 0,
                "original_tokens": 90_000, "tokens": 1_500, "window": 128_000,
                "current": 90_000, "count_source": "usage", "mode": "ceiling",
                "reason": "auto", "decision": {}}

    monkeypatch.setattr(compress_mod, "decide", slow_decide)
    monkeypatch.setattr(compress_mod, "apply", slow_apply)
    monkeypatch.setattr(conversation, "_history_messages", lambda *a, **k: [])

    conv = client.post(f"{API}/conversations", json={}).json()["data"]

    async def collect():
        return [e async for e in conversation.generate_reply_stream(conv["id"], "hi")]

    events = asyncio.run(collect())
    kinds = [next(iter(e)) if len(e) == 1 else "?" for e in events]
    assert "context_compress_start" in kinds
    assert "context_compress_done" in kinds
    pings = [e for e in events
             if e.get("ping") and (e.get("waiting") == "compact")]
    assert pings, "摘要期间没有心跳，界面会显示成卡死"
    assert kinds.index("context_compress_start") < kinds.index("context_compress_done")

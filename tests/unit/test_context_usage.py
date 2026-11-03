"""会话用量口径：`last_prompt_tokens` = 当前上下文，`max_context_tokens` = 历史峰值。

顶部「上下文 %」的分子必须是**最近一轮真实 prompt**：压缩之后 prompt 会变小，
如果聚合时取 MAX，百分比会永远停在压缩前的高位（这正是之前的问题）。
老数据没有 `last_prompt_tokens` 字段时，退回该轮的峰值，不能直接变 0 / 隐藏。
"""
from __future__ import annotations

API = "/api/agent-zero"


def _new_conversation(client) -> dict:
    from services.agent_zero.task_layer import conversation

    cid = client.post(f"{API}/conversations", json={}).json()["data"]["id"]
    conv = conversation.get_conversation(cid)
    assert conv, "会话创建失败"
    return conv


def _turn(conv: dict, usage: dict, text: str = "ok") -> None:
    from services.agent_zero.task_layer import conversation

    conversation._persist_assistant(
        conv, {"id": "role-1", "name": "Atlas"}, text, "test-model", 10,
        usage=usage,
    )


def test_usage_total_uses_last_prompt_not_peak(client):
    from services.agent_zero.task_layer import conversation

    conv = _new_conversation(client)
    _turn(conv, {"prompt_tokens": 5000, "completion_tokens": 50,
                 "last_prompt_tokens": 5000, "max_context_tokens": 5000})
    _turn(conv, {"prompt_tokens": 50000, "completion_tokens": 50,
                 "last_prompt_tokens": 52000, "max_context_tokens": 52000})
    # 压缩后的下一轮：prompt 变小，但本轮峰值仍可能高于它（压缩发生在轮内）
    _turn(conv, {"prompt_tokens": 12000, "completion_tokens": 50,
                 "last_prompt_tokens": 9000, "max_context_tokens": 51000})

    total = conversation.usage_total(conv["id"])
    assert total["last_prompt_tokens"] == 9000, total
    # 峰值是历史最大值，不会因为压缩后的下一轮变小而回落
    assert total["max_context_tokens"] == 52000, total
    assert total["prompt_tokens"] == 67000, total


def test_usage_total_falls_back_to_peak_for_old_rows(client):
    from services.agent_zero.task_layer import conversation

    conv = _new_conversation(client)
    # 老版本落库的 usage 没有 last_prompt_tokens
    _turn(conv, {"prompt_tokens": 4000, "completion_tokens": 20,
                 "max_context_tokens": 4000})

    total = conversation.usage_total(conv["id"])
    assert total["last_prompt_tokens"] == 4000, total

"""跨轮历史预算（vibecoding 长会话）单元测试。

锁定这些行为：
- 条数上限之外，再按 token 预算从旧到新裁剪：短问答多留几轮，大附件少留；
- 裁剪丢整条消息、起点对齐到 user，最新 user 及其后永不丢；
- 预算默认取模型窗口的 20%（8k～60k），窗口未知时退保守值；
- 配置 `llm.history.max_messages` / `max_tokens` 能覆盖默认。
"""
from __future__ import annotations

import asyncio

from services.agent_zero.task_layer import conversation as conv_mod


def msg(role: str, chars: int = 1000) -> dict:
    return {"role": role, "content": "甲" * chars}


def test_trim_history_under_budget_is_untouched():
    messages = [msg("user", 10), msg("assistant", 10)]
    assert conv_mod._trim_history(messages, 10_000) is messages


def test_trim_history_drops_oldest_whole_messages_and_starts_at_user():
    messages = [msg("user"), msg("assistant"), msg("user"),
                msg("assistant"), msg("user"), msg("assistant")]
    out = conv_mod._trim_history(messages, 3_000)
    assert out[0]["role"] == "user"
    assert out[-1] is messages[-1]
    assert len(out) < len(messages)


def test_trim_history_never_drops_latest_user_turn():
    # 单轮就超预算：也只能原样保留（当前请求必须发出去）
    messages = [msg("user", 50_000)]
    assert conv_mod._trim_history(messages, 100) == messages
    # 最新一轮即使很大也不裁
    messages = [msg("user", 5_000), msg("assistant", 10),
                msg("user", 50_000), msg("assistant", 10)]
    out = conv_mod._trim_history(messages, 100)
    assert out == messages[2:]


def test_trim_history_counts_images():
    image = [{"type": "text", "text": "看图"},
             {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]
    messages = [{"role": "user", "content": "甲" * 100},
                {"role": "assistant", "content": "乙" * 100},
                {"role": "user", "content": image},
                {"role": "assistant", "content": "好"}]
    out = conv_mod._trim_history(messages, 100)
    # 两轮各约 1.6k / 1.5k token，预算 100 只留得下最后一轮
    assert out == messages[2:]
    assert isinstance(out[0]["content"], list)


def _patch_llm(monkeypatch, history=None, window=128_000):
    from services.agent_zero.runtime.zero import llm as llm_mod

    async def policy():
        return {"history": history} if history is not None else {}

    async def cfg(_code):
        return {"context_window": window}

    monkeypatch.setattr(llm_mod, "get_context_policy", policy)
    monkeypatch.setattr(llm_mod, "get_llm_config", cfg)


def test_history_limits_from_config(monkeypatch):
    _patch_llm(monkeypatch, {"max_messages": 80, "max_tokens": 12_000})
    assert asyncio.run(conv_mod._history_limits({"loop": "default"})) == (80, 12_000)


def test_history_limits_auto_share_of_window(monkeypatch):
    _patch_llm(monkeypatch, {"max_messages": 40})
    assert asyncio.run(conv_mod._history_limits({"loop": "default"})) == (40, 25_600)


def test_history_limits_clamps_small_and_huge_windows(monkeypatch):
    _patch_llm(monkeypatch, None, window=32_000)
    assert asyncio.run(conv_mod._history_limits({}))[1] == 8_000
    _patch_llm(monkeypatch, None, window=1_000_000)
    assert asyncio.run(conv_mod._history_limits({}))[1] == 60_000


def test_history_limits_fall_back_when_window_unknown(monkeypatch):
    _patch_llm(monkeypatch, None, window=0)
    assert asyncio.run(conv_mod._history_limits({}))[1] == conv_mod.CHAT_HISTORY_FALLBACK_TOKENS

"""上下文压缩（OpenCode V2 口径）单元测试。

锁定这些行为：
- 触发只认 ceiling = window − max(15% 窗口, 16000)，不再有增速预测；
- 切分保留整轮 exchange，尾巴从 user 开始，长任务（一条 user + 多轮工具）也
  不会把当前请求折叠掉；
- 产物是 system + `<conversation-checkpoint>` + 近期原文，且**一定装进 budget**；
- 摘要失败降级为确定性摘要，绝不抛错（run 不能因压缩中断）；
- 转录按级别缩短，最新的 exchange 永不省略。
"""
from __future__ import annotations

import asyncio

from services.agent_zero.runtime.zero import compress as C

POLICY = {"auto": True, "keep": C.KEEP_DEFAULT, "buffer": 0, "percent": None}
WINDOW = 128_000


def conv(rounds: int = 8, size: int = 1600) -> list:
    messages = [{"role": "system", "content": "系统提示"}]
    for i in range(rounds):
        messages.append({"role": "user", "content": f"问题 {i} " + "甲" * size})
        messages.append({"role": "assistant", "content": f"回答 {i} " + "乙" * size})
    return messages


def decide(messages, **kwargs):
    args = dict(models={}, policy=POLICY, window_tokens=WINDOW, measured_tokens=120_000)
    args.update(kwargs)
    return asyncio.run(C.decide(messages, **args))


def apply(messages, decision):
    async def fake(transcript, previous, models, max_tokens):
        return "## 目标\n- 把活干完\n\n## 下一步\n1. 继续", "llm"

    original = C._summarize
    C._summarize = fake
    try:
        return asyncio.run(C.apply(messages, models={}, decision=decision))
    finally:
        C._summarize = original


# ── 触发口径 ────────────────────────────────────────────────────────────

def test_ceiling_matches_opencode_reserve():
    assert C.ceiling(128_000) == 128_000 - 19_200      # 15%
    assert C.ceiling(32_000) == 32_000 - 16_000        # 16k 下限
    assert C.ceiling(20_000) == 20_000 - 3_000         # 小窗口只留 15%
    assert C.ceiling(100_000, 30_000) == 70_000        # 显式预留
    assert C.ceiling(0) == 0


def test_decide_under_ceiling_does_not_compress():
    decision = decide(conv(), measured_tokens=50_000)
    assert decision["should_compress"] is False
    assert decision["reason"] == "under"
    assert decision["budget"] == C.ceiling(WINDOW)


def test_decide_over_ceiling_compresses():
    decision = decide(conv(), measured_tokens=C.ceiling(WINDOW) + 1)
    assert decision["should_compress"] is True
    assert decision["reason"] == "auto"
    assert decision["count_source"] == "usage"


def test_decide_ignores_auto_when_disabled():
    decision = decide(conv(), policy={**POLICY, "auto": False})
    assert decision["should_compress"] is False
    assert decision["reason"] == "disabled"


def test_decide_without_window_skips():
    decision = decide(conv(), window_tokens=0)
    assert decision["should_compress"] is False
    assert decision["reason"] == "no_window"


def test_decide_force_ignores_ceiling():
    decision = decide(conv(), measured_tokens=1_000, force=True,
                      budget_tokens=6_000)
    assert decision["should_compress"] is True
    assert decision["mode"] == "budget"
    assert decision["budget"] == 6_000


def test_decide_nothing_to_compact():
    decision = decide([{"role": "system", "content": "s"},
                       {"role": "user", "content": "只有一句"},
                       {"role": "assistant", "content": "回答"}],
                      measured_tokens=200_000)
    assert decision["should_compress"] is False
    assert decision["reason"] == "nothing"


# ── 切分 ────────────────────────────────────────────────────────────────

def test_split_starts_tail_at_user_message():
    messages = conv(8)
    split = C.split_conversation(messages, 5_000)
    assert split is not None
    head, older, tail, previous = split
    assert head and head[0]["role"] == "system"
    assert older and tail
    assert tail[0]["role"] == "user"
    assert previous is None
    # 折叠的 + 保留的 = 除 system 外的全部，一条不丢
    assert len(older) + len(tail) == len(messages) - 1


def test_split_keeps_tool_exchange_together():
    messages = [{"role": "system", "content": "s"}]
    for i in range(6):
        messages.append({"role": "user", "content": f"u{i} " + "甲" * 1200})
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{i}", "type": "function",
                                         "function": {"name": "read_file",
                                                      "arguments": "{\"path\":\"a\"}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": "结果 " + "乙" * 1200})
    split = C.split_conversation(messages, 3_000)
    assert split is not None
    _head, _older, tail, _previous = split
    assert tail[0]["role"] == "user"
    # 尾巴里不会出现「有 tool 结果但没有对应 assistant 调用」的悬空状态
    for idx, message in enumerate(tail):
        if message["role"] == "tool":
            assert idx > 0 and tail[idx - 1]["role"] in ("assistant", "tool")


def test_split_whole_context_fits_keep_keeps_only_latest_exchange():
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "第一答"},
                {"role": "user", "content": "第二问"},
                {"role": "assistant", "content": "第二答"}]
    split = C.split_conversation(messages, 100_000)
    assert split is not None
    _head, older, tail, _previous = split
    assert [m["content"] for m in older] == ["第一问", "第一答"]
    assert [m["content"] for m in tail] == ["第二问", "第二答"]


def test_split_single_exchange_keeps_current_request():
    """长任务：一条 user + 几十轮工具，当前请求必须留在尾巴里。"""
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "把仓库分析完"}]
    for i in range(20):
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{i}", "type": "function",
                                         "function": {"name": "grep",
                                                      "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": "命中 " + "丙" * 1500})
    split = C.split_conversation(messages, 4_000)
    assert split is not None
    _head, older, tail, _previous = split
    assert older, "中间的执行历史应该被折叠"
    assert tail[0]["role"] == "user" and tail[0]["content"] == "把仓库分析完"


def test_split_extracts_previous_checkpoint():
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": C.checkpoint_content("## 目标\n- 旧摘要")},
                {"role": "user", "content": "新问题 " + "甲" * 3000},
                {"role": "assistant", "content": "新回答 " + "乙" * 3000}]
    split = C.split_conversation(messages, 1_000)
    assert split is not None
    _head, older, _tail, previous = split
    assert previous == "## 目标\n- 旧摘要"
    assert all(not C._is_checkpoint(m) for m in older)


# ── 组装 ────────────────────────────────────────────────────────────────

def test_apply_builds_checkpoint_and_fits_budget():
    messages = conv(10)
    decision = decide(messages, measured_tokens=140_000)
    res = apply(messages, decision)

    assert res["compressed"] is True
    assert res["source"] == "llm"
    out = res["messages"]
    assert out[0]["role"] == "system"
    assert C._is_checkpoint(out[1])
    assert C.checkpoint_summary(out[1]).startswith("## 目标")
    # 上游契约：首个非 system 必须是 user
    assert next(m["role"] for m in out if m["role"] != "system") == "user"
    # 一定装进 budget
    assert C.estimate_tokens(out) <= res["budget"]
    # 近期原文保留
    assert out[-1] is messages[-1]


def test_apply_falls_back_to_digest_when_summary_fails(monkeypatch):
    async def failing(transcript, previous, models, max_tokens):
        return None, "error"

    monkeypatch.setattr(C, "_summarize", failing)
    messages = conv(10)
    decision = decide(messages, measured_tokens=140_000)
    res = asyncio.run(C.apply(messages, models={}, decision=decision))

    assert res["compressed"] is True          # 仍然压缩成功
    assert res["source"] == "heuristic"
    assert C._is_checkpoint(res["messages"][1])


def test_apply_never_raises_on_unexpected_error(monkeypatch):
    async def boom(transcript, previous, models, max_tokens):
        raise RuntimeError("summary 挂了")

    monkeypatch.setattr(C, "_summarize", boom)
    messages = conv(10)
    decision = decide(messages, measured_tokens=140_000)
    res = asyncio.run(C.apply(messages, models={}, decision=decision))
    assert res["source"] == "error"
    assert C.estimate_tokens(res["messages"]) <= res["budget"]
    assert any(m.get("role") == "user" for m in res["messages"])


def test_apply_hard_trims_single_oversized_message():
    huge = {"role": "user", "content": "甲" * 400_000}
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回答"},
                huge]
    decision = decide(messages, measured_tokens=200_000, budget_tokens=4_000)
    res = apply(messages, decision)
    assert C.estimate_tokens(res["messages"]) <= 4_000
    assert res["messages"][-1]["content"].endswith("…[已截断：超出上下文预算]")
    assert any(m.get("role") == "user" for m in res["messages"])


def test_apply_keeps_current_request_when_tail_must_shrink():
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "当前请求"}]
    for i in range(12):
        messages.append({"role": "assistant", "content": "过程 " + "丁" * 1500})
        messages.append({"role": "user", "content": f"补充 {i} " + "戊" * 1500})
    decision = decide(messages, measured_tokens=200_000, budget_tokens=6_000)
    res = apply(messages, decision)
    texts = [str(m.get("content") or "") for m in res["messages"]]
    assert any("当前请求" in t or "补充" in t for t in texts)
    assert C.estimate_tokens(res["messages"]) <= 6_000


def test_truncate_oversized_drops_orphan_tool_results():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "grep", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "结果 " + "乙" * 20_000},
    ]
    out, _truncated = C._truncate_oversized(messages, 500)
    assert not any(m.get("role") == "tool" for m in out)
    assert any(m.get("role") == "user" for m in out)
    assert C.estimate_tokens(out) <= 500


# ── 转录 ────────────────────────────────────────────────────────────────

def test_transcript_shrinks_from_oldest_and_keeps_newest():
    messages = []
    for i in range(6):
        messages.append({"role": "user", "content": f"用户{i} " + "甲" * 3000})
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": "c", "type": "function",
                                         "function": {"name": "read", "arguments": "{}"}}]})
        messages.append({"role": "tool", "content": "输出 " + "乙" * 3000})
    text = C.build_transcript(messages, 3_000)
    assert C.count_tokens(text) <= 3_000
    assert "用户5" in text            # 最新的 exchange 没有被省略
    assert "省略" in text             # 旧的被缩短/省略过


def test_summary_template_detection():
    assert C.summary_is_filled(
        "## 目标\n- 把仓库里的过期配置全部清理掉\n\n## 下一步\n1. 继续跑测试")
    assert C.summary_is_filled("## Objective\n- clean up the stale configs")
    assert not C.summary_is_filled("好的，我来汇总一下。")
    assert not C.summary_is_filled("## 目标")


# ── 摘要调用：有界、无重试、失败降级 ────────────────────────────────────

class _FakeLlm:
    calls = 0
    replies = ()
    fail = None

    def __init__(self, models):
        pass

    async def chat_once(self, role, messages, **kwargs):
        _FakeLlm.calls += 1

        class _R:
            content = _FakeLlm.replies[min(_FakeLlm.calls - 1, len(_FakeLlm.replies) - 1)]

        if _FakeLlm.fail:
            raise _FakeLlm.fail
        return _R()

    async def chat(self, *args, **kwargs):
        raise AssertionError("压缩摘要不允许走会重试 20 次的 chat()")


def _fake_llm(monkeypatch, replies, fail=None):
    from services.agent_zero.runtime.zero import llm as llm_mod

    _FakeLlm.calls = 0
    _FakeLlm.replies = replies
    _FakeLlm.fail = fail
    monkeypatch.setattr(llm_mod, "ZeroLlm", _FakeLlm)


def test_summarize_uses_single_shot_and_one_nudge(monkeypatch):
    _fake_llm(monkeypatch, ["这里没有套模板", "## 目标\n- 完成压缩并继续工作"])
    text, source = asyncio.run(C._summarize("历史", None, {"summary": "m"}, 4_096))
    assert source == "llm"
    assert text.startswith("## 目标")
    assert _FakeLlm.calls == 2          # 第一次 + 提醒一次，不会无限重试


def test_summarize_gives_up_after_one_failure(monkeypatch):
    _fake_llm(monkeypatch, ["## 目标\n- ok"], fail=RuntimeError("upstream timeout"))
    text, source = asyncio.run(C._summarize("历史", None, {"summary": "m"}, 4_096))
    assert text is None and source == "error"
    assert _FakeLlm.calls == 1          # 不重试：失败立刻交给降级路径


def test_apply_without_split_leaves_messages_alone():
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "只有一句"},
                {"role": "assistant", "content": "回答"}]
    decision = decide(messages, measured_tokens=200_000)
    res = asyncio.run(C.apply(messages, models={}, decision=decision))
    assert res["compressed"] is False
    assert res["messages"] == messages


# ── 一级压缩：旧工具输出就地清理（不调摘要模型）─────────────────────────

def conv_with_tools(rounds: int = 6, size: int = 4000) -> list:
    messages = [{"role": "system", "content": "系统提示"}]
    for i in range(rounds):
        messages.append({"role": "user", "content": f"任务 {i}"})
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{i}", "type": "function",
                                         "function": {"name": "grep",
                                                      "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": f"结果 {i} " + "乙" * size})
    return messages


def test_prune_tool_outputs_keeps_structure():
    messages = conv_with_tools(rounds=3)
    pruned, saved, count, deduped = C.prune_tool_outputs(messages)
    assert count == 3
    assert saved > 0
    assert len(pruned) == len(messages)
    assert [m["role"] for m in pruned] == [m["role"] for m in messages]
    assert all(m.get("tool_call_id") == o.get("tool_call_id")
               for m, o in zip(pruned, messages) if m.get("role") == "tool")
    tool_in = next(m for m in messages if m.get("role") == "tool")
    tool_out = next(m for m in pruned if m.get("role") == "tool")
    assert "旧工具输出已清理" in tool_out["content"]
    assert len(tool_out["content"]) < len(tool_in["content"])


def test_apply_prune_only_when_it_is_enough(monkeypatch):
    called = []

    async def fake_summary(*_a, **_k):
        called.append(1)
        return "## 目标\n- 不该被调用", "llm"

    monkeypatch.setattr(C, "_summarize", fake_summary)
    messages = conv_with_tools()
    decision = decide(messages, measured_tokens=30_000, budget_tokens=20_000)
    res = asyncio.run(C.apply(messages, models={}, decision=decision))

    assert res["source"] == "prune"
    assert res["pruned_tool_outputs"] >= 1
    assert not called, "工具输出清完就够时不该调摘要模型"
    assert C.estimate_tokens(res["messages"]) <= 20_000 * (1 - C.PRUNE_HEADROOM)
    # 结构不变：tool 消息还在，只是正文变短
    assert sum(1 for m in res["messages"] if m.get("role") == "tool") == 6


def test_apply_falls_back_to_checkpoint_when_prune_is_not_enough(monkeypatch):
    async def fake_summary(*_a, **_k):
        return "## 目标\n- 摘要", "llm"

    monkeypatch.setattr(C, "_summarize", fake_summary)
    messages = conv_with_tools()
    decision = decide(messages, measured_tokens=30_000, budget_tokens=12_000)
    res = asyncio.run(C.apply(messages, models={}, decision=decision))

    assert res["source"] == "llm"
    assert C.estimate_tokens(res["messages"]) <= 12_000
    assert C._is_checkpoint(res["messages"][1])


# ── token 花在哪：按角色分解 ────────────────────────────────────────────

def test_token_breakdown_splits_roles():
    messages = [
        {"role": "system", "content": "系统" * 300},
        {"role": "user", "content": "用户" * 300},
        {"role": "assistant", "content": "回答" * 300},
        {"role": "tool", "tool_call_id": "c1", "content": "结果" * 300},
    ]
    b = C.token_breakdown(messages)
    assert b["system"] > 0 and b["user"] > 0
    assert b["assistant"] > 0 and b["tool"] > 0
    assert b["image"] == 0
    # 分解的合计必须与整体估算一致（否则统计会骗人）
    assert b["total"] == C.estimate_tokens(messages)


def test_decision_carries_breakdown():
    messages = conv_with_tools(rounds=3)
    decision = decide(messages, measured_tokens=30_000)
    assert decision["breakdown"]["tool"] > 0
    assert decision["breakdown"]["total"] == C.estimate_tokens(messages)


# ── 重复工具输出去重（Agent 循环里重复读同一文件）──────────────────────

def test_prune_dedups_identical_tool_outputs():
    messages = [{"role": "system", "content": "s"}]
    for i in range(3):
        messages.append({"role": "user", "content": f"第 {i} 次"})
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": "完全一样的输出 " + "甲" * 3000})

    pruned, saved, clipped, deduped = C.prune_tool_outputs(messages)
    assert deduped == 2                 # 只保留最后一次原文
    assert saved > 0
    tools = [m for m in pruned if m.get("role") == "tool"]
    assert "完全一样的输出" in tools[-1]["content"]
    assert "完全相同" in tools[0]["content"] and "完全相同" in tools[1]["content"]
    # 配对关系不变
    assert [m["tool_call_id"] for m in tools] == ["c0", "c1", "c2"]

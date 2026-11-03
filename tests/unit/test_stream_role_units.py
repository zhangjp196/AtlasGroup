"""`_CtxMeter` / `_stamp_entry` / `_apply_compaction` 的回归测试。

这三个是从 `_stream_role` 里抽出来的：`_stream_role` 当时 1473 行，压缩逻辑
（入口一次 + 每轮一次）和上下文计量都写在函数体内，改一处要通读整段。
抽取后行为必须与原来完全一致——尤其是
- 压缩事件的顺序（start → 心跳 → done）与「内部结果事件不外泄」，
- 锚点/增量的语义（真实值覆盖增量，0 不清空锚点）。

`_apply_compaction` 靠 `{"_compressed": res}` 这个内部事件把结果带回调用方，
调用方**必须**过滤掉它，否则前端会收到一个陌生事件。这里把契约钉死。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from services.agent_zero.task_layer import conversation as conv_mod


# ── 抽取后的结构与契约 ────────────────────────────────────────────────


def test_stream_role_shrank_and_delegates():
    """_stream_role 不再自己实现压缩/计量，改为调用抽出来的单元。"""
    src = inspect.getsource(conv_mod._stream_role)
    assert "_apply_compaction(" in src
    assert "_CtxMeter(" in src
    # 旧的内联实现必须消失，否则等于没拆
    assert "context_compress_start" not in src
    assert '"anchor": 0' not in src


def test_apply_compaction_is_async_generator():
    assert inspect.isasyncgenfunction(conv_mod._apply_compaction)


def test_apply_compaction_emits_result_before_done():
    """顺序契约：start → (结果) → done，结果事件必须在 done 之前。"""
    src = inspect.getsource(conv_mod._apply_compaction)
    start = src.index('yield {"context_compress_start"')
    result = src.index('yield {"_compressed"')
    done = src.index('yield {"context_compress_done"')
    assert start < result < done


# ── _CtxMeter ────────────────────────────────────────────────────────


def test_meter_starts_from_estimate_then_anchor_wins():
    meter = conv_mod._CtxMeter([{"role": "user", "content": "hello world"}])
    start = meter.tokens()
    assert start >= 0

    meter.add("x" * 400)
    grown = meter.tokens()
    assert grown >= start

    meter.anchor(1234)
    assert meter.tokens() == 1234          # 真实值覆盖估算


def test_meter_ignores_zero_anchor():
    """0 不是真实值（拿不到 usage 时的占位），不能把锚点清零。"""
    meter = conv_mod._CtxMeter([])
    meter.anchor(500)
    meter.add("abc")
    before = meter.tokens()
    meter.anchor(0)
    assert meter.tokens() == before


def test_meter_add_ignores_empty():
    meter = conv_mod._CtxMeter([])
    meter.anchor(100)
    meter.add("")
    meter.add(None)
    assert meter.tokens() == 100


def test_meter_add_accumulates_delta():
    meter = conv_mod._CtxMeter([])
    meter.anchor(1000)
    text = "abcdefghij" * 50
    meter.add(text)
    once = meter.tokens() - 1000
    meter.add(text)
    twice = meter.tokens() - 1000
    assert twice == once * 2
    assert once > 0


@pytest.mark.parametrize("bad", ["nope", None, -5])
def test_meter_anchor_tolerates_bad_input(bad):
    meter = conv_mod._CtxMeter([])
    meter.anchor(777)
    meter.anchor(bad)
    assert meter.tokens() >= 0


# ── _stamp_entry ─────────────────────────────────────────────────────


def test_stamp_entry_sets_ts_and_seq():
    entry = {"kind": "text", "text": "hi"}
    out = conv_mod._stamp_entry(entry, 0.0, 7)
    assert out is entry                      # 就地返回
    assert out["seq"] == 7
    assert isinstance(out["ts"], int) and out["ts"] >= 0


def test_stamp_entry_ts_grows_with_elapsed():
    early = conv_mod._stamp_entry({}, 0.0, 0)["ts"]
    late = conv_mod._stamp_entry({}, -5.0, 1)["ts"]   # started 更早 → ts 更大
    assert late >= early >= 0


# ── _extract_final_text ──────────────────────────────────────────────


def _tl(*steps):
    return [dict(s) for s in steps]


def test_extract_final_text_takes_last_round_text():
    tl = _tl({"kind": "text", "round": 0, "text": "过程"},
             {"kind": "text", "round": 1, "text": "结论"})
    assert conv_mod._extract_final_text(tl, 1) == "结论"
    assert len(tl) == 1                        # 已从时间线摘掉，避免两处重复显示


def test_extract_final_text_rejects_text_followed_by_tool():
    """「说一句→调工具→结束」里的那句是过程正文，不能当最终答复。"""
    tl = _tl({"kind": "text", "round": 1, "text": "我先看看"},
             {"kind": "tool", "round": 1, "name": "shell"})
    assert conv_mod._extract_final_text(tl, 1) == ""
    assert len(tl) == 2                        # 没摘掉，过程保留


def test_extract_final_text_allows_non_tool_step_after_text():
    """收尾正文后的非工具步骤（思考 / 重试 / 压缩）不影响它成为最终答复。"""
    tl = _tl({"kind": "text", "round": 1, "text": "做完了"},
             {"kind": "retry", "round": 1, "text": "重试过"})
    assert conv_mod._extract_final_text(tl, 1) == "做完了"
    assert [s["kind"] for s in tl] == ["retry"]


def test_extract_final_text_ignores_earlier_rounds():
    tl = _tl({"kind": "text", "round": 0, "text": "过程里的上一轮"},
             {"kind": "tool", "round": 1, "name": "shell"})
    assert conv_mod._extract_final_text(tl, 1) == ""


def test_extract_final_text_empty_and_blank():
    assert conv_mod._extract_final_text([], 1) == ""
    tl = _tl({"kind": "text", "round": 1, "text": "   "})
    assert conv_mod._extract_final_text(tl, 1) == ""
    assert len(tl) == 1


# ── _apply_compaction 端到端（打桩 compress） ─────────────────────────


def test_apply_compaction_event_flow(monkeypatch):
    from services.agent_zero.runtime.zero import compress as compress_mod

    applied = {}

    async def fake_apply(messages, *, models=None, decision=None):
        applied["messages"] = messages
        return {"messages": [{"role": "system", "content": "summary"}],
                "tokens": 42}

    monkeypatch.setattr(compress_mod, "apply", fake_apply)

    async def scenario():
        events = []
        res = None
        async for ev in conv_mod._apply_compaction(
                [{"role": "user", "content": "long"}], {"loop": "m"},
                {"should_compress": True, "current": 100}, 2, "r1", "main", ""):
            if "_compressed" in ev:
                res = ev["_compressed"]
                continue
            events.append(ev)
        return events, res

    events, res = asyncio.run(scenario())
    assert events[0].get("context_compress_start") is not None
    assert events[-1].get("context_compress_done") is not None
    assert res and res["tokens"] == 42
    # 结果事件已过滤：调用方拿到的每个事件都是前端认识的
    assert all("_compressed" not in e for e in events)
    assert applied["messages"][0]["content"] == "long"

"""历史「立即插入」数据的转录位置：落在它被取走的那一轮回答之后。

V2（2026-11-02）起「立即插入」改成「停止当前回复，再把这条当下一轮跑」——
正常路径不再产生干预数据，本文件转为**历史数据兼容**的回归保护：老会话里
`inject=1` 的消息已被取走、排队 run 已删，读取时仍要排回它所属的那一回合。
产生这种老数据的入口是 `run_service.mark_intervention`。

以下根因说明保留，因为它解释了为什么排序需要 `injected_into_run` 锚点。

用户反馈（2026-10-30，紧接排队位置修复之后）：**「立即插入也是一样」**。

根因与排队位置那个是同一套的第二步：

旧版 `inject_run` 把消息的 `queued` 清 0、`inject` 置 1，交给执行中的 run 当干预取走。
取走时 `cancel_run_by_message` 删掉它对应的排队 run —— 于是转录里这条消息**没有任何
run 可锚**，`_order_messages_by_turn` 只能退回它自己的 `created_at`。而它落库时刻早于
当前回复，排序结果就是「夹在当前回答上面 / 挂在用户消息下面」，与排队消息那次的现象
一模一样（实测数据：msg-be9e49bdd87e 在 run-243763b366cd 的 seq=166 被取走，之后它的
排队 run 被删除）。

两处修法：

1. 取走时把归属写下（`props.injected_into_run`）并清掉 `queued` / `inject` ——
   见 `conversation.mark_message_injected`；
2. 排序时用这个归属回锚，且同一锚点内排在助手回复**之后**（role_rank=2）——
   见 `conversation._order_messages_by_turn`。

同时验证：不写归属时（旧数据）排序仍然可用，不会抛错。
"""
from __future__ import annotations

from services.agent_zero.task_layer import conversation, run_service

API = "/api/agent-zero"


def _conv(client) -> str:
    return client.post(f"{API}/conversations", json={}).json()["data"]["id"]


def _msg(conv_id: str, content: str, *, queued: bool = False) -> str:
    return conversation.send_message(conv_id, content, "local", queued=queued)["id"]


def _order(client, conv_id: str) -> list:
    data = client.get(f"{API}/conversations/{conv_id}/messages").json()["data"]
    items = data.get("items") if isinstance(data, dict) else data
    return [(m.get("by") or "", str(m.get("content") or "")[:8]) for m in items]


def _start(client, conv_id: str, msg_id: str, content: str) -> dict:
    """建 run 并认领（running），返回该 run。"""
    run = run_service.create_run(conv_id, "local", content=content,
                                 queued_msg_id=msg_id, mode="autonomous")
    assert run_service.claim_run(run["id"]), "run 未能认领为 running"
    return run_service.get_run(run["id"])


def _reply(conv_id: str, run_id: str, content: str) -> str:
    return conversation._persist_assistant(
        conversation.get_conversation(conv_id), {"id": "default", "name": "Atlas"},
        content, "test-model", 10, run_id=run_id,
    )


# ── 1. 取走时写归属、清标记 ─────────────────────────────────────────────

def test_drained_intervention_records_run_and_clears_flags(client):
    """被执行的 run 取走的干预消息：写 injected_into_run，queued/inject 都清 0。"""
    cid = _conv(client)
    u1 = _msg(cid, "第一轮问题", queued=True)
    run1 = _start(client, cid, u1, "第一轮问题")

    # 用户点「立即插入」（旧实现）：排队消息 → 无排队标记 + inject=1，落一条排队 run
    u2 = _msg(cid, "插一句：先别动 CSS", queued=True)
    qrun = run_service.create_run(cid, "local", content="插一句：先别动 CSS",
                                  queued_msg_id=u2, mode="autonomous")
    assert run_service.mark_intervention(u2) is True

    seen: set = set()
    drained = conversation._drain_new_interventions(cid, seen, run1["id"])

    assert [m["id"] for m in drained] == [u2], "立即插入的消息必须被当前 run 取走"
    props = (conversation.get_message_node(cid, u2) or {}).get("props") or {}
    assert props.get("injected_into_run") == run1["id"], (
        "没记下「被哪一轮取走」——转录排序就失去锚点，会退回落库时刻而夹到回答上面"
    )
    assert int(props.get("queued") or 0) == 0, "排队标记没清：前端会一直显示「排队中」"
    assert int(props.get("inject") or 0) == 0, "inject 没清：以后会被重复注入一次"
    # 它的排队 run 已被取走时删除，保证最多执行一次
    assert run_service.get_run(qrun["id"]) is None


def test_drain_without_run_id_still_clears_queued(client):
    """没传 run_id 时（旧调用方）：仍要把 queued 清掉，只是不写归属。"""
    cid = _conv(client)
    u = _msg(cid, "补充一句", queued=True)
    run_service.create_run(cid, "local", content="补充一句",
                           queued_msg_id=u, mode="autonomous")
    run_service.mark_intervention(u)

    conversation._drain_new_interventions(cid, set())

    props = (conversation.get_message_node(cid, u) or {}).get("props") or {}
    assert int(props.get("queued") or 0) == 0
    assert not props.get("injected_into_run")


# ── 2. 转录位置：插在那一轮回答之后 ─────────────────────────────────────

def test_injected_message_renders_after_the_reply(client):
    """被取走的插入消息排在**这一轮回答之后**，不是回答上面 / 用户消息下面。"""
    cid = _conv(client)
    u1 = _msg(cid, "第一轮问题", queued=True)
    run1 = _start(client, cid, u1, "第一轮问题")
    a1 = _reply(cid, run1["id"], "第一轮的回答正文")

    u2 = _msg(cid, "插一句：先别动 CSS", queued=True)
    run_service.create_run(cid, "local", content="插一句：先别动 CSS",
                           queued_msg_id=u2, mode="autonomous")
    run_service.mark_intervention(u2)
    conversation._drain_new_interventions(cid, set(), run1["id"])
    assert a1, "回复落库失败"

    order = _order(client, cid)
    labels = [f"{by}:{text}" for by, text in order]
    i_u1 = next(i for i, x in enumerate(labels) if x.startswith("local:第一轮问题"))
    i_a1 = next(i for i, x in enumerate(labels) if x.startswith("assistant:第一轮的回"))
    i_u2 = next(i for i, x in enumerate(labels) if x.startswith("local:插一句"))

    assert i_u1 < i_a1 < i_u2, (
        f"插入消息位置不对（应为 问题1 → 回答1 → 插入消息）：{labels}"
    )


def test_injected_message_after_reply_before_next_turn(client):
    """一轮之后又有新回合时：插入消息仍留在它那一回合内，不跑到下一回合后面。"""
    cid = _conv(client)
    u1 = _msg(cid, "第一轮问题", queued=True)
    run1 = _start(client, cid, u1, "第一轮问题")
    _reply(cid, run1["id"], "第一轮的回答正文")
    run_service.finish_run(run1["id"], run_service.STATUS_DONE)

    u2 = _msg(cid, "插一句", queued=True)
    run_service.create_run(cid, "local", content="插一句", queued_msg_id=u2,
                           mode="autonomous")
    run_service.mark_intervention(u2)
    conversation._drain_new_interventions(cid, set(), run1["id"])

    u3 = _msg(cid, "第三轮问题", queued=True)
    run3 = _start(client, cid, u3, "第三轮问题")
    _reply(cid, run3["id"], "第三轮的回答")

    labels = [f"{by}:{text}" for by, text in _order(client, cid)]
    i_a1 = next(i for i, x in enumerate(labels) if "第一轮的回" in x)
    i_u2 = next(i for i, x in enumerate(labels) if x.startswith("local:插一句"))
    i_u3 = next(i for i, x in enumerate(labels) if x.startswith("local:第三轮问题"))

    assert i_a1 < i_u2 < i_u3, f"插入消息跑出了它所属的回合：{labels}"


# ── 3. 回归保护：没有归属的旧数据仍能排 ────────────────────────────────

def test_ordering_tolerates_messages_without_any_run(client):
    """没有 run 锚点的消息（老数据 / 闲聊）不因新逻辑报错，仍按自己的时间戳排。"""
    cid = _conv(client)
    a = _msg(cid, "老消息 A")
    b = _msg(cid, "老消息 B")

    labels = [f"{by}:{text}" for by, text in _order(client, cid)]
    assert labels[0].startswith("local:老消息 A")
    assert labels[1].startswith("local:老消息 B")
    assert a and b


# ── 4. 历史数据：没有归属字段时从事件日志还原 ──────────────────────────

def test_legacy_injected_message_is_recovered_from_event_log(client):
    """老消息上没有 injected_into_run：从 intervention 事件还原归属，位置正确。

    该字段是事后加的，改之前已经发生过很多次「立即插入」（实测库里 10 条）。
    若只认 props，这些历史会话刷新后依旧错位——而「被哪一轮取走」本来就落在
    事件日志里，据此还原不需要数据迁移。
    """
    from services.agent_zero.task_layer import run_service

    cid = _conv(client)
    u1 = _msg(cid, "第一轮问题", queued=True)
    run1 = _start(client, cid, u1, "第一轮问题")
    _reply(cid, run1["id"], "第一轮的回答正文")

    # 模拟「老版本写下的数据」：插入消息、它的排队 run 被取走删掉、
    # 只有事件日志留了痕迹，props 上没有归属。
    u2 = _msg(cid, "老数据里的插入消息", queued=True)
    run_service.create_run(cid, "local", content="老数据里的插入消息",
                           queued_msg_id=u2, mode="autonomous")
    run_service.mark_intervention(u2)
    conversation._drain_new_interventions(cid, set())      # 不传 run_id：不写归属
    props = (conversation.get_message_node(cid, u2) or {}).get("props") or {}
    assert not props.get("injected_into_run"), "前置条件：props 上确实没有归属"

    # 但事件日志里有一条 intervention 记录（这正是线上老数据的形态）
    run_service.append_event(run1["id"], "event",
                             {"intervention": {"id": u2, "content": "老数据里的插入消息"}})

    labels = [f"{by}:{text}" for by, text in _order(client, cid)]
    i_a1 = next(i for i, x in enumerate(labels) if x.startswith("assistant:第一轮的回"))
    i_u2 = next(i for i, x in enumerate(labels) if x.startswith("local:老数据里的"))

    assert i_a1 < i_u2, f"历史插入消息未从事件日志还原归属，位置仍错：{labels}"

"""排队消息：不被当前 run 当作「干预」吞掉，且转录位置落在上一轮回复之后。

复现自 2026-10-30 的真实故障（conv-629d43c53260）：

- 16:38:33 用户发 u1 → run-f67700e57451 开始跑；
- 16:40:02 用户又发 u2（u1 还在跑）→ 落库为**排队消息**，建 run-7d1e940548f4；
- 16:46:10 u1 的 run 收尾；
- 16:48:27 用户发 u3 → 建 run-53a8db3e5eb6（**事后从库里消失，事件 0 条**）。

两条独立缺陷：

1. **干预误吞**：`_drain_new_interventions` 只按消息上的 `queued` 标记判断，而
   该标记会被认领 / 编辑 / 异常路径清成 0。一旦为 0，排队消息就被正在执行的
   run 当成「对话内干预」取走，紧接着 `cancel_run_by_message` 删掉它的排队 run
   ——消息既没单独跑一轮，也没有任何回复。实测证据：run-243763b366cd 的事件
   seq=166 是 `{"intervention": {"id": "msg-be9e49bdd87e"}}`，而它的排队 run 已
   被删除、事件数为 0。
   → 判定改为以 **run 表**为准（还有 queued run 等它执行 = 不是干预）。

2. **转录位置错**：`list_messages` 按 `created_at` 排序，而排队消息的落库时刻
   早于它所属 run 的启动时刻，于是 u2 出现在 a1 **之前**（用户看到的：排队消息
   夹在上一轮回复上面 / 下面错位）。
   → 改为按「所属 run 的启动时刻」排（助手回复用 props.run_id，用户消息用
   queued_msg_id 反查），尚未启动的排队 run 用它自己的 created_at。
"""
from __future__ import annotations

from services.agent_zero.task_layer import conversation, run_service

API = "/api/agent-zero"


def _conv(client) -> str:
    return client.post(f"{API}/conversations", json={}).json()["data"]["id"]


def _msg(conv_id: str, content: str, *, queued: bool = False) -> str:
    """直接落一条用户消息（不经 HTTP，避免触发 run 调度）。"""
    return conversation.send_message(conv_id, content, "local", queued=queued)["id"]


def _order(client, conv_id: str) -> list:
    """转录顺序：[(by, content 前缀)]，用于断言展示位置。"""
    data = client.get(f"{API}/conversations/{conv_id}/messages").json()["data"]
    items = data.get("items") if isinstance(data, dict) else data
    return [(m.get("by") or "", str(m.get("content") or "")[:6]) for m in items]


# ── 缺陷 1：排队消息不被当成干预吞掉 ────────────────────────────────

def test_queued_message_is_not_drained_as_intervention(client):
    """有排队 run 等着的消息，不能被执行中的 run 当干预取走（取走=吞掉）。"""
    cid = _conv(client)
    queued_msg = _msg(cid, "这条是排队消息", queued=True)
    run = run_service.create_run(cid, "local", content="这条是排队消息",
                                 queued_msg_id=queued_msg, mode="autonomous")
    assert run["status"] == "queued"

    # 模拟执行中的 run 每轮取干预：seen 先只放一条无关 id
    drained = conversation._drain_new_interventions(cid, {"msg-unrelated"})

    assert [m["id"] for m in drained] == [], (
        "排队消息被当成干预取走了——它的 run 随后会被 cancel_run_by_message 删掉，"
        "用户看到的就是「排队消息不自动处理」"
    )
    # 关键：它的排队 run 必须还在，等 worker 认领
    assert [r["id"] for r in run_service.conversation_queue(cid)] == [run["id"]]


def test_queued_message_survives_drain_and_still_gets_scheduled(client):
    """取干预后排队 run 仍在 → 后续被 worker 认领（不丢消息）。"""
    cid = _conv(client)
    qmsg = _msg(cid, "排队的第二轮", queued=True)
    run = run_service.create_run(cid, "local", content="排队的第二轮",
                                 queued_msg_id=qmsg, mode="autonomous")
    conversation._drain_new_interventions(cid, set())

    assert run_service.conversation_queue(cid), "排队 run 不该被删"
    assert run_service.get_run(run["id"])["status"] == "queued"


def test_plain_message_without_run_is_still_drained_as_intervention(client):
    """回归保护：真正的新消息（没有排队 run）仍要能作为干预注入。"""
    cid = _conv(client)
    mid = _msg(cid, "补充一句：先别改 CSS")

    drained = conversation._drain_new_interventions(cid, set())

    assert [m["id"] for m in drained] == [mid]


def test_legacy_inject_message_bypasses_queue_guard(client):
    """历史数据里的「立即插入」（inject=1）仍要能无视排队守卫被取走。

    V2 起「立即插入」改成「停止当前回复，再把这条当下一轮跑」，不再写 inject=1
    （见 `run_service.inject_run`）。但线上已有会话里存着这种标记，取干预 / 排序
    逻辑仍必须认得它，否则老会话的干预消息会被当成排队消息一直挂着。
    """
    cid = _conv(client)
    qmsg = _msg(cid, "立即插入这条", queued=True)
    run_service.create_run(cid, "local", content="立即插入这条",
                           queued_msg_id=qmsg, mode="autonomous")
    assert run_service.mark_intervention(qmsg) is True

    drained = conversation._drain_new_interventions(cid, set())

    assert [m["id"] for m in drained] == [qmsg], "显式 inject 的消息必须被取走"


def test_inject_run_only_moves_to_head_and_keeps_message_queued(client):
    """「立即插入」= 提到队首 + 消息保持排队（由当前 run 停下后执行）。

    不再把消息标成干预：一旦标了，正在执行的 run 会把消息取走、继续在原位输出，
    界面就会出现「输出还在原位、消息却跳位」的错乱（用户反馈的那条）。
    """
    cid = _conv(client)
    first = _msg(cid, "先跑这条", queued=True)
    run_service.create_run(cid, "local", content="先跑这条", queued_msg_id=first,
                           mode="autonomous")
    target = _msg(cid, "要立刻跑这条", queued=True)
    run = run_service.create_run(cid, "local", content="要立刻跑这条",
                                 queued_msg_id=target, mode="autonomous")

    assert run_service.inject_run(run["id"]) is True

    props = (conversation.get_message_node(cid, target) or {}).get("props") or {}
    assert int(props.get("inject") or 0) == 0, "新语义不再写 inject=1"
    assert int(props.get("queued") or 0) == 1, "消息仍是排队消息，等当前 run 停下后执行"
    assert [r["id"] for r in run_service.conversation_queue(cid)][0] == run["id"]
    assert [m["id"] for m in conversation._drain_new_interventions(cid, set())] == [], (
        "提到队首的消息不能被正在执行的 run 当干预吞掉"
    )


def test_mark_queued_message_active_clears_inject_flag(client):
    """run 认领后清 inject 标记，避免这条消息以后再被当干预重复注入。"""
    cid = _conv(client)
    qmsg = _msg(cid, "立即插入后又被认领", queued=True)
    run = run_service.create_run(cid, "local", content="立即插入后又被认领",
                                 queued_msg_id=qmsg, mode="autonomous")
    run_service.inject_run(run["id"])
    conversation.mark_queued_message_active(qmsg)

    node = conversation.get_message_node(cid, qmsg)
    props = (node or {}).get("props") or {}
    assert int(props.get("queued") or 0) == 0
    assert int(props.get("inject") or 0) == 0


def test_queued_message_not_in_current_run_context(client):
    """排队消息不能进当前 run 的上下文：否则当前 run 会替它执行一遍。

    只靠消息上的 `queued` 标记不够——标记会被清掉，这里显式清掉后再验证。
    """
    cid = _conv(client)
    u1 = _msg(cid, "第一轮问题")
    conversation.set_message_queued(u1, False)
    qmsg = _msg(cid, "排队中的第二轮", queued=True)
    run = run_service.create_run(cid, "local", content="排队中的第二轮",
                                 queued_msg_id=qmsg, mode="autonomous")
    # 模拟标记被意外清掉（认领 / 编辑 / 异常路径）
    conversation.set_message_queued(qmsg, False)

    ctx = conversation._history_messages(cid, limit=50)
    joined = " ".join(str(m.get("content") or "") for m in ctx)

    assert "排队中的第二轮" not in joined, (
        "排队消息进了当前上下文——当前 run 会提前看到并「顺便」执行它"
    )
    assert "第一轮问题" in joined
    assert run_service.get_run(run["id"])["status"] == "queued"


# ── 缺陷 2：排队消息的转录位置 ──────────────────────────────────────

def test_queued_message_renders_after_previous_reply(client):
    """排队消息排在**上一轮回复之后**，而不是用户消息下面 / 回复上面。"""
    cid = _conv(client)
    u1 = _msg(cid, "第一轮问题")
    run1 = run_service.create_run(cid, "local", content="第一轮问题",
                                  queued_msg_id=u1, mode="autonomous")
    assert run_service.claim_run(run1["id"])
    # 第一轮回复落库（同一个 run）
    conversation._persist_assistant(
        conversation.get_conversation(cid), {"id": "default", "name": "Atlas"},
        "第一轮的回复正文", "test-model", 10, run_id=run1["id"],
    )
    run_service.finish_run(run1["id"], run_service.STATUS_DONE)

    # 但 u2 是在 run1 **还在跑**的时候发出的：消息先落库，run 后启动
    u2 = _msg(cid, "排队发送的第二轮", queued=True)
    run2 = run_service.create_run(cid, "local", content="排队发送的第二轮",
                                  queued_msg_id=u2, mode="autonomous")
    conversation.set_message_queued(u2, True)

    order = _order(client, cid)
    labels = [f"{by}:{text}" for by, text in order]

    i_u1 = next(i for i, x in enumerate(labels) if x.startswith("local:第一轮问题"))
    i_a1 = next(i for i, x in enumerate(labels) if x.startswith("assistant:第一轮的回复"))
    i_u2 = next(i for i, x in enumerate(labels) if x.startswith("local:排队发送的"))

    assert i_u1 < i_a1 < i_u2, (
        f"排队消息位置不对（应为 问题1 → 回复1 → 排队消息2）：{labels}"
    )
    assert run_service.get_run(run2["id"])["status"] == "queued"

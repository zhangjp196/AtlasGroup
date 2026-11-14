"""纯消息排序逻辑，与会话持久化和执行编排隔离。"""

from __future__ import annotations

from typing import Any, Optional


def order_messages_by_turn(messages: list, msg_run_anchor: dict,
                            run_anchor_by_id: Optional[dict] = None) -> list:
    """按「真实回合顺序」重排消息，返回重排后的列表。

    排序锚点 = (该消息所属 run 的启动时刻, run 行序号)：
      - 助手回复：用 `props.run_id` 找那一轮 run；
      - 用户消息：用 `queued_msg_id` 反查（见 msg_run_anchor）；「立即插入」被取走的
        消息用它自己的 `injected_into_run`；
      - 只服务于某个回合、本身不产出内容的助手消息（如成员「收到，我在后台执行」
        的 task_ack）：用 `anchor_run_id` 归到那一轮，排在回合内的 @ 消息之后；
      - 尚未启动的排队 run（started_at 为空）→ 用 run 自己的 created_at；
      - 没有 run 的消息（闲聊 / 历史遗留）→ 自己的 ts。

    为什么必须这样：队列模式下用户消息落库时刻早于它所属 run 的启动时刻。
    只按 created_at 排，排队消息会出现在上一轮回复**之前**，看起来像是回复答错了
    对象（实测：u2 排在 a1 前）。按 run 锚点重排后，排队消息落到它真正执行的位置。

    锚点相同时：用户消息（回合发起）在前、助手回复在后；「立即插入」的消息排在最后
    （它是插进那一轮回复里的补充，用户期望看到的位置是**那条回答之后**）；再相同则
    保持原始顺序。
    """
    if not messages:
        return messages
    run_anchor_by_id = run_anchor_by_id or {}

    def _norm(stamp: Any) -> str:
        """统一时间格式：props.ts 是 ISO（`2026-10-30T16:48:27`），而库里
        created_at / started_at 是 `2026-10-30 16:48:27`（空格分隔）。
        两者直接做字符串比较会错位——`T`(0x54) 比空格(0x20) 大，同一分钟内的
        ISO 时间会被判成「更晚」，顺序整个反过来。这里统一成空格分隔。"""
        return str(stamp or "").replace("T", " ")

    keyed = []
    for idx, m in enumerate(messages):
        mid = str(m.get("id") or "")
        anchor = None
        # 助手回复按它自己的 run 取锚点（最准确）
        if str(m.get("by") or "") == "assistant":
            anchor = run_anchor_by_id.get(str(m.get("run_id") or ""))
        if anchor is None:
            anchor = msg_run_anchor.get(mid)
        if anchor is None:
            # 「立即插入」被某轮取走：它的排队 run 已删，只能靠这个归属回锚，
            # 否则会退回自己的 created_at 而夹到当前回答**上面**。
            anchor = run_anchor_by_id.get(str(m.get("injected_into_run") or ""))
        if anchor is None:
            # 确认消息等「不产出内容、只代表这一回合开始」的助手消息：归到那一轮，
            # 与 @ 消息共用锚点，再靠下面的 role_rank 排在其后。
            anchor = run_anchor_by_id.get(str(m.get("anchor_run_id") or ""))
        if anchor is not None:
            anchor = (_norm(anchor[0]), anchor[1])
        else:
            # 没有 run：退化成按自己的时间戳排（run 行序号取 0，最早）
            anchor = (_norm(m.get("ts")), 0)
        # 同一锚点内：用户消息（发起本回合）在前、助手回复居中、立即插入的最后
        by = str(m.get("by") or "")
        if by == "assistant":
            role_rank = 1
        elif m.get("injected_into_run"):
            role_rank = 2
        else:
            role_rank = 0
        keyed.append(((anchor, role_rank, idx), m))
    keyed.sort(key=lambda x: x[0])
    return [m for _k, m in keyed]

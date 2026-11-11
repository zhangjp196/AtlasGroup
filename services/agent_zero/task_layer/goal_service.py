"""目标模式：对话级长程目标，run 结束后自动续跑，直到完成 / 预算耗尽。

与「自主执行」（单个 run 内动态规划推进）互补：目标模式跨 run——每次 run
收尾后若目标仍未达成且预算未用完，系统自动入队一条续跑 run，并把目标与
进度注入系统提示。模型用 `goal_report` 工具汇报进展或宣布完成。

状态：
  active     —— 进行中（会自动续跑）
  achieved   —— 模型宣布完成（goal_report status=achieved）
  exhausted  —— token 预算或续跑次数用尽
  closed     —— 用户手动结束
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores.zero_store import _q, append_audit, new_id

logger = logging.getLogger(__name__)

# 0 = 自动续跑次数不限（只受 token_budget 约束，token_budget=0 也不限）。
DEFAULT_MAX_RUNS = 0
# 「等待用户」标记：写进 note 前缀，不新增数据库列；after_run_finished 看到它
# 就暂停自动续跑，直到模型报告 progress（或 goal_manage resume）把前缀清掉。
BLOCKED_PREFIX = "⏸ 等待用户："


def _normalize(row: Dict[str, Any]) -> Dict[str, Any]:
    for k in ("created_at", "updated_at"):
        if row.get(k) is not None:
            row[k] = str(row[k])
    return row


def get_goal(conversation_id: str) -> Optional[Dict[str, Any]]:
    """该对话最近一条目标（不论状态）；无则 None。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('goals')} WHERE conversation_id = :cid "
                 f"ORDER BY created_at DESC, id DESC LIMIT 1"),
            {"cid": conversation_id},
        ).mappings().first()
    return _normalize(dict(row)) if row else None


def active_goal(conversation_id: str) -> Optional[Dict[str, Any]]:
    goal = get_goal(conversation_id)
    return goal if goal and goal.get("status") == "active" else None


def is_blocked(goal: Optional[Dict[str, Any]]) -> bool:
    """目标是否处于「等待用户」状态（note 前缀标记）。"""
    return bool(goal) and str(goal.get("note") or "").startswith(BLOCKED_PREFIX)


def set_goal(conversation_id: str, objective: str,
             token_budget: Optional[int] = None,
             max_runs: Optional[int] = None) -> Dict[str, Any]:
    """创建（或替换）目标：旧的目标关闭，新的目标开始。"""
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("目标为空")
    budget = max(0, int(token_budget or 0))
    runs = max(0, int(max_runs if max_runs is not None else DEFAULT_MAX_RUNS))
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('goals')} SET status = 'closed', updated_at = datetime('now','localtime') "
                 f"WHERE conversation_id = :cid AND status = 'active'"),
            {"cid": conversation_id},
        )
        goal_id = new_id("goal")
        db.execute(
            text(f"INSERT INTO {_q('goals')} "
                 f"(id, conversation_id, objective, status, token_budget, max_runs) "
                 f"VALUES (:id, :cid, :obj, 'active', :budget, :runs)"),
            {"id": goal_id, "cid": conversation_id, "obj": objective[:4000],
             "budget": budget, "runs": runs},
        )
        db.commit()
    append_audit("local", "goal_set",
                 args={"conversation_id": conversation_id, "goal_id": goal_id,
                       "objective_chars": len(objective), "token_budget": budget,
                       "max_runs": runs})
    return get_goal(conversation_id) or {"id": goal_id, "status": "active"}


def update_goal(conversation_id: str, *, objective: Optional[str] = None,
                token_budget: Optional[int] = None,
                max_runs: Optional[int] = None) -> Optional[Dict[str, Any]]:
    goal = get_goal(conversation_id)
    if not goal:
        return None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        if objective is not None and str(objective).strip():
            db.execute(
                text(f"UPDATE {_q('goals')} SET objective = :obj, updated_at = datetime('now','localtime') "
                     f"WHERE id = :id"),
                {"obj": str(objective).strip()[:4000], "id": goal["id"]},
            )
        if token_budget is not None:
            db.execute(
                text(f"UPDATE {_q('goals')} SET token_budget = :b, updated_at = datetime('now','localtime') "
                     f"WHERE id = :id"),
                {"b": max(0, int(token_budget)), "id": goal["id"]},
            )
        if max_runs is not None:
            db.execute(
                text(f"UPDATE {_q('goals')} SET max_runs = :m, updated_at = datetime('now','localtime') "
                     f"WHERE id = :id"),
                {"m": max(0, int(max_runs)), "id": goal["id"]},
            )
        db.commit()
    return get_goal(conversation_id)


def close_goal(conversation_id: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('goals')} SET status = 'closed', updated_at = datetime('now','localtime') "
                 f"WHERE conversation_id = :cid AND status = 'active'"),
            {"cid": conversation_id},
        )
        db.commit()
        return bool(res.rowcount)


def _compose_report_note(note: str, progress_pct: Any = None,
                         next_steps: Any = None, blockers: Any = None,
                         evidence: str = "") -> str:
    parts: list[str] = []
    if str(note or "").strip():
        parts.append(str(note).strip())
    try:
        if progress_pct is not None:
            pct = max(0, min(100, int(progress_pct)))
            parts.append(f"完成度 {pct}%")
    except (TypeError, ValueError):
        pass
    def _list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []
    nxt = _list(next_steps)
    if nxt:
        parts.append("下一步：" + "；".join(nxt[:12]))
    blk = _list(blockers)
    if blk:
        parts.append("阻塞：" + "；".join(blk[:12]))
    if str(evidence or "").strip():
        parts.append("证据：" + str(evidence).strip())
    return " | ".join(parts)[:4000]


def report_progress(conversation_id: str, status: str, note: str = "", *,
                    progress_pct: Any = None, next_steps: Any = None,
                    blockers: Any = None, evidence: str = "") -> Dict[str, Any]:
    """goal_report 工具的后端：progress 记进展，blocked 暂停续跑，achieved 结束目标。"""
    goal = active_goal(conversation_id)
    if not goal:
        return {"error": "当前没有进行中的目标（目标模式由用户在界面开启）"}
    status = (status or "").strip().lower()
    if status not in ("progress", "blocked", "achieved"):
        return {"error": f"非法状态: {status}（progress / blocked / achieved）"}
    report_note = _compose_report_note(note, progress_pct, next_steps, blockers,
                                      evidence)
    if status == "blocked":
        report_note = (BLOCKED_PREFIX + (report_note or "需要用户确认后继续"))[:4000]
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        if status == "achieved":
            db.execute(
                text(f"UPDATE {_q('goals')} SET status = 'achieved', note = :note, "
                     f"updated_at = datetime('now','localtime') WHERE id = :id"),
                {"note": report_note or None, "id": goal["id"]},
            )
        else:
            db.execute(
                text(f"UPDATE {_q('goals')} SET note = :note, updated_at = datetime('now','localtime') "
                     f"WHERE id = :id"),
                {"note": report_note or None, "id": goal["id"]},
            )
        db.commit()
    append_audit("local", "goal_report",
                 args={"conversation_id": conversation_id, "goal_id": goal["id"],
                       "status": status, "note_chars": len(report_note or "")})
    return get_goal(conversation_id) or goal


def manage(conversation_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """`goal_manage` 工具的后端：查看 / 调整 / 暂停 / 恢复 / 结束目标。"""
    action = str((args or {}).get("action") or "status").strip().lower()
    goal = get_goal(conversation_id)
    if not goal:
        return {"error": "当前对话没有目标（目标模式由用户在界面开启）"}
    if action in ("status", "get"):
        return {"ok": True, "goal": goal, "blocked": is_blocked(goal)}
    if action == "update":
        if goal.get("status") != "active":
            return {"error": f"目标已结束（{goal.get('status')}），不能再修改"}
        updated = update_goal(
            conversation_id,
            objective=args.get("objective"),
            token_budget=args.get("token_budget"),
            max_runs=args.get("max_runs"))
        return {"ok": True, "goal": updated or get_goal(conversation_id)}
    if action == "note":
        out = report_progress(conversation_id, "progress",
                              str(args.get("note") or ""))
        if out.get("error"):
            return out
        return {"ok": True, "goal": out}
    if action == "blocked":
        out = report_progress(conversation_id, "blocked",
                              str(args.get("note") or ""))
        if out.get("error"):
            return out
        return {"ok": True, "goal": out}
    if action == "resume":
        if goal.get("status") != "active":
            return {"error": f"目标已结束（{goal.get('status')}），无法恢复"}
        out = report_progress(conversation_id, "progress",
                              str(args.get("note") or "已收到用户确认，继续推进目标"))
        return out if out.get("error") else {"ok": True, "goal": out, "blocked": False}
    if action in ("close", "stop"):
        reason = str(args.get("note") or "").strip()
        if reason:
            report_progress(conversation_id, "progress", reason)
        ok = close_goal(conversation_id)
        return {"ok": bool(ok), "goal": get_goal(conversation_id)}
    return {"error": f"非法 action: {action}"}


def continue_prompt(goal: Dict[str, Any]) -> str:
    """自动续跑时注入的用户消息文本。"""
    budget = int(goal.get("token_budget") or 0)
    used = int(goal.get("tokens_used") or 0)
    runs_used = int(goal.get("runs_used") or 0)
    max_runs = int(goal.get("max_runs") or 0)
    budget_txt = f"token 已用 {used} / 预算 {budget}" if budget else f"token 已用 {used}（不限预算）"
    runs_txt = f"上限 {max_runs} 次" if max_runs > 0 else "不限次数"
    return (
        f"（目标模式自动继续）目标：{goal.get('objective')}\n"
        f"这是第 {runs_used + 1} 次运行（{runs_txt}；{budget_txt}）。"
        "继续推进目标：先回顾已完成的步骤，再执行下一步；"
        "目标完成时调用 goal_report(status=\"achieved\") 并给出结论摘要。"
    )


def _account_run(conversation_id: str, usage: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把这一轮 run 的 token / 次数记到目标上，返回刷新后的目标。"""
    goal = active_goal(conversation_id)
    if not goal:
        return None
    tokens = int(usage.get("prompt_tokens") or usage.get("prompt") or 0) + \
        int(usage.get("completion_tokens") or usage.get("completion") or 0)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('goals')} SET tokens_used = tokens_used + :t, "
                 f"runs_used = runs_used + 1, updated_at = datetime('now','localtime') WHERE id = :id"),
            {"t": tokens, "id": goal["id"]},
        )
        db.commit()
    return get_goal(conversation_id) or goal


def _budget_available(goal: Dict[str, Any]) -> bool:
    """预算 / 续跑次数还有余额；用尽则把目标标成 exhausted。"""
    budget = int(goal.get("token_budget") or 0)
    used = int(goal.get("tokens_used") or 0)
    runs_used = int(goal.get("runs_used") or 0)
    max_runs = int(goal.get("max_runs") or 0)
    if not ((budget and used >= budget) or (max_runs > 0 and runs_used >= max_runs)):
        return True
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('goals')} SET status = 'exhausted', updated_at = datetime('now','localtime') "
                 f"WHERE id = :id AND status = 'active'"),
            {"id": goal["id"]},
        )
        db.commit()
    logger.info("目标 %s 预算/次数用尽（runs=%s/%s, tokens=%s/%s），停止自动续跑",
                goal["id"], runs_used, max_runs or "∞", used, budget or "∞")
    return False


def _enqueue_continuation(conversation_id: str, run_id: str,
                          prompt: str) -> Optional[Dict[str, Any]]:
    """入队一条自动续跑 run（沿用上一轮的思考级别）。"""
    from services.agent_zero.task_layer import conversation, run_service

    try:
        previous = run_service.get_run(run_id) or {}
        msg = conversation.send_message(conversation_id, prompt, "system", queued=True)
        return run_service.create_run(
            conversation_id, "system",
            content=prompt, mode="autonomous",
            queued_msg_id=msg.get("id"),
            thinking_level=previous.get("thinking_level"),
        )
    except Exception as e:  # 续跑失败不影响已完成的 run
        logger.warning("目标模式自动续跑入队失败: %s", e)
        return None


def _can_continue(goal: Optional[Dict[str, Any]]) -> bool:
    if not goal or goal.get("status") != "active":
        return False
    # 模型标记「等待用户」：暂停自动续跑，等用户回复 / goal_manage(resume)。
    if is_blocked(goal):
        logger.info("目标 %s 处于等待用户状态，暂停自动续跑", goal["id"])
        return False
    return _budget_available(goal)


def after_run_finished(conversation_id: str, run_id: str,
                       usage: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """run 成功收尾后：记账 + 若目标仍 active 则入队续跑 run。返回续跑 run（若有）。

    只在 run 状态为 done 时调用；用户停止的 run 不自动续跑（失败走
    ``after_run_failed``，同样自动继续）。
    """
    goal = _account_run(conversation_id, usage)
    if not _can_continue(goal):
        return None
    return _enqueue_continuation(conversation_id, run_id, continue_prompt(goal))


def after_run_failed(conversation_id: str, run_id: str, error: str = "",
                     usage: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """run 失败后：目标仍 active 就自动入队一条「诊断 + 重试」run。

    失败不再直接停在工单上等人：先让角色自己看错误、修环境 / 改做法，改不动
    再用 goal_report(status="blocked") 交回人。成员 run（team_task_id）不在这里
    续跑——后台任务失败由任务层统一交回主持。
    """
    from services.agent_zero.task_layer import run_service

    run = run_service.get_run(run_id) or {}
    if str(run.get("team_task_id") or "").strip():
        return None
    goal = _account_run(conversation_id, usage or {})
    if not _can_continue(goal):
        return None
    prompt = (
        f"（失败后自动继续）目标：{goal.get('objective')}\n"
        f"上一轮运行失败：{error or run.get('error') or '未知错误'}\n"
        "先判断失败原因（环境 / 依赖 / 权限 / 命令写错），能修的先修再重试，"
        "不要重复同一种失败做法；需要人决定时调用 goal_report(status=\"blocked\")，"
        "真正完成时调用 goal_report(status=\"achieved\") 并给出结论。"
    )
    return _enqueue_continuation(conversation_id, run_id, prompt)

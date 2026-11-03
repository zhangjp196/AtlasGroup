"""前台工具 / 子代理的实时活动登记：让「进程」面板看得见正在跑的东西。

`tool_jobs` 只登记「超过前台预算、被转后台」的任务。仍在预算内正常等待的
shell / read_file / call_subagent 等并不在册，于是进程面板里常常只剩一条
「对话运行」——用户看不到当前到底在跑什么（2026-10-29 现场反馈）。

这里做一份**只读的轻量登记**：工具开始时 `begin`，结束时 `end`（异常 /
取消 / 运行收尾时由 `clear_conversation` 兜底）。它不持有任务、不改变
执行语义，只为进程面板提供 name / args / 开始时间 / 耗时。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

# 这些工具本质是「派生子代理」，进程面板要显示成子代理而不是普通工具。
SUBAGENT_NAMES = frozenset({
    "call_subagent", "spawn_subagent", "call_agent", "call_role",
    "spawn_agent", "call_team",
})

_ACTIVE: Dict[str, Dict[str, Any]] = {}
# 异常场景下的兜底上限，防止登记表无限增长（正常路径都会 end）。
MAX_ACTIVE = 500


def kind_for(name: str) -> str:
    """工具名 → 进程面板的类型（tool / subagent）。"""
    return "subagent" if str(name or "") in SUBAGENT_NAMES else "tool"


def begin(*, conversation_id: str, run_id: str = "", name: str,
          args: Optional[Dict[str, Any]] = None, call_id: str = "",
          started_at: Optional[float] = None) -> str:
    """登记一次前台工具调用，返回进程 id（结束时传给 end）。"""
    process_id = "proc-" + uuid.uuid4().hex[:12]
    _ACTIVE[process_id] = {
        "id": process_id,
        "conversation_id": conversation_id or "",
        "run_id": run_id or "",
        "name": name or "",
        "args": args or {},
        "call_id": call_id or "",
        "kind": kind_for(name),
        "started_at": float(started_at or time.time()),
        "_task": None,
    }
    if len(_ACTIVE) > MAX_ACTIVE:
        # 最老的先丢：正常情况下不会走到这里，纯属异常兜底。
        for old in sorted(_ACTIVE, key=lambda k: _ACTIVE[k]["started_at"])[:len(_ACTIVE) - MAX_ACTIVE]:
            _ACTIVE.pop(old, None)
    return process_id


def end(process_id: str) -> None:
    if process_id:
        _ACTIVE.pop(str(process_id), None)


def attach(process_id: str, task: Any) -> None:
    """把执行这次调用的 asyncio 任务挂到登记项上，供进程面板单独停止。"""
    rec = _ACTIVE.get(str(process_id or ""))
    if rec is not None:
        rec["_task"] = task


def kill(process_id: str) -> bool:
    """取消一次**仍在执行**的前台工具/子代理（不停止整轮对话）。

    调用方（conversation 的等待循环）会把 inner task 的取消当成「工具已取消」，
    继续走下一轮；只有外层 run 被取消时才会向上抛 CancelledError。
    """
    rec = _ACTIVE.get(str(process_id or ""))
    if rec is None:
        return False
    task = rec.get("_task")
    if task is None:
        return False
    try:
        if task.done():
            return False
        task.cancel()
        return True
    except Exception:
        return False


def list_active(conversation_id: str) -> List[Dict[str, Any]]:
    """该会话当前仍在跑的前台工具 / 子代理（按开始时间排序）。"""
    now = time.time()
    items: List[Dict[str, Any]] = []
    for rec in list(_ACTIVE.values()):
        if rec.get("conversation_id") != (conversation_id or ""):
            continue
        items.append({
            "id": rec["id"],
            "kind": rec.get("kind") or "tool",
            "name": rec.get("name") or "",
            "status": "running",
            "elapsed_s": round(max(0.0, now - float(rec.get("started_at") or 0)), 1),
            "started_at": float(rec.get("started_at") or 0),
            "args": rec.get("args") or {},
            "run_id": rec.get("run_id") or "",
            "call_id": rec.get("call_id") or "",
            "summary": "",
            "stoppable": True,
        })
    items.sort(key=lambda it: float(it.get("elapsed_s") or 0), reverse=True)
    return items


def clear_conversation(conversation_id: str) -> None:
    """运行收尾 / 强制停止时清掉该会话的所有前台登记。"""
    cid = conversation_id or ""
    for process_id, rec in list(_ACTIVE.items()):
        if rec.get("conversation_id") == cid:
            _ACTIVE.pop(process_id, None)



"""ask_user：向用户弹出选择框并等待回答（运行中阻塞，超时/停止自动忽略）。

生命周期：
1. 工具 `ask_user` 创建问题 → `append_event(run_id, "ask_user", ...)`；
2. 前端弹窗 → `POST /questions/{id}` 提交；
3. 工具被唤醒，把结果作为工具结果回给模型；同时发 `ask_user_done`。

状态只保存在进程内：进程重启后，等待中的问题随运行一起被回收（run 恢复会
把 running 收尾），不会留下“永远等”的僵尸问题。
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from services.agent_zero.stores import zero_store

logger = logging.getLogger(__name__)

ASK_TIMEOUT_S = 300.0

_pending: Dict[str, Dict[str, Any]] = {}
_current_run: ContextVar[str] = ContextVar("atlas_current_run", default="")


def set_current_run(run_id: str) -> None:
    _current_run.set(str(run_id or ""))


def current_run_id() -> str:
    return str(_current_run.get() or "")


def _clean_options(raw: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in raw or []:
        if isinstance(item, str) and item.strip():
            out.append({"label": item.strip(), "description": ""})
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("text") or item.get("value") or "").strip()
            if not label:
                continue
            out.append({"label": label,
                        "description": str(item.get("description") or item.get("hint") or "").strip()})
        if len(out) >= 20:
            break
    return out


def create(conv_id: str, run_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    args = args or {}
    question = str(args.get("question") or args.get("prompt") or "").strip()
    qid = zero_store.new_id("ask")
    payload = {
        "id": qid,
        "question": question or "请选择",
        "options": _clean_options(args.get("options")),
        "multi": bool(args.get("multi") or args.get("multiple")),
        "allow_other": args.get("allow_other") is not False,
    }
    _pending[qid] = {
        "event": asyncio.Event(),
        "answer": None,
        "payload": payload,
        "conversation_id": conv_id,
        "run_id": run_id,
        "created": time.time(),
    }
    return payload


def answer(question_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    slot = _pending.get(question_id)
    if slot is None:
        return None
    if slot["event"].is_set():
        return slot.get("answer")
    data = data or {}
    ignored = bool(data.get("ignored"))
    selected = [str(x) for x in (data.get("selected") or []) if str(x).strip()]
    other = str(data.get("other") or "").strip()
    if ignored or (not selected and not other):
        result = {"id": question_id, "ignored": True, "selected": [], "other": ""}
    else:
        result = {"id": question_id, "ignored": False,
                  "selected": selected, "other": other}
    slot["answer"] = result
    slot["event"].set()
    return result


def get(question_id: str) -> Optional[Dict[str, Any]]:
    slot = _pending.get(question_id)
    if slot is None:
        return None
    return {**slot["payload"], "pending": not slot["event"].is_set(),
            "answer": slot.get("answer")}


def pending_for(conv_id: str) -> List[Dict[str, Any]]:
    return [{**slot["payload"], "pending": True}
            for slot in _pending.values()
            if slot["conversation_id"] == conv_id and not slot["event"].is_set()]


def pending_all() -> List[Dict[str, Any]]:
    """跨会话的待回答问题（全局 Agent 面板 / 通知角标用）。"""
    out: List[Dict[str, Any]] = []
    for slot in _pending.values():
        if slot["event"].is_set():
            continue
        out.append({
            **slot["payload"],
            "pending": True,
            "conversation_id": slot.get("conversation_id") or "",
            "run_id": slot.get("run_id") or "",
        })
    out.sort(key=lambda x: x.get("id") or "", reverse=True)
    return out


async def wait(question_id: str, run_id: Optional[str] = None,
               timeout: float = ASK_TIMEOUT_S) -> Dict[str, Any]:
    slot = _pending.get(question_id)
    if slot is None:
        return {"id": question_id, "ignored": True, "reason": "missing"}
    from services.agent_zero.task_layer import run_service

    waited = 0.0
    while not slot["event"].is_set():
        if run_id and run_service.is_stop_requested(run_id):
            result = {"id": question_id, "ignored": True, "reason": "stopped"}
            slot["answer"] = result
            _pending.pop(question_id, None)
            return result
        if waited >= timeout:
            result = {"id": question_id, "ignored": True, "reason": "timeout"}
            slot["answer"] = result
            _pending.pop(question_id, None)
            return result
        try:
            await asyncio.wait_for(slot["event"].wait(), timeout=1.0)
        except asyncio.TimeoutError:
            waited += 1.0
    result = slot.get("answer") or {"id": question_id, "ignored": True}
    _pending.pop(question_id, None)
    return result


def cancel_run(run_id: str) -> int:
    """运行被强制停止：唤醒所有等待中的问题（按忽略处理）。"""
    n = 0
    for qid, slot in list(_pending.items()):
        if slot.get("run_id") == run_id and not slot["event"].is_set():
            slot["answer"] = {"id": qid, "ignored": True, "reason": "stopped"}
            slot["event"].set()
            n += 1
    return n

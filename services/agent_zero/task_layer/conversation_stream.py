"""Pure helpers for streaming conversation events and context usage."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from services.agent_zero.task_layer.conversation_context import (
    _compress_done_event,
    _compress_start_event,
)


def _trace_from_run_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rebuild the visible partial timeline after a stopped or crashed run."""
    trace: List[Dict[str, Any]] = []
    open_text: Dict[str, Any] | None = None
    tools: Dict[str, Dict[str, Any]] = {}
    thinking_parts: List[str] = []

    def close_text() -> None:
        nonlocal open_text
        open_text = None

    for event in events or []:
        if not isinstance(event, dict) or event.get("ping"):
            continue
        if "delta" in event:
            piece = str(event.get("delta") or "")
            if not piece:
                continue
            if open_text is None:
                open_text = {"kind": "text", "round": event.get("round"), "text": ""}
                trace.append(open_text)
            open_text["text"] += piece
        elif "thinking" in event:
            piece = str(event.get("thinking") or "")
            if not piece:
                continue
            close_text()
            thinking_parts.append(piece)
            if (trace and trace[-1].get("kind") == "thinking"
                    and trace[-1].get("round") == event.get("round")):
                trace[-1]["text"] = str(trace[-1].get("text") or "") + piece
            else:
                trace.append({"kind": "thinking", "round": event.get("round"),
                              "text": piece})
        elif "tool_call" in event:
            call = event.get("tool_call") or {}
            call_id = str(call.get("id") or "")
            if not call.get("name"):
                continue
            close_text()
            hit = tools.get(call_id) if call_id else None
            if hit is None and call.get("status") != "preparing":
                hit = next((step for step in trace
                            if step.get("kind") == "tool"
                            and step.get("status") == "preparing"
                            and not step.get("id")
                            and step.get("name") == call.get("name")
                            and step.get("round") == call.get("round")), None)
                if hit is not None:
                    hit["id"] = call_id
                    if call_id:
                        tools[call_id] = hit
            if hit is not None:
                hit.update({key: value for key, value in call.items() if key != "id"})
                hit["kind"] = "tool"
                hit["status"] = call.get("status") or hit.get("status") or "running"
            else:
                step = {"kind": "tool", "id": call_id, "name": call.get("name"),
                        "args": call.get("args"), "round": call.get("round"),
                        "status": call.get("status") or "running"}
                trace.append(step)
                if call_id:
                    tools[call_id] = step
        elif "tool_result" in event:
            result = event.get("tool_result") or {}
            result_id = str(result.get("id") or "")
            close_text()
            hit = tools.get(result_id) if result_id else None
            if hit is not None:
                hit.update({key: value for key, value in result.items() if key != "id"})
                hit["kind"] = "tool"
                hit["status"] = "done"
            else:
                step = {
                    "kind": "tool", "id": result_id, "name": result.get("name"),
                    "args": result.get("args"), "ok": result.get("ok"),
                    "summary": result.get("summary"), "display": result.get("display"),
                    "duration_ms": result.get("duration_ms"),
                    "round": result.get("round"), "status": "done",
                }
                trace.append(step)
                if result_id:
                    tools[result_id] = step
        elif "retry" in event:
            retry = event.get("retry") or {}
            hit = (trace[-1] if trace and trace[-1].get("kind") == "retry"
                   and trace[-1].get("round") == event.get("round") else None)
            if hit is not None:
                hit["attempt"] = retry.get("attempt")
                hit["max_attempts"] = retry.get("max_attempts")
                hit["text"] = retry.get("message") or hit.get("text")
            else:
                trace.append({"kind": "retry", "round": event.get("round"),
                              "attempt": retry.get("attempt"),
                              "max_attempts": retry.get("max_attempts"),
                              "text": retry.get("message") or ""})
        elif "task_proposal" in event:
            proposal = event.get("task_proposal") or {}
            close_text()
            trace.append({"kind": "proposal", "status": "pending",
                          "proposal_id": proposal.get("id"),
                          "goal": proposal.get("goal"),
                          "reason": proposal.get("reason")})
        elif "tool_approval" in event:
            approval = event.get("tool_approval") or {}
            close_text()
            trace.append({"kind": "approval", "id": approval.get("id"),
                          "name": approval.get("tool"),
                          "risk": approval.get("risk") or "medium",
                          "reasons": approval.get("reasons") or [],
                          "note": approval.get("note") or "",
                          "args": approval.get("args"),
                          "preview": approval.get("preview"),
                          "status": "pending", "round": approval.get("round")})
        elif "approval_decided" in event:
            decision = event.get("approval_decided") or {}
            hit = next((step for step in trace if step.get("kind") == "approval"
                        and step.get("id") == decision.get("id")), None)
            if hit is not None:
                hit["status"] = decision.get("status") or "denied"

    last_text = next((step for step in reversed(trace)
                      if step.get("kind") == "text"
                      and str(step.get("text") or "").strip()), None)
    return {
        "trace": trace,
        "content": str((last_text or {}).get("text") or "").strip(),
        "reasoning": "".join(thinking_parts),
        "tool_trace": [step for step in trace if step.get("kind") == "tool"],
    }


async def _apply_compaction(messages, models, decision, round_no, rid, rname, run_id):
    """Run context compaction and yield its ordered UI events.

    The internal ``_compressed`` event returns the result to the caller because
    async generators cannot return a value. Callers must consume and filter it.
    """
    from services.agent_zero.runtime.zero import compress as compress_service
    # Keep the heartbeat lifecycle in the conversation orchestrator for now.
    from services.agent_zero.task_layer.conversation import _ping_until_done

    yield {"context_compress_start": _compress_start_event(
        decision, round_no, rid, rname)}
    started = time.monotonic()
    task = asyncio.create_task(
        compress_service.apply(messages, models=models, decision=decision))
    async for event in _ping_until_done(
            task, run_id, label="上下文压缩", rid=rid, rname=rname,
            waiting="compact"):
        yield event
    result = task.result()
    yield {"_compressed": result}
    yield {"context_compress_done": _compress_done_event(
        result, round_no, rid, rname,
        int((time.monotonic() - started) * 1000))}


def _extract_final_text(timeline: List[Dict[str, Any]], last_round: int) -> str:
    """Remove and return the final text step, unless a tool follows it."""
    for index in range(len(timeline) - 1, -1, -1):
        step = timeline[index]
        if (step.get("kind") == "text"
                and step.get("round") == last_round
                and str(step.get("text") or "").strip()):
            if any(item.get("kind") == "tool" for item in timeline[index + 1:]):
                return ""
            text = str(step.get("text") or "").strip()
            timeline.pop(index)
            return text
    return ""


class _CtxMeter:
    """Estimate live context usage as the latest anchor plus generated deltas."""

    def __init__(self, messages: List[Dict[str, Any]]) -> None:
        try:
            from services.agent_zero.runtime.zero.compress import (
                count_tokens, estimate_tokens)
        except Exception:  # pragma: no cover - defensive fallback
            count_tokens = _zero_text
            estimate_tokens = _zero_messages
        self._count = count_tokens
        self._anchor = 0
        self._delta = 0
        self.anchor(int(estimate_tokens(messages) or 0))

    def add(self, text: Any) -> None:
        if text:
            self._delta += int(self._count(str(text)))

    def anchor(self, tokens: int) -> None:
        try:
            real = max(0, int(tokens or 0))
        except (TypeError, ValueError):
            real = 0
        if real:
            self._anchor = real
            self._delta = 0

    def tokens(self) -> int:
        return max(0, int(self._anchor) + int(self._delta))


def _zero_text(_text: str = "") -> int:
    return 0


def _zero_messages(_messages: Any = None) -> int:
    return 0


def _stamp_entry(entry: Dict[str, Any], started: float, seq: int) -> Dict[str, Any]:
    """Stamp a timeline entry with elapsed milliseconds and stable sequence."""
    entry["ts"] = int((time.time() - started) * 1000)
    entry["seq"] = seq
    return entry

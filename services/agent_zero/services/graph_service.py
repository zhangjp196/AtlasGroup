"""执行画布：把一轮对话里的主代理、团队成员、子代理和工具调用整理成可画的 DAG。

数据来源两路：
- 当前 / 最近一次 run 的 `subagent_event`（实时，带工具状态与预览）；
- 已完成消息的 trace 里 `display.type=subagent`（刷新后 / 事件过期后的历史兜底）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from services.agent_zero.services import team_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation, run_service


def _run_state(status: str) -> str:
    status = str(status or "").lower()
    if status in ("queued", "waiting"):
        return "queued"
    if status == "running":
        return "running"
    if status in ("failed", "stopped"):
        return "failed"
    if status == "done":
        return "done"
    return "idle"


def _tool_entry(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(tool.get("id") or tool.get("name") or ""),
        "name": str(tool.get("name") or tool.get("id") or "tool"),
        "status": str(tool.get("status") or "done"),
        "ok": tool.get("ok") is not False,
        "summary": str(tool.get("summary") or "")[:240],
    }


def _trace_nodes(conv_id: str) -> List[Dict[str, Any]]:
    """历史消息 trace 里的子代理（display.type=subagent）。"""
    found: Dict[str, Dict[str, Any]] = {}
    try:
        messages = conversation.list_messages(conv_id, limit=200)
    except Exception:
        return []
    for message in messages:
        trace = message.get("trace") if isinstance(message, dict) else None
        if not isinstance(trace, list):
            continue
        for idx, step in enumerate(trace):
            if not isinstance(step, dict) or step.get("kind") != "tool":
                continue
            display = step.get("display")
            if not isinstance(display, dict) or display.get("type") != "subagent":
                continue
            agent = display.get("agent") if isinstance(display.get("agent"), dict) else {}
            name = str(agent.get("name") or step.get("name") or "子代理")
            goal = str(display.get("goal") or "")
            key = f"{name}|{goal}"
            child_trace = display.get("trace") if isinstance(display.get("trace"), list) else []
            tools = []
            last_text = ""
            max_ts = 0
            for sub in child_trace:
                if not isinstance(sub, dict):
                    continue
                try:
                    ts = int(float(sub.get("ts") or 0))
                except (TypeError, ValueError):
                    ts = 0
                max_ts = max(max_ts, ts)
                if sub.get("kind") == "tool":
                    tools.append(_tool_entry(sub))
                elif sub.get("kind") == "text":
                    text = str(sub.get("text") or "").strip()
                    if text:
                        last_text = text[-400:]
            found[key] = {
                "id": f"trace-{message.get('id') or 'm'}-{idx}",
                "parent_id": "root",
                "kind": "subagent",
                "name": name,
                "role_id": str(agent.get("id") or ""),
                "goal": goal,
                "status": "done",
                "current": "",
                "text": last_text,
                "error": "",
                "tools": tools[:40],
                "elapsed_ms": max_ts,
                "started_at": "",
                "ended_at": "",
            }
    return list(found.values())


def _event_nodes(run_id: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """从 run 的 subagent_event 聚合每个子代理节点与活动时间线。"""
    nodes: Dict[str, Dict[str, Any]] = {}
    activity: List[Dict[str, Any]] = []
    if not run_id:
        return [], activity
    try:
        events = run_service.list_all_events(run_id)
    except Exception:
        return [], activity
    for ev in events:
        payload = ev.get("subagent_event") if isinstance(ev, dict) else None
        if not isinstance(payload, dict):
            continue
        call_id = str(payload.get("call_id") or "").strip()
        if not call_id:
            continue
        ts = payload.get("ts")
        try:
            ts = int(float(ts or 0))
        except (TypeError, ValueError):
            ts = 0
        node = nodes.get(call_id)
        if node is None:
            node = {
                "id": call_id, "parent_id": "root", "kind": "subagent",
                "name": str(payload.get("agent") or "子代理"),
                "role_id": "", "goal": str(payload.get("goal") or ""),
                "status": "running", "current": "", "text": "", "error": "",
                "tools": [], "elapsed_ms": 0,
                "started_at": ts, "ended_at": 0,
            }
            nodes[call_id] = node
        event = str(payload.get("event") or "")
        if event in ("tool_call", "tool_result"):
            tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
            tid = str(tool.get("id") or tool.get("name") or "tool")
            hit = next((x for x in node["tools"] if x["id"] == tid), None)
            if hit is None:
                hit = _tool_entry(tool)
                node["tools"].append(hit)
            else:
                hit.update(_tool_entry(tool))
            if event == "tool_call":
                hit["status"] = "running"
                node["current"] = hit["name"]
            else:
                hit["status"] = "done"
                hit["ok"] = tool.get("ok") is not False
                node["current"] = ""
        elif event == "progress":
            text = str(payload.get("text") or "")
            if text:
                node["text"] = (node["text"] + text)[-4000:]
        elif event == "done":
            node["status"] = "done"
            node["current"] = ""
            node["ended_at"] = ts
        elif event == "error":
            node["status"] = "failed"
            node["error"] = str(payload.get("error") or "")[:800]
            node["current"] = ""
            node["ended_at"] = ts
        if event in ("tool_call", "tool_result", "done", "error", "progress"):
            activity.append({
                "ts": ts, "agent": node["name"], "agent_id": call_id,
                "kind": event, "status": node["status"],
                "text": (str(payload.get("tool", {}).get("name") or "")
                         if event in ("tool_call", "tool_result")
                         else str(payload.get("text") or payload.get("error") or ""))[:240],
            })
    return list(nodes.values()), activity[-120:]


def conversation_graph(conv_id: str) -> Optional[Dict[str, Any]]:
    conv = conversation.get_conversation(conv_id)
    if not conv:
        return None
    runs = run_service.list_runs(conv_id, limit=30)
    latest = runs[0] if runs else None
    active = run_service.active_run(conv_id)
    run = active or latest
    run_id = str((run or {}).get("id") or "")
    team = None
    if conv.get("team_id"):
        try:
            team = team_service.get_team(str(conv["team_id"]))
        except Exception:
            team = None
    role = zero_store.get_role(str(conv.get("role_id") or "")) or {}
    leader = (team or {}).get("leader") or {}
    root_name = str(leader.get("name") or role.get("name") or "主持")
    root = {
        "id": "root", "parent_id": None, "kind": "root",
        "name": root_name,
        "role_id": str(leader.get("id") or role.get("id") or ""),
        "goal": str(conv.get("title") or "当前会话"),
        "status": _run_state((run or {}).get("status") or "idle"),
        "current": "", "text": "", "error": str((run or {}).get("error") or ""),
        "tools": [], "elapsed_ms": 0, "started_at": "", "ended_at": "",
    }

    live_nodes, activity = _event_nodes(run_id)
    nodes = live_nodes if live_nodes else _trace_nodes(conv_id)
    sub_names = {str(n.get("name") or "") for n in nodes}
    edges = [{"from": "root", "to": n["id"], "kind": n.get("kind") or "delegate"} for n in nodes]
    if team:
        for member in [leader, *(team.get("members") or [])]:
            if not isinstance(member, dict) or not member.get("id"):
                continue
            if member.get("id") == leader.get("id"):
                continue
            name = str(member.get("name") or member.get("id"))
            if name in sub_names:
                continue
            node = {
                "id": f"team-{member.get('id')}", "parent_id": "root", "kind": "team",
                "name": name, "role_id": str(member.get("id") or ""),
                "goal": str(member.get("expertise") or "团队成员 · 等待委派"),
                "status": "idle", "current": "", "text": "", "error": "",
                "tools": [], "elapsed_ms": 0, "started_at": "", "ended_at": "",
            }
            nodes.append(node)
            edges.append({"from": "root", "to": node["id"], "kind": "team"})

    usage = conversation.usage_total(conv_id)
    tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    today = time.strftime("%Y-%m-%d")
    runs_today = [r for r in runs if str(r.get("created_at") or "").startswith(today)]
    queued = [r for r in runs if str(r.get("status") or "") == "queued"]
    if not activity:
        activity = [{
            "ts": 0, "agent": n["name"], "agent_id": n["id"],
            "kind": "done", "status": n.get("status") or "done",
            "text": (n.get("text") or n.get("goal") or "已完成"),
        } for n in nodes]
    counts = {"running": 0, "done": 0, "failed": 0}
    for node in [root, *nodes]:
        state = str(node.get("status") or "")
        if state in counts:
            counts[state] += 1
    return {
        "conversation": {
            "id": conv.get("id"), "title": conv.get("title") or "",
            "workspace_id": conv.get("workspace_id") or "",
            "team_id": conv.get("team_id") or "",
            "role_id": conv.get("role_id") or "",
        },
        "run": run or None,
        "root": root,
        "nodes": nodes,
        "edges": edges,
        "activity": activity,
        "upcoming": [{
            "id": r.get("id"), "status": r.get("status"),
            "created_at": r.get("created_at") or "",
            "content": str(r.get("content") or "")[:160],
        } for r in queued[:10]],
        "stats": {
            "agents": len(nodes) + 1,
            "tools": sum(len(n.get("tools") or []) for n in nodes),
            "tokens": tokens,
            "runs_today": len(runs_today),
            "done_today": len([r for r in runs_today if str(r.get("status") or "") == "done"]),
            **counts,
        },
    }

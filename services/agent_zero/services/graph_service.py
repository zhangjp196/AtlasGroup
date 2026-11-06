"""执行画布：把一轮对话里的主代理、团队成员、子代理和工具调用整理成可画的 DAG。

数据来源三路：
- 当前 / 最近一次**主持 run** 的 `subagent_event`（实时，带工具状态与预览）；
- 同一对话里团队成员的后台**并行 run**（target_role_id）及其工具事件；
- 已完成消息的 trace 里 `display.type=subagent`（刷新后 / 事件过期后的历史兜底）。

画布要能回答三个问题：谁在跑、跑到哪一步、哪些成员在并行。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

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


def _parse_ts(value: Any) -> int:
    """把 DB 时间 / 事件时间统一成秒级时间戳。"""
    if not value:
        return 0
    if isinstance(value, (int, float)):
        n = float(value)
        return int(n / 1000 if n > 1e12 else n)
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(raw[:19], fmt)))
        except ValueError:
            continue
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _event_ts(ev: Dict[str, Any], fallback: int = 0) -> int:
    return _parse_ts(ev.get("_at")) or _parse_ts(ev.get("ts")) or fallback


def _duration_ms(run: Dict[str, Any]) -> int:
    start = _parse_ts(run.get("started_at") or run.get("created_at"))
    if not start:
        return 0
    end = _parse_ts(run.get("finished_at"))
    if not end:
        status = str(run.get("status") or "")
        if status in run_service.TERMINAL_STATUSES:
            end = _parse_ts(run.get("updated_at")) or start
        else:
            end = int(time.time())
    return max(0, (end - start) * 1000)


def _merge_tool(tools: List[Dict[str, Any]], tool: Dict[str, Any]) -> Dict[str, Any]:
    entry = _tool_entry(tool)
    hit = next((x for x in tools if x["id"] == entry["id"]), None)
    if hit is None:
        tools.append(entry)
        return entry
    hit.update(entry)
    return hit


def _collect_run_events(run_id: str, agent: str, agent_id: str) -> Dict[str, Any]:
    """一个 run 的工具 / 正文 / 错误 / 活动时间线。"""
    out: Dict[str, Any] = {
        "tools": [], "text": "", "error": "", "current": "", "activity": [],
    }
    if not run_id:
        return out
    try:
        events = run_service.list_all_events(run_id)
    except Exception:
        return out
    stream_text = ""
    final_text = ""
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = _event_ts(ev)
        tool_call = ev.get("tool_call")
        if isinstance(tool_call, dict):
            hit = _merge_tool(out["tools"], tool_call)
            hit["status"] = "running"
            out["current"] = hit["name"]
            out["activity"].append({
                "ts": ts, "agent": agent, "agent_id": agent_id,
                "kind": "tool_call", "status": "running",
                "text": hit["name"],
            })
        tool_result = ev.get("tool_result")
        if isinstance(tool_result, dict):
            hit = _merge_tool(out["tools"], tool_result)
            hit["status"] = "done"
            hit["ok"] = tool_result.get("ok") is not False
            out["current"] = ""
            out["activity"].append({
                "ts": ts, "agent": agent, "agent_id": agent_id,
                "kind": "tool_result",
                "status": "done" if hit["ok"] else "failed",
                "text": f"{hit['name']} · {'完成' if hit['ok'] else '失败'}",
            })
        delta = ev.get("delta")
        if isinstance(delta, str) and delta:
            stream_text += delta
        done = ev.get("done")
        if done:
            final_text = str(ev.get("text") or ev.get("content") or "")
            out["current"] = ""
            out["activity"].append({
                "ts": ts, "agent": agent, "agent_id": agent_id,
                "kind": "done", "status": "done",
                "text": (final_text or "已完成")[:240],
            })
        error = ev.get("error")
        if error:
            text = str(error.get("message") if isinstance(error, dict) else error)
            out["error"] = text[:800]
            out["current"] = ""
            out["activity"].append({
                "ts": ts, "agent": agent, "agent_id": agent_id,
                "kind": "error", "status": "failed", "text": text[:240],
            })
    out["text"] = final_text or stream_text[-4000:]
    return out


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


def _event_nodes(run_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
        if not ts:
            ts = _event_ts(ev)
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
            hit = _merge_tool(node["tools"], tool)
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
    for node in nodes.values():
        if node.get("started_at") and node.get("ended_at"):
            node["elapsed_ms"] = max(0, (int(node["ended_at"]) - int(node["started_at"])))
    return list(nodes.values()), activity[-120:]


def _run_node(run: Dict[str, Any], name: str, role_id: str,
              kind: str = "member") -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    content = _collect_run_events(str(run.get("id") or ""), name, str(run.get("id") or ""))
    return {
        "id": f"run-{run.get('id')}",
        "parent_id": "root",
        "kind": kind,
        "name": name,
        "role_id": role_id,
        "goal": str(run.get("content") or "")[:600],
        "status": _run_state(str(run.get("status") or "")),
        "current": content["current"],
        "text": content["text"],
        "error": str(run.get("error") or content["error"] or "")[:800],
        "tools": content["tools"][:60],
        "elapsed_ms": _duration_ms(run),
        "started_at": str(run.get("started_at") or ""),
        "ended_at": str(run.get("finished_at") or ""),
        "run_id": str(run.get("id") or ""),
        "mode": str(run.get("mode") or ""),
    }, content["activity"]


def _member_run_nodes(runs: List[Dict[str, Any]], root_run_id: str
                      ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """成员 run → 节点（每个 run 一个节点，便于多个后台任务并存）。"""
    nodes: List[Dict[str, Any]] = []
    activity: List[Dict[str, Any]] = []
    for run in runs:
        role_id = str(run.get("target_role_id") or "").strip()
        if not role_id or str(run.get("id") or "") == root_run_id:
            continue
        role = zero_store.get_role(role_id) or {}
        name = str(role.get("name") or role_id)
        node, acts = _run_node(run, name, role_id, kind="member")
        nodes.append(node)
        activity.extend(acts)
    return nodes, activity


def conversation_graph(conv_id: str) -> Optional[Dict[str, Any]]:
    conv = conversation.get_conversation(conv_id)
    if not conv:
        return None
    runs = run_service.list_runs(conv_id, limit=40)
    active = run_service.active_run(conv_id)
    coord_runs = [r for r in runs if not str(r.get("target_role_id") or "").strip()]
    latest_coord = coord_runs[0] if coord_runs else None
    root_run = active if (active and not str(active.get("target_role_id") or "").strip()) else latest_coord
    root_run_id = str((root_run or {}).get("id") or "")
    team = None
    if conv.get("team_id"):
        try:
            team = team_service.get_team(str(conv["team_id"]))
        except Exception:
            team = None
    role = zero_store.get_role(str(conv.get("role_id") or "")) or {}
    leader = (team or {}).get("leader") or {}
    root_name = str(leader.get("name") or role.get("name") or "主持")
    root_content = _collect_run_events(root_run_id, root_name, root_run_id) if root_run_id else {
        "tools": [], "text": "", "error": "", "current": "", "activity": [],
    }
    root_status = _run_state((root_run or {}).get("status") or "idle")
    if not root_run:
        member_running = any(str(r.get("status") or "") == "running"
                             and str(r.get("target_role_id") or "").strip()
                             for r in runs)
        root_status = "running" if member_running else "idle"
    root = {
        "id": "root", "parent_id": None, "kind": "root",
        "name": root_name,
        "role_id": str(leader.get("id") or role.get("id") or ""),
        "goal": str((root_run or {}).get("content") or conv.get("title") or "当前会话")[:600],
        "status": root_status,
        "current": root_content["current"], "text": root_content["text"][-4000:],
        "error": str((root_run or {}).get("error") or root_content["error"] or ""),
        "tools": root_content["tools"][:60], "elapsed_ms": _duration_ms(root_run or {}),
        "started_at": str((root_run or {}).get("started_at") or ""),
        "ended_at": str((root_run or {}).get("finished_at") or ""),
        "run_id": root_run_id,
    }

    live_nodes, activity = _event_nodes(root_run_id)
    nodes = live_nodes if live_nodes else _trace_nodes(conv_id)
    member_nodes, member_activity = _member_run_nodes(runs, root_run_id)
    nodes.extend(member_nodes)
    # 主持可能已经收尾，但成员还在并行执行：画布整体仍应显示「运行中」。
    if any(str(n.get("status") or "") == "running" for n in member_nodes) \
            and root.get("status") not in ("failed",):
        root["status"] = "running"
    activity.extend(member_activity)
    activity.extend(root_content["activity"])

    sub_names = {str(n.get("name") or "") for n in nodes}
    member_role_ids = {str(n.get("role_id") or "") for n in member_nodes}
    if team:
        for member in [leader, *(team.get("members") or [])]:
            if not isinstance(member, dict) or not member.get("id"):
                continue
            if member.get("id") == leader.get("id"):
                continue
            if str(member.get("id")) == zero_store.GLOBAL_ROLE_ID:
                continue
            if str(member.get("id")) in member_role_ids:
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

    edges = [{"from": "root", "to": n["id"],
              "kind": n.get("kind") or "delegate"} for n in nodes]

    # 角色头像跟随角色当前值：画布 / 任务成员都按 role_id 展示同一张图。
    for node in [root, *nodes]:
        rid = str(node.get("role_id") or "")
        if rid:
            node["avatar"] = str((zero_store.get_role(rid) or {}).get("avatar") or "")
        node.setdefault("avatar", "")

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
    activity = sorted(activity, key=lambda x: int(x.get("ts") or 0), reverse=True)[:120]
    counts = {"running": 0, "done": 0, "failed": 0, "queued": 0}
    for node in [root, *nodes]:
        state = str(node.get("status") or "")
        if state in counts:
            counts[state] += 1
    parallel = len([n for n in member_nodes if n.get("status") == "running"])
    return {
        "conversation": {
            "id": conv.get("id"), "title": conv.get("title") or "",
            "workspace_id": conv.get("workspace_id") or "",
            "team_id": conv.get("team_id") or "",
            "role_id": conv.get("role_id") or "",
        },
        "run": root_run or None,
        "root": root,
        "nodes": nodes,
        "edges": edges,
        "activity": activity,
        "upcoming": [{
            "id": r.get("id"), "status": r.get("status"),
            "created_at": r.get("created_at") or "",
            "content": str(r.get("content") or "")[:160],
            "kind": "member" if str(r.get("target_role_id") or "").strip() else "run",
            "role_id": str(r.get("target_role_id") or r.get("role_id") or ""),
            "role_name": str((zero_store.get_role(
                str(r.get("target_role_id") or "")) or {}).get("name") or ""),
        } for r in queued[:12]],
        "stats": {
            "agents": len(nodes) + 1,
            "tools": sum(len(n.get("tools") or []) for n in [root, *nodes]),
            "tokens": tokens,
            "runs_today": len(runs_today),
            "done_today": len([r for r in runs_today if str(r.get("status") or "") == "done"]),
            "parallel": parallel,
            "member_runs": len(member_nodes),
            **counts,
        },
    }

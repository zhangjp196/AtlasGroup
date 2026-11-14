"""Role-discussion orchestration with injected stream/event boundaries."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Tuple

from services.agent_zero.task_layer.conversation_tools import (
    MAX_DISCUSSION_ROLES,
    MAX_DISCUSSION_ROUNDS,
)

logger = logging.getLogger(__name__)


def _discussion_role_tokens(args: Dict[str, Any]) -> List[str]:
    raw = ((args or {}).get("roles") or (args or {}).get("role_ids")
           or (args or {}).get("participants") or [])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _resolve_discussion_roles(
    args: Dict[str, Any], current_role: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], str]:
    """把角色 id / 名字解析成参与者（2-3 个，去重，保留调用顺序）。"""
    tokens = _discussion_role_tokens(args or {})
    if not tokens:
        return [], "roles 不能为空：请提供 2-3 个角色 id 或名字"
    from services.agent_zero.stores import zero_store

    pool = zero_store.list_roles(limit=500)
    by_id = {str(role.get("id") or ""): role for role in pool}
    current_id = str(current_role.get("id") or "")
    if current_id and current_id not in by_id:
        pool.append(current_role)
        by_id[current_id] = current_role
    by_name = {
        str(role.get("name") or "").strip().casefold(): role
        for role in pool if str(role.get("name") or "").strip()
    }
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for token in tokens:
        role = by_id.get(token) or by_name.get(token.casefold())
        if not role:
            return [], f"找不到角色：{token}"
        rid = str(role.get("id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(role)
    if len(out) < 2:
        return [], "至少需要 2 个不同角色参与讨论"
    if len(out) > MAX_DISCUSSION_ROLES:
        out = out[:MAX_DISCUSSION_ROLES]
    return out, ""


def _discussion_prompt(topic: str, mode: str, participants: List[Dict[str, Any]],
                       transcript: List[Dict[str, Any]], speaker: Dict[str, Any],
                       round_no: int, rounds: int, context: str) -> str:
    mode_label = "交互验证 / 交叉审查" if mode == "verify" else "头脑风暴"
    lines = [
        "<role-discussion>",
        f"主题：{topic}",
        f"模式：{mode_label}",
        f"参与者：{'、'.join(str(p.get('name') or p.get('id')) for p in participants)}",
        f"你的角色：{speaker.get('name') or speaker.get('id')}"
        f"（{speaker.get('title') or '未填写岗位'}）",
        f"轮次：第 {round_no} 轮 / 共 {rounds} 轮",
    ]
    if context.strip():
        lines += ["", "补充上下文：", context.strip()[:6000]]
    if transcript:
        lines += ["", "此前讨论记录："]
        for item in transcript:
            lines.append(f"## 第 {item.get('round')} 轮")
            for message in item.get("messages") or []:
                lines.append(f"### {message.get('role_name')}")
                lines.append(str(message.get("output") or "")[:6000])
    if mode == "verify":
        lines += [
            "",
            "本轮要求：",
            "- 检查此前发言中的事实、假设与推理漏洞；不同意就明确说，并给出依据。",
            "- 补充你验证过的证据（文件 / 命令 / 测试结果）；没有验证就标注未验证。",
            "- 最后一轮给出可执行结论、分歧点与仍待验证项。",
        ]
    else:
        lines += [
            "",
            "本轮要求：",
            "- 结合此前发言提出新观点，不要复述别人已经说过的内容。",
            "- 可以对别人的观点补充、反驳或组合；给出具体可执行的建议。",
            "- 最后一轮收敛成可执行结论与待办。",
        ]
    lines.append("</role-discussion>")
    return "\n".join(lines)


async def exec_role_discussion(conv: Dict[str, Any], user: str,
                               role: Dict[str, Any], args: Dict[str, Any],
                               depth: int, *, call_id: str = "",
                               parent_run_id: str = "", max_depth: int,
                               max_rounds: int, stream_role, forward_event) -> str:
    """2-3 个角色直接多轮讨论（不经过主持转述）。"""
    if max_depth and depth >= max_depth:
        return json.dumps(
            {"error": f"角色讨论嵌套过深（最多 {max_depth} 层）"},
            ensure_ascii=False)
    topic = str((args or {}).get("topic") or "").strip()
    if not topic:
        return json.dumps({"error": "topic 不能为空"}, ensure_ascii=False)
    mode = str((args or {}).get("mode") or "brainstorm").strip().lower()
    if mode not in ("brainstorm", "verify"):
        mode = "brainstorm"
    try:
        rounds = int((args or {}).get("rounds") or 2)
    except (TypeError, ValueError):
        rounds = 2
    rounds = max(1, min(MAX_DISCUSSION_ROUNDS, rounds))
    context = str((args or {}).get("context") or "").strip()[:8000]
    participants, error = _resolve_discussion_roles(args or {}, role)
    if error:
        return json.dumps({"error": error}, ensure_ascii=False)

    from services.agent_zero.services import ask_service
    from services.agent_zero.task_layer import prompt_builder

    from services.agent_zero.task_layer import run_service

    run_id = parent_run_id or ask_service.current_run_id()
    base_call = str(call_id or "discussion").strip()
    transcript: List[Dict[str, Any]] = []
    for round_no in range(1, rounds + 1):
        messages: List[Dict[str, Any]] = []
        for speaker in participants:
            goal = f"{mode}: {topic}"
            sink: Dict[str, Any] = {}
            state: Dict[str, Any] = {}
            prompt = _discussion_prompt(
                topic, mode, participants, transcript, speaker,
                round_no, rounds, context)
            reservation_id = None
            try:
                reservation_id = run_service.reserve_subagent_slot(parent_run_id)
                if not reservation_id:
                    messages.append({
                        "role_id": str(speaker.get("id") or ""),
                        "role_name": str(speaker.get("name") or speaker.get("id") or ""),
                        "output": "（全局并发额度已满，讨论角色未启动）",
                        "error": "全局并发额度已满，讨论角色未启动",
                    })
                    continue
                async for ev in stream_role(
                        conv, user, speaker, sink, autonomous=False,
                        _depth=depth + 1, _max_rounds=max_rounds,
                        run_id=parent_run_id or None,
                        isolated=True,
                        context_messages=[{"role": "user", "content": prompt}],
                        system_addon=prompt_builder.ROLE_DISCUSSION):
                    forward_event(
                        run_id, f"{base_call}#r{round_no}#{speaker.get('id')}",
                        str(speaker.get("name") or "角色"), goal, ev, state)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("角色讨论参与者 %s 失败: %s", speaker.get("name"), e)
                messages.append({
                    "role_id": str(speaker.get("id") or ""),
                    "role_name": str(speaker.get("name") or speaker.get("id") or ""),
                    "output": f"（失败：{e}）",
                    "error": str(e),
                })
                continue
            finally:
                if reservation_id:
                    run_service.release_subagent_slot(reservation_id)
            text = str(sink.get("text") or "").strip()
            if not text:
                err = str(sink.get("error") or "").strip()
                text = f"（未返回正文：{err}）" if err else "（未返回正文）"
            messages.append({
                "role_id": str(speaker.get("id") or ""),
                "role_name": str(speaker.get("name") or speaker.get("id") or ""),
                "output": text[:8000],
            })
        transcript.append({"round": round_no, "messages": messages})

    final_round = transcript[-1]["messages"] if transcript else []
    summary = "\n\n".join(
        f"### {item.get('role_name')}\n{item.get('output')}" for item in final_round)
    return json.dumps({
        "status": "done",
        "topic": topic,
        "mode": mode,
        "rounds": rounds,
        "participants": [
            {"id": str(p.get("id") or ""), "name": str(p.get("name") or "")}
            for p in participants
        ],
        "transcript": transcript,
        "output": summary,
    }, ensure_ascii=False)

"""群组后台任务：一次编排 = 多个成员并行 run，可暂停 / 继续 / 结束。

与普通对话 run 的区别：
- 任务是一等实体（``team_tasks``），一个任务可以包含多个成员 run；
- 成员 run 继续复用 ``conversation_runs``，通过 ``team_task_id`` 归属任务；
- 暂停 = 停掉当前活跃成员 run，任务保留；继续 = 为未完成成员重新派发；
- 结束 = 停掉所有活跃成员 run，任务进入终态。

执行画布按任务展示 leader → members 的编排关系，点击成员可看该 run 的实时流。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import run_service

logger = logging.getLogger(__name__)

STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
TASK_STATUSES = (STATUS_RUNNING, STATUS_PAUSED, STATUS_DONE, STATUS_FAILED, STATUS_STOPPED)
ACTIVE_RUN_STATUSES = ("queued", "running")
# 同一会话一小时内最多自动交回多少次（防任务链跑飞）；不暴露成配置。
AUTO_CONTINUE_PER_HOUR = 20


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _q(name: str) -> str:
    return zero_store._q(name)


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _fetch(task_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('team_tasks')} WHERE id = :id"),
            {"id": task_id},
        ).mappings().first()
    if not row:
        return None
    task = dict(row)
    task["members"] = _decode(task.get("members")) or []
    for key in ("created_at", "updated_at"):
        if task.get(key) is not None:
            task[key] = str(task[key])
    return task


def _save_members(task_id: str, members: List[Dict[str, Any]],
                  status: Optional[str] = None) -> None:
    SessionLocal = get_session_local()
    sets = ["members = :members", "updated_at = datetime('now','localtime')"]
    params: Dict[str, Any] = {"id": task_id, "members": _json(members)}
    if status:
        sets.append("status = :status")
        params["status"] = status
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('team_tasks')} SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        db.commit()


def _run_map(task: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    runs: Dict[str, Dict[str, Any]] = {}
    for member in task.get("members") or []:
        run_id = str((member or {}).get("run_id") or "").strip()
        if not run_id:
            continue
        run = run_service.get_run(run_id)
        if run:
            runs[run_id] = run
    return runs


def _member_status(member: Dict[str, Any],
                   runs: Dict[str, Dict[str, Any]]) -> str:
    run_id = str(member.get("run_id") or "").strip()
    run = runs.get(run_id)
    if not run:
        return str(member.get("status") or "queued")
    status = str(run.get("status") or "queued")
    if status in ACTIVE_RUN_STATUSES:
        return status
    if status == "done":
        return "done"
    if status == "failed":
        return "failed"
    if status == "stopped":
        return "stopped"
    return status


def _task_status(task: Dict[str, Any], runs: Dict[str, Dict[str, Any]]) -> str:
    stored = str(task.get("status") or STATUS_RUNNING)
    if stored in (STATUS_PAUSED, STATUS_STOPPED):
        return stored
    statuses = [_member_status(m, runs) for m in (task.get("members") or [])]
    if not statuses:
        return STATUS_DONE
    if any(s in ACTIVE_RUN_STATUSES for s in statuses):
        return STATUS_RUNNING
    if all(s == "done" for s in statuses):
        return STATUS_DONE
    if any(s == "failed" for s in statuses):
        return STATUS_FAILED
    if any(s == "stopped" for s in statuses):
        return STATUS_STOPPED
    return STATUS_DONE


def _public(task: Dict[str, Any],
            run_usage: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    from services.agent_zero.services import usage_service

    runs = _run_map(task)
    run_ids = [str((member or {}).get("run_id") or "").strip()
               for member in (task.get("members") or [])]
    run_ids = [run_id for run_id in run_ids if run_id]
    # 列表接口一次聚合整页 run，详情接口只算自己这几个 run。
    if run_usage is None:
        run_usage = usage_service.usage_by_run(run_ids)
    members: List[Dict[str, Any]] = []
    for member in task.get("members") or []:
        item = dict(member or {})
        run_id = str(item.get("run_id") or "").strip()
        run = runs.get(run_id) or {}
        item["run_status"] = str(run.get("status") or item.get("status") or "queued")
        item["status"] = _member_status(item, runs)
        item["error"] = str(run.get("error") or "")
        item["started_at"] = str(run.get("started_at") or "")
        item["finished_at"] = str(run.get("finished_at") or "")
        item["run_id"] = run_id
        item["usage"] = run_usage.get(run_id) or usage_service.empty_usage()
        members.append(item)
    status = _task_status(task, runs)
    active = len([m for m in members if m["status"] in ACTIVE_RUN_STATUSES])
    done = len([m for m in members if m["status"] == "done"])
    leader_id = str(task.get("leader_role_id") or "")
    leader = zero_store.get_role(leader_id) or {}
    continued_at = str(task.get("continued_at") or "")
    # 任务用量 = 各成员 run 之和（输入 / 输出 / 速度 / 缓存都按总量重算）。
    task_usage = usage_service.sum_usage(
        [member.get("usage") for member in members])
    return {
        "id": task.get("id"),
        "conversation_id": task.get("conversation_id"),
        "team_id": task.get("team_id") or "",
        "leader_role_id": leader_id,
        "leader_name": str(leader.get("name") or leader_id or "主持"),
        "title": task.get("title") or "",
        "goal": task.get("goal") or "",
        "status": status,
        "members": members,
        "usage": task_usage,
        "active_runs": active,
        "done_members": done,
        "total_members": len(members),
        "continued_at": continued_at,
        "created_at": task.get("created_at") or "",
        "updated_at": task.get("updated_at") or "",
        "can_pause": status == STATUS_RUNNING and active > 0,
        "can_resume": status in (STATUS_PAUSED, STATUS_FAILED) and active == 0,
        "can_stop": status in (STATUS_RUNNING, STATUS_PAUSED),
        # 「继续对话」不是重启成员，而是把结果交回主会话；只在任务已结束、
        # 且还没交回过时可用（结果不会反复烧主会话 token）。
        "can_continue": (status in (STATUS_DONE, STATUS_FAILED, STATUS_STOPPED)
                         and active == 0 and not continued_at),
    }


def _wake_runs(run_ids: List[str]) -> None:
    """后台唤醒（路由 / 工具调用方也可以自己 await request_run）。"""
    if not run_ids:
        return
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return
    try:
        from services.agent_zero.task_layer import conv_worker

        for run_id in run_ids:
            loop.create_task(conv_worker.request_run(run_id))
    except Exception:
        logger.debug("后台任务唤醒失败（worker 会轮询拾取）", exc_info=True)


def create_task(conv: Dict[str, Any], team: Dict[str, Any],
                leader_role: Dict[str, Any],
                assignments: List[Dict[str, Any]], user: str,
                *, persist: bool = True,
                title: Optional[str] = None,
                mention: bool = True) -> Dict[str, Any]:
    """创建后台任务并为每个成员派发一个独立 run。

    ``assignments``: [{role_id, role_name, goal, context, role?}]
    ``mention=False``：单角色会话的后台任务（自己派给自己），不再写
    「主持 @ 成员」这种群聊式消息，只保留成员的确认回复。

    同一角色可以出现在多条 assignment 里（同一任务内并行多件活）；一个角色
    的多项独立任务则各自调用一次 ``create_task``，由 worker 并行执行。
    """
    from services.agent_zero.task_layer import conversation

    task_id = zero_store.new_id("teamtask") if persist else ""
    goals = [str((a or {}).get("goal") or "").strip() for a in assignments]
    if not str(title or "").strip():
        title = (f"群组任务 · {len(assignments)} 个成员" if len(assignments) > 1
                 else next((g for g in goals if g), "群组后台任务"))
    title = str(title).strip()[:120]
    goal_text = "\n".join(
        f"[{(a.get('role_name') or a.get('role_id') or '成员')}] {a.get('goal') or ''}"
        for a in assignments
    )[:4000]
    members: List[Dict[str, Any]] = []
    for assignment in assignments:
        role_id = str(assignment.get("role_id") or "").strip()
        goal = str(assignment.get("goal") or "").strip()
        context = str(assignment.get("context") or "").strip()
        role = assignment.get("role") or zero_store.get_role(role_id) or {
            "id": role_id, "name": assignment.get("role_name") or role_id,
        }
        if not role_id or not goal:
            continue
        content = goal + (f"\n\n## 主持补充的上下文\n{context}" if context else "")
        mention_msg = (conversation.append_mention_message(
            conv["id"], leader_role, role, goal) if mention else None)
        run = run_service.create_run(
            conv["id"], user,
            role_id=role_id,
            target_role_id=role_id,
            team_task_id=task_id or None,
            content=content,
            mentions=[role_id],
            mode="autonomous",
            queued_msg_id=(mention_msg or {}).get("id"),
        )
        # 对话与任务分离：前台只留一句成员确认；执行在后台，过程不进转录。
        # run_id 只作排序锚点，让确认消息排在自己那条「@成员」之后。
        conversation.append_task_ack_message(
            conv["id"], role, goal, run_id=str(run.get("id") or ""))
        members.append({
            "role_id": role_id,
            "role_name": str(role.get("name") or role_id),
            "goal": goal,
            "context": context,
            "status": "queued",
            "run_id": str(run.get("id") or ""),
        })
    if not members:
        raise ValueError("没有可派发的成员任务")

    if not persist:
        return {
            "id": "", "conversation_id": conv.get("id"), "team_id": team.get("id") or "",
            "leader_role_id": str(leader_role.get("id") or ""),
            "title": title, "goal": goal_text, "status": STATUS_RUNNING,
            "members": members, "active_runs": len(members),
            "done_members": 0, "total_members": len(members),
            "created_at": "", "updated_at": "",
            "can_pause": True, "can_resume": False, "can_stop": True,
        }

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('team_tasks')} "
                 f"(id, conversation_id, team_id, leader_role_id, title, goal, status, "
                 f" members, created_by) "
                 f"VALUES (:id, :cid, :tid, :lid, :title, :goal, :status, :members, :by)"),
            {"id": task_id, "cid": conv["id"], "tid": team.get("id") or None,
             "lid": leader_role.get("id") or None, "title": title,
             "goal": goal_text, "status": STATUS_RUNNING,
             "members": _json(members), "by": user},
        )
        db.commit()
    _wake_runs([m["run_id"] for m in members])
    task = get_task(task_id) or {
        "id": task_id, "members": members, "status": STATUS_RUNNING,
    }
    # 角色 / 群组的后台任务 → 自动建一张工单（工单面板可见，状态随任务收敛）。
    try:
        from services.agent_zero.services import work_service

        work_service.create_from_task(task, conv)
    except Exception:
        logger.debug("后台任务建工单失败 task=%s", task_id, exc_info=True)
    return task


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    task = _fetch(task_id)
    return _public(task) if task else None


def list_tasks(conversation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('team_tasks')} "
                 f"WHERE conversation_id = :cid "
                 f"ORDER BY created_at DESC, id DESC LIMIT :n"),
            {"cid": conversation_id, "n": max(1, min(int(limit), 200))},
        ).mappings().all()
    out: List[Dict[str, Any]] = []
    tasks: List[Dict[str, Any]] = []
    run_ids: List[str] = []
    for row in rows:
        task = dict(row)
        task["members"] = _decode(task.get("members")) or []
        for key in ("created_at", "updated_at"):
            if task.get(key) is not None:
                task[key] = str(task[key])
        tasks.append(task)
        for member in task["members"]:
            run_id = str((member or {}).get("run_id") or "").strip()
            if run_id:
                run_ids.append(run_id)
    # 整页任务一次聚合 token：列表轮询不再按任务逐个查库。
    from services.agent_zero.services import usage_service

    run_usage = usage_service.usage_by_run(run_ids)
    for task in tasks:
        public = _public(task, run_usage)
        if public["status"] != str(task.get("status") or ""):
            _save_members(str(task["id"]), task["members"], public["status"])
        out.append(public)
    return out


def _stop_active(task: Dict[str, Any], *, force: bool = True,
                 member_status: str = "stopped") -> int:
    """停掉任务里仍在 queued/running 的成员 run。返回处理数量。"""
    stopped = 0
    for member in task.get("members") or []:
        run_id = str((member or {}).get("run_id") or "").strip()
        if not run_id:
            continue
        run = run_service.get_run(run_id)
        if not run or str(run.get("status") or "") not in ACTIVE_RUN_STATUSES:
            continue
        if str(run.get("status")) == "queued":
            # queued run 直接取消：run 与排队消息一起删除，继续时重新派发。
            run_service.cancel_run(run_id)
            member["run_id"] = ""
            member["status"] = member_status
        else:
            run_service.request_stop(run_id)
            member["status"] = member_status
            if force:
                try:
                    from services.agent_zero.task_layer import conv_worker

                    conv_worker.get_worker().force_stop(run_id)
                except Exception:
                    logger.debug("后台任务强制停止失败 run=%s", run_id, exc_info=True)
        stopped += 1
    return stopped


def pause_task(task_id: str, user: str = "local") -> Dict[str, Any]:
    task = _fetch(task_id)
    if not task:
        raise ValueError(f"后台任务不存在: {task_id}")
    public = _public(task)
    if public["status"] != STATUS_RUNNING:
        raise ValueError("只有运行中的后台任务可以暂停")
    _stop_active(task, member_status=STATUS_PAUSED)
    _save_members(task_id, task.get("members") or [], STATUS_PAUSED)
    zero_store.append_audit(user, "team_task_pause", args={"task_id": task_id})
    return get_task(task_id) or public


def stop_task(task_id: str, user: str = "local") -> Dict[str, Any]:
    task = _fetch(task_id)
    if not task:
        raise ValueError(f"后台任务不存在: {task_id}")
    _stop_active(task, member_status=STATUS_STOPPED)
    _save_members(task_id, task.get("members") or [], STATUS_STOPPED)
    zero_store.append_audit(user, "team_task_stop", args={"task_id": task_id})
    return get_task(task_id) or _public(task)


def resume_task(task_id: str, user: str = "local") -> Dict[str, Any]:
    task = _fetch(task_id)
    if not task:
        raise ValueError(f"后台任务不存在: {task_id}")
    public = _public(task)
    if public["status"] not in (STATUS_PAUSED, STATUS_FAILED):
        raise ValueError("只有已暂停或失败的后台任务可以继续")
    if int(public.get("active_runs") or 0) > 0:
        raise ValueError("成员仍在停止中，请稍后再继续")
    from services.agent_zero.task_layer import conversation

    conv = conversation.get_conversation(str(task.get("conversation_id") or ""))
    if not conv:
        raise ValueError("后台任务所属会话不存在")
    leader = zero_store.get_role(str(task.get("leader_role_id") or "")) or {
        "id": task.get("leader_role_id"), "name": "主持",
    }
    members: List[Dict[str, Any]] = []
    new_runs: List[str] = []
    for member in task.get("members") or []:
        item = dict(member or {})
        run = run_service.get_run(str(item.get("run_id") or ""))
        if run and str(run.get("status") or "") == "done":
            members.append(item)
            continue
        role_id = str(item.get("role_id") or "").strip()
        goal = str(item.get("goal") or "").strip()
        if not role_id or not goal:
            members.append(item)
            continue
        role = zero_store.get_role(role_id) or {
            "id": role_id, "name": item.get("role_name") or role_id,
        }
        continue_goal = goal + "\n\n（这是暂停后继续：接着上次未完成的部分做，不要重复已完成的工作。）"
        mention = conversation.append_mention_message(
            conv["id"], leader, role, continue_goal)
        run = run_service.create_run(
            conv["id"], user,
            role_id=role_id,
            target_role_id=role_id,
            team_task_id=task_id,
            content=continue_goal,
            mentions=[role_id],
            mode="autonomous",
            queued_msg_id=(mention or {}).get("id"),
        )
        item["run_id"] = str(run.get("id") or "")
        item["status"] = "queued"
        members.append(item)
        new_runs.append(item["run_id"])
    _save_members(task_id, members, STATUS_RUNNING)
    _wake_runs(new_runs)
    zero_store.append_audit(user, "team_task_resume",
                            args={"task_id": task_id, "runs": len(new_runs)})
    return get_task(task_id) or public


def _member_result_text(run_id: str, limit: int = 1200) -> str:
    """成员 run 的最后一条助手正文（交回主会话时用）。"""
    rid = str(run_id or "").strip()
    if not rid:
        return ""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT props FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' AND props->>'run_id' = :rid "
                 f"AND props->>'by' = 'assistant' "
                 f"ORDER BY created_at DESC, id DESC LIMIT 1"),
            {"rid": rid},
        ).scalar()
    props = _decode(row)
    return str((props or {}).get("content") or "").strip()[:limit]


def results_text(task: Dict[str, Any], per_member: int = 1200) -> str:
    """后台任务的结果摘要：每个成员一条，供主会话整合。"""
    blocks: List[str] = []
    for member in task.get("members") or []:
        name = str(member.get("role_name") or member.get("role_id") or "成员")
        run_id = str(member.get("run_id") or "")
        run = run_service.get_run(run_id) or {}
        status = str(run.get("status") or member.get("status") or "")
        body = _member_result_text(run_id, per_member) or "（没有产出正文）"
        blocks.append(f"### {name}（{status or '未知'}）\n{body}")
    return "\n\n".join(blocks)[:8000]


def _claim_continue(task_id: str) -> bool:
    """CAS 标记「已交回过主会话」；并发点击只有一个成功（幂等）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('team_tasks')} SET "
                 f"continued_at = datetime('now','localtime'), "
                 f"updated_at = datetime('now','localtime') "
                 f"WHERE id = :id "
                 f"AND (continued_at IS NULL OR continued_at = '')"),
            {"id": task_id},
        )
        db.commit()
        return bool(res.rowcount)


def _release_continue(task_id: str) -> None:
    """创建主持 run 失败时回滚标记，允许用户重试。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('team_tasks')} SET continued_at = NULL WHERE id = :id"),
            {"id": task_id},
        )
        db.commit()


def _auto_chain_ok(conversation_id: str) -> bool:
    """同一条对话里一小时内的自动交回次数是否还有额度（防任务链跑飞）。"""
    try:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            used = db.execute(
                text(f"SELECT COUNT(*) FROM {_q('team_tasks')} "
                     f"WHERE conversation_id = :cid "
                     f"AND COALESCE(continued_at, '') <> '' "
                     f"AND continued_at >= datetime('now','localtime','-1 hour')"),
                {"cid": conversation_id},
            ).scalar()
        return int(used or 0) < AUTO_CONTINUE_PER_HOUR
    except Exception:
        logger.debug("自动继续额度检查失败，按允许处理", exc_info=True)
        return True


def on_run_finished(run_id: str) -> Optional[Dict[str, Any]]:
    """成员 run 收尾：整个后台任务结束时，往主会话写一条「任务完成」触发器。

    任务一结束（完成或失败）就把结果交回主会话：汇总成员结果后创建一条主持
    run，由角色整合、修失败、接着推进；不需要用户点「继续对话」。用户主动
    停止的任务不自动重启；会话有待授权 / 待回答时先停下等人。幂等：同一任务
    只写一条触发器、只自动交回一次。
    """
    run = run_service.get_run(run_id) or {}
    task_id = str(run.get("team_task_id") or "").strip()
    if not task_id:
        return None
    task = _fetch(task_id)
    if not task:
        return None
    public = _public(task)
    if public["status"] in (STATUS_RUNNING, STATUS_PAUSED):
        return None
    if int(public.get("active_runs") or 0) > 0:
        return None
    conv_id = str(task.get("conversation_id") or "")
    if not conv_id:
        return None
    from services.agent_zero.task_layer import conversation as _conv

    try:
        _conv.append_task_done_message(conv_id, public, results_text(task, 600))
    except Exception:
        logger.exception("写入后台任务完成触发器失败 task=%s", task_id)
        return None
    # 工单面板同步：任务完成 / 失败 → 对应工单收敛。
    try:
        from services.agent_zero.services import work_service

        work_service.finish_task(public, results_text(task, 1200))
    except Exception:
        logger.debug("后台任务收敛工单失败 task=%s", task_id, exc_info=True)
    # 自动继续：任务完成 / 失败都把结果交回主会话，由主持角色整合、修失败、接着推进。
    status = str(public.get("status") or "")
    if status in (STATUS_DONE, STATUS_FAILED) and not str(task.get("continued_at") or ""):
        try:
            from services.agent_zero.services import work_service

            reason = work_service.pending_reason(conv_id)
            if reason:
                logger.info("会话 %s 待处理（%s），暂不自动继续任务 %s",
                            conv_id, reason, task_id)
            elif not _auto_chain_ok(conv_id):
                logger.info("会话 %s 一小时内自动继续次数已达上限，任务 %s 等待人工确认",
                            conv_id, task_id)
            else:
                continue_task(task_id, user="system")
                if status == STATUS_FAILED:
                    work_service.mark_auto_handled(
                        public, "任务失败，已自动交回角色处理")
        except ValueError:
            # 并发收尾时另一个成员 run 已经交回过（CAS 只允许一次），不是错误。
            logger.debug("后台任务已交回主会话，跳过重复触发 task=%s", task_id)
        except Exception:
            logger.exception("后台任务自动交回主会话失败 task=%s", task_id)
    return public


def continue_task(task_id: str, user: str = "local") -> Dict[str, Any]:
    """把后台任务的结果交回主会话。

    两条入口共用：任务结束（完成 / 失败）时由 `on_run_finished` 自动触发
    （user="system"）；自动交回没有发生时，用户也可以点「继续对话」手动触发。
    汇总成员结果并创建一条主持 run，由角色继续推进；只会交回一次。
    """
    task = _fetch(task_id)
    if not task:
        raise ValueError(f"后台任务不存在: {task_id}")
    public = _public(task)
    if public["status"] not in (STATUS_DONE, STATUS_FAILED, STATUS_STOPPED):
        raise ValueError("后台任务还没结束，等它跑完再继续")
    if int(public.get("active_runs") or 0) > 0:
        raise ValueError("成员仍在执行，请稍后再继续")
    if str(task.get("continued_at") or ""):
        raise ValueError("这个后台任务已经交回过主会话，不重复触发")

    from services.agent_zero.task_layer import conversation

    conv = conversation.get_conversation(str(task.get("conversation_id") or ""))
    if not conv:
        raise ValueError("后台任务所属会话不存在")
    if not _claim_continue(task_id):
        raise ValueError("这个后台任务已经交回过主会话，不重复触发")

    title = str(task.get("title") or "后台任务")
    status = str(public.get("status") or "")
    summary = results_text(task)
    label = {STATUS_DONE: "已完成", STATUS_FAILED: "失败",
             STATUS_STOPPED: "已停止"}.get(status, status or "已结束")
    if status == STATUS_FAILED:
        tail = ("成员执行失败：先诊断失败原因（环境、依赖、权限、命令本身），"
                "能修的先修再重试，不要假装已经完成；修好后继续推进，"
                "需要新的工作时再派发。")
    else:
        tail = ("请基于以上结果继续推进：先给出整合后的结论，再说明还缺什么；"
                "需要新的工作时再派发。不要重复执行已经完成的成员工作。")
    content = (
        f"后台任务「{title}」{label}。\n\n"
        f"## 成员结果\n{summary}\n\n{tail}"
    )
    try:
        run = run_service.create_run(
            str(conv["id"]), user,
            role_id=str(task.get("leader_role_id") or "") or conv.get("role_id"),
            content=content,
            mode="autonomous",
        )
    except Exception:
        _release_continue(task_id)
        raise
    _wake_runs([str(run.get("id") or "")])
    # 完成触发器换成「已交回对话」：自动继续之后卡片上不再留手动按钮。
    try:
        conversation.mark_task_done_continued(str(conv["id"]), task_id)
    except Exception:
        logger.debug("标记任务完成触发器已交回失败 task=%s", task_id, exc_info=True)
    zero_store.append_audit(user, "team_task_continue",
                            args={"task_id": task_id, "run_id": run.get("id"),
                                  "auto": user == "system"})
    return {"task": get_task(task_id), "run": run}


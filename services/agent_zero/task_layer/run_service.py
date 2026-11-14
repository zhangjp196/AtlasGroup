"""对话运行（conversation run）生命周期与事件日志。

**为什么需要**：一轮对话回复此前直接跑在 HTTP SSE 请求里，浏览器一关 /
请求超时，run 就死；长工具只能靠独立线程重跑（还会重复副作用）。这里把 run
落库（状态 + 游标），由 `conv_worker` 后台驱动；连接只是观察窗口：

- 创建：`create_run`（同一对话至多一个活跃 run，双跑在此拦截）；
- 执行：worker `claim_run`（queued → running，CAS 单写）→ 跑完 `finish_run`；
- 观测：每个事件经 `append_event` 落 `conversation_run_events`（递增 seq），
  重连按 `since=seq` 续拉，不依赖连接存续；
- 停止：`request_stop` 标记 + worker 直接取消任务（强制停止，不等事件间隙）。

事件与 `conversation.generate_reply_stream` 的产出同构；delta / thinking 由
worker 侧合并后落库，避免逐 token 写库。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores.zero_store import _q, append_audit, new_id, touch_conversation

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
TERMINAL_STATUSES = (STATUS_DONE, STATUS_FAILED, STATUS_STOPPED)
def _json(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)


def create_run(conversation_id: str, user: str, *,
               role_id: Optional[str] = None,
               target_role_id: Optional[str] = None,
               team_task_id: Optional[str] = None,
               content: Optional[str] = None,
               mentions: Optional[List[str]] = None,
               mode: str = "chat",
               auto_execute: bool = False,
               queue_pos: Optional[int] = None,
               queued_msg_id: Optional[str] = None,
               thinking_level: Optional[str] = None) -> Dict[str, Any]:
    """创建一次对话运行（入队）。

    **队列模式**：同一对话可以有多条 queued run，按队列序号排队（序号小先跑，
    同序号按创建时间）。双跑仍被拦截：`claim_run` 只在该对话没有 running run
    时成功（同一对话至多一条正在执行）。

    queue_pos=None → 追加到队尾（该对话当前最大序号 +1）；
    queue_pos 显式给值 → 按该位置插入（编辑排队消息时保留原位置用）。
    传 -1 表示排在队首（内部只用「移到最前」一个入口，避免序号稀疏）。

    mode='autonomous' = 自主执行（同一 run 内动态规划推进到完成，替代旧的任务实体）。
    auto_execute=True = 跳过确认，直接执行（worker 透传给回复生成）。
    queued_msg_id = 排队消息已落 GDAG 转录的节点 id（消息先可见，run 稍后执行）。
    """
    if mode not in ("chat", "autonomous"):
        raise ValueError(f"非法运行模式: {mode}")
    run_id = new_id("run")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        if queue_pos is None:
            pos = _next_queue_pos(db, conversation_id)
        else:
            pos = int(queue_pos)
        db.execute(
            text(f"INSERT INTO {_q('conversation_runs')} "
                 f"(id, conversation_id, role_id, target_role_id, team_task_id, status, mode, auto_execute, "
                 f" content, mentions, cursor, queue_pos, queued_msg_id, thinking_level, created_by) "
                 f"VALUES (:id, :cid, :rid, :trid, :ttid, :status, :mode, :auto_execute, :content, "
                 f" :mentions, 0, :pos, :qmid, :thinking, :by)"),
            {"id": run_id, "cid": conversation_id, "rid": role_id,
             "trid": (str(target_role_id).strip() or None) if target_role_id else None,
             "ttid": (str(team_task_id).strip() or None) if team_task_id else None,
             "status": STATUS_QUEUED, "mode": mode, "auto_execute": bool(auto_execute), "content": content,
             "mentions": _json(mentions) if mentions is not None else None,
             "pos": pos, "qmid": queued_msg_id,
             "thinking": thinking_level or None,
             "by": user},
        )
        db.commit()
    append_audit(user, "conversation_run_create",
                 args={"run_id": run_id, "conversation_id": conversation_id,
                       "role_id": role_id, "target_role_id": target_role_id or "",
                       "team_task_id": team_task_id or "",
                       "mode": mode, "auto_execute": bool(auto_execute),
                       "queue_pos": pos,
                       "mentions": list(mentions or []),
                       "thinking_level": thinking_level or "",
                       "content_chars": len(content or "")})
    touch_conversation(conversation_id)
    return get_run(run_id) or {"id": run_id, "conversation_id": conversation_id,
                               "status": STATUS_QUEUED, "mode": mode}


def _next_queue_pos(db, conversation_id: str) -> int:
    """该对话排队 run 的尾部序号（追加用）。"""
    row = db.execute(
        text(f"SELECT COALESCE(MAX(queue_pos), 0) FROM {_q('conversation_runs')} "
             f"WHERE conversation_id = :cid AND status = :st"),
        {"cid": conversation_id, "st": STATUS_QUEUED},
    ).scalar()
    return int(row or 0) + 1


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('conversation_runs')} WHERE id = :id"),
            {"id": run_id},
        ).mappings().first()
    return _normalize(dict(row)) if row else None


# 已完成运行的保留天数：事件表按 delta 持续增长，不清理会拖慢库与备份
RUN_HISTORY_DAYS = 7


def prune_history(days: int = RUN_HISTORY_DAYS) -> int:
    """删除过期（> days）的 done / failed / stopped 运行，事件随外键级联删除。

    running / queued 永不触碰；正在被前端观察的 run 也不会被删（只有完成态）。
    days <= 0 表示不清理。返回删除条数。
    """
    if int(days) <= 0:
        return 0
    from services.agent_zero.database import retry_on_locked

    SessionLocal = get_session_local()

    def _run() -> int:
        with SessionLocal() as db:
            res = db.execute(
                text(f"DELETE FROM {_q('conversation_runs')} "
                     f"WHERE status IN ('done', 'failed', 'stopped') "
                     f"AND COALESCE(finished_at, updated_at) < "
                     f"datetime('now','localtime', :cut)"),
                {"cut": f"-{int(days)} days"},
            )
            db.commit()
            return int(res.rowcount or 0)

    return retry_on_locked(_run)


def list_runs(conversation_id: str, limit: int = 20,
              status: Optional[str] = None) -> List[Dict[str, Any]]:
    """对话的运行记录（倒序）。status 可选过滤。"""
    SessionLocal = get_session_local()
    cond = "conversation_id = :cid"
    params: Dict[str, Any] = {"cid": conversation_id, "n": max(1, min(int(limit), 100))}
    if status:
        cond += " AND status = :st"
        params["st"] = status
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('conversation_runs')} WHERE {cond} "
                 f"ORDER BY created_at DESC, id DESC LIMIT :n"),
            params,
        ).mappings().all()
    return [_normalize(dict(r)) for r in rows]


def active_run(conversation_id: str) -> Optional[Dict[str, Any]]:
    """该对话当前活跃的主持 run（queued/running）；无则回退到成员 run。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('conversation_runs')} "
                 f"WHERE conversation_id = :cid AND status IN ('queued','running') "
                 f"ORDER BY CASE WHEN COALESCE(target_role_id, '') = '' THEN 0 ELSE 1 END, "
                 f"created_at DESC, id DESC LIMIT 1"),
            {"cid": conversation_id},
        ).mappings().first()
    return _normalize(dict(row)) if row else None


def list_active_runs(limit: int = 30, exclude_zone: str = "") -> List[Dict[str, Any]]:
    """全局活跃 run（queued/running），含对话标题与工作区——跨工作区可见性用。

    ``exclude_zone``：排除某个 zone 的会话（全局 Agent 不进入自己的注意力队列）。
    """
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        cond = "r.status IN ('queued', 'running')"
        params: Dict[str, Any] = {"n": max(1, min(int(limit), 100))}
        if exclude_zone:
            cond += " AND COALESCE(c.zone, '') <> :zone"
            params["zone"] = exclude_zone
        rows = db.execute(
            text(f"SELECT r.id, r.conversation_id, r.status, r.mode, r.created_at, r.started_at, "
                 f"c.title, c.workspace_id, w.name AS workspace_name "
                 f"FROM {_q('conversation_runs')} r "
                 f"JOIN {_q('conversations')} c ON c.id = r.conversation_id "
                 f"LEFT JOIN {_q('workspaces')} w ON w.id = c.workspace_id "
                 f"WHERE {cond} "
                 f"ORDER BY r.created_at DESC, r.id DESC LIMIT :n"),
            params,
        ).mappings().all()
    return [dict(r) for r in rows]


def list_queued(limit: int = 50) -> List[Dict[str, Any]]:
    """全局待跑 run（worker 取用；按队列序号，同序号按创建时间 = FIFO）。

    主持 run 优先于成员并行 run：先把用户这一轮跑起来，再由它派发成员。
    """
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('conversation_runs')} "
                 f"WHERE status = :st "
                 f"ORDER BY CASE WHEN COALESCE(target_role_id, '') = '' THEN 0 ELSE 1 END, "
                 f"queue_pos ASC, created_at ASC, id ASC LIMIT :n"),
            {"st": STATUS_QUEUED, "n": max(1, min(int(limit), 200))},
        ).mappings().all()
    return [_normalize(dict(r)) for r in rows]


def conversation_queue(conversation_id: str, limit: int = 50,
                       include_member: bool = False) -> List[Dict[str, Any]]:
    """该对话的排队 run（按队列序号，同序号按创建时间）。

    默认不返回团队成员的后台并行 run：它们由主持派发，不是用户排队消息。
    """
    SessionLocal = get_session_local()
    cond = "conversation_id = :cid AND status = :st"
    if not include_member:
        cond += " AND COALESCE(target_role_id, '') = ''"
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('conversation_runs')} "
                 f"WHERE {cond} "
                 f"ORDER BY queue_pos ASC, created_at ASC, id ASC LIMIT :n"),
            {"cid": conversation_id, "st": STATUS_QUEUED,
             "n": max(1, min(int(limit), 200))},
        ).mappings().all()
    return [_normalize(dict(r)) for r in rows]


def running_run(conversation_id: str,
                include_member: bool = True) -> Optional[Dict[str, Any]]:
    """该对话当前正在执行的主持 run（status=running）。

    默认在没有主持 run 时回退到成员 run（“会话是否忙碌”的判断用）。
    **主会话区必须传 ``include_member=False``**：成员的并行后台 run 不是这一轮
    的主回复，不能让主会话的发送按钮跟着锁住 —— 用户要的是「主会话随时能发，
    后台任务在自己的触发器上推进」。
    """
    SessionLocal = get_session_local()
    cond = "conversation_id = :cid AND status = :running"
    if not include_member:
        cond += " AND COALESCE(target_role_id, '') = ''"
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('conversation_runs')} "
                 f"WHERE {cond} "
                 f"ORDER BY CASE WHEN COALESCE(target_role_id, '') = '' THEN 0 ELSE 1 END, "
                 f"started_at DESC, id DESC LIMIT 1"),
            {"cid": conversation_id, "running": STATUS_RUNNING},
        ).mappings().first()
    return _normalize(dict(row)) if row else None


def active_target_run(conversation_id: str,
                      target_role_id: str) -> Optional[Dict[str, Any]]:
    """该成员在会话里是否已有排队 / 执行中的 run。

    角色回复里 `@成员` 时用来去重：已经派过活（或主持已用 delegate 派过）
    就不再重复入队，避免一句话里 @ 两次就派两次。
    """
    tid = str(target_role_id or "").strip()
    if not tid:
        return None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('conversation_runs')} "
                 f"WHERE conversation_id = :cid AND target_role_id = :trid "
                 f"AND status IN ('queued','running') "
                 f"ORDER BY created_at DESC, id DESC LIMIT 1"),
            {"cid": conversation_id, "trid": tid},
        ).mappings().first()
    return _normalize(dict(row)) if row else None


def running_slots() -> List[Dict[str, Any]]:
    """当前正在执行的 run（conversation_id + 是否成员并行 run），供 worker 调度。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT conversation_id, target_role_id FROM {_q('conversation_runs')} "
                 f"WHERE status = :running"),
            {"running": STATUS_RUNNING},
        ).mappings().all()
    return [dict(r) for r in rows]


def parallel_member_limit() -> int:
    """同一对话最多同时跑几个成员 run（主持 run 不占这个名额）。"""
    try:
        from services.agent_zero.config import get_settings

        return max(1, int(get_settings().max_parallel_member_runs))
    except Exception:
        return 4


def running_conversation_ids() -> set:
    """正在执行 run 的对话 id 集合（worker 调度用：同一对话只跑一条）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT DISTINCT conversation_id FROM {_q('conversation_runs')} "
                 f"WHERE status = :running"),
            {"running": STATUS_RUNNING},
        ).fetchall()
    return {r[0] for r in rows}


def run_for_message(conversation_id: str, msg_id: str) -> Optional[Dict[str, Any]]:
    """该消息对应的最近一条 run（排队 / 执行中 / 已完成都可查）。"""
    if not msg_id:
        return None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('conversation_runs')} "
                 f"WHERE conversation_id = :cid AND queued_msg_id = :mid "
                 f"ORDER BY created_at DESC, id DESC LIMIT 1"),
            {"cid": conversation_id, "mid": msg_id},
        ).mappings().first()
    return _normalize(dict(row)) if row else None


def discard_run(run_id: str) -> bool:
    """删除一条排队 run，但**保留**它的消息节点（编辑排队消息时用）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"DELETE FROM {_q('conversation_runs')} "
                 f"WHERE id = :id AND status = :queued"),
            {"id": run_id, "queued": STATUS_QUEUED},
        )
        db.commit()
        return bool(res.rowcount)


def cancel_queued_runs(conversation_id: str) -> int:
    """取消该对话所有排队 run（截断 / 撤回后清理；消息由调用方处理）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"DELETE FROM {_q('conversation_runs')} "
                 f"WHERE conversation_id = :cid AND status = :queued"),
            {"cid": conversation_id, "queued": STATUS_QUEUED},
        )
        db.commit()
        return int(res.rowcount or 0)


def _queue_ids(db, conversation_id: str) -> List[str]:
    """该对话排队 run 的 id，按当前展示顺序（序号升序，同序号按创建时间）。"""
    rows = db.execute(
        text(f"SELECT id FROM {_q('conversation_runs')} "
             f"WHERE conversation_id = :cid AND status = :st "
             f"ORDER BY queue_pos ASC, created_at ASC, id ASC"),
        {"cid": conversation_id, "st": STATUS_QUEUED},
    ).fetchall()
    return [r[0] for r in rows]


def _write_queue_order(db, run_ids: List[str]) -> None:
    """把 0..n-1 的序号整体写回（保持稀疏度为零，后续追加直接用 max+1）。"""
    for idx, rid in enumerate(run_ids):
        db.execute(
            text(f"UPDATE {_q('conversation_runs')} "
                 f"SET queue_pos = :pos, updated_at = datetime('now','localtime') "
                 f"WHERE id = :id AND status = :st"),
            {"pos": idx, "id": rid, "st": STATUS_QUEUED},
        )


def move_queued_run(run_id: str, direction: str) -> bool:
    """上移 / 下移一条排队 run（direction = 'up' | 'down'）。

    返回是否真的移动了：已在边界（队首再上移 / 队尾再下移）或该 run 不再是
    queued（已被调度 / 取消）时返回 False，调用方据此提示「已到顶 / 已到底」。

    **为什么按整表重写而不是交换两行的序号**：历史数据里 queue_pos 可能全为 0
    （从未调整过）或带 -1（旧插队标记迁移来），只交换两行会让中间那些同序号条目
    的相对位置变得不可预测。整体重写 0..n-1 之后，序号唯一且稠密，排序才确定。
    单对话排队条数是个位数到几十，一次全量 UPDATE 的成本可忽略。
    """
    if direction not in ("up", "down"):
        raise ValueError(f"非法移动方向: {direction}")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        run = db.execute(
            text(f"SELECT conversation_id FROM {_q('conversation_runs')} "
                 f"WHERE id = :id AND status = :st"),
            {"id": run_id, "st": STATUS_QUEUED},
        ).first()
        if not run:
            return False
        ids = _queue_ids(db, run[0])
        if run_id not in ids:
            return False
        idx = ids.index(run_id)
        target = idx - 1 if direction == "up" else idx + 1
        if target < 0 or target >= len(ids):
            return False
        ids[idx], ids[target] = ids[target], ids[idx]
        _write_queue_order(db, ids)
        db.commit()
    append_audit("local", "conversation_queue_move",
                 args={"run_id": run_id, "conversation_id": run[0],
                       "direction": direction, "from_index": idx, "to_index": target})
    return True


def prioritize_run(run_id: str) -> bool:
    """把排队中的 run 移到队首（「立即插入」兜底用）。仅 queued 可移动。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        run = db.execute(
            text(f"SELECT conversation_id FROM {_q('conversation_runs')} "
                 f"WHERE id = :id AND status = :st"),
            {"id": run_id, "st": STATUS_QUEUED},
        ).first()
        if not run:
            return False
        ids = _queue_ids(db, run[0])
        if run_id not in ids or ids.index(run_id) == 0:
            return bool(ids and run_id in ids)
        ids.remove(run_id)
        ids.insert(0, run_id)
        _write_queue_order(db, ids)
        db.commit()
    return True


def cancel_run(run_id: str) -> bool:
    """取消排队中的 run（仅 queued）。对应排队消息一并从转录移除。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT queued_msg_id FROM {_q('conversation_runs')} "
                 f"WHERE id = :id AND status = :queued"),
            {"id": run_id, "queued": STATUS_QUEUED},
        ).first()
        if not row:
            return False
        res = db.execute(
            text(f"DELETE FROM {_q('conversation_runs')} "
                 f"WHERE id = :id AND status = :queued"),
            {"id": run_id, "queued": STATUS_QUEUED},
        )
        db.commit()
        deleted = bool(res.rowcount)
    if deleted and row[0]:
        try:
            from services.agent_zero.task_layer import conversation

            conversation.delete_message(str(row[0]))
        except Exception:
            logger.warning("取消排队消息节点失败 msg=%s", row[0])
    return deleted


def inject_run(run_id: str) -> bool:
    """「立即插入」的队列侧：把该排队 run 提到队首，等当前 run 停下后紧接着执行。

    **语义 = 停止当前回复，然后把这条当下一轮跑**（用户要求：不要再把消息塞进
    正在生成的回答里）。真正的停止由路由层做（`task_routes.inject_queued_run`
    调 `request_stop` + `force_stop`），这里只负责：

    - 提到队首，保证停下来的第一件事就是跑它；
    - 清掉消息上可能残留的 `inject=1`：那是旧实现「把消息当干预注入」留下的标记，
      留着会让执行中的 run 把它当干预取走（界面表现为「输出还在原位、消息位置
      又错乱」）。新语义下消息保持 `queued=1`，由 worker 认领时正常清除。
    """
    moved = prioritize_run(run_id)
    if not moved:
        return False
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT queued_msg_id FROM {_q('conversation_runs')} "
                 f"WHERE id = :id AND status = :queued"),
            {"id": run_id, "queued": STATUS_QUEUED},
        ).first()
        if not row:
            return False
        msg_id = row[0]
        if msg_id:
            db.execute(
                text("UPDATE gdag_nodes SET props = json_set(props, '$.inject', 0) "
                     "WHERE id = :nid"),
                {"nid": msg_id},
            )
        db.commit()
        return True


def mark_intervention(msg_id: str) -> bool:
    """把一条排队消息标成「立即取走」的干预（queued=0 + inject=1）。

    旧「立即插入」实现就是走这条路（见 `conversation._drain_new_interventions`）。
    新前台不再产生这种数据，但历史会话里已经写过，排序 / 上下文构建仍需能识别，
    所以保留入口供兼容与测试使用。
    """
    if not msg_id:
        return False
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text("UPDATE gdag_nodes SET props = json_set(props, '$.queued', 0) "
                 "WHERE id = :nid"),
            {"nid": msg_id},
        )
        db.execute(
            text("UPDATE gdag_nodes SET props = json_set(props, '$.inject', 1) "
                 "WHERE id = :nid"),
            {"nid": msg_id},
        )
        db.commit()
        return bool(res.rowcount)


def cancel_run_by_message(msg_id: str) -> bool:
    """消息已被正在执行的 run 作为干预取走：删除它对应的排队 run（不删消息）。

    与 `inject_run` 配对：插入的消息若在 run 结束前被取走，就不必再单独执行。
    """
    if not msg_id:
        return False
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"DELETE FROM {_q('conversation_runs')} "
                 f"WHERE queued_msg_id = :mid AND status = :queued"),
            {"mid": msg_id, "queued": STATUS_QUEUED},
        )
        db.commit()
        return bool(res.rowcount)


def claim_run(run_id: str) -> bool:
    """queued → running（CAS 单写，防多 worker 双跑）。成功返回 True。

    并发规则：
    - 所有会话 / 成员任务共享 ``max_concurrent_conversation_runs`` 全局上限；
      在数据库 CAS 中检查，避免多个 worker 实例各自按本地槽位超额认领；
    - 同一对话**至多一条主持 run**（target_role_id 为空）；
    - 成员并行 run（target_role_id 非空）可以同时跑，上限见
      ``parallel_member_limit()``；主持与成员可以并存（主持派发后立即返回，
      成员继续在后台并行执行）。
    """
    SessionLocal = get_session_local()
    limit = parallel_member_limit()
    from services.agent_zero.config import get_settings

    global_limit = max(0, int(get_settings().max_concurrent_conversation_runs))
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversation_runs')} "
                 f"SET status = :running, started_at = datetime('now','localtime'), updated_at = datetime('now','localtime') "
                 f"WHERE id = :id AND status = :queued "
                 f"AND (SELECT COUNT(*) FROM {_q('conversation_runs')} rg "
                 f"     WHERE rg.status = :running) < :global_limit "
                 f"AND ( "
                 f"  (COALESCE(target_role_id, '') = '' AND NOT EXISTS ( "
                 f"    SELECT 1 FROM {_q('conversation_runs')} r2 "
                 f"    WHERE r2.conversation_id = (SELECT conversation_id FROM {_q('conversation_runs')} WHERE id = :id) "
                 f"      AND r2.status = :running AND COALESCE(r2.target_role_id, '') = '')) "
                 f"  OR "
                 f"  (COALESCE(target_role_id, '') <> '' AND ( "
                 f"    SELECT COUNT(*) FROM {_q('conversation_runs')} r3 "
                 f"    WHERE r3.conversation_id = (SELECT conversation_id FROM {_q('conversation_runs')} WHERE id = :id) "
                 f"      AND r3.status = :running AND COALESCE(r3.target_role_id, '') <> '') < :parallel_limit) "
                 f")"),
            {"id": run_id, "running": STATUS_RUNNING, "queued": STATUS_QUEUED,
             "parallel_limit": limit, "global_limit": global_limit},
        )
        db.commit()
        return bool(res.rowcount)


def finish_run(run_id: str, status: str,
               error: Optional[str] = None) -> bool:
    """收尾（幂等）：仅活跃中的 run 可转终态；已终态不覆盖。"""
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"非法终态: {status}")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversation_runs')} "
                 f"SET status = :st, error = :err, finished_at = datetime('now','localtime'), updated_at = datetime('now','localtime') "
                 f"WHERE id = :id AND status IN ('queued','running')"),
            {"id": run_id, "st": status, "err": (error or None)},
        )
        db.commit()
        return bool(res.rowcount)


def request_stop(run_id: str, *, silent: bool = False) -> bool:
    """请求停止（幂等）；worker 在下一个事件间隙收尾。返回是否有活跃 run 被标记。

    `silent=True` = 「立即插入（停止并继续）」用：停下当前回复属于正常流程，
    收尾时不写 interrupted 事件、不打中断标记（前端因此没有红条与停止提示），
    用户只看到「这轮停了，接着跑新消息」。
    """
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversation_runs')} "
                 f"SET stop_requested = true, "
                 f"    stop_silent = (stop_silent OR :silent), "
                 f"    updated_at = datetime('now','localtime') "
                 f"WHERE id = :id AND status IN ('queued','running')"),
            {"id": run_id, "silent": bool(silent)},
        )
        db.commit()
        return bool(res.rowcount)


def is_stop_silent(run_id: str) -> bool:
    """该 run 的停止是否是静默的（「停止并继续」触发）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        v = db.execute(
            text(f"SELECT stop_silent FROM {_q('conversation_runs')} WHERE id = :id"),
            {"id": run_id},
        ).scalar()
    return bool(v)


def _finalize_interrupted(run_ids: List[str], reason: str) -> None:
    """把已置为 failed 的 run 还原成部分回复，并补一条错误事件给前端。"""
    for rid in run_ids:
        try:
            from services.agent_zero.task_layer import conversation

            node_id = conversation.persist_interrupted_run(
                rid, outcome="failed", error=reason)
            if node_id:
                append_event(rid, "interrupted", {
                    "id": node_id, "outcome": "failed", "error": reason,
                })
            append_event(rid, "error", {"error": reason})
        except Exception:
            logger.exception("恢复 interrupted 回复失败 run=%s", rid)


def recover_running_runs(reason: str = "服务重启，运行中断") -> int:
    """启动恢复：把遗留的 `running` run 收尾为失败。

    进程重启后没有任何执行者会接管这些 run，若不收尾，前端会永远显示「进行中」。
    收尾前先把已落库的事件还原成一条部分回复，避免重启后回复消失。
    仅单实例部署假设（desktop 端已用 flock 保证单实例；服务端需改为租约/心跳）。
    返回被收尾的条数。
    """
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id FROM {_q('conversation_runs')} WHERE status = :running"),
            {"running": STATUS_RUNNING},
        ).mappings().all()
        res = db.execute(
            text(f"UPDATE {_q('conversation_runs')} "
                 f"SET status = :st, error = :err, finished_at = datetime('now','localtime'), updated_at = datetime('now','localtime') "
                 f"WHERE status = :running"),
            {"st": STATUS_FAILED, "err": reason, "running": STATUS_RUNNING},
        )
        db.commit()
    _finalize_interrupted([str(r["id"]) for r in rows], reason)
    return int(res.rowcount or 0)


def orphan_running_runs(active_ids: set, min_age_seconds: float = 30.0) -> List[str]:
    """找出「状态还是 running、但没有任何存活任务持有」的 run。

    单实例下 `active_ids` 就是 worker 的 `_live`；不在里面的 running 行说明执行
    任务已经意外结束（异常绕过了收尾 / 进程被强杀后又被拉起），必须回收，
    否则该对话的队列会永久堵住。`min_age_seconds` 只是避开刚认领的瞬间。
    """
    cutoff = f"-{max(1, int(min_age_seconds))} seconds"
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id FROM {_q('conversation_runs')} "
                 f"WHERE status = :running "
                 f"  AND updated_at < datetime('now','localtime', :cutoff)"),
            {"running": STATUS_RUNNING, "cutoff": cutoff},
        ).mappings().all()
    return [str(r["id"]) for r in rows if str(r["id"]) not in active_ids]


def fail_orphan_run(run_id: str, reason: str) -> bool:
    """回收一条失联的 running run（幂等）；成功返回 True。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversation_runs')} "
                 f"SET status = :st, error = :err, finished_at = datetime('now','localtime'), updated_at = datetime('now','localtime') "
                 f"WHERE id = :id AND status = :running"),
            {"id": run_id, "st": STATUS_FAILED, "err": reason, "running": STATUS_RUNNING},
        )
        db.commit()
        changed = bool(res.rowcount)
    if changed:
        _finalize_interrupted([run_id], reason)
    return changed


def is_stop_requested(run_id: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        v = db.execute(
            text(f"SELECT stop_requested FROM {_q('conversation_runs')} WHERE id = :id"),
            {"id": run_id},
        ).scalar()
    return bool(v)


# ── 事件日志 ────────────────────────────────────────────────


# ── 进程内事件总线 ──────────────────────────────────────────
# 事件落库后立刻推给正在观察的 SSE 连接。只靠轮询数据库的话，正文每
# interval 才跳一大块，流式输出看起来是"一段一段"而不是"流"。
# 队列只在同一进程内有效（当前部署就是 in-process）；多实例下 SSE 仍按
# 游标轮询数据库兜底，不会丢事件。
_subscribers: Dict[str, List[asyncio.Queue]] = {}


def subscribe(run_id: str) -> asyncio.Queue:
    """返回一个队列，后续 append_event 的帧会即时投递到它。"""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    lst = _subscribers.get(run_id)
    if not lst:
        return
    try:
        lst.remove(q)
    except ValueError:
        pass
    if not lst:
        _subscribers.pop(run_id, None)


def _publish(run_id: str, evt: Dict[str, Any]) -> None:
    for q in _subscribers.get(run_id, ()):
        try:
            q.put_nowait(evt)          # 同步投递：观察方从自己的队列取出
        except Exception:              # 队列满 / 连接已断：不影响落库
            pass


def append_event(run_id: str, kind: str, data: Dict[str, Any]) -> Optional[int]:
    """追加一条事件，返回其 seq（run 不存在返回 None）。

    seq 由 run.cursor 原子自增得到（UPDATE ... RETURNING），并发 append 不会撞号。
    备份 VACUUM 等写锁冲突会短暂重试——事件落库失败会打断整轮 run。
    """
    from services.agent_zero.database import retry_on_locked

    SessionLocal = get_session_local()

    def _write() -> Optional[int]:
        with SessionLocal() as db:
            seq = db.execute(
                text(f"UPDATE {_q('conversation_runs')} "
                     f"SET cursor = cursor + 1, updated_at = datetime('now','localtime') "
                     f"WHERE id = :id RETURNING cursor"),
                {"id": run_id},
            ).scalar()
            if seq is None:
                return None
            db.execute(
                text(f"INSERT INTO {_q('conversation_run_events')} "
                     f"(run_id, seq, kind, data) VALUES (:rid, :seq, :kind, :data)"),
                {"rid": run_id, "seq": int(seq), "kind": kind,
                 "data": _json(data)},
            )
            db.commit()
            return int(seq)

    seq = retry_on_locked(_write)
    if seq is None:
        return None
    _publish(run_id, {**(data or {}), "_seq": seq, "_kind": kind})
    return seq


def list_events(run_id: str, since: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
    """since 之后的事件（seq 升序），最多 limit 条。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT seq, kind, data, created_at FROM {_q('conversation_run_events')} "
                 f"WHERE run_id = :rid AND seq > :since "
                 f"ORDER BY seq ASC LIMIT :n"),
            {"rid": run_id, "since": max(0, int(since)),
             "n": max(1, min(int(limit), 1000))},
        ).mappings().all()
    return _decode_events(rows)


def list_all_events(run_id: str) -> List[Dict[str, Any]]:
    """该 run 的全部事件（用于中断后还原部分回复）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT seq, kind, data, created_at FROM {_q('conversation_run_events')} "
                 f"WHERE run_id = :rid ORDER BY seq ASC"),
            {"rid": run_id},
        ).mappings().all()
    return _decode_events(rows)


def _decode_events(rows) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        raw = r["data"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        data = dict(raw or {})
        data["_seq"] = int(r["seq"])
        data["_kind"] = r["kind"]
        data["_at"] = str(r["created_at"] or "")
        out.append(data)
    return out


def count_runs(conversation_id: Optional[str] = None,
               status: Optional[str] = None) -> int:
    SessionLocal = get_session_local()
    cond, params = ["1=1"], {}
    if conversation_id:
        cond.append("conversation_id = :cid")
        params["cid"] = conversation_id
    if status:
        cond.append("status = :st")
        params["st"] = status
    with SessionLocal() as db:
        n = db.execute(
            text(f"SELECT COUNT(*) FROM {_q('conversation_runs')} "
                 f"WHERE {' AND '.join(cond)}"),
            params,
        ).scalar()
    return int(n or 0)


def _normalize(row: Dict[str, Any]) -> Dict[str, Any]:
    """把行内时间序列化为字符串，mentions JSONB → list。"""
    for k in ("created_at", "updated_at", "started_at", "finished_at"):
        if row.get(k) is not None:
            row[k] = str(row[k])
    m = row.get("mentions")
    if isinstance(m, str):
        try:
            row["mentions"] = json.loads(m)
        except Exception:
            row["mentions"] = []
    if not row.get("mode"):
        row["mode"] = "chat"
    return row

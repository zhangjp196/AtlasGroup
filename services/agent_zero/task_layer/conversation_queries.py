"""Read-side conversation queries and transcript projections."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services.agent_zero.services import attachment_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer.conversation_constants import (
    CLEAR_KIND,
    GLOBAL_AGENT_ZONE,
)
from services.agent_zero.task_layer.message_order import order_messages_by_turn


ARCHIVE_AFTER_DAYS = 30
_archive_sweep_at = 0.0


def archive_stale_conversations(days: int = ARCHIVE_AFTER_DAYS) -> int:
    """把 `days` 天内没有活动（updated_at）的对话标记为 archived。幂等、可反复调用。"""
    global _archive_sweep_at
    import time

    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    now = time.time()
    if now - _archive_sweep_at < 600:  # 每 10 分钟最多扫一次
        return 0
    _archive_sweep_at = now
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} SET archived = 1 "
                 f"WHERE archived = 0 "
                 f"AND COALESCE(zone, '') <> '{GLOBAL_AGENT_ZONE}' "
                 f"AND id NOT IN (SELECT conversation_id FROM {_q('bot_routes')}) "
                 f"AND updated_at < datetime('now','localtime','-{int(days)} days')")
        )
        db.commit()
        return res.rowcount or 0


def _page_message_stats(db: Any, items: list, *, by_session: bool = False) -> None:
    """给一页会话就地补上 unread / unread_count / message_count / last_message。

    `by_session=True` 时把一条会话根下的全部栏目一起统计（会话中心的一张卡 =
    一个会话，未读 / 预览取整棵会话）。

    列表页一次最多 50 条，逐条跑「消息数 + 最后一条 + 未读」会变成 150 次查询
    （模板里最贵的接口之一）。这里改成按页两次聚合：

    1. 统计：每个会话的最近助手消息时间 + 未读条数（未读要跟各自的 last_read_at
       比较，所以把这一页的 (id, last_read_at) 作为 CTE 传进去）；
    2. 预览：窗口函数按会话分组取最后一条消息，顺带拿到该会话消息总数。

    两条 SQL 都显式走 gdag_nodes 的 (conversation_id, stage) 表达式索引，
    只扫本页会话的消息，不随总会话数线性变慢。
    """
    if not items:
        return
    from sqlalchemy import text
    from services.agent_zero.stores.zero_store import _q

    values = []
    params: Dict[str, Any] = {}
    for i, item in enumerate(items):
        params[f"id{i}"] = str(item.get("id") or "")
        params[f"lr{i}"] = str(item.get("last_read_at") or "")
        values.append(f"(:id{i}, :lr{i})")
    page = f"WITH page(id, last_read) AS (VALUES {', '.join(values)})"
    if by_session:
        scope = (f", threads(root_id, tid, last_read) AS ("
                 f"SELECT p.id, c.id, c.last_read_at FROM page p "
                 f"JOIN {_q('conversations')} c "
                 f"ON c.id = p.id OR c.session_id = p.id)")
        source = (f"page p JOIN threads t ON t.root_id = p.id "
                  f"JOIN {_q('gdag_nodes')} g INDEXED BY idx_gdag_conv "
                  f"ON json_extract(g.props, '$.conversation_id') = t.tid")
        group = "p.id"
        unread_time = "t.last_read"
        preview_partition = "t.root_id"
    else:
        scope = ""
        source = (f"page p JOIN {_q('gdag_nodes')} g INDEXED BY idx_gdag_conv "
                  f"ON json_extract(g.props, '$.conversation_id') = p.id")
        group = "p.id"
        unread_time = "p.last_read"
        preview_partition = "json_extract(g.props, '$.conversation_id')"

    stats_sql = (
        f"{page}{scope} "
        f"SELECT {group} AS cid, "
        f"  MAX(CASE WHEN json_extract(g.props, '$.by') = 'assistant' "
        f"           THEN g.created_at END) AS last_assistant_at, "
        f"  SUM(CASE WHEN json_extract(g.props, '$.by') = 'assistant' "
        f"           AND json_extract(g.props, '$.stage') = 'exploration' "
        f"           AND COALESCE(json_extract(g.props, '$.kind'), '') <> 'checkpoint' "
        f"           AND g.created_at > COALESCE({unread_time}, '') "
        f"           THEN 1 ELSE 0 END) AS unread_count "
        f"FROM {source} "
        f"WHERE g.type = 'fact' "
        f"GROUP BY {group}"
    )
    preview_sql = (
        f"{page}{scope} "
        f"SELECT cid, content, n FROM ("
        f"  SELECT {preview_partition} AS cid, "
        f"    json_extract(g.props, '$.content') AS content, "
        f"    COUNT(*) OVER (PARTITION BY {preview_partition}) AS n, "
        f"    ROW_NUMBER() OVER (PARTITION BY {preview_partition} "
        f"      ORDER BY g.created_at DESC, g.rowid DESC) AS rn "
        f"  FROM {source} "
        f"  WHERE g.type = 'fact' "
        f"    AND json_extract(g.props, '$.stage') = 'exploration' "
        f"    AND COALESCE(json_extract(g.props, '$.kind'), '') "
        f"        NOT IN ('checkpoint', :clear_kind)"
        f") WHERE rn = 1"
    )

    stats = {str(r["cid"]): r for r in
             db.execute(text(stats_sql), params).mappings().all()}
    previews = {str(r["cid"]): r for r in
                db.execute(text(preview_sql),
                           {**params, "clear_kind": CLEAR_KIND}).mappings().all()}

    for item in items:
        cid = str(item.get("id") or "")
        st = stats.get(cid)
        last_at = str(st["last_assistant_at"] or "") if st else ""
        item["unread"] = bool(last_at) and last_at > str(item.get("last_read_at") or "")
        item["unread_count"] = int(st["unread_count"] or 0) if st else 0
        pv = previews.get(cid)
        item["message_count"] = int(pv["n"] or 0) if pv else 0
        item["last_message"] = str(pv["content"] or "")[:120] if pv else ""


def list_conversations(workspace_id: Optional[str] = None,
                       role_id: Optional[str] = None,
                       team_id: Optional[str] = None,
                       limit: int = 50, offset: int = 0,
                       keyword: Optional[str] = None,
                       source: Optional[str] = None,
                       forked_from_id: Optional[str] = None,
                       include_archived: bool = False,
                       session_id: Optional[str] = None,
                       roots_only: bool = False) -> list:
    """对话列表（工作区 / 角色 / 关键字 / 来源过滤；附消息数 + 最后一条预览）。

    **排序 = 进行中优先，两组各自按时间倒序**：有活跃 run（queued/running/waiting）
    或 active 目标的会话算「进行中」，排在前面；其余算「完结」，排在后面。
    同一组内按 updated_at 倒序（最近活动在最上）。

    每条带 `active`（进行中）/ `has_active_goal` / `run_status` 字段，前端据此分组。

    `roots_only` = 只列会话根（会话中心 / 角色详情用）；`session_id` = 列某个根
    会话下的全部栏目（窗口左侧栏目栏用）。根的统计（运行 / 未读 / 更新时间）
    按整棵会话聚合，栏目则保持自己的口径。

    source='task_result' = 后台任务结果会话（收件箱）。
    默认排除自动归档（30 天无活动）的对话；include_archived=True 时包含。
    """
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    archive_stale_conversations()
    scope_expr = "COALESCE(NULLIF(c.session_id, ''), c.id)"
    # live = 正在跑（有活跃 run）或正在追（active 目标）。用子查询算在 SQL 里，
    # 这样分页仍然正确：不能取完一页再在 Python 里排序。
    if roots_only:
        live_expr = (
            f"CASE WHEN EXISTS (SELECT 1 FROM {_q('conversation_runs')} r "
            f"  JOIN {_q('conversations')} tc ON tc.id = r.conversation_id "
            f"  WHERE COALESCE(NULLIF(tc.session_id, ''), tc.id) = {scope_expr} "
            f"  AND r.status IN ('queued','running','waiting')) "
            f"OR EXISTS (SELECT 1 FROM {_q('goals')} gg "
            f"  JOIN {_q('conversations')} tg ON tg.id = gg.conversation_id "
            f"  WHERE COALESCE(NULLIF(tg.session_id, ''), tg.id) = {scope_expr} "
            f"  AND gg.status = 'active') "
            f"THEN 1 ELSE 0 END"
        )
        running_count_expr = (
            f"(SELECT COUNT(*) FROM {_q('conversation_runs')} r "
            f"  JOIN {_q('conversations')} tc ON tc.id = r.conversation_id "
            f"  WHERE COALESCE(NULLIF(tc.session_id, ''), tc.id) = {scope_expr} "
            f"  AND r.status IN ('queued','running','waiting'))"
        )
        sort_expr = (
            f"(SELECT MAX(tc.updated_at) FROM {_q('conversations')} tc "
            f"  WHERE COALESCE(NULLIF(tc.session_id, ''), tc.id) = {scope_expr})"
        )
    else:
        live_expr = (
            f"CASE WHEN EXISTS (SELECT 1 FROM {_q('conversation_runs')} r "
            f"  WHERE r.conversation_id = c.id "
            f"  AND r.status IN ('queued','running','waiting')) "
            f"OR EXISTS (SELECT 1 FROM {_q('goals')} gg "
            f"  WHERE gg.conversation_id = c.id AND gg.status = 'active') "
            f"THEN 1 ELSE 0 END"
        )
        running_count_expr = (
            f"(SELECT COUNT(*) FROM {_q('conversation_runs')} r "
            f"  WHERE r.conversation_id = c.id "
            f"  AND r.status IN ('queued','running','waiting'))"
        )
        sort_expr = "c.updated_at"
    # 未读 / 消息数不在主查询里做相关子查询：那是「每个会话各扫一遍 gdag_nodes」，
    # 300 个会话就要扫 300 次全表（实测 3.6s）。主查询只取分页需要的最小列，
    # 未读与预览在拿到这一页 id 之后按页批量算，见 _page_message_stats。
    base = (f"SELECT c.id, c.workspace_id, c.bound_workspace_id, c.workspace_custom, "
            f"c.role_id, c.team_id, c.session_id, "
            f"{scope_expr} AS session_root_id, "
            f"(SELECT COUNT(*) FROM {_q('conversations')} tc "
            f"  WHERE tc.session_id = {scope_expr} OR tc.id = {scope_expr}) AS thread_count, "
            f"c.zone, c.source, c.title, "
            f"c.archived, c.forked_from_id, c.forked_from_message_id, "
            f"c.created_by, c.created_at, {sort_expr} AS sort_at, c.last_read_at, "
            f"{running_count_expr} AS running_count, "
            f"(SELECT r2.status FROM {_q('conversation_runs')} r2 "
            f"  WHERE r2.conversation_id = c.id "
            f"  AND r2.status IN ('queued','running','waiting') "
            f"  ORDER BY r2.created_at DESC, r2.id DESC LIMIT 1) AS run_status, "
            f"(SELECT gg2.status FROM {_q('goals')} gg2 "
            f"  WHERE gg2.conversation_id = c.id ORDER BY gg2.created_at DESC, "
            f"  gg2.id DESC LIMIT 1) AS goal_status, "
            f"{live_expr} AS is_live "
            f"FROM {_q('conversations')} c WHERE 1=1")
    params: Dict[str, Any] = {"limit": max(1, min(int(limit), 200)),
                              "offset": max(0, int(offset))}
    if not include_archived:
        base += " AND c.archived = 0"
    if roots_only:
        base += " AND COALESCE(c.session_id, '') = ''"
    if session_id:
        base += f" AND {scope_expr} = :sid"
        params["sid"] = str(session_id)
    if workspace_id:
        base += " AND c.workspace_id = :wid"
        params["wid"] = workspace_id
    if role_id:
        base += " AND c.role_id = :rid"
        params["rid"] = role_id
    if team_id:
        base += " AND c.team_id = :tid"
        params["tid"] = team_id
    if source:
        base += " AND c.source = :src"
        params["src"] = source
    if forked_from_id:
        # 分支树：查某条会话（两跳内的直接子分支）。
        base += " AND c.forked_from_id = :fork"
        params["fork"] = forked_from_id
    if keyword:
        # 用 json_extract 而不是 ->>：表达式索引建在 json_extract 上，->> 命中不了。
        base += (f" AND EXISTS (SELECT 1 FROM {_q('gdag_nodes')} g "
                 f"WHERE g.type = 'fact' "
                 f"AND json_extract(g.props, '$.conversation_id') = c.id "
                 f"AND json_extract(g.props, '$.stage') = 'exploration' "
                 f"AND COALESCE(json_extract(g.props, '$.kind'), '') <> 'checkpoint' "
                 f"AND json_extract(g.props, '$.content') LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    # 进行中在前（is_live DESC），组内时间倒序。
    sql = (f"SELECT * FROM ({base}) t "
           f"ORDER BY t.is_live DESC, t.sort_at DESC, t.created_at DESC, t.id "
           f"LIMIT :limit OFFSET :offset")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(text(sql), params).mappings().all()
        out = []
        for r in rows:
            item = dict(r)
            root = str(item.get("session_root_id") or item.get("id") or "")
            item["created_at"] = str(item.get("created_at") or "")
            item["updated_at"] = str(item.get("sort_at") or item.get("created_at") or "")
            item["session_id"] = root
            item["session_root_id"] = root
            item["thread_count"] = int(item.get("thread_count") or 1)
            item.pop("sort_at", None)
            item["active"] = bool(item.pop("is_live", 0))
            item["running_count"] = int(item.get("running_count") or 0)
            item["last_read_at"] = str(item.get("last_read_at") or "")
            item["run_status"] = str(item.pop("run_status", "") or "")
            item["workspace_id"] = zero_store.public_workspace_id(item.get("workspace_id"))
            item["workspace_custom"] = bool(item["workspace_id"])
            item["has_active_goal"] = item.pop("goal_status", None) == "active"
            out.append(item)
        # 未读 / 消息数 / 最后一条预览：整页两次聚合查询，不再逐条 N+1。
        # 根会话按整棵会话（含栏目）统计；列栏目时按各自的会话口径。
        _page_message_stats(db, out, by_session=roots_only)
    # token 用量：整页一次聚合（输入 / 输出 / 速度 / 缓存），列表卡片直接展示。
    from services.agent_zero.services import usage_service

    usage_map = usage_service.usage_by_conversation(
        [item["id"] for item in out], stage="exploration")
    for item in out:
        item["usage"] = usage_map.get(item["id"]) or usage_service.empty_usage()
    return out


def mark_conversation_read(conv_id: str) -> bool:
    """标记已读；标记会话根时，同时清除其所有栏目的未读状态。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} "
                 f"SET last_read_at = datetime('now','localtime') "
                 f"WHERE id = :cid OR session_id = :cid"),
            {"cid": conv_id},
        )
        db.commit()
        return bool(res.rowcount)


def mark_all_conversations_read() -> int:
    """一键已读：把全部未归档普通会话的已读水位推到当前时间。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('conversations')} "
                 f"SET last_read_at = datetime('now','localtime') "
                 f"WHERE archived = 0 AND COALESCE(zone, '') <> 'global-agent'")
        )
        db.commit()
        return int(res.rowcount or 0)


def count_conversations(workspace_id: Optional[str] = None,
                        role_id: Optional[str] = None,
                        team_id: Optional[str] = None,
                        keyword: Optional[str] = None,
                        source: Optional[str] = None,
                        forked_from_id: Optional[str] = None,
                        include_archived: bool = False,
                        session_id: Optional[str] = None,
                        roots_only: bool = False) -> int:
    """对话总数（与 list_conversations 同过滤条件）。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    sql = f"SELECT COUNT(*) FROM {_q('conversations')} WHERE 1=1"
    if not include_archived:
        sql += " AND archived = 0"
    if roots_only:
        sql += " AND COALESCE(session_id, '') = ''"
    if session_id:
        sql += " AND COALESCE(NULLIF(session_id, ''), id) = :sid"
    params: Dict[str, Any] = {}
    if session_id:
        params["sid"] = str(session_id)
    if workspace_id:
        sql += " AND workspace_id = :wid"
        params["wid"] = workspace_id
    if role_id:
        sql += " AND role_id = :rid"
        params["rid"] = role_id
    if team_id:
        sql += " AND team_id = :tid"
        params["tid"] = team_id
    if keyword:
        sql += (f" AND EXISTS (SELECT 1 FROM {_q('gdag_nodes')} g "
                f"WHERE g.type = 'fact' "
                f"AND json_extract(g.props, '$.conversation_id') = {_q('conversations')}.id "
                f"AND json_extract(g.props, '$.stage') = 'exploration' "
                f"AND COALESCE(json_extract(g.props, '$.kind'), '') <> 'checkpoint' "
                f"AND json_extract(g.props, '$.content') LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    if source:
        sql += " AND source = :src"
        params["src"] = source
    if forked_from_id:
        sql += " AND forked_from_id = :fork"
        params["fork"] = forked_from_id
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        n = db.execute(text(sql), params).scalar()
    return int(n or 0)


def _last_message(db: Any, conv_id: str) -> str:
    from sqlalchemy import text
    from services.agent_zero.stores.zero_store import _q

    row = db.execute(
        text(f"SELECT props->>'content' AS c FROM {_q('gdag_nodes')} "
             f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
             f"AND props->>'stage' = 'exploration' "
             f"AND COALESCE(props->>'kind', '') <> 'checkpoint' "
             f"AND COALESCE(props->>'kind', '') <> :clear_kind "
             f"ORDER BY created_at DESC, rowid DESC LIMIT 1"),
        {"cid": conv_id, "clear_kind": CLEAR_KIND},
    ).scalar()
    return str(row or "")[:120]


def _count_messages(db: Any, conv_id: str) -> int:
    from sqlalchemy import text
    from services.agent_zero.stores.zero_store import _q

    n = db.execute(
        text(f"SELECT COUNT(*) FROM {_q('gdag_nodes')} "
             f"WHERE type = 'fact' AND props->>'conversation_id' = :cid "
             f"AND props->>'stage' = 'exploration' "
             f"AND COALESCE(props->>'kind', '') <> 'checkpoint' "
             f"AND COALESCE(props->>'kind', '') <> :clear_kind"),
        {"cid": conv_id, "clear_kind": CLEAR_KIND},
    ).scalar()
    return int(n or 0)


def _usage_of_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从一条 gdag 行里取出 props.usage（props / usage 可能是 JSON 文本）。"""
    if not row:
        return {}
    props = row.get("props")
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except Exception:
            return {}
    if not isinstance(props, dict):
        return {}
    usage = props.get("usage")
    if isinstance(usage, str):
        try:
            usage = json.loads(usage)
        except Exception:
            return {}
    return usage if isinstance(usage, dict) else {}


def usage_total(conv_id: str) -> Dict[str, Any]:
    """当前会话的用量总览：输入/输出为合计，缓存命中与速度按总量重算（即加权平均）。

    直接聚合库里的助手回复（不只当前加载的 200 条转录），所以长会话也准确。
    运行中的那一轮还没落库，由前端把实时 usage 叠加上去。

    `last_prompt_tokens` = 最近一轮的真实 prompt（当前上下文占用），
    `max_context_tokens` = 历史峰值（仅供「峰值 / 压缩前」对照）。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(
                f"SELECT "
                f"SUM(CAST(json_extract(props, '$.usage.prompt_tokens') AS INTEGER)) AS prompt_tokens, "
                f"SUM(CAST(json_extract(props, '$.usage.completion_tokens') AS INTEGER)) AS completion_tokens, "
                f"SUM(CAST(json_extract(props, '$.usage.cached_tokens') AS INTEGER)) AS cached_tokens, "
                f"SUM(CAST(json_extract(props, '$.usage.llm_ms') AS INTEGER)) AS llm_ms, "
                f"SUM(CAST(json_extract(props, '$.usage.rounds') AS INTEGER)) AS rounds, "
                f"MAX(CAST(json_extract(props, '$.usage.max_context_tokens') AS INTEGER)) AS max_context_tokens, "
                f"SUM(CASE WHEN json_extract(props, '$.usage') IS NOT NULL THEN 1 ELSE 0 END) AS turns "
                f"FROM {_q('gdag_nodes')} "
                f"WHERE type = 'fact' "
                f"  AND json_extract(props, '$.conversation_id') = :cid "
                f"  AND json_extract(props, '$.stage') = 'exploration' "
                f"  AND json_extract(props, '$.by') = 'assistant'"
            ),
            {"cid": conv_id},
        ).mappings().first()
        # 「当前上下文占用」取自**最近一轮有真实 prompt 的回复**，而不是会话峰值：
        # 压缩之后 prompt 会变小，取 MAX 的话顶部百分比永远停在压缩前的高位。
        # 老数据没有 last_prompt_tokens 字段时退回该轮的 max_context_tokens。
        last_row = db.execute(
            text(f"SELECT props FROM {_q('gdag_nodes')} "
                 f"INDEXED BY idx_gdag_conv_stage "
                 f"WHERE type = 'fact' "
                 f"  AND json_extract(props, '$.conversation_id') = :cid "
                 f"  AND json_extract(props, '$.stage') = 'exploration' "
                 f"  AND json_extract(props, '$.by') = 'assistant' "
                 f"  AND COALESCE(CAST(json_extract(props, "
                 f"      '$.usage.prompt_tokens') AS INTEGER), 0) > 0 "
                 f"ORDER BY created_at DESC, rowid DESC LIMIT 1"),
            {"cid": conv_id},
        ).mappings().first()
    last_usage = _usage_of_row(last_row)
    last_prompt = (int(last_usage.get("last_prompt_tokens") or 0)
                   or int(last_usage.get("max_context_tokens") or 0))
    data = dict(row or {})
    prompt = int(data.get("prompt_tokens") or 0)
    completion = int(data.get("completion_tokens") or 0)
    cached = int(data.get("cached_tokens") or 0)
    llm_ms = int(data.get("llm_ms") or 0)
    total = prompt + completion
    turns = int(data.get("turns") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
        "llm_ms": llm_ms,
        "rounds": int(data.get("rounds") or 0),
        "max_context_tokens": int(data.get("max_context_tokens") or 0),
        "last_prompt_tokens": last_prompt,
        "turns": turns,
        "cache_rate": round(cached / prompt * 100, 1) if prompt else 0,
        "speed": round(completion / (llm_ms / 1000), 1) if llm_ms else 0,
        "avg_tokens_per_turn": round(total / turns, 1) if turns else 0,
    }


def list_messages(conv_id: str, limit: int = 200) -> list:
    """探索态消息（GDAG fact 节点，按时间升序）。"""
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, props, created_at FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' "
                 f"AND json_extract(props, '$.conversation_id') = :cid "
                 f"AND json_extract(props, '$.stage') = 'exploration' "
                 # 转任务建议节点不单独成条消息：它现在是那次回复轨迹里的
                 # 一个 step（kind='proposal'），单独列出来会变成一个"额外回合"，
                 # 看起来跑到正文前面去了（实测踩到）。
                 # 节点本身仍保留——确认/忽略要靠它的 status/task_id。
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> 'task_proposal' "
                 f"AND COALESCE(json_extract(props, '$.kind'), '') <> 'checkpoint' "
                 f"ORDER BY created_at ASC, rowid ASC LIMIT :n"),
            {"cid": conv_id, "n": max(1, min(int(limit), 500))},
        ).mappings().all()
        # 建议的状态只存在节点上；消息轨迹里的 step 是生成时的快照，
        # 这里以节点为准回填，否则刷新后已忽略/已接受会重新变回 pending。
        proposal_rows = db.execute(
            text(f"SELECT id, props FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' "
                 f"AND json_extract(props, '$.conversation_id') = :cid "
                 f"AND json_extract(props, '$.kind') = 'task_proposal'"),
            {"cid": conv_id},
        ).mappings().all()
        # 消息所属 run：排队消息的**真实回合位置**由 run 决定，而不是它的发送时刻。
        # 队列模式下用户在上一轮回复还在跑时发消息，消息节点立刻落库（created_at 早于
        # 那条回复），但它的 run 要等当前 run 收尾才启动。按 created_at 排就会把它插到
        # 上一轮回复**前面**（实测：u2 排在了 a1 之前），看起来像回复答错了对象。
        # rowid 作次级键：时间只到秒，同一秒内建的多条 run 靠它区分先后。
        run_rows = db.execute(
            text(f"SELECT rowid AS seq, id, queued_msg_id, target_role_id, status, "
                 f"started_at, created_at "
                 f"FROM {_q('conversation_runs')} WHERE conversation_id = :cid"),
            {"cid": conv_id},
        ).mappings().all()
        # 历史数据兜底：「立即插入」被取走时的归属字段是后加的，老消息上没有。
        # 但「取走」这件事本身就落在事件日志里（intervention 事件带 run_id + 消息 id），
        # 据此还原归属，老会话刷新后位置也正确，不必单独跑一次数据迁移。
        injected_rows = db.execute(
            text(f"SELECT json_extract(e.data, '$.intervention.id') AS mid, e.run_id AS rid "
                 f"FROM {_q('conversation_run_events')} e "
                 f"JOIN {_q('conversation_runs')} r ON r.id = e.run_id "
                 f"WHERE r.conversation_id = :cid "
                 f"AND json_extract(e.data, '$.intervention.id') IS NOT NULL"),
            {"cid": conv_id},
        ).mappings().all()
    from services.agent_zero.stores.zero_store import decode_row

    proposal_status: dict = {}
    for pr in proposal_rows:
        pp = decode_row({"props": pr["props"]}).get("props") or {}
        proposal_status[str(pr["id"])] = {
            "status": pp.get("status") or "pending",
            "run_id": pp.get("run_id") or "",
        }

    # 消息 id → run 的排序锚点 (时刻, run 行序号)。时刻用 run 的**实际启动时刻**；
    # 尚未启动的排队 run（started_at 为空）用它自己的 created_at。
    # 只到秒的时间戳不足以区分同一秒内建的 run，所以带上 rowid 作次级键。
    msg_run_anchor: dict = {}
    run_anchor_by_id: dict = {}
    # 老数据兜底：`task_ack` 的 anchor_run_id 是后加的。历史确认消息没有这个字段，
    # 但它的 run 与它由同一次派发几乎同时创建（同一秒），且 run 的 target_role_id
    # 就是确认消息的 role_id。据此还原锚点，老会话刷新后顺序也正确，不必迁移数据。
    ack_run_by_role_created: dict = {}
    for rr in run_rows:
        rid = str(rr["id"] or "")
        seq = int(rr["seq"] or 0)
        anchor = (str(rr["started_at"] or rr["created_at"] or ""), seq)
        if rid:
            run_anchor_by_id[rid] = anchor
        trid = str(rr["target_role_id"] or "")
        created = str(rr["created_at"] or "")
        if trid and created:
            prev = ack_run_by_role_created.get((trid, created))
            if prev is None or seq > prev[0]:
                ack_run_by_role_created[(trid, created)] = (seq, rid)
        mid = str(rr["queued_msg_id"] or "")
        if not mid:
            continue
        prev = msg_run_anchor.get(mid)
        if prev is None or anchor < prev:
            msg_run_anchor[mid] = anchor

    # 「立即插入」被哪一轮取走：新数据落在 props.injected_into_run 上；
    # 老数据没有这个字段，但从事件日志能还原（intervention 事件带 run_id + 消息 id）。
    # 不回填的老消息刷新后会退回自己的 created_at，位置仍然夹在当前回答上面。
    injected_run_by_msg: dict = {}
    for ir in injected_rows:
        mid = str(ir["mid"] or "")
        rid = str(ir["rid"] or "")
        if mid and rid:
            injected_run_by_msg.setdefault(mid, rid)

    out = []
    for r in rows:
        props = decode_row({"props": r["props"]}).get("props") or {}
        trace = props.get("trace") or []
        tool_trace = props.get("tool_trace") or []
        for steps in (trace, tool_trace):
            for step in steps:
                if step.get("kind") != "proposal":
                    continue
                live = proposal_status.get(str(step.get("proposal_id") or ""))
                if live:
                    step["status"] = live["status"]
                    if live["run_id"]:
                        step["run_id"] = live["run_id"]
        kind = str(props.get("kind") or "")
        anchor_run_id = str(props.get("anchor_run_id") or "")
        if not anchor_run_id and kind == "task_ack":
            role_id = str(props.get("role_id") or "")
            created = str(r["created_at"] or "")
            if role_id and created:
                anchor_run_id = (ack_run_by_role_created.get((role_id, created))
                                 or (0, ""))[1]
        out.append({
            "id": r["id"],
            "content": props.get("content") or "",
            "by": props.get("by") or "",
            "display_by": props.get("display_by") or "",
            "role_id": props.get("role_id") or "",
            "role_name": props.get("role_name") or "",
            "role_avatar": props.get("role_avatar") or "",
            "role_title": props.get("role_title") or "",
            "role_department": props.get("role_department") or "",
            "duration_ms": int(props.get("duration_ms") or 0),
            # 深度思考 + 工具调用情况：刷新后仍可回看（与流式一致）
            "reasoning": props.get("reasoning") or "",
            "tool_trace": tool_trace,
            # 有序轨迹：思考 / 工具 / 中间正文按发生顺序（前端渲染时间线的唯一依据）
            "trace": trace,
            # token 用量（输入/输出）：轨迹面板指标条
            "usage": props.get("usage") or {},
            "refs": props.get("refs") or [],
            "attachments": attachment_service.public_attachments(
                conv_id, props.get("attachments") or []),
            "kind": kind,
            "goal": props.get("goal") or "",
            "reason": props.get("reason") or "",
            "status": props.get("status") or "",
            # 任务完成通知要靠它区分 成功 / 失败 / 已取消（前端按它上色）
            "outcome": props.get("outcome") or "",
            "error": props.get("error") or "",
            "interrupted": bool(props.get("interrupted")),
            "task_id": props.get("task_id") or "",
            "task_title": props.get("task_title") or "",
            "task_status": props.get("task_status") or "",
            # 完成触发器是否已交回对话（自动继续 / 手动点「继续对话」都会置位）
            "task_continued": bool(props.get("continued")),
            "summary": props.get("summary") or "",
            # 该条回复所属的运行：转录排序要以这一轮 run 的启动时刻为锚点
            "run_id": props.get("run_id") or "",
            # 「立即插入」被哪一轮取走：它的排队 run 已被删除，没有这个归属就只能按
            # 自己的 created_at 排（会显示在当前回答上面）。见 mark_message_injected。
            # 老数据从事件日志还原，见 injected_run_by_msg。
            "injected_into_run": (props.get("injected_into_run")
                                  or injected_run_by_msg.get(str(r["id"] or "")) or ""),
            # 确认消息（task_ack）属于哪一轮：只用于转录排序。新数据写在
            # props.anchor_run_id；老数据按 (role_id, 创建秒) 反查成员 run 还原。
            "anchor_run_id": anchor_run_id,
            "queued": int(props.get("queued") or 0) == 1,
            "edited": bool(props.get("edited_at")),
            "ts": props.get("ts") or str(r["created_at"] or ""),
        })
    return order_messages_by_turn(out, msg_run_anchor, run_anchor_by_id)


def list_conversation_ids(workspace_id: Optional[str] = None,
                          role_id: Optional[str] = None,
                          include_archived: bool = True) -> List[str]:
    """按工作区 / 角色列出**所有**对话 id（不分页）。

    批量删除用：对话列表是分页的，前端只有当前一页的 id，全删必须由后端取全量。
    默认包含已归档对话——「删除全部」的语义就是清空，归档的不该漏下。
    """
    from sqlalchemy import text
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    sql = f"SELECT id FROM {_q('conversations')} WHERE 1=1"
    params: Dict[str, Any] = {}
    if not include_archived:
        sql += " AND archived = 0"
    if workspace_id:
        sql += " AND workspace_id = :wid"
        params["wid"] = workspace_id
    if role_id:
        sql += " AND role_id = :rid"
        params["rid"] = role_id
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(text(sql), params).fetchall()
    return [str(r[0]) for r in rows]

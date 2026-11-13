"""模型 token 使用统计（从对话消息的 usage 字段聚合）。

数据来源：`gdag_nodes` 里 `by=assistant` 的消息 `props.usage`。每次 run 结束
都会把本轮各次 LLM 往返的 prompt / completion / cached / llm_ms / rounds 落库，
因此历史数据不需要额外埋点即可统计。

口径：
- 输入 = 各轮 prompt 之和（多轮工具循环每轮都会重发上下文，累加才是真实开销）
- 输出 = 各轮 completion 之和
- 缓存命中率 = cached / prompt
- 生成速度 = completion / llm_ms
- 1d / 7d / 30d 按本地时间滚动窗口；all 为全部历史
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores.zero_store import _q

logger = logging.getLogger(__name__)

PERIODS = (("1d", 1), ("7d", 7), ("30d", 30), ("all", 0))

# 单条实体（会话 / 工单 / 任务 / run）的用量口径与总览一致：
# 输入 = 各轮 prompt 之和，输出 = 各轮 completion 之和，速度与缓存按总量重算。
USAGE_AGG_KEYS = ("prompt_tokens", "completion_tokens", "cached_tokens",
                  "llm_ms", "rounds", "turns")


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            continue
    return None


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _channel_map() -> Dict[str, str]:
    """模型 ID → 渠道名（用当前模型配置映射；历史渠道同名即可）。"""
    try:
        from services.agent_zero.runtime.zero import llm as llm_mod

        out: Dict[str, str] = {}
        for profile in llm_mod.list_model_profiles():
            model = str(profile.get("effective_model") or profile.get("model") or "").strip()
            if not model:
                continue
            channel = str(profile.get("channel_name") or profile.get("channel_id") or "").strip()
            if channel:
                out[model] = channel
        return out
    except Exception:
        return {}


def _load_events() -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        # 只取「助手消息 + 带 usage」的节点：usage 页每次刷新都会走这里，
        # 先全表取回再在 Python 里过滤，历史一长就是几百毫秒。
        # json_extract 写法能命中 idx_gdag_by 表达式索引。
        rows = db.execute(
            text(f"SELECT props, created_at FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact' "
                 f"  AND json_extract(props, '$.by') = 'assistant' "
                 f"  AND json_extract(props, '$.usage') IS NOT NULL")
        ).mappings().all()
    events: List[Dict[str, Any]] = []
    for row in rows:
        props = _as_dict(row.get("props"))
        if str(props.get("by") or "") != "assistant":
            continue
        usage = _as_dict(props.get("usage"))
        if not usage:
            continue
        prompt = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
        if not prompt and not completion:
            continue
        ts = _parse_time(row.get("created_at")) or _parse_time(props.get("ts"))
        if ts is None:
            continue
        cached = _int(usage.get("cached_tokens"))
        llm_ms = _int(usage.get("llm_ms"))
        duration_ms = _int(props.get("duration_ms"))
        rounds = _int(usage.get("rounds")) or 1
        events.append({
            "ts": ts,
            "model": str(props.get("model") or "").strip() or "unknown",
            "conversation_id": str(props.get("conversation_id") or ""),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "cached_tokens": min(cached, prompt) if prompt else cached,
            "max_context_tokens": _int(usage.get("max_context_tokens")),
            "llm_ms": llm_ms,
            "duration_ms": duration_ms,
            "rounds": rounds,
        })
    events.sort(key=lambda e: e["ts"], reverse=True)
    return events


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _usage_agg_sql(column: str, stage: Optional[str] = None) -> str:
    if column not in ("conversation_id", "run_id"):
        raise ValueError(f"unsupported usage group: {column}")
    sums = ", ".join(
        f"SUM(CAST(json_extract(props, '$.usage.{key}') AS INTEGER)) AS {key}"
        for key in USAGE_AGG_KEYS if key != "turns"
    )
    # turns = 有 usage 的助手消息条数（usage 本身不存 turns 字段）。
    sums += ", COUNT(*) AS turns"
    # 一律用 json_extract 而不是 ->>：表达式索引建在 json_extract 上，
    # ->> 写法命中不了索引，会长成「每次全表扫 gdag_nodes」。
    stage_filter = "  AND json_extract(props, '$.stage') = :stage " if stage else ""
    return (
        f"SELECT json_extract(props, '$.{column}') AS grp, {sums} "
        f"FROM {_q('gdag_nodes')} "
        f"WHERE type = 'fact' "
        f"  AND json_extract(props, '$.by') = 'assistant' "
        f"  AND json_extract(props, '$.usage') IS NOT NULL "
        f"  AND json_extract(props, '$.{column}') "
        f"      IN (SELECT value FROM json_each(:values)) "
        f"{stage_filter}"
        f"GROUP BY grp"
    )


def _finish_usage(row: Dict[str, Any]) -> Dict[str, Any]:
    """把 SUM 出来的原始字段补成前端直接可用的口径（缓存率 / 速度 / 均值）。"""
    prompt = _int(row.get("prompt_tokens"))
    completion = _int(row.get("completion_tokens"))
    cached = _int(row.get("cached_tokens"))
    if prompt:
        cached = min(cached, prompt)
    llm_ms = _int(row.get("llm_ms"))
    rounds = _int(row.get("rounds"))
    turns = _int(row.get("turns"))
    total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
        "cache_rate": round(cached / prompt * 100, 1) if prompt else 0.0,
        "llm_ms": llm_ms,
        "speed": round(completion / (llm_ms / 1000), 1) if llm_ms else 0.0,
        "rounds": rounds,
        "turns": turns,
        "avg_tokens_per_turn": round(total / turns, 1) if turns else 0.0,
    }


def empty_usage() -> Dict[str, Any]:
    return _finish_usage({})


def sum_usage(usages: Optional[Any]) -> Dict[str, Any]:
    """把多份用量相加（速度 / 缓存率按合计重算，不做平均）。"""
    sums = {key: 0 for key in USAGE_AGG_KEYS}
    for usage in usages or []:
        if not isinstance(usage, dict):
            continue
        for key in USAGE_AGG_KEYS:
            sums[key] += _int(usage.get(key))
    return _finish_usage(sums)


def _aggregate(column: str, values: Optional[Any],
               stage: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    ids = [str(item).strip() for item in (values or []) if str(item or "").strip()]
    if not ids:
        return {}
    params: Dict[str, Any] = {"values": json.dumps(sorted(set(ids)))}
    if stage:
        params["stage"] = stage
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(_usage_agg_sql(column, stage)), params,
        ).mappings().all()
    return {str(row["grp"]): _finish_usage(dict(row))
            for row in rows if str(row.get("grp") or "")}


def usage_by_conversation(conversation_ids: Optional[Any] = None,
                          stage: Optional[str] = None
                          ) -> Dict[str, Dict[str, Any]]:
    """按会话聚合 token 用量（一次查询覆盖整页列表，避免 N+1）。

    ``stage='exploration'`` 只算前台对话轮次，与 ``conversation.usage_total``
    口径一致（后台任务的 task 阶段由任务 / 工单各自统计，不重复计入会话）。
    """
    return _aggregate("conversation_id", conversation_ids, stage)


def usage_by_run(run_ids: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    """按 run 聚合 token 用量（后台任务成员 run 精确归因）。

    运行中的 run 还没落成助手消息，GDAG 里查不到用量；这里用该 run 最新的
    `usage` 事件补上（事件是累计快照，按 run 取最新，再与已落库用量取较大值），
    任务中心 / 工单的 tokens 才能在后台执行过程中实时变化。
    """
    ids = [str(item).strip() for item in (run_ids or []) if str(item or "").strip()]
    usage = _aggregate("run_id", ids)
    for run_id, live in _live_usage_by_run(ids).items():
        current = usage.get(run_id)
        if not current or int(live.get("total_tokens") or 0) > int(current.get("total_tokens") or 0):
            usage[run_id] = live
    return usage


def _live_usage_by_run(run_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """每个 run 最新一条 usage 事件的累计快照（没有则跳过）。"""
    ids = [str(item).strip() for item in (run_ids or []) if str(item or "").strip()]
    if not ids:
        return {}
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(
                f"SELECT e.run_id, e.data "
                f"FROM {_q('conversation_run_events')} e "
                f"JOIN (SELECT run_id, MAX(seq) AS seq "
                f"      FROM {_q('conversation_run_events')} "
                f"      WHERE kind = 'usage' "
                f"        AND run_id IN (SELECT value FROM json_each(:ids)) "
                f"      GROUP BY run_id) last "
                f"  ON last.run_id = e.run_id AND last.seq = e.seq"
            ),
            {"ids": json.dumps(sorted(set(ids)))},
        ).mappings().all()
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        data = _as_dict(row.get("data"))
        usage = _as_dict(data.get("usage"))
        prompt = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
        if prompt <= 0 and completion <= 0:
            continue
        out[str(row["run_id"])] = _finish_usage({
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cached_tokens": _int(usage.get("cached_tokens")),
            "llm_ms": _int(usage.get("llm_ms")),
            "rounds": _int(usage.get("rounds")),
            "turns": 1,
        })
    return out


def task_run_map(task_ids: Optional[Any] = None) -> Dict[str, List[str]]:
    """群组后台任务 → 成员 run id 列表。"""
    ids = [str(item).strip() for item in (task_ids or []) if str(item or "").strip()]
    if not ids:
        return {}
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, members FROM {_q('team_tasks')} "
                 f"WHERE id IN (SELECT value FROM json_each(:ids))"),
            {"ids": json.dumps(sorted(set(ids)))},
        ).mappings().all()
    out: Dict[str, List[str]] = {}
    for row in rows:
        run_ids = []
        for member in _as_list(row.get("members")):
            if not isinstance(member, dict):
                continue
            run_id = str(member.get("run_id") or "").strip()
            if run_id:
                run_ids.append(run_id)
        out[str(row["id"])] = run_ids
    return out


def usage_by_task(task_ids: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    """按群组后台任务聚合 token（= 各成员 run 的用量之和）。"""
    run_map = task_run_map(task_ids)
    run_usage = usage_by_run({rid for ids in run_map.values() for rid in ids})
    out: Dict[str, Dict[str, Any]] = {}
    for task_id, run_ids in run_map.items():
        sums = {key: 0 for key in USAGE_AGG_KEYS}
        for run_id in run_ids:
            usage = run_usage.get(run_id)
            if not usage:
                continue
            for key in USAGE_AGG_KEYS:
                sums[key] += _int(usage.get(key))
        out[task_id] = _finish_usage(sums)
    return out


def _empty_totals() -> Dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "messages": 0,
        "rounds": 0,
        "llm_ms": 0,
        "duration_ms": 0,
        "max_context_tokens": 0,
    }


def _finish_totals(totals: Dict[str, Any], *, days: int,
                   events: List[Dict[str, Any]]) -> Dict[str, Any]:
    prompt = totals["prompt_tokens"]
    completion = totals["completion_tokens"]
    total = totals["total_tokens"]
    messages = totals["messages"]
    cached = totals["cached_tokens"]
    llm_ms = totals["llm_ms"]
    active_days = len({e["ts"].date() for e in events})
    denominator_days = days if days > 0 else max(1, active_days)
    out = dict(totals)
    out.update({
        "cache_rate": round(cached / prompt * 100, 1) if prompt else 0.0,
        "avg_tokens_per_message": round(total / messages, 1) if messages else 0.0,
        "avg_prompt_per_message": round(prompt / messages, 1) if messages else 0.0,
        "avg_completion_per_message": round(completion / messages, 1) if messages else 0.0,
        "avg_llm_ms": round(llm_ms / messages, 1) if messages else 0.0,
        "tokens_per_second": round(completion / (llm_ms / 1000), 1) if llm_ms else 0.0,
        "active_days": active_days,
        "avg_tokens_per_day": round(total / denominator_days, 1) if denominator_days else 0.0,
        "avg_tokens_per_active_day": round(total / active_days, 1) if active_days else 0.0,
    })
    return out


def _bucket(events: List[Dict[str, Any]], channels: Dict[str, str]) -> Dict[str, Any]:
    totals = _empty_totals()
    by_model: Dict[str, Dict[str, Any]] = {}
    by_channel: Dict[str, Dict[str, Any]] = {}
    for e in events:
        model = e["model"]
        channel = channels.get(model) or "—"
        for bucket, key in ((by_model, model), (by_channel, channel)):
            row = bucket.setdefault(key, {
                "name": key,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "messages": 0,
                "rounds": 0,
                "llm_ms": 0,
            })
            row["prompt_tokens"] += e["prompt_tokens"]
            row["completion_tokens"] += e["completion_tokens"]
            row["total_tokens"] += e["total_tokens"]
            row["cached_tokens"] += e["cached_tokens"]
            row["messages"] += 1
            row["rounds"] += e["rounds"]
            row["llm_ms"] += e["llm_ms"]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                    "cached_tokens", "rounds", "llm_ms"):
            totals[key] += e[key]
        totals["messages"] += 1
        totals["duration_ms"] += e["duration_ms"]
        totals["max_context_tokens"] = max(totals["max_context_tokens"],
                                           e["max_context_tokens"])

    def _finish_rows(rows: Dict[str, Dict[str, Any]], name_key: str) -> List[Dict[str, Any]]:
        out = []
        for key, row in rows.items():
            prompt = row["prompt_tokens"]
            row["cache_rate"] = round(row["cached_tokens"] / prompt * 100, 1) if prompt else 0.0
            row["share"] = round(row["total_tokens"] / totals["total_tokens"] * 100, 1) if totals["total_tokens"] else 0.0
            row["tokens_per_second"] = round(
                row["completion_tokens"] / (row["llm_ms"] / 1000), 1) if row["llm_ms"] else 0.0
            out.append(row)
        out.sort(key=lambda r: r["total_tokens"], reverse=True)
        return out

    return {
        "totals": totals,
        "models": _finish_rows(by_model, "model"),
        "channels": _finish_rows(by_channel, "channel"),
    }


def _period_summary(events: List[Dict[str, Any]], since: Optional[datetime],
                    days: int, channels: Dict[str, str]) -> Dict[str, Any]:
    selected = [e for e in events if since is None or e["ts"] >= since]
    bucket = _bucket(selected, channels)
    totals = _finish_totals(bucket["totals"], days=days, events=selected)
    top_model = bucket["models"][0] if bucket["models"] else None
    peak = None
    if selected:
        day_totals: Dict[str, int] = defaultdict(int)
        for e in selected:
            day_totals[e["ts"].strftime("%Y-%m-%d")] += e["total_tokens"]
        day, value = max(day_totals.items(), key=lambda kv: kv[1])
        peak = {"date": day, "total_tokens": value}
    recent = []
    for e in selected[:20]:
        recent.append({
            "ts": e["ts"].strftime("%Y-%m-%d %H:%M:%S"),
            "model": e["model"],
            "channel": channels.get(e["model"]) or "—",
            "prompt_tokens": e["prompt_tokens"],
            "completion_tokens": e["completion_tokens"],
            "total_tokens": e["total_tokens"],
            "cached_tokens": e["cached_tokens"],
            "llm_ms": e["llm_ms"],
            "duration_ms": e["duration_ms"],
            "rounds": e["rounds"],
        })
    return {
        "since": since.strftime("%Y-%m-%d %H:%M:%S") if since else None,
        "days": days,
        "totals": totals,
        "models": bucket["models"],
        "channels": bucket["channels"],
        "top_model": top_model,
        "peak_day": peak,
        "recent": recent,
    }


def _daily_series(events: List[Dict[str, Any]], days: int = 30) -> List[Dict[str, Any]]:
    today = datetime.now().date()
    start = today - timedelta(days=days - 1)
    buckets: Dict[str, Dict[str, Any]] = {}
    for i in range(days):
        day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        buckets[day] = {"date": day, "prompt_tokens": 0, "completion_tokens": 0,
                        "total_tokens": 0, "messages": 0}
    for e in events:
        key = e["ts"].strftime("%Y-%m-%d")
        row = buckets.get(key)
        if row is None:
            continue
        row["prompt_tokens"] += e["prompt_tokens"]
        row["completion_tokens"] += e["completion_tokens"]
        row["total_tokens"] += e["total_tokens"]
        row["messages"] += 1
    return [buckets[(start + timedelta(days=i)).strftime("%Y-%m-%d")]
            for i in range(days)]


def build_stats() -> Dict[str, Any]:
    events = _load_events()
    channels = _channel_map()
    now = datetime.now()
    periods: Dict[str, Any] = {}
    for key, days in PERIODS:
        since = None if days <= 0 else now - timedelta(days=days)
        periods[key] = _period_summary(events, since, days, channels)
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "periods": periods,
        "daily": _daily_series(events, 30),
        "channels": channels,
    }

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
        rows = db.execute(
            text(f"SELECT props, created_at FROM {_q('gdag_nodes')} "
                 f"WHERE type = 'fact'")
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

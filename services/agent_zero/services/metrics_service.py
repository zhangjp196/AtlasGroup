"""本地可观测性：运行指标 / 健康检查 / 最近运行。

全部基于 sqlite 现成数据与进程内计数器，不引入外部监控依赖。
"""

from __future__ import annotations

import shutil
import time
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import text

from services.agent_zero.database import get_db_path, get_session_local
from services.agent_zero.stores.zero_store import _q


def _app_version() -> str:
    try:
        from services.agent_zero import webapp

        return str(getattr(webapp, "APP_VERSION", ""))
    except Exception:
        return ""


def classify_error(message: Any) -> str:
    s = str(message or "").lower()
    if not s:
        return "none"
    if "没有任何输出" in s or "卡死" in s or "失联" in s or "stall" in s:
        return "stall"
    if "鉴权" in s or "auth" in s or "401" in s or "403" in s:
        return "auth"
    if "网络" in s or "connect" in s or "connection" in s or "断流" in s:
        return "network"
    if "timeout" in s or "超时" in s or "timed out" in s:
        return "timeout"
    if "context" in s or "token" in s or "too long" in s or "溢出" in s or "过长" in s:
        return "overflow"
    if "429" in s or "rate limit" in s:
        return "rate_limit"
    return "other"


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, int(round((p / 100.0) * (len(vals) - 1)))))
    return round(vals[idx], 1)


def run_metrics(hours: int = 24) -> Dict[str, Any]:
    hours = max(1, min(int(hours or 24), 24 * 30))
    table = f"{_q('conversation_runs')}"
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(
                f"SELECT id, conversation_id, status, mode, error, created_at, "
                f"       started_at, finished_at, "
                f"       CASE WHEN finished_at IS NOT NULL AND started_at IS NOT NULL "
                f"            THEN ROUND((julianday(finished_at)-julianday(started_at))*86400, 3) "
                f"            ELSE NULL END AS duration_s "
                f"FROM {table} "
                f"WHERE created_at >= datetime('now','localtime', :since) "
                f"ORDER BY created_at DESC"
            ),
            {"since": f"-{hours} hours"},
        ).mappings().all()
        current = db.execute(
            text(f"SELECT status, COUNT(*) AS n FROM {table} "
                 f"WHERE status IN ('queued','running') GROUP BY status"),
        ).mappings().all()

    counts = {"done": 0, "failed": 0, "stopped": 0, "running": 0, "queued": 0}
    durations: List[float] = []
    errors: Dict[str, int] = {}
    for r in rows:
        st = str(r["status"] or "")
        counts[st] = counts.get(st, 0) + 1
        if r["duration_s"] is not None:
            durations.append(float(r["duration_s"]))
        if st in ("failed", "stopped"):
            k = classify_error(r["error"])
            errors[k] = errors.get(k, 0) + 1

    finished = counts["done"] + counts["failed"]
    return {
        "window_hours": hours,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runs": {
            "total": len(rows),
            **{k: counts.get(k, 0) for k in
               ("done", "failed", "stopped", "running", "queued")},
            "success_rate": round(counts["done"] / finished * 100, 1) if finished else 0.0,
            "p50_s": _pct(durations, 50),
            "p95_s": _pct(durations, 95),
            "max_s": round(max(durations), 1) if durations else 0.0,
        },
        "errors": dict(sorted(errors.items(), key=lambda x: -x[1])),
        "now": {str(r["status"]): int(r["n"]) for r in current},
        "llm": _llm_stats(),
        "db": db_stats(),
        "backups": _backup_stats(),
    }


def _llm_stats() -> Dict[str, Any]:
    try:
        from services.agent_zero.runtime.zero import llm

        return llm.error_stats()
    except Exception:
        return {}


def db_stats() -> Dict[str, Any]:
    path = get_db_path()
    size = 0
    try:
        from pathlib import Path

        size = Path(path).stat().st_size
    except OSError:
        pass
    disk = {}
    try:
        from pathlib import Path

        usage = shutil.disk_usage(str(Path(path).parent))
        disk = {"free": usage.free, "total": usage.total}
    except Exception:
        pass
    return {"path": path, "size": size, "disk": disk}


def _backup_stats() -> Dict[str, Any]:
    try:
        from services.agent_zero.services import backup_service

        return backup_service.stats()
    except Exception:
        return {}


def recent_runs(limit: int = 100) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 500))
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(
                f"SELECT r.id, r.conversation_id, c.title AS conversation_title, "
                f"       r.status, r.mode, r.error, r.created_at, r.started_at, "
                f"       r.finished_at, r.updated_at "
                f"FROM {_q('conversation_runs')} r "
                f"LEFT JOIN {_q('conversations')} c ON c.id = r.conversation_id "
                f"ORDER BY r.created_at DESC LIMIT :n"
            ),
            {"n": limit},
        ).mappings().all()
    return [dict(r) for r in rows]


def schema_version() -> int:
    try:
        from services.agent_zero.database import get_schema_version

        return int(get_schema_version())
    except Exception:
        return 0


def health() -> Dict[str, Any]:
    """深健康检查：DB 可读 + worker 心跳 + 队列/运行数。"""
    data: Dict[str, Any] = {"version": _app_version(), "ts": time.time()}
    ok = True

    db_ok = False
    try:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            db.execute(text("SELECT 1")).scalar()
        db_ok = True
    except Exception as e:
        data["db_error"] = f"{type(e).__name__}: {e}"
    data["db"] = {"ok": db_ok, "schema_version": schema_version(), **db_stats()}
    ok = ok and db_ok

    worker: Dict[str, Any] = {}
    try:
        from services.agent_zero.task_layer import conv_worker, run_service

        w = conv_worker.get_worker()
        worker = w.stats()
        age = worker.get("last_cycle_age_s")
        worker["ok"] = bool(worker.get("is_running")) and (
            age is None or float(age) < 30.0)
        worker["running_now"] = int(run_service.count_runs(status=run_service.STATUS_RUNNING))
        worker["queued_now"] = int(run_service.count_runs(status=run_service.STATUS_QUEUED))
        ok = ok and bool(worker["ok"])
    except Exception as e:
        worker = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        ok = False
    data["worker"] = worker
    data["ok"] = ok
    return data



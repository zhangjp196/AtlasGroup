"""sqlite 备份 / 完整性检查 / 恢复（桌面单机）。

- 每日一份 `VACUUM INTO` 快照，保留最近 N 份；启动时补一次过期备份；
- 启动前 `PRAGMA quick_check`，损坏时把坏文件改名并从最近备份恢复；
- 备份/恢复都走独立 sqlite 连接，不依赖 SQLAlchemy 引擎。
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.agent_zero.database import get_db_path

logger = logging.getLogger(__name__)

KEEP_BACKUPS = 7
_TS_FMT = "%Y%m%d-%H%M%S"


def _db_path() -> Path:
    return Path(get_db_path())


def backup_dir() -> Path:
    d = _db_path().parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _conn(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or _db_path()), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def integrity_ok(path: Optional[Path] = None) -> bool:
    target = path or _db_path()
    if not target.is_file():
        return True
    try:
        with _conn(target) as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return bool(row) and str(row[0]).lower() == "ok"
    except Exception as e:
        logger.warning("数据库完整性检查失败 %s: %s", target, e)
        return False


def create_backup(reason: str = "manual") -> Dict[str, Any]:
    """VACUUM INTO 一份快照，返回 {path, name, size, created_at}。"""
    src = _db_path()
    if not src.is_file():
        raise FileNotFoundError(f"数据库不存在: {src}")
    reason = "".join(ch for ch in str(reason or "manual")
                     if ch.isalnum() or ch in "-_")[:24] or "manual"
    dest = backup_dir() / f"agent_zero-{datetime.now().strftime(_TS_FMT)}-{reason}.db"
    if dest.exists():
        dest = dest.with_name(dest.stem + f"-{int(time.time() % 1000)}.db")
    with _conn() as conn:
        conn.execute("VACUUM INTO ?", (str(dest),))
    _prune()
    info = {
        "path": str(dest),
        "name": dest.name,
        "size": dest.stat().st_size,
        "created_at": dest.stat().st_mtime,
    }
    logger.info("数据库备份完成: %s (%d bytes)", dest, info["size"])
    try:
        from services.agent_zero.services.events import log_event

        log_event("backup.created", backup=dest.name, size=info["size"], reason=reason)
    except Exception:
        pass
    return info


def list_backups() -> List[Dict[str, Any]]:
    d = backup_dir()
    items: List[Dict[str, Any]] = []
    for p in sorted(d.glob("agent_zero-*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            st = p.stat()
        except OSError:
            continue
        items.append({
            "name": p.name,
            "path": str(p),
            "size": st.st_size,
            "created_at": st.st_mtime,
            "created": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return items


def latest_backup() -> Optional[Dict[str, Any]]:
    items = list_backups()
    return items[0] if items else None


def stats() -> Dict[str, Any]:
    items = list_backups()
    latest = items[0] if items else None
    return {
        "count": len(items),
        "keep": KEEP_BACKUPS,
        "latest": latest,
        "total_bytes": sum(i["size"] for i in items),
    }


def _prune() -> None:
    items = list_backups()
    for old in items[KEEP_BACKUPS:]:
        try:
            Path(old["path"]).unlink()
        except OSError:
            pass


def ensure_daily_backup(max_age_s: float = 24 * 3600) -> Optional[Dict[str, Any]]:
    if not _db_path().is_file():
        # 首次启动：数据库还没建出来，没什么可备份（不该打一条错误堆栈）
        return None
    latest = latest_backup()
    if latest and (time.time() - float(latest["created_at"])) < max_age_s:
        return None
    try:
        return create_backup("daily")
    except Exception:
        logger.exception("每日备份失败")
        return None


def restore_latest() -> Optional[Dict[str, Any]]:
    """用最近备份替换当前数据库（仅在完整性检查失败时调用）。"""
    src = _db_path()
    latest = latest_backup()
    if not latest:
        logger.error("数据库损坏且没有可用备份: %s", src)
        return None
    stamp = datetime.now().strftime(_TS_FMT)
    corrupt = src.with_name(src.name + f".corrupt-{stamp}")
    try:
        if src.exists():
            shutil.move(str(src), str(corrupt))
        shutil.copy2(latest["path"], str(src))
        logger.warning("数据库已从备份恢复: %s（损坏文件: %s）", latest["name"], corrupt.name)
        try:
            from services.agent_zero.services.events import log_event

            log_event("backup.restored", backup=latest["name"], corrupt=corrupt.name)
        except Exception:
            pass
        return latest
    except Exception:
        logger.exception("从备份恢复数据库失败")
        return None


def preflight() -> Dict[str, Any]:
    """启动前完整性检查；损坏则尝试恢复。返回 {ok, restored}。"""
    src = _db_path()
    if not src.is_file():
        return {"ok": True, "restored": None}
    if integrity_ok(src):
        return {"ok": True, "restored": None}
    restored = restore_latest()
    return {"ok": restored is not None, "restored": restored}

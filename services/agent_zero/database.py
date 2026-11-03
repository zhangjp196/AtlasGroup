"""AtlasX 数据库（sqlite 单文件，唯一数据源）。

V3 直接按 ORM 模型建表（`Base.metadata.create_all`），不再兼容旧版 sqlite 库：
版本守卫见 `init_database`。本地单用户，没有 schema / 连接池账号等概念。
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from typing import Any

from sqlalchemy import String, Text, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """ORM 基类。"""


_engine = None
_session_factory = None

# 当前 sqlite 结构版本。v4 起不再兼容旧库：启动时归档旧文件，按新结构建空库。
SCHEMA_VERSION = 4


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _config() -> dict:
    from services.shared.yaml_config import load_service_config

    path = os.environ.get("AI_PLATFORM_CONFIG_YAML") or os.path.join(
        _project_root(), "config", "agent_zero", "config.yaml"
    )
    try:
        return load_service_config(path) or {}
    except Exception as e:  # pragma: no cover
        logger.warning("读取数据库配置失败: %s", e)
        return {}


def get_db_path() -> str:
    """sqlite 文件路径（相对项目根解析）。"""
    from services.shared.project_paths import resolved_sqlite_db_path

    raw = str((_config().get("sqlalchemy") or {}).get("db_path")
              or "data/agent_zero/agent_zero.db")
    return resolved_sqlite_db_path(raw)


def get_engine():
    """进程内单例 sqlite 引擎。"""
    global _engine
    if _engine is not None:
        return _engine

    path = get_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sec = _config().get("sqlalchemy") or {}
    # 连接池上限要覆盖「并发 run × 每个 run 的短会话」+ 路由线程池。
    # sqlite 连接本身很便宜，池子太小只会让并发会话排队等连接（表现为莫名变慢）。
    try:
        pool_size = max(5, int(sec.get("pool_size") or 10))
    except (TypeError, ValueError):
        pool_size = 10
    try:
        max_overflow = max(0, int(sec.get("max_overflow") or 20))
    except (TypeError, ValueError):
        max_overflow = 20
    _engine = create_engine(
        f"sqlite:///{path}",
        echo=bool(sec.get("echo") or False),
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=30,
        pool_recycle=1800,
    )

    @event.listens_for(_engine, "connect")
    def _on_connect(dbapi_conn, record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    logger.info("agent_zero sqlite: %s", path)
    return _engine


def get_session_local():
    """线程安全的会话工厂（在首次使用的线程上创建）。

    工厂绑定当时的引擎。测试 / 桌面端可能重建引擎（换库文件），所以这里比对
    引擎实例：不一致就重建工厂，避免旧线程拿着上一个库的会话写数据。
    """
    engine = get_engine()
    factory = getattr(_session_factory, "factory", None)
    if factory is None or getattr(_session_factory, "engine", None) is not engine:
        factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        _session_factory.factory = factory
        _session_factory.engine = engine
    return factory


_session_factory = threading.local()


def is_locked_error(exc: BaseException) -> bool:
    """sqlite 写锁冲突（VACUUM / 备份 / 并发写时会出现）。"""
    text_ = str(exc).lower()
    return "database is locked" in text_ or "database table is locked" in text_


def retry_on_locked(fn: Any, *, attempts: int = 4, delay: float = 0.2) -> Any:
    """执行 fn()；只在「database is locked」时短暂重试。

    备份的 VACUUM INTO 可能让写事务等超过 busy_timeout：一次锁冲突不该把整轮
    对话的落库打断（append_event / 节点写入是热路径）。
    """
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except Exception as e:
            if attempt + 1 >= attempts or not is_locked_error(e):
                raise
            time.sleep(delay * (attempt + 1))
    return None


def _column_default_sql(column: Any) -> str:
    """NOT NULL 新列的默认值（优先 server_default，其次 Python 标量默认值）。

    sqlite 的 `ALTER TABLE ... ADD COLUMN` 只接受常量默认值：`datetime(...)`
    这类表达式必须写成 `DEFAULT (expr)`，否则报 "default value is not constant"。
    """
    if column.server_default is not None:
        raw = str(column.server_default.arg).strip()
        if "(" in raw and not (raw.startswith("(") and raw.endswith(")")):
            return f"({raw})"
        return raw
    if column.default is not None and not column.default.is_callable:
        value = column.default.arg
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
    if isinstance(column.type, (Text, String)):
        return "''"
    return "0"


def _migrate_additive(conn) -> list:
    """按 ORM 结构给已有库补缺失列（只加不删，不改类型）。

    V3 基线用 `create_all` 建表，但 `create_all` 不会给已存在的表加列：
    旧库（历史 priority 队列、没有 stop_silent / queue_pos 的库）会因此在新代码
    上直接报 `no such column`。这里只做「加列」这一种安全的增量迁移，不删除、
    不改类型；用户数据原样保留。
    """
    added: list = []
    for table in Base.metadata.sorted_tables:
        rows = conn.execute(text(f'PRAGMA table_info("{table.name}")')).mappings().all()
        existing = {str(row["name"]) for row in rows}
        if not existing:  # 新表由 create_all 建，不在这里处理
            continue
        for column in table.columns:
            if column.name in existing:
                continue
            if column.primary_key:
                logger.warning("旧库 %s 缺少主键列 %s，无法增量迁移",
                               table.name, column.name)
                continue
            ddl_type = column.type.compile(dialect=conn.dialect)
            table_q, col_q = f'"{table.name}"', f'"{column.name}"'
            default = _column_default_sql(column) if not column.nullable else ""
            # sqlite 不允许 ADD COLUMN 带非常量默认值（如 datetime(...)）：
            # 这类列先按可空补上，再把存量行回填成同一条表达式。
            expr_default = default.startswith("(")
            stmt = f"ALTER TABLE {table_q} ADD COLUMN {col_q} {ddl_type}"
            if default and not expr_default:
                stmt += f" NOT NULL DEFAULT {default}"
            conn.execute(text(stmt))
            if default and expr_default:
                conn.execute(text(
                    f"UPDATE {table_q} SET {col_q} = {default[1:-1]} "
                    f"WHERE {col_q} IS NULL"))
            added.append(f"{table.name}.{column.name}")
    if added:
        logger.info("旧库增量迁移：补列 %s", ", ".join(added))
    return added


def _migrate_legacy_queue(conn) -> None:
    """旧队列口径 priority（0/1 插队）→ queue_pos（整数序号）。

    旧库 priority=1 表示「插队」：迁成 queue_pos=-1，仍排在最前；其余保持 0，
    按 created_at 升序 = FIFO。只在两列同时存在时执行，幂等。
    """
    cols = {str(row[1]) for row in conn.execute(
        text('PRAGMA table_info("conversation_runs")'))}
    if not {"priority", "queue_pos"}.issubset(cols):
        return
    conn.execute(text(
        "UPDATE conversation_runs SET queue_pos = -1 "
        "WHERE priority = 1 AND queue_pos = 0"))
    conn.execute(text(
        "UPDATE conversation_runs SET queue_pos = 0 "
        "WHERE priority = 0 AND queue_pos = -1"))


def _schema_version_on_disk(path: str) -> int:
    """读取库文件里记录的 schema 版本；没有库 / 没有标记都返回 0。"""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return 0
    try:
        con = sqlite3.connect(path)
        try:
            row = con.execute(
                "SELECT version FROM schema_meta WHERE id = 1").fetchone()
        except sqlite3.Error:
            row = None
        finally:
            con.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _archive_legacy_database() -> bool:
    """V4 不再兼容旧数据：把旧库整体改名归档，随后建空库。

    旧库里的会话 / 角色 / 群组 / 设置都不再迁移。归档文件名带时间戳，不删除，
    用户仍可从 `data/agent_zero/agent_zero.db.legacy-*.bak` 找回。
    """
    path = get_db_path()
    if not os.path.exists(path):
        return False
    if _schema_version_on_disk(path) == SCHEMA_VERSION:
        return False
    if os.path.getsize(path) == 0:
        try:
            os.unlink(path)
        except OSError:
            pass
        return True
    stamp = time.strftime("%Y%m%d-%H%M%S")
    archived = f"{path}.legacy-{stamp}.bak"
    try:
        os.replace(path, archived)
        for suffix in ("-wal", "-shm"):
            side = path + suffix
            if os.path.exists(side):
                os.replace(side, archived + suffix)
        logger.warning("旧数据不再兼容：已归档到 %s，将按 v%d 结构新建空库",
                       archived, SCHEMA_VERSION)
        return True
    except Exception:
        logger.warning("旧库归档失败，按空库启动", exc_info=True)
        return False


def init_database() -> None:
    """按 v3 基线建表（幂等），补旧库缺失列，并做版本守卫。"""
    from services.agent_zero.models.zero import (  # noqa: F401
        Workspace, Role, Conversation, ConversationRun, ConversationRunEvent,
        GdagNode, GdagEdge, AuditLog, AppSetting, AgentTeam,
        TodoItem, Goal,
        ENSURE_INDEXES,
    )

    global _engine
    if _archive_legacy_database() and _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
        _engine = None
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        added = _migrate_additive(conn)
        # 旧库补上未读水位时，用最后活动时间回填：历史会话不该在升级后集体变未读。
        if "conversations.last_read_at" in added:
            conn.execute(text(
                "UPDATE conversations SET last_read_at = updated_at "
                "WHERE last_read_at IS NULL"))
        _migrate_legacy_queue(conn)
        for stmt in ENSURE_INDEXES:
            conn.execute(text(stmt))
        version = _guard_old_database(conn)
        logger.info("agent_zero 数据库就绪（schema v%s）", version)


def get_db_schema() -> str:
    """sqlite 没有 schema 概念：保留这个钩子只为兼容既有调用方。"""
    return ""


def get_schema_version() -> int:
    """当前数据库结构版本（v3 基线）。"""
    return SCHEMA_VERSION


# schema_meta 只记录「这份数据是按哪个结构建的」，不是业务表，因此不走 ORM。
_SCHEMA_META_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_meta ("
    "id INTEGER PRIMARY KEY, checksum TEXT NOT NULL DEFAULT '', "
    "version INTEGER NOT NULL DEFAULT 0, "
    "updated_at DATETIME NOT NULL DEFAULT (datetime('now','localtime')))"
)


def _model_checksum() -> str:
    """按 ORM 表结构算一个稳定 checksum（表名 + 列名 + 列类型）。"""
    import hashlib

    rows = []
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        cols = ",".join(f"{c.name}:{c.type}" for c in table.columns)
        rows.append(f"{table.name}({cols})")
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:32]


def _meta_columns(conn) -> list:
    try:
        return [str(r[1]) for r in conn.execute(text("PRAGMA table_info(schema_meta)"))]
    except Exception:
        return []


def _guard_old_database(conn) -> int:
    """版本守卫：只做标记与备份，绝不删除 / 替换用户数据。

    数据库文件（含历史 v1 结构）可能已经有大量对话与设置，重新建库等于丢数据，
    所以这里只保证两点：
      1. `schema_meta` 存在，且带有当前 ORM 结构的 checksum / version；
      2. 结构对不上时**先**把库文件复制一份 `agent_zero.db.v2.bak` 再继续跑，
         由上层日志提示用户。
    """
    db_path = get_db_path()
    existed = os.path.exists(db_path)
    conn.execute(text(_SCHEMA_META_DDL))
    cols = _meta_columns(conn)
    if "checksum" not in cols:
        # 历史 v1 只有 id/version：补列，标记为当前结构。
        conn.execute(text(
            "ALTER TABLE schema_meta ADD COLUMN checksum TEXT NOT NULL DEFAULT ''"))
    checksum = _model_checksum()
    row = conn.execute(text(
        "SELECT checksum, version FROM schema_meta WHERE id = 1")).first()
    if row is None:
        conn.execute(
            text("INSERT INTO schema_meta (id, checksum, version, updated_at) "
                 "VALUES (1, :c, :v, datetime('now','localtime'))"),
            {"c": checksum, "v": SCHEMA_VERSION})
        return SCHEMA_VERSION
    if str(row[0] or "") == checksum and int(row[1] or 0) == SCHEMA_VERSION:
        return SCHEMA_VERSION
    if existed:
        backup = db_path + ".v2.bak"
        try:
            if not os.path.exists(backup):
                shutil.copy2(db_path, backup)
            logger.warning(
                "数据库结构标记与当前版本不一致（原 v%s），已备份到 %s 后按现有数据继续运行",
                row[1], backup)
        except Exception:
            logger.warning("数据库结构标记不一致，且备份失败", exc_info=True)
    conn.execute(
        text("UPDATE schema_meta SET checksum = :c, version = :v, "
             "updated_at = datetime('now','localtime') WHERE id = 1"),
        {"c": checksum, "v": SCHEMA_VERSION})
    return SCHEMA_VERSION

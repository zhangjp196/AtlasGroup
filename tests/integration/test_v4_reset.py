"""V4：旧库不再迁移，启动时归档并按空库重建。"""
from __future__ import annotations

import glob
import os
import sqlite3

from services.agent_zero import database


def test_legacy_database_is_archived_and_rebuilt(app_env):
    database.init_database()
    path = database.get_db_path()
    database._engine.dispose() if database._engine else None
    database._engine = None

    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS schema_meta (id INTEGER PRIMARY KEY, version INTEGER)")
    con.execute("DELETE FROM schema_meta")
    con.execute("INSERT INTO schema_meta (id, version) VALUES (1, 3)")
    con.execute("CREATE TABLE IF NOT EXISTS legacy_marker (id TEXT)")
    con.execute("INSERT INTO legacy_marker (id) VALUES ('old')")
    con.commit()
    con.close()

    database.init_database()

    backups = glob.glob(path + ".legacy-*.bak")
    assert backups, "旧库应被改名归档"
    assert os.path.exists(path)
    con = sqlite3.connect(path)
    assert con.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()[0] == 4
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "legacy_marker" not in tables

"""落库韧性：锁冲突短暂重试；首次启动没有数据库时不打错误堆栈。"""
from __future__ import annotations

import pytest

from services.agent_zero import database


def test_retry_on_locked_retries_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("database is locked")
        return "ok"

    assert database.retry_on_locked(fn, attempts=4, delay=0) == "ok"
    assert calls["n"] == 3


def test_retry_on_locked_does_not_retry_other_errors():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        database.retry_on_locked(fn, delay=0)
    assert calls["n"] == 1


def test_retry_on_locked_gives_up_after_attempts():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RuntimeError("database table is locked")

    with pytest.raises(RuntimeError):
        database.retry_on_locked(fn, attempts=3, delay=0)
    assert calls["n"] == 3


def test_first_boot_without_db_skips_backup(tmp_path, monkeypatch):
    from services.agent_zero.services import backup_service

    monkeypatch.setattr(backup_service, "_db_path", lambda: tmp_path / "missing.db")
    monkeypatch.setattr(backup_service, "latest_backup", lambda: None)
    assert backup_service.ensure_daily_backup() is None


def test_prune_history_keeps_running_and_recent(client):
    from services.agent_zero.task_layer import run_service

    def conv():
        return client.post("/api/agent-zero/conversations", json={}).json()["data"]

    c_old, c_new, c_run = conv(), conv(), conv()
    old = run_service.create_run(c_old["id"], "u")
    new = run_service.create_run(c_new["id"], "u")
    running = run_service.create_run(c_run["id"], "u")

    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    with get_session_local()() as db:
        db.execute(text(f"UPDATE {_q('conversation_runs')} SET status='done', "
                        f"finished_at=datetime('now','localtime','-10 days'), "
                        f"updated_at=datetime('now','localtime','-10 days') WHERE id=:id"),
                   {"id": old["id"]})
        db.execute(text(f"UPDATE {_q('conversation_runs')} SET status='done', "
                        f"finished_at=datetime('now','localtime') WHERE id=:id"),
                   {"id": new["id"]})
        db.execute(text(f"UPDATE {_q('conversation_runs')} SET status='running' WHERE id=:id"),
                   {"id": running["id"]})
        db.commit()

    removed = run_service.prune_history(days=7)
    assert removed >= 1
    assert run_service.get_run(old["id"]) is None
    assert run_service.get_run(new["id"]) is not None
    assert run_service.get_run(running["id"]) is not None

"""会话未读：打开会话时清红点，新助手消息到达后重新变未读。"""
from __future__ import annotations

import json

API = "/api/agent-zero"


def _conversation(client) -> str:
    return client.post(f"{API}/conversations", json={}).json()["data"]["id"]


def _insert_assistant(conv_id: str, content: str = "回复") -> None:
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('gdag_nodes')} (id, type, camp, props) "
                 f"VALUES (:id, 'fact', 'default', :props)"),
            {"id": f"node-{conv_id}", "props": json.dumps({
                "conversation_id": conv_id, "by": "assistant",
                "stage": "exploration", "content": content,
            }, ensure_ascii=False)},
        )
        db.commit()


def _unread(client, conv_id: str) -> bool:
    items = client.get(f"{API}/conversations").json()["data"]["items"]
    row = next(i for i in items if i["id"] == conv_id)
    return bool(row.get("unread"))


def test_new_conversation_is_read_then_unread_after_reply(client):
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    cid = _conversation(client)
    assert _unread(client, cid) is False

    # 把已读水位放到过去，模拟「用户离开后 Agent 才回复」
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(text(f"UPDATE {_q('conversations')} "
                        f"SET last_read_at = '2000-01-01 00:00:00' WHERE id = :id"),
                   {"id": cid})
        db.commit()
    _insert_assistant(cid)
    assert _unread(client, cid) is True

    res = client.post(f"{API}/conversations/{cid}/read")
    assert res.status_code == 200, res.text
    assert _unread(client, cid) is False


def test_mark_read_unknown_conversation_404(client):
    assert client.post(f"{API}/conversations/conv-missing/read").status_code == 404


def test_legacy_db_backfills_read_watermark(client):
    """旧库补 last_read_at 时用 updated_at 回填：升级后历史会话不该集体变未读。"""
    import sqlite3

    from services.agent_zero import database

    cid = _conversation(client)
    _insert_assistant(cid)
    db_path = database.get_db_path()
    con = sqlite3.connect(db_path)
    con.execute("ALTER TABLE conversations DROP COLUMN last_read_at")
    con.commit()
    con.close()

    database._engine = None
    database.init_database()

    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(conversations)")]
    row = con.execute("SELECT last_read_at FROM conversations WHERE id = ?",
                      (cid,)).fetchone()
    con.close()
    assert "last_read_at" in cols
    assert row and row[0]
    assert _unread(client, cid) is False

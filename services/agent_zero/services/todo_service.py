"""任务清单（todo）：对话级的待办列表，模型与用户维护同一份。

口径对齐 DSH 的 `todo_write`：

- 工具一次传入**完整清单**（整份覆盖），不是逐条增删；
- 状态只有三种：`pending`（待处理）/ `in_progress`（进行中）/ `completed`（已完成）；
- 同一时刻最多一条 `in_progress`（多余的在写入时降级为 `pending`）；
- 传空列表 = 清空。

存储层沿用 `todo_items`（列的取值是 open / doing / done）：
本模块在边界处做一次映射，对外的 API / 事件 / 工具参数统一用 DSH 词表，
这样前端、工具、历史数据不会出现两套状态名。任务清单是进度唯一的载体，
不再有「执行计划流程图」。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores.zero_store import _q, new_id

logger = logging.getLogger(__name__)

# 对外状态（DSH 词表）←→ 存储状态
DB_BY_STATUS = {"pending": "open", "in_progress": "doing", "completed": "done"}
STATUS_BY_DB = {v: k for k, v in DB_BY_STATUS.items()}
PUBLIC_STATUSES = tuple(DB_BY_STATUS.keys())

_STATUS_ALIASES = {
    "pending": "pending", "todo": "pending", "open": "pending", "backlog": "pending",
    "in_progress": "in_progress", "in-progress": "in_progress", "doing": "in_progress",
    "active": "in_progress", "running": "in_progress", "working": "in_progress",
    "completed": "completed", "complete": "completed", "done": "completed",
    "closed": "completed", "finished": "completed",
}


def normalize_status(value: Any, default: str = "pending") -> str:
    return _STATUS_ALIASES.get(str(value or "").strip().lower(), default)


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    """数据库行 → 对外条目（status 用 DSH 词表，title 对外叫 content）。"""
    content = str(row.get("title") or "")
    return {
        "id": row.get("id"),
        "conversation_id": row.get("conversation_id"),
        "content": content,
        "title": content,          # 兼容旧前端 / 旧调用方
        "status": STATUS_BY_DB.get(str(row.get("status") or "open"), "pending"),
        "source": row.get("source") or "user",
        "note": row.get("note"),
        "created_at": str(row["created_at"]) if row.get("created_at") is not None else "",
        "updated_at": str(row["updated_at"]) if row.get("updated_at") is not None else "",
    }


def counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"pending": 0, "in_progress": 0, "completed": 0, "total": len(items)}
    for item in items:
        status = normalize_status(item.get("status"))
        out[status] = out.get(status, 0) + 1
    return out


def list_todos(conversation_id: str) -> List[Dict[str, Any]]:
    """按清单顺序返回（模型写入的顺序；用户新增的排在末尾）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('todo_items')} WHERE conversation_id = :cid "
                 f"ORDER BY position ASC, created_at ASC, id ASC"),
            {"cid": conversation_id},
        ).mappings().all()
    return [_public(dict(r)) for r in rows]


def _insert(db, conversation_id: str, content: str, status: str,
            source: str = "agent", note: Optional[str] = None,
            position: int = 0) -> str:
    todo_id = new_id("todo")
    db.execute(
        text(f"INSERT INTO {_q('todo_items')} "
             f"(id, conversation_id, title, status, source, note, position) "
             f"VALUES (:id, :cid, :title, :status, :src, :note, :pos)"),
        {"id": todo_id, "cid": conversation_id, "title": content[:500],
         "status": DB_BY_STATUS.get(normalize_status(status), "open"),
         "src": source if source in ("user", "agent") else "user",
         "note": (note or "").strip()[:2000] or None,
         "pos": int(position)},
    )
    return todo_id


def add_todo(conversation_id: str, content: str, source: str = "user",
             note: Optional[str] = None) -> Dict[str, Any]:
    content = (content or "").strip()
    if not content:
        raise ValueError("任务内容为空")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT COALESCE(MAX(position), -1) FROM {_q('todo_items')} "
                 f"WHERE conversation_id = :cid"), {"cid": conversation_id},
        ).first()
        position = int(row[0] or 0) + 1 if row else 0
        todo_id = _insert(db, conversation_id, content, "pending", source, note,
                          position=position)
        db.commit()
        row = db.execute(
            text(f"SELECT * FROM {_q('todo_items')} WHERE id = :id"), {"id": todo_id},
        ).mappings().first()
    return _public(dict(row)) if row else {"id": todo_id, "content": content,
                                           "status": "pending"}


def update_todo(todo_id: str, conversation_id: str, *,
                content: Optional[str] = None,
                status: Optional[str] = None,
                note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    sets: List[str] = []
    params: Dict[str, Any] = {"id": todo_id, "cid": conversation_id}
    if content is not None and str(content).strip():
        sets.append("title = :title")
        params["title"] = str(content).strip()[:500]
    if status is not None and str(status).strip():
        sets.append("status = :status")
        params["status"] = DB_BY_STATUS[normalize_status(status)]
    if note is not None:
        sets.append("note = :note")
        params["note"] = str(note).strip()[:2000] or None
    if not sets:
        return None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('todo_items')} SET {', '.join(sets)}, "
                 f"updated_at = datetime('now','localtime') "
                 f"WHERE id = :id AND conversation_id = :cid"),
            params,
        )
        db.commit()
        row = db.execute(
            text(f"SELECT * FROM {_q('todo_items')} WHERE id = :id AND conversation_id = :cid"),
            {"id": todo_id, "cid": conversation_id},
        ).mappings().first()
    return _public(dict(row)) if row else None


def delete_todo(todo_id: str, conversation_id: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"DELETE FROM {_q('todo_items')} "
                 f"WHERE id = :id AND conversation_id = :cid"),
            {"id": todo_id, "cid": conversation_id},
        )
        db.commit()
        return bool(res.rowcount)


def clear_todos(conversation_id: str, status: Optional[str] = None) -> int:
    """批量清理：status 为空清空全部，否则只删该状态（如只清 completed）。"""
    params: Dict[str, Any] = {"cid": conversation_id}
    where = "conversation_id = :cid"
    if status:
        where += " AND status = :st"
        params["st"] = DB_BY_STATUS[normalize_status(status)]
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(text(f"DELETE FROM {_q('todo_items')} WHERE {where}"), params)
        db.commit()
        return int(res.rowcount or 0)


def _parse_items(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """工具入参 → [{content, status, note}]；非法结构返回 None。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if raw is None or not isinstance(raw, (list, tuple)):
        return None
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            content = str(item.get("content") or item.get("title") or "").strip()
            status = item.get("status")
            note = item.get("note")
        else:
            content, status, note = str(item or "").strip(), None, None
        if not content:
            continue
        out.append({"content": content[:500],
                    "status": normalize_status(status),
                    "note": (str(note).strip()[:2000] if note else None)})
    return out


def replace_todos(conversation_id: str, items: List[Dict[str, Any]],
                  source: str = "agent") -> List[Dict[str, Any]]:
    """整份覆盖：按内容匹配已有条目，保留 id 与创建时间；缺失的删除，新出现的插入。

    「整份覆盖」是 DSH todo_write 的语义：模型只描述**期望的完整清单**，
    由我们做增量对齐，避免每次重写把 id 全部换掉（会让界面闪烁、选中状态丢失）。
    """
    # 最多一条 in_progress：保留第一条，其余降级为 pending。
    seen_live = False
    normalized: List[Dict[str, Any]] = []
    for item in items:
        it = dict(item)
        if it["status"] == "in_progress":
            if seen_live:
                it["status"] = "pending"
            seen_live = True
        normalized.append(it)

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        existing = db.execute(
            text(f"SELECT * FROM {_q('todo_items')} WHERE conversation_id = :cid"),
            {"cid": conversation_id},
        ).mappings().all()
        pool: Dict[str, List[Dict[str, Any]]] = {}
        for row in existing:
            pool.setdefault(str(row["title"] or ""), []).append(dict(row))

        for index, item in enumerate(normalized):
            matches = pool.get(item["content"]) or []
            if matches:
                row = matches.pop(0)
                db.execute(
                    text(f"UPDATE {_q('todo_items')} SET status = :st, "
                         f"note = COALESCE(:note, note), position = :pos, "
                         f"updated_at = datetime('now','localtime') "
                         f"WHERE id = :id"),
                    {"st": DB_BY_STATUS[item["status"]], "note": item.get("note"),
                     "pos": index, "id": row["id"]},
                )
            else:
                _insert(db, conversation_id, item["content"], item["status"],
                        source, item.get("note"), position=index)
        # 不在新清单里的条目删掉（整份覆盖语义）
        for rows in pool.values():
            for row in rows:
                db.execute(text(f"DELETE FROM {_q('todo_items')} WHERE id = :id"),
                           {"id": row["id"]})
        db.commit()
    return list_todos(conversation_id)


def handle_tool(conversation_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """`todo_write` 工具入口：整份覆盖；同时兼容旧的 action 调用。

    新语义（推荐）：`{"todos": [{"content": "...", "status": "pending"}]}`
    旧语义（历史消息 / 旧提示词）：action=add/list/update/complete/delete/clear
    """
    try:
        if "todos" in args:
            items = _parse_items(args.get("todos"))
            if items is None:
                return {"error": "todos 必须是数组："
                                 '[{"content": "...", "status": "pending"}]'}
            items = replace_todos(conversation_id, items)
            return {"ok": True, "items": items, "counts": counts(items)}
        # ── 旧接口兼容 ───────────────────────────────────────────────
        action = str(args.get("action") or "").strip().lower()
        if action in ("", "list"):
            items = list_todos(conversation_id)
            return {"ok": True, "items": items, "counts": counts(items)}
        if action == "add":
            entries: List[str] = []
            raw = args.get("items")
            if isinstance(raw, (list, tuple)):
                entries = [str(it.get("title") if isinstance(it, dict) else it or "").strip()
                           for it in raw]
            elif args.get("title"):
                entries = [str(args.get("title") or "")]
            entries = [x for x in entries if x]
            if not entries:
                return {"error": "任务内容为空"}
            current = list_todos(conversation_id)
            payload = [{"content": x["content"], "status": x["status"]} for x in current]
            payload += [{"content": x, "status": "pending"} for x in entries]
            items = replace_todos(conversation_id, payload)
            return {"ok": True, "items": items, "counts": counts(items)}
        if action == "clear":
            clear_todos(conversation_id, args.get("status"))
            items = list_todos(conversation_id)
            return {"ok": True, "items": items, "counts": counts(items)}
        todo_id = str(args.get("id") or "").strip()
        if action in ("update", "complete", "delete"):
            if not todo_id:
                return {"error": "需要 id（先 list 查看任务 id）"}
            if action == "delete":
                delete_todo(todo_id, conversation_id)
            else:
                update_todo(todo_id, conversation_id,
                            content=args.get("title"),
                            status=("completed" if action == "complete"
                                    else args.get("status")),
                            note=args.get("note"))
            items = list_todos(conversation_id)
            return {"ok": True, "items": items, "counts": counts(items)}
        return {"error": f"非法 action: {action}"}
    except ValueError as e:
        return {"error": str(e)}

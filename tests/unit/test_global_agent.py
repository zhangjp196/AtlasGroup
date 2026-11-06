"""全局 Agent 生命周期：唯一会话、自动归档豁免、注意力口径。"""
from __future__ import annotations

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation


def _init(app_env):
    from services.agent_zero import database

    database.init_database()
    zero_store.ensure_defaults()
    return zero_store.DEFAULT_WORKSPACE_ID


def _set_archived(conv_id: str, archived: int) -> None:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text("UPDATE conversations SET archived = :a WHERE id = :id"),
            {"a": archived, "id": conv_id},
        )
        db.commit()


def test_global_agent_conversation_is_singleton(app_env):
    """重复打开只返回同一个全局 Agent；历史重复会话被归档而不是删除。"""
    ws = _init(app_env)

    first = conversation.ensure_global_agent_conversation()
    second = conversation.ensure_global_agent_conversation()
    assert first["id"] == second["id"]
    assert first["zone"] == conversation.GLOBAL_AGENT_ZONE
    assert first["role_id"] == zero_store.GLOBAL_ROLE_ID

    duplicate = conversation.create_conversation(
        ws, "local", zone=conversation.GLOBAL_AGENT_ZONE)
    canonical = conversation.ensure_global_agent_conversation()
    # 保留其中一条作为入口，其余活跃的全局 Agent 会话归档（不删除历史）。
    assert canonical["id"] in (first["id"], duplicate["id"])
    archived_id = duplicate["id"] if canonical["id"] == first["id"] else first["id"]
    assert conversation.get_conversation(archived_id)["archived"] == 1
    assert conversation.get_conversation(archived_id) is not None
    # 再打开保持稳定，不会来回切换。
    assert conversation.ensure_global_agent_conversation()["id"] == canonical["id"]


def test_global_agent_conversation_is_restored_when_archived(app_env):
    _init(app_env)
    conv = conversation.ensure_global_agent_conversation()
    _set_archived(conv["id"], 1)

    restored = conversation.ensure_global_agent_conversation()
    assert restored["id"] == conv["id"]
    assert restored["archived"] == 0


def test_archive_sweep_skips_global_agent(app_env, monkeypatch):
    """全局 Agent 是 App 入口，不能被 30 天无活动自动归档。"""
    ws = _init(app_env)
    global_conv = conversation.ensure_global_agent_conversation()
    normal = conversation.create_conversation(ws, "local")

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text("UPDATE conversations SET updated_at = "
                 "datetime('now','localtime','-60 days') WHERE id IN (:a, :b)"),
            {"a": global_conv["id"], "b": normal["id"]},
        )
        db.commit()

    monkeypatch.setattr(conversation, "_archive_sweep_at", 0.0)
    assert conversation.archive_stale_conversations() >= 1
    assert conversation.get_conversation(global_conv["id"])["archived"] == 0
    assert conversation.get_conversation(normal["id"])["archived"] == 1


def test_attention_running_excludes_global_agent(app_env):
    """全局 Agent 自己的 run 不进入注意力队列（避免全局完成提示 / 计数抖动）。"""
    from services.agent_zero.services import attention_service
    from services.agent_zero.task_layer import run_service

    ws = _init(app_env)
    global_conv = conversation.ensure_global_agent_conversation()
    normal = conversation.create_conversation(ws, "local")
    run_service.create_run(global_conv["id"], "local", content="全局 Agent 在工作")
    run_service.create_run(normal["id"], "local", content="普通会话在工作")

    running = attention_service.snapshot()["running"]
    ids = {str(item.get("conversation_id") or "") for item in running}
    assert global_conv["id"] not in ids
    assert normal["id"] in ids

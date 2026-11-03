"""压缩检查点跨轮保留。

- 压缩产物写成 GDAG 节点（kind=checkpoint），下一轮 `_history_messages` 把它
  放到 system 之后（第一条 user），压缩成果不再只在当轮有效；
- 它是「上下文的一部分」，不是聊天消息：转录 / 消息计数 / 会话预览 / 搜索都过滤；
- 只保留最新一条；对话被截断（编辑 / 撤回）后清空，避免摘要覆盖已删除内容。
"""
from __future__ import annotations

import time

import pytest

from services.agent_zero.task_layer import conversation
from services.agent_zero.stores import zero_store
from services.agent_zero.runtime.zero.gdag.store import GdagStore

API = "/api/agent-zero"
SUMMARY = "## 目标\n- 把迁移脚本收尾\n\n## 下一步\n1. 跑一遍回归"


def _mk_message(conv: dict, content: str, role: str = "user") -> str:
    node_id = zero_store.new_id("msg")
    GdagStore().upsert_node({
        "id": node_id, "type": "fact", "camp": "default",
        "props": {
            "conversation_id": conv["id"],
            "workspace_id": conv["workspace_id"],
            "content": content,
            "stage": "exploration",
            "by": role,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "confidence": 0.5,
    })
    return node_id


@pytest.fixture()
def conv(client):
    data = client.post(f"{API}/conversations", json={}).json()["data"]
    return conversation.get_conversation(data["id"])


def test_checkpoint_is_injected_into_next_turn_history(conv):
    msg_id = _mk_message(conv, "上一轮的问题")
    _mk_message(conv, "上一轮的答复", role="assistant")
    _mk_message(conv, "最新问题")
    node_id = conversation.persist_checkpoint(conv, SUMMARY, source="llm")
    assert node_id

    history = conversation._history_messages(conv["id"])
    assert history[0]["role"] == "user"
    assert history[0]["content"].startswith("<conversation-checkpoint>")
    assert "把迁移脚本收尾" in history[0]["content"]
    # 原文仍在后面（检查点旁保留近期原文）
    assert any("上一轮的问题" in str(m.get("content") or "") for m in history[1:])

    # 注入的历史能被压缩层识别为「既有检查点」（增量更新）
    from services.agent_zero.runtime.zero import compress

    assert compress.checkpoint_summary(history[0]) == SUMMARY
    split = compress.split_conversation(
        [{"role": "system", "content": "s"}, *history], keep=1)
    assert split is not None
    assert split[3] and "把迁移脚本收尾" in split[3]

    # 聊天消息相关的地方都看不到它
    listed = conversation.list_messages(conv["id"])
    assert all("<conversation-checkpoint>" not in str(m.get("content") or "")
               for m in listed)
    assert any(str(m.get("id")) == msg_id for m in listed)
    from services.agent_zero.database import get_session_local

    with get_session_local()() as db:
        # 列表预览取最后一条真实消息，而不是检查点
        assert conversation._last_message(db, conv["id"]) == "最新问题"
        assert conversation._count_messages(db, conv["id"]) == 3


def test_only_latest_checkpoint_is_kept(conv):
    first = conversation.persist_checkpoint(conv, SUMMARY, source="llm")
    time.sleep(0.01)
    second = conversation.persist_checkpoint(conv, "## 目标\n- 第二轮摘要", source="llm")
    assert first != second

    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    with get_session_local()() as db:
        ids = [str(r[0]) for r in db.execute(
            text(f"SELECT id FROM {_q('gdag_nodes')} "
                 f"WHERE props->>'conversation_id' = :cid "
                 f"AND props->>'kind' = 'checkpoint'"),
            {"cid": conv["id"]},
        ).fetchall()]
    assert ids == [second]
    assert conversation.latest_checkpoint(conv["id"])["summary"].startswith("## 目标\n- 第二轮")


def test_prune_result_does_not_write_checkpoint(conv):
    conversation.persist_compaction_result(
        conv, {"source": "prune", "summary": "", "pruned_tool_outputs": 3})
    assert conversation.latest_checkpoint(conv["id"]) is None


def test_truncation_clears_checkpoint(conv):
    msg_id = _mk_message(conv, "会被撤回的问题")
    conversation.persist_checkpoint(conv, SUMMARY, source="heuristic")
    assert conversation.latest_checkpoint(conv["id"])

    removed = conversation.truncate_messages(conv["id"], msg_id, include_self=True)
    assert removed >= 1
    assert conversation.latest_checkpoint(conv["id"]) is None

"""会话视觉能力：上传图片的校验口径要和真正跑对话时一致。"""
from __future__ import annotations

from services.agent_zero.task_layer import conversation


def _stub_role(monkeypatch, envelope_models=None):
    role = {"id": "role-1", "envelope": {"models": envelope_models or {}}}
    monkeypatch.setattr(conversation.zero_store, "get_role",
                        lambda _rid: role)


def test_chat_vision_uses_conversation_model(monkeypatch):
    _stub_role(monkeypatch)
    seen = []

    def fake_supports(code):
        seen.append(code)
        return code == "vision-model"

    monkeypatch.setattr(
        "services.agent_zero.runtime.zero.llm.model_supports_vision", fake_supports)
    monkeypatch.setattr(
        "services.agent_zero.runtime.zero.llm.get_active_model_code",
        lambda: "active-model")

    assert conversation.chat_vision_for({"role_id": "role-1",
                                         "model_code": "vision-model"}) is True
    assert seen == ["vision-model"]

    seen.clear()
    assert conversation.chat_vision_for({"role_id": "role-1",
                                         "model_code": "text-model"}) is False
    assert seen == ["text-model"]


def test_chat_vision_falls_back_to_active_model(monkeypatch):
    _stub_role(monkeypatch)
    seen = []
    monkeypatch.setattr(
        "services.agent_zero.runtime.zero.llm.model_supports_vision",
        lambda code: seen.append(code) or True)
    monkeypatch.setattr(
        "services.agent_zero.runtime.zero.llm.get_active_model_code",
        lambda: "active-model")

    assert conversation.chat_vision_for({"role_id": "role-1"}) is True
    assert seen == ["active-model"]


def test_chat_vision_respects_role_fixed_model(monkeypatch):
    """角色固定了 loop 模型时，会话里换模型不会改变本轮实际模型。"""
    _stub_role(monkeypatch, {"loop": "role-model"})
    seen = []
    monkeypatch.setattr(
        "services.agent_zero.runtime.zero.llm.model_supports_vision",
        lambda code: seen.append(code) or False)
    monkeypatch.setattr(
        "services.agent_zero.runtime.zero.llm.get_active_model_code",
        lambda: "active-model")

    assert conversation.chat_vision_for(
        {"role_id": "role-1", "model_code": "conv-model"}) is False
    assert seen == ["role-model"]


def test_attachment_only_message_enters_history(app_env):
    """只发图片（正文为空）也要成立，并在历史里保留图片标记。"""
    from services.agent_zero import database
    from services.agent_zero.stores import zero_store

    database.init_database()
    zero_store.ensure_defaults()
    conv = conversation.create_conversation(zero_store.DEFAULT_WORKSPACE_ID, "local")
    att = {"id": "att-0123456789abcdef", "name": "shot.png",
           "mime": "image/png", "size": 10, "kind": "image"}
    msg = conversation.send_message(conv["id"], "", "local", attachments=[att])
    assert msg["id"]

    history = conversation._history_messages(conv["id"])
    assert history
    assert "shot.png" in str(history[-1]["content"])

    try:
        conversation.send_message(conv["id"], "", "local")
    except ValueError as e:
        assert "消息为空" in str(e)
    else:  # pragma: no cover
        raise AssertionError("空消息应被拒绝")


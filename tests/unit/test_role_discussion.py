"""角色直接讨论：参与者解析与提示词组装。"""
from __future__ import annotations


ROLES = [
    {"id": "r1", "name": "架构师", "title": "架构"},
    {"id": "r2", "name": "测试", "title": "QA"},
    {"id": "r3", "name": "安全", "title": "Security"},
    {"id": "r4", "name": "产品", "title": "PM"},
]


def test_resolve_roles_by_id_and_name(monkeypatch):
    from services.agent_zero.stores import zero_store
    from services.agent_zero.task_layer import conversation

    monkeypatch.setattr(zero_store, "list_roles", lambda limit=500: list(ROLES))
    out, error = conversation._resolve_discussion_roles(
        {"roles": ["架构师", "r2"]}, ROLES[0])
    assert error == ""
    assert [role["id"] for role in out] == ["r1", "r2"]


def test_resolve_roles_rejects_unknown_and_single(monkeypatch):
    from services.agent_zero.stores import zero_store
    from services.agent_zero.task_layer import conversation

    monkeypatch.setattr(zero_store, "list_roles", lambda limit=500: list(ROLES))
    _, error = conversation._resolve_discussion_roles(
        {"roles": ["r1", "不存在"]}, ROLES[0])
    assert "找不到角色" in error
    _, error = conversation._resolve_discussion_roles(
        {"roles": ["r1"]}, ROLES[0])
    assert "至少需要 2 个" in error


def test_resolve_roles_dedupes_and_caps_at_three(monkeypatch):
    from services.agent_zero.stores import zero_store
    from services.agent_zero.task_layer import conversation

    monkeypatch.setattr(zero_store, "list_roles", lambda limit=500: list(ROLES))
    out, error = conversation._resolve_discussion_roles(
        {"roles": ["r1", "r1", "r2", "r3", "r4"]}, ROLES[0])
    assert error == ""
    assert [role["id"] for role in out] == ["r1", "r2", "r3"]


def test_discussion_prompt_contains_previous_rounds_and_mode():
    from services.agent_zero.task_layer import conversation

    prompt = conversation._discussion_prompt(
        "是否引入队列", "verify", ROLES[:2],
        [{"round": 1, "messages": [{"role_name": "架构师", "output": "我建议引入"}]}],
        ROLES[1], 2, 2, "线上峰值 200 QPS")
    assert "是否引入队列" in prompt
    assert "交互验证" in prompt
    assert "架构师" in prompt
    assert "我建议引入" in prompt
    assert "第 2 轮 / 共 2 轮" in prompt
    assert "线上峰值 200 QPS" in prompt


def test_role_discussion_runs_two_rounds_and_feeds_prior_turns(monkeypatch):
    import asyncio
    import json

    from services.agent_zero.stores import zero_store
    from services.agent_zero.task_layer import conversation

    monkeypatch.setattr(zero_store, "list_roles", lambda limit=500: list(ROLES))
    calls = []

    async def fake_stream(conv, user, speaker, sink, **kwargs):
        prompt = kwargs["context_messages"][0]["content"]
        calls.append((speaker["name"], prompt))
        sink["text"] = f"{speaker['name']} 的本轮观点"
        yield {"start": True}
        yield {"done": True}

    monkeypatch.setattr(conversation, "_stream_role", fake_stream)
    monkeypatch.setattr(conversation, "_forward_subagent_event",
                        lambda *args, **kwargs: None)

    out = asyncio.run(conversation._exec_role_discussion(
        {}, "user", ROLES[0],
        {"topic": "是否引入队列", "roles": ["架构师", "测试"], "rounds": 2},
        0,
    ))
    data = json.loads(out)
    assert data["status"] == "done"
    assert data["rounds"] == 2
    assert [p["name"] for p in data["participants"]] == ["架构师", "测试"]
    assert len(calls) == 4
    # 第二轮第一个发言者的提示词里已经包含第一轮的发言。
    assert "架构师 的本轮观点" in calls[2][1]
    assert len(data["transcript"]) == 2
    assert "测试 的本轮观点" in data["output"]

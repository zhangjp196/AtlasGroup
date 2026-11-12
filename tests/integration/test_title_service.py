"""会话标题：AI 生成 / 手动重命名 / 竞态保护。"""

from __future__ import annotations

import asyncio


def _new_conversation(client):
    API = "/api/agent-zero"
    conv = client.post(f"{API}/conversations",
                       json={"title": ""})
    assert conv.status_code == 200, conv.text
    return conv.json()["data"]["id"]


def test_clean_title_takes_first_line_and_limits_length():
    from services.agent_zero.task_layer import title_service

    assert title_service.clean_title('Title: "构建失败排查"\n\n多余解释') == "构建失败排查"
    assert title_service.clean_title("  ## 修复登录超时  ") == "修复登录超时"
    long = "标" * 120
    out = title_service.clean_title(long)
    assert len(out) == 100 and out.endswith("...")


def test_auto_title_then_manual_rename_guard(client, monkeypatch):
    from services.agent_zero.task_layer import conversation, title_service

    API = "/api/agent-zero"
    cid = _new_conversation(client)
    conversation.send_message(cid, "帮我把构建失败的日志分析一下", "local")

    row = title_service.get_title(cid)
    assert row["title_source"] == "fallback"
    assert "构建失败" in row["title"]

    async def fake(prompt, conv):
        return 'Title: "构建失败排查"\nshould be ignored'

    monkeypatch.setattr(title_service, "_call_model", fake)
    assert asyncio.run(title_service.generate(cid)) == "构建失败排查"
    row = title_service.get_title(cid)
    assert row["title"] == "构建失败排查" and row["title_source"] == "auto"

    # 已经是 auto：自动生成不再重复覆盖
    assert asyncio.run(title_service.generate(cid)) is None

    # 手动重命名标记为 user
    r = client.patch(f"{API}/conversations/{cid}", json={"title": "我的标题"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["title_source"] == "user"
    # 自动生成绝不覆盖用户标题
    assert asyncio.run(title_service.generate(cid)) is None
    assert title_service.get_title(cid)["title"] == "我的标题"

    # 用户主动点「重新生成」可以覆盖
    r = client.post(f"{API}/conversations/{cid}/title/regenerate")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["generated"] is True
    assert body["title"] == "构建失败排查"
    assert title_service.get_title(cid)["title_source"] == "auto"


def test_title_rename_rejects_empty(client):
    API = "/api/agent-zero"
    cid = _new_conversation(client)
    r = client.patch(f"{API}/conversations/{cid}", json={"title": "   "})
    assert r.status_code == 422 or r.status_code == 400

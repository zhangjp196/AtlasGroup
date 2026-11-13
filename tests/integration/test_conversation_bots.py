from __future__ import annotations

import asyncio
import time

import pytest
from starlette.testclient import TestClient

from services.agent_zero.services import bot_transports, conversation_bots
from services.agent_zero.task_layer import conversation

API = "/api/agent-zero"


def _wait(check):
    for _ in range(100):
        result = check()
        if result:
            return result
        time.sleep(0.05)
    pytest.fail("等待机器人事件超时")


@pytest.mark.parametrize(("provider", "endpoint"), [
    ("feishu", "/conversations"),
    ("wecom", "/app/global-agent"),
    ("feishu", "/app/global-chat"),
])
def test_bot_binds_one_conversation_and_rebinds(app_env, monkeypatch, provider, endpoint):
    from services.agent_zero.webapp import create_app

    callbacks = {}
    replies = []

    async def transport(account, secret, receive, connected):
        callbacks.update(account=account, receive=receive, loop=asyncio.get_running_loop())
        assert secret == "sample-secret"
        connected()
        await asyncio.Event().wait()

    async def agent(conv_id, user, *, content, **kwargs):
        assert user == f"bot:{provider}"
        yield {"done": True, "text": f"Agent: {content}"}

    async def reply(text):
        replies.append(text)

    monkeypatch.setattr(conversation, "generate_reply_stream", agent)
    monkeypatch.setitem(conversation_bots.TRANSPORTS, provider, transport)
    monkeypatch.setattr(conversation_bots.secret_store, "available", lambda: False)
    app = create_app(config_yaml=str(app_env["config"]), web_dir=str(app_env["root"] / "web"),
                     logs_dir=str(app_env["tmp"] / "logs"))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 10000)) as client:
        cid = client.post(API + endpoint, json={}).json()["data"]["id"]
        other = client.post(API + "/conversations", json={}).json()["data"]["id"]
        path = API + "/bots"
        body = {"provider": provider, "bot_id": "app_test", "secret": "sample-secret"}
        assert client.post(path, json=body, headers={"Origin": "https://example.com"}).status_code == 403
        res = client.post(path, json=body)
        assert res.status_code == 200, res.text
        assert "sample-secret" not in res.text
        binding = res.json()["data"]
        assert binding["bound"] is False
        assert binding["conversation_id"] == ""
        # 机器人连接是全局的：不能重复添加。
        assert client.post(path, json=body).status_code == 409
        _wait(lambda: callbacks.get("receive"))

        def deliver(msg_id, peer, text):
            incoming = bot_transports.IncomingMessage(msg_id, peer, text, reply)
            callbacks["loop"].call_soon_threadsafe(callbacks["receive"], incoming)

        # 未绑定会话时，消息不会进入任何会话。
        deliver("ignored", "group:elsewhere", "hello")
        time.sleep(0.3)
        assert replies == []

        # 绑到会话 A：任意聊天发来的消息都进入 A。
        bound = client.post(path + f"/{binding['id']}/bind", json={"conversation_id": cid})
        assert bound.status_code == 200, bound.text
        assert bound.json()["data"]["conversation_id"] == cid
        deliver("msg", "group:target", "你好")
        deliver("msg", "group:target", "duplicate")
        _wait(lambda: len(replies) == 1)
        assert replies[0] == "Agent: 你好"
        # 普通会话：机器人绑定根后默认新建一个栏目（不是把消息倒进根会话）。
        # 全局面板没有栏目列，消息仍进原会话。
        global_panel = endpoint.startswith("/app/")
        if global_panel:
            assert [item["content"] for item in conversation.list_messages(cid)] == ["你好"]
            lanes = []
        else:
            lanes = [item["id"] for item in conversation.list_conversations(
                session_id=cid, limit=50) if item["id"] != cid]
            assert len(lanes) == 1, lanes
            assert [item["content"] for item in conversation.list_messages(lanes[0])] == ["你好"]
            assert conversation.list_messages(cid) == []

        # 绑到会话 B：A 的绑定自动解除，后续消息只进 B（普通会话默认新栏目）。
        assert client.post(path + f"/{binding['id']}/bind",
                           json={"conversation_id": other}).status_code == 200
        deliver("msg-other", "group:other", "在吗")
        _wait(lambda: len(replies) == 2)
        assert replies[1] == "Agent: 在吗"
        other_lanes = [item["id"] for item in conversation.list_conversations(
            session_id=other, limit=50) if item["id"] != other]
        assert len(other_lanes) == 1, other_lanes
        assert [item["content"] for item in conversation.list_messages(other_lanes[0])] == ["在吗"]
        if lanes:
            assert [item["content"] for item in conversation.list_messages(lanes[0])] == ["你好"]

        # 解绑后消息静默忽略。
        assert client.delete(path + f"/{binding['id']}/route").status_code == 200
        deliver("unbound", "group:other", "忽略我")
        time.sleep(0.3)
        assert len(replies) == 2

        assert client.delete(path + "/" + binding["id"]).status_code == 200
        assert client.get(path).json()["data"]["items"] == []


def test_bot_safe_commands_and_text_aliases(app_env, monkeypatch):
    """机器人指令：/new、/clear 与「新建栏目」等短句都只影响当前栏目。"""
    from services.agent_zero.webapp import create_app

    callbacks = {}
    replies = []

    async def transport(account, secret, receive, connected):
        callbacks.update(receive=receive, loop=asyncio.get_running_loop())
        connected()
        await asyncio.Event().wait()

    async def agent(conv_id, user, *, content, **kwargs):
        yield {"done": True, "text": f"Agent: {content}"}

    async def reply(text):
        replies.append(text)

    monkeypatch.setattr(conversation, "generate_reply_stream", agent)
    monkeypatch.setitem(conversation_bots.TRANSPORTS, "feishu", transport)
    monkeypatch.setattr(conversation_bots.secret_store, "available", lambda: False)
    app = create_app(config_yaml=str(app_env["config"]), web_dir=str(app_env["root"] / "web"),
                     logs_dir=str(app_env["tmp"] / "logs"))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 10000)) as client:
        cid = client.post(API + "/conversations", json={}).json()["data"]["id"]
        binding = client.post(API + "/bots", json={
            "provider": "feishu", "bot_id": "app_cmd", "secret": "sample-secret",
        }).json()["data"]
        assert client.post(API + f"/bots/{binding['id']}/bind",
                           json={"conversation_id": cid}).status_code == 200
        _wait(lambda: callbacks.get("receive"))

        def deliver(msg_id, text):
            incoming = bot_transports.IncomingMessage(msg_id, "group:cmd", text, reply)
            callbacks["loop"].call_soon_threadsafe(callbacks["receive"], incoming)

        def lanes():
            return [item["id"] for item in conversation.list_conversations(
                session_id=cid, limit=50) if item["id"] != cid]

        # /help 不建栏目，也不跑模型。
        deliver("h", "/help")
        _wait(lambda: len(replies) == 1)
        assert "可用指令" in replies[0]
        assert lanes() == []

        deliver("n", "新建栏目")
        _wait(lambda: len(replies) == 2)
        assert replies[1].startswith("已新建栏目")
        first = lanes()
        assert len(first) == 1
        assert conversation.list_messages(first[0]) == []

        # /clear 只清当前栏目上下文；历史消息仍在。
        deliver("c", "/clear")
        _wait(lambda: len(replies) == 3)
        assert "已清空" in replies[2]

        # /new 带标题再开一个栏目，后续消息落到新栏目。
        deliver("n2", "/new 方案讨论")
        _wait(lambda: len(replies) == 4)
        assert "方案讨论" in replies[3]
        opened = lanes()
        assert len(opened) == 2
        deliver("m", "你好")
        _wait(lambda: len(replies) == 5)
        assert replies[4] == "Agent: 你好"
        titles = {item["id"]: item.get("title") for item in conversation.list_conversations(
            session_id=cid, limit=50)}
        new_lane = next(item for item in opened if titles.get(item) == "方案讨论")
        assert [m["content"] for m in conversation.list_messages(new_lane)] == ["你好"]
        oldest = next(item for item in first)
        assert [m for m in conversation.list_messages(oldest) if m.get("by") != "system"] == []


def test_bot_natural_language_commands_are_applied(app_env, monkeypatch):
    """模型从自然语言里解析出的 new 指令：机器人自己执行，不依赖界面。"""
    from services.agent_zero.webapp import create_app

    callbacks = {}
    replies = []
    conv_ids = []

    async def transport(account, secret, receive, connected):
        callbacks.update(receive=receive, loop=asyncio.get_running_loop())
        connected()
        await asyncio.Event().wait()

    async def agent(conv_id, user, *, content, **kwargs):
        conv_ids.append(conv_id)
        if content == "帮我建一个专门讨论方案的地方":
            yield {"session_command": {
                "name": "new", "arg": "自动栏目", "done": False, "note": "",
            }}
        yield {"done": True, "text": "好的。"}

    async def reply(text):
        replies.append(text)

    monkeypatch.setattr(conversation, "generate_reply_stream", agent)
    monkeypatch.setitem(conversation_bots.TRANSPORTS, "feishu", transport)
    monkeypatch.setattr(conversation_bots.secret_store, "available", lambda: False)
    app = create_app(config_yaml=str(app_env["config"]), web_dir=str(app_env["root"] / "web"),
                     logs_dir=str(app_env["tmp"] / "logs"))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 10000)) as client:
        cid = client.post(API + "/conversations", json={}).json()["data"]["id"]
        binding = client.post(API + "/bots", json={
            "provider": "feishu", "bot_id": "app_nl", "secret": "sample-secret",
        }).json()["data"]
        assert client.post(API + f"/bots/{binding['id']}/bind",
                           json={"conversation_id": cid}).status_code == 200
        _wait(lambda: callbacks.get("receive"))

        def deliver(msg_id, text):
            incoming = bot_transports.IncomingMessage(msg_id, "group:nl", text, reply)
            callbacks["loop"].call_soon_threadsafe(callbacks["receive"], incoming)

        deliver("n1", "帮我建一个专门讨论方案的地方")
        _wait(lambda: len(replies) == 1)
        assert "自动栏目" in replies[0]
        lanes = [item for item in conversation.list_conversations(
            session_id=cid, limit=50) if item["id"] != cid]
        assert len(lanes) == 2, lanes
        target = next(item for item in lanes if item.get("title") == "自动栏目")

        deliver("n2", "继续")
        _wait(lambda: len(replies) == 2)
        assert len(conv_ids) == 2
        assert conv_ids[1] == target["id"]
        assert [m["content"] for m in conversation.list_messages(target["id"])] == ["继续"]


def test_provider_event_mapping_and_wecom_ack():
    received = []
    frames = []

    async def send(command, body, request_id=None, **kwargs):
        frames.append((command, body, request_id, kwargs))

    body = {"msgid": "m1", "chattype": "single", "from": {"userid": "u1"},
            "msgtype": "text", "text": {"content": " hello "}}
    bot_transports._wecom_message(body, "req", received.append, send)
    assert received[0].peer == "single:u1"
    assert received[0].text == "hello"

    async def respond():
        await received[0].acknowledge()
        await received[0].reply("answer")

    asyncio.run(respond())
    assert [frame[1]["stream"]["finish"] for frame in frames] == [False, True]
    assert [frame[2] for frame in frames] == ["req", "req"]
    assert frames[0][1]["stream"]["id"] == frames[1][1]["stream"]["id"]
    # 回复类命令不等 ack：企业微信经常不回，等 10s 会把「正在处理」挂住。
    assert all(frame[3].get("wait_ack") is False for frame in frames)

    event = {"header": {"event_type": "im.message.receive_v1"}, "event": {
        "sender": {"sender_type": "user"},
        "message": {"chat_id": "oc_chat", "chat_type": "group", "message_id": "om_msg",
                    "message_type": "text", "content": '{"text":"@_user_1 hello"}',
                    "mentions": [{"key": "@_user_1"}]},
    }}
    bot_transports._feishu_message(event, received.append, send)
    assert (received[1].id, received[1].peer, received[1].text) == ("om_msg", "oc_chat", "hello")
    event["event"]["sender"]["sender_type"] = "app"
    bot_transports._feishu_message(event, received.append, send)
    assert len(received) == 2

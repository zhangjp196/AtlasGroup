"""飞书与企业微信机器人的长连接和回复。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import websockets


@dataclass(frozen=True)
class IncomingMessage:
    id: str
    peer: str
    text: str
    reply: Callable[[str], Awaitable[None]]
    acknowledge: Callable[[], Awaitable[None]] | None = None


Receiver = Callable[[IncomingMessage], None]


async def _feishu_post(client, path, data, *, token=""):
    headers = {"Authorization": f"Bearer {token}"} if token else {"locale": "zh"}
    response = await client.post(path, json=data, headers=headers)
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        raise RuntimeError(f"飞书接口错误 ({result.get('code')})")
    return result


async def feishu(account, secret, receive: Receiver, connected) -> None:
    from services.agent_zero.services.feishu_frame import Frame

    credentials = {"app_id": account, "app_secret": secret}
    async with httpx.AsyncClient(base_url="https://open.feishu.cn", timeout=15) as client:
        endpoint = (await _feishu_post(client, "/callback/ws/endpoint",
                                      {"AppID": account, "AppSecret": secret}))["data"]
        url = urlsplit(endpoint["URL"])
        if url.scheme != "wss" or not url.hostname:
            raise RuntimeError("飞书返回了无效的连接地址")
        service = int(parse_qs(url.query)["service_id"][0])
        interval = max(5, int((endpoint.get("ClientConfig") or {}).get("PingInterval") or 120))
        token = ""
        expires = 0.0

        async def reply(message_id: str, content: str) -> None:
            nonlocal token, expires
            if time.monotonic() >= expires:
                result = await _feishu_post(client, "/open-apis/auth/v3/tenant_access_token/internal", credentials)
                token = result["tenant_access_token"]
                expires = time.monotonic() + max(1, result["expire"] - 60)
            for offset in range(0, len(content), 5000):
                await _feishu_post(client, f"/open-apis/im/v1/messages/{quote(message_id, safe='')}/reply",
                                   {"msg_type": "text", "content": json.dumps({"text": content[offset:offset + 5000]})},
                                   token=token)

        # 飞书协议自带应用层 ping；关掉 websockets 的协议级 ping，否则服务端
        # 不回 pong 时客户端会在 20s 左右主动断开，表现为「刚启动能用，过一会就断」。
        async with websockets.connect(
            url.geturl(), open_timeout=15, close_timeout=3,
            ping_interval=None, ping_timeout=None,
        ) as ws:
            connected()
            ping = Frame(SeqID=0, LogID=0, service=service, method=0)
            ping.add_header("type", "ping")
            fragments: dict[str, tuple[float, dict[int, bytes]]] = {}
            next_ping = time.monotonic() + interval
            while True:
                if time.monotonic() >= next_ping:
                    await ws.send(ping.SerializeToString())
                    next_ping = time.monotonic() + interval
                try:
                    raw = await asyncio.wait_for(ws.recv(), max(0.01, next_ping - time.monotonic()))
                except TimeoutError:
                    continue
                frame = Frame.FromString(raw)
                headers = {header.key: header.value for header in frame.headers}
                if frame.method != 1 or headers.get("type") != "event":
                    continue
                payload = frame.payload
                count = int(headers.get("sum", "1"))
                if count < 1 or count > 32:
                    continue
                if count > 1:
                    now = time.monotonic()
                    fragments = {key: entry for key, entry in fragments.items() if entry[0] > now}
                    key = headers["message_id"]
                    _, parts = fragments.setdefault(key, (now + 10, {}))
                    index = int(headers["seq"])
                    if not 0 <= index < count:
                        continue
                    parts[index] = payload
                    if len(parts) < count:
                        continue
                    del fragments[key]
                    payload = b"".join(parts[i] for i in range(count))
                try:
                    _feishu_message(json.loads(payload), receive, reply)
                    code = 200
                except (ValueError, TypeError, AttributeError, KeyError):
                    code = 500
                frame.payload = json.dumps({"code": code}).encode()
                await ws.send(frame.SerializeToString())


def _feishu_message(payload, receive, reply) -> None:
    if payload.get("header", {}).get("event_type") != "im.message.receive_v1":
        return
    event = payload["event"]
    message = event["message"]
    if event["sender"].get("sender_type") != "user" or message.get("message_type") != "text":
        return
    if message.get("chat_type") not in ("p2p", "group"):
        return
    content = json.loads(message["content"]).get("text", "")
    for mention in message.get("mentions") or ():
        key = mention.get("key")
        if key:
            content = content.replace(key, "")
    message_id = message["message_id"]

    async def send(text: str) -> None:
        await reply(message_id, text)

    receive(IncomingMessage(message_id, message["chat_id"], content.strip(), send))


async def wecom(account, secret, receive: Receiver, connected) -> None:
    # 企业微信长连接有自己的应用层 ping（25s）；协议级 ping 服务端不一定回
    # pong，开着会让 websockets 在 20s 后误判断线，导致隔一段时间就重连。
    async with websockets.connect(
        "wss://openws.work.weixin.qq.com", open_timeout=15, close_timeout=3,
        ping_interval=None, ping_timeout=None,
    ) as ws:
        pending: dict[str, asyncio.Future] = {}

        async def send(command, body=None, request_id=None, wait_ack=True):
            request_id = request_id or uuid.uuid4().hex
            frame = {"cmd": command, "headers": {"req_id": request_id}}
            if body is not None:
                frame["body"] = body
            # 回复类命令服务端可能不回 ack；等 ack 会让一条已经发出去的消息
            # 被误判为失败，甚至拖住后续心跳。发送成功即视为完成。
            if not wait_ack:
                await ws.send(json.dumps(frame, ensure_ascii=False))
                return
            ack = asyncio.get_running_loop().create_future()
            pending[request_id] = ack
            try:
                await ws.send(json.dumps(frame, ensure_ascii=False))
                response = await asyncio.wait_for(ack, 10)
                errcode = response.get("errcode")
                if errcode not in (None, 0):
                    raise RuntimeError(f"企业微信接口错误 ({errcode})")
            finally:
                pending.pop(request_id, None)

        async def listen():
            async for raw in ws:
                frame = json.loads(raw)
                request_id = frame.get("headers", {}).get("req_id")
                if frame.get("cmd") == "aibot_msg_callback" and request_id:
                    _wecom_message(frame.get("body") or {}, request_id, receive, send)
                elif frame.get("cmd") == "aibot_event_callback":
                    if (frame.get("body") or {}).get("event", {}).get("eventtype") == "disconnected_event":
                        raise RuntimeError("企业微信连接被替换（同一机器人只允许一个长连接）")
                else:
                    ack = pending.get(request_id)
                    if ack is not None and not ack.done():
                        ack.set_result(frame)
            raise RuntimeError("企业微信长连接已关闭")

        listener = asyncio.create_task(listen())
        try:
            await send("aibot_subscribe", {"bot_id": account, "secret": secret})
            connected()
            while True:
                done, _ = await asyncio.wait((listener,), timeout=25)
                if done:
                    listener.result()
                await send("ping", wait_ack=False)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)


def _wecom_message(body, request_id, receive, send) -> None:
    if body.get("msgtype") != "text" or not body.get("msgid"):
        return
    chat_type = body.get("chattype")
    if chat_type not in ("single", "group"):
        return
    peer = body.get("chatid") if chat_type == "group" else (body.get("from") or {}).get("userid")
    if not peer:
        return
    content = (body.get("text") or {}).get("content") or ""
    if not isinstance(content, str):
        return
    # 群聊回调里带 @机器人 前缀；去掉后才是用户真正输入的内容。
    if chat_type == "group" and content.startswith("@"):
        parts = content.split(maxsplit=1)
        if len(parts) == 2:
            content = parts[1]
    stream_id = uuid.uuid4().hex

    async def acknowledge() -> None:
        await send("aibot_respond_msg", {"msgtype": "stream", "stream": {
            "id": stream_id, "finish": False, "content": "正在处理…",
        }}, request_id=request_id, wait_ack=False)

    async def reply(text: str) -> None:
        clipped = text.encode()[:20_000].decode(errors="ignore")
        # 必须用同一个 stream_id 发 finish=true，否则「正在处理…」会一直挂着。
        # 流式消息 10 分钟内有效，而单轮 run 最多 9 分钟。
        try:
            await send("aibot_respond_msg", {"msgtype": "stream", "stream": {
                "id": stream_id, "finish": True, "content": clipped,
            }}, request_id=request_id, wait_ack=False)
            return
        except (RuntimeError, TimeoutError):
            pass
        await send("aibot_send_msg", {"chatid": peer,
                                    "msgtype": "markdown", "markdown": {"content": clipped}},
                   wait_ack=False)

    receive(IncomingMessage(body["msgid"], f"{chat_type}:{peer}", content.strip(), reply, acknowledge))

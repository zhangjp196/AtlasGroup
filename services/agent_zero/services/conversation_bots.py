"""机器人长连接与全局绑定。

机器人连接和它对会话的绑定都放在全局设置里：

- 一份 App ID / Secret 只建一条长连接；
- 一个机器人同一时间只绑定一个会话，换绑时旧会话自动解绑；
- 机器人收到的聊天消息都进入当前绑定的会话，回复发回原聊天。

聊天身份（peer_id）只用于展示「最近活跃的聊天」，不参与路由，因此换绑会话
不需要重新配对。
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import asdict, dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from services.agent_zero.database import get_session_local
from services.agent_zero.models.zero import BotRoute, Conversation, ConversationBot
from services.agent_zero.services import bot_commands, bot_transports
from services.agent_zero.stores import secret_store, zero_store
from services.agent_zero.task_layer import conv_worker, conversation, run_service

logger = logging.getLogger(__name__)
TRANSPORTS = {"feishu": bot_transports.feishu, "wecom": bot_transports.wecom}


@dataclass(frozen=True)
class Binding:
    id: str
    provider: str
    bot_id: str
    secret: str


@dataclass(frozen=True)
class Route:
    id: str
    bot_id: str
    conversation_id: str
    peer_id: str
    lane_id: str = ""


def _bindings() -> list[Binding]:
    statement = select(ConversationBot).order_by(ConversationBot.provider, ConversationBot.bot_id)
    with get_session_local()() as db:
        return [Binding(row.id, row.provider, row.bot_id, row.secret)
                for row in db.scalars(statement)]


def _route_for(bot_id: str) -> Route | None:
    with get_session_local()() as db:
        row = db.scalar(select(BotRoute).where(BotRoute.bot_id == bot_id))
        return (Route(row.id, row.bot_id, row.conversation_id, row.peer_id, row.lane_id or "")
                if row else None)


def _bind(bot_id: str, conversation_id: str) -> Route:
    """把机器人绑定到会话；已有绑定直接改指向（旧会话自动解绑）。"""
    with get_session_local()() as db:
        bot = db.get(ConversationBot, bot_id)
        if bot is None:
            raise ValueError("机器人不存在")
        row = db.scalar(select(BotRoute).where(BotRoute.bot_id == bot_id))
        if row is not None:
            row.conversation_id = conversation_id
            # 换绑会话后，机器人默认重新开一个栏目，不沿用旧会话的栏目。
            row.lane_id = None
            db.commit()
            return Route(row.id, row.bot_id, row.conversation_id, row.peer_id, "")
        identifier = zero_store.new_id("botr")
        try:
            db.add(BotRoute(id=identifier, bot_id=bot_id, conversation_id=conversation_id,
                            peer_id=bot.peer_id or ""))
            db.commit()
        except IntegrityError as exc:
            raise ValueError("机器人绑定失败，请重试") from exc
        return Route(identifier, bot_id, conversation_id, bot.peer_id or "", "")


def _unbind(bot_id: str) -> bool:
    with get_session_local()() as db:
        result = db.execute(delete(BotRoute).where(BotRoute.bot_id == bot_id))
        db.commit()
        return bool(result.rowcount)


def _touch_peer(bot_id: str, peer_id: str) -> None:
    """记住最近一次收到消息的聊天，仅用于全局设置里展示。"""
    with get_session_local()() as db:
        db.execute(update(ConversationBot).where(ConversationBot.id == bot_id)
                   .values(peer_id=peer_id))
        db.execute(update(BotRoute).where(BotRoute.bot_id == bot_id).values(peer_id=peer_id))
        db.commit()


def _store(provider: str, bot_id: str, secret: str) -> str:
    identifier = zero_store.new_id("bot")
    # 旧库 conversation_bots.conversation_id 带外键；填全局 Agent 会话满足约束，
    # 新库不再依赖这个列，绑定关系只看 bot_routes。
    owner = ""
    try:
        owner = str(conversation.ensure_global_agent_conversation("local").get("id") or "")
    except Exception:
        logger.debug("获取全局 Agent 会话失败，机器人归属改用已有会话", exc_info=True)
    if not owner:
        with get_session_local()() as db:
            owner = str(db.scalar(select(Conversation.id).limit(1)) or "")
    if not owner:
        raise ValueError("请先创建一个会话，再添加机器人")
    with get_session_local()() as db:
        existing = db.scalar(select(ConversationBot.id)
                             .where(ConversationBot.provider == provider,
                                    ConversationBot.bot_id == bot_id))
        if existing:
            raise ValueError("此机器人已添加，可在下方直接绑定会话。")
        if secret_store.set_secret(identifier, secret):
            secret = secret_store.PREFIX + identifier
        try:
            db.add(ConversationBot(id=identifier, conversation_id=owner, provider=provider,
                                   bot_id=bot_id, secret=secret, peer_id=""))
            db.commit()
        except IntegrityError as exc:
            if secret.startswith(secret_store.PREFIX):
                secret_store.delete_secret(identifier)
            raise ValueError("无法添加机器人，请检查凭据是否重复。") from exc
    return identifier


def _remove_bot(bot_id: str) -> bool:
    with get_session_local()() as db:
        row = db.get(ConversationBot, bot_id)
        if row is None:
            return False
        db.execute(delete(BotRoute).where(BotRoute.bot_id == bot_id))
        db.delete(row)
        db.commit()
    return True


def _session_root(conversation_id: str) -> str:
    conv = conversation.get_conversation(conversation_id) or {}
    return str(conv.get("session_root_id") or conv.get("session_id") or conv.get("id") or "")


def _set_lane(route_id: str, lane_id: str) -> None:
    with get_session_local()() as db:
        db.execute(update(BotRoute).where(BotRoute.id == route_id)
                   .values(lane_id=lane_id or None))
        db.commit()


def _create_lane(session_id: str, provider: str, title: str = "") -> str:
    """在会话根下新建一个栏目；机器人默认就落在新栏目里。"""
    root_conv = conversation.get_conversation(session_id) or {}
    if not root_conv or root_conv.get("archived"):
        return ""
    if str(root_conv.get("zone") or "") in (conversation.GLOBAL_AGENT_ZONE,
                                            conversation.GLOBAL_CHAT_ZONE):
        return ""
    root_id = str(root_conv.get("session_root_id") or root_conv.get("id") or "")
    if not root_id:
        return ""
    try:
        created = conversation.create_conversation(
            "", f"bot:{provider}", source="bot", session_id=root_id)
    except Exception:
        logger.exception("机器人新建栏目失败 session=%s", root_id)
        return ""
    lane_id = str(created.get("id") or "")
    if not lane_id:
        return ""
    # 不预置「飞书机器人」这类固定标题：首条消息会先写 fallback，再由
    # title_service 用 LLM 生成正式标题；显式 /new <标题> 才直接写 user。
    name = str(title or "").strip()[:200]
    if name:
        with get_session_local()() as db:
            db.execute(update(Conversation).where(Conversation.id == lane_id)
                       .values(title=name, title_source="user"))
            db.commit()
    return lane_id


def _rename_lane(lane_id: str, title: str) -> bool:
    name = str(title or "").strip()[:200]
    if not lane_id or not name:
        return False
    with get_session_local()() as db:
        result = db.execute(update(Conversation).where(Conversation.id == lane_id)
                            .values(title=name, title_source="user"))
        db.commit()
        return bool(result.rowcount)


_PLACEHOLDER_TITLES = {"新栏目", "飞书机器人", "企业微信机器人", "机器人"}


def _repair_placeholder_title(lane: dict) -> None:
    """清掉旧版机器人写死的占位标题，让下一轮由 LLM 生成正式标题。"""
    lane_id = str(lane.get("id") or "")
    if not lane_id or str(lane.get("source") or "") != "bot":
        return
    if str(lane.get("title_source") or "") != "user":
        return
    if str(lane.get("title") or "").strip() not in _PLACEHOLDER_TITLES:
        return
    with get_session_local()() as db:
        db.execute(update(Conversation).where(Conversation.id == lane_id)
                   .values(title=None, title_source="fallback"))
        db.commit()
    lane["title"] = ""
    lane["title_source"] = "fallback"


def _resolve_lane(route: Route, provider: str, *, create: bool = False) -> str:
    """机器人当前使用的栏目。

    绑定在会话根上时默认新建一个栏目（`create=True`）；绑定在某个栏目上时
    直接用那个栏目。旧栏目被删 / 归档后重新兜底。
    """
    conv = conversation.get_conversation(route.conversation_id) or {}
    if not conv:
        return ""
    if conv.get("archived"):
        return ""
    root_id = str(conv.get("session_root_id") or conv.get("id") or "")
    # 全局 Agent / 全局对话是单面板，没有栏目列：消息仍进原会话。
    if str(conv.get("zone") or "") in (conversation.GLOBAL_AGENT_ZONE,
                                       conversation.GLOBAL_CHAT_ZONE):
        return str(conv.get("id") or "")
    lane_id = str(route.lane_id or "")
    if lane_id:
        lane = conversation.get_conversation(lane_id) or {}
        if lane and not lane.get("archived") \
                and str(lane.get("session_root_id") or lane.get("id")) == root_id:
            _repair_placeholder_title(lane)
            return lane_id
    # 绑定本身就是栏目：沿用用户指定的那一条。
    if str(conv.get("id") or "") != root_id:
        lane_id = str(conv.get("id") or "")
        _repair_placeholder_title(conv)
    elif create:
        lane_id = _create_lane(root_id, provider)
    else:
        lane_id = ""
    if lane_id:
        _set_lane(route.id, lane_id)
    return lane_id


def _enqueue(conversation_id: str, text: str, provider: str) -> str:
    conv = conversation.get_conversation(conversation_id)
    if not conv or conv.get("archived"):
        return ""
    msg = conversation.send_message(conversation_id, text, "user", queued=True)
    try:
        run = run_service.create_run(
            conversation_id, f"bot:{provider}", role_id=conv.get("role_id"), content=text,
            mode="chat",
            queued_msg_id=msg["id"], thinking_level=conv.get("thinking_level"),
        )
    except Exception:
        conversation.delete_message(msg["id"])
        raise
    return run["id"]


class BotConnection:
    def __init__(self, binding: Binding):
        self.binding = binding
        self.status = "connecting"
        self.error = ""
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.pending: set[asyncio.Task] = set()
        self.lane_lock = asyncio.Lock()
        self.task = asyncio.create_task(self.connect())

    def public(self) -> dict:
        binding = asdict(self.binding)
        binding.pop("secret")
        route = _route_for(self.binding.id)
        return {**binding, "status": self.status, "error": self.error,
                "conversation_id": route.conversation_id if route else "",
                "lane_id": route.lane_id if route else "",
                "peer_id": route.peer_id if route else "",
                "bound": route is not None}

    def connected(self) -> None:
        self.status = "connected"
        self.error = ""

    async def connect(self) -> None:
        delay = 2
        while True:
            try:
                secret = self.binding.secret
                if secret.startswith(secret_store.PREFIX):
                    secret = await asyncio.to_thread(secret_store.get_secret, secret[len(secret_store.PREFIX):])
                if not secret:
                    self.status = "error"
                    self.error = "机器人凭据不可用，请重新添加"
                    return
                self.status = "connecting"
                await TRANSPORTS[self.binding.provider](self.binding.bot_id, secret, self.receive, self.connected)
                raise RuntimeError("长连接已关闭")
            except Exception as exc:
                if self.status == "connected":
                    delay = 2
                self.status = "reconnecting"
                self.error = "长连接中断或认证失败，请检查凭据、平台配置及网络"
                logger.warning("机器人 %s 连接异常 (%s): %s",
                               self.binding.id, type(exc).__name__, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    def receive(self, message: bot_transports.IncomingMessage) -> None:
        if (not message.id or not message.peer or not message.text or message.id in self.seen
                or len(self.pending) >= 16):
            return
        self.seen[message.id] = None
        if len(self.seen) > 2048:
            self.seen.popitem(last=False)
        task = asyncio.create_task(self.handle(message))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def handle(self, message: bot_transports.IncomingMessage) -> None:
        try:
            route = await asyncio.to_thread(_route_for, self.binding.id)
            if route is None:
                # 未绑定会话时静默忽略，避免机器人在无关群里刷屏。
                return
            if route.peer_id != message.peer:
                await asyncio.to_thread(_touch_peer, self.binding.id, message.peer)
            provider = self.binding.provider
            command = bot_commands.parse(message.text)
            if command and command.name == "help":
                await message.reply(bot_commands.help_text())
                return
            # 栏目解析 / 新建放在同一把锁里：并发消息不会各建一个默认栏目。
            async with self.lane_lock:
                if command and command.name == "new":
                    root_id = await asyncio.to_thread(_session_root, route.conversation_id)
                    lane_id = await asyncio.to_thread(
                        _create_lane, root_id, provider, command.arg) if root_id else ""
                    if not lane_id:
                        await message.reply("该会话不能新建栏目（全局面板或会话已归档）。")
                        return
                    await asyncio.to_thread(_set_lane, route.id, lane_id)
                else:
                    lane_id = await asyncio.to_thread(
                        _resolve_lane, route, provider, create=command is None)
            if not lane_id:
                if command:
                    await message.reply("当前会话还没有栏目，先发一条消息，或发送 /new 新建一个栏目。")
                else:
                    await message.reply("AtlasX 会话已归档或不可用。")
                return
            if command:
                await message.reply(await self.run_command(command, lane_id))
                return
            if message.acknowledge:
                try:
                    await message.acknowledge()
                except Exception as exc:
                    logger.info("机器人 %s 未能发送处理中提示 (%s)", self.binding.id, type(exc).__name__)
            run_id = await asyncio.to_thread(
                _enqueue, lane_id, message.text[:20_000], provider)
            if not run_id:
                await message.reply("AtlasX 会话已归档或不可用。")
                return
            await conv_worker.request_run(run_id)
            try:
                await self.reply_when_finished(run_id, message, route)
            except TimeoutError:
                await message.reply("AtlasX 仍在处理此消息。可在会话中查看进度。")
        except Exception as exc:
            self.error = "机器人消息处理或回复失败，请查看 AtlasX 中的会话"
            logger.warning("机器人 %s 消息处理异常 (%s): %s",
                           self.binding.id, type(exc).__name__, exc)

    async def run_command(self, command: bot_commands.BotCommand, lane_id: str) -> str:
        """执行会话内安全指令，返回给聊天窗口的回复。"""
        name = command.name
        if name == "help":
            return bot_commands.help_text()
        if name == "new":
            lane = await asyncio.to_thread(conversation.get_conversation, lane_id) or {}
            return f"已新建栏目：{lane.get('title') or command.arg or '新栏目'}"
        if name == "status":
            lane = await asyncio.to_thread(conversation.get_conversation, lane_id) or {}
            running = await asyncio.to_thread(run_service.running_run, lane_id)
            title = lane.get("title") or "未命名栏目"
            return (f"当前栏目：{title}\n"
                    f"状态：{'运行中' if running else '空闲'}\n"
                    f"ID：{lane_id}")
        if name == "clear":
            ok = await asyncio.to_thread(conversation.clear_context, lane_id)
            return ("已清空当前栏目的上下文（历史消息保留）。" if ok
                    else "当前栏目不存在，无法清空。")
        if name == "compact":
            try:
                result = await conversation.compact_context(lane_id)
            except RuntimeError:
                return "当前栏目正在执行，先 /stop 再压缩。"
            except Exception as exc:
                logger.warning("机器人压缩失败 lane=%s: %s", lane_id, exc)
                return "压缩失败，请到 AtlasX 查看详情。"
            if result.get("compressed"):
                return (f"已压缩上下文：{result.get('before_tokens', 0)} → "
                        f"{result.get('after_tokens', 0)} tokens。")
            return f"无需压缩（{result.get('reason') or '上下文很短'}）。"
        if name == "stop":
            run = await asyncio.to_thread(run_service.running_run, lane_id)
            if not run:
                return "当前栏目没有正在执行的任务。"
            ok = await asyncio.to_thread(run_service.request_stop, run["id"])
            return "已停止当前栏目正在执行的任务。" if ok else "停止请求未能发送。"
        return bot_commands.help_text()

    async def reply_when_finished(self, run_id: str, message: bot_transports.IncomingMessage,
                                  route: Route) -> None:
        async with asyncio.timeout(540):
            while True:
                run = await asyncio.to_thread(run_service.get_run, run_id)
                if not run:
                    return
                if run["status"] in run_service.TERMINAL_STATUSES:
                    break
                await asyncio.sleep(1)
        if run["status"] == run_service.STATUS_DONE:
            events = await asyncio.to_thread(run_service.list_all_events, run_id)
            replies = [str(event.get("text") or "").strip() for event in events if event["_kind"] == "done"]
            reply = "\n\n".join(filter(None, replies)) or "本轮执行已完成，请在 AtlasX 查看详情。"
            # 模型用自然语言理解出的会话指令：机器人自己执行，不依赖界面。
            notes = await self.apply_session_commands(events, run, route)
            if notes:
                reply = f"{reply}\n\n" + "\n".join(notes)
        else:
            reply = "本轮未完成，请在 AtlasX 查看详情。"
        await message.reply(reply)
        self.error = ""

    async def apply_session_commands(self, events: list[dict], run: dict,
                                     route: Route) -> list[str]:
        """执行模型从自然语言里解析出的安全会话指令（栏目内，不影响整体）。"""
        lane_id = str(run.get("conversation_id") or "")
        notes: list[str] = []
        seen: set[tuple[str, str]] = set()
        for event in events:
            if event.get("_kind") == "session_command":
                payload = event
            elif isinstance(event.get("session_command"), dict):
                payload = event["session_command"]
            else:
                continue
            if payload.get("done"):
                continue
            name = str(payload.get("name") or "").strip().lower()
            arg = str(payload.get("arg") or "").strip()
            if not name or (name, arg) in seen:
                continue
            seen.add((name, arg))
            if name == "new":
                new_id = await asyncio.to_thread(
                    _create_lane, lane_id, self.binding.provider, arg or "")
                if not new_id:
                    notes.append("新建栏目失败。")
                    continue
                await asyncio.to_thread(_set_lane, route.id, new_id)
                lane = await asyncio.to_thread(conversation.get_conversation, new_id) or {}
                notes.append(f"已在当前会话下新建栏目「{lane.get('title') or '新栏目'}」。")
                lane_id = new_id
            elif name == "compact":
                try:
                    result = await conversation.compact_context(lane_id)
                except RuntimeError:
                    notes.append("当前栏目正在执行，未能压缩。")
                except Exception:
                    notes.append("压缩失败，请到 AtlasX 查看详情。")
                else:
                    if result.get("compressed"):
                        notes.append("已压缩当前栏目上下文。")
                    else:
                        notes.append(f"无需压缩（{result.get('reason') or '上下文很短'}）。")
            elif name == "rename":
                if await asyncio.to_thread(_rename_lane, lane_id, arg):
                    notes.append(f"已重命名当前栏目为「{arg[:80]}」。")
                else:
                    notes.append("重命名失败（缺少标题或栏目不存在）。")
            elif name == "read":
                await asyncio.to_thread(conversation.mark_conversation_read, lane_id)
                notes.append("已标记当前栏目已读。")
            elif name in ("delete", "clean"):
                notes.append("为安全起见，机器人不能删除会话 / 清空历史；请在 AtlasX 里操作。")
            elif name in ("work", "model", "attach", "conv", "settings", "about", "theme"):
                notes.append("该操作需要在 AtlasX 界面完成。")
            # stop：run 已经结束，无需再停。
        return notes

    async def close(self) -> None:
        tasks = [self.task, *self.pending]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class BotManager:
    def __init__(self):
        self.connections: dict[str, BotConnection] = {}
        self.lock = asyncio.Lock()

    async def sync(self) -> None:
        async with self.lock:
            bindings = {item.id: item for item in await asyncio.to_thread(_bindings)}
            for identifier in self.connections.keys() - bindings.keys():
                connection = self.connections.pop(identifier)
                await connection.close()
                if connection.binding.secret == secret_store.PREFIX + identifier:
                    await asyncio.to_thread(secret_store.delete_secret, identifier)
            for identifier in bindings.keys() - self.connections.keys():
                self.connections[identifier] = BotConnection(bindings[identifier])

    async def maintain(self) -> None:
        while True:
            try:
                await self.sync()
            except Exception:
                logger.exception("同步机器人连接失败")
            await asyncio.sleep(3)

    async def close(self) -> None:
        await asyncio.gather(*(conn.close() for conn in self.connections.values()))
        self.connections.clear()

    async def list(self) -> list[dict]:
        await self.sync()
        return [conn.public() for conn in self.connections.values()]

    async def add(self, provider: str, bot_id: str, secret: str) -> dict:
        identifier = await asyncio.to_thread(_store, provider, bot_id, secret)
        await self.sync()
        return self.connections[identifier].public()

    async def bind(self, identifier: str, conversation_id: str) -> dict | None:
        await self.sync()
        conn = self.connections.get(identifier)
        if conn is None:
            return None
        await asyncio.to_thread(_bind, identifier, conversation_id)
        return conn.public()

    async def unbind(self, identifier: str) -> bool:
        removed = await asyncio.to_thread(_unbind, identifier)
        await self.sync()
        return removed

    async def remove(self, identifier: str) -> bool:
        removed = await asyncio.to_thread(_remove_bot, identifier)
        await self.sync()
        return removed

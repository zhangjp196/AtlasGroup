"""机器人长连接、角色 / 群组目标和外部聊天对话映射。

每个机器人账号只建立一条长连接，并绑定一个持久角色或共享群组。每个外部聊天
按 peer_id 映射到自己的会话；首次来消息或显式新建时创建，回复发回原聊天。
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass

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
    target_type: str = ""
    target_id: str = ""


@dataclass(frozen=True)
class Route:
    id: str
    bot_id: str
    conversation_id: str
    peer_id: str


def _bindings() -> list[Binding]:
    statement = select(ConversationBot).order_by(ConversationBot.provider, ConversationBot.bot_id)
    with get_session_local()() as db:
        return [Binding(row.id, row.provider, row.bot_id, row.secret,
                        row.target_type or "", row.target_id or "")
                for row in db.scalars(statement)]


def _route_for(bot_id: str, peer_id: str) -> Route | None:
    with get_session_local()() as db:
        row = db.scalar(select(BotRoute).where(
            BotRoute.bot_id == bot_id, BotRoute.peer_id == peer_id))
        return (Route(row.id, row.bot_id, row.conversation_id, row.peer_id)
                if row else None)


def _peer_routes(bot_id: str) -> list[dict]:
    with get_session_local()() as db:
        rows = db.execute(select(BotRoute, Conversation)
                          .join(Conversation, Conversation.id == BotRoute.conversation_id)
                          .where(BotRoute.bot_id == bot_id)
                          .order_by(BotRoute.created_at.desc()))
        return [{"conversation_id": route.conversation_id, "peer_id": route.peer_id,
                 "local": route.peer_id.startswith("atlasx:"),
                 "title": conv.title or route.conversation_id,
                 "updated_at": conv.updated_at.isoformat() if conv.updated_at else ""}
                for route, conv in rows]


def _bind_target(bot_id: str, target_type: str, target_id: str) -> Binding | None:
    """绑定角色或共享群组；外部聊天的对话映射仍按 peer 独立保存。"""
    with get_session_local()() as db:
        bot = db.get(ConversationBot, bot_id)
        if bot is None:
            return None
        if bot.target_type != target_type or bot.target_id != target_id:
            db.execute(delete(BotRoute).where(BotRoute.bot_id == bot_id))
        bot.target_type = target_type
        bot.target_id = target_id
        db.commit()
        return Binding(bot.id, bot.provider, bot.bot_id, bot.secret,
                       target_type, target_id)


def _bind_peer(bot_id: str, peer_id: str, conversation_id: str) -> Route:
    with get_session_local()() as db:
        row = db.scalar(select(BotRoute).where(
            BotRoute.bot_id == bot_id, BotRoute.peer_id == peer_id))
        if row is None:
            row = BotRoute(id=zero_store.new_id("botr"), bot_id=bot_id,
                           conversation_id=conversation_id, peer_id=peer_id)
            db.add(row)
        else:
            row.conversation_id = conversation_id
            row.lane_id = None
        db.commit()
        return Route(row.id, bot_id, conversation_id, peer_id)


def _set_peer_conversation(route_id: str, conversation_id: str) -> None:
    with get_session_local()() as db:
        db.execute(update(BotRoute).where(BotRoute.id == route_id)
                   .values(conversation_id=conversation_id, lane_id=None))
        db.commit()


def _create_target_conversation(target_type: str, target_id: str, provider: str,
                                title: str = "") -> dict:
    if target_type == "role":
        role = zero_store.get_role(target_id)
        if not role or role.get("scope") == "temporary" or target_id == zero_store.GLOBAL_ROLE_ID:
            raise ValueError("绑定角色不存在或不可用于机器人")
        conv = conversation.create_conversation(
            str(role.get("workspace_id") or ""), f"bot:{provider}",
            role_id=target_id, source="bot")
    elif target_type == "group":
        from services.agent_zero.services import team_service

        team = team_service.get_team(target_id)
        if not team or str(team.get("kind") or "group") != "group":
            raise ValueError("绑定群组不存在或不可用于机器人")
        conv = conversation.create_conversation(
            str(team.get("workspace_id") or ""), f"bot:{provider}",
            team_id=target_id, source="bot")
    else:
        raise ValueError("机器人只能绑定角色或群组")
    name = str(title or "").strip()[:200]
    if name:
        with get_session_local()() as db:
            db.execute(update(Conversation).where(Conversation.id == conv["id"])
                       .values(title=name, title_source="user"))
            db.commit()
        conv["title"] = name
    return conv


def _unbind(bot_id: str) -> bool:
    with get_session_local()() as db:
        result = db.execute(update(ConversationBot).where(ConversationBot.id == bot_id)
                            .values(target_type=None, target_id=None))
        db.execute(delete(BotRoute).where(BotRoute.bot_id == bot_id))
        db.commit()
        return bool(result.rowcount)


def _touch_peer(bot_id: str, peer_id: str) -> None:
    """记住最近一次收到消息的聊天，仅用于全局设置里展示。"""
    with get_session_local()() as db:
        db.execute(update(ConversationBot).where(ConversationBot.id == bot_id)
                   .values(peer_id=peer_id))
        db.commit()


def _store(provider: str, bot_id: str, secret: str) -> str:
    identifier = zero_store.new_id("bot")
    with get_session_local()() as db:
        existing = db.scalar(select(ConversationBot.id)
                             .where(ConversationBot.provider == provider,
                                    ConversationBot.bot_id == bot_id))
        if existing:
            raise ValueError("此机器人已添加，可在下方绑定角色或群组。")
        if secret_store.set_secret(identifier, secret):
            secret = secret_store.PREFIX + identifier
        try:
            # conversation_id 是旧版遗留字段；不再通过它绑定会话。
            db.add(ConversationBot(id=identifier, conversation_id="", provider=provider,
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


def _rename_conversation(lane_id: str, title: str) -> bool:
    name = str(title or "").strip()[:200]
    if not lane_id or not name:
        return False
    with get_session_local()() as db:
        result = db.execute(update(Conversation).where(Conversation.id == lane_id)
                            .values(title=name, title_source="user"))
        db.commit()
        return bool(result.rowcount)


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
        self.peer_locks: dict[str, asyncio.Lock] = {}
        self.task = asyncio.create_task(self.connect())

    def public(self) -> dict:
        with get_session_local()() as db:
            row = db.get(ConversationBot, self.binding.id)
            if row is None:
                return {"id": self.binding.id, "provider": self.binding.provider,
                        "bot_id": self.binding.bot_id, "status": self.status,
                        "error": self.error, "target_type": "", "target_id": "",
                        "peer_id": "", "bound": False}
            return {"id": row.id, "provider": row.provider, "bot_id": row.bot_id,
                    "target_type": row.target_type or "", "target_id": row.target_id or "",
                    "peer_id": row.peer_id or "", "bound": bool(row.target_type and row.target_id),
                    "status": self.status, "error": self.error}

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
            if not self.binding.target_type or not self.binding.target_id:
                # 未绑定时忽略，以免在不相关的外部聊天中刷提示。
                return
            await asyncio.to_thread(_touch_peer, self.binding.id, message.peer)
            provider = self.binding.provider
            command = bot_commands.parse(message.text)
            if command and command.name == "help":
                await message.reply(bot_commands.help_text())
                return
            lock = self.peer_locks.setdefault(message.peer, asyncio.Lock())
            async with lock:
                route = await asyncio.to_thread(_route_for, self.binding.id, message.peer)
                if command and command.name == "new":
                    conv = await asyncio.to_thread(
                        _create_target_conversation, self.binding.target_type,
                        self.binding.target_id, provider, command.arg)
                    if route:
                        await asyncio.to_thread(_set_peer_conversation, route.id, str(conv["id"]))
                    else:
                        route = await asyncio.to_thread(
                            _bind_peer, self.binding.id, message.peer, str(conv["id"]))
                    await message.reply(f"已新建对话：{conv.get('title') or '新对话'}")
                    return
                conv = conversation.get_conversation(route.conversation_id) if route else None
                if not conv or conv.get("archived"):
                    conv = await asyncio.to_thread(
                        _create_target_conversation, self.binding.target_type,
                        self.binding.target_id, provider)
                    route = await asyncio.to_thread(
                        _bind_peer, self.binding.id, message.peer, str(conv["id"]))
            conversation_id = str(route.conversation_id)
            if command:
                await message.reply(await self.run_command(command, conversation_id))
                return
            if message.acknowledge:
                try:
                    await message.acknowledge()
                except Exception as exc:
                    logger.info("机器人 %s 未能发送处理中提示 (%s)", self.binding.id, type(exc).__name__)
            run_id = await asyncio.to_thread(
                _enqueue, conversation_id, message.text, provider)
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
            return "新建对话失败，请重试。"
        if name == "status":
            lane = await asyncio.to_thread(conversation.get_conversation, lane_id) or {}
            running = await asyncio.to_thread(run_service.running_run, lane_id)
            title = lane.get("title") or "未命名对话"
            return (f"当前对话：{title}\n"
                    f"状态：{'运行中' if running else '空闲'}\n"
                    f"ID：{lane_id}")
        if name == "clear":
            ok = await asyncio.to_thread(conversation.clear_context, lane_id)
            return ("已清空当前对话的上下文（历史消息保留）。" if ok
                    else "当前对话不存在，无法清空。")
        if name == "compact":
            try:
                result = await conversation.compact_context(lane_id)
            except RuntimeError:
                return "当前对话正在执行，先 /stop 再压缩。"
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
                return "当前对话没有正在执行的任务。"
            ok = await asyncio.to_thread(run_service.request_stop, run["id"])
            return "已停止当前对话正在执行的任务。" if ok else "停止请求未能发送。"
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
                try:
                    created = await asyncio.to_thread(
                        _create_target_conversation, self.binding.target_type,
                        self.binding.target_id, self.binding.provider, arg or "")
                except ValueError:
                    notes.append("新建对话失败。")
                    continue
                await asyncio.to_thread(_set_peer_conversation, route.id, str(created["id"]))
                notes.append(f"已新建对话「{created.get('title') or '新对话'}」。")
                lane_id = str(created["id"])
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
                if await asyncio.to_thread(_rename_conversation, lane_id, arg):
                    notes.append(f"已重命名当前对话为「{arg[:80]}」。")
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
            for identifier in bindings.keys() & self.connections.keys():
                self.connections[identifier].binding = bindings[identifier]

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

    async def bind(self, identifier: str, target_type: str, target_id: str) -> dict | None:
        await self.sync()
        conn = self.connections.get(identifier)
        if conn is None:
            return None
        binding = await asyncio.to_thread(_bind_target, identifier, target_type, target_id)
        if binding is None:
            return None
        conn.binding = binding
        return conn.public()

    async def conversations(self, identifier: str) -> list[dict]:
        return await asyncio.to_thread(_peer_routes, identifier)

    async def create_conversation(self, identifier: str, title: str = "") -> dict | None:
        await self.sync()
        conn = self.connections.get(identifier)
        if conn is None:
            return None
        binding = conn.public()
        if not binding.get("bound"):
            raise ValueError("请先为机器人绑定角色或群组")
        conv = await asyncio.to_thread(
            _create_target_conversation, str(binding["target_type"]),
            str(binding["target_id"]), conn.binding.provider, title)
        await asyncio.to_thread(_bind_peer, identifier, f"atlasx:{conv['id']}", str(conv["id"]))
        return conv

    async def unbind(self, identifier: str) -> bool:
        removed = await asyncio.to_thread(_unbind, identifier)
        await self.sync()
        return removed

    async def remove(self, identifier: str) -> bool:
        removed = await asyncio.to_thread(_remove_bot, identifier)
        await self.sync()
        return removed

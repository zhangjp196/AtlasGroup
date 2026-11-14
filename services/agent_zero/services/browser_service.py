"""可见内置浏览器的自动化命令队列。

Agent 在服务器侧入队，桌面 WebView 前端取出并通过原生桥执行，再把结果送回。
浏览器页面保持可见，用户可以随时直接查看和接管操作。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, Optional

_commands: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
_pending: Dict[str, asyncio.Future] = {}
_last_client_seen = 0.0


def heartbeat() -> None:
    global _last_client_seen
    _last_client_seen = time.monotonic()


def available() -> bool:
    return time.monotonic() - _last_client_seen < 15


async def automate(op: str, *, url: str = "", selector: str = "", value: str = "",
                   timeout: float = 90) -> Any:
    if not available():
        return {"error": "桌面内置浏览器尚未连接；请先打开 AtlasX 桌面端会话窗口。"}
    command_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    _pending[command_id] = future
    await _commands.put({
        "id": command_id,
        "op": op,
        "url": url,
        "selector": selector,
        "value": value,
    })
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": "可见浏览器未连接或操作超时；请确认桌面窗口仍在运行后重试。"}
    finally:
        _pending.pop(command_id, None)


async def next_command(timeout: float = 20) -> Optional[Dict[str, Any]]:
    try:
        return await asyncio.wait_for(_commands.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


def complete(command_id: str, *, result: Any = None, error: str = "") -> bool:
    future = _pending.get(command_id)
    if future is None or future.done():
        return False
    future.set_result({"error": error} if error else result)
    return True

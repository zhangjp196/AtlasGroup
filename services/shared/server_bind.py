"""服务监听端口：默认端口被占用时自动顺延（本地单机部署）。

规则：
1. 环境变量 ``AI_PLATFORM_SERVER_PORT`` / ``AGENT_ZERO_SERVER_PORT`` 优先且不自动顺延；
2. 配置 ``server.auto_port: false`` 时严格使用配置端口；
3. 否则从配置端口开始向后尝试 ``server.port_tries`` 个端口；
4. 仍失败则让系统分配一个空闲端口。

返回**已 bind + listen 的 socket**，交给 uvicorn 直接服务，避免「检测到空闲 →
真正 bind 时又被抢占」的竞态。
"""
from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)

ENV_SERVER_PORT = "AI_PLATFORM_SERVER_PORT"
ENV_SERVER_PORT_ALT = "AGENT_ZERO_SERVER_PORT"


def env_port() -> int | None:
    import os

    raw = (os.environ.get(ENV_SERVER_PORT)
           or os.environ.get(ENV_SERVER_PORT_ALT) or "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError as e:
        raise ValueError(f"{ENV_SERVER_PORT} 无效: {raw!r}") from e
    if not (1 <= port <= 65535):
        raise ValueError(f"{ENV_SERVER_PORT} 超出范围: {port}")
    return port


def _try_bind(host: str, port: int) -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(2048)
        sock.set_inheritable(True)
        return sock
    except OSError:
        try:
            sock.close()
        except OSError:
            pass
        return None


def bind_socket(host: str, preferred: int, *,
                auto: bool = True, tries: int = 30
                ) -> tuple[socket.socket, int]:
    """绑定监听 socket，返回 (socket, 实际端口)。"""
    fixed = env_port()
    if fixed is not None:
        sock = _try_bind(host, fixed)
        if sock is None:
            raise OSError(f"端口 {fixed} 已被占用（由环境变量指定，不自动切换）")
        return sock, fixed

    preferred = int(preferred or 0)
    if not auto:
        sock = _try_bind(host, preferred)
        if sock is None:
            raise OSError(f"端口 {preferred} 已被占用（server.auto_port=false）")
        return sock, preferred

    count = max(1, min(int(tries or 1), 500))
    for port in range(preferred, min(65535, preferred + count) + 1):
        sock = _try_bind(host, port)
        if sock is not None:
            if port != preferred:
                logger.warning("端口 %s 已被占用，自动切换为 %s", preferred, port)
            return sock, port

    # 顺延范围耗尽：让系统分配临时端口
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    sock.listen(2048)
    port = int(sock.getsockname()[1])
    logger.warning("端口 %s-%s 均不可用，改用系统分配端口 %s",
                   preferred, preferred + count, port)
    return sock, port

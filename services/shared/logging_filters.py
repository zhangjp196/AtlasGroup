"""共享日志过滤器：减少高频、低价值日志写入 logs/ 文件。"""
from __future__ import annotations

import logging

# 注册中心子服务 → 平台 的心跳路径（httpx / uvicorn access 等）
_REGISTRY_HEARTBEAT_MARKER = "/internal/registry/heartbeat"


class HeartbeatLogFilter(logging.Filter):
    """文件日志中排除成功的注册中心心跳（INFO/DEBUG），保留失败告警。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return _REGISTRY_HEARTBEAT_MARKER not in msg


def attach_heartbeat_log_filter(handler: logging.Handler) -> None:
    """为 handler 挂载心跳过滤（已挂载则跳过）。"""
    if any(isinstance(f, HeartbeatLogFilter) for f in handler.filters):
        return
    handler.addFilter(HeartbeatLogFilter())

"""结构化事件日志（JSONL）。

文本日志继续写 `agent_zero.log`；这里同一个 logger 树再挂一个 JSON handler，
`log_event()` 写入的关键事件带上 run_id / conv_id / 状态 / 耗时等字段，
方便本地诊断与后续接采集。
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("atlas.events")


def log_event(event: str, **fields: Any) -> None:
    # extra 里不能覆盖 LogRecord 的内置字段；这里统一放到 event_fields 下。
    _log.info(event, extra={"event": event, "event_fields": fields})

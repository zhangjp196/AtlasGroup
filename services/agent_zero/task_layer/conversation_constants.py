"""Stable constants shared across conversation orchestration and domain modules."""

GLOBAL_AGENT_ZONE = "global-agent"
GLOBAL_CHAT_ZONE = "global-chat"
CLEAR_KIND = "clear_context"
CHECKPOINT_KIND = "checkpoint"

CHAT_HISTORY = 60
CHAT_HISTORY_SHARE = 0.20
CHAT_HISTORY_MIN_TOKENS = 8_000
CHAT_HISTORY_MAX_TOKENS = 60_000
CHAT_HISTORY_FALLBACK_TOKENS = 30_000

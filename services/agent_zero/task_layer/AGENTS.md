# Conversation and task layer

- `conversation.py` is a compatibility/orchestration surface with legacy callers; avoid adding unrelated responsibilities there.
- Read-side listing, unread aggregation, transcript projection, and usage totals live in `conversation_queries.py`; stable shared markers live in `conversation_constants.py`.
- History construction, context markers, and compaction checkpoints live in `conversation_context.py`; orchestration should call its compatibility exports instead of copying those flows.
- Stream support helpers for compaction events, live context metering, final transcript extraction, and interrupted-run timeline recovery live in `conversation_stream.py`; `conversation.py` re-exports them for legacy callers.
- Pure transcript ordering and tool activity summaries live in `message_order.py` and `tool_observability.py`.
- Put new lifecycle logic in a focused module and expose a small helper from `conversation.py` only where existing callers require it.
- Preserve event ordering, cancellation propagation, run persistence, and isolated subagent context.
- For stream changes, add focused tests for event order and failure/cancellation paths before moving the next block.

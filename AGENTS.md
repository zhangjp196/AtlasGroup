# AgentZero coding guide

## Start here
- Backend: `services/agent_zero/routes/` → `services/agent_zero/services/` or `task_layer/` → `stores/` / `runtime/`.
- Frontend: `frontend/src/views/` → `components/` / `composables/` → `api/` and `stores/`.
- Session execution is in `task_layer/`; conversation reads live in `task_layer/conversation_queries.py`, history and compaction in `task_layer/conversation_context.py`, pure transcript ordering in `task_layer/message_order.py`, and compact tool activity summaries in `task_layer/tool_observability.py`. Workspace file access is in `services/workspace_service.py` and `routes/workspace_routes.py`.
- Read `doc/development.md` before changing shared architecture or adding a new extension point.

## Work loop
- Check `git status` and read diffs before editing. Preserve existing user changes.
- For structural questions, use CodeGraph; run `codegraph status` and sync the index before relying on pending files.
- Make one behavior change at a time. Keep orchestration thin; put reusable behavior in a service, composable, or focused component.
- Add or update focused tests for behavior changes. Run the narrowest relevant check; run `bash devops/dev.sh check:full` for cross-cutting changes.
- Report commands actually run and any checks skipped.

## Rules
- Preserve workspace path containment and session/workspace ownership when adding file operations.
- Do not silently swallow missing dependencies or failed checks.
- Do not add a new global singleton when its lifecycle can belong to the app or request.
- Avoid unrelated formatting or broad file moves. Keep diffs small enough to review.

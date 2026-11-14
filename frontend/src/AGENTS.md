# Frontend guide

- Keep page composition in `views/`, reusable UI in `components/`, reusable stateful behavior in `composables/`, and transport/types in `api/`.
- Bind session UI to the active conversation's `workspace_id`; clear or cancel stale async state when the conversation or workspace changes.
- Reuse existing API and shared UI components before adding parallel implementations.
- Keep dialogs usable at narrow popup sizes and expose loading, empty, and error states.
- Run `npm run typecheck`; add focused unit coverage for reusable state or presentation logic.

# Chat UI

- `ChatPanel.vue` owns session composition and active conversation state; feature panels belong in sibling components.
- Long-lived or asynchronous behavior belongs in a composable or feature component with explicit cleanup.
- When session or workspace identity changes, abort old requests and clear session-scoped previews and controls.
- Keep chat usable in popup, drawer, and tab presentations; new panels must handle narrow viewports.

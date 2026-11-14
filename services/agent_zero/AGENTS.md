# Backend guide

- Keep HTTP parsing and response mapping in `routes/`; put reusable behavior in `services/`, `task_layer/`, or `runtime/` according to ownership.
- Keep filesystem operations behind `services/workspace_service.py`; resolve every path through its workspace root helpers.
- Keep provider-specific and tool-specific behavior behind the existing adapter/registry seams.
- Prefer explicit inputs and app/request-owned lifecycles over module-level mutable state.
- Update the closest unit or integration test when behavior changes.

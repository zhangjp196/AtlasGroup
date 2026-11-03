#!/usr/bin/env bash
# 本地质量门禁：编译 + ruff + pytest
# 用法: bash devops/ci.sh        （可 PYTHON=/path/to/python 指定解释器）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

echo "[ci] python: $PY"
echo "[ci] compileall"
"$PY" -m compileall -q services desktop tests

echo "[ci] ruff"
"$PY" -m ruff check services desktop tests

echo "[ci] pytest"
"$PY" -m pytest

if command -v node >/dev/null 2>&1; then
  echo "[ci] node --check web/assets/app.js"
  node --check web/assets/app.js
  echo "[ci] frontend behaviour tests"
  node tests/js/composer_enter_test.js
  node tests/js/send_button_test.js
  node tests/js/memory_entry_test.js
  node tests/js/side_tabs_test.js
  node tests/js/ws_collapse_all_test.js
  node tests/js/ws_no_use_button_test.js
  node tests/js/no_auto_collapse_test.js
  node tests/js/conversation_sections_test.js
  node tests/js/active_runs_test.js
  node tests/js/process_panel_float_test.js
  node tests/js/todo_panel_entry_test.js
  node tests/js/todo_panel_layout_test.js
  node tests/js/todo_card_test.js
  node tests/js/branding_test.js
  node tests/js/conv_drawer_test.js
  node tests/js/mention_test.js
  node tests/js/mention_menu_test.js
  node tests/js/new_chat_test.js
  node tests/js/im_layout_test.js
  node tests/js/v3_shell_test.js
  node tests/js/outline_hover_test.js
  node tests/js/tools_catalog_test.js
  node tests/js/context_chip_test.js
  node tests/js/turn_fold_test.js
  node tests/js/queue_reorder_test.js
  node tests/js/queued_position_test.js
  node tests/js/injected_position_test.js
  node tests/js/command_wrap_test.js
  node tests/js/tool_badge_test.js
  node tests/js/flow_row_test.js
  node tests/js/flow_row_layout_test.js
  node tests/js/settings_workspaces_chat_test.js
  node tests/js/settings_layout_test.js
  node tests/js/usage_compact_test.js
  node tests/js/subscription_form_layout_test.js
  node tests/js/subscription_form_render_test.js
  node tests/js/stability_v2_test.js
else
  echo "[ci] node 未安装，跳过前端测试"
fi

echo "[ci] OK"

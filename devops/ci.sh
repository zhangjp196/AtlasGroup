#!/usr/bin/env bash
# 本地质量门禁：前端构建 + Python 编译 / ruff / pytest
# 用法: bash devops/ci.sh        （可 PYTHON=/path/to/python 指定解释器）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

echo "[ci] python: $PY"

# 先构建唯一前端：pytest 里的静态入口测试依赖 web/dist。
if command -v npm >/dev/null 2>&1 && [ -f "$ROOT/frontend/package-lock.json" ]; then
  echo "[ci] frontend deps"
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    (cd "$ROOT/frontend" && npm ci --no-audit --no-fund)
  fi
  echo "[ci] frontend typecheck / unit / build"
  (cd "$ROOT/frontend" && npm run typecheck && npm run test:unit && npm run build)
else
  echo "[ci] npm 或 frontend/package-lock.json 缺失，跳过 Vue 前端检查"
fi

echo "[ci] compileall"
"$PY" -m compileall -q services desktop tests

echo "[ci] ruff"
"$PY" -m ruff check services desktop tests

echo "[ci] pytest"
"$PY" -m pytest

echo "[ci] OK"

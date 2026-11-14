#!/usr/bin/env bash
# 本地质量门禁：前端类型 / 单测 / 构建 + Python 编译 / ruff / pytest
# 用法: bash devops/ci.sh [fast|full] （可 PYTHON=/path/to/python 指定解释器）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODE="${1:-full}"

PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[ci] Python 3 不可用" >&2
    exit 1
  fi
  PY="$(command -v python3)"
fi

echo "[ci] python: $PY"

if ! command -v npm >/dev/null 2>&1; then
  echo "[ci] npm 不可用；请安装 package.json 要求的 Node.js 和 npm" >&2
  exit 1
fi
if [[ ! -f "$ROOT/frontend/package-lock.json" ]]; then
  echo "[ci] 缺少 frontend/package-lock.json" >&2
  exit 1
fi
if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund)
fi

if [[ "$MODE" == "e2e" ]]; then
  echo "[ci] Chrome workspace browser regression"
  (cd "$ROOT/frontend" && npm run test:e2e:workspace)
  exit 0
fi

echo "[ci] frontend typecheck"
(cd "$ROOT/frontend" && npm run typecheck)

if [[ "$MODE" == "fast" ]]; then
  echo "[ci] ruff"
  "$PY" -m ruff check services desktop tests
  echo "[ci] fast checks OK"
  exit 0
fi
if [[ "$MODE" != "full" ]]; then
  echo "用法: bash devops/ci.sh [fast|e2e|full]" >&2
  exit 2
fi

echo "[ci] frontend unit / build"
(cd "$ROOT/frontend" && npm run test:unit && npm run build)

echo "[ci] compileall"
"$PY" -m compileall -q services desktop tests

echo "[ci] ruff"
"$PY" -m ruff check services desktop tests

echo "[ci] pytest"
"$PY" -m pytest

echo "[ci] OK"

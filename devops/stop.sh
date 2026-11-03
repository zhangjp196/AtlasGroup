#!/usr/bin/env bash
# AtlasX — 停止服务
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE="agent_zero"
PID_FILE="${ROOT}/data/pids/${SERVICE}.pid"
RUNTIME_FILE="${ROOT}/data/agent_zero/runtime.json"
STOPPED=0

if [[ -f "$PID_FILE" ]]; then
  PID="$(tr -d '[:space:]' < "$PID_FILE")"
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "  ■ 停止 ${SERVICE} (PID $PID)"
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 60); do kill -0 "$PID" 2>/dev/null || break; sleep 0.1; done
    kill -9 "$PID" 2>/dev/null || true
    STOPPED=1
  fi
  rm -f "$PID_FILE"
fi

# 兜底：按命令行匹配残留进程（兼容相对/绝对入口路径）
if command -v pgrep >/dev/null 2>&1; then
  for PID in $(pgrep -f "main-agent-zero[.]py" 2>/dev/null || true); do
    [[ "$PID" == "$$" ]] && continue
    # 仅处理本项目目录下的进程，避免误杀其它 checkout
    if command -v lsof >/dev/null 2>&1; then
      PROC_CWD="$(lsof -a -p "$PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
      if [[ -n "$PROC_CWD" && "$PROC_CWD" != "$ROOT" ]]; then
        continue
      fi
    fi
    echo "  ■ 停止残留进程 (PID $PID)"
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 0.1; done
    kill -9 "$PID" 2>/dev/null || true
    STOPPED=1
  done
fi

if [[ "$STOPPED" -eq 0 ]]; then
  echo "  （${SERVICE} 未在运行）"
fi

# 地址信息只对运行中的进程有效
rm -f "$RUNTIME_FILE"

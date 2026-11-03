#!/usr/bin/env bash
# AtlasX — 启动服务（后台）
#
# 端口取 config/agent_zero/config.yaml 的 server.port；
# auto_port=true 时若被占用会自动向后顺延，实际地址写入 data/agent_zero/runtime.json。
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE="agent_zero"
ENTRY="main-agent-zero.py"
SVC_DIR="services/agent_zero"
CONFIG="config/agent_zero/config.yaml"
VENV="${ROOT}/.venv/bin/python"
PID_DIR="${ROOT}/data/pids"
PID_FILE="${PID_DIR}/${SERVICE}.pid"
LOG_FILE="${ROOT}/logs/${SERVICE}.log"
RUNTIME_FILE="${ROOT}/data/agent_zero/runtime.json"

if [[ ! -x "$VENV" ]]; then
  echo "  ✗ venv 不存在: ${VENV}"
  echo "    运行: bash devops/dev.sh env"
  exit 1
fi
if [[ ! -f "${ROOT}/${CONFIG}" ]]; then
  echo "  ✗ 配置不存在: ${CONFIG}"
  exit 1
fi

mkdir -p "$PID_DIR" "$(dirname "$LOG_FILE")"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(tr -d '[:space:]' < "$PID_FILE")"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "  ↻ 停止旧进程 (PID $OLD_PID)"
    kill "$OLD_PID" 2>/dev/null || true
    for _ in $(seq 1 40); do kill -0 "$OLD_PID" 2>/dev/null || break; sleep 0.1; done
    kill -9 "$OLD_PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

# 上次异常退出留下的地址信息会让 status 误报
rm -f "$RUNTIME_FILE"

echo "  ▶ ${SVC_DIR}/${ENTRY}"
nohup env -u PYTHONHOME \
  VIRTUAL_ENV="${ROOT}/.venv" \
  PATH="$(dirname "$VENV"):${PATH:-/usr/bin:/bin}" \
  PYTHONPATH="${ROOT}:${ROOT}/${SVC_DIR}" \
  PYTHONUNBUFFERED=1 \
  AI_PLATFORM_CONFIG_YAML="${ROOT}/${CONFIG}" \
  "$VENV" "${ROOT}/${SVC_DIR}/${ENTRY}" >> "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
sleep 0.5
if ! kill -0 "$PID" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "  ✗ 启动失败，查看 logs/${SERVICE}.log"
  exit 1
fi

URL=""
for _ in $(seq 1 80); do
  if [[ -f "$RUNTIME_FILE" ]]; then
    URL="$("$VENV" - "$RUNTIME_FILE" "$PID" <<'PY' 2>/dev/null || true
import json
import sys

try:
    info = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
if int(info.get("pid") or 0) == int(sys.argv[2]):
    print(info.get("url") or "")
PY
)"
    [[ -n "$URL" ]] && break
  fi
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.1
done

echo "  ✓ ${SERVICE} (PID $PID)"
if [[ -n "$URL" ]]; then
  echo "    ${URL}"
else
  echo "    （地址尚未就绪，见 data/agent_zero/runtime.json 或 logs/${SERVICE}.log）"
fi
echo "    日志: logs/${SERVICE}.log"

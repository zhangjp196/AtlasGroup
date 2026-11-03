#!/usr/bin/env bash
# AtlasX — 开发与运维入口
#
# 用法:
#   bash devops/dev.sh env       # 创建 .venv 并安装后端依赖（requirements.txt 锁文件）
#   bash devops/dev.sh env-dev   # 额外安装测试/CI 依赖（requirements-dev.txt）
#   bash devops/dev.sh dev       # 前台运行后端（调试）
#   bash devops/dev.sh start     # 后台启动后端
#   bash devops/dev.sh stop      # 停止后端
#   bash devops/dev.sh restart   # 重启后端
#   bash devops/dev.sh status    # 查看实际监听地址（端口被占用时会自动顺延）
#   bash devops/dev.sh logs      # 跟踪后端日志
#   bash devops/dev.sh ci        # 本地质量门禁：compile + ruff + pytest
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SERVICE="agent_zero"
SVC_DIR="services/agent_zero"
ENTRY="main-agent-zero.py"
CONFIG="config/agent_zero/config.yaml"
VENV="${ROOT}/.venv"
PY="${VENV}/bin/python"
REQ="${SVC_DIR}/requirements.txt"
[[ -f "${ROOT}/${REQ}" ]] || REQ="${SVC_DIR}/requirements.in"
REQ_DEV="${SVC_DIR}/requirements-dev.txt"
RUNTIME="${ROOT}/data/agent_zero/runtime.json"

cmd_env() {
  command -v python3 >/dev/null || { echo "python3 不可用"; exit 1; }
  python3 -m venv "$VENV"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r "${ROOT}/${REQ}"
  echo "[env] 后端依赖安装完成 → ${VENV}"
}

cmd_env_dev() {
  cmd_env
  [[ -f "${ROOT}/${REQ_DEV}" ]] && "$PY" -m pip install -r "${ROOT}/${REQ_DEV}"
  echo "[env-dev] 测试/CI 依赖安装完成 → ${VENV}"
}

cmd_dev() {
  [[ -x "$PY" ]] || { echo "缺少环境，先运行: bash devops/dev.sh env"; exit 1; }
  rm -f "$RUNTIME"
  PYTHONPATH="${ROOT}:${ROOT}/${SVC_DIR}" \
    AI_PLATFORM_CONFIG_YAML="${ROOT}/${CONFIG}" \
    "$PY" "${ROOT}/${SVC_DIR}/${ENTRY}"
}

cmd_status() {
  if [[ ! -f "$RUNTIME" ]]; then
    echo "  ○ ${SERVICE} 未在运行（没有 data/agent_zero/runtime.json）"
    exit 1
  fi
  "$PY" - "$RUNTIME" <<'PY'
import json
import os
import sys

path = sys.argv[1]
try:
    info = json.load(open(path, encoding="utf-8"))
except Exception as exc:
    print(f"  ✗ runtime.json 无法读取: {exc}")
    raise SystemExit(1)

pid = int(info.get("pid") or 0)
try:
    os.kill(pid, 0)
except OSError:
    print(f"  ○ 进程已退出（PID {pid}），可删除 {path}")
    raise SystemExit(1)

print(f"  ● {info.get('url')}")
print(f"    PID {pid} · {info.get('host')}:{info.get('port')} · 启动于 {info.get('started_at')}")
PY
}

cmd_logs() { tail -n 200 -f "${ROOT}/logs/${SERVICE}.log"; }

case "${1:-}" in
  env)      cmd_env ;;
  env-dev)  cmd_env_dev ;;
  ci)       bash "${ROOT}/devops/ci.sh" ;;
  dev)      cmd_dev ;;
  start)    bash "${ROOT}/devops/start.sh" ;;
  stop)     bash "${ROOT}/devops/stop.sh" ;;
  restart)  bash "${ROOT}/devops/stop.sh" && bash "${ROOT}/devops/start.sh" ;;
  status)   cmd_status ;;
  logs)     cmd_logs ;;
  *)
    sed -n '2,12p' "$0"
    exit 1
    ;;
esac

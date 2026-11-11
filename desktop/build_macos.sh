#!/usr/bin/env bash
# 构建 macOS 原生应用：dist/AtlasX.app
#
# 依赖（仅构建时）：pyinstaller、pywebview、pillow（生成图标）
# 运行：bash desktop/build_macos.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  echo "找不到 Python：$PY（可用 PYTHON=... 指定）" >&2
  exit 1
fi

if ! "$PY" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "安装构建依赖 pyinstaller …"
  "$PY" -m pip install --trusted-host mirrors.tuna.tsinghua.edu.cn pyinstaller
fi

# 前端必须先构建，PyInstaller 会把 web/dist 一起收进 .app；避免打包到旧产物。
if ! command -v npm >/dev/null 2>&1; then
  echo "找不到 npm：打包前需要 Node（仅构建期）来构建 frontend/。" >&2
  exit 1
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "安装前端依赖 …"
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund)
fi
echo "构建前端 …"
(cd "$ROOT/frontend" && npm run build)
if [ ! -f "$ROOT/web/dist/index.html" ]; then
  echo "前端构建产物缺失：web/dist/index.html" >&2
  exit 1
fi

if [ ! -f "$ROOT/desktop/Atlas.icns" ]; then
  "$PY" "$ROOT/desktop/make_icon.py"
fi

rm -rf "$ROOT/build/desktop" "$ROOT/dist/AtlasX.app" "$ROOT/dist/AtlasX"

# PyInstaller 默认把缓存写到 ~/Library/Application Support/pyinstaller；
# 沙箱 / CI / 只读 HOME 下会直接 PermissionError。统一放到仓库 build 下，
# 既避免权限问题，也让缓存随仓库清理。
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT/build/pyinstaller-cache}"

"$PY" -m PyInstaller \
  --noconfirm --clean \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build/desktop" \
  "$ROOT/desktop/AtlasX.spec"

# 本地 ad-hoc 签名：没有开发者证书也能在这台 Mac 上直接打开。
# 分发到其它机器前请用 Developer ID 正式签名 + notarize。
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$ROOT/dist/AtlasX.app" >/dev/null 2>&1 || true
fi

# ── 生成 DMG（拖入 Applications 即安装）──────────────────────────────
VERSION="$("$PY" - <<'PY'
import re
from pathlib import Path
text = Path("desktop/AtlasX.spec").read_text(encoding="utf-8")
m = re.search(r'"CFBundleShortVersionString":\s*"([^"]+)"', text)
print(m.group(1) if m else Path("VERSION").read_text(encoding="utf-8").strip())
PY
)"
DMG="$ROOT/dist/AtlasX-${VERSION}.dmg"
STAGE="$ROOT/build/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto "$ROOT/dist/AtlasX.app" "$STAGE/AtlasX.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "AtlasX" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo ""
echo "✓ 构建完成：$ROOT/dist/AtlasX.app"
echo "✓ DMG：$DMG"
echo "  双击打开，或：open \"$ROOT/dist/AtlasX.app\""
echo "  数据目录：~/Library/Application Support/Atlas（沿用旧名，兼容已有会话）"

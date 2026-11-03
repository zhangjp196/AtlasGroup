# AtlasX for macOS

把 AtlasX 打包成一个原生 macOS 应用（`.app`）：本地 FastAPI 服务跑在后台线程，
界面用系统 WKWebView（pywebview）承载，带原生窗口、菜单、Dock 图标与文件选择器。

## 一键构建

```bash
# 构建依赖（只需一次）
.venv/bin/pip install pywebview pyinstaller pillow

# 打包
bash desktop/build_macos.sh

# 运行
open dist/AtlasX.app
```

产物：
- `dist/AtlasX.app`（包含 Python 运行时、后端与静态前端，无需另装环境）
- `dist/AtlasX-3.3.7.dmg`（拖入 Applications 即安装）

## 从源码运行（开发）

```bash
.venv/bin/python desktop/launcher.py
```

使用独立数据目录，避免和开发库混在一起：

```bash
ATLAS_HOME="$HOME/Library/Application Support/AtlasX-dev" .venv/bin/python desktop/launcher.py
```

调试 WebView：

```bash
ATLAS_DEBUG=1 .venv/bin/python desktop/launcher.py
```

## 数据目录

> 数据目录**沿用旧名** `Atlas`：改名会把已有会话 / 附件 / 日志留在原地失联，
> 所以显示层改名（AtlasX）时这里保持不变。锁文件 `atlas.lock`、bundle id、
> Keychain service 同理，详见仓库根 README「命名与标识」。

首次启动会把内置配置复制到 `~/Library/Application Support/Atlas/`：

```
config/agent_zero/config.yaml   # 可手改；界面设置仍写入 sqlite
data/agent_zero/agent_zero.db   # 会话 / 消息 / 运行 / 设置
data/agent_zero/uploads/        # 对话附件
data/agent_zero/runtime.json    # 本次桌面端实际监听地址
logs/agent_zero.log
webview/                        # WKWebView 本地存储
window.json                     # 窗口大小 / 位置
```

桌面端使用 **127.0.0.1 的临时端口**，不会和 `devops/dev.sh` 的 10210 冲突，
也不监听局域网。菜单栏「AtlasX → 打开数据目录」可直接在 Finder 中查看。

## 原生能力

- 标准 macOS 窗口：红绿灯、最小化、缩放、全屏（`Ctrl+Cmd+F`）、窗口大小/位置跨启动记忆
- 启动时读取登录 shell 的 PATH，nvm / fnm / Homebrew 的 `npm` / `npx` / `node` / `python3` 在 .app 内可用
- Python 工具已移除；需要跑脚本时用 shell 调用 `python3`，不会把 AtlasX 自己当解释器再启动一个实例
- MCP 新增支持「安装命令」与 `command + args`，无 npm 时可手动指定绝对路径
- 默认菜单：`Cmd+Q` 退出、`Cmd+H` 隐藏、`Cmd+C/V` 复制粘贴、View 菜单全屏/缩放
- 附件选择走系统 `NSOpenPanel`（`<input type=file>` 在 WKWebView 中即原生面板）
- 添加工作区时可点文件夹旁的图标，用系统文件夹选择器直接定位
- 外部链接在默认浏览器打开；下载交给系统
- Dock 图标 / 应用名 / 版本来自 `desktop/Atlas.icns` 与 `desktop/AtlasX.spec`
- 关窗时优雅停止 uvicorn；正在执行的 run 状态留在 sqlite，下次启动会恢复/回收

前端通过 `window.pywebview.api` 使用原生能力（源码运行与打包运行都可用）：

```js
await window.pywebview.api.pick_folder()
await window.pywebview.api.reveal_data()
await window.pywebview.api.info()
```

## 签名与分发

构建脚本只做 **ad-hoc 签名**（`codesign --sign -`），适合本机使用。首次打开若被
Gatekeeper 拦截：

```bash
xattr -dr com.apple.quarantine "dist/AtlasX.app"
# 或右键 AtlasX.app → 打开
```

要分发给别人，需要 Apple Developer ID 证书：

```bash
codesign --force --deep --options runtime --sign "Developer ID Application: ..." dist/AtlasX.app
xcrun notarytool submit ... --wait
xcrun stapler staple dist/AtlasX.app
```

## 结构

```
desktop/
  launcher.py        # 桌面端入口：起服务 + 原生窗口 + JS bridge
  AtlasX.spec        # PyInstaller 打包配置（AtlasX-windows.spec 为 Windows）
  build_macos.sh     # 构建脚本
  make_icon.py       # 生成 icon.png / Atlas.icns
  Atlas.icns         # 应用图标（已生成）
  icon.png
```

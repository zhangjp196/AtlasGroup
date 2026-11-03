#!/usr/bin/env python3
"""AtlasX macOS 桌面端启动器：本地 FastAPI 服务 + 原生 WKWebView 窗口。

- 数据目录：`~/Library/Application Support/Atlas`（沿用旧名，兼容已有会话；可用 `ATLAS_HOME` 覆盖）
- 首次启动把内置 `config/agent_zero/config.yaml` 复制到数据目录，之后可手改
- 服务监听 127.0.0.1 的临时端口，不占用 10210，也不暴露到局域网
- 原生窗口：标准红绿灯 / 全屏 / 缩放 / 关闭；窗口大小位置跨启动记忆
- 原生能力（暴露给前端 `window.pywebview.api`）：系统文件夹选择器、打开数据目录、
  在浏览器打开、应用信息、退出
- 关窗时优雅停止 uvicorn（Uvicorn Server.should_exit）
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Optional
from pathlib import Path

# 打包后 pydantic 会按 entry point 加载本机装过的 logfire 插件；该插件用
# inspect.getsource 读源码，在 PyInstaller 里没有 .py 会抛 OSError，导致启动即崩。
# AtlasX 不做 logfire 埋点，用官方开关禁用（对源码运行同样无副作用）。
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")

APP_NAME = "AtlasX"
# 数据目录沿用旧名：改名会让已有会话 / 附件 / 日志失联；锁名同理（两版同目录时
# 仍能互斥）。标识层迁移是独立决策，见 README「命名与标识」。
APP_HOME_DIR = "Atlas"

# 单实例锁的句柄：进程存活期间必须一直持有，进程退出/崩溃由内核自动释放。
_INSTANCE_LOCK = None


def _acquire_instance_lock(home: Path) -> bool:
    """同一数据目录只允许一个 AtlasX 进程（flock，崩溃后自动释放）。

    第二个实例必须直接退出而不是继续启动：它的启动恢复会把第一个实例正在跑的
    run 全部标记为失败，两个 worker 也会同时消费同一份 sqlite 队列。
    """
    global _INSTANCE_LOCK
    try:
        fh = open(home / "atlas.lock", "a+", encoding="utf-8")
    except OSError as e:
        print(f"[{APP_NAME}] 无法创建单实例锁: {e}", flush=True)
        return True  # 锁文件不可写时不阻塞启动（罕见，交给后面的端口/DB 报错）
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    _INSTANCE_LOCK = fh
    return True


def _focus_running_instance(home: Path) -> None:
    """尽力把已在运行的窗口带到前台；没有辅助功能权限时静默失败。"""
    pid = 0
    try:
        info = json.loads(
            (home / "data" / "agent_zero" / "runtime.json").read_text(encoding="utf-8"))
        pid = int(info.get("pid") or 0)
    except Exception:
        pass
    if pid > 1:
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to set frontmost of '
                 f'(first process whose unix id is {pid}) to true'],
                timeout=2, capture_output=True,
            )
        except Exception:
            pass
    try:
        subprocess.run(
            ["osascript", "-e",
             'display notification "AtlasX 已经在运行，已切换到该窗口" with title "AtlasX"'],
            timeout=2, capture_output=True,
        )
    except Exception:
        pass


def _resource_root() -> Path:
    """打包后指向 .app 内的资源目录；源码运行时指向仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[1]


def _read_version() -> str:
    try:
        text = (_resource_root() / "VERSION").read_text(encoding="utf-8").strip()
        return text or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = _read_version()


def _support_root() -> Path:
    raw = str(os.environ.get("ATLAS_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_HOME_DIR
    return Path.home() / "Library" / "Application Support" / APP_HOME_DIR


def _hydrate_path() -> None:
    """GUI .app 的 PATH 只有 /usr/bin:/bin…，拿不到 nvm/fnm/homebrew 的 npm。

    启动时问一次登录 shell 的 PATH，并合并到当前进程；这样 MCP / 技能安装
    能直接找到用户终端里的 npm / npx，而不必手动写绝对路径。
    """
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        proc = subprocess.run(
            [shell, "-ilc", 'printf %s "$PATH"'],
            capture_output=True, text=True, timeout=12,
        )
        lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        extra = lines[-1] if lines else ""
    except Exception:
        return
    if not extra:
        return
    parts = [p for p in extra.split(":") if p]
    for p in (os.environ.get("PATH") or "").split(":"):
        if p and p not in parts:
            parts.append(p)
    os.environ["PATH"] = ":".join(parts)


def _prepare_environment() -> tuple[Path, Path]:
    _hydrate_path()
    res = _resource_root()
    home = _support_root()

    for rel in ("config/agent_zero", "data/agent_zero", "logs", "webview"):
        (home / rel).mkdir(parents=True, exist_ok=True)

    cfg = home / "config" / "agent_zero" / "config.yaml"
    if not cfg.exists():
        src = res / "config" / "agent_zero" / "config.yaml"
        if src.is_file():
            shutil.copy2(src, cfg)

    os.environ["AI_PLATFORM_ROOT"] = str(home)
    os.environ["AI_PLATFORM_CONFIG_YAML"] = str(cfg)
    os.environ["AGENT_ZERO_WEB_DIR"] = str(res / "web")
    os.environ["AGENT_ZERO_LOGS_DIR"] = str(home / "logs")

    # 源码运行时把仓库根加进 sys.path；打包后 services 已在 bundle 里。
    if str(res) not in sys.path:
        sys.path.insert(0, str(res))
    return res, home


class AtlasXApi:
    """暴露给前端的原生能力（`window.pywebview.api.*`）。"""

    def __init__(self, home: Path, url: str) -> None:
        self.home = home
        self.url = url
        self.window = None

    def bind(self, window) -> None:
        self.window = window

    def pick_folder(self) -> str:
        """系统 NSOpenPanel（选择目录），取消返回空串。"""
        import webview

        if self.window is None:
            return ""
        try:
            result = self.window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=str(Path.home()),
                allow_multiple=False,
            )
        except Exception:
            return ""
        if not result:
            return ""
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    def reveal_data(self) -> bool:
        subprocess.Popen(["open", str(self.home)])
        return True

    def open_external(self, url: str) -> bool:
        target = str(url or "").strip()
        if target.startswith(("http://", "https://", "mailto:")):
            webbrowser.open(target)
            return True
        return False

    def info(self) -> dict:
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "home": str(self.home),
            "url": self.url,
            "frozen": bool(getattr(sys, "frozen", False)),
        }

    def quit(self) -> bool:
        if self.window is not None:
            self.window.destroy()
        return True


def _load_window_state(home: Path) -> dict:
    try:
        data = json.loads((home / "window.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _screen_visible_frame() -> Optional[tuple]:
    """当前主屏可用区域 (x, y, w, h)；拿不到就返回 None。"""
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import NSScreen

        f = NSScreen.mainScreen().visibleFrame()
        return (float(f.origin.x), float(f.origin.y),
                float(f.size.width), float(f.size.height))
    except Exception:
        return None


def _safe_window_state(state: dict) -> dict:
    """把记忆的窗口位置/尺寸夹到屏幕内。

    pywebview 6 的 Cocoa 实现里，窗口初始 frame 落在屏幕外时会在
    `windowDidMove_` 里拿到 `i.window=None`，直接 AttributeError 闪退。
    历史 window.json 又可能是「1920 宽 + x=屏幕右边缘」这种组合（换过显示器 /
    最大化过一次就会留下），所以每次启动都校验，宁可直接居中也不能崩。
    """
    width = int(state.get("width") or 1440)
    height = int(state.get("height") or 900)
    frame = _screen_visible_frame()
    if frame is None:
        return {"width": width, "height": height,
                "x": state.get("x"), "y": state.get("y")}
    sx, sy, sw, sh = frame
    width = max(960, min(width, int(sw)))
    height = max(640, min(height, int(sh)))
    x = state.get("x")
    y = state.get("y")
    if x is None or y is None:
        return {"width": width, "height": height, "x": None, "y": None}
    x = max(int(sx), min(int(x), int(sx + sw - width)))
    y = max(int(sy), min(int(y), int(sy + sh - height)))
    return {"width": width, "height": height, "x": x, "y": y}


def _save_window_state(home: Path, window) -> None:
    try:
        data = {"width": int(window.width), "height": int(window.height)}
        if window.x is not None:
            data["x"] = int(window.x)
        if window.y is not None:
            data["y"] = int(window.y)
        (home / "window.json").write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _start_server(holder: dict) -> None:
    """在当前线程起 uvicorn（桌面端用临时端口，避免和开发服务抢 10210）。"""
    from services.agent_zero.webapp import create_app, write_runtime

    app = create_app(
        config_yaml=os.environ["AI_PLATFORM_CONFIG_YAML"],
        web_dir=os.environ["AGENT_ZERO_WEB_DIR"],
        logs_dir=os.environ["AGENT_ZERO_LOGS_DIR"],
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2048)
    sock.set_inheritable(True)
    port = int(sock.getsockname()[1])
    url = f"http://127.0.0.1:{port}/"

    home = _support_root()
    write_runtime(str(home / "data" / "agent_zero" / "runtime.json"),
                  "127.0.0.1", port)

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        reload=False,
        access_log=False,
        log_level="warning",
        lifespan="on",
    ))
    holder["server"] = server
    holder["url"] = url

    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        name="atlas-uvicorn",
        daemon=True,
    )
    holder["thread"] = thread
    thread.start()

    # 等 startup 完成：最多 30s；失败就让调用方报错。
    for _ in range(600):
        if server.started:
            return
        if not thread.is_alive():
            break
        time.sleep(0.05)
    raise RuntimeError(
        f"{APP_NAME} 服务启动失败，请查看 Application Support/{APP_HOME_DIR}/logs/agent_zero.log")


def _stop_server(holder: dict, timeout: float = 8.0) -> None:
    server = holder.get("server")
    if server is not None:
        server.should_exit = True
    thread = holder.get("thread")
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)


def _set_dock_icon(res: Path) -> None:
    """源码运行时也给 Dock 一个图标（打包后由 .app 的 Info.plist 接管）。"""
    if sys.platform != "darwin":
        return
    candidates = [res / "desktop" / "Atlas.icns", res / "desktop" / "icon.png"]
    icon = next((p for p in candidates if p.is_file()), None)
    if icon is None:
        return
    try:
        from AppKit import NSApplication, NSImage

        image = NSImage.alloc().initWithContentsOfFile_(str(icon))
        if image is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        pass


def _native_menu(api: AtlasXApi, holder: dict):
    from webview.menu import Menu, MenuAction, MenuSeparator

    return [
        Menu("__app__", [
            MenuAction("打开数据目录", api.reveal_data),
            MenuAction("在浏览器中打开",
                       lambda: webbrowser.open(holder.get("url") or "")),
            MenuSeparator(),
        ]),
    ]


def run_desktop() -> int:
    res, home = _prepare_environment()

    # 单实例：必须在建 app / 启动恢复之前拿到锁，否则第二个进程会误杀正在跑的 run。
    if not _acquire_instance_lock(home):
        _focus_running_instance(home)
        return 0

    # 无窗口冒烟模式：只起服务并打印 URL（CI / 构建自检用）
    if os.environ.get("ATLAS_SMOKE"):
        holder: dict = {}
        _start_server(holder)
        print(holder["url"], flush=True)
        _stop_server(holder)
        return 0

    holder = {}
    _start_server(holder)

    import webview

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["SHOW_DEFAULT_MENUS"] = True

    api = AtlasXApi(home, holder["url"])
    state = _safe_window_state(_load_window_state(home))
    window = webview.create_window(
        APP_NAME,
        holder["url"],
        js_api=api,
        width=int(state.get("width") or 1440),
        height=int(state.get("height") or 900),
        x=state.get("x"),
        y=state.get("y"),
        min_size=(960, 640),
        resizable=True,
        fullscreen=False,
        background_color="#0b0d12",
        text_select=True,
        zoomable=True,
        confirm_close=False,
        menu=_native_menu(api, holder) if sys.platform == "darwin" else None,
    )
    if window is None:
        _stop_server(holder)
        return 1
    api.bind(window)

    def _on_closing(*_args) -> None:
        _save_window_state(home, window)
        _stop_server(holder)

    window.events.closing += _on_closing

    _set_dock_icon(res)
    try:
        webview.start(
            debug=bool(os.environ.get("ATLAS_DEBUG")),
            private_mode=False,
            storage_path=str(home / "webview"),
        )
    finally:
        _stop_server(holder)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_desktop())

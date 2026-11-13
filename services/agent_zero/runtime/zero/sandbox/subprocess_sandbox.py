"""子进程沙箱（P0 默认档；第一档）。

约束：cwd = 工作区（FS 边界）；env 剥离代理变量（P0 默认断网尽力实现，
P1 上 gVisor / microVM 实现真正的默认断网）；超时 kill；输出截断 64KB。
"""

from __future__ import annotations

import asyncio
import locale
import logging
import os
import shutil
import signal
import subprocess
from typing import Callable, Dict, Optional

from services.agent_zero.runtime.zero.sandbox.base import Sandbox, SandboxResult

logger = logging.getLogger(__name__)

MAX_OUTPUT = 64 * 1024
IS_WINDOWS = os.name == "nt"

# 进程已退出、管道仍未 EOF 时的收尾等待窗口（秒）。
# 有后台子进程继承 stdout 时（`cmd &` / `tail -f` / `| tail`）管道永远不会关，
# `communicate()` 会无限等——这里只等这么久，随后判定停滞并返回已读到的输出。
_PIPE_LINGER_S = 2.0

# Windows 的 PowerShell / cmd 重定向输出时默认用控制台代码页（中文系统是 GBK），
# 直接按 UTF-8 解码会乱码。启动时先把输出编码钉成 UTF-8，再执行用户命令。
_PS_UTF8_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$OutputEncoding = [System.Text.Encoding]::UTF8; "
)
_CMD_UTF8_PREAMBLE = "chcp 65001 >nul & "


def shell_argv(cmd: str) -> list:
    """按平台选 shell：Windows 用 PowerShell（退回 cmd），其它用 bash。"""
    if IS_WINDOWS:
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            return [ps, "-NoProfile", "-NonInteractive", "-Command",
                    _PS_UTF8_PREAMBLE + cmd]
        return [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c",
                _CMD_UTF8_PREAMBLE + cmd]
    return [shutil.which("bash") or "/bin/bash", "-c", cmd]


def decode_output(raw: bytes) -> str:
    """UTF-8 优先；Windows 上被强制 UTF-8 之前的老输出回落到系统代码页。"""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        return raw.decode(encoding, "replace")


def _clean_env() -> Dict[str, str]:
    """剥离代理 / 路径注入风险变量；保留基础环境。"""
    drop = ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "PYTHONPATH")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # 子进程里的 Python 也按 UTF-8 输出（Windows 默认走代码页，中文会乱码）
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


async def _kill_tree_async(proc) -> None:
    """杀掉整个进程树（shell 及其子进程），不只杀直接子进程。

    Windows 的 taskkill 也走异步子进程：旧实现用 `subprocess.run(timeout=5)`
    同步阻塞事件循环，点「停止」时反而会卡住几秒。
    """
    if IS_WINDOWS:
        taskkill = shutil.which("taskkill") or "taskkill"
        try:
            killer = await asyncio.create_subprocess_exec(
                taskkill, "/F", "/T", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=5)
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def process_exit_code(proc) -> Optional[int]:
    """探测子进程是否已退出，返回退出码（未退出返回 None）。

    **不能用 `proc.wait()`**：CPython 的 `Process.wait()` 在子进程退出后仍会
    等 stdout/stderr 的 EOF 才返回。命令把长驻进程放到后台（`cmd &`、`nohup`、
    `--watch`）时，管道写端被子进程继承，EOF 永远不来，`wait()` 就永远不返回
    ——`bash x.sh | tail -25` 挂 40 分钟就是这么来的（2026-10-29 现场）。

    这里改读 transport 的返回码：它由 SIGCHLD 直接写入，与管道无关。
    """
    rc = getattr(proc, "returncode", None)
    if rc is not None:
        return int(rc)
    transport = getattr(proc, "_transport", None)
    getter = getattr(transport, "get_returncode", None) if transport else None
    if getter is None:
        return None
    try:
        rc = getter()
    except Exception:
        return None
    return int(rc) if rc is not None else None


async def _wait_exit(proc, timeout: float = 3.0) -> Optional[int]:
    """在 timeout 秒内等进程退出（只等退出，不等管道 EOF）。"""
    deadline = asyncio.get_event_loop().time() + max(0.0, timeout)
    while True:
        rc = process_exit_code(proc)
        if rc is not None:
            return rc
        if asyncio.get_event_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.05)


class SubprocessSandbox(Sandbox):
    """子进程执行（python / shell），cwd 限定工作区。"""

    def __init__(self, workspace, timeout_s: float = 60.0,
                 idle_timeout_s: float = 0.0, *,
                 on_output: Optional[Callable[[str, bytes], None]] = None):
        super().__init__(workspace, timeout_s)
        # 无总超时（timeout_s=0）时的兜底：连续这么久没有任何输出就判定挂住。
        # 0 = 不判定（老行为）。由 config 的 tools.shell_idle_timeout_s 控制。
        self.idle_timeout_s = float(idle_timeout_s or 0)
        self.on_output = on_output

    async def run_shell(self, cmd: str, timeout: Optional[float] = None) -> SandboxResult:
        """执行 shell 命令（macOS/Linux: bash -c；Windows: PowerShell / cmd）。"""
        timeout = timeout or self.timeout_s
        self.workspace.mkdir(parents=True, exist_ok=True)
        return await self._run(shell_argv(cmd), timeout)

    async def _run(self, argv: list, timeout: float) -> SandboxResult:
        t0 = asyncio.get_event_loop().time()
        kwargs = {}
        if IS_WINDOWS:
            # Windows 没有进程组/setsid：用新进程组 + taskkill /T 回收整棵树。
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # 独立进程组：强制停止 / 超时时可以连同 shell 子进程一起杀掉。
            kwargs["start_new_session"] = True
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.workspace),
            env=_clean_env(),
            # stdin 关掉：命令若等待输入会立刻拿到 EOF，而不是无声干等。
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        timed_out = False
        idle_stalled = False
        pipe_stalled = False
        try:
            if timeout and timeout > 0:
                out_b, err_b, pipe_stalled, idle_stalled = await asyncio.wait_for(
                    self._drain(proc), timeout=timeout)
            else:
                out_b, err_b, pipe_stalled, idle_stalled = await self._drain(proc)
        except asyncio.TimeoutError:
            timed_out = True
            await _kill_tree_async(proc)
            await _wait_exit(proc, 3.0)
            out_b, err_b = b"", b"killed: timeout"
        except asyncio.CancelledError:
            # 运行被强制停止：先杀掉整个进程组，再让取消继续向上传播。
            await _kill_tree_async(proc)
            await _wait_exit(proc, 3.0)
            raise
        duration = asyncio.get_event_loop().time() - t0
        out = decode_output(out_b)
        err = decode_output(err_b)
        if idle_stalled:
            # 空闲兜底命中：命令一个字都没输出地挂住，已杀掉进程组。
            timed_out = True
            err = (err or "") + (
                f"\n[atlas] 命令连续 {int(self.idle_timeout_s)}s 没有任何输出，"
                "判定挂住并已终止（进程组一并回收）。如果它本该长时间静默运行"
                "（如构建），请在命令里输出进度，或用重定向后台运行"
                "（`cmd >out.log 2>&1 </dev/null &`），之后用 tool_job / 读文件看结果。\n"
            )
        elif pipe_stalled:
            # 进程已退出，但某个子进程仍握着输出管道（`&` / `tail -f` / `| tail`）。
            # 不再无限等 EOF，如实返回已读到的输出并标注原因。
            err = (err or "") + (
                "\n[atlas] 命令进程已退出，但其子进程仍持有 stdout/stderr 管道，"
                "输出读取被判定为停滞（不再无限等待）。常见原因：命令把长驻进程"
                "放到后台（如 `&`、`nohup`、`--watch`、`tail -f`），或用了 "
                "`| tail` / `| head` 这类管道。请改用不带管道的形式，或显式重定向"
                "（`cmd >out.log 2>&1 </dev/null &`）。\n"
            )
        # 管道停滞时后台子进程仍握着 fd：主动回收 transport，避免
        # 「Event loop is closed」的 __del__ 噪音（结果不受影响）。
        if pipe_stalled:
            if self.on_output is not None:
                await _kill_tree_async(proc)
            try:
                transport = getattr(proc, "_transport", None)
                if transport is not None:
                    transport.close()
            except Exception:
                pass
        truncated = len(out_b) > MAX_OUTPUT or len(err_b) > MAX_OUTPUT
        return SandboxResult(
            ok=(process_exit_code(proc) == 0) and not timed_out,
            exit_code=process_exit_code(proc) or 0,
            stdout=out[:MAX_OUTPUT],
            stderr=err[:MAX_OUTPUT],
            timed_out=timed_out,
            duration_s=round(duration, 3),
            truncated=truncated,
            pipe_stalled=pipe_stalled,
        )

    async def _drain(self, proc) -> tuple:
        """读输出直到进程退出**且**管道结束，返回 (out, err, pipe_stalled, idle_stalled)。

        关键点（2026-10-29 现场教训）：

        1. **判定进程退出不看管道**：`proc.wait()` / `communicate()` 在子进程
           退出后仍会等 stdio EOF。命令把长驻进程放到后台时（`cmd &`、`nohup`、
           `--watch`、`| tail`），写端被子进程继承，EOF 永远不来，于是这条
           命令「永远不结束」，整轮 run 静默到用户手动停止。这里用
           `process_exit_code()`（transport 返回码，SIGCHLD 直接写入）判定。
        2. **退出后只给管道 `_PIPE_LINGER_S` 收尾窗口**：窗口内拿不到 EOF 就
           判定停滞，把已读到的内容如实返回。
        3. **`idle_timeout_s` 兜底**：连续这么久 stdout/stderr 都没有新字节、
           进程仍未退出 → 杀进程组（防止「命令还在跑但什么都不做」）。
        """

        async def _pump(stream, sink, channel: str) -> None:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return
                if self.on_output is not None:
                    self.on_output(channel, chunk)
                remaining = MAX_OUTPUT + 1 - len(sink)
                if remaining > 0:
                    sink.extend(chunk[:remaining])
                state["at"] = asyncio.get_event_loop().time()

        out_chunks = bytearray()
        err_chunks = bytearray()
        state = {"at": asyncio.get_event_loop().time()}
        out_task = asyncio.ensure_future(_pump(proc.stdout, out_chunks, "stdout"))
        err_task = asyncio.ensure_future(_pump(proc.stderr, err_chunks, "stderr"))
        idle_limit = float(self.idle_timeout_s or 0)
        idle_stalled = False
        # 等进程退出：轮询返回码（不看管道），顺带做空闲兜底判定。
        pipe_stalled = False
        try:
            while process_exit_code(proc) is None:
                await asyncio.sleep(0.1)
                if idle_limit > 0 and (
                        asyncio.get_event_loop().time() - state["at"] >= idle_limit):
                    idle_stalled = True
                    await _kill_tree_async(proc)
                    await _wait_exit(proc, 3.0)
                    break
            if not idle_stalled:
                _, pending = await asyncio.wait((out_task, err_task), timeout=_PIPE_LINGER_S)
                pipe_stalled = bool(pending)
        finally:
            for t in (out_task, err_task):
                if not t.done():
                    t.cancel()
            await asyncio.gather(out_task, err_task, return_exceptions=True)
        return bytes(out_chunks), bytes(err_chunks), pipe_stalled, idle_stalled

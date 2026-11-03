"""沙箱抽象（分档 subprocess / gVisor / microVM / WASM）。

P0 仅子进程档；更高档位接口预留。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxResult:
    ok: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_s: float = 0.0
    truncated: bool = False
    # 进程已退出、但子进程仍持有输出管道（`&` / `tail -f` / `| tail`）：
    # 输出在停滞窗口内被回收，返回 false 以免把「读不到 EOF」当成命令失败。
    pipe_stalled: bool = False


class Sandbox:
    """沙箱执行器基类（P0：SubprocessSandbox）。"""

    def __init__(self, workspace: Path, timeout_s: float = 60.0):
        self.workspace = Path(workspace)
        self.timeout_s = timeout_s


class SandboxError(Exception):
    """沙箱执行异常。"""

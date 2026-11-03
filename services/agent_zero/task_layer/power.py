"""运行期防睡眠：有 run 在跑时阻止系统空闲睡眠。

## 为什么

macOS 在「一段时间无用户输入」后会进入空闲睡眠（`pmset` 里能看到
`Sleep → DarkWake → Sleep` 的连续记录）。整机睡过去，Python 进程被冻住，
对话 run 表面上是「仍在进行中」，实际上十几分钟没有任何进展——醒来后一切
照常继续，于是用户看到的是「模型卡死了 13 分钟」（2026-10-29 现场：
`thinking→thinking` 空档 492s、单轮 llm_ms 919s，与 pmset 日志精确对齐）。

## 做法

运行计数 > 0 时启动一个防睡眠子进程，计数归零就停：

- macOS：`caffeinate -i -s -w <本进程 pid>`（`-i` 阻止空闲睡眠、`-s` 阻止
  系统睡眠，`-w` 跟随本进程退出自动结束，不留孤儿）
- Linux：`systemd-inhibit --what=idle:sleep --mode=block`（存在才用）
- Windows / 其它：不动作（桌面端目前只在 macOS 分发）

**失败必须静默**：防睡眠只是体验优化，拿不到就退化成老行为，不能因此
让对话跑不起来。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_state = {"count": 0, "proc": None, "cmd": ""}


def _spawn() -> None:
    """启动防睡眠子进程（已在跑 / 平台不支持则直接返回）。"""
    if _state["proc"] is not None and _state["proc"].poll() is None:
        return
    cmd: list[str] = []
    if sys.platform == "darwin":
        caffeinate = shutil.which("caffeinate") or "/usr/bin/caffeinate"
        if os.path.isfile(caffeinate):
            # -w 跟着本进程走：AtlasX 退出 / 崩溃时 caffeinate 自己结束。
            cmd = [caffeinate, "-i", "-s", "-w", str(os.getpid())]
    elif sys.platform.startswith("linux"):
        inhibitor = shutil.which("systemd-inhibit")
        if inhibitor:
            cmd = [inhibitor, "--what=idle:sleep", "--mode=block",
                   "--who=AtlasX", "--why=conversation run in progress", "cat"]
    if not cmd:
        return
    try:
        _state["proc"] = subprocess.Popen(          # noqa: S603
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _state["cmd"] = " ".join(cmd[:3])
        logger.info("已开启运行期防睡眠: %s", _state["cmd"])
    except Exception:
        logger.debug("开启防睡眠失败（忽略）", exc_info=True)
        _state["proc"] = None


def _stop() -> None:
    proc = _state.get("proc")
    _state["proc"] = None
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        logger.info("已关闭运行期防睡眠")
    except Exception:
        logger.debug("关闭防睡眠失败（忽略）", exc_info=True)


def acquire() -> None:
    """一行 run 开始：计数 +1，第一个进入者启动防睡眠。"""
    with _LOCK:
        _state["count"] += 1
        if _state["count"] == 1:
            _spawn()


def release() -> None:
    """一行 run 结束：计数 -1，归零时停止防睡眠。"""
    with _LOCK:
        _state["count"] = max(0, _state["count"] - 1)
        if _state["count"] == 0:
            _stop()


def status() -> dict:
    with _LOCK:
        proc = _state.get("proc")
        return {
            "active_runs": _state["count"],
            "sleep_blocked": bool(proc is not None and proc.poll() is None),
            "command": _state.get("cmd") or "",
        }

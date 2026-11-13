"""敏感信息存储：macOS Keychain 优先，失败时退回 sqlite 明文。

- 服务名固定 `com.atlas.agentzero`，account 形如 `llm-default` / `llm-<code>`；
- sqlite 里只保留 `keychain:<ref>` 占位符，日志与诊断包里不会出现明文；
- 非 macOS / 没有 security 命令时直接返回不可用，调用方保留原值。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SERVICE = "com.atlas.agentzero"
PREFIX = "keychain:"
_TIMEOUT_S = 5.0
_CACHE_TTL_S = 60.0
# 每次读配置都会解析 api_key；security 是子进程调用，必须缓存，否则每次
# LLM 请求都要 fork 一次。写入时立即刷新缓存。
_cache: Dict[str, Tuple[float, Optional[str]]] = {}


def available() -> bool:
    return sys.platform == "darwin" and bool(shutil.which("security"))


def set_secret(name: str, value: str) -> bool:
    if not available() or not value:
        return False
    try:
        # -U：已存在则更新
        proc = subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", SERVICE, "-a", str(name), "-w", str(value)],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if proc.returncode != 0:
            logger.warning("写入 Keychain 失败 %s: %s", name, (proc.stderr or "").strip())
            return False
        _cache[str(name)] = (time.time() + _CACHE_TTL_S, str(value))
        return True
    except Exception as e:
        logger.warning("写入 Keychain 异常 %s: %s", name, e)
        return False


def get_secret(name: str) -> Optional[str]:
    key = str(name)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    if not available():
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE,
             "-a", str(name), "-w"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if proc.returncode != 0:
            return None
        value = (proc.stdout or "").strip() or None
        _cache[key] = (time.time() + _CACHE_TTL_S, value)
        return value
    except Exception:
        return None


def delete_secret(name: str) -> None:
    _cache.pop(str(name), None)
    if available():
        try:
            subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", str(name)],
                           capture_output=True, timeout=_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            logger.warning("无法删除 Keychain 条目 %s", name)



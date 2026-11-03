"""本地设置存储（app_settings 表）。

LLM 覆盖写入 sqlite；`runtime/zero/llm` 每次读配置时合并这里的值，
因此改完设置不需要重启服务。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores import secret_store
from services.agent_zero.stores.zero_store import _q

logger = logging.getLogger(__name__)

# LLM 配置版本号：每当 llm 覆盖被写入就自增，供 runtime/zero/llm 的配置快照缓存
# 立即失效（不必等 TTL，也不必在每次 LLM 请求前查一遍 sqlite）。
_llm_version = 0


def llm_version() -> int:
    return _llm_version


def get(key: str, default: Any = None) -> Any:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        raw = db.execute(
            text(f"SELECT value FROM {_q('app_settings')} WHERE key = :k"), {"k": key}
        ).scalar()
    if raw is None:
        return default
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return default
    return raw


def _protect_llm_keys(payload: Any) -> Any:
    """把 llm 覆盖里的 api_key 搬进 Keychain，sqlite 只留占位符。"""
    if not isinstance(payload, dict) or not secret_store.available():
        return payload
    data = json.loads(json.dumps(payload, ensure_ascii=False))
    refs = [("llm-default", data)]
    models = data.get("models")
    if isinstance(models, dict):
        refs += [(f"llm-{code}", entry) for code, entry in models.items()
                 if isinstance(entry, dict)]
    subscriptions = data.get("subscriptions")
    if isinstance(subscriptions, dict):
        refs += [(f"llm-sub-{sid}", entry) for sid, entry in subscriptions.items()
                 if isinstance(entry, dict)]
    for ref, entry in refs:
        key = entry.get("api_key")
        if isinstance(key, str) and key.strip() and not key.startswith(secret_store.PREFIX):
            if secret_store.set_secret(ref, key):
                entry["api_key"] = secret_store.PREFIX + ref
    return data


def _resolve_llm_keys(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    refs = [payload]
    models = payload.get("models")
    if isinstance(models, dict):
        refs += [entry for entry in models.values() if isinstance(entry, dict)]
    subscriptions = payload.get("subscriptions")
    if isinstance(subscriptions, dict):
        refs += [entry for entry in subscriptions.values() if isinstance(entry, dict)]
    for entry in refs:
        key = entry.get("api_key")
        if isinstance(key, str) and key.startswith(secret_store.PREFIX):
            entry["api_key"] = secret_store.get_secret(
                key[len(secret_store.PREFIX):]) or ""
    return payload


def migrate_plaintext_api_keys() -> bool:
    """启动时把历史明文 api_key 迁到 Keychain（幂等；失败则保持原样）。"""
    if not secret_store.available():
        return False
    raw = get("llm", {})
    if not isinstance(raw, dict):
        return False
    protected = _protect_llm_keys(raw)
    if protected != raw:
        put("llm", protected)
        logger.info("已把 sqlite 中的 LLM api_key 迁移到 macOS Keychain")
        return True
    return False


def put(key: str, value: Any, retries: int = 3) -> None:
    global _llm_version
    if key == "llm":
        value = _protect_llm_keys(value)
    SessionLocal = get_session_local()
    for attempt in range(retries):
        try:
            with SessionLocal() as db:
                db.execute(
                    text(f"INSERT INTO {_q('app_settings')} (key, value, updated_at) "
                         f"VALUES (:k, :v, datetime('now','localtime')) "
                         f"ON CONFLICT (key) DO UPDATE SET value = :v, "
                         f"updated_at = datetime('now','localtime')"),
                    {"k": key, "v": json.dumps(value, ensure_ascii=False)},
                )
                db.commit()
            if key == "llm":
                _llm_version += 1      # 让 LLM 配置快照立即失效
            return
        except Exception as e:
            # 写入撞车（另一个连接正在写）时重试，别把用户点「始终允许」的动作弄丢
            if attempt < retries - 1 and "locked" in str(e).lower():
                time.sleep(0.3 * (attempt + 1))
                continue
            raise


def llm_override() -> Dict[str, Any]:
    v = get("llm", {})
    return _resolve_llm_keys(v) if isinstance(v, dict) else {}

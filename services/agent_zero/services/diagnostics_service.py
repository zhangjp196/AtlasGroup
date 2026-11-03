"""诊断包：一个 zip 打包定位问题所需的最小信息。

内容：版本/系统、健康检查、24h 指标、脱敏设置、最近运行、备份信息、
最近日志（文本 + JSONL）。所有文本统一脱敏（api_key / Bearer / sk-*）。
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from services.agent_zero.services import backup_service, metrics_service
from services.agent_zero.version import APP_NAME

_MAX_LOG_BYTES = 2_000_000

_PATTERNS = (
    (re.compile(r'(?i)("?api[_-]?key"?\s*[:=]\s*)["\']?[A-Za-z0-9._\-]{6,}["\']?'),
     r'\1"***"'),
    (re.compile(r'(?i)(authorization\s*[:=]\s*bearer\s+)\S+'), r'\1***'),
    (re.compile(r'sk-[A-Za-z0-9_\-]{8,}'), 'sk-***'),
    (re.compile(r'(?i)(x-opencode-session\s*[:=]\s*)\S+'), r'\1***'),
)


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _logs_dir() -> Path:
    raw = str(os.environ.get("AGENT_ZERO_LOGS_DIR") or "").strip()
    if raw:
        return Path(raw)
    try:
        from services.shared.project_paths import get_project_root

        return Path(get_project_root()) / "logs"
    except Exception:
        return Path("logs")


def _tail(path: Path, limit: int = _MAX_LOG_BYTES) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                fh.readline()          # 丢掉截断的半行
            raw = fh.read()
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _settings_bundle() -> Dict[str, Any]:
    try:
        from services.agent_zero.runtime.zero import llm

        code = llm.get_active_model_code()
        return {
            "active_model": code,
            "model": llm.get_model_name(code),
            "protocol": llm._normalize_protocol(llm._resolve_llm_config(code).get("protocol")),
            "profiles": llm.list_model_profiles(),
            "thinking_levels": llm.get_thinking_levels_for(code),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _meta() -> Dict[str, Any]:
    return {
        "app": APP_NAME,
        "version": metrics_service._app_version(),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "schema_version": metrics_service.schema_version(),
        "db": metrics_service.db_stats(),
        "cwd": os.getcwd(),
    }


def build_bundle() -> bytes:
    logs = _logs_dir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps(_meta(), ensure_ascii=False, indent=2, default=str))
        z.writestr("health.json", json.dumps(metrics_service.health(), ensure_ascii=False,
                                            indent=2, default=str))
        z.writestr("metrics.json", json.dumps(metrics_service.run_metrics(24),
                                              ensure_ascii=False, indent=2, default=str))
        z.writestr("settings.json", json.dumps(_settings_bundle(), ensure_ascii=False,
                                               indent=2, default=str))
        z.writestr("runs.json", json.dumps(metrics_service.recent_runs(100),
                                           ensure_ascii=False, indent=2, default=str))
        z.writestr("backups.json", json.dumps(backup_service.stats(),
                                              ensure_ascii=False, indent=2, default=str))
        for name in ("agent_zero.log", "agent_zero.jsonl"):
            text = _tail(logs / name)
            if text:
                z.writestr(f"logs/{name}", redact(text))
    data = buf.getvalue()
    try:
        from services.agent_zero.services.events import log_event

        log_event("diagnostics.exported", size=len(data))
    except Exception:
        pass
    return data


def bundle_name() -> str:
    return f"atlasx-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"

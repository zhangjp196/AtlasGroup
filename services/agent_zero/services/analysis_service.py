"""分析文档落盘约定：默认集中到工作区 `.atlas/`，按类型 + 日期分目录。

模型在生成分析 / 调研 / 报告类文档时通常不需要关心路径；`save_analysis`
未显式给 `path` 时由这里生成：

    .atlas/<kind>/<YYYY-MM-DD>/<HHMM>-<标题>.md

显式给了 path 就完全尊重（仍受工作区边界约束）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict

from services.agent_zero.services import workspace_service

_ROOT = ".atlas"
_KINDS = {
    "analysis": "analysis",
    "analyse": "analysis",
    "report": "reports",
    "research": "research",
    "summary": "summaries",
    "note": "notes",
    "plan": "plans",
    "review": "reviews",
}
_EXTS = {"md", "markdown", "txt", "html", "json", "csv"}
_SAFE = re.compile(r"[^\w\u4e00-\u9fff.\-]+", re.UNICODE)


def _slug(text: str, fallback: str = "note") -> str:
    s = _SAFE.sub("-", str(text or "").strip()).strip("-._")
    s = re.sub(r"-{2,}", "-", s)
    return (s[:60] or fallback)


def _kind_dir(kind: str) -> str:
    k = str(kind or "analysis").strip().lower()
    if k in _KINDS:
        return _KINDS[k]
    return _SAFE.sub("-", k).strip("-._")[:24] or "analysis"


def default_relpath(title: str, kind: str = "analysis", ext: str = "md") -> str:
    ext = str(ext or "md").strip().lower().lstrip(".")
    if ext not in _EXTS:
        ext = "md"
    now = datetime.now()
    return f"{_ROOT}/{_kind_dir(kind)}/{now.strftime('%Y-%m-%d')}/" \
           f"{now.strftime('%H%M')}-{_slug(title)}.{ext}"


def save(ws_id: str, *, title: str, content: str, kind: str = "analysis",
         ext: str = "md", path: str = "") -> Dict[str, Any]:
    rel = str(path or "").strip()
    if not rel:
        rel = default_relpath(title, kind, ext)
    info = workspace_service.write_file(ws_id, rel, content)
    return {
        "ok": True,
        "path": info.get("path") or rel,
        "size": info.get("size", 0),
        "kind": _kind_dir(kind),
        "title": str(title or "").strip(),
    }

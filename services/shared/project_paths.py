"""项目根目录与 uploads/data 路径（与 services 同级，不依赖进程 cwd）。"""
from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

_PROJECT_ROOT: Path | None = None


def _default_root() -> Path:
    """可写根目录：桌面端通过 AI_PLATFORM_ROOT 指到 Application Support。"""
    import os

    env = str(os.environ.get("AI_PLATFORM_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def get_project_root() -> Path:
    """仓库根目录（CLI）/ 应用数据根目录（macOS 桌面端）。"""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = _default_root()
    return _PROJECT_ROOT


def resolve_under_project_root(path: PathLike) -> Path:
    """相对路径按项目根解析；绝对路径原样返回。"""
    p = Path(path)
    if p.is_absolute():
        return p
    return get_project_root() / p


def project_data(*parts: str) -> Path:
    return get_project_root().joinpath("data", *parts)


def resolved_sqlite_db_path(db_path: PathLike) -> str:
    """YAML 中 data/*.db 等相对路径 → 项目根下的绝对路径字符串。"""
    return str(resolve_under_project_root(db_path))



"""Workspace 服务：文件根目录解析与安全读写。

只有用户选定本地文件夹的工作区（``workspaces.root_path``）才有**工作目录**；默认
工作区是**纯聊天工作区**，没有用户文件夹。但工具仍要有一个落点：纯聊天工作区
用**托管目录**（``chat_root``，默认 ``data/agent_zero/workspace/<ws_id>``）作为
shell 的 cwd 与文件工具的根（见 ``exec_root``）。所有相对路径都会被解析到当前
根目录内，绝对路径和 ``..`` 逃逸一律拒绝。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 512 * 1024  # 512KB

WORKSPACE_KINDS = ("task", "group", "role")


class WorkspacePathError(ValueError):
    """路径非法或逃逸出 Workspace。"""


_WS_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def validate_workspace_kind(kind: str) -> str:
    k = (kind or "").strip()
    if k not in WORKSPACE_KINDS:
        raise WorkspacePathError(f"非法工作区类型: {kind!r}（允许: {'/'.join(WORKSPACE_KINDS)}）")
    return k


def validate_workspace_id(ws_id: str) -> str:
    i = (ws_id or "").strip()
    if not i or not _WS_ID_RE.match(i):
        raise WorkspacePathError(f"非法工作区 id: {ws_id!r}")
    return i


def _custom_root(ws_id: str) -> Optional[Path]:
    """读取用户选择的外部文件夹；没有配置则返回 None。"""
    try:
        from services.agent_zero.stores import zero_store

        ws = zero_store.get_workspace(ws_id)
    except Exception:
        return None
    raw = str((ws or {}).get("root_path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise WorkspacePathError(f"工作区目录必须是绝对路径: {raw}")
    try:
        path = path.resolve()
    except OSError as e:
        raise WorkspacePathError(f"工作区目录不可用: {raw}") from e
    if not path.is_dir():
        raise WorkspacePathError(f"工作区目录不存在: {raw}")
    return path


def has_working_dir(ws_id: str) -> bool:
    """该工作区有没有**用户选定的工作目录**。

    只有用户选定的本地文件夹（``workspaces.root_path``）才算工作目录。默认
    工作区没有 root_path，是**纯聊天工作区**：没有用户文件夹，但工具仍有一个
    托管目录可用（见 ``exec_root``）。
    """
    try:
        from services.agent_zero.stores import zero_store

        ws = zero_store.get_workspace(ws_id)
    except Exception:
        return False
    return bool(str((ws or {}).get("root_path") or "").strip())


def chat_root(ws_id: str) -> Path:
    """纯聊天工作区的**托管目录**（shell 的 cwd / 文件工具的相对路径根）。

    位置：``<data>/agent_zero/workspace/<ws_id>``，按需创建。它不是用户文件夹，
    也不会被当作用户的工作目录（``has_working_dir`` 仍为 False），只是让 shell
    与文件工具在纯聊天工作区里有一个安全的落点。
    """
    from services.agent_zero.config import get_settings

    validate_workspace_id(ws_id)
    root = get_settings().resolve_workspace_dir() / ws_id
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def exec_root(ws_id: str) -> Path:
    """工具执行根目录：有用户文件夹用它，纯聊天工作区用托管目录。"""
    custom = _custom_root(ws_id)
    if custom is not None:
        return custom
    return chat_root(ws_id)


def get_workspace_root(kind: str, ws_id: str) -> Path:
    """**用户选定**的工作目录；纯聊天工作区没有，直接报错。

    命令 / 钩子 / 版本控制这类「必须落在用户自己的仓库里」的能力用它；
    shell 与文件工具用 ``exec_root``（纯聊天工作区回退到托管目录）。
    """
    validate_workspace_id(ws_id)
    custom = _custom_root(ws_id)
    if custom is not None:
        return custom
    raise WorkspacePathError(
        "该工作区是纯聊天工作区，没有用户文件夹；需要读写项目文件请在「工作区」中"
        "添加一个指向本地文件夹的工作区。"
    )


def ensure_workspace(kind: str, ws_id: str, name: Optional[str] = None) -> Path:
    """确保工作区目录存在（只对有 root_path 的工作区有效）。"""
    validate_workspace_kind(kind)
    ws_root = get_workspace_root(kind, ws_id)
    ws_root.mkdir(parents=True, exist_ok=True)
    return ws_root


def resolve_workspace_path(ws_id: str, rel_path: Optional[str] = None) -> Path:
    """相对路径解析到执行根目录内（有用户文件夹用它，否则用托管目录）；禁止 .. 逃逸。"""
    ws_root = exec_root(ws_id)
    raw = (rel_path or "").strip() or "."
    candidate = Path(raw)
    if candidate.is_absolute():
        raise WorkspacePathError("仅允许工作区内的相对路径")
    try:
        resolved = (ws_root / candidate).resolve()
        resolved.relative_to(ws_root.resolve())
    except (ValueError, OSError) as e:
        raise WorkspacePathError("路径超出工作区范围") from e
    return resolved


def safe_relpath(ws_id: str, path: Path) -> str:
    """相对当前执行根目录的 posix 路径。"""
    ws_root = exec_root(ws_id)
    try:
        return path.resolve().relative_to(ws_root.resolve()).as_posix()
    except ValueError:
        return path.name


def list_dir(ws_id: str, rel_path: Optional[str] = None,
             include_hidden: bool = False) -> List[Dict[str, Any]]:
    """列出目录内容；include_hidden=True 时包含 . 开头的目录与文件（UI 文件树用）。"""
    target = resolve_workspace_path(ws_id, rel_path)
    if not target.exists():
        raise WorkspacePathError(f"路径不存在: {rel_path or '.'}")
    if not target.is_dir():
        raise WorkspacePathError(f"不是目录: {rel_path or '.'}")
    items = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if (not include_hidden and entry.name.startswith(".")
                and entry.name not in {".gitkeep"}):
            continue
        st = entry.stat()
        items.append({
            "name": entry.name,
            "path": safe_relpath(ws_id, entry),
            "type": "dir" if entry.is_dir() else "file",
            "size": 0 if entry.is_dir() else int(st.st_size),
        })
    return items


def read_file(ws_id: str, rel_path: str, *, max_bytes: int = MAX_READ_BYTES) -> Dict[str, Any]:
    """读取文件内容。"""
    target = resolve_workspace_path(ws_id, rel_path)
    if not target.exists() or not target.is_file():
        raise WorkspacePathError(f"文件不存在: {rel_path}")
    size = target.stat().st_size
    truncated = size > max_bytes
    data = target.read_bytes()[:max_bytes]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return {
        "path": safe_relpath(ws_id, target),
        "size": size,
        "truncated": truncated,
        "content": text,
        "encoding": "utf-8",
    }


def write_file(ws_id: str, rel_path: str, content: str, *,
               create_dirs: bool = True) -> Dict[str, Any]:
    """写入文件内容。"""
    target = resolve_workspace_path(ws_id, rel_path)
    if create_dirs:
        target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.is_dir():
        raise WorkspacePathError(f"目标是目录，无法写入: {rel_path}")
    data = content if isinstance(content, str) else str(content)
    target.write_text(data, encoding="utf-8")
    return {
        "path": safe_relpath(ws_id, target),
        "size": target.stat().st_size,
        "ok": True,
    }


def delete_file(ws_id: str, rel_path: str) -> Dict[str, Any]:
    """删除文件或目录（禁删工作区根）。"""
    target = resolve_workspace_path(ws_id, rel_path)
    if not target.exists():
        raise WorkspacePathError(f"路径不存在: {rel_path}")
    if target.resolve() == exec_root(ws_id).resolve():
        raise WorkspacePathError("禁止删除工作区根")
    if target.is_dir():
        import shutil

        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "path": rel_path}


def _safe_new_name(name: str) -> str:
    clean = str(name or "").strip()
    if not clean or clean in (".", ".."):
        raise WorkspacePathError("名称不能为空")
    if "/" in clean or "\\" in clean or "\x00" in clean:
        raise WorkspacePathError("名称不能包含路径分隔符")
    return clean


def rename_entry(ws_id: str, rel_path: str, new_name: str) -> Dict[str, Any]:
    """重命名文件 / 目录（仅当前工作区内，不能覆盖已有条目）。"""
    target = resolve_workspace_path(ws_id, rel_path)
    if not target.exists():
        raise WorkspacePathError(f"路径不存在: {rel_path}")
    if target.resolve() == exec_root(ws_id).resolve():
        raise WorkspacePathError("禁止重命名工作区根")
    name = _safe_new_name(new_name)
    dest = target.with_name(name)
    try:
        dest.resolve().relative_to(exec_root(ws_id).resolve())
    except (ValueError, OSError) as e:
        raise WorkspacePathError("路径超出工作区范围") from e
    if dest.exists():
        raise WorkspacePathError(f"目标已存在: {name}")
    target.rename(dest)
    return {"ok": True, "path": safe_relpath(ws_id, dest), "name": name}


def create_directory(ws_id: str, parent_rel: Optional[str], name: str) -> Dict[str, Any]:
    """在指定目录下新建文件夹。"""
    parent = resolve_workspace_path(ws_id, parent_rel)
    if not parent.exists() or not parent.is_dir():
        raise WorkspacePathError(f"不是目录: {parent_rel or '.'}")
    dest = parent / _safe_new_name(name)
    try:
        dest.resolve().relative_to(exec_root(ws_id).resolve())
    except (ValueError, OSError) as e:
        raise WorkspacePathError("路径超出工作区范围") from e
    if dest.exists():
        raise WorkspacePathError(f"已存在: {name}")
    dest.mkdir()
    return {"ok": True, "path": safe_relpath(ws_id, dest), "name": name}


def reveal_in_file_manager(ws_id: str, rel_path: str) -> Dict[str, Any]:
    """在系统文件管理器中定位文件 / 文件夹（只读操作）。"""
    import platform
    import subprocess

    target = resolve_workspace_path(ws_id, rel_path)
    if not target.exists():
        raise WorkspacePathError(f"路径不存在: {rel_path}")
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", str(target)])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            folder = target if target.is_dir() else target.parent
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as e:
        raise WorkspacePathError(f"无法打开文件管理器: {e}") from e
    return {"ok": True, "path": str(target)}


def resolve_file_for_download(ws_id: str, rel_path: str) -> Path:
    """解析下载文件路径。"""
    target = resolve_workspace_path(ws_id, rel_path)
    if not target.exists() or not target.is_file():
        raise WorkspacePathError(f"文件不存在: {rel_path}")
    return target


def build_workspace_zip(ws_id: str, paths: List[str]) -> Optional[bytes]:
    """将多个文件/目录打包为 zip。"""
    import io
    import zipfile

    ws_root = get_workspace_root("task", ws_id)
    if not paths:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in paths:
            target = resolve_workspace_path(ws_id, rel_path)
            if target.is_file():
                zf.write(target, target.relative_to(ws_root))
            elif target.is_dir():
                for file_path in target.rglob("*"):
                    if file_path.is_file():
                        zf.write(file_path, file_path.relative_to(ws_root))
    data = buf.getvalue()
    return data if data else None

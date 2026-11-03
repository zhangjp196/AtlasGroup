"""工作区文件检查点：按用户消息快照文件，支持回退（rewind）。

对齐 Claude Code 的 checkpoint 思路，但不依赖 git：

- 每条用户消息对应的 run **真正开始执行之前**创建一次快照；
- 文件内容按 sha256 去重存进 blob 仓库，清单（manifest）只记路径 / 哈希 / 大小；
- 回退可以只恢复文件、只截断对话，或两者都做；
- 文件回退会恢复被改 / 被删的文件，并按需删除检查点之后新建的文件；
- VCS / 依赖 / 构建 / 运行数据目录与超大文件不参与快照，避免把仓库级数据
  复制一遍。``.atlas`` 等应用元数据永不回退。

存储布局（默认 ``data/agent_zero/file_checkpoints``）::

    <workspace_id>/manifests/<checkpoint_id>.json
    <workspace_id>/blobs/<sha[:2]>/<sha>
    <workspace_id>/state.json           # mtime/size → sha 缓存

一个 manifest 大致如下::

    {
      "id": "ckpt_...", "workspace_id": "ws", "conversation_id": "conv_...",
      "message_id": "msg_...", "run_id": "run_...", "label": "修一下登录超时",
      "created_at": "2026-11-01T12:00:00", "created_ts": 1793500000.0,
      "root": "/abs/workspace", "complete": true,
      "files": [{"path": "src/app.py", "sha": "…", "size": 123, "mode": 420}],
      "file_count": 1, "total_bytes": 123, "skipped_large": 0
    }

``complete=false`` 表示受文件数 / 总量上限影响，有文件没进快照；这时回退只恢复
已知文件，**不会删除**新增文件，避免误删未记录的文件。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat as stat_mod
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from services.agent_zero.services import workspace_service
from services.agent_zero.stores import zero_store
from services.shared.project_paths import project_data

logger = logging.getLogger(__name__)

# 参与快照的上限：单文件 2MB、单次 20000 个文件 / 256MB。
# 超出会标记 complete=false，回退时不做删除。
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 20_000
MAX_TOTAL_BYTES = 256 * 1024 * 1024

# 每个会话保留的检查点数量（更早的自动清理，blob 随之回收）。
KEEP_PER_CONVERSATION = 50

# 不参与快照的目录 / 文件（按名字或相对路径匹配）。
IGNORED_DIRS = {
    ".git", ".svn", ".hg", ".atlas",
    "node_modules", ".venv", "venv", "env", ".tox",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".idea", ".vscode", "dist", "build", "target", "out",
    "logs", "log", "data", "tmp", "temp", "coverage", ".coverage",
}
IGNORED_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
IGNORED_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".o", ".a",
    ".swp", ".swo", ".tmp", ".temp", ".lock", ".log",
}

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(workspace_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(workspace_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[workspace_id] = lock
        return lock


# ── 存储路径 ──────────────────────────────────────────────────────────────

def storage_root() -> Path:
    """检查点仓库根目录（测试可 monkeypatch）。"""
    return project_data("agent_zero", "file_checkpoints")


def _ws_dir(workspace_id: str) -> Path:
    workspace_service.validate_workspace_id(workspace_id)
    return storage_root() / workspace_id


def _manifest_dir(workspace_id: str) -> Path:
    return _ws_dir(workspace_id) / "manifests"


def _blob_dir(workspace_id: str) -> Path:
    return _ws_dir(workspace_id) / "blobs"


def _blob_path(workspace_id: str, sha: str) -> Path:
    return _blob_dir(workspace_id) / sha[:2] / sha


def _state_path(workspace_id: str) -> Path:
    return _ws_dir(workspace_id) / "state.json"


def _checkpoint_id() -> str:
    return zero_store.new_id("ckpt")


def _now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── 基础工具 ──────────────────────────────────────────────────────────────

def _valid_id(value: str) -> str:
    cid = str(value or "").strip()
    if not _ID_RE.match(cid):
        raise ValueError(f"非法检查点 id: {value!r}")
    return cid


def _ignored_rel(rel: str) -> bool:
    """相对路径是否属于不参与快照的目录 / 文件。"""
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if any(p in IGNORED_DIRS for p in parts):
        return True
    name = parts[-1]
    return name in IGNORED_FILES or Path(name).suffix.lower() in IGNORED_SUFFIXES


def _walk_files(root: Path) -> Iterable[Tuple[str, Path]]:
    """遍历工作区里的普通文件（不跟随符号链接，跳过忽略目录）。"""
    stack: List[Tuple[str, Path]] = [("", root)]
    while stack:
        rel_dir, abs_dir = stack.pop()
        try:
            entries = sorted(os.scandir(abs_dir), key=lambda e: e.name)
        except OSError:
            continue
        subdirs: List[Tuple[str, Path]] = []
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if _ignored_rel(rel):
                        continue
                    subdirs.append((rel, Path(entry.path)))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if _ignored_rel(rel):
                continue
            yield rel, Path(entry.path)
        stack.extend(reversed(subdirs))


def _safe_join(root: Path, rel: str) -> Path:
    raw = str(rel or "").strip()
    if not raw or raw.startswith(("/", "\\")):
        raise ValueError(f"非法相对路径: {rel!r}")
    candidate = Path(raw)
    if candidate.is_absolute() or any(p == ".." for p in candidate.parts):
        raise ValueError(f"非法相对路径: {rel!r}")
    target = (root / candidate).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as e:
        raise ValueError(f"路径超出工作区: {rel!r}") from e
    return target


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _write_blob(workspace_id: str, sha: str, data: bytes) -> None:
    dest = _blob_path(workspace_id, sha)
    if dest.is_file():
        return
    _atomic_write_bytes(dest, data)


def _read_blob(workspace_id: str, sha: str) -> bytes:
    path = _blob_path(workspace_id, sha)
    if not path.is_file():
        raise ValueError(f"检查点数据缺失: {sha[:12]}")
    return path.read_bytes()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── 哈希缓存：stat 未变就不重新读文件 ─────────────────────────────────────

def _load_state(workspace_id: str, root: Path) -> Dict[str, Dict[str, Any]]:
    try:
        raw = json.loads(_state_path(workspace_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if str(raw.get("root") or "") != str(root):
        return {}
    files = raw.get("files")
    return files if isinstance(files, dict) else {}


def _save_state(workspace_id: str, root: Path,
                files: Dict[str, Dict[str, Any]]) -> None:
    payload = {"version": 1, "root": str(root), "files": files,
               "updated_at": _now_text()}
    try:
        _atomic_write_bytes(
            _state_path(workspace_id),
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
    except OSError:
        logger.debug("检查点缓存写入失败 ws=%s", workspace_id, exc_info=True)


def _scan_current(workspace_id: str, root: Path,
                  *, write_blobs: bool = False
                  ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int], bool]:
    """扫描当前工作区：path → {sha,size,mtime_ns}。

    ``write_blobs=True`` 时顺便把内容写进 blob 仓库（快照用）。
    返回 (files, skipped 计数, complete)。
    """
    cached = _load_state(workspace_id, root)
    files: Dict[str, Dict[str, Any]] = {}
    skipped_large = 0
    skipped_limit = 0
    total = 0
    complete = True
    for rel, path in _walk_files(root):
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_size > MAX_FILE_BYTES:
            skipped_large += 1
            continue
        if len(files) >= MAX_FILES or (total + st.st_size) > MAX_TOTAL_BYTES:
            skipped_limit += 1
            complete = False
            continue
        hit = cached.get(rel)
        if (isinstance(hit, dict) and int(hit.get("mtime_ns") or -1) == st.st_mtime_ns
                and int(hit.get("size") or -1) == st.st_size
                and str(hit.get("sha") or "")):
            sha = str(hit["sha"])
            if write_blobs and not _blob_path(workspace_id, sha).is_file():
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if _sha256(data) != sha:
                    sha = _sha256(data)
                _write_blob(workspace_id, sha, data)
        else:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            sha = _sha256(data)
            if write_blobs:
                _write_blob(workspace_id, sha, data)
        files[rel] = {"sha": sha, "size": st.st_size,
                      "mtime_ns": st.st_mtime_ns,
                      "mode": stat_mod.S_IMODE(st.st_mode)}
        total += st.st_size
    skipped = {"large": skipped_large, "limit": skipped_limit}
    return files, skipped, complete


# ── manifest 读写 ─────────────────────────────────────────────────────────

def _manifest_path(workspace_id: str, checkpoint_id: str) -> Path:
    return _manifest_dir(workspace_id) / f"{_valid_id(checkpoint_id)}.json"


def _read_manifests(workspace_id: str) -> List[Dict[str, Any]]:
    folder = _manifest_dir(workspace_id)
    if not folder.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id"):
            data["_path"] = str(path)
            out.append(data)
    return out


def _write_manifest(workspace_id: str, manifest: Dict[str, Any]) -> None:
    body = {k: v for k, v in manifest.items() if not k.startswith("_")}
    _atomic_write_bytes(
        _manifest_path(workspace_id, str(manifest["id"])),
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def _remove_manifest(manifest: Dict[str, Any]) -> None:
    path = str(manifest.get("_path") or "")
    if not path:
        return
    try:
        Path(path).unlink()
    except OSError:
        pass


def _prune_conversation(workspace_id: str, conversation_id: str,
                        *, keep: int = KEEP_PER_CONVERSATION) -> int:
    items = [m for m in _read_manifests(workspace_id)
             if str(m.get("conversation_id") or "") == conversation_id]
    items.sort(key=lambda m: float(m.get("created_ts") or 0), reverse=True)
    removed = 0
    for old in items[keep:]:
        _remove_manifest(old)
        removed += 1
    return removed


def _gc_blobs(workspace_id: str) -> int:
    """回收没有被任何 manifest 引用的 blob。"""
    referenced: Set[str] = set()
    for manifest in _read_manifests(workspace_id):
        for entry in manifest.get("files") or []:
            sha = str((entry or {}).get("sha") or "")
            if sha:
                referenced.add(sha)
    folder = _blob_dir(workspace_id)
    if not folder.is_dir():
        return 0
    removed = 0
    for path in folder.glob("*/*"):
        if not path.is_file():
            continue
        if path.name not in referenced:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    for path in folder.glob("*"):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return removed


# ── 对外：快照 ────────────────────────────────────────────────────────────

def _message_text(message_id: str) -> str:
    """取消息正文做检查点标签（失败返回空）。"""
    if not message_id:
        return ""
    try:
        from services.agent_zero.runtime.zero.gdag.store import GdagStore

        node = GdagStore().get_node(message_id)
    except Exception:
        return ""
    props = (node or {}).get("props") or {}
    text = str(props.get("content") or props.get("prompt") or "")
    return re.sub(r"\s+", " ", text).strip()[:120]


def snapshot(workspace_id: str, *, conversation_id: str,
             message_id: str = "", run_id: str = "",
             label: str = "") -> Dict[str, Any]:
    """给工作区拍一次检查点；同一个会话 + 消息的旧检查点会被替换。"""
    root = workspace_service.get_workspace_root("task", workspace_id).resolve()
    conversation_id = str(conversation_id or "").strip()
    if not conversation_id:
        raise ValueError("缺少会话 id")
    message_id = str(message_id or "").strip()
    run_id = str(run_id or "").strip()
    label = (label or _message_text(message_id)).strip()[:120]
    with _lock(workspace_id):
        files, skipped, complete = _scan_current(workspace_id, root,
                                                 write_blobs=True)
        cp_id = _checkpoint_id()
        entries = [{"path": rel, "sha": info["sha"], "size": int(info["size"]),
                    "mode": int(info.get("mode") or 0o644)}
                   for rel, info in sorted(files.items())]
        manifest = {
            "version": 1,
            "id": cp_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "run_id": run_id,
            "label": label,
            "created_at": _now_text(),
            "created_ts": time.time(),
            "root": str(root),
            "complete": bool(complete),
            "files": entries,
            "file_count": len(entries),
            "total_bytes": sum(int(e["size"]) for e in entries),
            "skipped_large": int(skipped.get("large") or 0),
            "skipped_limit": int(skipped.get("limit") or 0),
        }
        # 同一消息重跑：替换旧检查点（保留最新一次 run 之前的状态）
        replaced = 0
        if message_id:
            for old in _read_manifests(workspace_id):
                if (str(old.get("conversation_id") or "") == conversation_id
                        and str(old.get("message_id") or "") == message_id
                        and str(old.get("id")) != cp_id):
                    _remove_manifest(old)
                    replaced += 1
        _write_manifest(workspace_id, manifest)
        _prune_conversation(workspace_id, conversation_id)
        _save_state(workspace_id, root, files)
        if replaced:
            _gc_blobs(workspace_id)
    payload = {k: v for k, v in manifest.items() if not k.startswith("_")}
    payload["skipped_large"] = int(skipped.get("large") or 0)
    payload["skipped_limit"] = int(skipped.get("limit") or 0)
    return payload


def create_for_run(run: Dict[str, Any], conv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """run 开始执行前调用：只有带用户消息、且有工作目录时才拍快照。"""
    run = run or {}
    conv = conv or {}
    message_id = str(run.get("queued_msg_id") or "").strip()
    workspace_id = str(conv.get("workspace_id") or "").strip()
    conversation_id = str(conv.get("id") or run.get("conversation_id") or "").strip()
    if not message_id or not workspace_id or not conversation_id:
        return None
    if not workspace_service.has_working_dir(workspace_id):
        return None
    return snapshot(workspace_id, conversation_id=conversation_id,
                    message_id=message_id, run_id=str(run.get("id") or ""),
                    label=_message_text(message_id))


# ── 对外：查询 / 差异 ─────────────────────────────────────────────────────

def _existing_message_ids(conversation_id: str) -> Set[str]:
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import _q

    SessionLocal = get_session_local()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                text(f"SELECT id FROM {_q('gdag_nodes')} "
                     f"WHERE type = 'fact' "
                     f"AND props->>'conversation_id' = :cid "
                     f"AND props->>'stage' = 'exploration' "
                     f"AND COALESCE(props->>'kind', '') <> 'checkpoint'"),
                {"cid": conversation_id},
            ).fetchall()
    except Exception:
        return set()
    return {str(r[0]) for r in rows}


def _summary(manifest: Dict[str, Any],
             existing: Optional[Set[str]] = None) -> Dict[str, Any]:
    message_id = str(manifest.get("message_id") or "")
    return {
        "id": str(manifest.get("id") or ""),
        "workspace_id": str(manifest.get("workspace_id") or ""),
        "conversation_id": str(manifest.get("conversation_id") or ""),
        "message_id": message_id,
        "run_id": str(manifest.get("run_id") or ""),
        "label": str(manifest.get("label") or ""),
        "created_at": str(manifest.get("created_at") or ""),
        "created_ts": float(manifest.get("created_ts") or 0),
        "file_count": int(manifest.get("file_count") or 0),
        "total_bytes": int(manifest.get("total_bytes") or 0),
        "complete": bool(manifest.get("complete", True)),
        "skipped_large": int(manifest.get("skipped_large") or 0),
        "skipped_limit": int(manifest.get("skipped_limit") or 0),
        "message_present": (message_id in existing) if existing is not None else True,
    }


def list_for_conversation(conversation_id: str, workspace_id: str,
                          *, limit: int = 200) -> List[Dict[str, Any]]:
    existing = _existing_message_ids(conversation_id)
    items = [m for m in _read_manifests(workspace_id)
             if str(m.get("conversation_id") or "") == conversation_id]
    items.sort(key=lambda m: float(m.get("created_ts") or 0), reverse=True)
    return [_summary(m, existing) for m in items[:max(1, int(limit))]]


def get(workspace_id: str, checkpoint_id: str) -> Dict[str, Any]:
    cid = _valid_id(checkpoint_id)
    path = _manifest_path(workspace_id, cid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"检查点不存在: {checkpoint_id}")
    if not isinstance(data, dict) or str(data.get("id") or "") != cid:
        raise ValueError(f"检查点不存在: {checkpoint_id}")
    if str(data.get("workspace_id") or "") != workspace_id:
        raise ValueError("检查点不属于当前工作区")
    return data


def diff(workspace_id: str, checkpoint_id: str,
         *, limit: int = 200) -> Dict[str, Any]:
    """检查点 vs 当前工作区：新增 / 修改 / 删除。"""
    manifest = get(workspace_id, checkpoint_id)
    existing = _existing_message_ids(str(manifest.get("conversation_id") or ""))
    base = _summary(manifest, existing)
    try:
        root = workspace_service.get_workspace_root("task", workspace_id).resolve()
    except Exception as e:
        return {"checkpoint": base, "available": False, "reason": str(e),
                "added": [], "modified": [], "deleted": [],
                "counts": {"added": 0, "modified": 0, "deleted": 0},
                "truncated": False}
    if str(manifest.get("root") or "") != str(root):
        return {"checkpoint": base, "available": False,
                "reason": "工作区目录已变更，无法对比 / 回退",
                "added": [], "modified": [], "deleted": [],
                "counts": {"added": 0, "modified": 0, "deleted": 0},
                "truncated": False}
    with _lock(workspace_id):
        current, _skipped, _complete = _scan_current(workspace_id, root)
        _save_state(workspace_id, root, current)
    entries = {str(e.get("path") or ""): e for e in manifest.get("files") or []}
    added = [{"path": p, "size": int(info.get("size") or 0)}
             for p, info in current.items() if p not in entries]
    modified = [{"path": p, "size": int(info.get("size") or 0),
                 "old_size": int(entries[p].get("size") or 0)}
                for p, info in current.items()
                if p in entries and str(info.get("sha")) != str(entries[p].get("sha"))]
    deleted = [{"path": p, "size": int(e.get("size") or 0)}
               for p, e in entries.items() if p not in current]
    added.sort(key=lambda x: x["path"])
    modified.sort(key=lambda x: x["path"])
    deleted.sort(key=lambda x: x["path"])
    counts = {"added": len(added), "modified": len(modified),
              "deleted": len(deleted)}
    cap = max(1, int(limit))
    return {
        "checkpoint": base,
        "available": True,
        "added": added[:cap],
        "modified": modified[:cap],
        "deleted": deleted[:cap],
        "counts": counts,
        "truncated": any(v > cap for v in counts.values()),
        "delete_added_allowed": bool(manifest.get("complete", True)),
    }


# ── 对外：回退 / 删除 ─────────────────────────────────────────────────────

def _remove_empty_dirs(root: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path.resolve() == root.resolve():
            continue
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if _ignored_rel(rel):
            continue
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass


def restore(workspace_id: str, checkpoint_id: str, *,
            delete_added: bool = True) -> Dict[str, Any]:
    """把工作区文件恢复到检查点状态。

    - 恢复被修改 / 删除的文件；
    - ``delete_added=True`` 时删除检查点之后新建的文件（快照不完整时跳过，
      并在返回值里注明原因）。
    """
    manifest = get(workspace_id, checkpoint_id)
    try:
        root = workspace_service.get_workspace_root("task", workspace_id).resolve()
    except Exception as e:
        raise ValueError(str(e))
    if str(manifest.get("root") or "") != str(root):
        raise ValueError("工作区目录已变更，无法回退")

    entries = {str(e.get("path") or ""): e for e in manifest.get("files") or []}
    missing_before = [p for p, e in entries.items()
                      if not _blob_path(workspace_id, str(e.get("sha") or "")).is_file()]
    if missing_before:
        raise ValueError(f"检查点数据不完整：缺少 {len(missing_before)} 个文件的内容")

    restored: List[str] = []
    unchanged = 0
    deleted: List[str] = []
    blocked_reason = ""
    with _lock(workspace_id):
        current, _skipped, _complete = _scan_current(workspace_id, root)
        for rel, entry in entries.items():
            target = _safe_join(root, rel)
            if target.is_symlink():
                raise ValueError(f"目标已是符号链接，拒绝覆盖: {rel}")
            if target.is_dir():
                raise ValueError(f"目标路径已是目录，拒绝覆盖: {rel}")
            info = current.get(rel) or {}
            if str(info.get("sha") or "") == str(entry.get("sha") or ""):
                unchanged += 1
                continue
            data = _read_blob(workspace_id, str(entry.get("sha") or ""))
            _atomic_write_bytes(target, data)
            mode = entry.get("mode")
            if isinstance(mode, int):
                try:
                    os.chmod(target, mode)
                except OSError:
                    pass
            restored.append(rel)
            try:
                st = target.stat()
                current[rel] = {"sha": str(entry.get("sha") or ""),
                                "size": st.st_size,
                                "mtime_ns": st.st_mtime_ns}
            except OSError:
                current.pop(rel, None)
        if delete_added:
            if not bool(manifest.get("complete", True)):
                blocked_reason = ("检查点不完整（有文件因数量 / 大小上限未记录），"
                                  "已跳过删除新增文件")
            else:
                for rel, info in list(current.items()):
                    if rel in entries:
                        continue
                    if int(info.get("size") or 0) > MAX_FILE_BYTES:
                        continue
                    target = _safe_join(root, rel)
                    try:
                        if target.is_file() and not target.is_symlink():
                            target.unlink()
                            deleted.append(rel)
                    except OSError:
                        continue
                _remove_empty_dirs(root)
        _save_state(workspace_id, root, current)
        if restored or deleted:
            _gc_blobs(workspace_id)
    return {
        "checkpoint": _summary(manifest, _existing_message_ids(
            str(manifest.get("conversation_id") or ""))),
        "restored": len(restored),
        "restored_paths": sorted(restored)[:200],
        "unchanged": unchanged,
        "deleted": len(deleted),
        "deleted_paths": sorted(deleted)[:200],
        "delete_skipped": bool(blocked_reason),
        "delete_skipped_reason": blocked_reason,
    }


def delete(workspace_id: str, checkpoint_id: str) -> bool:
    cid = _valid_id(checkpoint_id)
    with _lock(workspace_id):
        try:
            manifest = get(workspace_id, cid)
        except ValueError:
            return False
        path = _manifest_path(workspace_id, cid)
        try:
            path.unlink()
        except OSError:
            return False
        _gc_blobs(workspace_id)
    return True


def prune_for_conversation(conversation_id: str,
                           workspace_id: Optional[str] = None) -> int:
    """删掉消息已不存在的检查点（编辑 / 撤回 / 回退后清理）。"""
    conv_id = str(conversation_id or "").strip()
    if not conv_id:
        return 0
    ws = str(workspace_id or "").strip()
    if not ws:
        try:
            from services.agent_zero.task_layer import conversation

            conv = conversation.get_conversation(conv_id) or {}
            ws = str(conv.get("workspace_id") or "")
        except Exception:
            return 0
    if not ws:
        return 0
    existing = _existing_message_ids(conv_id)
    removed = 0
    with _lock(ws):
        for manifest in _read_manifests(ws):
            if str(manifest.get("conversation_id") or "") != conv_id:
                continue
            message_id = str(manifest.get("message_id") or "")
            if message_id and message_id not in existing:
                _remove_manifest(manifest)
                removed += 1
        if removed:
            _gc_blobs(ws)
    return removed

"""工作区代码图谱（CodeGraph）：文件 → 符号 → 依赖 → 反向引用。

对标 Aider repomap / OpenCode 的代码索引，目标不是「把代码塞进上下文」，
而是让模型先用图谱定位，再只读需要的文件，减少 token。

特性：
- **自动初始化**：第一次注入系统提示或调用 codegraph 时惰性建立；
- **自动增量更新**：每个文件记录 mtime/size，文件改动才重新解析；写文件
  工具会主动 invalidate；
- **零依赖**：Python 用 stdlib ast，JS/TS 用保守正则；不引入 tree-sitter；
- **持久化**：每个源文件一个 GDAG entity 节点，文件之间 `references` 边；
- **查询**：summary / find / deps / impact / status / refresh，由 `codegraph`
  工具暴露；返回路径、符号、行号，不返回文件正文。
"""
from __future__ import annotations

import ast
import functools
import logging
import os
import posixpath
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.runtime.zero.gdag.store import GdagStore
from services.agent_zero.stores.zero_store import _q

logger = logging.getLogger(__name__)

SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".rb", ".php", ".swift", ".scala", ".cs",
}
SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "out", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", "logs",
    ".idea", ".vscode", "htmlcov", ".tox", "target", "vendor",
    "Pods", "DerivedData", "coverage",
}
MAX_FILES = 800
MAX_DEPTH = 8
MAX_FILE_BYTES = 2 * 1024 * 1024
SYMBOLS_PER_FILE = 12
# 增量扫描的最小间隔（秒）。这段扫描是**同步**的，挂在每一轮系统提示拼装前，
# 群聊里每个发言角色还会各来一次；以前 3s 等于「几乎每轮都重扫整个工作区」，
# 大仓库能吃掉几秒的首字延迟。写文件 / 外部变更都会显式 invalidate()，所以
# 拉长到 30s 只会让「外部编辑器改了文件」最多晚半分钟反映在提示里。
SCAN_TTL_S = 30.0

_store = GdagStore()
# ws_id -> {"entries": {path: entry}, "sigs": {path: sig},
#           "scan_ts": float, "root": str, "updated": float}
_cache: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)

    return wrapper

_JS_FUNC_RE = re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)")
_JS_CLASS_RE = re.compile(r"\b(?:class|interface)\s+([A-Za-z_$][\w$]*)")
_JS_EXPORT_RE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)"
)
_JS_ARROW_RE = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
)
_JS_IMPORT_RE = re.compile(r"(?:from\s+|require\s*\(\s*)['\"]([^'\"]+)['\"]")


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _extract_symbols(path: Path) -> Tuple[List[str], Dict[str, int], List[str]]:
    """返回 (符号列表, 符号行号, import 模块列表)。失败返回空，不抛错。"""
    ext = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return [], {}, []
    symbols: List[str] = []
    lines: Dict[str, int] = {}
    imports: List[str] = []

    if ext == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return [], {}, []
        for node in getattr(tree, "body", []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(node.name)
                lines.setdefault(node.name, int(getattr(node, "lineno", 1) or 1))
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id.isupper():
                        symbols.append(tgt.id)
                        lines.setdefault(tgt.id, int(getattr(node, "lineno", 1) or 1))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
                else:
                    imports.extend(a.name for a in node.names)
    else:
        for regex, kind in (
            (_JS_FUNC_RE, "function"), (_JS_CLASS_RE, "class"),
            (_JS_EXPORT_RE, "export"), (_JS_ARROW_RE, "function"),
        ):
            for m in regex.finditer(text):
                name = m.group(1)
                symbols.append(name)
                lines.setdefault(name, _line_of(text, m.start()))
        imports.extend(m.group(1) for m in _JS_IMPORT_RE.finditer(text))

    seen: set = set()
    out: List[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    out = out[:SYMBOLS_PER_FILE]
    return out, {s: lines.get(s, 1) for s in out}, sorted(set(imports))


def _scan_candidates(root: Path) -> List[Tuple[Path, str, os.stat_result]]:
    """有界遍历工作区，返回源文件候选（绝对路径、相对路径、stat）。"""
    out: List[Tuple[Path, str, os.stat_result]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        depth = 0 if rel_dir == Path(".") else len(rel_dir.parts)
        if depth >= MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for fn in filenames:
            if len(out) >= MAX_FILES:
                return out
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SOURCE_EXTS:
                continue
            path = Path(dirpath) / fn
            try:
                st = path.stat()
            except Exception:
                continue
            if int(st.st_size) > MAX_FILE_BYTES:
                continue
            rel = (rel_dir / fn).as_posix() if rel_dir != Path(".") else fn
            out.append((path, rel, st))
    out.sort(key=lambda item: item[1])
    return out


def _parse_entry(path: Path, rel: str, st: os.stat_result) -> Dict[str, Any]:
    symbols, symbol_lines, imports = _extract_symbols(path)
    return {
        "path": rel,
        "symbols": symbols,
        "symbol_lines": symbol_lines,
        "imports": imports,
        "size": int(st.st_size),
    }


def _file_id(ws_id: str, path: str) -> str:
    return f"map:file:{ws_id}:{path}"


def _persist_graph(ws_id: str, entries: Dict[str, Dict[str, Any]],
                   changed: List[str], removed: List[str]) -> int:
    """增量写 GDAG：只 upsert 变化文件，删除消失文件，重建 references 边。"""
    try:
        for rel in removed:
            try:
                _store.delete_node(_file_id(ws_id, rel))
            except Exception:
                logger.debug("删除代码实体失败: %s", rel, exc_info=True)
        for rel in changed:
            entry = entries.get(rel)
            if not entry:
                continue
            _store.upsert_node({
                "id": _file_id(ws_id, rel),
                "type": "entity",
                "camp": ws_id,
                "props": {
                    "kind": "code_file",
                    "path": rel,
                    "symbols": entry["symbols"],
                    "symbol_lines": entry["symbol_lines"],
                    "imports": entry["imports"][:20],
                    "workspace_id": ws_id,
                },
                "confidence": 0.5,
            })

        # 边是派生数据：每次变更重建当前工作区的 references 边。
        SessionLocal = get_session_local()
        prefix = f"map:file:{ws_id}:%"
        with SessionLocal() as db:
            db.execute(
                text(f"DELETE FROM {_q('gdag_edges')} "
                     f"WHERE kind = 'references' AND from_id LIKE :prefix"),
                {"prefix": prefix},
            )
            db.commit()

        by_path = {rel: entry for rel, entry in entries.items()}
        by_base: Dict[str, List[str]] = {}
        for rel in by_path:
            by_base.setdefault(os.path.basename(rel), []).append(rel)
            by_base.setdefault(os.path.splitext(os.path.basename(rel))[0], []).append(rel)

        edges = 0
        for rel, entry in entries.items():
            for imp in entry["imports"]:
                target = _resolve_import(rel, imp, by_path, by_base)
                if not target or target == rel:
                    continue
                _store.add_edge({
                    "from_id": _file_id(ws_id, rel),
                    "to_id": _file_id(ws_id, target),
                    "kind": "references",
                })
                edges += 1
        return edges
    except Exception as exc:
        logger.debug("代码图谱落库失败 ws=%s: %s", ws_id, exc)
        return 0


def _resolve_import(importer: str, imp: str, by_path: Dict[str, Any],
                    by_base: Dict[str, List[str]]) -> Optional[str]:
    """把 import 解析成工作区里的文件（保守匹配，不追求完整模块系统）。"""
    raw = str(imp or "").strip()
    if not raw:
        return None
    if raw.startswith("."):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), raw))
        candidates = [base]
        candidates += [base + ext for ext in SOURCE_EXTS]
        candidates += [f"{base}/index{ext}" for ext in SOURCE_EXTS]
        for candidate in candidates:
            if candidate in by_path:
                return candidate
    stem = raw.split(".")[-1].split("/")[-1]
    matches = by_base.get(stem) or []
    if not matches:
        matches = by_base.get(raw) or []
    for candidate in matches:
        if candidate != importer:
            return candidate
    return None


@_locked
def get_map(ws_id: str, *, force: bool = False) -> List[Dict[str, Any]]:
    """自动初始化 / 增量更新并返回工作区代码图谱。"""
    from services.agent_zero.services import workspace_service as wsvc

    try:
        root = wsvc.exec_root(ws_id)
    except Exception:
        return []
    if not root:
        return []
    root = Path(root)
    now = time.time()
    state = _cache.get(ws_id)
    if state and state.get("root") != str(root):
        state = None
    if (not force and state and now - float(state.get("scan_ts") or 0) < SCAN_TTL_S):
        return _sorted_entries(state.get("entries") or {})

    old_entries: Dict[str, Dict[str, Any]] = (state or {}).get("entries") or {}
    old_sigs: Dict[str, Tuple[int, int]] = (state or {}).get("sigs") or {}
    entries: Dict[str, Dict[str, Any]] = {}
    sigs: Dict[str, Tuple[int, int]] = {}
    changed: List[str] = []
    candidates = _scan_candidates(root)

    for path, rel, st in candidates:
        sig = (int(st.st_mtime_ns), int(st.st_size))
        sigs[rel] = sig
        if not force and old_sigs.get(rel) == sig and rel in old_entries:
            entries[rel] = old_entries[rel]
        else:
            entries[rel] = _parse_entry(path, rel, st)
            changed.append(rel)

    removed = [rel for rel in old_entries if rel not in entries]
    if changed or removed or state is None or state.get("root") != str(root):
        _persist_graph(ws_id, entries, changed, removed)

    by_base = _base_index(entries)
    edge_count = 0
    for rel, entry in entries.items():
        for imp in entry["imports"]:
            target = _resolve_import(rel, imp, entries, by_base)
            if target and target != rel:
                edge_count += 1

    _cache[ws_id] = {
        "entries": entries,
        "sigs": sigs,
        "scan_ts": now,
        "root": str(root),
        "updated": now if (changed or removed or state is None) else (state or {}).get("updated", now),
        "edges": edge_count,
    }
    return _sorted_entries(entries)


def _base_index(entries: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for rel in entries:
        out.setdefault(os.path.basename(rel), []).append(rel)
        out.setdefault(os.path.splitext(os.path.basename(rel))[0], []).append(rel)
    return out


def _sorted_entries(entries: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [entries[path] for path in sorted(entries)]


@_locked
def invalidate(ws_id: str) -> None:
    """写文件 / 外部变更后标记过期；下一次 get_map 只重解析变化文件。"""
    state = _cache.get(ws_id)
    if state:
        state["scan_ts"] = 0
    # 没有缓存时无需处理：下一次 get_map 会建立完整索引。


@_locked
def refresh(ws_id: str) -> Dict[str, Any]:
    """强制重建并返回状态。"""
    invalidate(ws_id)
    get_map(ws_id, force=True)
    return status(ws_id)


def render_map(ws_id: str, max_chars: int = 2500,
               max_symbols: int = 5) -> str:
    """紧凑文本地图：路径 + 关键符号，供系统提示注入。"""
    entries = get_map(ws_id)
    if not entries:
        return ""
    state = _cache.get(ws_id) or {}
    lines: List[str] = []
    used = 0
    for e in entries:
        sym = ", ".join(e["symbols"][:max_symbols])
        line = f"{e['path']}" + (f" — {sym}" if sym else "")
        if used + len(line) > max_chars:
            lines.append(f"…（还有 {len(entries) - len(lines)} 个文件未列出，"
                         "用 codegraph 工具查询具体符号 / 依赖）")
            break
        lines.append(line)
        used += len(line) + 1
    head = (f"共 {len(entries)} 个源文件"
            f"（自动增量索引，更新于 {time.strftime('%H:%M:%S', time.localtime(state.get('updated') or time.time()))}）")
    return head + "\n" + "\n".join(lines)


def _compact_hit(entry: Dict[str, Any], limit: int = 8) -> Dict[str, Any]:
    symbols = entry["symbols"][:limit]
    return {
        "path": entry["path"],
        "symbols": symbols,
        "lines": {s: entry["symbol_lines"].get(s, 1) for s in symbols},
        "imports": entry["imports"][:limit],
    }


def find_symbol(ws_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """按符号 / 路径 / import 关键词定位文件（返回紧凑摘要，不带正文）。"""
    q = str(query or "").strip().lower()
    if not q:
        return []
    hits: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
    for e in get_map(ws_id):
        path_l = e["path"].lower()
        symbol_hit = any(q in s.lower() for s in e["symbols"])
        import_hit = any(q in imp.lower() for imp in e["imports"])
        if not (q in path_l or symbol_hit or import_hit):
            continue
        rank = 0 if symbol_hit else (1 if q in path_l else 2)
        hits.append(((rank, len(e["path"])), _compact_hit(e)))
    hits.sort(key=lambda item: item[0])
    return [hit for _rank, hit in hits[:max(1, min(int(limit), 30))]]


def _match_file(ws_id: str, target: str) -> List[Dict[str, Any]]:
    t = str(target or "").strip().lower().replace("\\", "/")
    if not t:
        return []
    entries = get_map(ws_id)
    out = []
    for e in entries:
        path_l = e["path"].lower()
        base = os.path.basename(path_l)
        stem = os.path.splitext(base)[0]
        if path_l == t or base == t or stem == t:
            out.append(e)
    if out:
        return out
    return [e for e in entries if t in e["path"].lower()]


def dependencies(ws_id: str, target: str) -> Dict[str, Any]:
    """某个文件 / 模块的直接依赖与反向依赖。"""
    entries = get_map(ws_id)
    by_path = {e["path"]: e for e in entries}
    by_base = _base_index(by_path)
    files = _match_file(ws_id, target)
    if not files:
        return {"target": target, "files": [], "error": "未找到文件"}
    results = []
    for entry in files:
        rel = entry["path"]
        imports = []
        for imp in entry["imports"]:
            target_path = _resolve_import(rel, imp, by_path, by_base)
            imports.append({"import": imp, "file": target_path or ""})
        imported_by = []
        for other in entries:
            if other["path"] == rel:
                continue
            for imp in other["imports"]:
                if _resolve_import(other["path"], imp, by_path, by_base) == rel:
                    imported_by.append(other["path"])
                    break
        results.append({
            "file": rel,
            "symbols": entry["symbols"][:20],
            "imports": imports[:30],
            "imported_by": sorted(set(imported_by))[:30],
        })
    return {"target": target, "files": results}


def impact(ws_id: str, target: str, depth: int = 2) -> Dict[str, Any]:
    """反向依赖影响面：谁直接 / 间接 import 了目标文件。"""
    entries = get_map(ws_id)
    by_path = {e["path"]: e for e in entries}
    by_base = _base_index(by_path)
    targets = [e["path"] for e in _match_file(ws_id, target)]
    if not targets:
        return {"target": target, "files": [], "error": "未找到文件"}
    reverse: Dict[str, set] = {path: set() for path in by_path}
    for rel, entry in by_path.items():
        for imp in entry["imports"]:
            target_path = _resolve_import(rel, imp, by_path, by_base)
            if target_path:
                reverse.setdefault(target_path, set()).add(rel)
    seen: Dict[str, int] = {path: 0 for path in targets}
    frontier = list(targets)
    for level in range(1, max(1, min(int(depth), 4)) + 1):
        nxt = []
        for path in frontier:
            for parent in reverse.get(path, set()):
                if parent not in seen:
                    seen[parent] = level
                    nxt.append(parent)
        frontier = nxt
        if not frontier:
            break
    return {
        "target": target,
        "targets": targets,
        "affected": [
            {"path": path, "depth": d}
            for path, d in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))
            if d > 0
        ][:50],
    }


def status(ws_id: str) -> Dict[str, Any]:
    entries = get_map(ws_id)
    state = _cache.get(ws_id) or {}
    symbols = sum(len(e["symbols"]) for e in entries)
    imports = sum(len(e["imports"]) for e in entries)
    return {
        "workspace_id": ws_id,
        "files": len(entries),
        "symbols": symbols,
        "imports": imports,
        "edges": int(state.get("edges") or 0),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(state.get("updated") or time.time())),
        "cache_ttl_s": SCAN_TTL_S,
    }


def entity_id(ws_id: str, rel_path: str) -> str:
    """文件实体稳定 id（与 _persist_graph 同一约定）。"""
    rel = str(rel_path or "").strip()
    if rel.startswith("./"):
        rel = rel[2:]
    return _file_id(ws_id, rel)


def resolve_entities(ws_id: str, names: List[str]) -> List[str]:
    """把「涉及的文件 / 符号」解析成实体节点 id。"""
    ids: List[str] = []
    entries = get_map(ws_id)
    for raw in names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        low = name.lower()
        matched = None
        for e in entries:
            base = os.path.basename(e["path"]).lower()
            if e["path"].lower() == low or base == low:
                matched = e
                break
        if matched:
            ids.append(entity_id(ws_id, matched["path"]))
        else:
            eid = f"ent:{ws_id}:{low}"
            try:
                _store.upsert_node({
                    "id": eid, "type": "entity", "camp": ws_id,
                    "props": {"kind": "entity", "name": name, "workspace_id": ws_id},
                    "confidence": 0.4,
                })
            except Exception as exc:
                logger.debug("通用实体创建失败 %s: %s", name, exc)
                continue
            ids.append(eid)
    return ids


def mentioned_entities(content: str, ws_id: str) -> List[str]:
    """自动从记忆正文里识别提及的文件 / 符号 → 实体 id（零 LLM）。"""
    text = str(content or "")
    if not text:
        return []
    ids: List[str] = []
    for e in get_map(ws_id):
        path = e["path"]
        base = os.path.basename(path)
        if path in text or base in text:
            ids.append(entity_id(ws_id, path))
            continue
        for s in e["symbols"]:
            if re.search(r"\b" + re.escape(s) + r"\b", text):
                ids.append(entity_id(ws_id, path))
                break
    return ids

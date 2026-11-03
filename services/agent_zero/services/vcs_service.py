"""版本控制服务：git / svn 统一查看与管理。

工作区根目录若是一个 git 或 svn 仓库，这里提供一套统一的小操作集，
供界面面板与 agent 工具（registry 里的 `vcs` 工具）共用：

  查看: summary / status / log / diff / branches / remotes
  管理: commit / switch（含新建分支） / stage / unstage / discard /
        remote_add / remote_remove / remote_set_url / fetch

安全约束：所有命令以参数列表形式（不经 shell）在工作区根目录执行；
文件路径经 workspace_service 解析，禁止 `..` 与绝对路径逃逸；
分支 / 修订号只允许安全字符，避免参数注入。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.agent_zero.services import workspace_service

CMD_TIMEOUT_S = 20.0
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_./\-]{1,120}$")
_SAFE_REV_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_SAFE_REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


class VcsError(ValueError):
    """版本控制操作失败或参数非法。"""


def _run(cmd: List[str], root: Path, timeout: Optional[float] = None) -> Dict[str, Any]:
    # 桌面 App 从 Finder 启动时没有 LANG，svn 会把本地编码当成 ASCII：
    # 中文提交说明直接报 E000022，日志/状态里的中文也乱码。显式固定 UTF-8。
    if cmd and cmd[0] == "svn" and "--non-interactive" not in cmd:
        cmd = [cmd[0], "--non-interactive", *cmd[1:]]
    if cmd and cmd[0] == "git" and "core.quotepath=false" not in cmd:
        # 默认 core.quotepath=true 会把中文路径输出成 \345\275\225 这种八进制转义
        cmd = [cmd[0], "-c", "core.quotepath=false", *cmd[1:]]
    env = dict(os.environ)
    env["LC_ALL"] = "C.UTF-8"
    env["LANG"] = "C.UTF-8"
    env.setdefault("LC_CTYPE", "C.UTF-8")
    if not root.is_dir():
        raise VcsError(f"工作区目录不存在: {root}")
    limit_s = float(timeout or CMD_TIMEOUT_S)
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True,
                              encoding="utf-8", errors="replace",
                              timeout=limit_s, env=env)
    except FileNotFoundError:
        raise VcsError(f"未找到 {cmd[0]} 命令：请先安装 {cmd[0]} 并确保它在 PATH 中")
    except subprocess.TimeoutExpired:
        raise VcsError(f"命令超时（{int(limit_s)}s）：{' '.join(cmd)}")
    return {"ok": proc.returncode == 0, "out": proc.stdout, "err": proc.stderr}


def _find_vcs_root(root: Path) -> Optional[str]:
    """从工作区向上找最近的 .git / .svn（最多 16 层，到 home 的上级为止）。

    工作区常常是某个大仓库里的子目录（“上级有 svn”）；只查工作区根目录会
    误判为没有版本控制。svn/git 命令在子目录里执行时天然只看该子树，所以
    找到标记后仍以工作区路径为操作目标即可。
    """
    cur = root
    stop = Path.home().parent
    for _ in range(16):
        if (cur / ".git").exists():
            return "git"
        if (cur / ".svn").is_dir():
            return "svn"
        if cur.parent == cur or cur == stop:
            break
        cur = cur.parent
    return None


def detect(ws_id: str) -> Optional[str]:
    """检测工作区的版本控制类型：git / svn / None（会向上级目录查找）。"""
    try:
        root = workspace_service.get_workspace_root("task", ws_id)
    except workspace_service.WorkspacePathError:
        return None
    return _find_vcs_root(root)


def _require_root(ws_id: str, vcs: Optional[str]) -> Path:
    detected = detect(ws_id)
    if not detected:
        if not workspace_service.has_working_dir(ws_id):
            raise VcsError(
                "该工作区是纯聊天工作区，没有用户文件夹，无法使用版本控制；"
                "请在「工作区」中添加一个指向本地仓库的工作区")
        raise VcsError("该工作区没有版本控制（根目录既不是 git 也不是 svn 仓库）")
    if vcs and detected != vcs:
        raise VcsError(f"该工作区是 {detected} 仓库，不支持 {vcs} 操作")
    return workspace_service.get_workspace_root("task", ws_id)


def _validated_paths(ws_id: str, paths: List[str]) -> List[str]:
    out: List[str] = []
    for raw in paths or []:
        rel = str(raw or "").strip()
        if not rel:
            continue
        target = workspace_service.resolve_workspace_path(ws_id, rel)
        out.append(workspace_service.safe_relpath(ws_id, target))
    return out


def _safe_name(value: str, kind: str) -> str:
    v = str(value or "").strip()
    if not v or not _SAFE_NAME_RE.match(v):
        raise VcsError(f"非法{kind}: {value!r}")
    return v


def _safe_rev(value: str) -> str:
    v = str(value or "").strip()
    if not v or not _SAFE_REV_RE.match(v):
        raise VcsError(f"非法修订号: {value!r}")
    return v


def _safe_remote(value: str) -> str:
    v = str(value or "").strip()
    if not v or not _SAFE_REMOTE_RE.match(v):
        raise VcsError(f"非法远端名: {value!r}")
    return v


def _safe_url(value: str) -> str:
    v = str(value or "").strip()
    lowered = v.lower()
    if (not v or len(v) > 2048 or v.startswith("-")
            or lowered.startswith(("ext::", "fd::"))
            or any(c in v for c in "\r\n\x00")
            or any(c.isspace() for c in v)):
        raise VcsError(f"非法仓库地址: {value!r}")
    return v


# ── git ──────────────────────────────────────────────────────────────

def _git_status_entries(root: Path) -> List[Dict[str, Any]]:
    # -z：路径不再做 C 风格引号/转义（含空格、中文的目录也能原样显示）。
    res = _run(["git", "status", "--porcelain", "-z"], root)
    if not res["ok"]:
        raise VcsError(res["err"] or "git status 失败")
    entries: List[Dict[str, Any]] = []
    chunks = res["out"].split("\x00")
    i = 0
    while i < len(chunks):
        line = chunks[i]
        i += 1
        if len(line) < 3:
            continue
        x, y = line[0], line[1]
        path = line[3:]
        if x in ("R", "C") or y in ("R", "C"):
            # -z 下 rename/copy 的下一个 NUL 段是原路径，跳过
            i += 1
        if x == "?":
            state = "untracked"
        elif "U" in (x, y):
            state = "conflict"
        elif "R" in (x, y):
            state = "renamed"
        elif "A" in (x, y):
            state = "added"
        elif "D" in (x, y):
            state = "deleted"
        elif "C" in (x, y):
            state = "copied"
        elif "M" in (x, y):
            state = "modified"
        else:
            state = "unknown"
        entries.append({"path": path, "state": state,
                        "staged": x not in ("", "?")})
    return entries


def _git_summary(root: Path) -> Dict[str, Any]:
    branch = _run(["git", "branch", "--show-current"], root)["out"].strip()
    if not branch:
        head = _run(["git", "rev-parse", "--short", "HEAD"], root)["out"].strip()
        branch = f"detached {head}" if head else ""
    ahead = behind = 0
    sb = _run(["git", "status", "-sb"], root)
    for line in sb["out"].splitlines():
        if line.startswith("##"):
            m = re.search(r"ahead (\d+)", line)
            if m:
                ahead = int(m.group(1))
            m = re.search(r"behind (\d+)", line)
            if m:
                behind = int(m.group(1))
    log = []
    try:
        log = _git_log(root, 1)
    except VcsError:
        # 空仓库（还没有第一个 commit）没有 log，但 summary 仍应可用
        log = []
    rc = _run(["git", "rev-list", "--count", "HEAD"], root)
    commits = int(rc["out"].strip()) if rc["ok"] and rc["out"].strip() else 0
    br = _run(["git", "branch", "--list"], root)
    branch_count = len([l for l in br["out"].splitlines() if l.strip()])
    rm = _run(["git", "remote"], root)
    remote_count = len([l for l in rm["out"].splitlines() if l.strip()]) if rm["ok"] else 0
    return {
        "type": "git",
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "changed": len(_git_status_entries(root)),
        "commits": commits,
        "branch_count": branch_count,
        "remote_count": remote_count,
        "last_commit": log[0] if log else None,
    }


def _git_log(root: Path, limit: int) -> List[Dict[str, Any]]:
    fmt = "%H\x1f%h\x1f%an\x1f%ad\x1f%s\x1f%P\x1f%D"
    res = _run(["git", "log", f"-n{max(1, min(limit, 100))}",
                f"--pretty=format:{fmt}", "--date=iso-strict"], root)
    if not res["ok"]:
        raise VcsError(res["err"] or "git log 失败")
    out = []
    for line in res["out"].splitlines():
        parts = line.split("\x1f")
        if len(parts) == 7:
            refs = [r.strip() for r in parts[6].split(",") if r.strip()]
            out.append({"rev": parts[1], "sha": parts[0], "author": parts[2],
                        "date": parts[3], "message": parts[4],
                        "parents": [p for p in parts[5].split() if p],
                        "refs": refs})
    return out


def _git_branches(root: Path) -> Dict[str, Any]:
    res = _run(["git", "branch", "-a"], root)
    if not res["ok"]:
        raise VcsError(res["err"] or "git branch 失败")
    local: List[Dict[str, Any]] = []
    remote: List[str] = []
    for line in res["out"].splitlines():
        line = line.strip()
        if not line:
            continue
        current = line.startswith("*")
        name = line.lstrip("* ").strip()
        if "->" in name:  # 别名分支跳过
            continue
        if name.startswith("remotes/"):
            remote.append(name[len("remotes/"):])
        else:
            local.append({"name": name, "current": current})
    return {"type": "git", "local": local, "remote": remote}


def _git_remotes(root: Path) -> Dict[str, Any]:
    """git 远端清单：名称、fetch / push 地址、当前分支的上游关系。"""
    res = _run(["git", "remote", "-v"], root)
    if not res["ok"]:
        raise VcsError(res["err"] or "git remote 失败")
    remotes: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for line in res["out"].splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3:
            continue
        name, url, kind = fields[0], fields[1], fields[2].strip("()").lower()
        if name not in remotes:
            remotes[name] = {"name": name, "fetch_url": "", "push_url": ""}
            order.append(name)
        if kind == "fetch" and not remotes[name]["fetch_url"]:
            remotes[name]["fetch_url"] = url
        elif kind == "push":
            remotes[name]["push_url"] = url
    upstream_res = _run(["git", "rev-parse", "--abbrev-ref",
                         "--symbolic-full-name", "@{upstream}"], root)
    upstream = upstream_res["out"].strip() if upstream_res["ok"] else ""
    out: List[Dict[str, Any]] = []
    for name in order:
        item = remotes[name]
        item["url"] = item["fetch_url"] or item["push_url"]
        item["upstream"] = upstream
        prefix = f"{name}/"
        item["tracking_branch"] = upstream[len(prefix):] if upstream.startswith(prefix) else ""
        out.append(item)
    return {"type": "git", "remotes": out, "upstream": upstream}


def _git_remote_add(root: Path, name: str, url: str) -> Dict[str, Any]:
    res = _run(["git", "remote", "add", name, url], root)
    if not res["ok"]:
        raise VcsError(res["err"] or res["out"] or f"添加远端 {name} 失败")
    return _git_remotes(root)


def _git_remote_remove(root: Path, name: str) -> Dict[str, Any]:
    res = _run(["git", "remote", "remove", name], root)
    if not res["ok"]:
        raise VcsError(res["err"] or res["out"] or f"删除远端 {name} 失败")
    return _git_remotes(root)


def _git_remote_set_url(root: Path, name: str, url: str,
                        push: bool = False) -> Dict[str, Any]:
    args = ["git", "remote", "set-url"]
    if push:
        args.append("--push")
    res = _run(args + [name, url], root)
    if not res["ok"]:
        raise VcsError(res["err"] or res["out"] or f"更新远端 {name} 失败")
    return _git_remotes(root)


def _git_fetch(root: Path, remote: Optional[str]) -> Dict[str, Any]:
    args = ["git", "fetch", "--prune"]
    if remote:
        args.append(remote)
    res = _run(args, root, timeout=60.0)
    if not res["ok"]:
        raise VcsError(res["err"] or res["out"] or "git fetch 失败")
    return {"ok": True, "output": (res["out"] + res["err"]).strip()}


def _git_diff(root: Path, path: Optional[str], rev: Optional[str],
              ws_id: str) -> str:
    if rev:
        res = _run(["git", "diff", f"{rev}^", rev] + (["--", path] if path else []), root)
        if res["ok"] and res["out"]:
            return res["out"]
        # 根提交没有父提交：用 git show 的补丁部分
        res2 = _run(["git", "show", "--patch", rev], root)
        lines = res2["out"].splitlines()
        idx = next((i for i, l in enumerate(lines) if l.startswith("diff --git")), None)
        return "\n".join(lines[idx:]) if idx is not None else res2["out"]
    args = ["git", "diff"]
    if path:
        args += ["--", path]
    res = _run(args, root)
    if not res["ok"]:
        raise VcsError(res["err"] or "git diff 失败")
    if res["out"]:
        return res["out"]
    # 未跟踪文件：git diff 不显示，构造整文件新增 diff
    if path:
        for entry in _git_status_entries(root):
            if entry["path"] == path and entry["state"] == "untracked":
                data = workspace_service.read_file(ws_id, path)
                lines = data["content"].splitlines()
                head = f"--- /dev/null\n+++ {path}\n@@ -0,0 +{len(lines)} @@\n"
                return head + "\n".join(f"+{l}" for l in lines)
    return ""


def _git_commit(root: Path, message: str, paths: List[str], all_changes: bool) -> Dict[str, Any]:
    if all_changes:
        add = _run(["git", "add", "-A"], root)
        if not add["ok"]:
            raise VcsError(add["err"] or "git add 失败")
    elif paths:
        add = _run(["git", "add", "--"] + paths, root)
        if not add["ok"]:
            raise VcsError(add["err"] or "git add 失败")
    res = _run(["git", "commit", "-m", message], root)
    if not res["ok"]:
        raise VcsError((res["err"] or res["out"] or "git commit 失败").strip())
    return {"ok": True, "output": res["out"].strip()}


def _git_stage(root: Path, paths: List[str], staged: bool) -> Dict[str, Any]:
    if not paths:
        raise VcsError("需要至少一个文件路径")
    if staged:
        res = _run(["git", "add", "--"] + paths, root)
    else:
        res = _run(["git", "reset", "--"] + paths, root)
    if not res["ok"]:
        raise VcsError(res["err"] or "暂存操作失败")
    return {"ok": True, "output": res["out"].strip(), "paths": paths}


def _git_discard(root: Path, paths: List[str]) -> Dict[str, Any]:
    if not paths:
        raise VcsError("需要至少一个文件路径")
    restore: List[str] = []
    deleted: List[str] = []
    for p in paths:
        full = root / p
        tracked = _run(["git", "ls-files", "--", p], root)
        if tracked["ok"] and tracked["out"].strip():
            restore.append(p)
        elif full.exists():
            # 未跟踪文件：丢弃即删除（路径已校验在工作区内）
            try:
                full.unlink()
                deleted.append(p)
            except OSError as e:
                raise VcsError(f"删除未跟踪文件失败: {e}")
    if restore:
        res = _run(["git", "checkout", "--"] + restore, root)
        if not res["ok"]:
            raise VcsError(res["err"] or "丢弃改动失败")
    return {"ok": True, "output": res["out"].strip() if restore else "",
            "paths": paths, "deleted": deleted}


# ── svn ──────────────────────────────────────────────────────────────

def _svn_info(root: Path) -> Dict[str, Any]:
    res = _run(["svn", "info"], root)
    if not res["ok"]:
        raise VcsError(res["err"] or "svn info 失败")
    info: Dict[str, Any] = {}
    for line in res["out"].splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            info[key.strip().lower()] = val.strip()
    return info


def _svn_status_entries(root: Path) -> List[Dict[str, Any]]:
    res = _run(["svn", "status"], root)
    if not res["ok"]:
        raise VcsError(res["err"] or "svn status 失败")
    mapping = {"M": "modified", "A": "added", "D": "deleted",
               "?": "untracked", "!": "missing", "R": "renamed",
               "C": "conflict", "I": "ignored"}
    entries: List[Dict[str, Any]] = []
    for line in res["out"].splitlines():
        if not line.strip():
            continue
        flag = line[0]
        path = line[1:].strip()
        entries.append({"path": path, "state": mapping.get(flag, "unknown"),
                        "staged": False})
    return entries


def _svn_summary(root: Path) -> Dict[str, Any]:
    info = _svn_info(root)
    log = _svn_log(root, 1)
    branch_info = _svn_branches(root)
    return {
        "type": "svn",
        "branch": info.get("url", ""),
        "repo_root": info.get("repository root", ""),
        "revision": int(info.get("revision", 0) or 0),
        "changed": len(_svn_status_entries(root)),
        "commits": int(info.get("revision", 0) or 0),
        "branch_count": len(branch_info.get("branches") or []) or 1,
        "remote_count": 0,
        "last_commit": log[0] if log else None,
    }


def _svn_log(root: Path, limit: int) -> List[Dict[str, Any]]:
    import xml.etree.ElementTree as ET

    # 从仓库 HEAD 往回取：默认按工作副本基准版本取，刚提交的那版会看不到。
    res = _run(["svn", "log", "-r", "HEAD:1",
                "-l", str(max(1, min(limit, 100))), "--xml"], root)
    if not res["ok"]:
        raise VcsError(res["err"] or "svn log 失败")
    out: List[Dict[str, Any]] = []
    try:
        tree = ET.fromstring(res["out"])
    except ET.ParseError:
        return out
    for entry in tree.findall("logentry"):
        author = entry.findtext("author") or ""
        date = (entry.findtext("date") or "")[:19]
        msg = (entry.findtext("msg") or "").strip()
        out.append({"rev": entry.get("revision", ""), "sha": entry.get("revision", ""),
                    "author": author, "date": date, "message": msg,
                    "parents": [], "refs": []})
    # SVN 是按路径的线性历史：把相邻（更旧）的一条作为父提交，供版本图连线。
    for i in range(len(out) - 1):
        out[i]["parents"] = [out[i + 1]["rev"]]
    return out


def _svn_branches(root: Path) -> Dict[str, Any]:
    info = _svn_info(root)
    repo_root = info.get("repository root") or info.get("repository url") or ""
    branches: List[str] = []
    if repo_root:
        res = _run(["svn", "ls", f"{repo_root}/branches"], root)
        if res["ok"]:
            branches = [b.rstrip("/") for b in res["out"].splitlines() if b.strip()]
    current_url = info.get("url", "")
    current_name = ""
    if "/branches/" in current_url:
        current_name = current_url.split("/branches/", 1)[1] or "trunk"
    else:
        current_name = "trunk"
    return {"type": "svn", "current": current_name, "current_url": current_url,
            "repo_root": repo_root, "branches": branches}


_TEXT_EXTS = frozenset({
    ".txt", ".md", ".py", ".json", ".yaml", ".yml", ".js", ".mjs", ".ts",
    ".css", ".html", ".htm", ".xml", ".sh", ".bash", ".zsh", ".sql", ".toml",
    ".ini", ".cfg", ".conf", ".log", ".csv", ".tsv", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".rb", ".php", ".vue", ".svelte",
})
_BINARY_NOTICE = ("marked as a binary type", "binary type", "二进制类型", "无法显示")


def _diff_as_text(root: Path, path: Optional[str]) -> bool:
    """目标是否应按文本展示：扩展名文本 + 文件不含 NUL（避免把真二进制刷屏）。"""
    if not path:
        return False
    target = root / path
    if target.suffix.lower() not in _TEXT_EXTS:
        return False
    try:
        if not target.is_file() or target.stat().st_size > 2_000_000:
            return False
        return b"\x00" not in target.read_bytes()
    except OSError:
        return False


def _svn_diff(root: Path, path: Optional[str], rev: Optional[str]) -> str:
    args = ["svn", "diff"]
    if rev:
        args += ["-c", rev]
    if path:
        args.append(path)
    res = _run(args, root)
    if not res["ok"]:
        raise VcsError(res["err"] or "svn diff 失败")
    out = res["out"]
    # svn 按 mime-type 把文件当二进制时只回一句「无法显示」。文本类文件
    # （mime-type 被误标 / 历史遗留）用 --force 再取一次真实 diff。
    if any(k in out.lower() for k in _BINARY_NOTICE) and _diff_as_text(root, path):
        forced = _run(["svn", "diff", "--force"] + args[2:], root)
        if forced["ok"] and forced["out"].strip():
            out = forced["out"]
    return out


def _repair_text_mime(root: Path, paths: List[str]) -> int:
    """把被误标成 application/octet-stream 的文本文件改回 text/plain。

    svn 对纯中文内容会误判为二进制（`svn add` 显示 `(bin)`），随后 diff 只会
    输出「无法显示」。提交前顺手修正 mime-type，避免这个坑一直传下去。
    只处理文本扩展名且无 NUL 的现存文件，最多 50 个。
    """
    fixed = 0
    for rel in (paths or [])[:50]:
        if not _diff_as_text(root, rel):
            continue
        try:
            res = _run(["svn", "propget", "svn:mime-type", rel], root)
            mime = (res["out"] or "").strip().lower()
            if res["ok"] and mime.startswith("application/octet-stream"):
                _run(["svn", "propset", "svn:mime-type",
                      "text/plain; charset=utf-8", rel], root)
                fixed += 1
        except Exception:
            continue
    return fixed


def _svn_commit(root: Path, message: str, paths: List[str]) -> Dict[str, Any]:
    # 显式声明说明是 UTF-8：配合 _run 里的 LC_ALL，中文提交在桌面端不再 E000022。
    targets = list(paths or [])
    if not targets:
        targets = [e["path"] for e in _svn_status_entries(root)
                   if e["state"] in ("modified", "added")]
    _repair_text_mime(root, targets)
    args = ["svn", "commit", "--encoding", "UTF-8", "-m", message]
    if paths:
        args += paths
    res = _run(args, root)
    if not res["ok"]:
        raise VcsError((res["err"] or res["out"] or "svn commit 失败").strip())
    return {"ok": True, "output": res["out"].strip()}


def _svn_discard(root: Path, paths: List[str]) -> Dict[str, Any]:
    if not paths:
        raise VcsError("需要至少一个文件路径")
    res = _run(["svn", "revert", "--"] + paths, root)
    if not res["ok"]:
        raise VcsError(res["err"] or "svn revert 失败")
    return {"ok": True, "output": res["out"].strip(), "paths": paths}


def _svn_switch(root: Path, branch: str) -> Dict[str, Any]:
    info = _svn_info(root)
    repo_root = info.get("repository root") or info.get("repository url") or ""
    target = branch.strip()
    if target.startswith(("http://", "https://", "svn://", "file://")):
        url = target
    else:
        if not repo_root:
            raise VcsError("无法解析仓库根 URL，无法切换分支")
        name = _safe_name(target, "分支名")
        url = f"{repo_root}/branches/{name}" if name != "trunk" else repo_root
    res = _run(["svn", "switch", url], root)
    if not res["ok"]:
        raise VcsError(res["err"] or "svn switch 失败")
    return {"ok": True, "output": res["out"].strip(), "url": url}


# ── 统一入口 ─────────────────────────────────────────────────────────

def summary(ws_id: str) -> Dict[str, Any]:
    root = _require_root(ws_id, None)
    return _git_summary(root) if detect(ws_id) == "git" else _svn_summary(root)


def status(ws_id: str) -> Dict[str, Any]:
    root = _require_root(ws_id, None)
    entries = _git_status_entries(root) if detect(ws_id) == "git" else _svn_status_entries(root)
    return {"type": detect(ws_id), "entries": entries}


def log(ws_id: str, limit: int = 20) -> Dict[str, Any]:
    root = _require_root(ws_id, None)
    commits = _git_log(root, limit) if detect(ws_id) == "git" else _svn_log(root, limit)
    return {"type": detect(ws_id), "commits": commits}


def diff(ws_id: str, path: Optional[str] = None, rev: Optional[str] = None) -> Dict[str, Any]:
    root = _require_root(ws_id, None)
    rel = _validated_paths(ws_id, [path])[0] if path else None
    r = _safe_rev(rev) if rev else None
    if detect(ws_id) == "git":
        text = _git_diff(root, rel, r, ws_id)
    else:
        text = _svn_diff(root, rel, r)
    return {"type": detect(ws_id), "diff": text, "path": rel, "rev": r}


def branches(ws_id: str) -> Dict[str, Any]:
    root = _require_root(ws_id, None)
    return _git_branches(root) if detect(ws_id) == "git" else _svn_branches(root)


def remotes(ws_id: str) -> Dict[str, Any]:
    root = _require_root(ws_id, "git")
    return _git_remotes(root)


def remote_add(ws_id: str, name: str, url: str) -> Dict[str, Any]:
    root = _require_root(ws_id, "git")
    return _git_remote_add(root, _safe_remote(name), _safe_url(url))


def remote_remove(ws_id: str, name: str) -> Dict[str, Any]:
    root = _require_root(ws_id, "git")
    return _git_remote_remove(root, _safe_remote(name))


def remote_set_url(ws_id: str, name: str, url: str,
                   push: bool = False) -> Dict[str, Any]:
    root = _require_root(ws_id, "git")
    return _git_remote_set_url(root, _safe_remote(name), _safe_url(url), push=push)


def fetch(ws_id: str, remote: Optional[str] = None) -> Dict[str, Any]:
    root = _require_root(ws_id, "git")
    name = _safe_remote(remote) if remote else None
    return _git_fetch(root, name)


def switch(ws_id: str, branch: str, create: bool = False) -> Dict[str, Any]:
    root = _require_root(ws_id, None)
    if detect(ws_id) == "git":
        name = _safe_name(branch, "分支名")
        if create:
            res = _run(["git", "checkout", "-B", name], root)
        else:
            res = _run(["git", "checkout", name], root)
        if not res["ok"]:
            raise VcsError(res["err"] or res["out"] or f"切换分支 {name} 失败")
        return {"ok": True, "output": res["out"].strip(), "branch": name}
    return _svn_switch(root, branch)


def commit(ws_id: str, message: str, paths: Optional[List[str]] = None,
           all_changes: bool = False) -> Dict[str, Any]:
    root = _require_root(ws_id, None)
    msg = str(message or "").strip()
    if not msg:
        raise VcsError("提交信息不能为空")
    rel = _validated_paths(ws_id, paths or [])
    if detect(ws_id) == "git":
        return _git_commit(root, msg, rel, all_changes)
    return _svn_commit(root, msg, rel)


def stage(ws_id: str, paths: List[str]) -> Dict[str, Any]:
    root = _require_root(ws_id, "git")
    return _git_stage(root, _validated_paths(ws_id, paths), True)


def unstage(ws_id: str, paths: List[str]) -> Dict[str, Any]:
    root = _require_root(ws_id, "git")
    return _git_stage(root, _validated_paths(ws_id, paths), False)


def discard(ws_id: str, paths: List[str]) -> Dict[str, Any]:
    root = _require_root(ws_id, None)
    rel = _validated_paths(ws_id, paths)
    if detect(ws_id) == "git":
        return _git_discard(root, rel)
    return _svn_discard(root, rel)

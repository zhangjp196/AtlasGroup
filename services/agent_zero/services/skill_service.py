"""技能库：data/agent_zero/skills/<id>/SKILL.md + sqlite 索引。

技能 = 一份可复用的操作指南（SKILL.md，可带附属文件）。
来源：本地创建 / 上传（.md 或 .zip） / URL 抓取 / npm 安装 /
      `npx skills add <owner/repo>`（vercel-labs/skills CLI）。
模型侧：系统提示列出可用技能，`use_skill` 读取正文，`create_skill` 写入。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_db_path, get_session_local
from services.agent_zero.stores import zero_store
from services.agent_zero.stores.zero_store import _q

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 60_000
MAX_UPLOAD_BYTES = 5_000_000


def skills_root() -> Path:
    root = Path(Path(get_db_path()).parent) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "-", (name or "").strip()).strip("-")
    return (slug or "skill")[:60].lower()


def _safe_rel(name: str) -> str:
    """附属文件的相对路径（保留目录结构，每段清洗非法字符）。"""
    parts = [re.sub(r"[^A-Za-z0-9_\-.]", "_", str(seg))[:80]
             for seg in str(name).split("/") if str(seg).strip()]
    return "/".join(parts)


def list_skills(enabled_only: bool = False) -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('skills')} "
                 f"{'WHERE enabled = 1' if enabled_only else ''} "
                 f"ORDER BY created_at DESC LIMIT 200"),
        ).mappings().all()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "name": r["name"],
            "description": r["description"] or "",
            "source": r["source"] or "local",
            "enabled": bool(r["enabled"]),
            "created_at": str(r["created_at"] or ""),
            "updated_at": str(r["updated_at"] or ""),
            "files": _dir_files(r["path"]),
        })
    return out


def _dir_files(rel_path: str) -> List[str]:
    if not rel_path:
        return []
    d = skills_root() / rel_path
    if not d.is_dir():
        return []
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())[:50]


def get_skill(skill_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('skills')} WHERE id = :id"), {"id": skill_id},
        ).mappings().first()
    return dict(row) if row else None


def find_skill(name_or_id: str) -> Optional[Dict[str, Any]]:
    needle = (name_or_id or "").strip().lower()
    if not needle:
        return None
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('skills')} WHERE id = :n "
                 f"OR lower(name) = :n OR lower(name) = :s COLLATE NOCASE LIMIT 1"),
            {"n": needle, "s": _slug(needle)},
        ).mappings().first()
    return dict(row) if row else None


def create_skill(name: str, description: str, content: str,
                 source: str = "local", extra_files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("技能名称不能为空")
    content = (content or "").strip()
    if not content:
        raise ValueError("技能内容不能为空")
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS]

    sid = zero_store.new_id("skill")
    rel = f"{_slug(name)}-{sid[6:]}"
    skill_dir = skills_root() / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    for fname, ftext in (extra_files or {}).items():
        rel = _safe_rel(fname)
        if rel:
            target = skill_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(ftext), encoding="utf-8")

    SessionLocal = get_session_local()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('skills')} "
                 f"(id, name, description, path, source, enabled, created_at, updated_at) "
                 f"VALUES (:id, :n, :d, :p, :s, 1, :c, :u)"),
            {"id": sid, "n": name[:120], "d": (description or "")[:500],
             "p": rel, "s": source, "c": now, "u": now},
        )
        db.commit()
    zero_store.append_audit("local", "skill_create", args={"skill": sid, "name": name})
    return get_skill(sid) or {"id": sid, "name": name}


def update_skill(skill_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    skill = get_skill(skill_id)
    if not skill:
        raise ValueError(f"技能不存在: {skill_id}")
    name = str(patch.get("name") or skill["name"]).strip() or skill["name"]
    description = patch.get("description", skill["description"])
    content = patch.get("content")
    enabled = patch.get("enabled", skill["enabled"])

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('skills')} SET name = :n, description = :d, enabled = :e, "
                 f"updated_at = datetime('now','localtime') WHERE id = :id"),
            {"id": skill_id, "n": name[:120], "d": (description or "")[:500],
             "e": 1 if enabled else 0},
        )
        db.commit()

    if content is not None:
        skill_dir = skills_root() / (skill["path"] or "")
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(str(content)[:MAX_CONTENT_CHARS], encoding="utf-8")
    return get_skill(skill_id) or skill


def delete_skill(skill_id: str) -> bool:
    skill = get_skill(skill_id)
    if not skill:
        return False
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(text(f"DELETE FROM {_q('skills')} WHERE id = :id"), {"id": skill_id})
        db.commit()
    skill_dir = skills_root() / (skill["path"] or "")
    if skill_dir.is_dir():
        shutil.rmtree(skill_dir, ignore_errors=True)
    return True


def read_skill_text(skill: Dict[str, Any]) -> str:
    path = skills_root() / (skill.get("path") or "") / "SKILL.md"
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_CONTENT_CHARS]
    except Exception:
        return ""


def zip_skill(skill_id: str) -> bytes:
    skill = get_skill(skill_id)
    if not skill:
        raise ValueError(f"技能不存在: {skill_id}")
    skill_dir = skills_root() / (skill["path"] or "")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if skill_dir.is_dir():
            for p in skill_dir.rglob("*"):
                if p.is_file():
                    zf.writestr(p.relative_to(skill_dir).as_posix(),
                                p.read_bytes())
        else:
            zf.writestr("SKILL.md", "")
    return buf.getvalue()


def import_upload(filename: str, data: bytes) -> Dict[str, Any]:
    """上传 .md / .zip 建技能。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"上传文件过大（上限 {MAX_UPLOAD_BYTES} 字节）")
    name = (filename or "skill").rsplit("/", 1)[-1]
    if name.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except Exception as e:
            raise ValueError(f"无效的 zip 文件: {e}")
        names = [n for n in zf.namelist() if not n.startswith("__MACOSX")]
        md_name = next((n for n in names if n.lower().endswith("skill.md")
                        or n.lower().endswith(".md")), None)
        if not md_name:
            raise ValueError("zip 里未找到 SKILL.md / .md 文件")
        content = zf.read(md_name).decode("utf-8", "replace")
        extra = {n: zf.read(n).decode("utf-8", "replace")
                 for n in names if n != md_name and not n.lower().endswith((".png", ".jpg", ".gif"))}
        parent = md_name.rsplit("/", 1)[0] if "/" in md_name else ""
        # 名字：SKILL.md 的标题 > zip 里的目录名 > 上传的文件名
        title = next((l.strip().lstrip("#").strip() for l in content.splitlines()
                      if l.strip().startswith("#")), "")
        base = title or parent or (name[:-4] if name.lower().endswith(".zip") else name)
        return create_skill(base or "skill", _first_heading(content), content,
                            source="upload", extra_files=extra)
    content = data.decode("utf-8", "replace")
    base = name.rsplit(".", 1)[0] if "." in name else name
    return create_skill(base or "skill", _first_heading(content), content, source="upload")


def import_url(url: str, name: str = "") -> Dict[str, Any]:
    import httpx

    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    content = resp.text
    title = name or url.rstrip("/").rsplit("/", 1)[-1] or "skill"
    return create_skill(title, _first_heading(content), content, source="url")


def _first_heading(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 徽章 / HTML / 代码围栏 / 表格分隔行不算「描述」
        if line.startswith(("![", "[!", "<", "```", "---", "|")):
            continue
        return line[:200]
    return ""


async def install_from_npm(package: str, name: str = "") -> Dict[str, Any]:
    """npm 安装一个技能包，把包目录里的 SKILL.md 装进技能库。"""
    pkg = (package or "").strip()
    if not pkg:
        return {"ok": False, "error": "缺少包名"}
    if not shutil.which("npm"):
        return {"ok": False, "error": "本机未安装 npm，无法自动安装技能"}
    if not re.match(r"^[A-Za-z0-9_./@\-]{1,120}$", pkg):
        return {"ok": False, "error": f"非法包名: {pkg}"}

    root = skills_root() / "_npm"
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "package.json").exists():
        (root / "package.json").write_text(json.dumps(
            {"name": "agent-zero-skills", "version": "1.0.0", "private": True}),
            encoding="utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "--no-audit", "--no-fund", pkg,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "npm install 超时（180s）"}
    except Exception as e:
        return {"ok": False, "error": f"npm install 失败: {e}"}
    if proc.returncode != 0:
        return {"ok": False, "error": (err or b"").decode("utf-8", "replace")[-800:]}

    if pkg.startswith("@"):
        pkg_name = pkg.split("/")[1].split("@")[0] if "/" in pkg else pkg
    else:
        pkg_name = pkg.split("/")[0].split("@")[0]
    pkg_dir = root / "node_modules" / pkg_name
    if not pkg_dir.is_dir():
        return {"ok": False, "error": f"安装后未找到 node_modules/{pkg_name}"}
    md = next((p for p in pkg_dir.rglob("*.md")
               if p.name.lower() in ("skill.md", "skills.md", "readme.md")), None)
    if not md:
        return {"ok": False, "error": f"包 {pkg} 内没有 SKILL.md / README.md"}
    text_ext = (".md", ".txt", ".json", ".js", ".ts", ".py", ".yaml", ".yml", ".css", ".html")
    extra = {p.relative_to(pkg_dir).as_posix(): p.read_text(encoding="utf-8", errors="replace")
             for p in pkg_dir.rglob("*")
             if p.is_file() and p != md and p.suffix.lower() in text_ext
             and p.stat().st_size < 200_000}
    skill = create_skill(name or pkg_name, _first_heading(md.read_text(encoding="utf-8", errors="replace")),
                         md.read_text(encoding="utf-8", errors="replace"),
                         source=f"npm:{pkg}", extra_files=extra)
    return {"ok": True, "skill": skill, "output": (out or b"").decode("utf-8", "replace")[-600:]}


# ── npx skills（vercel-labs/skills CLI）───────────────────────────────────

SKILLS_CLI_TIMEOUT = 300
MAX_SKILLS_PER_SOURCE = 30
_CLI_TEXT_EXT = (".md", ".txt", ".json", ".js", ".ts", ".py", ".yaml", ".yml",
                 ".sh", ".css", ".html", ".sql")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _cli_workdir(source: str) -> Path:
    root = skills_root() / "_npx"
    root.mkdir(parents=True, exist_ok=True)
    work = root / _slug(source)
    work.mkdir(parents=True, exist_ok=True)
    return work


async def _skills_cli(argv: List[str], cwd: Path, timeout: int = SKILLS_CLI_TIMEOUT):
    env = dict(os.environ)
    env["DISABLE_TELEMETRY"] = "1"
    proc = await asyncio.create_subprocess_exec(
        "npx", "--yes", "skills", *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", f"npx skills 超时（{timeout}s）"
    return proc.returncode, (out or b"").decode("utf-8", "replace"), \
        (err or b"").decode("utf-8", "replace")


def _norm_source(source: str) -> str:
    src = (source or "").strip()
    if not src:
        raise ValueError("缺少技能源，例如 vercel-labs/agent-skills")
    if not re.match(r"^[A-Za-z0-9_./:@\-]{2,160}$", src):
        raise ValueError(f"非法技能源: {src}")
    return src


def _norm_skills(skills: Any) -> List[str]:
    if isinstance(skills, str):
        items = re.split(r"[,\s]+", skills)
    else:
        items = [str(s) for s in (skills or [])]
    return [s.strip() for s in items if s.strip()][:MAX_SKILLS_PER_SOURCE]


def _frontmatter(text: str) -> Dict[str, str]:
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n", text or "", re.S)
    if not m:
        return {}
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip().lower()] = val.strip().strip("\"'").strip()
    return meta


def _import_skill_dir(skill_dir: Path, source: str) -> Dict[str, Any]:
    md = skill_dir / "SKILL.md"
    text = md.read_text(encoding="utf-8", errors="replace")
    meta = _frontmatter(text)
    name = meta.get("name") or skill_dir.name or "skill"
    description = meta.get("description") or _first_heading(text)
    extra = {p.relative_to(skill_dir).as_posix(): p.read_text(encoding="utf-8", errors="replace")
             for p in skill_dir.rglob("*")
             if p.is_file() and p != md and p.suffix.lower() in _CLI_TEXT_EXT
             and p.stat().st_size < 200_000}

    existing = find_skill(name)
    if existing:
        skill = update_skill(existing["id"], {"description": description, "content": text})
        skill_dir = skills_root() / (existing.get("path") or "")
        skill_dir.mkdir(parents=True, exist_ok=True)
        # 重新导入时镜像源目录：先清掉旧版附属文件，避免残留上一版本
        for stale in list(skill_dir.rglob("*")):
            if stale.is_file() and stale.name != "SKILL.md":
                stale.unlink()
        for fname, ftext in extra.items():
            rel = _safe_rel(fname)
            if rel:
                target = skill_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(ftext, encoding="utf-8")
        return skill
    return create_skill(name, description, text, source=f"skills:{source}", extra_files=extra)


async def install_from_skills_cli(source: str, skills: Any = None,
                                  agent: str = "claude-code") -> Dict[str, Any]:
    """用 `npx skills add <owner/repo>` 安装 Agent Skills，导入到技能库。

    安装只发生在 data/agent_zero/skills/_npx/<源>/ 里（项目级），不改动用户其它 agent 目录。
    """
    try:
        src = _norm_source(source)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not shutil.which("npx"):
        return {"ok": False, "error": "本机未安装 npx/npm，无法使用 npx skills 安装技能"}

    work = _cli_workdir(src)
    picked = _norm_skills(skills)
    argv = ["add", src, "-y", "-a", agent, "--copy", "--full-depth", "--json"]
    if picked:
        for s in picked:
            argv += ["-s", s]
    else:
        argv += ["-s", "*"]

    code, out, err = await _skills_cli(argv, work)

    # --json 给出每个技能的安装状态与路径
    results: List[Dict[str, Any]] = []
    try:
        results = json.loads(_strip_ansi(out).strip() or "[]")
    except Exception:
        results = []

    installed = [r for r in results if isinstance(r, dict)
                 and r.get("status") == "installed" and r.get("path")]
    if not installed:
        # 兜底：扫描安装目录里的 SKILL.md
        for p in work.rglob("*"):
            if p.is_file() and p.name.lower() == "skill.md":
                installed.append({"name": p.parent.name, "path": str(p.parent)})

    if not installed and code != 0:
        return {"ok": False, "error": _strip_ansi(err or out)[-800:]}

    imported = []
    for r in installed[:MAX_SKILLS_PER_SOURCE]:
        d = Path(r["path"])
        if not (d / "SKILL.md").is_file():
            continue
        imported.append(_import_skill_dir(d, f"{src}/{r.get('name') or d.name}"))
    skipped = max(0, len(installed) - MAX_SKILLS_PER_SOURCE)
    return {
        "ok": True,
        "source": src,
        "installed": len(installed),
        "imported": len(imported),
        "skipped": skipped,
        "skills": imported,
        "security": [r.get("security") for r in installed[:MAX_SKILLS_PER_SOURCE] if r.get("security")],
        "output": _strip_ansi(out)[-500:],
    }


async def list_skills_cli(source: str) -> Dict[str, Any]:
    """列出某个源里可用的技能名（不安装）。"""
    try:
        src = _norm_source(source)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not shutil.which("npx"):
        return {"ok": False, "error": "本机未安装 npx/npm"}
    work = _cli_workdir(src)
    cache = work / ".skills-list.json"
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - cache.stat().st_mtime < 6 * 3600:
                return {"ok": True, "source": src, "skills": cached.get("skills", []),
                        "cached": True, "output": ""}
        except Exception:
            pass
    code, out, err = await _skills_cli(["add", src, "-l", "--full-depth"], work, timeout=180)
    if code != 0:
        return {"ok": False, "error": _strip_ansi(err or out)[-600:]}
    text = _strip_ansi(out)
    names = re.findall(
        r"^\s*[│|]\s{4}([A-Za-z0-9_][A-Za-z0-9_\-\u4e00-\u9fff.]*)\s*$", text, re.M)
    if not names:
        names = re.findall(r"^\s*[│|]*\s*-\s+([A-Za-z0-9_\-\u4e00-\u9fff./]+)\s*$", text, re.M)
    seen: List[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    try:
        cache.write_text(json.dumps({"source": src, "skills": seen}, ensure_ascii=False),
                         encoding="utf-8")
    except Exception:
        pass
    return {"ok": True, "source": src, "skills": seen, "output": text[-300:]}


async def search_skills_registry(query: str, limit: int = 30) -> Dict[str, Any]:
    """在 skills.sh 注册表里搜索技能（比 `npx skills find` 更适合非交互调用）。"""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "缺少搜索关键词"}
    try:
        import httpx

        resp = httpx.get("https://skills.sh/api/search", params={"q": q},
                         timeout=20, headers={"User-Agent": "agent-zero/1.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"ok": False, "error": f"搜索失败: {e}"}
    items = []
    for s in (data.get("skills") or [])[:limit]:
        source = str(s.get("source") or "").strip()
        skill = str(s.get("skillId") or s.get("name") or "").strip()
        if not source or not skill:
            continue
        items.append({"source": source, "skill": skill, "name": s.get("name") or skill,
                      "installs": int(s.get("installs") or 0)})
    return {"ok": True, "query": q, "items": items}


def skills_prompt() -> str:
    """系统提示里的技能清单（只有启用的技能）。"""
    skills = list_skills(enabled_only=True)
    if not skills:
        return ""
    lines = [f"- **{s['name']}**：{s['description'] or '（无描述）'}" for s in skills]
    return (
        "## 可用技能（可复用的操作指南）\n"
        "任务与某个技能匹配时，先用 use_skill 读取它的完整内容，再按其中的步骤执行；"
        "如果你总结出一套可复用的做法，用 create_skill 写入技能库。\n" + "\n".join(lines)
    )

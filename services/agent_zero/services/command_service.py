"""自定义斜杠命令（Slash Commands）——对齐 Claude Code / OpenCode 的扩展方式。

命令就是一个 Markdown 文件，文件名即命令名：

- 工作区：``<workspace>/.atlas/commands/*.md``
- 兼容 Claude Code：``<workspace>/.claude/commands/*.md``
- 应用全局：``data/agent_zero/commands/*.md``
- 兼容用户级 Claude Code：``~/.claude/commands/*.md``
- 内置：init / review / test / commit（可被同名文件覆盖）

支持子目录命名空间（``git/review.md`` → ``/git:review``）。文件可带 YAML
frontmatter：

.. code-block:: markdown

    ---
    description: 审查当前改动
    argument-hint: "[path]"
    ---
    请审查 @src/app.py 的改动：$ARGUMENTS

占位符：``$ARGUMENTS``（全部参数）、``$1``…``$9``（按位置）。
``@相对路径`` 会在命令展开时读取工作区文件内容（文本 / Office / PDF 走
document_service，单文件 30k 字符、总量 60k 字符封顶），避免模型再花一轮去读。

命令文件是本地用户自己写的，和 AGENTS.md 同信任级；不支持在命令里执行 shell
（``!`cmd` ``），需要命令式副作用时请让模型调用工具，走正常权限审批。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from services.shared.project_paths import get_project_root, project_data

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 120_000
MAX_FILE_CHARS = 30_000
MAX_FILES_TOTAL_CHARS = 60_000
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff:]{1,64}$")
_CALL_RE = re.compile(r"^/([A-Za-z0-9_\-\u4e00-\u9fff:]+)(?:[ \t\r\n]+([\s\S]*))?$")
_BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
               ".zip", ".gz", ".tar", ".7z", ".rar", ".exe", ".dll", ".so",
               ".dylib", ".bin", ".mp3", ".mp4", ".mov", ".wav", ".woff",
               ".woff2", ".ttf", ".otf"}

# 内置命令：source=builtin，文件名可覆盖（工作区 > 全局 > 内置）。
BUILTIN_COMMANDS: Dict[str, Dict[str, str]] = {
    "init": {
        "description": "分析工作区并生成 / 更新 AGENTS.md 项目约定",
        "argument_hint": "[补充说明]",
        "body": (
            "请分析当前工作区，并在仓库根目录创建或**增量更新** `AGENTS.md`。"
            "内容只写能从仓库中核实的事实：项目用途、目录结构、常用命令"
            "（安装 / 启动 / 测试 / 构建）、代码风格与约定、测试方式、容易踩的坑。"
            "已有 `AGENTS.md` 时保留仍然正确的部分，补齐缺失项，不要整篇重写。"
            "不要编造不存在的脚本或目录。\n\n用户补充：$ARGUMENTS"
        ),
    },
    "review": {
        "description": "审查当前未提交改动，按严重程度列出问题",
        "argument_hint": "[路径或范围]",
        "body": (
            "请审查当前工作区**尚未提交**的改动（用 `dev{op:vcs, action:diff}` 或 "
            "`git diff` / `git status` 获取；不要假设改动内容）。"
            "按严重程度从高到低列出问题：正确性 bug、安全风险、性能问题、可维护性。"
            "每条给出文件与行号、问题原因、建议改法；没有问题的方面明确说没有。"
            "**只审查、不修改代码**，最后给出一句总体结论。\n\n"
            "审查范围：$ARGUMENTS"
        ),
    },
    "plan": {
        "description": "先列出可执行计划，再按计划推进",
        "argument_hint": "[任务说明]",
        "body": (
            "请先为下面的任务列出可执行计划：用 `todo_write` 写清步骤、验证方式和风险，"
            "然后直接按计划开始推进；每完成一步就更新清单，不要只给计划不动手。"
            "如果任务还需要澄清，只问最关键的一个问题。\n\n任务：$ARGUMENTS"
        ),
    },
    "test": {
        "description": "运行测试并修复失败，直到通过",
        "argument_hint": "[测试命令或路径]",
        "body": (
            "请先识别本项目的测试方式（读 README / 构建文件 / 现有测试目录），"
            "然后运行测试。若有失败，定位根因并**修改代码**修复，再重跑，"
            "直到全部通过或确认是环境 / 依赖缺失无法继续。"
            "不要为了让测试通过而删除或跳过测试；最后总结跑了什么、改了什么。\n\n"
            "指定范围：$ARGUMENTS"
        ),
    },
    "commit": {
        "description": "根据当前改动生成提交信息并提交（不 push）",
        "argument_hint": "[提交说明补充]",
        "body": (
            "请查看 `git status` 与 `git diff`，把本次改动整理成一次提交："
            "提交信息遵循仓库现有风格（读最近几条 `git log`），说明“为什么改”而不只是"
            "“改了什么”。只提交与本次任务相关的文件，不要提交密钥、日志或构建产物；"
            "**不要 push**。提交完成后报告 commit hash 与改动摘要。\n\n"
            "补充说明：$ARGUMENTS"
        ),
    },
}

_CACHE: Dict[str, Any] = {"at": 0.0, "workspace": None, "commands": []}
_CACHE_TTL_S = 2.0


def global_commands_dir() -> Path:
    return project_data("agent_zero", "commands")


def user_commands_dir() -> Path:
    return Path.home() / ".claude" / "commands"


def workspace_commands_dirs(workspace_id: Optional[str]) -> List[Path]:
    """工作区命令目录（按优先级从高到低）。纯聊天工作区返回空。"""
    if not workspace_id:
        return []
    try:
        from services.agent_zero.services import workspace_service

        root = workspace_service.get_workspace_root("task", workspace_id)
    except Exception:
        return []
    return [root / ".atlas" / "commands", root / ".claude" / "commands"]


def _parse_file(path: Path, source: str, root: Optional[Path]) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("读取命令文件失败 %s: %s", path, e)
        return None
    meta: Dict[str, Any] = {}
    body = raw
    if raw.lstrip().startswith("---"):
        text = raw.lstrip()
        end = text.find("\n---", 3)
        if end > 0:
            front = text[3:end]
            body = text[end + 4:]
            try:
                loaded = yaml.safe_load(front)
                if isinstance(loaded, dict):
                    meta = loaded
            except yaml.YAMLError as e:
                logger.warning("命令 frontmatter 解析失败 %s: %s", path, e)
    if root is not None:
        try:
            rel = path.relative_to(root).with_suffix("")
            name = ":".join(part for part in rel.parts if part)
        except ValueError:
            name = path.stem
    else:
        name = path.stem
    name = str(meta.get("name") or name).strip().lstrip("/")
    if not _NAME_RE.match(name):
        logger.warning("命令名不合法，已跳过: %s", name)
        return None
    body = (body or "").strip()
    if not body:
        return None
    description = str(meta.get("description") or "").strip()
    if not description:
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                description = line[:160]
                break
    try:
        timeout = float(meta.get("timeout") or 0)
    except (TypeError, ValueError):
        timeout = 0.0
    return {
        "name": name,
        "description": description or name,
        "argument_hint": str(meta.get("argument-hint") or meta.get("argument_hint") or "").strip(),
        "body": body,
        "path": str(path),
        "source": source,
        "builtin": False,
        "allowed_tools": meta.get("allowed-tools") or meta.get("allowed_tools") or [],
        "model": str(meta.get("model") or "").strip(),
        "timeout": timeout,
    }


def _scan(root: Path, source: str) -> List[Dict[str, Any]]:
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    try:
        files = sorted(p for p in root.rglob("*.md") if p.is_file())
    except OSError:
        return []
    for path in files:
        cmd = _parse_file(path, source, root)
        if cmd:
            out.append(cmd)
    return out


def _builtin_commands() -> List[Dict[str, Any]]:
    return [{
        "name": name,
        "description": spec["description"],
        "argument_hint": spec.get("argument_hint", ""),
        "body": spec["body"],
        "path": "",
        "source": "builtin",
        "builtin": True,
        "allowed_tools": [],
        "model": "",
        "timeout": 0.0,
    } for name, spec in BUILTIN_COMMANDS.items()]


def list_commands(workspace_id: Optional[str] = None,
                  *, refresh: bool = False) -> List[Dict[str, Any]]:
    """全部可用命令（同名以更局部的定义为准）。"""
    now = time.monotonic()
    if (not refresh and now - float(_CACHE.get("at") or 0) < _CACHE_TTL_S
            and _CACHE.get("workspace") == (workspace_id or "")):
        return [dict(c) for c in (_CACHE.get("commands") or [])]

    found: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(items: List[Dict[str, Any]]) -> None:
        for item in items:
            name = item["name"]
            if name in seen:
                continue
            seen.add(name)
            found.append(item)

    for root in workspace_commands_dirs(workspace_id):
        source = "workspace" if root.name == "commands" and root.parent.name == ".atlas" else "claude"
        _add(_scan(root, source))
    _add(_scan(global_commands_dir(), "global"))
    _add(_scan(user_commands_dir(), "user"))
    _add(_builtin_commands())

    _CACHE.update(at=now, workspace=workspace_id or "", commands=found)
    return [dict(c) for c in found]


def get_command(workspace_id: Optional[str], name: str) -> Optional[Dict[str, Any]]:
    name = str(name or "").strip().lstrip("/")
    for cmd in list_commands(workspace_id):
        if cmd["name"] == name:
            return cmd
    return None


def invalidate() -> None:
    _CACHE.update(at=0.0, workspace=None, commands=[])


# ── 展开 ──────────────────────────────────────────────────────────────────

def _split_args(raw: str) -> List[str]:
    try:
        import shlex

        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _read_path_text(path: Path) -> str:
    """文本直接读；Office / PDF 走 document_service 抽取。"""
    from services.agent_zero.services import document_service

    if document_service.is_document(path.name):
        return str(document_service.extract_text(path) or "")
    raw = path.read_bytes()[:2_000_000]
    for enc in ("utf-8-sig", "utf-16", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _include_files(workspace_id: Optional[str], text: str) -> tuple[str, List[str]]:
    """把 ``@相对路径`` 展开成文件内容附在命令末尾（只读、受工作区边界约束）。"""
    from services.agent_zero.services import workspace_service

    refs = re.findall(r"(?<![\w@])@([^\s@，。；、）)\]]+)", text or "")
    if not refs:
        return "", []
    blocks: List[str] = []
    used: List[str] = []
    total = 0
    for raw in refs:
        rel = raw.strip().strip("'\"`")
        if not rel or rel.startswith(("/", "~")):
            continue
        if total >= MAX_FILES_TOTAL_CHARS:
            break
        try:
            path = workspace_service.resolve_workspace_path(workspace_id, rel)
            if not path.is_file():
                continue
            if path.suffix.lower() in _BINARY_EXT:
                blocks.append(f"[@{rel} 是二进制文件，未内联；请让模型用工具查看]")
                continue
            extracted = _read_path_text(path)
        except Exception as e:
            blocks.append(f"[未能读取 @{rel}: {type(e).__name__}]")
            continue
        content = str(extracted or "")
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n…（已截断）"
        remaining = MAX_FILES_TOTAL_CHARS - total
        if len(content) > remaining:
            content = content[:remaining] + "\n…（总量已达上限）"
        total += len(content)
        used.append(rel)
        blocks.append(f"--- 文件: {rel} ---\n{content}")
    if not blocks:
        return "", []
    return "\n\n[命令引用的文件内容]\n" + "\n\n".join(blocks), used


def _expand_body(body: str, args: str) -> str:
    positional = _split_args(args or "")
    out = body.replace("$ARGUMENTS", args or "")
    for i in range(1, 10):
        value = positional[i - 1] if len(positional) >= i else ""
        out = out.replace(f"${i}", value)
    if args and "$ARGUMENTS" not in body and "$1" not in body:
        out = f"{out}\n\n参数：{args}"
    return out


def resolve(workspace_id: Optional[str], text: str) -> Optional[Dict[str, Any]]:
    """把一条以 ``/name args`` 开头的用户消息展开成发给模型的提示。

    不是已注册命令时返回 None（例如用户真的想发一个 ``/Users/...`` 路径）。
    """
    raw = (text or "").strip()
    match = _CALL_RE.match(raw)
    if not match:
        return None
    name, args = match.group(1), (match.group(2) or "").strip()
    cmd = get_command(workspace_id, name)
    if not cmd:
        return None
    body = _expand_body(cmd["body"], args)
    includes, files = _include_files(workspace_id, body)
    prompt = (
        f"[自定义命令 /{cmd['name']}]\n"
        f"（命令说明：{cmd['description']}；定义来源："
        f"{'内置' if cmd.get('builtin') else cmd.get('path') or cmd.get('source')}）\n\n"
        f"{body}{includes}"
    )
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS] + "\n\n…（命令展开超长，已截断）"
    return {
        "name": cmd["name"],
        "description": cmd["description"],
        "args": args,
        "prompt": prompt,
        "files": files,
        "source": cmd.get("source") or "",
    }


# ── 写入（设置页）─────────────────────────────────────────────────────────

def _safe_segments(name: str) -> List[str]:
    name = str(name or "").strip().lstrip("/")
    if not _NAME_RE.match(name):
        raise ValueError("命令名只能包含字母、数字、下划线、短横线、中文与冒号（最长 64）")
    parts = [p for p in name.replace(":", "/").split("/") if p]
    if not parts:
        raise ValueError("命令名为空")
    return [re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", p)[:64] for p in parts]


def create_command(workspace_id: Optional[str], name: str, body: str,
                   description: str = "", argument_hint: str = "") -> Dict[str, Any]:
    """在工作区 ``.atlas/commands`` 写一个命令文件。"""
    body = str(body or "").strip()
    if not body:
        raise ValueError("命令正文不能为空")
    dirs = workspace_commands_dirs(workspace_id)
    if not dirs:
        raise ValueError("纯聊天工作区没有目录，无法保存命令；请先添加一个本地工作区")
    root = dirs[0]
    rel = Path(*_safe_segments(name)).with_suffix(".md")
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    meta: Dict[str, Any] = {}
    if description.strip():
        meta["description"] = description.strip()[:300]
    if argument_hint.strip():
        meta["argument-hint"] = argument_hint.strip()[:120]
    text = body
    if meta:
        text = ("---\n" + yaml.safe_dump(meta, allow_unicode=True,
                                         sort_keys=False) + "---\n" + body)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")
    invalidate()
    return {"ok": True, "name": name, "path": str(target),
            "commands": _public_list(workspace_id)}


def delete_command(workspace_id: Optional[str], name: str) -> Dict[str, Any]:
    """删除工作区内的命令文件（内置 / 全局命令不可删）。"""
    cmd = get_command(workspace_id, name)
    if not cmd:
        raise ValueError("命令不存在")
    if cmd.get("builtin") or cmd.get("source") not in ("workspace", "claude"):
        raise ValueError("只能删除当前工作区里的命令")
    try:
        Path(cmd["path"]).unlink()
    except OSError as e:
        raise ValueError(f"删除失败: {e}") from e
    invalidate()
    return {"ok": True, "name": name, "commands": _public_list(workspace_id)}


def _public_list(workspace_id: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for cmd in list_commands(workspace_id, refresh=True):
        out.append({
            "name": cmd["name"],
            "description": cmd["description"],
            "argument_hint": cmd.get("argument_hint") or "",
            "source": cmd.get("source") or "",
            "builtin": bool(cmd.get("builtin")),
            "path": cmd.get("path") or "",
        })
    return out


def summarize(workspace_id: Optional[str]) -> Dict[str, Any]:
    """设置页 / 诊断摘要。"""
    return {
        "global_dir": str(global_commands_dir()),
        "workspace_dirs": [str(p) for p in workspace_commands_dirs(workspace_id)],
        "commands": _public_list(workspace_id),
    }

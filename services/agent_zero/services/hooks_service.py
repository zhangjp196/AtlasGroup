"""工作区钩子（Hooks）——对齐 Claude Code 的 PreToolUse / PostToolUse 等扩展点。

配置文件（JSON，均可不建）：

- 全局：``data/agent_zero/hooks.json``
- 工作区：``<workspace>/.atlas/hooks.json``

工作区里的同名事件会**追加**在全局之后。格式（两种都接受）：

.. code-block:: json

    {
      "PreToolUse": [
        {"matcher": "shell|fs", "command": "python3 .atlas/hooks/guard.py",
         "timeout": 10}
      ],
      "PostToolUse": [{"command": "..."}],
      "UserPromptSubmit": [{"command": "..."}],
      "Stop": [{"command": "..."}]
    }

也接受 Claude Code 的外层 ``{"hooks": {"PreToolUse": [...]}}``。``matcher`` 是
对工具正式名（canonical，如 ``shell`` / ``read_file`` / ``write_file``）做正则
搜索；省略或 ``*`` 表示全部工具。

钩子通过 **stdin** 收一行 JSON（Claude Code 同款字段）：

``hook_event_name`` / ``session_id`` / ``run_id`` / ``cwd`` / ``workspace_id`` /
``tool_name`` / ``tool_pack`` / ``tool_input`` / ``tool_response`` / ``tool_ok`` / ``prompt``

``tool_name`` 是具体工具名（``read_file`` / ``write_file`` / ``shell``），``tool_pack``
是它所属的包（``fs`` / ``dev`` …，独立工具为空）。``matcher`` 对两者都做正则搜索。

stdout 可以是纯文本，也可以是 JSON：

- ``{"decision":"deny","reason":"..."}``（PreToolUse 拒绝）
- ``{"decision":"block","reason":"..."}``（PostToolUse 反馈给模型）
- ``{"hookSpecificOutput":{"permissionDecision":"deny|ask|allow",
  "permissionDecisionReason":"...","additionalContext":"..."}}``
- ``{"updatedInput": {...}}``（替换工具参数）
- ``{"additionalContext":"..."}``（UserPromptSubmit / PostToolUse 注入上下文）

退出码非 0 只记日志，不会阻断执行；超时默认 10 秒（可配 1~120）。
钩子是本地用户自己的脚本，与 shell 工具同信任级；但它不替代权限审批。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.shared.project_paths import get_project_root, project_data

logger = logging.getLogger(__name__)

EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
DEFAULT_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 120.0
MAX_OUTPUT_CHARS = 20_000
MAX_HOOKS_PER_EVENT = 20

_CACHE: Dict[str, Any] = {"at": 0.0, "workspace": None, "hooks": []}
_CACHE_TTL_S = 2.0


# ── 配置加载 ──────────────────────────────────────────────────────────────

def global_hooks_path() -> Path:
    return project_data("agent_zero", "hooks.json")


def workspace_hooks_path(workspace_id: Optional[str]) -> Optional[Path]:
    """工作区钩子文件；纯聊天工作区（没有目录）返回 None。"""
    if not workspace_id:
        return None
    try:
        from services.agent_zero.services import workspace_service

        root = workspace_service.get_workspace_root("task", workspace_id)
    except Exception:
        return None
    return root / ".atlas" / "hooks.json"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        logger.warning("读取钩子配置失败 %s: %s", path, e)
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        logger.warning("钩子配置不是合法 JSON %s: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_event_entries(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            item = {"command": item}
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or item.get("cmd") or "").strip()
        if not command:
            continue
        if item.get("enabled") is False:
            continue
        try:
            timeout = float(item.get("timeout") or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        timeout = min(MAX_TIMEOUT_S, max(1.0, timeout))
        out.append({
            "event": "",                       # 填在 _load_hooks
            "matcher": str(item.get("matcher") or item.get("tools") or "*").strip(),
            "command": command,
            "timeout": timeout,
            "source": str(item.get("source") or ""),
        })
    return out


def _normalize_file(path: Path, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    body = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
    hooks: List[Dict[str, Any]] = []
    for event in EVENTS:
        entries = _normalize_event_entries(body.get(event))
        for entry in entries[:MAX_HOOKS_PER_EVENT]:
            entry["event"] = event
            entry["source"] = entry.get("source") or str(path)
            hooks.append(entry)
    return hooks


def _load_hooks(workspace_id: Optional[str]) -> List[Dict[str, Any]]:
    """全局 + 工作区钩子（2 秒缓存；改文件后很快生效）。"""
    now = time.monotonic()
    if (now - float(_CACHE.get("at") or 0) < _CACHE_TTL_S
            and _CACHE.get("workspace") == (workspace_id or "")):
        return list(_CACHE.get("hooks") or [])

    hooks: List[Dict[str, Any]] = []
    hooks.extend(_normalize_file(global_hooks_path(), _read_json(global_hooks_path())))
    ws_path = workspace_hooks_path(workspace_id)
    if ws_path is not None:
        hooks.extend(_normalize_file(ws_path, _read_json(ws_path)))
    _CACHE.update(at=now, workspace=workspace_id or "", hooks=hooks)
    return list(hooks)


def invalidate() -> None:
    """配置变更后立即失效（写接口调用；也可等 2 秒自动失效）。"""
    _CACHE.update(at=0.0, workspace=None, hooks=[])


def has_hooks(workspace_id: Optional[str]) -> bool:
    return bool(_load_hooks(workspace_id))


def summarize(workspace_id: Optional[str]) -> Dict[str, Any]:
    """给设置页 / 诊断用的配置摘要（不执行任何命令）。"""
    hooks = _load_hooks(workspace_id)
    ws_path = workspace_hooks_path(workspace_id)
    return {
        "global_path": str(global_hooks_path()),
        "workspace_path": str(ws_path) if ws_path else "",
        "hooks": [
            {k: h.get(k) for k in ("event", "matcher", "command", "timeout", "source")}
            for h in hooks
        ],
    }


def _write_workspace_config(workspace_id: Optional[str], data: Dict[str, Any]) -> Path:
    path = workspace_hooks_path(workspace_id)
    if path is None:
        raise ValueError("纯聊天工作区没有目录，无法保存钩子；请先添加一个本地工作区")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    invalidate()
    return path


def add_workspace_hook(workspace_id: Optional[str], event: str,
                       command: str, matcher: str = "*",
                       timeout: float = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    """追加一条工作区钩子（设置页用）。"""
    event = str(event or "").strip()
    if event not in EVENTS:
        raise ValueError(f"未知事件: {event}（可选 {', '.join(EVENTS)}）")
    command = str(command or "").strip()
    if not command:
        raise ValueError("命令不能为空")
    try:
        timeout = min(MAX_TIMEOUT_S, max(1.0, float(timeout or DEFAULT_TIMEOUT_S)))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    path = workspace_hooks_path(workspace_id)
    if path is None:
        raise ValueError("纯聊天工作区没有目录，无法保存钩子；请先添加一个本地工作区")
    data = _read_json(path)
    body = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
    entries = body.get(event)
    if not isinstance(entries, list):
        entries = []
    entries.append({"matcher": str(matcher or "*").strip() or "*",
                    "command": command, "timeout": timeout})
    body[event] = entries
    if body is not data:
        data["hooks"] = body
    else:
        data = body
    _write_workspace_config(workspace_id, data)
    return {"ok": True, "path": str(path), **summarize(workspace_id)}


def remove_workspace_hook(workspace_id: Optional[str], event: str,
                          index: int) -> Dict[str, Any]:
    """按事件内的序号删除一条工作区钩子（0 起，只动工作区文件）。"""
    path = workspace_hooks_path(workspace_id)
    if path is None:
        raise ValueError("纯聊天工作区没有目录")
    data = _read_json(path)
    body = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
    entries = body.get(event)
    if not isinstance(entries, list) or not (0 <= int(index) < len(entries)):
        raise ValueError("钩子不存在")
    entries.pop(int(index))
    body[event] = entries
    if body is not data:
        data["hooks"] = body
    else:
        data = body
    _write_workspace_config(workspace_id, data)
    return {"ok": True, "path": str(path), **summarize(workspace_id)}


def ensure_template(workspace_id: Optional[str]) -> Dict[str, Any]:
    """工作区还没有 hooks.json 时创建一个空模板。"""
    path = workspace_hooks_path(workspace_id)
    if path is None:
        raise ValueError("纯聊天工作区没有目录，无法创建钩子文件")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({event: [] for event in EVENTS},
                                   ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    invalidate()
    return {"ok": True, "path": str(path), **summarize(workspace_id)}


# ── 执行 ──────────────────────────────────────────────────────────────────

def _cwd_for(workspace_id: Optional[str]) -> Path:
    try:
        from services.agent_zero.services import workspace_service

        # 与 shell 一致的执行根目录（纯聊天工作区回退到托管目录）
        return workspace_service.exec_root(workspace_id or "")
    except Exception:
        return get_project_root()


def _match(matcher: str, tool_name: str) -> bool:
    matcher = (matcher or "*").strip()
    if not matcher or matcher == "*":
        return True
    try:
        return re.search(matcher, tool_name or "") is not None
    except re.error:
        return matcher == (tool_name or "")


def _clean_env(extra: Dict[str, str]) -> Dict[str, str]:
    drop = ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "PYTHONPATH")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def _shell_argv(cmd: str) -> List[str]:
    if os.name == "nt":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            return [ps, "-NoProfile", "-NonInteractive", "-Command", cmd]
        return [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c", cmd]
    return [shutil.which("bash") or "/bin/bash", "-c", cmd]


async def _kill_tree(proc) -> None:
    try:
        if os.name != "nt" and hasattr(os, "killpg"):
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _parse_output(stdout: str, event: str) -> Dict[str, Any]:
    """把钩子 stdout 归一为 {decision, reason, context, updated_input, text}。"""
    text = (stdout or "").strip()
    out: Dict[str, Any] = {"decision": "", "reason": "", "context": "",
                           "updated_input": None, "text": text[:MAX_OUTPUT_CHARS]}
    if not text:
        return out
    parsed: Any = None
    if text[0] in "{[":
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
    if not isinstance(parsed, dict):
        # 纯文本：UserPromptSubmit 视为注入上下文（Claude Code 同款）
        if event == "UserPromptSubmit":
            out["context"] = text[:MAX_OUTPUT_CHARS]
        return out

    decision = str(parsed.get("decision") or "").strip().lower()
    reason = str(parsed.get("reason") or "").strip()
    specific = parsed.get("hookSpecificOutput")
    if isinstance(specific, dict):
        decision = decision or str(specific.get("permissionDecision") or "").strip().lower()
        reason = reason or str(
            specific.get("permissionDecisionReason") or "").strip()
        ctx = specific.get("additionalContext") or specific.get("additional_context")
        if ctx:
            out["context"] = str(ctx)[:MAX_OUTPUT_CHARS]
        if isinstance(specific.get("updatedInput"), dict):
            out["updated_input"] = specific["updatedInput"]
    ctx = parsed.get("additionalContext") or parsed.get("additional_context")
    if ctx:
        out["context"] = str(ctx)[:MAX_OUTPUT_CHARS]
    if isinstance(parsed.get("updatedInput"), dict):
        out["updated_input"] = parsed["updatedInput"]
    if parsed.get("continue") is False and not decision:
        decision = "block"
    out["decision"] = decision
    out["reason"] = reason[:4000]
    return out


async def _run_one(hook: Dict[str, Any], *, workspace_id: Optional[str],
                   run_id: str, session_id: str,
                   payload: Dict[str, Any]) -> Dict[str, Any]:
    event = str(hook.get("event") or "")
    cwd = _cwd_for(workspace_id)
    body = dict(payload)
    body.update({
        "hook_event_name": event,
        "session_id": session_id,
        "run_id": run_id,
        "cwd": str(cwd),
        "workspace_id": workspace_id or "",
    })
    env = _clean_env({
        "ATLAS_HOOK_EVENT": event,
        "ATLAS_HOOK_TOOL": str(body.get("tool_name") or ""),
        "ATLAS_WORKSPACE": workspace_id or "",
        "ATLAS_RUN_ID": run_id,
        "ATLAS_SESSION_ID": session_id,
    })
    started = time.monotonic()
    result: Dict[str, Any] = {
        "event": event,
        "command": hook.get("command"),
        "matcher": hook.get("matcher"),
        "ok": False,
        "exit_code": None,
        "output": "",
        "stderr": "",
        "decision": "",
        "reason": "",
        "context": "",
        "updated_input": None,
        "duration_ms": 0,
        "timed_out": False,
    }
    payload_bytes = (json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8")
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *_shell_argv(str(hook.get("command") or "")),
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=payload_bytes),
                timeout=float(hook.get("timeout") or DEFAULT_TIMEOUT_S))
        except asyncio.TimeoutError:
            result["timed_out"] = True
            await _kill_tree(proc)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            except Exception:
                stdout, stderr = b"", b""
        out_text = (stdout or b"").decode("utf-8", "replace")
        err_text = (stderr or b"").decode("utf-8", "replace")
        result["exit_code"] = proc.returncode
        result["ok"] = proc.returncode == 0 and not result["timed_out"]
        result["output"] = out_text[:MAX_OUTPUT_CHARS]
        result["stderr"] = err_text[-4000:]
        parsed = _parse_output(out_text, event)
        result.update({k: parsed[k] for k in
                       ("decision", "reason", "context", "updated_input")})
        if result["timed_out"]:
            logger.warning("钩子超时（%ss）: %s", hook.get("timeout"), hook.get("command"))
        elif proc.returncode != 0:
            logger.warning("钩子退出码 %s: %s — %s", proc.returncode,
                           hook.get("command"), err_text.strip()[:500])
    except Exception as e:
        result["stderr"] = f"{type(e).__name__}: {e}"
        logger.warning("钩子执行失败: %s — %s", hook.get("command"), e)
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    return result


async def run_event(event: str, *, workspace_id: Optional[str],
                    run_id: str = "", session_id: str = "",
                    tool_name: str = "", tool_pack: str = "",
                    payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """执行某个事件下匹配的全部钩子（顺序执行，便于决定可复现）。"""
    hooks = [h for h in _load_hooks(workspace_id) if h.get("event") == event]
    if event in ("PreToolUse", "PostToolUse") and tool_name:
        hooks = [h for h in hooks
                 if _match(str(h.get("matcher") or "*"), tool_name)
                 or (tool_pack and _match(str(h.get("matcher") or "*"), tool_pack))]
    if not hooks:
        return []
    base = dict(payload or {})
    if tool_pack:
        base.setdefault("tool_pack", tool_pack)
    results: List[Dict[str, Any]] = []
    for hook in hooks:
        results.append(await _run_one(
            hook, workspace_id=workspace_id, run_id=run_id,
            session_id=session_id or run_id, payload=base))
    return results


def _aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    decision = "allow"
    reasons: List[str] = []
    context: List[str] = []
    updated: Optional[Dict[str, Any]] = None
    for r in results:
        d = str(r.get("decision") or "").lower()
        if d in ("deny", "block"):
            decision = "deny"
            if r.get("reason"):
                reasons.append(str(r["reason"]))
        elif d == "ask" and decision != "deny":
            decision = "ask"
            if r.get("reason"):
                reasons.append(str(r["reason"]))
        if r.get("context"):
            context.append(str(r["context"]))
        if isinstance(r.get("updated_input"), dict):
            updated = r["updated_input"]
    return {"decision": decision, "reason": "\n".join(reasons),
            "context": "\n\n".join(context), "updated_input": updated}


async def user_prompt_submit(workspace_id: Optional[str], prompt: str,
                             *, run_id: str = "", session_id: str = "") -> Dict[str, Any]:
    """用户消息提交前：可注入上下文，也可直接阻断本轮。"""
    results = await run_event(
        "UserPromptSubmit", workspace_id=workspace_id, run_id=run_id,
        session_id=session_id, payload={"prompt": prompt})
    agg = _aggregate(results)
    return {**agg, "blocked": agg["decision"] == "deny", "results": results}


async def pre_tool_use(workspace_id: Optional[str], tool_name: str,
                       tool_input: Dict[str, Any], *, run_id: str = "",
                       session_id: str = "", tool_pack: str = "") -> Dict[str, Any]:
    results = await run_event(
        "PreToolUse", workspace_id=workspace_id, run_id=run_id,
        session_id=session_id, tool_name=tool_name, tool_pack=tool_pack,
        payload={"tool_name": tool_name, "tool_input": tool_input})
    agg = _aggregate(results)
    return {"decision": agg["decision"], "reason": agg["reason"],
            "context": agg["context"],
            "tool_input": agg["updated_input"] if agg["updated_input"] is not None
            else tool_input,
            "results": results}


async def post_tool_use(workspace_id: Optional[str], tool_name: str,
                        tool_input: Dict[str, Any], tool_response: str,
                        ok: bool, *, run_id: str = "",
                        session_id: str = "", tool_pack: str = "") -> Dict[str, Any]:
    results = await run_event(
        "PostToolUse", workspace_id=workspace_id, run_id=run_id,
        session_id=session_id, tool_name=tool_name, tool_pack=tool_pack,
        payload={"tool_name": tool_name, "tool_input": tool_input,
                 "tool_response": (tool_response or "")[:MAX_OUTPUT_CHARS],
                 "tool_ok": bool(ok)})
    agg = _aggregate(results)
    return {"decision": agg["decision"], "reason": agg["reason"],
            "context": agg["context"], "results": results}


async def stop(workspace_id: Optional[str], status: str, *, run_id: str = "",
               session_id: str = "", last_message: str = "") -> List[Dict[str, Any]]:
    """run 结束时触发（结果只记录，不改变 run 状态）。"""
    return await run_event(
        "Stop", workspace_id=workspace_id, run_id=run_id,
        session_id=session_id,
        payload={"status": status, "last_message": (last_message or "")[:4000]})

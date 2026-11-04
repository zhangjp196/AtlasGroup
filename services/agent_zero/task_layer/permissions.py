"""危险操作的权限档位（**会话级**：单次询问 / 自动判断 / 会话允许 / 完全允许）。

档位作用于**会话本身**，实时保存在 `conversations.permission_mode`：
- 每条对话有自己的档位，切换即落库；
- 新建对话默认继承上一条对话的档位（按最近更新）；也可在「工单助手 → 授权管理」
  显式设置全局默认档位，显式值优先；
- 审批时点「始终允许」= 把当前会话的档位升为 always；
- 「本次对话允许」的按工具授权（permission_grants）仍按对话作用域保存。

判定分两层，互相配合：
1. **规则打分**（这里）：工具本身的危险等级 + 参数里的高危特征（rm、sudo、
   网络、覆盖写入…）。规则决定「要不要问」。
2. **模型自述**：危险操作前模型在正文里写的那句话（`note`）随审批事件推给
   界面，用户据此判断——规则负责拦，模型负责解释。

例外：**强制人工确认**操作（如 Agent Team 创建）不受档位与会话授权影响，
每次都必须手动批准。

四个档位（会话级）：
- auto     自动判断：低/中危自动放行，高危才询问
- once     每次询问（默认）：每一次危险调用都要确认（批准只生效这一次）
- session  会话允许：同一对话内该工具第一次询问，批准后后续自动放行
- always   完全允许：不问
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores import settings_store, zero_store
from services.agent_zero.stores.zero_store import _q

logger = logging.getLogger(__name__)

MODES = ("auto", "once", "session", "always")
MODE_LABELS = {
    "auto": "Auto",
    "once": "Ask",
    "session": "Session",
    "always": "Allow",
}

DEFAULT_MODE = "once"
APPROVAL_TIMEOUT_S = 120.0

# shell / python 里的高危特征 → 说明（命中即升为高危）
SHELL_PATTERNS: List[Tuple[str, str]] = [
    (r"\brm\s+(-[a-z]+\s+)*-?[rf]", "递归/强制删除文件"),
    (r"\brm\b", "删除文件"),
    (r"\bsudo\b|\bdoas\b", "提权执行"),
    (r"\bkill\b|\bpkill\b", "终止进程"),
    (r"\bmv\b|\bcp\s+.*\.\.", "移动 / 覆盖文件"),
    (r"\bchmod\b|\bchown\b", "修改权限"),
    (r"\bgit\s+(push|reset|rebase)\b", "改写/推送仓库"),
    (r"\b(npm|pnpm|yarn|pip3?|brew)\s+install\b", "安装依赖（执行第三方代码）"),
    (r"\b(curl|wget)\b", "网络访问"),
    (r"\bdd\b|\btruncate\b|\b>\s", "覆盖 / 截断文件内容"),
    (r"\b(reboot|shutdown|mkfs)\b", "系统级操作"),
    # Windows / PowerShell
    (r"\b(remove-item|rm-item|del|erase|rd|rmdir)\b", "删除文件 / 目录（Windows）"),
    (r"\b(stop-process|kill|taskkill)\b", "终止进程（Windows）"),
    (r"\b(invoke-webrequest|invoke-restmethod|iwr|irm|curl|wget)\b", "网络访问（Windows）"),
    (r"\b(set-executionpolicy|start-process|reg\s+add|sc\s+delete)\b", "系统级操作（Windows）"),
    (r"\b(format-volume|diskpart|bcdedit)\b", "磁盘 / 启动配置（Windows）"),
]

PYTHON_PATTERNS: List[Tuple[str, str]] = [
    (r"\bos\.remove|\bos\.unlink|shutil\.rmtree", "删除文件 / 目录"),
    (r"open\([^)]*['\"](?=[wax])", "写入文件"),
    (r"\bsubprocess\b|\bos\.system|\bos\.popen", "执行外部命令"),
    (r"\brequests\b|\burllib\b|\bsocket\b|\bhttpx\b", "网络访问"),
    (r"\bshutil\.move|\bshutil\.copy", "移动 / 覆盖文件"),
    (r"\bkill\b|\bos\.kill", "终止进程"),
]
# 命令里「在跑 python」的迹象：独立 python 工具已下线，Python 代码现在都从 shell 走
# （`python3 -c '...'` / heredoc / `python x.py`）。只有命中它才对 shell 的 cmd 套用
# PYTHON_PATTERNS，避免把 `grep subprocess *.py` 这类普通命令误判成高危
# （`*.py` 里的 py 不是解释器，所以不单独匹配裸 py）。
PYTHON_CMD_RE = re.compile(r"\bpython[0-9.]*\b|<<\s*['\"]?PY")

MCP_HIGH_HINTS = ("exec", "run", "shell", "write", "delete", "remove", "install",
                  "upload", "publish", "send", "create")
MCP_LOW_HINTS = ("read", "list", "get", "search", "fetch", "describe", "ping")


def get_conversation_mode(conv_id: str) -> str:
    """该会话当前生效的权限档位（实时保存在 conversations 表）。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT permission_mode FROM {_q('conversations')} WHERE id = :c"),
            {"c": conv_id},
        ).first()
    mode = str((row[0] if row else "") or "")
    return mode if mode in MODES else last_mode()


def last_mode() -> str:
    """全局默认档位（新会话继承）。

    会话档位切换时会同步写 ``permissions.default``，所以默认值通常等于上一条
    会话的档位；在「工单助手 → 授权管理」里显式改默认值时，以显式值为准。
    """
    cfg = settings_store.get("permissions", {}) or {}
    mode = str(cfg.get("default") or "")
    if mode in MODES:
        return mode
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT permission_mode FROM {_q('conversations')} "
                 f"ORDER BY updated_at DESC LIMIT 1"),
        ).first()
    mode = str((row[0] if row else "") or "")
    return mode if mode in MODES else DEFAULT_MODE


def set_conversation_mode(conv_id: str, mode: str) -> str:
    """实时保存会话的权限档位；同时同步默认档位，下一条新会话延续它。"""
    mode = str(mode or "").strip()
    if mode not in MODES:
        raise ValueError(f"非法权限档位: {mode}")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversations')} SET permission_mode = :m, "
                 f"updated_at = datetime('now','localtime') WHERE id = :c"),
            {"m": mode, "c": conv_id},
        )
        db.commit()
    cfg = settings_store.get("permissions", {}) or {}
    settings_store.put("permissions", {
        "default": mode,
        "approval_timeout_s": float(cfg.get("approval_timeout_s") or APPROVAL_TIMEOUT_S),
    })
    zero_store.append_audit("local", "permission_mode_set",
                            args={"conversation_id": conv_id, "mode": mode})
    return mode


def get_policy() -> Dict[str, Any]:
    cfg = settings_store.get("permissions", {}) or {}
    out = {
        "default": last_mode(),
        "approval_timeout_s": float(cfg.get("approval_timeout_s") or APPROVAL_TIMEOUT_S),
        "modes": list(MODES),
        "labels": MODE_LABELS,
    }
    return out


def set_policy(patch: Dict[str, Any]) -> Dict[str, Any]:
    """保存全局默认档位 / 审批超时（会话档位切换时会自动同步默认）。"""
    cfg = settings_store.get("permissions", {}) or {}
    current = {
        "default": cfg.get("default") or DEFAULT_MODE,
        "approval_timeout_s": float(cfg.get("approval_timeout_s") or APPROVAL_TIMEOUT_S),
    }
    if "default" in patch:
        mode = str(patch.get("default") or "").strip()
        if mode:
            if mode not in MODES:
                raise ValueError(f"非法权限档位: {mode}")
            current["default"] = mode
    if "approval_timeout_s" in patch:
        current["approval_timeout_s"] = max(5.0, min(float(patch["approval_timeout_s"] or 0), 600.0))
    settings_store.put("permissions", current)
    return get_policy()


def base_risk(name: str) -> str:
    from services.agent_zero.tools import registry as tool_reg

    canon = tool_reg.canonical(name)
    tdef = tool_reg.get_tool(canon)
    if tdef is not None:
        return tdef.risk or "low"
    if str(name or "").startswith("mcp__"):
        lowered = str(name).lower()
        if any(h in lowered for h in MCP_HIGH_HINTS):
            return "high"
        if any(h in lowered for h in MCP_LOW_HINTS):
            return "low"
        return "medium"
    return "low"


def classify(name: str, args: Dict[str, Any]) -> Tuple[str, List[str]]:
    """规则打分：返回 (风险等级, 命中的理由列表)。"""
    canon = _canon(name)
    level = base_risk(name)
    reasons: List[str] = []
    if canon == "shell":
        cmd = str((args or {}).get("cmd") or "")
        for pattern, label in SHELL_PATTERNS:
            if re.search(pattern, cmd):
                reasons.append(label)
                level = "high"
        # 独立 python 工具已下线，Python 代码现在从 shell 走（python3 -c / heredoc /
        # python x.py）。以前那些特征只在 `python` 工具的 code 参数上检查，工具一撤，
        # `python3 -c "import shutil; shutil.rmtree(...)"` 就只剩「不匹配 SHELL_PATTERNS」
        # 的低危——等于把一条删库路径的风险标注悄悄关掉了。
        if PYTHON_CMD_RE.search(cmd):
            for pattern, label in PYTHON_PATTERNS:
                if re.search(pattern, cmd):
                    reasons.append(f"python：{label}")
                    level = "high"
    elif canon == "workspace" and str((args or {}).get("op") or "") == "write":
        reasons.append("写入工作区文件")
    elif canon in ("write_file",) and str((args or {}).get("path") or ""):
        reasons.append("写入工作区文件")
        if _is_hook_config_path(args):
            level = "high"
            reasons.append("写入钩子配置（后续工具调用会执行其中的命令）")
    elif canon == "edit_file":
        reasons.append("修改工作区文件")
        if _is_hook_config_path(args):
            level = "high"
            reasons.append("修改钩子配置（后续工具调用会执行其中的命令）")
    elif canon == "web":
        reasons.append("访问外部网络")
    elif canon == "memory_write":
        reasons.append("写入本地记忆")
    elif canon == "vcs":
        op = str((args or {}).get("op") or "")
        if op in ("commit", "switch"):
            level = "high"
            reasons.append("版本控制写操作（提交 / 切换分支）")
        elif op in ("stage", "unstage", "discard"):
            level = "medium"
            reasons.append("版本控制暂存 / 丢弃改动")
    elif canon == "team_manage" and str((args or {}).get("action") or "").strip().lower() == "create":
        level = "high"
        reasons.append("创建 Agent 团队")
    return level, reasons


def _canon(name: str) -> str:
    from services.agent_zero.tools import registry as tool_reg

    return tool_reg.canonical(name)


def _grant_key(tool: str) -> str:
    """MCP 授权按服务粒度记（mcp__<server>），其它工具按工具名。"""
    if str(tool).startswith("mcp__"):
        return f"mcp__{str(tool).split('__', 2)[1]}"
    return tool


def _has_session_grant(conv_id: str, tool: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT 1 FROM {_q('permission_grants')} "
                 f"WHERE conversation_id = :c AND (tool = :t1 OR tool = :t2) LIMIT 1"),
            {"c": conv_id, "t1": tool, "t2": _grant_key(tool)},
        ).first()
    return row is not None


# 无论档位（询问/自动判断/完全允许）都必须人工确认的操作：
# Agent Team 的创建——团队由模型动态组建，组建行为本身必须用户批准。
FORCED_CONFIRM_ACTIONS: Dict[str, set] = {"team_manage": {"create"}}

_HOOK_PATH_RE = re.compile(r"(^|[/\\])\.atlas[/\\]hooks\.json$", re.IGNORECASE)
_HOOK_SHELL_WRITE_RE = re.compile(
    r"(\.atlas[/\\]hooks\.json).*(>|>>|tee\b|cp\b|mv\b|install\b)"
    r"|(>|>>|tee\b|cp\b|mv\b|install\b).*(\.atlas[/\\]hooks\.json)",
    re.IGNORECASE | re.DOTALL,
)


def _is_hook_config_path(args: Dict[str, Any]) -> bool:
    rel = str((args or {}).get("path") or "").strip().replace("\\", "/").lstrip("/")
    return bool(_HOOK_PATH_RE.search(rel))


def _forced_confirmation(canon: str, args: Dict[str, Any]) -> bool:
    actions = FORCED_CONFIRM_ACTIONS.get(canon)
    if actions and str((args or {}).get("action") or "").strip().lower() in actions:
        return True
    # 钩子配置会执行本地命令：模型写它不能被任何档位静默放行
    if canon in ("write_file", "edit_file") and _is_hook_config_path(args):
        return True
    if canon == "shell":
        cmd = str((args or {}).get("cmd") or "")
        if _HOOK_SHELL_WRITE_RE.search(cmd):
            return True
    return False


def decide(conv_id: str, name: str, args: Dict[str, Any],
           auto_execute: bool = False) -> Dict[str, Any]:
    """这一工具调用要不要询问用户？档位是**会话级**的，每次调用实时读取，
    所以运行中途切换档位，后续的工具调用立即按新档位执行。

    返回 {"ask": bool, "risk": str, "reasons": [...], "mode": str}
    """
    # auto_execute 只是 run 创建时的快照，不再短路：以会话当前档位为准
    canon = _canon(name)
    mode = get_conversation_mode(conv_id)
    # ask_user 本身就是「让用户决定」，不能再套一层审批。
    if canon == "ask_user":
        return {"ask": False, "risk": "low", "reasons": [], "mode": mode}
    risk, reasons = classify(name, args)
    # 强制人工确认：不受权限档位、会话授权影响——每次都必须手动批准
    if _forced_confirmation(canon, args):
        return {"ask": True, "risk": "high",
                "reasons": reasons or ["创建 Agent 团队"], "mode": mode}
    if risk == "low":
        return {"ask": False, "risk": risk, "reasons": reasons, "mode": mode}
    # 用户在这条对话里点过「本次对话允许」→ 同对话内不再问（换对话重新问）
    if _has_session_grant(conv_id, canon) or _has_session_grant(conv_id, _grant_key(canon)):
        return {"ask": False, "risk": risk, "reasons": reasons, "mode": "session"}
    if mode == "always":
        return {"ask": False, "risk": risk, "reasons": reasons, "mode": mode}
    if mode == "auto" and risk != "high":
        return {"ask": False, "risk": risk, "reasons": reasons, "mode": mode}
    return {"ask": True, "risk": risk, "reasons": reasons, "mode": mode}


# ── 审批记录与进程内等待 ───────────────────────────────────────────────

_pending: Dict[str, Dict[str, Any]] = {}


def approval_preview(ws_id: str, name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """opencode 式审批预览：写/改文件类工具在批准前算出将要发生的 unified diff。

    覆盖内部文件工具（write_file / edit_file）与 MCP filesystem 类工具
   （mcp__<server>__write_file 等，传绝对路径）。其它工具返回 None，
    前端退回参数 JSON 展示。任何异常都静默 None——预览是锦上添花。
    """
    canon = _canon(name)
    rel = str((args or {}).get("path") or "").strip()
    if not rel or not (canon.endswith("write_file") or canon.endswith("edit_file")):
        return None
    try:
        from pathlib import Path

        from services.agent_zero.services import workspace_service as wsvc
        from services.agent_zero.runtime.zero import file_ops

        if canon in ("write_file", "edit_file"):
            target = wsvc.resolve_workspace_path(ws_id, rel)
            display_path = rel
        elif rel.startswith("/"):
            # MCP 工具传绝对路径：仅当它落在执行根目录内才做预览
            ws_root = wsvc.exec_root(ws_id)
            target = Path(rel).resolve()
            target.relative_to(ws_root.resolve())
            display_path = wsvc.safe_relpath(ws_id, target)
        else:
            return None
        if target.is_dir():
            return None
        existed = target.exists() and target.is_file()
        old_text = ""
        if existed:
            try:
                old_text = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                old_text = ""
        old_s = (args or {}).get("old_string") or (args or {}).get("oldString")
        new_s = (args or {}).get("new_string") or (args or {}).get("newString")
        if canon.endswith("write_file"):
            new_text = str((args or {}).get("content") or "")
        else:
            if old_s is None or new_s is None:
                return None
            try:
                new_text, _ = file_ops.apply_edit(
                    old_text, str(old_s), str(new_s),
                    replace_all=bool((args or {}).get("replace_all")))
            except Exception:
                return None  # 参数不合法（如 old_string 不唯一），工具本身会报错
        lines = file_ops.unified_diff(display_path, old_text, new_text).splitlines()
        shown = lines[:140]
        added = sum(1 for l in shown if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in shown if l.startswith("-") and not l.startswith("---"))
        return {"path": rel, "existed": existed, "added": added, "removed": removed,
                "truncated": len(lines) > len(shown), "diff": "\n".join(shown)}
    except Exception:
        return None


def create_approval(conv_id: str, run_id: Optional[str], name: str,
                    args: Dict[str, Any], risk: str,
                    reasons: List[str], note: str = "",
                    preview: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    approval_id = zero_store.new_id("appr")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"INSERT INTO {_q('tool_approvals')} "
                 f"(id, conversation_id, run_id, tool, args, risk, reasons, note, status) "
                 f"VALUES (:id, :c, :r, :t, :a, :k, :rs, :n, 'pending')"),
            {"id": approval_id, "c": conv_id, "r": run_id, "t": _canon(name),
             "a": json_dump(args), "k": risk, "rs": json_dump(reasons),
             "n": (note or "")[:1200]},
        )
        db.commit()
    _pending[approval_id] = {"event": asyncio.Event(), "decision": None}
    zero_store.append_audit("local", "permission_ask",
                            args={"approval_id": approval_id, "tool": _canon(name),
                                  "risk": risk, "reasons": reasons})
    return {"id": approval_id, "tool": _canon(name), "args": args,
            "risk": risk, "reasons": reasons, "note": note, "status": "pending",
            "preview": preview}


def json_dump(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT * FROM {_q('tool_approvals')} WHERE id = :id"),
            {"id": approval_id},
        ).mappings().first()
    return dict(row) if row else None


def list_pending(conv_id: str) -> List[Dict[str, Any]]:
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT * FROM {_q('tool_approvals')} "
                 f"WHERE conversation_id = :c AND status = 'pending' "
                 f"ORDER BY created_at DESC LIMIT 20"),
            {"c": conv_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def expire_orphan_approvals() -> int:
    """进程重启后，库里残留的 pending 审批没有等待方，统一标记为 expired。"""
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        res = db.execute(
            text(f"UPDATE {_q('tool_approvals')} SET status = 'expired', "
                 f"decided_at = datetime('now','localtime') "
                 f"WHERE status = 'pending'"),
        )
        db.commit()
    return res.rowcount


def decide_approval(approval_id: str, action: str) -> Dict[str, Any]:
    """用户点击按钮：approve_once / approve_session / approve_always / deny。"""
    approval = get_approval(approval_id)
    if not approval:
        raise ValueError(f"审批不存在: {approval_id}")
    if approval["status"] != "pending":
        return {"id": approval_id, "status": approval["status"]}

    tool = approval["tool"]
    status = "approved" if action.startswith("approve") else "denied"
    scope = {"approve_once": "once", "approve_session": "session",
             "approve_always": "always", "deny": "none"}.get(action, "once")

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('tool_approvals')} SET status = :s, scope = :sc, "
                 f"decided_at = datetime('now','localtime') WHERE id = :id"),
            {"s": status, "sc": scope, "id": approval_id},
        )
        if scope == "session":
            for key in {tool, _grant_key(tool)}:
                db.execute(
                    text(f"INSERT OR IGNORE INTO {_q('permission_grants')} "
                         f"(id, conversation_id, tool) VALUES (:id, :c, :t)"),
                    {"id": zero_store.new_id("grant"), "c": approval["conversation_id"],
                     "t": key},
                )
        db.commit()

    if scope == "always":
        # 「始终允许」= 把当前**会话**的档位升为 always（实时落库，新会话延续它）。
        # 必须在上面的事务提交之后写：嵌套写会让 sqlite 自己锁自己（database is locked）。
        set_conversation_mode(approval["conversation_id"], "always")

    slot = _pending.get(approval_id)
    if slot is not None:
        slot["decision"] = {"status": status, "scope": scope}
        slot["event"].set()

    zero_store.append_audit("local", "permission_decide",
                            args={"approval_id": approval_id, "tool": tool,
                                  "action": action},
                            result={"status": status, "scope": scope})
    return {"id": approval_id, "status": status, "scope": scope}


async def await_decision(approval_id: str, run_id: Optional[str] = None) -> Dict[str, Any]:
    """等待用户决策；超时 / 运行被停止 → 视为拒绝（安全默认）。"""
    slot = _pending.get(approval_id)
    if slot is None:
        # 进程重启后审批仍在库里：按库里的状态判断
        row = get_approval(approval_id)
        return {"status": (row or {}).get("status") or "expired", "scope": "none"}

    timeout = get_policy()["approval_timeout_s"]
    from services.agent_zero.task_layer import run_service

    waited = 0.0
    while not slot["event"].is_set():
        if run_id and run_service.is_stop_requested(run_id):
            return {"status": "denied", "scope": "none", "reason": "stopped"}
        if waited >= timeout:
            SessionLocal = get_session_local()
            with SessionLocal() as db:
                db.execute(
                    text(f"UPDATE {_q('tool_approvals')} SET status = 'expired' "
                         f"WHERE id = :id AND status = 'pending'"),
                    {"id": approval_id},
                )
                db.commit()
            return {"status": "expired", "scope": "none", "reason": "timeout"}
        try:
            await asyncio.wait_for(slot["event"].wait(), timeout=1.0)
        except asyncio.TimeoutError:
            waited += 1.0
    decision = slot.get("decision") or {"status": "denied", "scope": "none"}
    return decision


def forget(approval_id: str) -> None:
    _pending.pop(approval_id, None)


def cancel_pending_for_run(run_id: str) -> int:
    """运行被强制停止时，把它尚未决定的审批标记为 denied，避免留下僵尸审批。"""
    if not run_id:
        return 0
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id FROM {_q('tool_approvals')} "
                 f"WHERE run_id = :r AND status = 'pending'"),
            {"r": run_id},
        ).fetchall()
        if not rows:
            return 0
        db.execute(
            text(f"UPDATE {_q('tool_approvals')} SET status = 'denied', "
                 f"decided_at = datetime('now','localtime') "
                 f"WHERE run_id = :r AND status = 'pending'"),
            {"r": run_id},
        )
        db.commit()
    for (approval_id,) in rows:
        slot = _pending.pop(str(approval_id), None)
        if slot is not None:
            slot["decision"] = {"status": "denied", "scope": "none", "reason": "stopped"}
            slot["event"].set()
    return len(rows)

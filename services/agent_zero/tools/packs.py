"""工具包（tool packs）：把同类工具合并成少量「包 + op」入口。

**为什么**：工具数量会随功能增长线性膨胀，模型一次要看几十个 function，既占
提示词，又容易选错。这里把**同类**工具合并成 7 个包，每个包只暴露一个 function：
`op` 选动作，其余参数照原样透传。

**合并原则（2026-10 调整）**：只合并语义同族的动作，合并过度反而让模型选错。
- **命令与仓库走一个包**（`dev`）：`tool_job` / `vcs` /
  `codegraph`。vcs / codegraph 自带 `op` 参数，在包里统一暴露为 `action`
  （包的 `op` 仍负责选工具），执行前还原成它们真正读取的 `op`。
  `shell` 仍保持独立：它风险最高、审批预览要看到完整命令，不适合再套一层。
  以前把命令类塞进 `code` 包出过「整条命令写进 op」的事故，所以这里只合并
  真正同族的仓库类工具，`shell` 不进去。
- **同类工具先在工具层合并**（`registry.MODEL_HIDDEN` 保留旧入口做兼容）：
  `goal_report` 合入 `goal_manage`（action=report）；`call_subagent` / `call_team`
  合成 `delegate`（target 选子代理 / 团队成员）。设置页与模型工具清单只看到
  合并后的入口，历史消息里的旧名字仍能执行。
- **任务与协作包**（`plan`）保留 5 个动作：`todo` / `goal` / `delegate` /
  `team_admin` / `role_admin`。任务清单是唯一的进度载体（整份覆盖式 `todo_write`）；
  执行计划流程图已下线，不再有独立的 plan 工具。`ask_user` 是独立工具。
- 其余按用途保持 `fs` / `net` / `memory` / `skill` / `mcp`。

**只是一层协议适配**，不是新的执行层：
- 执行、权限、审批预览、审计、UI 标签仍然用原来的具体工具名；
- 旧的单工具名与别名继续可用（模型偶尔直接点名、历史消息里也可能就是旧名）；
- `propose_task` 由对话层直接处理，不打包，保持独立工具。

协议：
```json
{"name": "fs", "arguments": {"op": "read", "path": "README.md"}}
```
等价于 `read_file({"path": "README.md"})`。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 由对话层直接拦截、不经过分发器的工具：不打包（打了也没法用 op 表达）。
#
# 只列**当前真的存在**的内联工具。历史上这里还挂着 report_done / report_failed /
# escalate——那是任务节点模式下的终止动作，随任务模式一起下线了，registry 里早已
# 没有这三个 code。留着它们会让「覆盖面」测试的等式里多出三个永远不存在的项，
# 掩盖真正漏挂的新工具。
INLINE_TOOLS = frozenset({"propose_task"})

PACK_OP_FIELD = "op"


@dataclass(frozen=True)
class ToolPack:
    code: str
    title: str
    ops: Dict[str, str] = field(default_factory=dict)   # op -> 具体工具 code


PACKS: Tuple[ToolPack, ...] = (
    ToolPack("fs", "文件", {
        "read": "read_file",
        "office": "read_office",
        "write": "write_file",
        "edit": "edit_file",
        "list": "list_files",
    }),
    ToolPack("net", "联网与计算", {
        "fetch": "web",
        "time": "time",
        "calc": "calculate",
    }),
    ToolPack("memory", "记忆", {
        "read": "memory_read",
        "write": "memory_write",
        "forget": "memory_forget",
        "save": "save_analysis",
    }),
    # 任务 + 目标 + 协作：只保留「各自独立的一件事」，同类工具已经合并：
    # todo 是整份覆盖式的任务清单（todo_write）；goal 合并了 goal_report +
    # goal_manage；delegate 合并了子代理与团队成员。旧工具名仍可调用，
    # 但不再出现在模型工具清单里（见 registry.MODEL_HIDDEN）。
    ToolPack("plan", "任务与协作", {
        "todo": "todo_write",
        "goal": "goal_manage",
        "delegate": "delegate",
        "team_admin": "team_manage",
        "role_admin": "role_manage",
    }),
    # 开发与仓库：后台任务 / 版本控制 / 代码图谱。
    # vcs 与 codegraph 自带 `op` 参数，进包后在包里统一暴露成 `action`
    # （见 _pack_schema / _apply_subop），避免和包的 `op` 冲突。
    ToolPack("dev", "开发与仓库", {
        "job": "tool_job",
        "vcs": "vcs",
        "graph": "codegraph",
    }),
    ToolPack("skill", "技能", {
        "list": "list_skills",
        "use": "use_skill",
        "create": "create_skill",
        "install": "install_skill",
    }),
    ToolPack("mcp", "MCP", {
        "list": "list_mcp",
        "add": "add_mcp_server",
        "install": "install_mcp",
    }),
)

# 独立工具：**不进包**，直接以具体工具名下发给模型。
#
# shell / tool_job / vcs / codegraph 原来被塞进 `code` 包，问题有三：
# 1. 参数差异极大（cmd vs action/job_id vs op），包级 schema 只能取并集，
#    模型经常把整条命令写进 op（现场事故，见 pack_op_label 的注释）；
# 2. 风险等级不同（shell=high，codegraph=low），审批预览看不出到底要跑什么；
# 3. 它们本身已经是最小的语义单位，再套一层 op 没有信息增量。
# workspace 自带 op 参数，本来就不能进包（包的 op 会吃掉它）。
STANDALONE_TOOLS: Tuple[str, ...] = (
    "shell",
    "workspace",
    # ask_user 是通用的人机交互，不属于任何工具包。
    "ask_user",
    # 全局 Agent 专用：手动注入到 global-agent 会话，普通会话不下发。
    "app_manage",
)

# 只由对话层按会话类型手动注入的工具，不参与普通工具清单生成。
GLOBAL_ONLY_TOOLS: Tuple[str, ...] = ("app_manage",)

# 已经合并进新工具的旧入口（实现保留、可被历史消息调用，但不再下发给模型）。
# 与 registry.MODEL_HIDDEN 保持一致；这里用于设置页 / 目录的过滤。
MERGED_AWAY_TOOLS: Tuple[str, ...] = (
    "goal_report", "call_subagent", "call_team",
)

# 旧包名 → 新包名（历史消息 / 旧提示词里可能出现）。
PACK_ALIASES: Dict[str, str] = {"agent": "plan"}

# 已拆分包的旧 op → 具体工具（历史消息兼容：`code{op:shell}` 仍要能跑）。
LEGACY_PACK_OPS: Dict[str, Dict[str, str]] = {
    "code": {"shell": "shell", "job": "tool_job",
             "vcs": "vcs", "graph": "codegraph"},
}

# plan 包历史上暴露过这些 op；合并后（以及 ask_user 移出后）名字变了，
# 历史消息 / 旧提示词里的旧 op 仍要能解析到新工具。
LEGACY_PLAN_OPS: Dict[str, str] = {
    "subagent": "delegate",
    "team": "delegate",
    "ask": "ask_user",
    "goal_manage": "goal_manage",
}

_PACK_BY_CODE = {p.code: p for p in PACKS}
_TOOL_TO_PACK: Dict[str, str] = {}
for _pack in PACKS:
    for _code in _pack.ops.values():
        _TOOL_TO_PACK[_code] = _pack.code


# ── 配置开关 ────────────────────────────────────────────────────────────

_CACHE: Dict[str, Any] = {"at": 0.0, "value": True}


def enabled() -> bool:
    """是否启用工具包（config.yaml `tools.packs`，默认 true）。"""
    now = time.monotonic()
    if now - float(_CACHE.get("at") or 0) < 2.0:
        return bool(_CACHE.get("value", True))
    value = True
    try:
        from services.shared.project_paths import get_project_root
        from services.shared.yaml_config import load_service_config

        root = str(get_project_root())
        path = os.environ.get("AI_PLATFORM_CONFIG_YAML") or os.path.join(
            root, "config", "agent_zero", "config.yaml")
        cfg = load_service_config(path) or {}
        sec = cfg.get("tools") or {}
        value = sec.get("packs", True) is not False
    except Exception:
        logger.debug("读取 tools.packs 失败（按启用处理）", exc_info=True)
    _CACHE.update(at=now, value=value)
    return value


def is_pack(name: str) -> bool:
    """这个名字是不是工具包（用于时间线展示「模型调用的是哪个包」）。

    已拆掉的旧包名（`code`）与旧别名（`agent`）也算——历史消息里它们仍然出现，
    时间线要能照样渲染成工具包徽标。
    """
    key = str(name or "").strip()
    return key in _PACK_BY_CODE or key in PACK_ALIASES or key in LEGACY_PACK_OPS


def pack_op_label(name: str, args: Optional[Dict[str, Any]]) -> str:
    """时间线徽标用的 op：只接受**该包已知的动作名**，其余一律返回空串。

    模型偶尔会把整条命令塞进 op（现场示例：
    `code` + `op="cd /Users/... && python3 - <<'PY' ..."`）。resolve_call 会
    回一条「工具包 code 已拆分为独立工具」的错给模型，但徽标若照抄 op，就会把
    整条命令渲染成一个标签，把时间线顶出容器、连换行都不给。这里只认
    op ∈ pack.ops（或直接写具体工具名），长度再兜一层。
    """
    key = str(name or "").strip()
    legacy = LEGACY_PACK_OPS.get(key)
    if legacy is not None:
        op = str((args or {}).get(PACK_OP_FIELD) or "").strip()
        if op in legacy:
            return op[:32]
        for name_or_code, code in legacy.items():
            if op == code:
                return name_or_code[:32]
        return ""
    pack = _PACK_BY_CODE.get(key) or _PACK_BY_CODE.get(PACK_ALIASES.get(key, ""))
    if pack is None:
        return ""
    op = str((args or {}).get(PACK_OP_FIELD) or "").strip()
    if op in pack.ops:
        return op[:32]
    for key2, code in pack.ops.items():         # 直接写工具名当 op：归一成动作名
        if op == code:
            return key2[:32]
    return ""


# ── 生成发给模型的 function ─────────────────────────────────────────────

def _first_sentence(text: str) -> str:
    body = " ".join(str(text or "").split())
    for sep in ("。", "；", ".\n", ".\r", ". "):
        idx = body.find(sep)
        if 0 < idx < len(body) - 1:
            return body[: idx + 1]
    return body


def _merge_property(prop: Dict[str, Any], spec: Dict[str, Any],
                    op: str) -> Dict[str, Any]:
    """同名参数合并：类型必须一致，描述里标注它在该动作下的含义。"""
    merged = dict(prop)
    old_type = str(prop.get("type") or "")
    new_type = str(spec.get("type") or "")
    if old_type and new_type and old_type != new_type:
        logger.warning("工具包参数类型冲突：op=%s (%s vs %s)", op, old_type, new_type)
        return merged
    old_desc = str(prop.get("description") or "")
    new_desc = str(spec.get("description") or "")
    if new_desc and new_desc not in old_desc:
        merged["description"] = (
            f"{old_desc}；op={op} 时：{new_desc}" if old_desc else new_desc)
    # 同名参数在不同动作下可能有不同枚举（如 status：步骤 / 待办 / 目标）。
    # 取并集而不是保留第一个动作的枚举，否则后加入的动作合法值会被 schema 挡掉。
    old_enum = prop.get("enum")
    new_enum = spec.get("enum")
    if isinstance(new_enum, list):
        # 先出现的动作可能没有枚举（例如 tool_job 的 action 是自由字符串），
        # 后面出现的枚举也要补进来，不能因为第一个没有就把合法的值挡掉。
        merged["enum"] = list(dict.fromkeys([*(old_enum or []), *new_enum]))
    return merged


def _pack_schema(pack: ToolPack, ops: Dict[str, str],
                 tool_reg: Any) -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        PACK_OP_FIELD: {
            "type": "string",
            "enum": list(ops.keys()),
            "description": "动作：" + "；".join(
                f"{op}={tool_reg.get_tool(code).name or code}"
                for op, code in ops.items()),
        },
    }
    names = [str(tool_reg.get_tool(code).name or code) for code in ops.values()
             if tool_reg.get_tool(code) is not None]
    lines: List[str] = [f"{pack.title}工具包：{'、'.join(names)}。",
                        "用 op 选择动作（先选 op，再填该动作的参数）："]
    for op, code in ops.items():
        tool = tool_reg.get_tool(code)
        if tool is None:
            continue
        own = _has_own_op(tool)
        req_raw = {str(x) for x in (tool.parameters.get("required") or [])}
        # 自带 op 的工具在包里叫 action（见 _apply_subop），描述里也用 action
        required = ["action" if x == PACK_OP_FIELD else str(x) for x in req_raw]
        optional = ["action" if k == PACK_OP_FIELD else str(k)
                    for k in (tool.parameters.get("properties") or {})
                    if k not in req_raw]
        hint = f"必填：{', '.join(required) or '无'}"
        if optional:
            hint += f"；可选：{', '.join(optional)}"
        if own:
            hint += "（action 见该工具说明）"
        label = f"{op}（{code}）" if code != op else op
        lines.append(f"- {label}：{tool.name or code} — {_first_sentence(tool.description)}"
                     f"（{hint}）")
        for name, spec in (tool.parameters.get("properties") or {}).items():
            if not isinstance(spec, dict):
                continue
            # 工具自己的 op → 包里的 action：包的 op 已经被用来选工具了
            key = "action" if name == PACK_OP_FIELD else name
            if key in properties:
                properties[key] = _merge_property(properties[key], spec, op)
            else:
                properties[key] = dict(spec)
    return {
        "type": "function",
        "function": {
            "name": pack.code,
            "description": "\n".join(lines),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": [PACK_OP_FIELD],
                "additionalProperties": False,
            },
        },
    }


def _tool_allowed(code: str, allowed: set, blocked: set,
                  has_working_dir: bool, tool_reg: Any) -> bool:
    if code in INLINE_TOOLS or code in blocked:
        return False
    if not has_working_dir and code in tool_reg.WORKSPACE_REQUIRED_TOOLS:
        return False
    tool = tool_reg.get_tool(code)
    if tool is None:
        return False
    if tool.gated and code not in allowed:
        return False
    return True


def _has_own_op(tool: Any) -> bool:
    """工具自己就有 op 参数（vcs / codegraph / workspace）。

    进包时包的 op 已用于选工具，工具自己的 op 在 schema 里改名为 action，
    执行前由 `_apply_subop` 还原。
    """
    return PACK_OP_FIELD in ((tool.parameters or {}).get("properties") or {})


def build_pack_specs(envelope_tools: Iterable[str], *,
                     has_working_dir: bool = True,
                     excluded: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """按白名单生成包级 function（只保留至少有一个动作可用的包）。"""
    from services.agent_zero.tools import registry as tool_reg

    allowed = {tool_reg.canonical(t) for t in (envelope_tools or [])}
    blocked = {tool_reg.canonical(t) for t in (excluded or [])}
    out: List[Dict[str, Any]] = []
    for pack in PACKS:
        ops: Dict[str, str] = {}
        for op, code in pack.ops.items():
            if not _tool_allowed(code, allowed, blocked, has_working_dir, tool_reg):
                continue
            ops[op] = code
        if not ops:
            continue
        out.append(_pack_schema(pack, ops, tool_reg))
    return out


def unpacked_codes(envelope_tools: Iterable[str], *,
                   has_working_dir: bool = True,
                   excluded: Iterable[str] = ()) -> List[str]:
    """**不进包、需要单独下发**的工具。

    只返回 `STANDALONE_TOOLS`：不进包、需要单独下发的工具。包内自带 `op`
    参数的工具（vcs / codegraph）由包层把 `action` 还原成 `op`，不再额外下发。
    """
    from services.agent_zero.tools import registry as tool_reg

    allowed = {tool_reg.canonical(t) for t in (envelope_tools or [])}
    blocked = {tool_reg.canonical(t) for t in (excluded or [])}
    out: List[str] = []
    for code in STANDALONE_TOOLS:
        if code in GLOBAL_ONLY_TOOLS:
            continue
        if not _tool_allowed(code, allowed, blocked, has_working_dir, tool_reg):
            continue
        if code not in out:
            out.append(code)
    return out


def unpacked_specs(envelope_tools: Iterable[str], *,
                   has_working_dir: bool = True,
                   excluded: Iterable[str] = ()) -> List[Dict[str, Any]]:
    from services.agent_zero.tools import registry as tool_reg

    specs: List[Dict[str, Any]] = []
    for code in unpacked_codes(envelope_tools, has_working_dir=has_working_dir,
                               excluded=excluded):
        tool = tool_reg.get_tool(code)
        if tool is not None:
            specs.append(tool.schema())
    return specs


# ── 调用解析 ────────────────────────────────────────────────────────────

def _translate_merged(code: str, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """合并后的工具 → 具体实现（旧工具名与旧调用继续可用）。

    - goal_manage：action=report 或带有 status（旧 goal_report 调用）→ goal_report；
    - delegate：target=team 或带 member_id / member_name → call_team，否则 call_subagent。

    放在 resolve_call 里统一翻译，对话循环与分发器仍按旧的具体工具名分支，
    行为、权限、事件完全不变。
    """
    if code == "goal_manage":
        action = str(data.get("action") or "").strip().lower()
        if action == "report" or (not action and data.get("status")):
            out = {k: v for k, v in data.items() if k != "action"}
            out.setdefault("status", "progress")
            return "goal_report", out
        if not action and not data.get("status"):
            return "goal_manage", {**data, "action": "status"}   # 空调用 = 查看
        return "goal_manage", data
    if code == "delegate":
        target = str(data.get("target") or "").strip().lower()
        out = {k: v for k, v in data.items() if k != "target"}
        if target == "team" or out.get("member_id") or out.get("member_name"):
            return "call_team", out
        return "call_subagent", out
    return code, data


def _apply_subop(code: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """包里的 `action` → 工具真正读取的 `op`（只对自带 op 的工具）。"""
    from services.agent_zero.tools import registry as tool_reg

    tool = tool_reg.get_tool(code)
    if tool is not None and _has_own_op(tool) and "action" in data:
        out = dict(data)
        out["op"] = out.pop("action")
        return out
    return data


def resolve_call(name: str, args: Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """把模型的一次调用解析成 (具体工具, 参数, 错误)。

    - 包调用（`fs` + `op=read`）→ (`read_file`, {path: …})；
    - 直接点名（`read_file` / 旧别名）→ 原样返回；
    - 旧包名（`agent`）与已拆分包的旧 op（`code{op:shell}`）继续可用；
    - 缺少 op、未知 op、缺必填参数 → 返回可直接回给模型的错误文案。
    """
    from services.agent_zero.tools import registry as tool_reg

    raw = str(name or "").strip()
    canon = tool_reg.canonical(raw)
    data: Dict[str, Any] = dict(args or {})
    # 已拆掉的包（code）：整个包名不再存在，但历史消息里的 `code{op:shell}`
    # 必须还能跑，所以先按 LEGACY_PACK_OPS 把 op 换成具体工具再继续。
    legacy_ops = LEGACY_PACK_OPS.get(raw)
    if legacy_ops is not None:
        op = str(data.pop(PACK_OP_FIELD, "") or "").strip()
        code = legacy_ops.get(op) or (
            op if op in legacy_ops.values() else None)
        if code is None:
            return canon, data, (
                f"工具包 {raw} 已拆分为独立工具（"
                + "、".join(sorted(set(legacy_ops.values())))
                + f"），请直接调用它们（收到的 op={op!r}）")
        return _translate_merged(tool_reg.canonical(code), data) + (None,)

    pack = (_PACK_BY_CODE.get(raw) or _PACK_BY_CODE.get(canon)
            or _PACK_BY_CODE.get(PACK_ALIASES.get(raw, ""))
            or _PACK_BY_CODE.get(PACK_ALIASES.get(canon, "")))
    if pack is None:
        # 直接点名 / 旧别名：合并后的工具在这里拆成具体实现
        code, data = _translate_merged(canon, data)
        return code, _apply_subop(code, data), None

    op = str(data.pop(PACK_OP_FIELD, "") or "").strip()
    code = pack.ops.get(op)
    if code is None and pack.code == "plan":
        code = LEGACY_PLAN_OPS.get(op)              # 旧 op 名继续可用
    if code is None and op in pack.ops.values():
        code = op                                   # 允许直接写工具名当 op
    if code is None:
        return canon, data, (
            f"工具包 {pack.code} 需要参数 {PACK_OP_FIELD}，可选："
            + "、".join(pack.ops.keys())
            + f"（收到的 op={op!r}）")
    data = _apply_subop(code, data)
    tool = tool_reg.get_tool(code)
    if tool is None:
        return canon, data, f"工具包 {pack.code} 的 op={op} 没有对应实现"
    missing = [str(x) for x in (tool.parameters.get("required") or [])
               if data.get(x) in (None, "")]
    if missing:
        # 自带 op 的工具有些参数在包里叫 action，报错也用它叫得出口的名字
        shown = ["action" if x == PACK_OP_FIELD else x for x in missing]
        return code, data, (
            f"工具包 {pack.code} 的 op={op}（{tool.name or code}）缺少参数："
            + "、".join(shown))
    return _translate_merged(code, data) + (None,)


def catalog(has_working_dir: bool = True) -> Dict[str, Any]:
    """设置 → 工具页用的目录（纯数据；显示名由前端按语言渲染）。

    - 每个包：code / 是否可用 / 动作列表（op、具体工具、风险、是否可用）；
    - 独立工具：shell / workspace / ask_user。
    - `has_working_dir` 只影响 `available`，不影响列出哪些工具：页面要能完整
      展示工具全集，纯聊天工作区只把 `workspace` 置灰（见下面 standalone 的注释）。
    """
    from services.agent_zero.tools import registry as tool_reg

    packs_out: List[Dict[str, Any]] = []
    for pack in PACKS:
        ops: List[Dict[str, Any]] = []
        for op, code in pack.ops.items():
            tool = tool_reg.get_tool(code)
            if tool is None:
                continue
            entry = {
                "op": op,
                "tool": code,
                "risk": tool.risk,
                "workspace_required": code in tool_reg.WORKSPACE_REQUIRED_TOOLS,
                "available": has_working_dir or code not in tool_reg.WORKSPACE_REQUIRED_TOOLS,
            }
            # delegate 的 team 目标要先有绑定团队；页面据此标注（不是工作区问题）
            if code == "delegate":
                entry["conditional"] = "team"
            ops.append(entry)
        if not ops:
            continue
        packs_out.append({
            "code": pack.code,
            "available": any(o["available"] for o in ops),
            "ops": ops,
        })

    standalone: List[Dict[str, Any]] = []
    # 独立工具**总是全部列出**（纯聊天工作区只把可用性标成 false，页面置灰）。
    # 原因：这份目录由 /app/info 生成，那里的 has_working_dir 取的是**默认工作区**；
    # 若按它过滤，从纯聊天默认工作区切到有目录的工作区后，「独立工具」会一直是空的，
    # 直到重启（用户反馈：设置 → 工具 需要先更新）。
    for code in unpacked_codes(tool_reg.known_codes()):
        if code in _TOOL_TO_PACK:
            continue          # 已经作为某个包的动作出现，不再重复列
        tool = tool_reg.get_tool(code)
        if tool is None:
            continue
        standalone.append({
            "code": code,
            "risk": tool.risk,
            "workspace_required": code in tool_reg.WORKSPACE_REQUIRED_TOOLS,
            "available": has_working_dir or code not in tool_reg.WORKSPACE_REQUIRED_TOOLS,
        })
    return {"packs": packs_out, "standalone": standalone,
            "chat_only": not has_working_dir}

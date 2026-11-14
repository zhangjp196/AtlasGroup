"""Conversation tool schemas, capability filtering, and dispatch policy."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# 对话阶段按角色能力包络放开**全部**工具（与历史任务节点同一 build_tool_specs）。
# 有"顺序语义"的工具：一轮里只要出现其一，就整轮走串行（不并发），避免乱序 / 共享状态竞争。
#
# 判定发生在 `resolve_call` 之后，所以这里写的是**归一化后的具体工具名**。名单必须覆盖
# 每一类「不走分发器、由本函数里的专有分支处理」的工具：
#   - 瞬时动作：propose_task / todo_write / goal_report；
#   - 会就地改共享状态：team_manage（本轮的 specs 与对话的 team 绑定）、goal_manage；
#   - 派生子代理 / 团队成员：call_subagent / call_team（长时间、且要回放事件顺序）；
#     delegate 是它们合并后的入口，resolve_call 会拆成上面两个名字，这里也留一份兜底；
#   - 等用户：ask_user；监督后台任务：tool_job。
# 漏掉其中任何一个，模型把「它 + 一个普通工具」放在同一轮时，那个调用会先被并发批
# 送进分发器（那里没有它的实现，白跑一次），再由专有分支重跑一遍——既浪费一轮往返，
# 也埋着「哪天分发器补上实现就变成双写」的隐患（2026-10-30 整理时发现
# goal_manage / team_manage / call_team 都漏了）。
_SEQ_CALLS_TOOLS = frozenset({
    "propose_task",
    "todo_write",
    "goal_report",
    "goal_manage",
    "call_subagent",
    "call_team",
    "delegate",
    "background_task",
    "proactive_send",
    "role_discussion",
    "team_manage",
    "department_manage",
    "role_manage",
    "schedule_manage",
    "app_manage",
    "session_manage",
    "ask_user",
    # 工具监督本身是「读共享注册表 + 可能长时间 wait」：走串行，避免与
    # 其它工具的并发批互相干扰（也让 wait 的语义可预测）。
    "tool_job",
})
# 历史消息 / 旧提示词里的名字：registry 里已没有对应工具（分发器会把它们当未知
# 工具原样返回），但对话循环的兼容分支仍会处理，因此同样必须走串行。单独列出，
# 让「当前工具」与「历史兼容」在代码里一眼可分。
_SEQ_CALLS_LEGACY = frozenset({
    "todo",                       # → todo_write 的旧名
    "spawn_subagent",
    "call_agent", "call_role", "spawn_agent",
})
_SEQ_CALLS = _SEQ_CALLS_TOOLS | _SEQ_CALLS_LEGACY


def tool_reg_canonical(name: str) -> str:
    """工具名归一（包名 / 别名 → 具体工具 code）；失败时原样返回。"""
    try:
        from services.agent_zero.tools import registry as _reg

        return _reg.canonical(name)
    except Exception:
        return name


# **永不转后台**的工具：它们的「长时间没返回」是设计语义，不是挂住。
#   - ask_user：等用户选，等多久都合理；转后台等于「没等答案就继续」；
#   - tool_job：本身就是监督工具，它 wait 时被转后台会变成套娃；
#   - propose_task / todo_write / goal_report：瞬时动作。
# 这些工具仍受 TOOL_MAX_SECONDS（默认 0 = 不限）约束，但没有前台预算。
NO_ADOPT_TOOLS = frozenset({"ask_user", "tool_job", "propose_task",
                            "todo_write", "todo", "goal_report", "goal_manage",
                            "delegate", "background_task", "role_discussion"})
# 工具轮次不设上限：模型自己不再返回 tool_calls 时才收尾。
# 曾用 MAX_CHAT_TOOL_ROUNDS=6 截断，导致「分析整个仓库」这类长任务在第 6 轮被砍断——
# 循环自然走完时最后一轮仍是 tool_calls，没有任何纯文字收尾，
# 最终 text 为空，前端只剩「模型未单独给出结论」（2026-10-01 线上现象）。

# ── 超时策略：对话本身没有会话超时，单个工具默认也不设墙钟上限 ──────────
#
# 前端已刻意不设会话超时（api/agentZero.js 里两个超时参数都是 0）。但**中间
# 还有一层**：nginx（/opt/homebrew/etc/nginx/nginx.conf）反代 /hzclaw2/ 时
# 设了 `proxy_read_timeout 60s`——上游 60 秒没有字节就断开连接。
# 所以慢工具执行期间不能真的静默，必须按这个间隔发心跳把 nginx 的空闲计时
# 压住（同样也能压住任何其它中间层的读超时）。
# ── 展示载荷总预算（一条消息内所有工具加起来）────────────────────────
#
# 0 = 不限。单个文件载荷仍由 file_ops 的展示上限约束，但不再按「一条消息
# 48 KB」把后续工具结果正文剥掉——长任务里那会让用户看不到后面的 diff / 文件内容。
DISPLAY_BUDGET_PER_MESSAGE = 0


def _fit_display(display: Optional[Dict[str, Any]],
                 used: int) -> Tuple[Optional[Dict[str, Any]], int]:
    """把一个展示载荷塞进本条消息的剩余预算，返回 (载荷, 新用量)。

    超预算时不整条丢弃 —— 丢掉的话用户看到的是「这一步没有内容」，
    比看到「读过 a.py 第 40-47 行，正文省略」糟糕得多。
    """
    if not display:
        return None, used
    cost = len(str(display.get("text") or display.get("diff") or ""))
    if not DISPLAY_BUDGET_PER_MESSAGE or used + cost <= DISPLAY_BUDGET_PER_MESSAGE:
        return display, used + cost
    stripped = {k: v for k, v in display.items()
                if k not in ("text", "diff", "entries")}
    stripped["omitted"] = True
    return stripped, used


TOOL_KEEPALIVE_SECONDS = 20.0      # 须明显小于 nginx 的 60s，留足抖动余量
# 单个工具的墙钟上限（兜底：防工具挂死时无限等下去）。0 = 不限。
# 注意：工具直接在本轮等待完成（有心跳保活），**不再转后台**。
TOOL_MAX_SECONDS = 0.0

# 说明：对话侧的文件工具**不再单列一套长名**（历史遗留）。read_file /
# edit_file / write_file / list_files 就是唯一那套，由 `_chat_tool_specs` 从
# registry 按 envelope 门控生成；长名（*_workspace_*）仅作别名保留兼容旧调用。

PROPOSE_TASK_TOOL: Dict[str, Any] = {
    "type": "function", "function": {
        "name": "propose_task",
        "description": (
            "当用户的目标已足够清晰、且确实希望执行时，调用本工具**提出**在本对话内"
            "自主执行（动态规划推进到完成）的建议；系统会请用户点击确认后才开始执行。"
            "仅用于「要去做」的诉求；纯咨询 / 闲聊 / 澄清阶段不要调用。"
        ),
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string", "description": "清晰、可执行的执行目标"},
            "reason": {"type": "string", "description": "为什么现在适合自主执行（可选）"},
        }, "required": ["goal"]},
    },
}

BACKGROUND_TASK_TOOL: Dict[str, Any] = {
    "type": "function", "function": {
        "name": "background_task",
        "description": (
            "把一段或多段耗时 / 多步的工作放到**后台任务**执行（对话与任务分离）。"
            "多项互不依赖的任务用 `tasks` 数组一次派发，它们会并行执行；"
            "调用后本轮只回一句确认并收尾：执行过程不会进入对话；"
            "完成后系统会在这个对话里生成任务完成触发器，由角色继续整合。"
            "适合长任务、批处理、需要大量工具的工作；简单问答 / 纯聊天不要用。"
        ),
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string", "description": "单个后台任务的目标 / 交付物（与 tasks 二选一）"},
            "title": {"type": "string", "description": "任务标题（可选，便于识别）"},
            "context": {"type": "string", "description": "补充上下文（可选）"},
            "tasks": {
                "type": "array",
                "description": "并行派发多项独立任务：每项各自建一条后台任务并同时执行。"
                               "只用于确实互不依赖的工作；不要一次只派一项后干等。",
                "items": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string",
                                 "description": "该任务的目标 / 交付物"},
                        "title": {"type": "string",
                                  "description": "任务标题（可选）"},
                        "context": {"type": "string",
                                    "description": "只给该任务的上下文（可选）"},
                    },
                    "required": ["goal"],
                },
            },
        }, "required": []},
    },
}

# 一次最多并行派发多少条后台任务（防止模型一口气建出几十条把队列打满）。
MAX_BACKGROUND_TASKS_PER_CALL = 8

ROLE_DISCUSSION_TOOL: Dict[str, Any] = {
    "type": "function", "function": {
        "name": "role_discussion",
        "description": (
            "让 2-3 个角色（含你自己）进行多轮直接讨论，不经过主持转述。"
            "适合头脑风暴、方案对比、交互验证、交叉审查。"
            "角色按顺序发言，后发言者能看到前面所有发言；讨论过程显示为子轨迹，"
            "最终把完整记录返回给你。"
        ),
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "讨论主题 / 要解决的问题"},
            "roles": {
                "type": "array",
                "description": "参与讨论的角色 id 或名字（2-3 个，必须包含至少两个不同角色）",
                "items": {"type": "string"},
            },
            "rounds": {
                "type": "integer", "minimum": 1, "maximum": 3,
                "description": "讨论轮数（默认 2）",
            },
            "mode": {
                "type": "string", "enum": ["brainstorm", "verify"],
                "description": "brainstorm=发散头脑风暴；verify=交互验证 / 交叉审查",
            },
            "context": {
                "type": "string",
                "description": "给参与角色的补充上下文（可选）",
            },
        }, "required": ["topic", "roles"]},
    },
}
MAX_DISCUSSION_ROLES = 3
MAX_DISCUSSION_ROUNDS = 3
PROACTIVE_SEND_TOOL: Dict[str, Any] = {
    "type": "function", "function": {
        "name": "proactive_send",
        "description": (
            "在当前会话里**主动向用户发起一条消息**（不需要用户先提问）。"
            "适合：后台任务完成后汇报、发现重要变化、长时间任务的中途进展、"
            "需要提醒用户但用户暂时没在对话里。"
            "消息会延迟几分钟后由你自己生成并出现在会话中；不要频繁使用，"
            "全局 Agent 会限制频率和安静时段。"
        ),
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string",
                         "description": "要主动告诉用户的要点（不是逐字稿，模型会据此生成回复）"},
            "delay_minutes": {"type": "integer", "minimum": 1, "maximum": 1440,
                               "description": "多少分钟后发起（默认 1 分钟）"},
            "reason": {"type": "string", "description": "为什么现在要主动发起（可选）"},
        }, "required": ["message"]},
    },
}
MAX_PROACTIVE_DELAY_MIN = 1440




def _background_task_items(args: Dict[str, Any]) -> List[Dict[str, str]]:
    """把 `background_task` 参数归一成任务列表（兼容旧的单 goal 调用）。

    `tasks` 里的每一项都是独立后台任务：各自一条 team_task、一个成员 run，
    由 worker 并行执行。没有 `tasks` 时退化为单任务。
    """
    items: List[Dict[str, str]] = []

    def _append(source: Dict[str, Any]) -> None:
        goal = str((source or {}).get("goal") or "").strip()
        if not goal:
            return
        items.append({
            "goal": goal[:8000],
            "title": str((source or {}).get("title") or "").strip()[:120],
            "context": str((source or {}).get("context") or "").strip()[:8000],
        })

    raw = (args or {}).get("tasks")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                _append(item)
    if not items:
        _append(args or {})
    return items[:MAX_BACKGROUND_TASKS_PER_CALL]


def _append_background_task_tool(specs: List[Dict[str, Any]]) -> None:
    """把「后台任务」工具补进本轮工具清单（角色会话 / 群组主持都可用）。

    群组里团队委派用 delegate；后台任务用于把**本角色自己**的多项独立工作
    并行放到后台，两者不冲突。
    """
    names = {(s.get("function") or {}).get("name") for s in specs}
    if "background_task" in names:
        return
    specs.append(BACKGROUND_TASK_TOOL)


def _append_role_discussion_tool(specs: List[Dict[str, Any]]) -> None:
    """把「角色讨论」工具补进本轮工具清单（含后台任务成员；子代理不注入）。"""
    names = {(s.get("function") or {}).get("name") for s in specs}
    if "role_discussion" in names:
        return
    specs.append(ROLE_DISCUSSION_TOOL)

def _append_proactive_tool(specs: List[Dict[str, Any]]) -> None:
    """主动消息只给主会话角色，子代理 / 后台任务成员不注入。"""
    names = {(s.get("function") or {}).get("name") for s in specs}
    if "proactive_send" in names:
        return
    specs.append(PROACTIVE_SEND_TOOL)


def _chat_envelope(role: Dict[str, Any]) -> Dict[str, Any]:
    """对话角色的能力包络（与任务节点同源，见 config_source.envelope_from_role）。"""
    from services.agent_zero.runtime.zero import config_source

    return config_source.envelope_from_role(role)


def _chat_tool_specs(envelope: Dict[str, Any],
                     caps: Any = None,
                     autonomous: bool = False,
                     workspace_id: Optional[str] = None,
                     mcp_specs: Optional[List[Dict[str, Any]]] = None,
                     team_bound: bool = False) -> List[Dict[str, Any]]:
    """对话可用工具 = 角色包络内的标准工具 + 转任务建议。

    **工具包**（`tools.packs: true`，默认）：按用途合并成 fs / net / memory /
    plan / skill / mcp，每个包一个 function + `op`，执行与权限仍按具体工具名
    （`packs.resolve_call` 负责解析）。

    **命令与代码类走独立工具**（`packs.STANDALONE_TOOLS`）：shell / tool_job /
    vcs / codegraph / workspace 各自一个 function，直接按名字调用。它们参数差异大、
    风险等级不同，套一层 `op` 只会让模型绕路（也更容易把整条命令写进 op）。
    `propose_task` 由对话层直接处理，保持独立。

    **任务 / 目标 / 协作合并为一个 `plan` 包**：todo（任务清单）/ goal（目标模式）
    / delegate（子代理与团队成员）/ team_admin / role_admin，边界都是「编排自己的执行」，
    合并后模型少一次包选择；旧包名 `agent` 作为别名继续可解析。

    **文件工具只有一套名字**（read_file / edit_file / write_file / list_files），
    与其他标准工具一样受 `envelope.tools` 门控——不再有"对话恒开的另一套长名"。
    此前对话额外恒开一套长名（list_workspace_files / read_workspace_file /
    edit_workspace_file / write_workspace_file），于是同一个「读文件」模型会看到
    两个工具（实测混淆），而且 edit/write 恒开、绕过了能力包络（与 parity 测试
    "读写必须显式授予" 相矛盾）。

    进度只有**任务清单**一个载体（`plan{op:todo}` = 整份覆盖式 `todo_write`）：
    多步任务先列清单，推进时更新状态；界面在对话里直接渲染这张清单，右侧不再有
    独立的「计划流程」面板。``autonomous`` 参数保留（自主执行同样使用它）。

    纯聊天工作区（没有用户文件夹）：只有 `workspace`（中间存储）不下发；shell 与
    文件类工具照常可用，落点是托管目录（`workspace_service.exec_root`）。
    """
    from services.agent_zero.tools import registry as tool_reg
    from services.agent_zero.tools import packs as tool_packs
    from services.agent_zero.services import workspace_service as wsvc

    has_dir = wsvc.has_working_dir(workspace_id) if workspace_id else False
    allowed = envelope.get("tools") or []
    excluded = tool_reg.WORKSPACE_BOUND_TOOLS if not workspace_id else frozenset()
    use_packs = tool_packs.enabled()

    specs: List[Dict[str, Any]] = []
    if use_packs:
        # 工具包：一个包一个 function + op；命令类工具由 unpacked_specs 单独下发
        specs.extend(tool_packs.build_pack_specs(
            allowed, has_working_dir=has_dir, excluded=excluded))
        # 独立工具：shell / workspace / ask_user（vcs / codegraph / tool_job
        # 由 dev 包统一下发）。
        specs.extend(tool_packs.unpacked_specs(
            allowed, has_working_dir=has_dir, excluded=excluded))
    else:
        for s in tool_reg.build_tool_specs(allowed, has_working_dir=has_dir):
            if s["function"]["name"] not in excluded:
                specs.append(s)
    specs.extend(list(getattr(caps, "tool_specs", None) or []))
    specs.append(PROPOSE_TASK_TOOL)

    # MCP 服务工具（mcp__<server>__<tool>）：由 _stream_role 预先列好传入
    specs.extend(mcp_specs or [])

    dedup: List[Dict[str, Any]] = []
    seen = set()
    for s in specs:
        n = s.get("function", {}).get("name")
        if n in seen:
            continue
        seen.add(n)
        dedup.append(s)
    return dedup

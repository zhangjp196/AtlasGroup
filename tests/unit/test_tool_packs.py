"""工具包（tool packs）回归测试。

- 每个标准工具都必须归属某个包 / 独立工具 / 对话层名单，三类互不重叠，
  避免以后新增工具忘了挂载、模型侧悄悄又变多；
- 包 schema 只包含白名单里的动作；
- 包调用解析成具体工具（权限 / 执行 / UI 仍用旧名字），旧名与别名继续可用；
- 命令与代码类是独立工具（不再有 code 包），旧 code{op:...} 调用继续可用；
- 计划 / 目标 / 协作合并成 plan 一个包，旧包名 agent 仍可解析；
- 工具自己的 `action` 参数不会被包的 `op` 吃掉；
- 端到端：`net` + `op=time` 能经分发器真的执行。
"""
from __future__ import annotations

import asyncio

import pytest

from services.agent_zero.stores import zero_store
from services.agent_zero.tools import packs, registry


@pytest.fixture(autouse=True)
def _reset_pack_cache():
    """配置缓存是模块级的：每个测试从默认值开始，避免读到别的测试的临时配置。"""
    packs._CACHE.update(at=0.0, value=True)


# ── 覆盖性 ──────────────────────────────────────────────────────────────

def test_every_tool_is_packed_standalone_or_inline():
    packed = [code for pack in packs.PACKS for code in pack.ops.values()]
    assert len(packed) == len(set(packed)), "同一个工具被放进两个包"
    standalone = set(packs.unpacked_codes(registry.known_codes()))
    assert not (standalone & set(packed)), "独立工具又挂进了包"
    missing = (set(registry.known_codes()) - set(packed)
               - set(packs.INLINE_TOOLS) - standalone
               - set(packs.MERGED_AWAY_TOOLS)
               - set(packs.GLOBAL_ONLY_TOOLS))
    assert not missing, f"这些工具既没挂包也不算内联：{sorted(missing)}"


def test_catalog_lists_every_standalone_tool_even_without_workspace():
    """设置 → 工具页在任何工作区都列全工具，纯聊天只置灰不可用的那一项。

    目录由 /app/info 生成，那里的 has_working_dir 取的是**默认工作区**（可能是
    纯聊天）。若按它过滤独立工具，从纯聊天默认工作区切到有目录的工作区后，
    「独立工具」区域会一直是空的，直到重启（用户反馈：设置 → 工具 需要先更新）。
    """
    full = packs.catalog(has_working_dir=True)
    chat = packs.catalog(has_working_dir=False)
    full_codes = [s["code"] for s in full["standalone"]]
    chat_codes = [s["code"] for s in chat["standalone"]]
    assert full_codes == chat_codes, "纯聊天工作区把独立工具从目录里删掉了"
    assert {"shell", "workspace", "ask_user"} <= set(chat_codes)
    assert all(s["available"] for s in full["standalone"])
    # 纯聊天工作区：只有需要用户文件夹的 workspace 置灰，shell / ask_user 仍可用
    unavailable = {s["code"] for s in chat["standalone"] if not s["available"]}
    assert unavailable == {"workspace"}


def test_dev_tools_are_packed_shell_stays_standalone():
    """后台任务 / 版本控制 / 代码图谱合成 dev 包；shell 保持独立。"""
    dev = next(p for p in packs.PACKS if p.code == "dev")
    assert set(dev.ops) == {"job", "vcs", "graph"}
    assert dev.ops["job"] == "tool_job"
    assert dev.ops["vcs"] == "vcs"
    assert dev.ops["graph"] == "codegraph"
    standalone = set(packs.unpacked_codes(registry.known_codes()))
    assert {"shell", "workspace", "ask_user"} <= standalone
    assert not ({"tool_job", "vcs", "codegraph"} & standalone)
    # shell 不进任何包：审批预览要能看到完整命令
    assert "shell" not in {code for p in packs.PACKS for code in p.ops.values()}
    # 自带 op 的工具在包里用 action 选子操作，执行前还原成 op
    canon, args, err = packs.resolve_call("dev", {"op": "vcs", "action": "status"})
    assert (canon, err) == ("vcs", None)
    assert args == {"op": "status"}
    canon, args, err = packs.resolve_call("dev", {"op": "graph", "action": "find",
                                                  "query": "x"})
    assert (canon, err) == ("codegraph", None)
    assert args == {"op": "find", "query": "x"}


def test_plan_pack_absorbs_collaboration():
    """任务 / 目标 / 协作合成一个包，同类工具先合并，包内只留少量动作。"""
    names = {p.code for p in packs.PACKS}
    assert "plan" in names and "agent" not in names
    plan = next(p for p in packs.PACKS if p.code == "plan")
    assert set(plan.ops) == {"todo", "goal", "delegate", "team_admin", "role_admin",
                             "department_admin"}
    # 任务清单是唯一进度载体：包里的 todo 动作直指 todo_write
    assert plan.ops["todo"] == "todo_write"
    # 合并掉的旧入口不再下发（实现保留，历史消息仍可调用）
    assert "goal_report" in packs.MERGED_AWAY_TOOLS
    assert {"call_subagent", "call_team"} <= set(packs.MERGED_AWAY_TOOLS)
    assert "ask_user" in packs.STANDALONE_TOOLS
    # 旧包名仍然可解析（历史消息 / 旧提示词）：agent{op:subagent} → delegate
    canon, _, err = packs.resolve_call("agent", {"op": "subagent", "goal": "x"})
    assert (canon, err) == ("call_subagent", None)
    assert packs.is_pack("agent")


def test_tools_with_own_op_expose_action_inside_packs():
    """自带 op 的工具进包后把 op 暴露成 action，schema 里不能出现两个 op。"""
    specs = packs.build_pack_specs(registry.known_codes())
    dev = next(s for s in specs if s["function"]["name"] == "dev")
    props = dev["function"]["parameters"]["properties"]
    assert set(props["op"]["enum"]) == {"job", "vcs", "graph"}
    # vcs / codegraph 的 op / job 的 action 合并成一个 action 参数
    assert "action" in props and "op" in props
    assert {"status", "commit", "find", "summary"} <= set(
        props["action"]["enum"])
    # workspace 仍然独立（自带 op，且不属于 dev）
    assert "workspace" in packs.unpacked_codes(registry.known_codes())
    direct = packs.unpacked_specs(registry.known_codes())
    assert any(s["function"]["name"] == "workspace" for s in direct)


def test_pack_codes_do_not_shadow_tool_codes():
    names = {p.code for p in packs.PACKS}
    assert not (names & set(registry.known_codes()))
    assert packs.is_pack("fs") and packs.is_pack("net")
    assert not packs.is_pack("read_file")
    assert not packs.is_pack("shell")        # 独立工具不是包


# ── schema 生成 ─────────────────────────────────────────────────────────

def test_pack_specs_follow_envelope():
    specs = packs.build_pack_specs(["read_file", "edit_file"])
    by_name = {s["function"]["name"]: s for s in specs}
    # fs 只有 read/edit 在包络里；恒开的（ungated）工具照旧出现
    fs = by_name["fs"]["function"]["parameters"]
    assert fs["required"] == ["op"]
    assert fs["properties"]["op"]["enum"] == ["read", "edit"]
    assert "path" in fs["properties"]
    assert "net" in by_name


def test_pack_specs_keep_workspace_tools_without_dir():
    """纯聊天工作区（没有用户文件夹）：只有 `workspace` 不下发，其余照常可用。

    shell / 文件类工具在纯聊天工作区里改用托管目录（`workspace_service.exec_root`），
    所以它们必须照常出现在模型工具清单里；`workspace`（中间存储）的语义就是用户
    文件夹里的文件，没有文件夹时下发只会误导模型。
    """
    specs = packs.build_pack_specs(registry.known_codes(), has_working_dir=False)
    names = {s["function"]["name"] for s in specs}
    assert "fs" in names
    assert {"net", "memory", "plan", "dev", "skill", "mcp"} <= names
    # 独立工具：shell / ask_user / session_manage 仍在，workspace 被过滤
    chat_only = packs.unpacked_codes(registry.known_codes(), has_working_dir=False)
    assert chat_only == ["shell", "ask_user", "session_manage"]
    # dev 包在纯聊天工作区里动作齐全（job / vcs / graph）
    dev = next(s for s in specs if s["function"]["name"] == "dev")
    assert set(dev["function"]["parameters"]["properties"]["op"]["enum"]) == {
        "job", "vcs", "graph"}


def test_pack_schema_describes_each_action_and_maps_tool_names():
    specs = packs.build_pack_specs(registry.known_codes())
    fs = next(s for s in specs if s["function"]["name"] == "fs")
    desc = fs["function"]["description"]
    assert "read（read_file）" in desc
    assert "write（write_file）" in desc
    # 必填参数在描述里点出来，模型不用猜
    assert "必填：path" in desc


def test_chat_specs_use_pack_layer(monkeypatch):
    from services.agent_zero.services import workspace_service as wsvc
    from services.agent_zero.task_layer import conversation

    monkeypatch.setattr(packs, "enabled", lambda: True)
    monkeypatch.setattr(wsvc, "has_working_dir", lambda ws: True)
    specs = conversation._chat_tool_specs(
        {"tools": list(registry.known_codes())}, None, False, "ws")
    names = {s["function"]["name"] for s in specs}
    assert {"fs", "net", "memory", "plan", "dev", "skill", "mcp"} <= names
    assert "read_file" not in names            # 不再逐个下发
    assert "report_plan" not in names          # 在 dev{op:plan} 里
    assert "propose_task" in names
    # 独立工具：shell / workspace / ask_user（vcs 等在 dev 包里）
    assert {"shell", "workspace", "ask_user"} <= names
    assert not ({"tool_job", "vcs", "codegraph"} & names)
    assert "code" not in names and "agent" not in names
    assert len(specs) <= 14, names


# ── 调用解析 ────────────────────────────────────────────────────────────

def test_resolve_pack_call():
    canon, args, err = packs.resolve_call("fs", {"op": "read", "path": "a.py"})
    assert (canon, err) == ("read_file", None)
    assert args == {"path": "a.py"}


def test_resolve_accepts_tool_name_as_op():
    canon, args, err = packs.resolve_call("fs", {"op": "read_file", "path": "a.py"})
    assert (canon, err) == ("read_file", None)
    assert args == {"path": "a.py"}


def test_resolve_reports_actionable_errors():
    _, _, err = packs.resolve_call("fs", {"path": "a.py"})
    assert err and "op" in err

    _, _, err = packs.resolve_call("fs", {"op": "nope"})
    assert err and "op" in err and "read" in err

    _, _, err = packs.resolve_call("fs", {"op": "edit", "path": "a.py"})
    assert err and "old_string" in err and "new_string" in err


def test_pack_op_label_only_accepts_known_actions():
    """时间线徽标只认该包已知的动作名。

    现场事故：模型把整条命令塞进 op（
    `code` + `op="cd /Users/... && python3 - <<'PY' ..."`），徽标照抄 op 之后
    变成 `code.cd /Users/... && python3 - <<'PY' ...`，整条命令顶宽对话区。
    """
    # code 包已拆，但历史消息里的 code{op:shell} 徽标仍要能显示
    assert packs.pack_op_label("code", {"op": "shell"}) == "shell"
    assert packs.pack_op_label("plan", {"op": "delegate"}) == "delegate"
    # ask 已经从包里移出（独立工具），旧徽标不再成立
    assert packs.pack_op_label("plan", {"op": "ask"}) == ""
    assert packs.pack_op_label("fs", {"op": "read_file"}) == "read"
    # 未知 / 非法的 op 一律不给徽标（让工具条目退回「只有名字」的短标题）
    assert packs.pack_op_label(
        "code", {"op": "cd /Users/x && python3 - <<'PY'\nprint(1)\nPY"}) == ""
    assert packs.pack_op_label("code", {}) == ""
    # 不是包名（直接点名的具体工具）不给徽标
    assert packs.pack_op_label("shell", {"op": "shell"}) == ""


def test_pack_op_label_length_capped():
    assert len(packs.pack_op_label("plan", {"op": "x" * 200})) == 0
    assert packs.pack_op_label("fs", {"op": "read"}) == "read"


def test_resolve_direct_tool_and_alias_untouched():
    assert packs.resolve_call("read_file", {"path": "a"})[:2] == (
        "read_file", {"path": "a"})
    assert packs.resolve_call("read_workspace_file", {"path": "a"})[0] == "read_file"


def test_resolve_split_code_pack_keeps_history_working():
    """code 包已拆成独立工具，但历史消息里的 code{op:...} 仍要能执行。"""
    canon, args, err = packs.resolve_call("code", {"op": "shell", "cmd": "ls"})
    assert (canon, err) == ("shell", None)
    assert args == {"cmd": "ls"}
    canon, _, err = packs.resolve_call("code", {"op": "job", "action": "list"})
    assert (canon, err) == ("tool_job", None)
    # 未知 op 给出可执行的指引（告诉模型去直接调独立工具）
    _, _, err = packs.resolve_call("code", {"op": "nope"})
    assert err and "独立工具" in err


def test_plan_pack_is_enriched_and_merges_action_enums():
    """任务包只留少量动作；合并后的参数（action / status / target / todos）取并集。"""
    specs = packs.build_pack_specs(registry.known_codes())
    plan = next(s for s in specs if s["function"]["name"] == "plan")
    fn = plan["function"]
    props = fn["parameters"]["properties"]
    assert set(props["op"]["enum"]) == {
        "todo", "goal", "delegate", "team_admin", "role_admin",
        "department_admin"}
    # goal 合并了 goal_report + goal_manage：报告 / 状态 / 恢复都要在
    assert {"report", "status", "update", "note", "blocked", "resume", "close"} <= set(
        props["action"]["enum"])
    assert {"progress", "achieved", "blocked"} <= set(props["status"]["enum"])
    # delegate 合并了子代理与团队成员
    assert set(props["target"]["enum"]) == {"subagent", "team"}
    # 任务清单是整份覆盖式：参数是 todos（不再有 action）
    todos = props["todos"]
    assert todos["type"] == "array"
    item_props = todos["items"]["properties"]
    assert set(item_props["status"]["enum"]) == {
        "pending", "in_progress", "completed"}


def test_resolve_plan_and_goal_manage():
    # 任务清单：plan{op:todo} → todo_write（整份覆盖）
    canon, args, err = packs.resolve_call(
        "plan", {"op": "todo", "todos": [{"content": "x", "status": "pending"}]})
    assert (canon, err) == ("todo_write", None)
    assert args["todos"][0]["content"] == "x"

    # 目标：goal(action=report) → goal_report；其它 action → goal_manage
    canon, args, err = packs.resolve_call(
        "plan", {"op": "goal", "action": "report", "status": "progress", "note": "x"})
    assert (canon, err) == ("goal_report", None)
    assert args["status"] == "progress"
    canon, args, err = packs.resolve_call(
        "plan", {"op": "goal", "action": "status"})
    assert (canon, err) == ("goal_manage", None)
    # 旧 goal_report 调用（带 status、无 action）继续走 goal_report
    canon, _, err = packs.resolve_call("goal_report", {"status": "achieved"})
    assert (canon, err) == ("goal_report", None)

    # 委派：target 决定子代理 / 团队成员
    canon, args, err = packs.resolve_call(
        "plan", {"op": "delegate", "goal": "g"})
    assert (canon, err) == ("call_subagent", None)
    assert "target" not in args
    canon, args, err = packs.resolve_call(
        "plan", {"op": "delegate", "target": "team", "member_name": "m", "goal": "g"})
    assert (canon, err) == ("call_team", None)


def test_tool_own_action_param_survives():
    # team_manage 自己就有 action 参数（包不再覆盖它）
    canon, args, err = packs.resolve_call(
        "plan", {"op": "team_admin", "action": "list"})
    assert (canon, err) == ("team_manage", None)
    assert args == {"action": "list"}


# ── 端到端：分发器真的执行 ──────────────────────────────────────────────

def test_dispatch_executes_pack_call():
    from services.agent_zero.runtime.zero.dispatch import ToolDispatcher

    loop = ToolDispatcher()
    task = {"id": "t1", "workspace_id": "", "camp": "default", "submitter": "u"}
    envelope = {"tools": ["time"]}
    config = {"zero": {"envelope": envelope}}
    node = {"id": "n1", "props": {"envelope": envelope}}

    result = asyncio.run(loop._dispatch(
        task, config, node, {"name": "net", "arguments": {"op": "time"}}))
    assert isinstance(result, dict) and result.get("now"), result


def test_dispatch_pack_error_is_returned_to_model():
    from services.agent_zero.runtime.zero.dispatch import ToolDispatcher

    loop = ToolDispatcher()
    task = {"id": "t1", "workspace_id": "", "camp": "default", "submitter": "u"}
    envelope = {"tools": ["time"]}
    result = asyncio.run(loop._dispatch(
        task, {"zero": {"envelope": envelope}}, {"id": "n1"},
        {"name": "net", "arguments": {}}))
    assert "op" in str(result.get("error") or "")


# ── 设置 → 运行时：展示的是包 + 独立工具，不是一长串工具 ────────────────

def test_frontend_has_labels_for_every_pack():
    """设置页必须覆盖后端所有工具包的显示名。"""
    import re
    from pathlib import Path

    src = Path("frontend/src/views/SettingsView.vue").read_text(encoding="utf-8")
    block = re.search(r"const PACK_LABELS[^=]*= \{(.*?)\n\}", src, re.S)
    assert block, "找不到 PACK_LABELS"
    labels = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
    for pack in packs.PACKS:
        assert pack.code in labels, f"PACK_LABELS 缺少 {pack.code}"
    assert not ({"code", "agent"} & labels), f"残留包：{sorted({'code','agent'} & labels)}"


def test_tool_names_show_packs(monkeypatch):
    from services.agent_zero.routes import app_routes

    monkeypatch.setattr(packs, "enabled", lambda: True)
    names = app_routes._tool_names(has_working_dir=True)
    assert "fs" in names and "net" in names and "plan" in names and "dev" in names
    assert "read_file" not in names
    assert "code" not in names             # code 包已拆除
    # 独立工具只剩 shell / workspace / ask_user
    assert {"shell", "workspace", "ask_user"} <= set(names)
    assert not ({"tool_job", "vcs", "codegraph", "report_plan"} & set(names))

    chat_only = app_routes._tool_names(has_working_dir=False)
    assert "fs" in chat_only and "shell" in chat_only      # 纯聊天工作区照常可用
    assert "workspace" not in chat_only                   # 只有中间存储不下发
    assert "net" in chat_only and "memory" in chat_only


# ── 重构收尾：死名字与会话常量必须和 registry 对得上 ────────────────────
#
# 2026-10-30 整理时发现的真实缺陷：`_SEQ_CALLS` 只认老名字，`plan_manage` /
# `goal_manage` / `team_manage` / `call_team` 全漏了。
# 后果是模型把「其中一个 + 一个普通工具」放同一轮时，那一个会先被并发批送进
# 分发器（无实现，白跑），再由专有分支重跑。这组测试把两边的名单钉在一起。

def _chat_seq_names():
    from services.agent_zero.task_layer import conversation

    return {registry.canonical(n) for n in conversation._SEQ_CALLS}


def test_model_hidden_tools_all_exist():
    """合并掉的旧入口（MODEL_HIDDEN）必须真的还在 registry 里，不能是死名字。"""
    known = set(registry.known_codes())
    unknown = set(registry.MODEL_HIDDEN) - known
    assert not unknown, f"MODEL_HIDDEN 有已下线的工具：{sorted(unknown)}"
    assert set(packs.MERGED_AWAY_TOOLS) == set(registry.MODEL_HIDDEN)
    # 合并后的入口本身要在册
    assert {"todo_write", "goal_manage", "delegate"} <= known
    # 计划流程图已下线：registry 里不能再有执行计划工具
    assert not ({"report_plan", "update_plan", "plan_manage"} & known)


def test_inline_tools_have_no_dead_names():
    """INLINE_TOOLS 同理：里面挂不存在的 code 会让覆盖面测试悄悄失灵。

    注意内联工具**本来就不在 registry 里**（propose_task 由对话层定义并直接处理），
    所以不能要求它 ⊆ known_codes；要钉的是「不能有已下线的名字」。
    """
    dead = {"report_done", "report_failed", "escalate"}
    assert not (packs.INLINE_TOOLS & dead), sorted(packs.INLINE_TOOLS & dead)
    assert "propose_task" in packs.INLINE_TOOLS
    # 内联工具若同时出现在 registry 里，_tool_allowed 会把它误判成不可用
    assert not (set(packs.INLINE_TOOLS) - {"propose_task"}) & set(registry.known_codes())


def test_chat_seq_calls_cover_every_own_branch():
    """对话循环里由专有分支处理的工具，必须全在 `_SEQ_CALLS` 里。

    这些分支不走分发器：若漏进并发批，就会白跑一次、再被重跑一遍。
    """
    own_branch = {
        "propose_task", "call_subagent",
        "call_team", "team_manage", "department_manage", "role_manage",
        "todo_write", "goal_report", "goal_manage",
    }
    missing = own_branch - _chat_seq_names()
    assert not missing, f"_SEQ_CALLS 漏了专有分支工具：{sorted(missing)}"


def test_chat_seq_calls_names_are_resolvable():
    """`_SEQ_CALLS` 在 resolve_call **之后**判定，所以每个成员都必须是
    可归一的真实工具名（历史别名也要能归一），不能是凭空写的字符串。"""
    known = set(registry.known_codes())
    from services.agent_zero.task_layer import conversation

    # 「当前工具」必须都能归一（propose_task / background_task 由对话层定义，
    # 不在 registry）
    bad = {n for n in conversation._SEQ_CALLS_TOOLS
           if n not in ("propose_task", "background_task", "role_discussion",
                        "proactive_send")
           and registry.canonical(n) not in known}
    assert not bad, f"_SEQ_CALLS_TOOLS 有无法归一的工具名：{sorted(bad)}"
    # 历史兼容名单里除 todo（→ todo_write 别名）外都是已下线的名字
    assert registry.canonical("todo") == "todo_write"
    assert not (conversation._SEQ_CALLS_LEGACY & known), (
        "历史兼容名单里出现了仍在 registry 的工具，应该移到 _SEQ_CALLS_TOOLS")

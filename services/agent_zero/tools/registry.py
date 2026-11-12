"""标准工具注册表（本地单用户版本）。

只保留文件 / 脚本 / 网页 / 记忆 / 计划这些常用能力。
`envelope.tools` 是白名单；`gated=False` 的工具恒开。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    code: str
    aliases: tuple = ()
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    gated: bool = True
    # 危险等级（权限系统的基础）：low 永远不询问；medium/high 按档位决定
    risk: str = "low"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.code,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


TOOL_DEFS: List[ToolDef] = [
    ToolDef(
        "shell", (),
        name="Shell 命令执行",
        description=(
            "在沙箱中执行命令。macOS/Linux 用 bash -c；Windows 用 PowerShell"
            "（无 PowerShell 时退回 cmd /c）。命令按当前平台语法编写。"
            "单次 sleep 最长 120s（超过会截断并在结果里提示）；需要更长等待时，"
            "本次返回后再调用一次，或把长任务放后台用 tool_job 轮询。"
        ),
        parameters=_obj({"cmd": {"type": "string", "description": "要执行的命令"}}, ["cmd"]),
        # 基础风险为中：普通命令（ls/cat/grep）在 Auto 档位可直接执行；
        # SHELL_PATTERNS 命中（rm/sudo/网络/写文件…）时再升为高危。
        risk="medium",
    ),
    ToolDef(
        "read_file", ("read_workspace_file",),
        name="读文件",
        description=(
            "读取工作区内某个文件（只读）。返回带行号的内容，行号格式为「N: 」，"
            "冒号后的才是文件真实内容。文件较长时用 offset/limit 分段读——"
            "结果末尾会明确告诉你总行数与下一段从第几行开始。传目录则列出该目录。"
            "Office（docx / xlsx / pptx / odt / ods）与 PDF 会自动抽取纯文本后返回。"
        ),
        parameters=_obj({
            "path": {"type": "string", "description": "相对工作区的文件或目录路径"},
            "offset": {"type": "integer", "description": "起始行号（1 起，默认 1）"},
            "limit": {"type": "integer", "description": "最多读多少行（默认 2000）"},
        }, ["path"]),
    ),
    ToolDef(
        "read_office", ("read_document", "office_read", "read_pdf"),
        name="读取 Office / PDF 文档",
        description=(
            "读取工作区里的 Office 文档（docx / xlsx / xlsm / pptx / odt / ods，"
            "旧版 doc / xls / ppt 尽力抽取）与 PDF，返回纯文本。表格按「工作表名 + "
            "行」输出，幻灯片按 Slide 分段，PDF 按页分段；传 part 可只读某个工作表 / "
            "某页 / 某张幻灯片（名称或 1 起序号）。长文档用 offset/limit 分段。"
            "不能 edit_file / write_file 改这些格式——它们是二进制容器。"
        ),
        parameters=_obj({
            "path": {"type": "string", "description": "相对工作区的 Office / PDF 路径"},
            "part": {"type": "string",
                     "description": "只读某个工作表 / 幻灯片 / 页（名称或 1 起序号）"},
            "offset": {"type": "integer", "description": "起始行号（1 起，默认 1）"},
            "limit": {"type": "integer", "description": "最多读多少行（默认 2000）"},
        }, ["path"]),
    ),
    ToolDef(
        "edit_file", ("edit_workspace_file",),
        name="编辑文件",
        description=(
            "对工作区内某个文件做**精确字符串替换**（改局部首选它，比整体覆写安全）。"
            "old_string 必须与文件内容逐字符一致（空白、缩进、行尾都要对），"
            "且必须在文件中唯一——出现多处会报错，此时请补充上下文或改用 replace_all。"
            "不要把 read 输出里的行号前缀「N: 」包含进 old_string。"
        ),
        parameters=_obj({
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "old_string": {"type": "string", "description": "要被替换的原文（逐字符一致）"},
            "new_string": {"type": "string", "description": "替换成的新内容"},
            "replace_all": {"type": "boolean", "description": "是否替换所有匹配处（默认 false）"},
        }, ["path", "old_string", "new_string"]),
        risk="medium",
    ),
    ToolDef(
        "write_file", ("write_workspace_file",),
        name="写入文件",
        description="整体写入/覆盖工作区内的文件（新建或全量重写）；回执带 diff。",
        parameters=_obj({
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "content": {"type": "string", "description": "完整文件内容"},
        }, ["path", "content"]),
        risk="medium",
    ),
    ToolDef(
        "list_files", ("list_workspace_files",),
        name="列文件",
        description="列出工作区某目录下的文件与子目录（等价于 read_file 传目录）。",
        parameters=_obj({
            "path": {"type": "string", "description": "相对工作区的目录路径，默认根目录 ."},
        }, []),
    ),
    ToolDef(
        "workspace", ("studio_workspace", "store"),
        name="工作区中间存储",
        description="读写/列出工作区文件（中间结果落盘，大产物用此处而非上下文）。",
        parameters=_obj({
            "op": {"type": "string", "enum": ["read", "write", "list"],
                   "description": "操作类型"},
            "path": {"type": "string", "description": "工作区内相对路径"},
            "content": {"type": "string", "description": "op=write 时的文件内容"},
        }, ["op", "path"]),
        risk="medium",
    ),
    ToolDef(
        "vcs", ("git", "svn", "version_control"),
        name="版本控制（git / svn）",
        description=(
            "工作区版本控制（自动识别 git 或 svn）。op=status 查看未提交改动，"
            "log 查看提交历史，diff 查看差异（传 path 或 rev），branches 列出分支，"
            "switch 切换分支（create=true 可新建），commit 提交（paths 指定文件或 "
            "all=true 提交全部），stage/unstage 暂存，discard 丢弃改动。"
            "提交与分支切换属于危险操作，会请求用户批准。"
        ),
        parameters=_obj({
            "op": {"type": "string", "enum": ["status", "log", "diff", "branches",
                                             "switch", "commit", "stage", "unstage",
                                             "discard", "summary"],
                   "description": "操作类型"},
            "path": {"type": "string", "description": "单个文件相对路径"},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "多个文件相对路径"},
            "rev": {"type": "string", "description": "提交 / 修订号（diff 时指定）"},
            "branch": {"type": "string", "description": "switch 时的目标分支名或 URL"},
            "create": {"type": "boolean", "description": "switch 时新建分支（git）"},
            "message": {"type": "string", "description": "commit 的提交信息"},
            "all": {"type": "boolean", "description": "commit 时提交全部改动"},
            "limit": {"type": "integer", "description": "log 返回条数（默认 20）"},
        }, ["op"]),
        gated=False,
        risk="low",
    ),
    ToolDef(
        "memory_read", (),
        name="记忆检索",
        description=("按关键词检索本工作区沉淀的记忆（事实 / 决策 / 偏好 / 经验，含系统自动沉淀的"
                     "历史对话经验）。相关性 × 新鲜度 × 结构评分。"),
        parameters=_obj({
            "query": {"type": "string", "description": "检索关键词"},
            "top_k": {"type": "integer", "description": "返回条数（默认 5）"},
        }, ["query"]),
        gated=False,
    ),
    ToolDef(
        "memory_write", (),
        name="记忆写入",
        description=("写入跨对话长期有效的结论到工作区记忆：fact / decision / preference / experience。"
                    "只存可复用、稳定的内容（项目约定、关键决策、用户偏好、踩过的坑），不要存临时状态。"
                    "可传 entities（涉及的文件路径或符号名）：记忆挂接到代码本体，"
                    "按文件 / 符号检索时自动召回；正文里提及的文件 / 符号也会被自动识别挂接。"),
        parameters=_obj({
            "content": {"type": "string", "description": "记忆内容（最多 4000 字符）"},
            "kind": {"type": "string", "enum": ["fact", "decision", "preference", "experience"],
                     "description": "记忆类型（默认 fact）"},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "可选标签，利于后续检索"},
            "entities": {"type": "array", "items": {"type": "string"},
                        "description": "涉及的文件路径或符号名（挂接到代码本体）"},
        }, ["content"]),
    ),
    ToolDef(
        "memory_forget", (),
        name="记忆删除",
        description="删除一条已沉淀的记忆（按 memory_read 返回的 id）。过时 / 错误的记忆应及时清理。",
        parameters=_obj({
            "node_id": {"type": "string", "description": "记忆节点 id"},
        }, ["node_id"]),
        gated=False,
    ),
    ToolDef(
        "codegraph", ("code_graph", "project_map", "repomap", "code_map"),
        name="代码图谱",
        description=(
            "优先使用的代码索引工具（只读、低 token）。自动维护工作区代码图谱"
            "（文件 → 符号 → 依赖 → 反向引用），先用它定位再读最小行段，避免盲读整个仓库。"
            "op=summary 返回紧凑地图；find 按符号 / 路径 / import 定位文件与行号；"
            "deps 查某文件的直接依赖和反向依赖；impact 查改动影响面；status 查看索引状态；"
            "refresh 强制增量重建。首次调用会自动初始化，之后自动更新。"
        ),
        parameters=_obj({
            "op": {"type": "string",
                   "enum": ["summary", "find", "deps", "impact", "status", "refresh"],
                   "description": "操作（默认 summary）"},
            "query": {"type": "string",
                      "description": "find 的符号 / 路径 / import 关键词；deps / impact 的目标文件或模块"},
            "path": {"type": "string", "description": "deps / impact 的目标文件（可替代 query）"},
            "limit": {"type": "integer", "description": "find 返回条数，默认 10，最大 30"},
        }, []),
        gated=False,
    ),
    ToolDef(
        "time", ("query_time",),
        name="当前时间查询",
        description="查询当前服务器时间。",
        parameters=_obj({}, []),
        gated=False,
    ),
    ToolDef(
        "calculate", (),
        name="算术表达式计算",
        description="安全计算四则/幂/取余算术表达式（如 (1+2)*3**2/4）。",
        parameters=_obj({"expr": {"type": "string", "description": "算术表达式"}}, ["expr"]),
        gated=False,
    ),
    ToolDef(
        "web", ("web_info",),
        name="网页抓取",
        description="抓取 http(s) 网页正文（网络策略由能力包络 network 控制）。",
        parameters=_obj({
            "url": {"type": "string", "description": "http(s) URL"},
        }, ["url"]),
    ),
    ToolDef(
        "todo_write", ("todo", "task_list", "todo_list"),
        name="任务清单",
        description=(
            "写入本对话的任务清单（**整份覆盖**）：传入的 todos 就是新的完整清单。\n"
            "- 每条 {content, status}；status 只能是 pending / in_progress / completed；\n"
            "- 同一时刻最多一条 in_progress：开始做哪条就把它标成 in_progress；\n"
            "- 每次调用都要传**完整清单**，不是只传改动的那几条；\n"
            "- 传空列表 = 清空清单；\n"
            "- 多步任务开始时先列清单，完成一步就更新状态，全部完成后逐条置为 completed。\n"
            "清单跨运行持久，用户在界面里也能直接勾选，模型与用户改的是同一份。"
        ),
        parameters=_obj({
            "todos": {
                "type": "array",
                "description": "完整任务清单（整份覆盖）",
                "items": _obj({
                    "content": {"type": "string", "description": "任务内容（简短、可执行）"},
                    "status": {"type": "string",
                               "enum": ["pending", "in_progress", "completed"],
                               "description": "状态：pending / in_progress / completed"},
                }, ["content", "status"]),
            },
        }, ["todos"]),
        gated=False,
    ),
    ToolDef(
        "goal_report", (),
        name="目标模式进度汇报",
        description=(
            "汇报当前目标进展：status=progress 记录进展并继续；status=achieved 表示目标**已完成**"
            "（系统停止自动续跑）；status=blocked 表示卡住、需要用户决策（暂停自动续跑，"
            "用户回复或调用 goal_manage(resume) 后恢复）。可附带 progress_pct / next_steps / "
            "blockers / evidence，让进度与卡点更具体。目标模式下应定期调用；普通对话没有目标时会报错。"
        ),
        parameters=_obj({
            "status": {"type": "string", "enum": ["progress", "achieved", "blocked"],
                       "description": "进展（继续）、完成（停止续跑）或阻塞（等待用户）"},
            "note": {"type": "string", "description": "进展说明 / 完成结论 / 阻塞原因"},
            "progress_pct": {"type": "integer", "description": "完成度百分比（0–100，可选）"},
            "next_steps": {"type": "array", "items": {"type": "string"},
                           "description": "接下来准备做的步骤（可选）"},
            "blockers": {"type": "array", "items": {"type": "string"},
                         "description": "当前卡点（可选）"},
            "evidence": {"type": "string",
                         "description": "支撑结论的证据：测试结果 / 文件路径 / 关键输出（可选）"},
        }, ["status"]),
        gated=False,
    ),
    ToolDef(
        "goal_manage", ("goal_admin", "manage_goal"),
        name="目标",
        description=(
            "查看 / 推进 / 调整当前对话的目标（只在已有目标时有效）：\n"
            "- status：查看目标、状态、预算、已用 token 与最近进展；\n"
            "- report：汇报进展（等价于旧的 goal_report）。status=progress 记录进展并继续；"
            "status=achieved 表示目标已完成（停止自动续跑）；status=blocked 表示卡住、"
            "需要用户决策（暂停自动续跑）；可带 progress_pct / next_steps / blockers / evidence；\n"
            "- update：修改目标描述 objective、token 预算 token_budget、最大续跑次数 max_runs；\n"
            "- note：追加一条进展说明（等价于 report progress）；\n"
            "- blocked / resume：标记等待用户 / 解除等待继续自动推进；\n"
            "- close：结束目标（附原因，停止自动续跑）。\n"
            "不要用它创建新目标——目标由用户在界面选择「目标」模式时创建。"
        ),
        parameters=_obj({
            "action": {"type": "string",
                       "enum": ["report", "status", "update", "note", "blocked",
                                "resume", "close"],
                       "description": "操作（不传 = report，汇报进展）"},
            "status": {"type": "string", "enum": ["progress", "achieved", "blocked"],
                       "description": "report 时的进展：继续 / 完成 / 阻塞"},
            "objective": {"type": "string", "description": "update 时的新目标描述"},
            "token_budget": {"type": "integer",
                             "description": "update 时的 token 预算（0 = 不限）"},
            "max_runs": {"type": "integer",
                         "description": "update 时的最大自动续跑次数（0 = 不限）"},
            "note": {"type": "string",
                     "description": "说明 / 进展 / 完成结论 / 阻塞原因"},
            "progress_pct": {"type": "integer", "description": "完成度百分比（0–100，可选）"},
            "next_steps": {"type": "array", "items": {"type": "string"},
                           "description": "接下来准备做的步骤（可选）"},
            "blockers": {"type": "array", "items": {"type": "string"},
                         "description": "当前卡点（可选）"},
            "evidence": {"type": "string",
                         "description": "支撑结论的证据：测试结果 / 文件路径 / 关键输出（可选）"},
        }, []),
        gated=False,
    ),
    ToolDef(
        "delegate", (),
        name="委派子任务",
        description=(
            "把一个或多个明确的子任务交给子代理 / 团队成员执行，并拿回结论。\n"
            "- target=subagent（默认）：派生自身子代理（父角色的临时收窄副本）；\n"
            "- target=team：委派给本对话绑定的 Agent 团队成员（需先用 team_manage 建队并绑定），"
            "成员以其角色人格在受限子循环里执行；会创建一个群组后台任务，"
            "可在执行画布查看实时过程、暂停 / 继续 / 结束。\n"
            "- **并行**：target=subagent 时用 assignments 一次派生多个互不依赖的子代理，"
            "它们会同时执行并一起返回；target=team 时用 assignments 一次派发给多个成员。\n"
            "- **任务内子代理**：后台任务 / 团队成员执行单个任务时同样可以派子代理，"
            "用于在任务内部并行拆分；任务成员只派子代理，不要再嵌套派发团队任务。\n"
            "子代理拥有独立上下文，可以继续派生子代理（不设层数上限，注意别重复劳动）。"
        ),
        parameters=_obj({
            "target": {"type": "string", "enum": ["subagent", "team"],
                       "description": "委派对象（默认 subagent）"},
            "goal": {"type": "string",
                     "description": "要完成的子任务（明确、可独立收尾）"},
            "name": {"type": "string",
                     "description": "target=subagent 时的子代理名称（可选）"},
            "instructions": {"type": "string",
                             "description": "target=subagent 时的补充指令（可选）"},
            "context": {"type": "string",
                        "description": "可选：只分享给子代理的上下文。子代理拥有独立上下文，"
                                       "看不到父对话历史；需要它知道的信息请写在这里"},
            "tools": {"type": "array", "items": {"type": "string"},
                      "description": "可选：收窄本次委派的可用工具（须为父代理 / 成员已有工具的子集）"},
            "assignments": {
                "type": "array",
                "description": "批量并行派发：target=team 时每个成员一个子任务；"
                               "target=subagent 时每个条目派生一个子代理。"
                               "尽量一次派完，不要用多次单发代替。",
                "items": _obj({
                    "member_id": {"type": "string", "description": "target=team 时的成员 id"},
                    "member_name": {"type": "string", "description": "target=team 时的成员名称（替代 id）"},
                    "goal": {"type": "string", "description": "该成员 / 子代理要完成的子任务"},
                    "context": {"type": "string", "description": "只给该成员 / 子代理的上下文（可选）"},
                    "name": {"type": "string", "description": "target=subagent 时的子代理名称（可选）"},
                    "instructions": {"type": "string", "description": "target=subagent 时的补充指令（可选）"},
                }, ["goal"]),
            },
            "member_id": {"type": "string",
                          "description": "target=team 时的成员 id（系统提示团队名单中）"},
            "member_name": {"type": "string",
                            "description": "target=team 时的成员名称（替代 member_id）"},
        }, []),
        gated=False,
    ),
    ToolDef(
        "call_subagent", ("spawn_subagent",),
        name="派子代理执行子任务（旧名）",
        description=(
            "旧入口，等同 delegate(target=subagent)；保留用于历史消息与旧提示词。"
        ),
        parameters=_obj({
            "goal": {"type": "string",
                     "description": "子代理要完成的子任务（明确、可独立收尾）"},
            "name": {"type": "string", "description": "子代理名称（可选）"},
            "instructions": {"type": "string",
                             "description": "可选：给子代理的补充指令"},
            "context": {"type": "string",
                        "description": "可选：只分享给子代理的上下文（子代理不继承父对话历史）"},
            "tools": {"type": "array", "items": {"type": "string"},
                      "description": "可选：收窄子代理可用工具（须为父工具子集）"},
        }, ["goal"]),
        gated=False,
    ),
    ToolDef(
        "call_team", ("call_team_member",),
        name="委派团队成员执行子任务（旧名）",
        description=(
            "旧入口，等同 delegate(target=team)；保留用于历史消息与旧提示词。"
        ),
        parameters=_obj({
            "member_id": {"type": "string", "description": "团队成员 id（系统提示团队名单中）"},
            "member_name": {"type": "string", "description": "团队成员名称（替代 member_id）"},
            "goal": {"type": "string", "description": "该成员要完成的子任务（明确、可独立收尾）"},
            "context": {"type": "string",
                        "description": "可选：只分享给该成员的上下文（成员不继承父对话历史）"},
            "assignments": {
                "type": "array",
                "description": "批量并行派发：每个成员一个子任务。",
                "items": _obj({
                    "member_id": {"type": "string"},
                    "member_name": {"type": "string"},
                    "goal": {"type": "string"},
                    "context": {"type": "string"},
                }, ["goal"]),
            },
        }, []),
        gated=True,
    ),
    ToolDef(
        "team_manage", ("agent_team",),
        name="动态定义 Agent 团队",
        description=(
            "为当前工作区**动态**创建 / 更新 / 删除团队（= 群聊）、列出团队，"
            "或把团队绑定 / 解绑到当前对话。团队也可以由用户在界面的群资料里管理，"
            "两边改的是同一份数据。\n"
            "任务需要多学科协作（如实现 + 测试 + 前端）时：先用 team_manage(create) 建立"
            "「主持 + 若干专长成员」，新团队默认绑定当前对话；随后在 orchestrate 模式下用 "
            "`delegate(target=team)` 委派子任务，roundtable 模式下成员会各自应答。\n"
            "members 可带 tools 收窄该成员可用工具（不填 = 默认全集）；mode 可设 "
            "orchestrate / roundtable / single；max_speakers 控制圆桌最多几个角色应答。"
            "简单任务不需要团队。"
        ),
        parameters=_obj({
            "action": {"type": "string",
                       "enum": ["create", "update", "delete", "list", "bind", "unbind"],
                       "description": "操作（list = 列出当前工作区团队）"},
            "team_id": {"type": "string", "description": "团队 id（update / delete / bind / unbind 时必填）"},
            "name": {"type": "string", "description": "团队名称"},
            "description": {"type": "string", "description": "团队目标描述（可选）"},
            "leader": _obj({
                "id": {"type": "string",
                       "description": "已有角色 id（可选；优先复用角色库里的角色并保留头像 / 人格）"},
                "name": {"type": "string", "description": "主持（编排者）名称"},
                "expertise": {"type": "string", "description": "主持的专长（可选）"},
            }, ["name"]),
            "members": {
                "type": "array",
                "description": "成员列表（最多 12 人）。已有角色请带 id（从 role_manage list 获取）；"
                               "只给 name 时会复用角色库里同名持久角色，保留头像与人格",
                "items": _obj({
                    "id": {"type": "string",
                           "description": "已有角色 id（可选；优先复用并保留头像）"},
                    "name": {"type": "string", "description": "成员名称"},
                    "expertise": {"type": "string", "description": "该成员的专长（如后端 / 测试）"},
                    "tools": {"type": "array", "items": {"type": "string"},
                              "description": "可选：该成员可用的工具子集（不填 = 默认全集）"},
                    "model_code": {"type": "string",
                                   "description": "可选：该成员使用的模型档案 code"},
                }, ["name"]),
            },
            "mode": {"type": "string",
                     "enum": ["orchestrate", "roundtable", "single"],
                     "description": "协作模式：orchestrate（主持编排，默认）/ roundtable（多角色圆桌）/ single（单角）"},
            "max_speakers": {"type": "integer",
                             "description": "圆桌模式每条消息最多几个角色应答（1–12，默认 4）"},
            "bind": {"type": "boolean",
                     "description": ("create 时是否绑定到当前对话（默认 true）；"
                                     "全局 Agent 会话不参与绑定，群组建好后从「群组」页进入")},
        }, ["action"]),
        gated=False,
    ),
    ToolDef(
        "app_manage", (),
        name="全局 App 管理",
        description=(
            "工单助手 专用：跨会话查看 / 处理待授权、回答问题、标记已读、"
            "设置会话权限、设置全局默认权限、停止运行。\n"
            "只在工单助手 会话里下发；普通会话不会看到这个工具。\n"
            "action=status 返回当前待授权 / 未读 / 运行 / 会话授权的快照；"
            "approve 需要 approval_id + decision；answer 需要 question_id；"
            "set_permission 需要 conversation_id + mode；stop_run 需要 run_id 或 "
            "conversation_id。所有写操作会回传 app_update 事件，界面自动刷新。\n"
            "proactive_status / set_proactive / cancel_proactive 用于查看、调整和取消"
            "会话内主动消息（模型主动向用户发起），避免打扰过度。"
        ),
        parameters=_obj({
            "action": {
                "type": "string",
                "enum": ["status", "approvals", "questions", "approve", "answer",
                         "mark_read", "set_permission", "set_default_permission",
                         "stop_run", "proactive_status", "set_proactive",
                         "cancel_proactive"],
                "description": "操作",
            },
            "approval_id": {"type": "string", "description": "工具审批 id"},
            "decision": {
                "type": "string",
                "enum": ["approve_once", "approve_session", "approve_role",
                         "approve_team", "approve_always", "deny"],
                "description": "approve 的决策",
            },
            "question_id": {"type": "string", "description": "待回答问题 id"},
            "selected": {"type": "array", "items": {"type": "string"},
                         "description": "回答问题的选项"},
            "other": {"type": "string", "description": "自由回答内容"},
            "ignored": {"type": "boolean", "description": "跳过问题"},
            "conversation_id": {"type": "string", "description": "目标会话 id"},
            "mode": {"type": "string", "enum": ["auto", "once", "session", "always"],
                     "description": "权限档位"},
            "run_id": {"type": "string", "description": "要停止的 run id"},
            "all": {"type": "boolean",
                    "description": "mark_read 时是否处理全部未读"},
            "enabled": {"type": "boolean", "description": "set_proactive：是否允许模型主动发起消息"},
            "max_per_day": {"type": "integer", "description": "每个会话每天最多主动消息数"},
            "min_gap_minutes": {"type": "integer", "description": "同一会话两条主动消息的最小间隔（分钟）"},
            "quiet_start": {"type": "string", "description": "安静时段开始（HH:MM，空=不限制）"},
            "quiet_end": {"type": "string", "description": "安静时段结束（HH:MM）"},
            "task_id": {"type": "string", "description": "cancel_proactive 的任务 id"},
        }, ["action"]),
        risk="low",
        gated=False,
    ),
    ToolDef(
        "role_manage", ("role_admin",),
        name="角色库",
        description=(
            "管理角色库：列出 / 创建 / 修改 / 删除角色，或把临时角色转为持久角色。\n"
            "角色是独立人格；可绑定一个工作区作为文件 / 终端上下文，群组只是角色集合。"
            "会话内按需创建的角色默认"
            "为**临时角色**（只属于当前会话，会话删除即销毁），并自动加入当前群聊；"
            "需要跨会话复用时用 action=promote 存为持久角色。\n"
            "创建时给出 name（谁）/ title（岗位）/ department_id（部门，可空）/ "
            "workspace_id（绑定的工作区，可空）/ expertise / persona / tools（工具子集，不填=默认全集）。"
            "切换工作区用 action=update 并带上 workspace_id（空字符串 = 不绑定）。"
        ),
        parameters=_obj({
            "action": {"type": "string",
                       "enum": ["list", "create", "update", "promote", "delete"],
                       "description": "操作"},
            "role_id": {"type": "string", "description": "角色 id（update / promote / delete 必填）"},
            "name": {"type": "string", "description": "角色名称（谁）"},
            "title": {"type": "string", "description": "岗位（做什么），与名称分离"},
            "department_id": {"type": "string",
                              "description": "所属部门 id（可空 = 未安排）"},
            "workspace_id": {"type": "string",
                             "description": ("角色绑定的工作区 id（可空 = 无工作区）；"
                                             "切换工作区时更新该字段")},
            "expertise": {"type": "string", "description": "一句话专长"},
            "persona": {"type": "string", "description": "人格 / 工作方式"},
            "tools": {"type": "array", "items": {"type": "string"},
                      "description": "可选：该角色可用的工具子集（不填=默认全集）"},
            "model_code": {"type": "string", "description": "可选：角色使用的模型档案 code"},
            "status": {"type": "string", "enum": ["online", "offline"],
                       "description": "update 时设置角色在线或离线"},
            "scope": {"type": "string", "enum": ["temporary", "persistent"],
                      "description": "create 时的生命周期（默认 temporary）"},
            "add_to_team": {"type": "boolean",
                            "description": "create 时是否加入当前会话绑定的群聊（默认 true）"},
        }, ["action"]),
        gated=False,
    ),
    ToolDef(
        "schedule_manage", ("schedule_admin",),
        name="定时 / 主动任务",
        description=(
            "管理定时任务与自由触发任务。\n"
            "trigger_mode=scheduled：按 schedule 固定执行（once / interval / daily / weekly）；"
            "trigger_mode=smart：到评估时间后由模型结合 condition 判断是否主动执行（自由触发），"
            "适合「观察某件事，时机合适就推进」；trigger_mode=manual：只手动运行。\n"
            "schedule 示例：{at:'2026-11-12 09:00'} / {every_minutes:60} / "
            "{time:'09:00'} / {weekday:0,time:'09:00'}（weekday 0=周一）。\n"
            "任务会落到一条普通会话里执行，可指定 role_id / workspace_id；"
            "run_mode=autonomous 时按自主执行推进。"
        ),
        parameters=_obj({
            "action": {"type": "string",
                       "enum": ["list", "create", "update", "delete", "run", "pause", "resume"],
                       "description": "操作"},
            "task_id": {"type": "string", "description": "任务 id（update / delete / run / pause / resume 必填）"},
            "title": {"type": "string", "description": "任务标题"},
            "prompt": {"type": "string", "description": "每次触发时发给角色的任务内容"},
            "trigger_mode": {"type": "string",
                             "enum": ["scheduled", "smart", "manual"],
                             "description": "触发方式"},
            "schedule_kind": {"type": "string",
                              "enum": ["once", "interval", "daily", "weekly", "smart"],
                              "description": "时间规则；smart 表示按评估间隔自由触发"},
            "schedule": {"type": "object",
                         "description": "时间参数：at / every_minutes / time / weekday"},
            "condition": {"type": "string",
                          "description": "smart 模式的触发条件，例如「如果今天的日志里有新报错就排查」"},
            "role_id": {"type": "string", "description": "执行任务的角色 id"},
            "workspace_id": {"type": "string", "description": "执行任务的工作区 id"},
            "run_mode": {"type": "string", "enum": ["chat", "autonomous"],
                         "description": "会话模式：普通对话或自主执行"},
            "auto_execute": {"type": "boolean", "description": "自主执行时是否跳过确认"},
            "max_runs": {"type": "integer", "description": "最多运行次数（0=不限）"},
        }, ["action"]),
        gated=False,
    ),
    ToolDef(
        "department_manage", ("department_admin", "org_manage"),
        name="组织部门",
        description=(
            "管理组织里的部门：列出 / 新建 / 修改 / 移动 / 删除。部门支持多级，"
            "用 parent_id 指定上级（不填 = 根部门）。删除部门时子部门会上移到上一级，"
            "部门内角色变为「未安排」。\n"
            "角色归档到部门用 role_manage(update, department_id=...)；部门只影响组织"
            "管理与展示，不改变角色能力。"
        ),
        parameters=_obj({
            "action": {"type": "string",
                       "enum": ["list", "create", "update", "delete"],
                       "description": "操作"},
            "department_id": {"type": "string",
                              "description": "部门 id（update / delete 必填）"},
            "name": {"type": "string", "description": "部门名称"},
            "parent_id": {"type": "string",
                          "description": "上级部门 id；空 = 根部门"},
            "sort": {"type": "integer", "description": "同级排序（越小越靠前）"},
            "avatar": {"type": "string",
                       "description": "可选：头像 URL（先通过 /avatars 上传）"},
        }, ["action"]),
        gated=False,
    ),
    ToolDef(
        "list_skills", (),
        name="列出技能",
        description="列出本地技能库（名称、描述、是否启用）。",
        parameters=_obj({}, []),
        gated=False,
    ),
    ToolDef(
        "use_skill", ("read_skill",),
        name="读取技能",
        description=(
            "读取某个技能（SKILL.md）的完整内容：技能是一份可执行的操作指南，"
            "任务与它匹配时先读取，再按其中的步骤执行。"
        ),
        parameters=_obj({
            "name": {"type": "string", "description": "技能名称或 id"},
        }, ["name"]),
        gated=False,
    ),
    ToolDef(
        "create_skill", ("write_skill",),
        name="创建 / 更新技能",
        description=(
            "把一份可复用的操作指南写入技能库（SKILL.md）。同名技能会被更新。"
            "角色调用时创建的是**该角色专属技能**，不会共享给其他角色；"
            "可以先用文件 / 图片 / 网页搜索等方式收集材料，再把整理后的步骤写入 content。"
            "每个角色最多 20 个专属技能，达到上限后先用 merge_skills 合并。"
            "内容应包含适用场景、步骤、注意事项，可结合工作区里的脚本或示例文件。"
        ),
        parameters=_obj({
            "name": {"type": "string", "description": "技能名称（小写、简短）"},
            "description": {"type": "string", "description": "一句话说明这个技能做什么"},
            "content": {"type": "string", "description": "SKILL.md 正文（Markdown）"},
        }, ["name", "content"]),
        risk="medium",
        gated=False,
    ),
    ToolDef(
        "merge_skills", ("skill_merge",),
        name="合并专属技能",
        description=(
            "把当前角色的多个专属技能合并成一个，并删除来源技能。每个角色最多 20 个"
            "专属技能，达到上限后先用它合并。只能合并属于当前角色的技能。"
        ),
        parameters=_obj({
            "skill_ids": {"type": "array", "items": {"type": "string"},
                          "description": "要合并的技能 id（至少 2 个）"},
            "name": {"type": "string", "description": "合并后的技能名称"},
            "description": {"type": "string", "description": "合并后的技能描述"},
        }, ["skill_ids", "name"]),
        risk="medium",
        gated=False,
    ),
    ToolDef(
        "install_skill", (),
        name="安装技能（npm / npx skills）",
        description=(
            "安装技能：package 为 npm 包名（包内需包含 SKILL.md），或 source 为 "
            "Agent Skills 源（owner/repo 或 URL，走 `npx skills add <源>`，"
            "可用 skills 只装源中的部分技能）。会执行 npm/npx，属于危险操作，需要用户批准。"
        ),
        parameters=_obj({
            "package": {"type": "string", "description": "npm 包名，例如 skill-xyz"},
            "source": {
                "type": "string",
                "description": "npx skills 源，例如 vercel-labs/agent-skills 或 https://github.com/…",
            },
            "skills": {
                "type": "array", "items": {"type": "string"},
                "description": "只安装源里指定的技能名（可选，默认全部）",
            },
        }, []),
        risk="high",
        gated=False,
    ),
    ToolDef(
        "list_mcp", ("list_mcp_servers",),
        name="列出 MCP 服务",
        description="列出已接入的 MCP 服务及其工具数量与状态。",
        parameters=_obj({}, []),
        gated=False,
    ),
    ToolDef(
        "add_mcp_server", (),
        name="接入 MCP 服务",
        description=(
            "添加一个 MCP 服务：本地 stdio（command + args，或 npm package 自动安装）"
            "或远程 http/sse（url）。添加后其工具以 mcp__<id>__<tool> 形式可用。"
        ),
        parameters=_obj({
            "name": {"type": "string", "description": "服务显示名"},
            "type": {"type": "string", "enum": ["stdio", "http"],
                     "description": "stdio = 本地进程；http = 远程 HTTP/SSE 端点"},
            "package": {"type": "string", "description": "stdio：npm 包名（自动安装并用 npx 启动）"},
            "install_command": {"type": "string",
                                "description": "stdio：自定义安装命令（无 npm 时用绝对路径）"},
            "command": {"type": "string", "description": "stdio：可执行命令（优先级高于 package）"},
            "args": {"type": "array", "items": {"type": "string"},
                     "description": "stdio：命令参数"},
            "url": {"type": "string", "description": "http：MCP 端点 URL"},
        }, ["name"]),
        risk="medium",
        gated=False,
    ),
    ToolDef(
        "ask_user", ("ask", "question", "choose"),
        name="向用户提问",
        description=(
            "当需要用户做选择时使用：弹出选择框（可多选），用户也可以选「其他」"
            "手动输入，或直接忽略。适合方案取舍、范围确认、风格选择等；"
            "不要用它问开放式的长问题。工具会阻塞等待回答（超时/停止按忽略处理）。"
        ),
        parameters=_obj({
            "question": {"type": "string", "description": "要问用户的问题"},
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "选项文字"},
                        "description": {"type": "string", "description": "可选说明"},
                    },
                    "required": ["label"],
                },
                "description": "2–8 个选项；也可以传字符串数组",
            },
            "multi": {"type": "boolean", "description": "是否允许多选（默认 false）"},
            "allow_other": {"type": "boolean",
                            "description": "是否允许「其他」手动输入（默认 true）"},
        }, ["question"]),
        risk="low",
        gated=False,
    ),
    ToolDef(
        "save_analysis", ("write_analysis", "save_report", "write_report"),
        name="保存分析文档",
        description=(
            "把分析 / 调研 / 报告类文档保存到工作区。**未指定 path 时**自动落到 "
            "`.atlas/<kind>/<YYYY-MM-DD>/<HHMM>-<标题>.<ext>`，按类型和日期分目录，"
            "不要把这些文档散落在仓库根目录。普通代码/配置改动仍用 write_file。"
        ),
        parameters=_obj({
            "title": {"type": "string", "description": "文档标题（用于文件名）"},
            "content": {"type": "string", "description": "文档正文（Markdown / 文本 / HTML）"},
            "kind": {"type": "string",
                     "description": "类型：analysis（默认）/ report / research / summary / note / plan / review"},
            "ext": {"type": "string", "description": "扩展名：md（默认）/ txt / html / json / csv"},
            "path": {"type": "string",
                     "description": "可选：显式工作区相对路径；给了就完全按它写"},
        }, ["title", "content"]),
        risk="low",
        gated=False,
    ),
    ToolDef(
        "install_mcp", (),
        name="安装 MCP 服务（npm）",
        description=(
            "用 npm 安装一个 MCP 服务包并接入。执行 `npm install <包>`，属于危险操作，"
            "需要用户批准。安装后可用 list_mcp 查看其工具。"
        ),
        parameters=_obj({
            "package": {"type": "string", "description": "npm 包名，例如 @modelcontextprotocol/server-filesystem"},
            "command": {"type": "string",
                        "description": "自定义安装命令（可选，建议绝对路径；无 npm 时使用）"},
            "name": {"type": "string", "description": "可选：服务显示名"},
            "args": {"type": "array", "items": {"type": "string"},
                     "description": "可选：启动参数（如工作区绝对路径）"},
        }, ["package"]),
        risk="high",
        gated=False,
    ),
    ToolDef(
        "tool_job", ("tool_status", "job", "tool_jobs"),
        name="工具后台任务",
        description=(
            "监督**耗时工具的后台任务**。前台等待超过预算（默认 45s）的工具会被"
            "自动转到后台，并回一条 {\"status\":\"background\",\"job_id\":...} 结果——"
            "此时不要干等，先做别的事，再用本工具查它：\n"
            "- action=list：列出本对话的后台任务（含状态与耗时）；\n"
            "- action=status + job_id：只看一条；\n"
            "- action=result + job_id：读输出（未结束会提示仍在运行）；\n"
            "- action=wait + job_id + timeout_s：最多等 N 秒（上限 300）；\n"
            "- action=kill + job_id：终止（挂住的命令就用它，不要重跑整条命令）。\n"
            "工具跑完后系统会自动补一条 tool_result 事件，界面会就地更新。"
        ),
        parameters=_obj({
            "action": {"type": "string",
                       "description": "list / status / result / wait / kill（默认 list）"},
            "job_id": {"type": "string", "description": "后台任务 id（status/result/wait/kill 必填）"},
            "timeout_s": {"type": "integer",
                          "description": "wait 时最多等多少秒（默认 30，上限 300）"},
        }, ["action"]),
        gated=False,
    ),
]

_REGISTRY: Dict[str, ToolDef] = {d.code: d for d in TOOL_DEFS}
_ALIASES: Dict[str, str] = {}
for _d in TOOL_DEFS:
    for _a in _d.aliases:
        _ALIASES[_a] = _d.code


def canonical(name: str) -> str:
    n = (name or "").strip()
    return _ALIASES.get(n, n)


def known_codes() -> List[str]:
    return sorted(_REGISTRY.keys())


def list_tools() -> List[Dict[str, Any]]:
    return [
        {"code": d.code, "name": d.name, "description": d.description,
         "aliases": list(d.aliases), "gated": d.gated, "risk": d.risk}
        for d in TOOL_DEFS
    ]


def get_tool(code: str) -> Optional[ToolDef]:
    return _REGISTRY.get((code or "").strip())


# 已合并进新工具的旧入口：实现和别名继续保留（历史消息 / 旧提示词仍能执行），
# 但不再出现在模型工具清单里（pack 模式与非 pack 模式都不下发）。
#   goal_report → goal_manage（action=report）
#   call_subagent / call_team → delegate（target 选择子代理 / 团队成员）
MODEL_HIDDEN = frozenset({
    "goal_report", "call_subagent", "call_team",
})

# 纯聊天工作区（没有用户文件夹）里**唯一不下发**的工具：`workspace`。
#
# 它是「工作区中间存储」——语义就是读写用户选定文件夹里的文件（大产物落盘 / 中间
# 结果），纯聊天工作区没有这样的文件夹，留着只会让模型把结果写进托管目录还以为在
# 用户的项目里。其余工具（shell / 文件读写 / vcs / codegraph / tool_job）在纯聊天
# 工作区里都可用，落点是托管目录 `workspace_service.exec_root`。
WORKSPACE_REQUIRED_TOOLS = frozenset({"workspace"})
WORKSPACE_BOUND_TOOLS = frozenset({
    "shell", "workspace", "vcs", "codegraph", "tool_job", "save_analysis",
    "read_file", "read_office", "write_file", "edit_file", "list_files",
})


def build_tool_specs(envelope_tools: List[str], *,
                     has_working_dir: bool = True) -> List[Dict[str, Any]]:
    allowed = {canonical(t) for t in (envelope_tools or [])}
    out: List[Dict[str, Any]] = []
    for d in TOOL_DEFS:
        if d.code in MODEL_HIDDEN:
            continue
        if not has_working_dir and d.code in WORKSPACE_REQUIRED_TOOLS:
            continue
        if not d.gated or d.code in allowed:
            out.append(d.schema())
    return out

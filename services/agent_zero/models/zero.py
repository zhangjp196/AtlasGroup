"""AtlasX 核心 ORM 模型（sqlite 单库，v3 基线结构）。

V3 不再兼容旧库：每张表按本文件一次性建表（`Base.metadata.create_all`），
没有增量 `ALTER` 迁移。时间戳默认取本地时间，JSON 列走 SQLAlchemy 通用 JSON。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from services.agent_zero.database import Base


class Workspace(Base):
    """工作区：一个对话容器，可选一个本地文件夹（root_path 决定聊天/文件工作区）。

    kind 区分三种空间：group（普通/分组空间）、task（单任务空间）、role（角色空间）。
    member_role_ids / zones / origin / host_role_id 供分组空间的成员与视图使用。
    """

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="group", server_default=text("'group'")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    member_role_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    zones: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    origin: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    host_role_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('task','group','role')", name="ck_workspaces_kind"
        ),
    )


class Role(Base):
    """角色：一个可执行的人格—— persona / expertise / 能力包络 / 模型档案。

    工作区里的默认角色 id 是 `default`（由 ensure_defaults 管理）。
    group_id 是角色所属空间（历史命名，等价于其它表的 workspace_id）。
    """

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    group_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona: Mapped[str | None] = mapped_column(Text, nullable=True)
    expertise: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    envelope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 角色级模型档案（可空 = 跟随对话 / 全局默认）
    model_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 角色生命周期：persistent（跨会话）/ temporary（只属于 session_id 的会话）
    scope: Mapped[str] = mapped_column(
        Text, nullable=False, default="persistent", server_default=text("'persistent'")
    )
    # 来源：user（手动）/ llm（模型在对话里创建）
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="user", server_default=text("'user'")
    )
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object | None] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=True
    )


class AgentTeam(Base):
    """Agent 团队：主持 (leader) + 成员 + 协作模式 (mode) + 上限 (max_speakers)。

    团队一旦绑定到对话 (conversations.team_id)，该对话的运行就按 mode 执行：
    orchestrate（主持编排，委派给成员） / roundtable（多角色圆桌） / single（单角）。
    """

    __tablename__ = "agent_teams"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 协作模式：orchestrate（主持编排）/ roundtable（多角色圆桌）/ single（单角）
    mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="orchestrate",
        server_default=text("'orchestrate'"),
    )
    # 圆桌模式每条消息最多几个角色应答（1..12）
    max_speakers: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default=text("4")
    )
    leader_role_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    member_role_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "mode IN ('orchestrate','roundtable','single')", name="ck_agent_teams_mode"
        ),
    )


class Conversation(Base):
    """对话：所有消息以 GDAG fact 节点存放（props 里带 conversation_id）。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("workspaces.id"), nullable=False
    )
    # 会话级工作区绑定（可空 = 纯聊天）：工作区只是文件上下文，不再决定会话归属
    bound_workspace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 空间分区（历史字段）：普通对话为空，群组/角色空间按 zone 分区展示。
    zone: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="chat", server_default=text("'chat'")
    )
    # 会话级权限档位（once/auto/session/always），新对话继承上一条对话的档位
    permission_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="once", server_default=text("'once'")
    )
    # 会话运行模式（chat/autonomous，界面显示 chat/agent），默认 agent
    run_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="autonomous", server_default=text("'autonomous'")
    )
    # 会话级模型 / 思考强度：空 = 跟随全局默认；切换后按会话记住
    model_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    thinking_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 标题来源："" / fallback（首条消息截断）/ auto（AI 生成）/ user（手动重命名）
    title_source: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    # 自动归档：超过 ARCHIVE_AFTER_DAYS 天没有活动的对话 archived=1（列表默认隐藏）
    archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # 已读水位：上次打开会话的时间；之后到达的助手消息算未读（打开会话时更新）。
    last_read_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    # 绑定的 Agent 团队（可空）
    team_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 会话分支来源：从哪条会话 / 哪条消息 fork 出来（普通会话为空）
    forked_from_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    forked_from_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )


class ConversationRun(Base):
    """一次后台对话运行（关浏览器继续，重连按游标续拉）。

    mode = chat（用户对话） / autonomous（自动运行）。
    """

    __tablename__ = "conversation_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="chat", server_default=text("'chat'")
    )
    auto_execute: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    mentions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 队列序号（越小越先执行）。同一对话的排队 run 在「上移 / 下移」时整体重写为
    # 0..n-1；从未调整过时全为 0，此时排序退化为 created_at 升序 = FIFO。
    # 历史上这里是 priority（0/1 表示是否插队），插队按钮已移除，改由本列排序。
    queue_pos: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # 排队消息对应的 GDAG 消息节点（消息已落转录，但尚未执行）
    queued_msg_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 本轮选择的思考级别（reasoning effort）；空 = 使用模型默认
    thinking_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # 静默停止：由「立即插入（停止并继续）」置位。收尾时不写 interrupted 事件、
    # 不给助手消息打 interrupted 标记，界面因此没有「已中断」红条与停止提示——
    # 用户看到的只是「这轮停下，接着跑新消息」。
    stop_silent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )
    started_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','waiting','done','failed','stopped')",
            name="ck_conversation_runs_status",
        ),
    )


class ConversationRunEvent(Base):
    """运行事件（可重放；seq 递增，SSE 按 since 续拉）。"""

    __tablename__ = "conversation_run_events"

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("conversation_runs.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )


class GdagNode(Base):
    """GDAG 节点（对话消息 / 记忆 / 计划等统一存放）。"""

    __tablename__ = "gdag_nodes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    camp: Mapped[str] = mapped_column(Text, nullable=False, default="default")
    props: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('entity','fact','relation','task','step',"
            "'artifact','experience','tool_call')",
            name="ck_gdag_nodes_type",
        ),
    )


class GdagEdge(Base):
    __tablename__ = "gdag_edges"

    from_id: Mapped[str] = mapped_column(
        Text, ForeignKey("gdag_nodes.id"), primary_key=True
    )
    to_id: Mapped[str] = mapped_column(
        Text, ForeignKey("gdag_nodes.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    exec_state: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "exec_state IN ('pending','running','done','failed') OR exec_state IS NULL",
            name="ck_gdag_edges_exec_state",
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )


class AppSetting(Base):
    """本地设置（LLM 覆盖等）；sqlite 是唯一数据源。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), onupdate=text("datetime('now','localtime')"), nullable=False
    )


class ToolApproval(Base):
    """危险操作的一次审批（单次 / 会话 / 自动判断 / 完全允许）。

    审批只在**工具调用**层面：状态 pending 的审批由前端按钮决定，worker 侧
    等待同一个 id 的决策（进程内事件 + 数据库兜底）。
    """

    __tablename__ = "tool_approvals"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk: Mapped[str] = mapped_column(Text, nullable=False, default="medium")
    reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )
    decided_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','denied','expired')",
            name="ck_tool_approvals_status",
        ),
    )


class PermissionGrant(Base):
    """「会话允许」的落库记录：同一对话内该工具后续自动放行。"""

    __tablename__ = "permission_grants"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )


class TodoItem(Base):
    """对话任务清单：用户或模型维护的待办条目。

    任务清单是跨 run 的持久清单：模型用 `todo_write` 整份覆盖写入（DSH 口径），
    用户在对话内卡片 / 浮动面板里勾选，两方改的是同一份。
    """

    __tablename__ = "todo_items"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="open", server_default=text("'open'")
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="user", server_default=text("'user'")
    )
    # 清单顺序（模型写入的顺序）：卡片 / 面板都按它展示，不按状态重排。
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','doing','done')",
            name="ck_todo_items_status",
        ),
    )


class Goal(Base):
    """目标模式：对话绑定的长程目标，run 结束后自动续跑直到完成 / 预算耗尽。

    token_budget = 0 表示不限；max_runs = 0 表示自动续跑次数不限。
    """

    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default=text("'active'")
    )
    token_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_runs: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    runs_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','achieved','closed','exhausted')",
            name="ck_goals_status",
        ),
    )


class Skill(Base):
    """技能（SKILL.md + 附属文件）：索引在 sqlite，内容在 data/agent_zero/skills。"""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    created_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime, server_default=text("datetime('now','localtime')"), nullable=False
    )


ENSURE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_gdag_camp ON gdag_nodes(camp)",
    "CREATE INDEX IF NOT EXISTS idx_gdag_type ON gdag_nodes(type, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_gdag_created ON gdag_nodes(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_gdag_by ON gdag_nodes"
    "((json_extract(props, '$.by')))",
    "CREATE INDEX IF NOT EXISTS idx_gdag_conv ON gdag_nodes"
    "((json_extract(props, '$.conversation_id')))",
    "CREATE INDEX IF NOT EXISTS idx_gdag_conv_stage ON gdag_nodes"
    "((json_extract(props, '$.conversation_id')), (json_extract(props, '$.stage')))",
    "CREATE INDEX IF NOT EXISTS idx_gdag_edges_to ON gdag_edges(to_id)",
    "CREATE INDEX IF NOT EXISTS idx_conv_runs_conv ON conversation_runs(conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_conv_runs_status ON conversation_runs(status)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_archived ON conversations(workspace_id, archived, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_workspaces_archived ON workspaces(archived)",
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action, ts)",
    "CREATE INDEX IF NOT EXISTS idx_approvals_conv ON tool_approvals(conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_grants_conv ON permission_grants(conversation_id, tool)",
    "CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name)",
    "CREATE INDEX IF NOT EXISTS idx_teams_ws ON agent_teams(workspace_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_todos_conv ON todo_items(conversation_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_goals_conv ON goals(conversation_id, created_at)",
)

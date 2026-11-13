export interface ListResponse<T> {
  items: T[]
  total?: number
}

/** token 用量：会话 / 工单 / 任务 / 单条回复统一用这个口径。 */
export interface TokenUsage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  cached_tokens?: number
  cache_rate?: number
  llm_ms?: number
  /** 生成速度（输出 token / 秒） */
  speed?: number
  /** 历史字段别名：全局用量统计里叫 tokens_per_second */
  tokens_per_second?: number
  rounds?: number
  turns?: number
  avg_tokens_per_turn?: number
  last_prompt_tokens?: number
  max_context_tokens?: number
  [key: string]: unknown
}

export interface Workspace {
  id: string
  name: string
  kind?: string
  root_path: string
  root_exists?: boolean
  chat_only?: boolean
  managed?: boolean
  archived?: boolean
  conversation_count?: number
  created_at?: string
  last_conv_updated?: string
  recent_conversations?: Array<{ id: string; title: string; updated_at: string; active?: boolean }>
  vcs?: { type?: string | null; error?: string | null } | null
}

export interface Conversation {
  id: string
  title?: string
  workspace_id?: string
  bound_workspace_id?: string
  team_id?: string
  role_id?: string
  model_code?: string
  thinking_level?: string
  permission_mode?: string
  run_mode?: string
  zone?: string
  /** 栏目：所属会话根 id（根会话也会归一成自己的 id）。 */
  session_id?: string
  session_root_id?: string
  /** 整棵会话（根 + 栏目）里的栏目数。 */
  thread_count?: number
  kind?: string
  active?: boolean
  unread?: boolean
  unread_count?: number
  archived?: boolean
  /** 分支来源：从哪条会话 / 哪条消息分出来的。 */
  forked_from_id?: string
  forked_from_message_id?: string
  title_source?: string
  running_count?: number
  usage?: TokenUsage
  usage_total?: TokenUsage
  last_read_at?: string
  last_message?: string
  message_count?: number
  run_status?: string
  has_active_goal?: boolean
  created_at?: string
  updated_at?: string
  messages?: Message[]
}

export interface Attachment {
  id?: string
  name?: string
  mime?: string
  size?: number
  kind?: string
  url?: string
  local?: boolean
  preview?: string
}

export interface TraceItem {
  kind: 'thinking' | 'tool' | 'text' | 'retry' | 'compress' | string
  text?: string
  id?: string
  name?: string
  status?: string
  ok?: boolean
  duration_ms?: number
  phase?: string
  args?: unknown
  display?: { diff?: string; [key: string]: unknown }
  ts?: number
  round?: number
  final?: boolean
}

export interface Message {
  id?: string
  by?: string
  display_by?: string
  kind?: string
  content?: string
  reasoning?: string
  trace?: TraceItem[]
  tool_trace?: TraceItem[]
  role_name?: string
  role_id?: string
  role_title?: string
  role_department?: string
  role_avatar?: string
  task_id?: string
  task_title?: string
  task_status?: string
  task_continued?: boolean
  summary?: string
  attachments?: Attachment[]
  duration_ms?: number
  usage?: TokenUsage
  error?: string
  interrupted?: boolean
  created_at?: string
}

export interface Role {
  id: string
  name: string
  workspace_id?: string
  title?: string
  department_id?: string
  department?: string
  persona?: string
  expertise?: string
  avatar?: string
  tools?: string[]
  model_code?: string
  chat_model_code?: string
  scope?: 'persistent' | 'temporary'
  source?: string
  session_id?: string
  created_at?: string
  system?: boolean
  status?: 'online' | 'offline'
}

export interface TeamMember {
  id?: string
  name?: string
  title?: string
  department?: string
  expertise?: string
  avatar?: string
  model_code?: string
  tools?: string[]
}

export interface Team {
  id: string
  /** group = 用户可管理的群组；participants = 会话私有参与角色 */
  kind?: 'group' | 'participants' | string
  name: string
  description?: string
  avatar?: string
  mode?: 'orchestrate' | 'roundtable' | 'single' | string
  max_speakers?: number
  workspace_id?: string
  leader?: TeamMember | null
  members?: TeamMember[]
  created_at?: string
}

export interface Department {
  id: string
  name: string
  parent_id?: string
  avatar?: string
  depth?: number
  path?: string
}

export interface ModelProfile {
  code: string
  name?: string
  model?: string
  base_url?: string
  api_key?: string
  effective_api_key?: string
  protocol?: string
  vision?: boolean
  thinking_levels?: string[]
  context_window?: number
  max_tokens?: number
  /** 同一模型档案允许同时在途的 LLM 请求数（默认 2） */
  concurrency?: number
  effective_concurrency?: number
  subscription_id?: string
  channel_id?: string
  channel_name?: string
  configured?: boolean
  [key: string]: unknown
}

export interface Subscription {
  id: string
  name?: string
  provider?: string
  protocol?: string
  base_url?: string
  anthropic_base_url?: string
  api_key?: string
  has_api_key?: boolean
  models_url?: string
  note?: string
  [key: string]: unknown
}

export interface PlatformPreset {
  code: string
  name: string
  group?: string
  provider?: string
  base_url?: string
  anthropic_base_url?: string
  models_url?: string
  protocol?: string
  note?: string
  requires_gateway?: boolean
}

export interface ModelsResponse {
  items: ModelProfile[]
  active: string
  subscriptions: Subscription[]
}

export interface AppInfo {
  name: string
  version: string
  mode?: string
  port?: number
  url?: string
  config_path?: string
  db_path?: string
  llm?: {
    models?: ModelProfile[]
    subscriptions?: Subscription[]
    active_model?: string
    base_url?: string
    model?: string
    configured?: boolean
    protocol?: string
    thinking_levels?: string[]
  }
  limits?: {
    max_concurrent_runs?: number
    max_parallel_member_runs?: number
    context_compress_percent?: number
    autonomous_run_max_seconds?: number
  }
  tools?: string[]
  tool_catalog?: ToolCatalog
}

export interface ToolPackOp {
  op?: string
  tool?: string
  risk?: string
  workspace_required?: boolean
  available?: boolean
  conditional?: string
}

export interface ToolPack {
  code?: string
  available?: boolean
  ops?: ToolPackOp[]
}

export interface ToolCatalog {
  packs?: ToolPack[]
  standalone?: Array<{ code?: string; risk?: string; workspace_required?: boolean; available?: boolean }>
  chat_only?: boolean
}

export interface ToolItem {
  code?: string
  name?: string
  description?: string
  risk?: string
  gated?: boolean
  aliases?: string[]
  [key: string]: unknown
}

export interface Approval {
  id: string
  tool?: string
  name?: string
  risk?: 'low' | 'medium' | 'high' | string
  note?: string
  status?: string
  role_id?: string
  role_name?: string
  team_id?: string
  team_name?: string
  can_grant_role?: boolean
  can_grant_team?: boolean
}

export interface Question {
  id: string
  question?: string
  options?: Array<string | { label?: string; value?: string }>
}

export interface WorkItem {
  id: string
  title?: string
  body?: string
  status?: string
  priority?: number
  source?: string
  source_ref?: string
  source_actor?: string
  workspace_id?: string
  workspace_name?: string
  role_id?: string
  role_name?: string
  role_title?: string
  role_department?: string
  team_id?: string
  team_name?: string
  conversation_id?: string
  attention_reason?: string
  result_summary?: string
  run_id?: string
  usage?: TokenUsage
  finished_at?: string
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export interface ScheduledTask {
  id: string
  title: string
  prompt: string
  workspace_id?: string
  role_id?: string
  conversation_id?: string
  trigger_mode: 'scheduled' | 'smart' | 'manual' | string
  schedule_kind: 'once' | 'interval' | 'daily' | 'weekly' | 'smart' | string
  schedule: Record<string, unknown>
  condition?: string
  status: 'active' | 'paused' | 'done' | string
  run_mode?: string
  auto_execute?: boolean
  max_runs?: number
  run_count?: number
  last_run_at?: string
  last_status?: string
  last_run_id?: string
  last_conversation_id?: string
  last_result?: string
  next_run_at?: string
  meta?: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

export interface ScheduleListResponse {
  items: ScheduledTask[]
  stats?: { total?: number; active?: number; smart?: number; paused?: number }
}

export interface AgentTool {
  id?: string
  name?: string
  status?: string
  ok?: boolean
  summary?: string
}

export interface AgentNode {
  id: string
  parent_id?: string | null
  kind?: string
  name?: string
  role_id?: string
  avatar?: string
  goal?: string
  status?: string
  current?: string
  text?: string
  error?: string
  tools?: AgentTool[]
  elapsed_ms?: number
  started_at?: string
  ended_at?: string
  run_id?: string
  mode?: string
}

export interface AgentActivity {
  ts?: number
  agent?: string
  agent_id?: string
  kind?: string
  status?: string
  text?: string
}

export interface AgentGraph {
  conversation?: { id?: string; title?: string }
  run?: { id?: string; status?: string } | null
  root?: AgentNode
  nodes?: AgentNode[]
  edges?: Array<{ from: string; to: string; kind?: string }>
  activity?: AgentActivity[]
  upcoming?: Array<{ id?: string; status?: string; created_at?: string; content?: string; kind?: string; role_id?: string; role_name?: string }>
  stats?: {
    agents?: number
    tools?: number
    tokens?: number
    runs_today?: number
    done_today?: number
    running?: number
    done?: number
    failed?: number
    queued?: number
    parallel?: number
    member_runs?: number
  }
}

export interface RunRecord {
  id: string
  conversation_id?: string
  role_id?: string
  target_role_id?: string
  team_task_id?: string
  status?: string
  mode?: string
  content?: string
  error?: string
  created_at?: string
  started_at?: string
  finished_at?: string
}

export interface TeamTaskMember {
  role_id?: string
  role_name?: string
  goal?: string
  context?: string
  status?: string
  run_id?: string
  run_status?: string
  error?: string
  usage?: TokenUsage
  started_at?: string
  finished_at?: string
}

export interface TeamTask {
  id: string
  conversation_id?: string
  team_id?: string
  leader_role_id?: string
  leader_name?: string
  title?: string
  goal?: string
  status?: string
  members?: TeamTaskMember[]
  usage?: TokenUsage
  active_runs?: number
  done_members?: number
  total_members?: number
  created_at?: string
  updated_at?: string
  continued_at?: string
  can_pause?: boolean
  can_resume?: boolean
  can_stop?: boolean
  can_continue?: boolean
}

export interface TeamTaskListResponse {
  items: TeamTask[]
  running?: number
}

export interface McpServer {
  id: string
  name: string
  type: string
  package?: string
  command?: string
  args?: string[]
  url?: string
  install_command?: string
  env?: Record<string, string>
  headers?: Record<string, string>
  enabled?: boolean
  installed?: boolean
  /** 常驻进程：有页面 / 会话状态的服务 */
  persistent?: boolean
  /** 只注入模型的工具名（空 = 全部） */
  tool_allowlist?: string[]
  note?: string
  tools?: number
  tool_names?: string[]
}

export interface Skill {
  id: string
  name: string
  description?: string
  content?: string
  role_id?: string | null
  enabled?: boolean
  builtin?: boolean
  created_at?: string
}

export interface HookSummary {
  event?: string
  command?: string
  matcher?: string
  timeout?: number
  [key: string]: unknown
}

export interface SlashCommand {
  name: string
  description?: string
  argument_hint?: string
  source?: string
  builtin?: boolean
  action?: string
}

export interface WorkItemsResponse {
  items: WorkItem[]
  stats?: {
    open?: number
    attention?: number
    done?: number
    tokens?: number
    prompt_tokens?: number
    completion_tokens?: number
    cached_tokens?: number
    llm_ms?: number
    rounds?: number
    turns?: number
    [key: string]: number | undefined
  }
  policy?: Record<string, unknown>
}

export interface RunStartResponse {
  id: string
  queue?: Array<{ id: string; content?: string; title?: string; target_role_id?: string }>
}

export interface QueueResponse {
  items: Array<{ id: string; content?: string; title?: string; target_role_id?: string }>
  running?: { id: string } | null
}

export interface RunEvent {
  _seq?: number
  start?: boolean
  role_id?: string
  role_name?: string
  thinking?: string
  delta?: string
  round?: number
  tool_call?: {
    id?: string
    name?: string
    status?: string
    args?: unknown
    display?: { diff?: string }
  }
  tool_result?: {
    id?: string
    name?: string
    ok?: boolean
    duration_ms?: number
    args?: unknown
    display?: { diff?: string }
  }
  usage?: TokenUsage
  context_tokens?: number
  todo_update?: { items?: Array<{ content: string; status: string }> }
  tool_approval?: Approval
  approval_decided?: { id: string; status?: string }
  ask_user?: Question
  ask_user_done?: { id: string }
  error?: string | { message?: string }
  done?: boolean
  content?: string
  text?: string
  subagent_event?: unknown
  org_update?: unknown
  role_update?: unknown
  app_update?: unknown
  schedule_update?: unknown
  /** 模型发起、由界面执行的会话指令（与斜杠命令同一套动作）。 */
  session_command?: {
    name?: string
    arg?: string
    done?: boolean
    note?: string
  }
}

export interface MagpieSettings {
  enabled: boolean
  secrets: boolean
  pii: boolean
  paths: boolean
}

export interface MagpieStats {
  redacted_total: number
  by_kind: Record<string, number>
  since?: string
}

export interface MagpieStatus {
  enabled: boolean
  settings: MagpieSettings
  stats: MagpieStats
  categories: Array<{ key: string; label: string }>
}

export interface MagpieTestResult {
  masked: string
  redacted: number
  by_kind: Record<string, number>
}

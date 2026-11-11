<script setup lang="ts">
import { ElButton, ElCheckbox, ElCheckboxGroup, ElEmpty, ElForm, ElFormItem, ElInput, ElInputNumber, ElMenu, ElMenuItem, ElOption, ElSelect, ElSwitch, ElTag } from 'element-plus'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import SideDrawer from '@/components/SideDrawer.vue'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { HookSummary, MagpieStatus, MagpieTestResult, McpServer, ModelProfile, PlatformPreset, Skill, SlashCommand, Subscription, ToolItem, Workspace } from '@/api/types'
import { ACTION_COMMANDS } from '@/lib/commands'
import { playSound } from '@/lib/sound'
import { THEMES, type ThemeId, type ThemeSchedule } from '@/lib/theme'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'
import PageHeader from '@/components/PageHeader.vue'
import ModelFlow from '@/components/ModelFlow.vue'

const app = useAppStore()
const data = useDataStore()
const router = useRouter()

const roleCountByWs = computed(() => {
  const map = new Map<string, number>()
  for (const role of data.persistentRoles) {
    const id = String(role.workspace_id || '')
    if (id) map.set(id, (map.get(id) || 0) + 1)
  }
  return map
})

type Tab = 'models' | 'usage' | 'magpie' | 'mcp' | 'skills' | 'commands' | 'appearance' | 'tools' | 'runtime' | 'workspaces'
const tab = ref<Tab>('models')
const tabs: Array<{ id: Tab; label: string }> = [
  { id: 'models', label: '模型与订阅' },
  { id: 'usage', label: '用量' },
  { id: 'magpie', label: '本机脱敏' },
  { id: 'mcp', label: 'MCP 服务' },
  { id: 'skills', label: '技能' },
  { id: 'commands', label: '命令与钩子' },
  { id: 'appearance', label: '外观' },
  { id: 'tools', label: '工具' },
  { id: 'runtime', label: '运行时' },
  { id: 'workspaces', label: '工作区' },
]

interface UsageTotals {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  cached_tokens?: number
  messages?: number
  rounds?: number
  llm_ms?: number
  cache_rate?: number
  avg_tokens_per_message?: number
  avg_tokens_per_day?: number
  tokens_per_second?: number
  active_days?: number
  max_context_tokens?: number
}

interface UsageRow {
  name?: string
  total_tokens?: number
  prompt_tokens?: number
  completion_tokens?: number
  cache_rate?: number
  messages?: number
  share?: number
  tokens_per_second?: number
}

interface UsageDay {
  date: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  messages: number
}

interface UsagePeriod {
  days?: number
  totals?: UsageTotals
  top_model?: UsageRow | null
  peak_day?: { date?: string; total_tokens?: number } | null
  models?: UsageRow[]
  channels?: UsageRow[]
}

interface UsageStats {
  generated_at?: string
  periods?: Record<string, UsagePeriod>
  daily?: UsageDay[]
}

const usage = ref<UsageStats | null>(null)
const usageLoading = ref(false)
const usagePeriod = ref<'1d' | '7d' | '30d' | 'all'>('7d')
const usagePeriods = [
  { key: '1d', label: '1 天' },
  { key: '7d', label: '7 天' },
  { key: '30d', label: '30 天' },
  { key: 'all', label: '全部' },
] as const

const usageSummary = computed(() => usage.value?.periods?.[usagePeriod.value] || null)
const usageTotals = computed<UsageTotals>(() => usageSummary.value?.totals || {})
const usageDaily = computed(() => usage.value?.daily || [])
const usageMax = computed(() => Math.max(1, ...usageDaily.value.map((day) => Number(day.total_tokens || 0))))

// ── Magpie 本机脱敏 ────────────────────────────────────────
const magpie = ref<MagpieStatus | null>(null)
const magpieLoading = ref(false)
const magpieSaving = ref(false)
const magpieTesting = ref(false)
const magpieTestText = ref('联系 alice@example.com，密钥 sk-abcdefghijklmnopqrstuvwxyz123456，路径 /Users/alice/project/a.txt')
const magpieTestResult = ref<MagpieTestResult | null>(null)
const magpieForm = reactive({
  enabled: true,
  secrets: true,
  pii: true,
  paths: true,
})
const KIND_LABELS: Record<string, string> = {
  SECRET: '密钥', JWT: 'JWT', PRIVATEKEY: '私钥', EMAIL: '邮箱',
  PHONE: '手机号', ID: '身份证', IP: 'IP', PATH: '本机路径',
}

const magpieRuleCount = computed(() => [
  magpieForm.secrets, magpieForm.pii, magpieForm.paths,
].filter(Boolean).length)

function openMagpie(): void {
  tab.value = 'magpie'
  if (!magpie.value) void loadMagpie()
  if (!usage.value) void loadUsage()
}

function kindSummary(byKind?: Record<string, number>): string {
  return Object.entries(byKind || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([kind, count]) => `${KIND_LABELS[kind] || kind} ${count}`)
    .join(' · ')
}

async function loadMagpie(): Promise<void> {
  magpieLoading.value = true
  try {
    magpie.value = await api<MagpieStatus>('/app/magpie')
    const settings = magpie.value.settings
    magpieForm.enabled = Boolean(settings?.enabled)
    magpieForm.secrets = Boolean(settings?.secrets)
    magpieForm.pii = Boolean(settings?.pii)
    magpieForm.paths = Boolean(settings?.paths)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    magpieLoading.value = false
  }
}

async function saveMagpie(): Promise<void> {
  if (magpieSaving.value) return
  magpieSaving.value = true
  try {
    magpie.value = await api<MagpieStatus>('/app/magpie/settings', {
      method: 'PUT',
      body: { ...magpieForm },
    })
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
    await loadMagpie()
  } finally {
    magpieSaving.value = false
  }
}

async function testMagpie(): Promise<void> {
  if (magpieTesting.value) return
  magpieTesting.value = true
  try {
    magpieTestResult.value = await api<MagpieTestResult>('/app/magpie/test', {
      method: 'POST',
      body: { text: magpieTestText.value },
    })
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    magpieTesting.value = false
  }
}

function fmtTokens(value?: number): string {
  const n = Number(value || 0)
  if (n >= 100_000_000) return `${(n / 100_000_000).toFixed(2)} 亿`
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)} 万`
  return String(Math.round(n))
}

function fmtNumber(value?: number): string {
  return Number(value || 0).toLocaleString('zh-CN')
}

function barHeight(day: UsageDay): string {
  return `${Math.max(2, Math.round(Number(day.total_tokens || 0) / usageMax.value * 100))}%`
}

async function loadUsage(): Promise<void> {
  usageLoading.value = true
  try {
    usage.value = await api<UsageStats>('/app/usage/stats')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    usageLoading.value = false
  }
}

let quotaRequestSeq = 0

async function loadQuota(force = false): Promise<void> {
  const sub = selectedSubscription.value
  const seq = ++quotaRequestSeq
  if (!sub || !supportsQuota.value) {
    quota.value = null
    quotaError.value = ''
    quotaLoading.value = false
    return
  }
  quotaLoading.value = true
  quotaError.value = ''
  try {
    const params = new URLSearchParams({ subscription_id: sub.id })
    if (force) params.set('refresh', 'true')
    const result = await api<QuotaData>(`/app/models/usage?${params.toString()}`)
    if (seq !== quotaRequestSeq) return
    quota.value = result
  } catch (error) {
    if (seq !== quotaRequestSeq) return
    quota.value = null
    quotaError.value = error instanceof Error ? error.message : String(error)
  } finally {
    if (seq === quotaRequestSeq) quotaLoading.value = false
  }
}

function quotaTone(percent?: number | null): string {
  const n = Number(percent || 0)
  if (n >= 90) return 'hot'
  if (n >= 70) return 'warn'
  return ''
}

function formatReset(seconds?: number | null): string {
  const n = Number(seconds || 0)
  if (!n) return ''
  if (n >= 86400) return `${Math.floor(n / 86400)} 天 ${Math.floor((n % 86400) / 3600)} 小时后重置`
  if (n >= 3600) return `${Math.floor(n / 3600)} 小时 ${Math.floor((n % 3600) / 60)} 分后重置`
  return `${Math.max(1, Math.floor(n / 60))} 分钟后重置`
}

function formatMoney(value?: number | null): string {
  const n = Number(value || 0)
  return `$${n.toFixed(2)}`
}

function formatCredits(credits?: QuotaCredits | null): string {
  if (!credits) return ''
  const remaining = formatMoney(credits.remaining)
  const limit = Number(credits.limit || 0)
  return limit > 0 ? `${remaining} / ${formatMoney(limit)}` : remaining
}

function formatTokens(value?: number | null): string {
  const n = Number(value || 0)
  if (!n) return '0'
  if (n >= 100000000) return `${(n / 100000000).toFixed(2)} 亿`
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万`
  return String(Math.round(n))
}

function formatContextWindow(value?: number | null): string {
  const n = Number(value || 0)
  if (!n) return ''
  if (n >= 1000000) return `${Number((n / 1000000).toFixed(1))}M 上下文`
  if (n >= 1000) return `${Math.round(n / 1000)}K 上下文`
  return `${n} 上下文`
}

function subscriptionQuotaLabel(sub: Subscription): string {
  const provider = String(sub.provider || '')
  const base = String(sub.base_url || '')
  if (provider === 'command-code' || /api\.commandcode\.ai/i.test(base)) return 'Command Code 额度可查'
  if (provider === 'opencode-go' || /opencode\.ai\/inference\/go/i.test(base)) return 'Go 额度可查'
  return ''
}

function modelPayload(clearSubId = ''): Array<Record<string, unknown>> {
  return data.models
    .filter((item) => item.code !== 'default')
    .map((item) => {
      const belongs = clearSubId && (item.subscription_id || item.channel_id || '') === clearSubId
      return {
        code: item.code,
        name: item.name,
        model: item.model,
        base_url: item.base_url,
        api_key: item.api_key,
        protocol: item.protocol,
        vision: item.vision,
        thinking_levels: item.thinking_levels,
        context_window: item.context_window,
        max_tokens: item.max_tokens,
        concurrency: item.concurrency,
        subscription_id: belongs ? undefined : item.subscription_id,
        channel_id: belongs ? undefined : item.channel_id,
        channel_name: belongs ? undefined : item.channel_name,
      }
    })
}

function openEditSubscription(sub: Subscription): void {
  editingSubId.value = sub.id
  subForm.name = sub.name || ''
  subForm.provider = sub.provider || ''
  subForm.protocol = sub.protocol || 'openai-chat'
  subForm.base_url = sub.base_url || ''
  subForm.anthropic_base_url = sub.anthropic_base_url || ''
  subForm.api_key = sub.api_key || ''
  subForm.models_url = sub.models_url || ''
  addKind.value = 'sub'
  addOpen.value = true
}

async function deleteSubscription(sub: Subscription): Promise<void> {
  const inUse = data.models.some((model) => model.code === data.activeModel
    && (model.subscription_id || model.channel_id || '') === sub.id)
  if (inUse) {
    app.toast('当前系统模型属于这个订阅，请先切换系统模型再删除', 'bad', 4500)
    return
  }
  if (!window.confirm(`删除订阅「${sub.name || sub.id}」？它下面的模型会保留，但会解除订阅绑定。`)) return
  try {
    const list = data.subscriptions.filter((item) => item.id !== sub.id).map((item) => ({ ...item }))
    await api('/app/settings', {
      method: 'PUT',
      body: { subscriptions: list, models: modelPayload(sub.id) },
    })
    if (selectedSubscriptionId.value === sub.id) selectedSubscriptionId.value = ''
    await data.loadAll()
    app.toast('订阅已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  }
}

function openSubMenu(event: MouseEvent, sub: Subscription): void {
  itemMenu.open = false
  subMenu.sub = sub
  subMenu.x = event.clientX
  subMenu.y = event.clientY
  subMenu.open = true
}

async function copyText(value?: string): Promise<void> {
  const text = String(value || '').trim()
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    app.toast('已复制')
  } catch {
    app.toast('复制失败，请手动选择文本', 'bad')
  }
}

const subMenuItems = computed<ContextMenuItem[]>(() => [
  { label: '编辑订阅', action: 'edit' },
  { label: '获取模型', action: 'discover' },
  { label: '复制 Base URL', action: 'copy', hint: subMenu.sub?.base_url || '' },
  { label: '', action: '', separator: true },
  { label: '删除订阅', action: 'delete', danger: true },
])

async function onSubMenu(action: string): Promise<void> {
  const sub = subMenu.sub
  if (!sub) return
  if (action === 'edit') return openEditSubscription(sub)
  if (action === 'copy') return copyText(sub.base_url)
  selectedSubscriptionId.value = sub.id
  if (action === 'discover') {
    await nextTick()
    return discoverModels()
  }
  if (action === 'delete') return deleteSubscription(sub)
}
const tools = ref<ToolItem[]>([])
const loadingTools = ref(false)
const schedule = reactive<ThemeSchedule>({ ...app.schedule })
const soundEnabled = ref(localStorage.getItem('atlasx.sound') !== 'off')

function setSoundEnabled(value: boolean): void {
  soundEnabled.value = value
  localStorage.setItem('atlasx.sound', value ? 'on' : 'off')
  if (value) playSound('done')
}
const mcpServers = ref<McpServer[]>([])
const mcpNpm = ref(true)

const PACK_LABELS: Record<string, string> = {
  fs: '文件',
  net: '联网与计算',
  memory: '记忆',
  plan: '任务与协作',
  dev: '开发与仓库',
  skill: '技能',
  mcp: 'MCP',
}

function toolLabel(code?: string): string {
  const key = String(code || '')
  return tools.value.find((tool) => tool.code === key)?.name || key
}

function packLabel(code?: string): string {
  const key = String(code || '')
  return PACK_LABELS[key] || key
}
const skills = ref<Skill[]>([])
const hooks = ref<HookSummary[]>([])
const workspaceCommands = ref<SlashCommand[]>([])
const legacyLoading = ref(false)
const activeWorkspaceId = computed(() => data.activeWorkspaces[0]?.id || '')
const selectedSubscriptionId = ref('')
const subQuery = ref('')
const modelQuery = ref('')
const platformOpen = ref(false)
const platformSearch = ref('')
const platformPresets = ref<PlatformPreset[]>([])
const platformLoading = ref(false)
const editingSubId = ref('')
const editingModelCode = ref('')
const subMenu = reactive({ open: false, x: 0, y: 0, sub: null as Subscription | null })
type ItemKind = 'mcp' | 'skill' | 'command' | 'hook' | 'model'
const itemMenu = reactive({
  open: false,
  x: 0,
  y: 0,
  kind: 'mcp' as ItemKind,
  id: '',
  name: '',
  index: -1,
  enabled: false,
  builtin: false,
  event: '',
  command: '',
})

const itemMenuItems = computed<ContextMenuItem[]>(() => {
  if (itemMenu.kind === 'mcp') {
    return [
      { label: itemMenu.enabled ? '停用服务' : '启用服务', action: 'toggle' },
      { label: '复制名称', action: 'copy' },
      { label: '', action: '', separator: true },
      { label: '删除服务', action: 'delete', danger: true },
    ]
  }
  if (itemMenu.kind === 'hook') {
    return [
      { label: '复制命令', action: 'copy' },
      { label: '', action: '', separator: true },
      { label: '删除 Hook', action: 'delete', danger: true },
    ]
  }
  if (itemMenu.kind === 'model') {
    return [
      { label: '编辑模型', action: 'edit' },
      { label: '设为系统模型', action: 'activate', disabled: itemMenu.enabled },
      { label: '复制模型 code', action: 'copy' },
      { label: '', action: '', separator: true },
      { label: '删除模型', action: 'delete', danger: true, disabled: itemMenu.enabled },
    ]
  }
  return [
    { label: '复制名称', action: 'copy' },
    { label: '', action: '', separator: true },
    { label: itemMenu.kind === 'skill' ? '删除技能' : '删除命令', action: 'delete', danger: true, disabled: itemMenu.builtin },
  ]
})

function openItemMenu(event: MouseEvent, kind: ItemKind,
                      data: { id?: string; name?: string; index?: number; enabled?: boolean;
                              builtin?: boolean; event?: string; command?: string }): void {
  subMenu.open = false
  itemMenu.kind = kind
  itemMenu.id = String(data.id || '')
  itemMenu.name = String(data.name || '')
  itemMenu.index = Number(data.index ?? -1)
  itemMenu.enabled = Boolean(data.enabled)
  itemMenu.builtin = Boolean(data.builtin)
  itemMenu.event = String(data.event || '')
  itemMenu.command = String(data.command || '')
  itemMenu.x = event.clientX
  itemMenu.y = event.clientY
  itemMenu.open = true
}

async function onItemMenu(action: string): Promise<void> {
  if (action === 'copy') {
    return copyText(itemMenu.kind === 'hook' ? itemMenu.command
      : itemMenu.kind === 'model' ? itemMenu.id
        : itemMenu.name)
  }
  if (itemMenu.kind === 'model') {
    const model = data.models.find((item) => item.code === itemMenu.id)
    if (!model) return
    if (action === 'edit') return openEditModel(model)
    if (action === 'activate') return activate(model.code)
    if (action === 'delete') return removeModel(model)
    return
  }
  if (itemMenu.kind === 'mcp') {
    const server = mcpServers.value.find((item) => item.id === itemMenu.id)
    if (!server) return
    if (action === 'toggle') return toggleMcp(server)
    if (action === 'delete') return deleteMcp(server)
  }
  if (itemMenu.kind === 'skill') {
    const skill = skills.value.find((item) => item.id === itemMenu.id)
    if (skill && action === 'delete') return deleteSkill(skill)
  }
  if (itemMenu.kind === 'command') {
    const command = workspaceCommands.value.find((item) => item.name === itemMenu.name)
    if (command && action === 'delete') return deleteCommand(command)
  }
  if (itemMenu.kind === 'hook' && action === 'delete') {
    try {
      await api(`/app/hooks?event=${encodeURIComponent(itemMenu.event)}&index=${itemMenu.index}&workspace_id=${encodeURIComponent(activeWorkspaceId.value)}`, {
        method: 'DELETE',
      })
      await loadLegacy()
      app.toast('Hook 已删除')
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
  }
}

interface QuotaWindow {
  used?: number | null
  limit?: number | null
  remaining?: number | null
  percent?: number | null
  reset_in_sec?: number | null
  status?: string
}

interface QuotaCredits {
  monthly?: number
  purchased?: number
  free?: number
  remaining?: number
  limit?: number | null
  percent?: number | null
}

interface QuotaPlan {
  id?: string
  name?: string
  status?: string
  period_start?: string
  period_end?: string
}

interface QuotaSummary {
  cost?: number
  requests?: number
  tokens?: number
  tokens_in?: number
  tokens_out?: number
  period_basis?: string
}

interface QuotaData {
  provider?: string
  windows?: Record<string, QuotaWindow>
  credits?: QuotaCredits
  plan?: QuotaPlan
  summary?: QuotaSummary
  subscribed_at?: string
}

interface DiscoveredModel {
  id: string
  name?: string
  protocol?: string
  supported?: boolean
  note?: string
  context_window?: number
}

const quota = ref<QuotaData | null>(null)
const quotaLoading = ref(false)
const quotaError = ref('')
const QUOTA_LABELS: Record<string, string> = {
  five_hour: '5 小时',
  weekly: '本周',
  monthly: '本月',
}
const QUOTA_ORDER = ['five_hour', 'weekly', 'monthly']

const filteredSubscriptions = computed(() => {
  const q = subQuery.value.trim().toLowerCase()
  return data.subscriptions.filter((sub) => !q || `${sub.name || ''} ${sub.id} ${sub.base_url || ''}`.toLowerCase().includes(q))
})
const selectedSubscription = computed(() => data.subscriptions.find((sub) => sub.id === selectedSubscriptionId.value) || null)
const subscriptionModels = computed(() => {
  const id = selectedSubscriptionId.value
  const q = modelQuery.value.trim().toLowerCase()
  return data.models
    .filter((model) => model.code !== 'default' && (model.subscription_id || model.channel_id || '') === id)
    .filter((model) => !q || `${model.name || ''} ${model.code} ${model.model || ''}`.toLowerCase().includes(q))
})
const isOpencodeGoSubscription = computed(() => {
  const sub = selectedSubscription.value
  if (!sub) return false
  return sub.provider === 'opencode-go'
    || /opencode\.ai\/inference\/go/i.test(String(sub.base_url || ''))
})
const isCommandCodeSubscription = computed(() => {
  const sub = selectedSubscription.value
  if (!sub) return false
  return sub.provider === 'command-code'
    || /api\.commandcode\.ai/i.test(String(sub.base_url || ''))
})
const supportsQuota = computed(() => {
  const sub = selectedSubscription.value
  if (!sub) return false
  return isOpencodeGoSubscription.value
    || isCommandCodeSubscription.value
    || Boolean((sub as Record<string, unknown>).supports_quota)
})
const quotaTitle = computed(() => {
  if (isCommandCodeSubscription.value) return 'Command Code 额度'
  if (isOpencodeGoSubscription.value) return 'OpenCode Go 额度'
  return '套餐额度'
})
const quotaEntries = computed(() => {
  const windows = quota.value?.windows || {}
  const keys = [
    ...QUOTA_ORDER.filter((key) => windows[key]),
    ...Object.keys(windows).filter((key) => !QUOTA_ORDER.includes(key)),
  ]
  return keys.map((key) => ({
    key,
    label: QUOTA_LABELS[key] || key,
    ...(windows[key] || {}),
  }))
})

watch(data.subscriptions, (list) => {
  if (!selectedSubscriptionId.value && list.length) selectedSubscriptionId.value = list[0].id
  if (selectedSubscriptionId.value && !list.some((sub) => sub.id === selectedSubscriptionId.value)) {
    selectedSubscriptionId.value = list[0]?.id || ''
  }
}, { immediate: true })

watch(selectedSubscriptionId, () => {
  modelQuery.value = ''
  void loadQuota()
})
const addTitle = computed(() => {
  if (addKind.value === 'sub' && editingSubId.value) return '编辑订阅'
  if (addKind.value === 'model' && editingModelCode.value) return '编辑模型'
  const labels: Record<Exclude<AddKind, ''>, string> = {
    mcp: '新增 MCP 服务',
    skill: '新建技能',
    command: '新建斜杠命令',
    hook: '新增 Hook',
    sub: '新增订阅',
    model: '新增模型',
  }
  return addKind.value ? labels[addKind.value] : '新增'
})
type AddKind = '' | 'mcp' | 'skill' | 'command' | 'hook' | 'sub' | 'model'
const addOpen = ref(false)
const addKind = ref<AddKind>('')
const mcpForm = reactive({ name: '', type: 'stdio', command: '', args: '', url: '', package: '', install_command: '', enabled: true })
const skillForm = reactive({ name: '', description: '', content: '' })
const commandForm = reactive({ name: '', description: '', argument_hint: '', body: '' })
const hookForm = reactive({ event: 'PreToolUse', command: '', matcher: '*', timeout: 10 })
const subForm = reactive({ name: '', provider: '', protocol: 'openai-chat', base_url: '', anthropic_base_url: '', api_key: '', models_url: '' })
const modelForm = reactive({
  code: '', name: '', model: '', subscription_id: '', protocol: 'openai-chat',
  context_window: 0, max_tokens: 0, concurrency: 2, vision: false,
  thinking_levels: [] as string[],
})
const discoverOpen = ref(false)
const discovering = ref(false)
const discoveredModels = ref<DiscoveredModel[]>([])
const discoveredSelected = ref<string[]>([])
const discoverQuery = ref('')
const discoverProtocol = ref('all')
const discoverHideAdded = ref(true)

const existingModelCodes = computed(() => new Set(data.models.map((model) => model.code)))
const discoveredAddable = computed(() => discoveredModels.value.filter(
  (item) => item.supported !== false && !existingModelCodes.value.has(item.id),
))
const discoverProtocolOptions = computed(() => {
  const count = (protocol: string) => discoveredModels.value.filter(
    (item) => (item.protocol || 'openai-chat') === protocol,
  ).length
  return [
    { value: 'all', label: `全部协议 · ${discoveredModels.value.length}` },
    { value: 'anthropic', label: `Anthropic Messages · ${count('anthropic')}` },
    { value: 'openai-chat', label: `OpenAI Chat · ${count('openai-chat')}` },
    { value: 'openai-response', label: `OpenAI Responses · ${count('openai-response')}` },
  ]
})
const filteredDiscoveredModels = computed(() => {
  const q = discoverQuery.value.trim().toLowerCase()
  return discoveredModels.value.filter((item) => {
    if (discoverProtocol.value !== 'all'
      && (item.protocol || 'openai-chat') !== discoverProtocol.value) return false
    if (discoverHideAdded.value && existingModelCodes.value.has(item.id)) return false
    if (!q) return true
    return `${item.name || ''} ${item.id}`.toLowerCase().includes(q)
  })
})
const discoverableModels = computed(() => filteredDiscoveredModels.value.filter(
  (item) => item.supported !== false && !existingModelCodes.value.has(item.id),
))
const discoverSelectedCount = computed(() => discoveredAddable.value.filter(
  (item) => discoveredSelected.value.includes(item.id),
).length)
const discoverAllSelected = computed(() => discoverableModels.value.length > 0
  && discoverableModels.value.every((item) => discoveredSelected.value.includes(item.id)))
const discoverPartiallySelected = computed(() => !discoverAllSelected.value
  && discoverableModels.value.some((item) => discoveredSelected.value.includes(item.id)))

function toggleDiscoverAll(checked: boolean | string | number): void {
  const ids = discoverableModels.value.map((item) => item.id)
  if (checked) {
    discoveredSelected.value = Array.from(new Set([...discoveredSelected.value, ...ids]))
  } else {
    discoveredSelected.value = discoveredSelected.value.filter((id) => !ids.includes(id))
  }
}

function clearDiscoverSelection(): void {
  discoveredSelected.value = []
}

function discoverMeta(item: DiscoveredModel): string {
  const parts: string[] = []
  if (item.name && item.name !== item.id) parts.push(item.id)
  if (item.context_window) parts.push(formatContextWindow(item.context_window))
  if (item.note) parts.push(item.note)
  return parts.join(' · ')
}

const discoveredEmptyHint = computed(() => {
  if (discoveredAddable.value.length) return '没有匹配的模型；清空搜索或切换协议筛选试试。'
  if (discoveredModels.value.length
    && discoveredModels.value.every((item) => existingModelCodes.value.has(item.id))) {
    return '这个端点返回的模型都已添加。'
  }
  return '当前筛选下没有可添加的模型。'
})

onMounted(async () => {
  await Promise.all([data.loadAll(), data.loadInfo()])
  Object.assign(schedule, app.schedule)
  void loadTools()
})

async function loadTools(): Promise<void> {
  loadingTools.value = true
  try {
    const out = await api<{ items: ToolItem[] }>('/app/tools')
    tools.value = out.items || []
  } catch {
    tools.value = []
  } finally {
    loadingTools.value = false
  }
}

async function loadLegacy(): Promise<void> {
  legacyLoading.value = true
  const workspaceId = activeWorkspaceId.value
  try {
    const [mcp, skillData, commandData, hookData] = await Promise.all([
      api<{ servers: McpServer[]; npm: boolean }>('/app/mcp').catch(() => ({ servers: [], npm: false })),
      api<{ items: Skill[] }>(`/app/skills?include_global=true`).catch(() => ({ items: [] })),
      api<{ commands: SlashCommand[] }>(`/app/commands?workspace_id=${encodeURIComponent(workspaceId)}`).catch(() => ({ commands: [] })),
      api<{ hooks?: HookSummary[] }>(`/app/hooks?workspace_id=${encodeURIComponent(workspaceId)}`).catch(() => ({ hooks: [] })),
    ])
    mcpServers.value = mcp.servers || []
    mcpNpm.value = Boolean(mcp.npm)
    skills.value = skillData.items || []
    workspaceCommands.value = [...ACTION_COMMANDS, ...(commandData.commands || [])]
    hooks.value = hookData.hooks || []
  } finally {
    legacyLoading.value = false
  }
}

async function toggleMcp(server: McpServer): Promise<void> {
  try {
    await api(`/app/mcp/servers/${encodeURIComponent(server.id)}`, {
      method: 'PUT',
      body: { enabled: !server.enabled },
    })
    await loadLegacy()
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function deleteMcp(server: McpServer): Promise<void> {
  if (!window.confirm(`删除 MCP 服务「${server.name || server.id}」？`)) return
  try {
    await api(`/app/mcp/servers/${encodeURIComponent(server.id)}`, { method: 'DELETE' })
    await loadLegacy()
    app.toast('MCP 服务已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function deleteSkill(skill: Skill): Promise<void> {
  if (!window.confirm(`删除技能「${skill.name}」？`)) return
  try {
    await api(`/app/skills/${encodeURIComponent(skill.id)}`, { method: 'DELETE' })
    await loadLegacy()
    app.toast('技能已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function deleteCommand(command: SlashCommand): Promise<void> {
  if (!window.confirm(`删除命令「/${command.name}」？`)) return
  try {
    await api(`/app/commands/${encodeURIComponent(command.name)}?workspace_id=${encodeURIComponent(activeWorkspaceId.value)}`, {
      method: 'DELETE',
    })
    await loadLegacy()
    app.toast('命令已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

function watchTab(id: string | number): void {
  const next = String(id) as Tab
  tab.value = next
  if (['mcp', 'skills', 'commands'].includes(next) && !legacyLoading.value) void loadLegacy()
  if (next === 'usage') {
    if (!usage.value) void loadUsage()
    if (!magpie.value) void loadMagpie()
  }
  if (next === 'magpie') {
    if (!magpie.value) void loadMagpie()
    if (!usage.value) void loadUsage()
  }
}

const PLATFORM_GROUPS = ['custom', 'commandcode', 'opencode', 'aggregator', 'local', 'official'] as const
const PLATFORM_LABELS: Record<string, string> = {
  custom: '自定义',
  commandcode: 'COMMAND CODE',
  opencode: 'OPENCODE',
  aggregator: '聚合平台',
  local: '本地网关',
  official: '官方 API',
}

const platformGroups = computed(() => {
  const q = platformSearch.value.trim().toLowerCase()
  const custom: PlatformPreset = { code: 'custom', name: '自定义平台', group: 'custom', protocol: 'openai-chat', note: '从空白开始，添加后填写 Base URL 与 API 密钥。' }
  const all = [custom, ...platformPresets.value]
  const filtered = all.filter((item) => !q || `${item.name} ${item.code} ${item.base_url || ''} ${item.note || ''}`.toLowerCase().includes(q))
  return PLATFORM_GROUPS
    .map((group) => ({ group, label: PLATFORM_LABELS[group], items: filtered.filter((item) => (item.group || 'official') === group) }))
    .filter((group) => group.items.length)
})

async function openPlatformPicker(): Promise<void> {
  platformOpen.value = true
  platformSearch.value = ''
  if (platformPresets.value.length) return
  platformLoading.value = true
  try {
    const out = await api<{ items: PlatformPreset[] }>('/app/models/presets')
    platformPresets.value = out.items || []
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    platformLoading.value = false
  }
}

function choosePlatform(preset?: PlatformPreset): void {
  editingSubId.value = ''
  subForm.name = preset?.name || ''
  subForm.provider = preset?.provider || preset?.code || ''
  subForm.protocol = preset?.protocol || 'openai-chat'
  subForm.base_url = preset?.base_url || ''
  subForm.anthropic_base_url = preset?.anthropic_base_url || ''
  subForm.models_url = preset?.models_url || ''
  subForm.api_key = ''
  platformOpen.value = false
  // 等平台选择器关闭动画结束再开表单，避免两个弹框叠在一起。
  window.setTimeout(() => {
    addKind.value = 'sub'
    addOpen.value = true
  }, 180)
}

function protocolLabel(protocol?: string): string {
  return ({ 'openai-chat': 'OpenAI Chat', 'openai-response': 'OpenAI Responses', anthropic: 'Anthropic Messages' } as Record<string, string>)[protocol || ''] || (protocol || 'OpenAI Chat')
}

function platformHost(preset: PlatformPreset): string {
  const url = preset.base_url || ''
  try {
    return url ? new URL(url).host : '自定义端点'
  } catch {
    return url || '自定义端点'
  }
}

function openAdd(kind: AddKind): void {
  addKind.value = kind
  addOpen.value = true
  if (kind === 'sub') {
    editingSubId.value = ''
    subForm.name = ''
    subForm.provider = ''
    subForm.protocol = 'openai-chat'
    subForm.base_url = ''
    subForm.anthropic_base_url = ''
    subForm.api_key = ''
    subForm.models_url = ''
  }
  if (kind === 'model') {
    editingModelCode.value = ''
    modelForm.code = ''
    modelForm.name = ''
    modelForm.model = ''
    modelForm.subscription_id = selectedSubscriptionId.value || data.subscriptions[0]?.id || ''
    modelForm.protocol = 'openai-chat'
    modelForm.context_window = 0
    modelForm.max_tokens = 0
    modelForm.concurrency = 2
    modelForm.vision = false
    modelForm.thinking_levels = []
  }
}

function openEditModel(model: ModelProfile): void {
  editingModelCode.value = model.code
  modelForm.code = model.code
  modelForm.name = model.name || ''
  modelForm.model = model.model || model.code
  modelForm.subscription_id = model.subscription_id || model.channel_id || selectedSubscriptionId.value || ''
  modelForm.protocol = model.protocol || 'openai-chat'
  modelForm.context_window = Number(model.context_window || 0)
  modelForm.max_tokens = Number(model.max_tokens || 0)
  modelForm.concurrency = Number(model.concurrency || 2)
  modelForm.vision = Boolean(model.vision)
  modelForm.thinking_levels = [...(model.thinking_levels || [])]
  addKind.value = 'model'
  addOpen.value = true
}

async function saveAdd(): Promise<void> {
  const kind = addKind.value
  try {
    if (kind === 'mcp') {
      await api('/app/mcp/servers', {
        method: 'POST',
        body: {
          name: mcpForm.name,
          type: mcpForm.type,
          command: mcpForm.command,
          args: mcpForm.args.split(/\s+/).filter(Boolean),
          url: mcpForm.url,
          package: mcpForm.package,
          install_command: mcpForm.install_command,
          enabled: mcpForm.enabled,
        },
      })
      await loadLegacy()
      app.toast('MCP 服务已添加')
    } else if (kind === 'skill') {
      await api('/app/skills', { method: 'POST', body: skillForm })
      await loadLegacy()
      app.toast('技能已创建')
    } else if (kind === 'command') {
      await api('/app/commands', {
        method: 'POST',
        body: { ...commandForm, workspace_id: activeWorkspaceId.value },
      })
      await loadLegacy()
      app.toast('命令已创建')
    } else if (kind === 'hook') {
      await api('/app/hooks', {
        method: 'POST',
        body: { ...hookForm, workspace_id: activeWorkspaceId.value },
      })
      await loadLegacy()
      app.toast('Hook 已添加')
    } else if (kind === 'sub') {
      const list = data.subscriptions.map((item) => ({ ...item }))
      const payload = {
        name: subForm.name,
        provider: subForm.provider,
        protocol: subForm.protocol,
        base_url: subForm.base_url,
        anthropic_base_url: subForm.anthropic_base_url,
        api_key: subForm.api_key,
        models_url: subForm.models_url,
      }
      if (editingSubId.value) {
        const idx = list.findIndex((item) => item.id === editingSubId.value)
        if (idx < 0) throw new Error('订阅不存在或已被删除')
        list[idx] = { ...list[idx], ...payload }
      } else {
        list.push({ id: `sub-${Date.now().toString(36)}`, ...payload })
      }
      await api('/app/settings', { method: 'PUT', body: { subscriptions: list } })
      await data.loadAll()
      app.toast(editingSubId.value ? '订阅已更新' : '订阅已添加')
      editingSubId.value = ''
      // 订阅的 Key / 端点可能刚改过，额度立即按新配置重查。
      void loadQuota(true)
    } else if (kind === 'model') {
      const code = modelForm.code.trim()
      if (!code) throw new Error('模型 code 不能为空')
      const duplicate = data.models.some((item) => item.code === code)
      if (!editingModelCode.value && duplicate) throw new Error(`模型 code 已存在：${code}`)
      const models: ModelProfile[] = data.models
        .filter((item) => item.code !== 'default')
        .map((item) => ({
          code: item.code,
          name: item.name,
          model: item.model,
          base_url: item.base_url,
          api_key: item.api_key,
          protocol: item.protocol,
          vision: item.vision,
          thinking_levels: item.thinking_levels,
          context_window: item.context_window,
          max_tokens: item.max_tokens,
          concurrency: item.concurrency,
          subscription_id: item.subscription_id,
          channel_id: item.channel_id,
          channel_name: item.channel_name,
        }))
      const sub = data.subscriptions.find((item) => item.id === modelForm.subscription_id)
      const payload: ModelProfile = {
        code,
        name: modelForm.name || code,
        model: modelForm.model || code,
        protocol: modelForm.protocol,
        context_window: modelForm.context_window || undefined,
        max_tokens: modelForm.max_tokens || undefined,
        concurrency: modelForm.concurrency || 2,
        vision: modelForm.vision,
        thinking_levels: modelForm.thinking_levels,
        subscription_id: modelForm.subscription_id || undefined,
        channel_id: modelForm.subscription_id || undefined,
        channel_name: sub?.name || undefined,
      }
      if (editingModelCode.value) {
        const idx = models.findIndex((item) => item.code === editingModelCode.value)
        if (idx < 0) throw new Error('模型不存在或已被删除')
        models[idx] = { ...models[idx], ...payload, code: editingModelCode.value }
      } else {
        models.push(payload)
      }
      await api('/app/settings', { method: 'PUT', body: { models } })
      await data.loadAll()
      app.toast(editingModelCode.value ? '模型已更新' : '模型已添加')
      editingModelCode.value = ''
    }
    addOpen.value = false
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  }
}

function setTheme(id: ThemeId): void {
  app.setTheme(id)
}

function saveSchedule(): void {
  app.setSchedule({ ...schedule })
  app.toast('外观设置已保存')
}

async function discoverModels(): Promise<void> {
  const sub = selectedSubscription.value
  if (!sub) return
  discovering.value = true
  try {
    const out = await api<{ models: DiscoveredModel[] }>(
      '/app/models/discover',
      { method: 'POST', body: { subscription_id: sub.id, base_url: sub.base_url || '', api_key: sub.api_key || '', code: data.activeModel || '' } },
    )
    discoveredModels.value = out.models || []
    discoveredSelected.value = []
    discoverQuery.value = ''
    discoverProtocol.value = 'all'
    discoverHideAdded.value = true
    discoverOpen.value = true
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    discovering.value = false
  }
}

async function addDiscovered(): Promise<void> {
  const sub = selectedSubscription.value
  if (!sub) return
  const existing = new Set(data.models.map((item) => item.code))
  const picked = discoveredModels.value.filter((item) => (
    discoveredSelected.value.includes(item.id)
    && item.supported !== false
    && !existing.has(item.id)
  ))
  if (!picked.length) {
    app.toast('选中的模型都已添加或暂不支持，请重新勾选')
    return
  }
  const models: ModelProfile[] = data.models
    .filter((item) => item.code !== 'default')
    .map((item) => ({
      code: item.code,
      name: item.name,
      model: item.model,
      base_url: item.base_url,
      api_key: item.api_key,
      protocol: item.protocol,
      vision: item.vision,
      thinking_levels: item.thinking_levels,
      context_window: item.context_window,
      max_tokens: item.max_tokens,
      concurrency: item.concurrency,
      subscription_id: item.subscription_id,
      channel_id: item.channel_id,
      channel_name: item.channel_name,
    }))
  for (const item of picked) {
    models.push({
      code: item.id,
      name: item.name || item.id,
      model: item.id,
      protocol: item.protocol || 'openai-chat',
      context_window: item.context_window || undefined,
      vision: false,
      subscription_id: sub.id,
      channel_id: sub.id,
      channel_name: sub.name || sub.id,
    })
  }
  try {
    await api('/app/settings', { method: 'PUT', body: { models } })
    await data.loadAll()
    discoverOpen.value = false
    app.toast(`已添加 ${picked.length} 个模型`)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  }
}

async function removeModel(model: ModelProfile): Promise<void> {
  if (data.activeModel === model.code) {
    app.toast('当前系统模型不能删除，请先切换系统模型', 'bad')
    return
  }
  if (!window.confirm(`删除模型「${model.name || model.code}」？`)) return
  try {
    const models = data.models
      .filter((item) => item.code !== 'default' && item.code !== model.code)
      .map((item) => ({
        code: item.code,
        name: item.name,
        model: item.model,
        base_url: item.base_url,
        api_key: item.api_key,
        protocol: item.protocol,
        vision: item.vision,
        thinking_levels: item.thinking_levels,
        context_window: item.context_window,
        max_tokens: item.max_tokens,
        concurrency: item.concurrency,
        subscription_id: item.subscription_id,
        channel_id: item.channel_id,
        channel_name: item.channel_name,
      }))
    await api('/app/settings', { method: 'PUT', body: { models } })
    await data.loadAll()
    app.toast('模型已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function activate(code: string): Promise<void> {
  try {
    await data.activateModel(code)
    app.toast('系统模型已切换')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function renameWorkspace(ws: Workspace): Promise<void> {
  const name = window.prompt('工作区名称', ws.name)
  if (!name || name === ws.name) return
  try {
    await api(`/workspaces/${encodeURIComponent(ws.id)}`, { method: 'PATCH', body: { name } })
    await data.loadAll()
    app.toast('工作区已重命名')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function toggleWorkspace(ws: Workspace): Promise<void> {
  const archived = !ws.archived
  if (archived && !window.confirm(`归档工作区「${ws.name}」？目录与会话都会保留。`)) return
  try {
    await api(`/workspaces/${encodeURIComponent(ws.id)}`, { method: 'PATCH', body: { archived } })
    await data.loadAll()
    app.toast(archived ? '工作区已归档' : '工作区已恢复')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

</script>

<template>
  <PageHeader section="设置" title="设置中心" chip="本地偏好">
  </PageHeader>

  <div class="canvas-body">
    <div class="settings-layout">
      <aside class="settings-nav">
        <el-menu :default-active="tab" @select="watchTab">
          <el-menu-item v-for="item in tabs" :key="item.id" :index="item.id">{{ item.label }}</el-menu-item>
        </el-menu>
      </aside>

      <div class="settings-content">
      <template v-if="tab === 'models'">
        <div class="models-settings-layout">
          <section class="models-pane subscriptions-pane">
            <header class="models-pane-head">
              <span class="pane-title">订阅 <b>{{ filteredSubscriptions.length }}</b></span>
              <div class="pane-actions">
                <el-button size="small" :disabled="!selectedSubscription" @click="selectedSubscription && openEditSubscription(selectedSubscription)">编辑</el-button>
                <el-button size="small" type="danger" plain :disabled="!selectedSubscription" @click="selectedSubscription && deleteSubscription(selectedSubscription)">删除</el-button>
                <el-button size="small" type="primary" @click="openPlatformPicker">＋ 添加平台</el-button>
              </div>
            </header>
            <div class="pane-filter">
              <el-input v-model="subQuery" size="small" placeholder="筛选订阅" clearable />
            </div>
            <div class="sub-list">
              <button
                v-for="sub in filteredSubscriptions"
                :key="sub.id"
                class="sub-card"
                :class="{ active: selectedSubscriptionId === sub.id }"
                @click="selectedSubscriptionId = sub.id"
                @contextmenu.prevent.stop="openSubMenu($event, sub)"
              >
                <span class="sub-avatar">{{ (sub.name || sub.id).slice(0, 1).toUpperCase() }}</span>
                <span class="sub-main">
                  <span class="sub-title">
                    {{ sub.name || sub.id }}
                    <el-tag v-if="data.activeModel && data.models.find((m) => m.code === data.activeModel)?.subscription_id === sub.id" size="small" type="success">当前</el-tag>
                  </span>
                  <small>{{ sub.provider || sub.base_url || '未配置端点' }}</small>
                  <small v-if="subscriptionQuotaLabel(sub)" style="color:var(--accent-ink)">{{ subscriptionQuotaLabel(sub) }}</small>
                  <small>{{ data.models.filter((m) => m.code !== 'default' && (m.subscription_id || m.channel_id) === sub.id).length }} 个模型</small>
                </span>
                <span class="sub-check">{{ selectedSubscriptionId === sub.id ? '✓' : '' }}</span>
              </button>
              <el-empty v-if="!filteredSubscriptions.length" description="还没有订阅" :image-size="52">
                <el-button size="small" type="primary" @click="openPlatformPicker">添加平台</el-button>
              </el-empty>
            </div>
          </section>

          <section class="models-pane models-list-pane">
            <header class="models-pane-head">
              <div class="pane-title-group">
                <span class="pane-title">模型</span>
                <span class="pane-sub">{{ selectedSubscription?.name || '未选择订阅' }} · {{ subscriptionModels.length }} 个模型</span>
              </div>
              <div class="pane-actions">
                <el-button size="small" :loading="discovering" :disabled="!selectedSubscription" @click="discoverModels">获取模型</el-button>
                <el-button size="small" :disabled="!selectedSubscription" @click="openAdd('model')">＋ 添加模型</el-button>
              </div>
            </header>
            <div class="pane-filter">
              <el-input v-model="modelQuery" size="small" placeholder="筛选模型" clearable />
            </div>

            <div v-if="supportsQuota" class="quota-card">
              <div class="quota-head">
                <b>{{ quotaTitle }}</b>
                <el-tag v-if="quota?.plan?.name" size="small" type="info">{{ quota.plan.name }}</el-tag>
                <span class="quota-status">{{ quotaLoading ? '刷新中…' : (quota ? '实时' : '未查询') }}</span>
                <el-button size="small" text :loading="quotaLoading" @click="loadQuota(true)">刷新</el-button>
              </div>
              <div v-if="quotaError" class="quota-error">{{ quotaError }}</div>
              <template v-else>
                <div v-if="quota?.credits" class="quota-window">
                  <div class="quota-window-head">
                    <b>剩余额度</b>
                    <span>{{ formatCredits(quota?.credits) }}</span>
                  </div>
                  <div v-if="quota?.credits?.limit" class="quota-bar">
                    <i :class="quotaTone(quota?.credits?.percent)" :style="{ width: `${Math.max(0, Math.min(100, Number(quota?.credits?.percent || 0)))}%` }" />
                  </div>
                  <small v-if="Number(quota?.credits?.purchased || 0) + Number(quota?.credits?.free || 0) > 0" class="quota-extra">
                    含额外额度 {{ formatMoney(Number(quota?.credits?.purchased || 0) + Number(quota?.credits?.free || 0)) }}
                  </small>
                </div>
                <div v-for="item in quotaEntries" :key="item.key" class="quota-window">
                  <div class="quota-window-head">
                    <b>{{ item.label }}</b>
                    <span>{{ item.percent == null ? '—' : `${Math.round(Number(item.percent))}%` }} · {{ formatReset(item.reset_in_sec) || (item.limit ? `$${Number(item.used || 0).toFixed(2)} / $${Number(item.limit).toFixed(2)}` : '') }}</span>
                  </div>
                  <div class="quota-bar">
                    <i :class="quotaTone(item.percent)" :style="{ width: `${Math.max(0, Math.min(100, Number(item.percent || 0)))}%` }" />
                  </div>
                </div>
                <div v-if="quota?.summary" class="quota-extra">
                  本期 {{ formatMoney(quota?.summary?.cost) }} · {{ quota?.summary?.requests || 0 }} 次请求<template v-if="quota?.summary?.tokens"> · {{ formatTokens(quota?.summary?.tokens) }} tokens</template>
                </div>
                <div v-if="!quota?.credits && !quotaEntries.length" class="quota-error" style="color:var(--faint)">还没有额度数据，点「刷新」查询。</div>
              </template>
            </div>

            <div class="model-list">
              <div
                v-for="model in subscriptionModels"
                :key="model.code"
                class="model-card"
                @contextmenu.prevent.stop="openItemMenu($event, 'model', { id: model.code, name: model.name || model.code, enabled: data.activeModel === model.code })"
              >
                <span class="model-avatar">{{ (model.name || model.code).slice(0, 1).toUpperCase() }}</span>
                <div class="model-main">
                  <div class="model-title">
                    <b>{{ model.name || model.code }}</b>
                    <el-tag v-if="model.vision" size="small" type="info">图片</el-tag>
                    <el-tag v-if="data.activeModel === model.code" size="small" type="success">当前</el-tag>
                  </div>
                  <small>{{ model.model || model.code }} · {{ protocolLabel(model.protocol) }} · 并发 {{ model.effective_concurrency || model.concurrency || 2 }}<template v-if="model.context_window"> · {{ formatContextWindow(model.context_window) }}</template></small>
                  <small v-if="model.thinking_levels?.length">think：{{ model.thinking_levels.join(' / ') }}</small>
                </div>
                <div class="model-actions">
                  <el-button size="small" text @click="openEditModel(model)">编辑</el-button>
                  <el-button v-if="data.activeModel !== model.code" size="small" text @click="activate(model.code)">设为系统模型</el-button>
                  <el-button size="small" text @click="removeModel(model)" :disabled="data.activeModel === model.code">删除</el-button>
                </div>
              </div>
              <el-empty v-if="!subscriptionModels.length" :description="selectedSubscription ? '这个订阅下还没有模型' : '先在左侧选择一个订阅'" :image-size="56">
                <template v-if="selectedSubscription">
                  <el-button size="small" type="primary" :loading="discovering" @click="discoverModels">从端点获取</el-button>
                  <el-button size="small" @click="openAdd('model')">手动添加</el-button>
                </template>
              </el-empty>
            </div>
            <footer class="models-pane-foot">
              左侧选择订阅（可编辑或删除），右侧只显示该订阅下的模型；默认选中第一个订阅。
            </footer>
          </section>
        </div>
      </template>

      <template v-else-if="tab === 'usage'">
        <div v-if="usageLoading && !usage" class="detail-section"><div class="empty">加载用量…</div></div>
        <template v-else>
          <div class="detail-section">
            <div class="toolbar" style="margin-bottom:0">
              <h3 style="margin:0">用量</h3>
              <span class="role-line">{{ usage?.generated_at ? `统计于 ${usage.generated_at}` : '本地用量统计' }}</span>
              <div class="usage-periods">
                <button
                  v-for="period in usagePeriods"
                  :key="period.key"
                  class="work-filter"
                  :class="{ on: usagePeriod === period.key }"
                  type="button"
                  @click="usagePeriod = period.key"
                >
                  {{ period.label }}
                </button>
              </div>
              <el-button size="small" style="margin-left:auto" @click="openMagpie">本机脱敏</el-button>
              <el-button size="small" :loading="usageLoading" @click="loadUsage">刷新</el-button>
            </div>
          </div>

          <div class="usage-grid">
            <div class="usage-stat"><span>总 Tokens</span><b>{{ fmtTokens(usageTotals.total_tokens) }}</b><small>{{ fmtNumber(usageTotals.total_tokens) }}</small></div>
            <div class="usage-stat"><span>输入 Tokens</span><b>{{ fmtTokens(usageTotals.prompt_tokens) }}</b><small>{{ fmtNumber(usageTotals.prompt_tokens) }}</small></div>
            <div class="usage-stat"><span>输出 Tokens</span><b>{{ fmtTokens(usageTotals.completion_tokens) }}</b><small>{{ fmtNumber(usageTotals.completion_tokens) }}</small></div>
            <div class="usage-stat"><span>缓存命中率</span><b>{{ usageTotals.cache_rate ?? 0 }}%</b><small>{{ fmtTokens(usageTotals.cached_tokens) }} 缓存</small></div>
            <div class="usage-stat"><span>消息数</span><b>{{ fmtNumber(usageTotals.messages) }}</b><small>平均 {{ fmtNumber(usageTotals.avg_tokens_per_message) }} tok/条</small></div>
            <div class="usage-stat"><span>平均每日</span><b>{{ fmtTokens(usageTotals.avg_tokens_per_day) }}</b><small>活跃 {{ usageTotals.active_days ?? 0 }} 天</small></div>
            <div class="usage-stat"><span>轮次</span><b>{{ fmtNumber(usageTotals.rounds) }}</b><small>累计调用</small></div>
            <div class="usage-stat"><span>生成速度</span><b>{{ usageTotals.tokens_per_second ?? 0 }} tok/s</b><small>{{ fmtNumber(usageTotals.llm_ms) }} ms</small></div>
          </div>

          <div class="usage-coverage">
            <div class="usage-cover"><span>覆盖模型</span><b>{{ usageSummary?.top_model?.name || '—' }}</b><small>{{ usageSummary?.top_model ? `${fmtTokens(usageSummary.top_model.total_tokens)} · ${usageSummary.top_model.share ?? 0}%` : '还没有调用记录' }}</small></div>
            <div class="usage-cover"><span>最近活跃</span><b>{{ usageSummary?.peak_day?.date || '—' }}</b><small>{{ usageSummary?.peak_day ? `${fmtTokens(usageSummary.peak_day.total_tokens)} tokens` : '还没有活跃记录' }}</small></div>
            <div class="usage-cover"><span>活跃天数</span><b>{{ usageTotals.active_days ?? 0 }} 天</b><small>按有调用记录的日期统计</small></div>
          </div>

          <div class="detail-section">
            <div class="toolbar" style="margin-bottom:8px">
              <h3 style="margin:0">最近 30 天</h3>
              <span class="role-line" style="margin-left:auto">输入 / 输出合计</span>
            </div>
            <div class="usage-chart">
              <div v-for="(day, index) in usageDaily" :key="day.date" class="usage-bar-col">
                <div class="usage-bar" :style="{ height: barHeight(day) }" :title="`${day.date} · ${fmtNumber(day.total_tokens)} tokens`" />
                <small v-if="index % 5 === 0 || index === usageDaily.length - 1">{{ day.date.slice(5) }}</small>
              </div>
              <div v-if="!usageDaily.length" class="empty">还没有用量记录</div>
            </div>
          </div>

          <div class="detail-section">
            <div class="toolbar" style="margin-bottom:8px">
              <h3 style="margin:0">模型排行</h3>
              <span class="role-line" style="margin-left:auto">{{ (usageSummary?.models || []).length }} 个模型</span>
            </div>
            <div class="usage-rank-list">
              <div v-for="row in usageSummary?.models || []" :key="row.name" class="usage-rank-row">
                <b>{{ row.name || '未知模型' }}</b>
                <span>{{ fmtTokens(row.total_tokens) }}</span>
                <small>{{ row.share ?? 0 }}% · 缓存 {{ row.cache_rate ?? 0 }}%</small>
              </div>
              <div v-if="!(usageSummary?.models || []).length" class="empty">还没有调用记录</div>
            </div>
          </div>
        </template>
      </template>

      <template v-else-if="tab === 'magpie'">
        <div v-if="magpieLoading && !magpie" class="detail-section"><div class="empty">加载本机脱敏状态…</div></div>
        <template v-else>
          <section class="magpie-head">
            <div class="magpie-head-main">
              <div class="magpie-eyebrow">本机脱敏 · MAGPIE</div>
              <h2>敏感信息不出这台电脑</h2>
              <p>
                请求出站前，把密钥、邮箱 / 手机号 / 身份证 / IP、本机绝对路径替换为 ⟦MG_…⟧ 占位符；
                模型回复与工具参数在本机还原。模型只看到占位符。
              </p>
              <div class="magpie-hero-stats">
                <span><b>{{ magpieRuleCount }}</b> 类规则已启用</span>
                <span>规则全部在本机执行</span>
                <span>{{ magpieForm.enabled ? '脱敏已开启' : '脱敏已关闭' }}</span>
              </div>
            </div>
            <div class="magpie-head-side">
              <span class="magpie-state" :class="{ on: magpieForm.enabled }">
                {{ magpieForm.enabled ? '已开启' : '已关闭' }}
              </span>
              <el-switch v-model="magpieForm.enabled" :loading="magpieSaving" @change="saveMagpie" />
            </div>
          </section>

          <ModelFlow
            :total-tokens="Number(usageTotals.total_tokens || 0)"
            :model-name="usageSummary?.top_model?.name || ''"
            :model-tokens="Number(usageSummary?.top_model?.total_tokens || 0)"
            :redacted="Number(magpie?.stats?.redacted_total || 0)"
            :enabled="magpieForm.enabled"
          />

          <div class="magpie-grid">
            <section class="detail-section magpie-rules">
              <div class="toolbar">
                <h3 style="margin:0">脱敏规则</h3>
                <span class="role-line">分类开关</span>
              </div>
              <p class="hintline">关闭某类后，对应信息会按原文发送给模型。</p>
              <div class="magpie-rule-list">
                <div class="magpie-rule">
                  <div>
                    <b>密钥 / Token</b>
                    <small>sk-… / ghp_… / JWT / Bearer / 私钥块 / password=…</small>
                  </div>
                  <el-switch v-model="magpieForm.secrets" :disabled="!magpieForm.enabled" @change="saveMagpie" />
                </div>
                <div class="magpie-rule">
                  <div>
                    <b>个人信息</b>
                    <small>邮箱 / 手机号 / 18 位身份证 / IPv4</small>
                  </div>
                  <el-switch v-model="magpieForm.pii" :disabled="!magpieForm.enabled" @change="saveMagpie" />
                </div>
                <div class="magpie-rule">
                  <div>
                    <b>本机绝对路径</b>
                    <small>/Users/… / /home/… / C:\Users\…</small>
                  </div>
                  <el-switch v-model="magpieForm.paths" :disabled="!magpieForm.enabled" @change="saveMagpie" />
                </div>
              </div>
            </section>

            <section class="detail-section magpie-preview-card">
              <div class="toolbar">
                <h3 style="margin:0">脱敏预览</h3>
                <span class="role-line">只在本机执行，不写入统计</span>
              </div>
              <el-input
                v-model="magpieTestText"
                type="textarea"
                :rows="4"
                :disabled="!magpieForm.enabled"
                placeholder="粘贴一段包含密钥 / 邮箱 / 路径的文本…"
              />
              <div class="magpie-preview-actions">
                <el-button size="small" type="primary" :loading="magpieTesting" :disabled="!magpieForm.enabled" @click="testMagpie">
                  预览脱敏
                </el-button>
                <span v-if="magpieTestResult" class="role-line">
                  已脱敏 {{ magpieTestResult.redacted }} 处 · {{ kindSummary(magpieTestResult.by_kind) }}
                </span>
              </div>
              <pre v-if="magpieTestResult" class="magpie-preview">{{ magpieTestResult.masked }}</pre>
              <div v-else class="empty" style="padding:18px 8px">预览结果会显示在这里</div>
            </section>
          </div>

          <p class="hintline magpie-foot">
            占位符映射只在本次 LLM 调用的内存里，不落库。正则识别是尽力而为，
            不保证覆盖所有敏感信息，也不替代隔离环境；脱敏失败时按原文发送并记录日志。
          </p>
        </template>
      </template>

      <template v-else-if="tab === 'appearance'">
        <div class="detail-section">
          <h3>主题</h3>
          <div class="theme-grid">
            <button v-for="theme in THEMES" :key="theme.id" class="theme-swatch" :class="{ on: app.theme === theme.id }" @click="setTheme(theme.id)">
              <i />
              <b>{{ theme.label }}</b>
            </button>
          </div>
        </div>
        <div class="detail-section">
          <h3>事件音效</h3>
          <label class="switch">
            <input v-model="soundEnabled" type="checkbox" @change="setSoundEnabled(soundEnabled)" />
            <span class="track" />
            <span class="switch-label">审批 / 新消息 / 运行完成使用不同提示音</span>
          </label>
          <p class="hintline">浏览器首次交互后生效；右上角提示会同时出现，10 秒后自动消失。</p>
        </div>
        <div class="detail-section">
          <h3>自动切换</h3>
          <el-form label-position="top" class="atlas-form">
            <el-form-item label="切换方式">
              <el-select v-model="schedule.mode" style="width:200px">
                <el-option label="手动" value="manual" />
                <el-option label="按昼夜" value="daynight" />
                <el-option label="按时间轮换" value="rotate" />
              </el-select>
            </el-form-item>
            <template v-if="schedule.mode === 'daynight'">
              <el-form-item label="白天主题">
                <el-select v-model="schedule.day" style="width:150px">
                  <el-option v-for="theme in THEMES" :key="theme.id" :label="theme.label" :value="theme.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="夜间主题">
                <el-select v-model="schedule.night" style="width:150px">
                  <el-option v-for="theme in THEMES" :key="theme.id" :label="theme.label" :value="theme.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="白天开始 / 夜间开始">
                <div style="display:flex;gap:8px">
                  <el-input v-model="schedule.dayStart" style="width:110px" placeholder="06:00" />
                  <el-input v-model="schedule.nightStart" style="width:110px" placeholder="19:00" />
                </div>
              </el-form-item>
            </template>
            <el-form-item v-if="schedule.mode === 'rotate'" label="轮换间隔（分钟）">
              <el-input-number v-model="schedule.interval" :min="5" :max="720" />
            </el-form-item>
          </el-form>
          <el-button type="primary" @click="saveSchedule">保存</el-button>
        </div>
      </template>

      <template v-else-if="tab === 'workspaces'">
        <div class="detail-section">
          <h3>使用中</h3>
          <div class="file-list">
            <div v-for="ws in data.activeWorkspaces" :key="ws.id" class="file-row" style="cursor:default">
              <span>▣</span>
              <span class="fname">{{ ws.name }}<small style="color:var(--faint)"> · {{ ws.root_path || '纯聊天' }}</small></span>
              <span class="fsize">{{ roleCountByWs.get(ws.id) || 0 }} 角色</span>
              <el-button size="small" @click="router.push(`/ws/${ws.id}`)">打开</el-button>
              <el-button size="small" @click="renameWorkspace(ws)">改名</el-button>
              <el-button size="small" @click="toggleWorkspace(ws)">归档</el-button>
            </div>
            <div v-if="!data.activeWorkspaces.length" class="empty">没有使用中的工作区</div>
          </div>
        </div>
        <div class="detail-section">
          <h3>已归档</h3>
          <div class="file-list">
            <div v-for="ws in data.archivedWorkspaces" :key="ws.id" class="file-row" style="cursor:default">
              <span>▣</span>
              <span class="fname">{{ ws.name }}</span>
              <el-button size="small" @click="toggleWorkspace(ws)">恢复</el-button>
            </div>
            <div v-if="!data.archivedWorkspaces.length" class="empty">没有已归档的工作区</div>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'tools'">
        <div class="detail-section">
          <div class="toolbar" style="margin-bottom:8px">
            <h3 style="margin:0">工具包</h3>
            <el-tag size="small">{{ (data.info?.tool_catalog?.packs || []).length }} 个包</el-tag>
            <el-tag size="small">{{ (data.info?.tool_catalog?.standalone || []).length }} 个独立工具</el-tag>
          </div>
          <div class="tool-pack-grid">
            <article
              v-for="(pack, index) in data.info?.tool_catalog?.packs || []"
              :key="pack.code || index"
              class="tool-pack"
              :class="{ off: pack.available === false }"
              :title="`${packLabel(pack.code)} (${pack.code}) · ${(pack.ops || []).length} 个动作`"
            >
              <div class="tool-pack-head">
                <b>{{ packLabel(pack.code) }}</b>
                <code>{{ pack.code }}</code>
                <el-tag size="small" style="margin-left:auto">{{ (pack.ops || []).length }}</el-tag>
              </div>
              <div class="tool-op-list">
                <span
                  v-for="op in pack.ops || []"
                  :key="op.op"
                  class="tool-op"
                  :class="{ off: op.available === false }"
                  :title="`${op.op} → ${toolLabel(op.tool)} (${op.tool})${op.available === false ? ' · 需要本地文件夹' : ''}`"
                >
                  <code>{{ op.op }}</code>
                  <span>{{ toolLabel(op.tool) }}</span>
                </span>
              </div>
            </article>
          </div>
          <div v-if="!(data.info?.tool_catalog?.packs || []).length" class="empty">没有读取到工具包</div>
        </div>

        <div class="detail-section">
          <div class="toolbar" style="margin-bottom:8px">
            <h3 style="margin:0">工具目录</h3>
            <el-tag size="small">{{ tools.length }}</el-tag>
          </div>
          <div v-if="loadingTools" class="empty">加载中…</div>
          <div v-else class="tool-catalog-list">
            <div
              v-for="(tool, index) in tools"
              :key="tool.code || index"
              class="tool-catalog-row"
              :title="`${tool.name || tool.code}\n\n${tool.description || '没有描述'}`"
            >
              <b>{{ tool.name || tool.code }}</b>
              <code>{{ tool.code }}</code>
              <span class="tool-catalog-hint" title="悬停查看完整说明">ⓘ</span>
              <el-tag v-if="tool.risk && tool.risk !== 'low'" size="small" :type="tool.risk === 'high' ? 'danger' : 'warning'">{{ tool.risk }}</el-tag>
            </div>
          </div>
          <div v-if="!loadingTools && !tools.length" class="empty">没有读取到工具目录</div>
        </div>
      </template>

      <template v-else-if="tab === 'runtime'">
        <div class="detail-section">
          <h3>应用</h3>
          <dl class="kv">
            <dt>名称</dt><dd>{{ data.info?.name || 'AtlasX' }}</dd>
            <dt>版本</dt><dd>{{ data.info?.version || '—' }}</dd>
            <dt>模式</dt><dd>{{ data.info?.mode || 'local' }}</dd>
            <dt>端口</dt><dd>{{ data.info?.port || '—' }}</dd>
            <dt>配置文件</dt><dd>{{ data.info?.config_path || '—' }}</dd>
            <dt>数据库</dt><dd>{{ data.info?.db_path || '—' }}</dd>
          </dl>
        </div>
        <div class="detail-section">
          <h3>限制</h3>
          <dl class="kv">
            <dt>并发 run</dt><dd>{{ data.info?.limits?.max_concurrent_runs ?? '—' }}</dd>
            <dt>单群成员并行</dt><dd>{{ data.info?.limits?.max_parallel_member_runs ?? '—' }}</dd>
            <dt>上下文压缩阈值</dt><dd>{{ data.info?.limits?.context_compress_percent ?? '—' }}%</dd>
            <dt>自主运行上限</dt><dd>{{ data.info?.limits?.autonomous_run_max_seconds ?? '—' }}s</dd>
          </dl>
        </div>
      </template>

      <template v-else-if="tab === 'mcp'">
        <div v-if="legacyLoading" class="detail-section"><div class="empty">加载中…</div></div>
        <div v-else class="detail-section">
          <div class="toolbar" style="margin-bottom:8px">
            <h3 style="margin:0">MCP 服务</h3>
            <el-tag size="small" :type="mcpNpm ? 'success' : 'info'">{{ mcpNpm ? 'npm 可用' : 'npm 不可用' }}</el-tag>
            <el-button size="small" style="margin-left:auto" @click="loadLegacy">刷新</el-button>
            <el-button size="small" type="primary" @click="openAdd('mcp')">新增</el-button>
          </div>
          <div class="file-list">
            <div v-for="server in mcpServers" :key="server.id" class="file-row" style="cursor:default" @contextmenu.prevent.stop="openItemMenu($event, 'mcp', { id: server.id, name: server.name || server.id, enabled: server.enabled })">
              <span>{{ server.enabled ? '●' : '○' }}</span>
              <span class="fname">
                {{ server.name || server.id }}
                <small style="color:var(--faint)">
                  · {{ server.type }} · {{ server.tools || 0 }} 个工具<template v-if="server.persistent"> · 常驻</template>
                </small>
              </span>
              <el-tag size="small" :type="server.installed ? 'success' : 'info'">{{ server.installed ? '已安装' : '未安装' }}</el-tag>
              <el-button size="small" @click="toggleMcp(server)">{{ server.enabled ? '停用' : '启用' }}</el-button>
              <el-button size="small" type="danger" plain @click="deleteMcp(server)">删除</el-button>
            </div>
            <div v-if="!mcpServers.length" class="empty">还没有 MCP 服务，点新增添加。</div>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'skills'">
        <div v-if="legacyLoading" class="detail-section"><div class="empty">加载中…</div></div>
        <div v-else class="detail-section">
          <div class="toolbar" style="margin-bottom:8px"><h3 style="margin:0">技能</h3><el-button size="small" type="primary" style="margin-left:auto" @click="openAdd('skill')">新建</el-button></div>
          <div class="file-list">
            <div v-for="skill in skills" :key="skill.id" class="file-row" style="cursor:default" @contextmenu.prevent.stop="openItemMenu($event, 'skill', { id: skill.id, name: skill.name })">
              <span>✦</span>
              <span class="fname">{{ skill.name }}<small style="color:var(--faint)"> · {{ skill.description || '无描述' }}</small></span>
              <el-tag size="small" type="info">{{ skill.role_id ? '角色专属' : '全局' }}</el-tag>
              <el-button size="small" type="danger" plain @click="deleteSkill(skill)">删除</el-button>
            </div>
            <div v-if="!skills.length" class="empty">还没有技能</div>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'commands'">
        <div v-if="legacyLoading" class="detail-section"><div class="empty">加载中…</div></div>
        <template v-else>
          <div class="detail-section">
            <div class="toolbar" style="margin-bottom:8px"><h3 style="margin:0">斜杠命令</h3><el-button size="small" type="primary" style="margin-left:auto" @click="openAdd('command')">新建</el-button></div>
            <div class="file-list">
              <div v-for="command in workspaceCommands" :key="command.name" class="file-row" style="cursor:default" @contextmenu.prevent.stop="openItemMenu($event, 'command', { name: command.name, builtin: command.builtin })">
                <span>/</span>
                <span class="fname">{{ command.name }}<small style="color:var(--faint)"> · {{ command.description || command.argument_hint || '无描述' }}</small></span>
                <el-tag size="small" type="info">{{ command.source || 'workspace' }}</el-tag>
                <el-button v-if="!command.builtin" size="small" type="danger" plain @click="deleteCommand(command)">删除</el-button>
              </div>
              <div v-if="!workspaceCommands.length" class="empty">当前工作区没有自定义命令</div>
            </div>
          </div>

          <div class="detail-section">
            <div class="toolbar" style="margin-bottom:8px"><h3 style="margin:0">命令 / hooks</h3><el-button size="small" type="primary" style="margin-left:auto" @click="openAdd('hook')">新增</el-button></div>
            <div class="file-list">
              <div v-for="(hook, index) in hooks" :key="index" class="file-row" style="cursor:default" @contextmenu.prevent.stop="openItemMenu($event, 'hook', { index, event: hook.event, command: hook.command, name: hook.event })">
                <span>⚡</span>
                <span class="fname">{{ hook.event || 'event' }}<small style="color:var(--faint)"> · {{ hook.command || '' }}</small></span>
                <el-tag size="small" type="info">{{ hook.matcher || '*' }}</el-tag>
              </div>
              <div v-if="!hooks.length" class="empty">没有配置 hooks</div>
            </div>
          </div>
        </template>
      </template>


        <SideDrawer :open="platformOpen" title="平台预设" subtitle="官方 API、编程订阅、聚合平台，以及自建 / 本地网关。" width="680px" @close="platformOpen = false">
          <el-input v-model="platformSearch" placeholder="搜索平台" clearable />
          <div v-if="platformLoading" class="empty">加载平台预设…</div>
          <div v-else class="platform-groups">
            <section v-for="group in platformGroups" :key="group.group">
              <h4>{{ group.label }}</h4>
              <div v-for="preset in group.items" :key="preset.code" class="platform-row">
                <span class="platform-main">
                  <b>{{ preset.name }}</b>
                  <small>{{ protocolLabel(preset.protocol) }} · {{ platformHost(preset) }}</small>
                  <small v-if="preset.note" class="platform-note">{{ preset.note }}</small>
                </span>
                <el-tag v-if="preset.requires_gateway" size="small" type="warning">需本地网关</el-tag>
                <el-button class="platform-add" size="small" @click="choosePlatform(preset)">＋</el-button>
              </div>
            </section>
            <div v-if="!platformGroups.length" class="empty">没有匹配的平台。</div>
          </div>
          <p class="platform-foot-note">本地网关预设使用常见默认端口；添加后可在订阅卡片里改成自己的地址。</p>
          <template #footer>
            <el-button @click="platformOpen = false">关闭</el-button>
          </template>
        </SideDrawer>
        <SideDrawer :open="addOpen" :title="addTitle" subtitle="保存后立即生效" width="620px" @close="addOpen = false">
          <el-form label-position="top" class="atlas-form">
            <template v-if="addKind === 'sub'">
              <el-form-item label="名称"><el-input v-model="subForm.name" /></el-form-item>
              <el-form-item label="Provider"><el-input v-model="subForm.provider" /></el-form-item>
              <el-form-item label="协议">
                <el-select v-model="subForm.protocol" style="width:100%">
                  <el-option label="OpenAI Chat" value="openai-chat" />
                  <el-option label="OpenAI Responses" value="openai-response" />
                  <el-option label="Anthropic Messages" value="anthropic" />
                </el-select>
              </el-form-item>
              <el-form-item label="OpenAI Base URL"><el-input v-model="subForm.base_url" /></el-form-item>
              <el-form-item label="Anthropic Base URL"><el-input v-model="subForm.anthropic_base_url" /></el-form-item>
              <el-form-item label="API Key"><el-input v-model="subForm.api_key" type="password" show-password /></el-form-item>
              <el-form-item label="Models URL（可选）"><el-input v-model="subForm.models_url" /></el-form-item>
            </template>

            <template v-else-if="addKind === 'model'">
              <el-form-item label="模型 code">
                <el-input v-model="modelForm.code" :disabled="Boolean(editingModelCode)" placeholder="例如 fast" />
                <div v-if="editingModelCode" class="hintline">code 是模型主键，编辑时不可修改。</div>
              </el-form-item>
              <el-form-item label="显示名"><el-input v-model="modelForm.name" /></el-form-item>
              <el-form-item label="模型 ID"><el-input v-model="modelForm.model" placeholder="例如 gpt-4o-mini" /></el-form-item>
              <el-form-item label="订阅">
                <el-select v-model="modelForm.subscription_id" clearable style="width:100%">
                  <el-option v-for="sub in data.subscriptions" :key="sub.id" :label="sub.name || sub.id" :value="sub.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="协议">
                <el-select v-model="modelForm.protocol" style="width:100%">
                  <el-option label="OpenAI Chat" value="openai-chat" />
                  <el-option label="OpenAI Responses" value="openai-response" />
                  <el-option label="Anthropic Messages" value="anthropic" />
                </el-select>
              </el-form-item>
              <el-form-item label="思考强度（可选）">
                <el-select
                  v-model="modelForm.thinking_levels"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="例如 low / medium / high"
                  style="width:100%"
                >
                  <el-option v-for="level in ['minimal', 'low', 'medium', 'high', 'xhigh']" :key="level" :label="level" :value="level" />
                </el-select>
                <small class="role-line">可多选；输入自定义名称后回车即可新增，多个档位会同时出现在聊天输入框里。</small>
              </el-form-item>
              <el-form-item label="上下文窗口 / 最大输出">
                <div style="display:flex;gap:8px">
                  <el-input-number v-model="modelForm.context_window" :min="0" :step="1000" controls-position="right" />
                  <el-input-number v-model="modelForm.max_tokens" :min="0" :step="1000" controls-position="right" />
                </div>
              </el-form-item>
              <el-form-item label="并发上限">
                <el-input-number v-model="modelForm.concurrency" :min="1" :max="64" controls-position="right" />
                <span class="hintline" style="margin-left:8px">同一模型同时在途的请求数，默认 2</span>
              </el-form-item>
              <el-form-item label="图片支持"><el-switch v-model="modelForm.vision" /></el-form-item>
            </template>

            <template v-else-if="addKind === 'mcp'">
              <el-form-item label="名称"><el-input v-model="mcpForm.name" /></el-form-item>
              <el-form-item label="类型">
                <el-select v-model="mcpForm.type" style="width:100%">
                  <el-option label="stdio" value="stdio" />
                  <el-option label="http / sse" value="http" />
                </el-select>
              </el-form-item>
              <el-form-item label="命令"><el-input v-model="mcpForm.command" placeholder="npx" /></el-form-item>
              <el-form-item label="参数"><el-input v-model="mcpForm.args" placeholder="-y @modelcontextprotocol/server-filesystem /path" /></el-form-item>
              <el-form-item label="URL"><el-input v-model="mcpForm.url" /></el-form-item>
              <el-form-item label="npm 包"><el-input v-model="mcpForm.package" /></el-form-item>
              <el-form-item label="安装命令"><el-input v-model="mcpForm.install_command" /></el-form-item>
              <el-form-item label="启用"><el-switch v-model="mcpForm.enabled" /></el-form-item>
            </template>

            <template v-else-if="addKind === 'skill'">
              <el-form-item label="名称"><el-input v-model="skillForm.name" /></el-form-item>
              <el-form-item label="描述"><el-input v-model="skillForm.description" /></el-form-item>
              <el-form-item label="内容"><el-input v-model="skillForm.content" type="textarea" :rows="8" /></el-form-item>
            </template>

            <template v-else-if="addKind === 'command'">
              <el-form-item label="命令名（不带 /）"><el-input v-model="commandForm.name" /></el-form-item>
              <el-form-item label="描述"><el-input v-model="commandForm.description" /></el-form-item>
              <el-form-item label="参数提示"><el-input v-model="commandForm.argument_hint" /></el-form-item>
              <el-form-item label="正文"><el-input v-model="commandForm.body" type="textarea" :rows="7" /></el-form-item>
            </template>

            <template v-else-if="addKind === 'hook'">
              <el-form-item label="事件">
                <el-select v-model="hookForm.event" style="width:100%">
                  <el-option v-for="event in ['UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop']" :key="event" :label="event" :value="event" />
                </el-select>
              </el-form-item>
              <el-form-item label="命令"><el-input v-model="hookForm.command" /></el-form-item>
              <el-form-item label="matcher"><el-input v-model="hookForm.matcher" /></el-form-item>
              <el-form-item label="超时（秒）"><el-input-number v-model="hookForm.timeout" :min="1" :max="120" /></el-form-item>
            </template>
          </el-form>
          <template #footer>
            <el-button @click="addOpen = false">取消</el-button>
            <el-button type="primary" @click="saveAdd">保存</el-button>
          </template>
        </SideDrawer>

        <SideDrawer
          :open="discoverOpen"
          title="获取模型"
          :subtitle="`${selectedSubscription?.name || selectedSubscription?.id || ''} · 共 ${discoveredModels.length} 个模型`"
          width="760px"
          @close="discoverOpen = false"
        >
          <div class="discover-toolbar">
            <el-input v-model="discoverQuery" placeholder="搜索模型 ID / 名称" clearable />
            <el-select v-model="discoverProtocol" style="width:200px">
              <el-option v-for="option in discoverProtocolOptions" :key="option.value" :label="option.label" :value="option.value" />
            </el-select>
            <el-switch v-model="discoverHideAdded" active-text="隐藏已添加" />
          </div>

          <div v-if="!discoveredModels.length" class="empty">端点没有返回模型。</div>
          <template v-else>
            <div class="discover-summary">
              <el-checkbox
                :model-value="discoverAllSelected"
                :indeterminate="discoverPartiallySelected"
                :disabled="!discoverableModels.length"
                @change="toggleDiscoverAll"
              >
                全选当前筛选（{{ discoverableModels.length }} 个可添加）
              </el-checkbox>
              <el-button v-if="discoverSelectedCount" size="small" text @click="clearDiscoverSelection">清空已选</el-button>
              <span>已选 {{ discoverSelectedCount }} 个 · 共 {{ discoveredModels.length }} 个</span>
            </div>

            <div v-if="!filteredDiscoveredModels.length" class="empty">
              {{ discoveredEmptyHint }}
            </div>
            <el-checkbox-group v-else v-model="discoveredSelected" class="discover-list">
              <el-checkbox
                v-for="item in filteredDiscoveredModels"
                :key="item.id"
                :value="item.id"
                :disabled="item.supported === false || existingModelCodes.has(item.id)"
                class="discover-row"
              >
                <span class="discover-main">
                  <span class="discover-title">
                    <b>{{ item.name || item.id }}</b>
                    <el-tag size="small" type="info" effect="plain">{{ protocolLabel(item.protocol) }}</el-tag>
                    <el-tag v-if="existingModelCodes.has(item.id)" size="small" type="success">已添加</el-tag>
                    <el-tag v-else-if="item.supported === false" size="small" type="warning">暂不支持</el-tag>
                  </span>
                  <small v-if="discoverMeta(item)">{{ discoverMeta(item) }}</small>
                </span>
              </el-checkbox>
            </el-checkbox-group>
          </template>

          <template #footer>
            <span class="discover-foot">已选 {{ discoverSelectedCount }} 个</span>
            <span class="spacer" />
            <el-button @click="discoverOpen = false">取消</el-button>
            <el-button type="primary" :disabled="!discoverSelectedCount" @click="addDiscovered">添加选中 {{ discoverSelectedCount }} 个</el-button>
          </template>
        </SideDrawer>
      </div>
    </div>
  </div>

  <ContextMenu
    :open="subMenu.open"
    :x="subMenu.x"
    :y="subMenu.y"
    :items="subMenuItems"
    @close="subMenu.open = false"
    @select="onSubMenu"
  />

  <ContextMenu
    :open="itemMenu.open"
    :x="itemMenu.x"
    :y="itemMenu.y"
    :items="itemMenuItems"
    @close="itemMenu.open = false"
    @select="onItemMenu"
  />
</template>
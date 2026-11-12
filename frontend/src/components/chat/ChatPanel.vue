<script setup lang="ts">
import { ElPopover } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import Modal from '@/components/Modal.vue'
import TokenMeter from '@/components/TokenMeter.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { Conversation, SlashCommand, WorkItem } from '@/api/types'
import { ACTION_COMMANDS } from '@/lib/commands'
import { conversationIsSharedGroup, conversationMeta, conversationTitle, GLOBAL_AGENT_ZONE } from '@/lib/derive'
import { compactNumber } from '@/lib/format'
import { cacheTone, mergeUsage, usageSummary } from '@/lib/usage'
import { useChat } from '@/composables/useChat'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { useChatWindowsStore, type ChatPresentation } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'
import ChatTaskCenter from './ChatTaskCenter.vue'
import ApprovalCard from './ApprovalCard.vue'
import AskCard from './AskCard.vue'
import Composer from './Composer.vue'
import ConversationSettingsDrawer from './ConversationSettingsDrawer.vue'
import GroupSettingsDrawer from '@/components/GroupSettingsDrawer.vue'
import GlobalInbox from './GlobalInbox.vue'
import LiveMessage from './LiveMessage.vue'
import MessageItem from './MessageItem.vue'
import ParticipantsDrawer from './ParticipantsDrawer.vue'
import TaskCard from './TaskCard.vue'

const router = useRouter()
const app = useAppStore()
const data = useDataStore()
const attention = useAttentionStore()
const chatWindows = useChatWindowsStore()
const chat = useChat()

const props = defineProps<{
  conversationId: string
  presentation?: ChatPresentation
  globalAgent?: boolean
  globalConversation?: boolean
  initialTab?: 'chat' | 'inbox'
  draftRoleId?: string
  draftParticipants?: string[]
  draftWorkspaceId?: string
  draftTeamId?: string
}>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'title', title: string): void; (e: 'promoted', conversationId: string): void; (e: 'update:presentation', mode: ChatPresentation): void }>()
const floating = computed(() => (props.presentation || 'popup') === 'popup')

const BUILTIN_COMMANDS = ACTION_COMMANDS
const isDraft = computed(() => !props.conversationId
  && Boolean(props.draftRoleId || props.draftTeamId))

const draft = ref('')
const selectedModel = ref('')
const perm = ref('once')
const thinking = ref('')
const commands = ref<SlashCommand[]>(BUILTIN_COMMANDS)
const scroller = ref<HTMLElement | null>(null)
const msgsEl = ref<HTMLElement | null>(null)
const stickToBottom = ref(true)
const canScroll = ref(false)
const atTop = ref(true)
const atBottom = ref(true)
let scrollRaf = 0
let resizeObserver: ResizeObserver | null = null
let settleTimers: number[] = []
// 会话历史按批展开：默认只渲染最近 60 条，点「查看更多」再往前补一批。
// 长会话一次铺 200 条（约 3900 个 DOM 节点）会把首帧拖到 400ms+。
const MESSAGE_WINDOW = 60
const messageWindow = ref(MESSAGE_WINDOW)
const hiddenMessages = computed(
  () => Math.max(0, chat.messages.value.length - messageWindow.value),
)
const visibleMessages = computed(() => {
  const all = chat.messages.value
  return all.length <= messageWindow.value ? all : all.slice(all.length - messageWindow.value)
})
watch([() => props.conversationId, () => chat.conversation.value?.id], () => {
  messageWindow.value = MESSAGE_WINDOW
})

function showEarlierMessages(): void {
  const el = scroller.value
  // 以「当前窗口第一条消息」为锚点：展开后把它顶回原来的位置，
  // 比记 scrollHeight 差值稳（Markdown / 图片高度是异步撑开的）。
  const anchorId = String(visibleMessages.value[0]?.id || '')
  messageWindow.value += MESSAGE_WINDOW
  nextTick(() => {
    if (!el || !anchorId) return
    const node = el.querySelector<HTMLElement>(`[data-mid="${CSS.escape(anchorId)}"]`)
    if (!node) return
    const offset = node.getBoundingClientRect().top - el.getBoundingClientRect().top
    el.scrollTop += offset - 8
    updateScrollState()
  })
}
const tempOpen = ref(false)
const tempForm = reactive({ name: '', expertise: '', persona: '' })
const taskCenterOpen = ref(false)
const settingsOpen = ref(false)
const participantsOpen = ref(false)
const groupInfoOpen = ref(false)
const avatarMenu = reactive({ open: false, x: 0, y: 0 })
const avatarMenuItems = computed<ContextMenuItem[]>(() => {
  if (isDraft.value) {
    return [
      { label: '发出第一条消息后才会创建会话', action: 'noop', disabled: true },
      { label: '', action: '', separator: true },
      { label: '关闭窗口', action: 'close' },
    ]
  }
  return [
    { label: '会话管理', action: 'settings' },
    isGroupChat.value
      ? { label: '群资料 / 群成员', action: 'group-info' }
      : { label: '参与角色', action: 'participants', disabled: Boolean(props.globalAgent) },
    ...(isGroupChat.value
      ? [{ label: '打开群组页', action: 'group-page' }]
      : []),
    { label: '重命名会话', action: 'rename' },
    { label: '复制会话 ID', action: 'copy-id' },
    { label: '', action: '', separator: true },
    { label: '清空会话上下文', action: 'clean' },
    { label: '标记已读', action: 'read' },
    { label: '', action: '', separator: true },
    { label: '删除会话', action: 'delete', danger: true, disabled: Boolean(props.globalAgent) },
    { label: '关闭窗口', action: 'close' },
  ]
})

function openAvatarMenu(event: MouseEvent): void {
  avatarMenu.x = event.clientX
  avatarMenu.y = event.clientY
  avatarMenu.open = true
}

async function onAvatarMenu(action: string): Promise<void> {
  avatarMenu.open = false
  if (action === 'settings') {
    settingsOpen.value = true
    return
  }
  if (action === 'participants') {
    participantsOpen.value = true
    return
  }
  if (action === 'group-info') {
    openGroupSettings()
    return
  }
  if (action === 'group-page') {
    openGroupPage()
    return
  }
  if (action === 'rename') {
    const current = conversation.value ? conversationTitle(conversation.value, data.teams, data.roles) : ''
    const next = window.prompt('会话标题', current) || ''
    if (next.trim()) await renameConversation(next.trim())
    return
  }
  if (action === 'copy-id') {
    const id = conversation.value?.id || props.conversationId
    if (!id) return
    try {
      await navigator.clipboard.writeText(id)
      app.toast('已复制会话 ID')
    } catch {
      app.toast('复制失败', 'bad')
    }
    return
  }
  if (action === 'clean') {
    cleanOpen.value = true
    return
  }
  if (action === 'read') {
    const id = conversation.value?.id
    if (!id) return
    try {
      await api(`/conversations/${encodeURIComponent(id)}/read`, { method: 'POST' })
      await data.loadAll()
      app.toast('已标记为已读')
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
    return
  }
  if (action === 'delete') {
    await deleteConversation()
    return
  }
  if (action === 'close') emit('close')
}
const cleanOpen = ref(false)
const cleaning = ref(false)
const backgroundTaskCount = ref(0)
// 会话关联工单：角色 / 群组的后台任务会自动建单，这里展示 + 打开详情窗口。
const conversationWork = ref<WorkItem[]>([])
const openWorkCount = computed(() => conversationWork.value.filter(
  (item) => ['inbox', 'working', 'needs_attention'].includes(String(item.status || '')),
).length)
const taskCenterCount = computed(() => backgroundTaskCount.value + openWorkCount.value)
// 已结束但还没交回主会话的后台任务：默认由后端自动交回；自动交回失败时
// 这里给出「继续对话」手动兜底。
const continueTask = ref<{ id: string; title: string } | null>(null)
let taskTimer = 0
const globalTab = ref<'chat' | 'inbox'>(props.initialTab || 'chat')
// 角标只算必须由人处理的事项：待授权 + 待回答。
const inboxCount = computed(() => Number(attention.counts.authorizations || 0))
watch(() => props.initialTab, (tab) => { if (tab) globalTab.value = tab })
// 浮窗记忆：按「作用 / 组件」区分，普通会话统一一份，全局管理、全局对话各自独立。
// 位置和尺寸都分开保存，互不覆盖。
const memoryKey = computed(() => {
  if (props.globalAgent) return 'global-agent'
  if (props.globalConversation) return 'global-chat'
  return 'session'
})

// 浮窗尺寸：右 / 下 / 右下角三处可拖拽拉伸。
const CHAT_SIZE_KEY = 'atlasx.chat.size.v4'
const LEGACY_CHAT_SIZE_KEY = 'atlasx.chat.size.v3'
type ChatSize = { w: number; h: number }
function readSizes(): Record<string, ChatSize> {
  try {
    const raw = JSON.parse(localStorage.getItem(CHAT_SIZE_KEY) || '{}')
    return raw && typeof raw === 'object' ? raw : {}
  } catch {
    return {}
  }
}
function legacySize(): ChatSize | null {
  try {
    const saved = JSON.parse(localStorage.getItem(LEGACY_CHAT_SIZE_KEY) || '{}')
    if (Number.isFinite(saved.w) && Number.isFinite(saved.h)) {
      return { w: Number(saved.w), h: Number(saved.h) }
    }
  } catch { /* ignore */ }
  return null
}
const size = ref<ChatSize>({ w: 440, h: 640 })
{
  const sizes = readSizes()
  const saved = sizes[memoryKey.value]
    || (memoryKey.value === 'session' ? legacySize() : null)
  if (saved && Number.isFinite(saved.w) && Number.isFinite(saved.h)) size.value = { w: saved.w, h: saved.h }
}
function persistSize(): void {
  if (!floating.value) return
  const map = readSizes()
  map[memoryKey.value] = { w: Math.round(size.value.w), h: Math.round(size.value.h) }
  try { localStorage.setItem(CHAT_SIZE_KEY, JSON.stringify(map)) } catch { /* ignore */ }
}

// 浮窗位置：普通会话统一使用一个位置，不按会话 / 角色区分；
// 全局管理 / 全局对话各自记住自己的位置。每次拖动都落盘。
const CHAT_POS_KEY = 'atlasx.chat.pos.v3'
type ChatPos = { x: number; y: number }
function readPositions(): Record<string, ChatPos> {
  try {
    const raw = JSON.parse(localStorage.getItem(CHAT_POS_KEY) || '{}')
    return raw && typeof raw === 'object' ? raw : {}
  } catch {
    return {}
  }
}
function clampPosition(value: ChatPos): ChatPos {
  return {
    x: Math.min(Math.max(8, window.innerWidth - 120), Math.max(8, value.x)),
    y: Math.min(Math.max(8, window.innerHeight - 80), Math.max(8, value.y)),
  }
}
function defaultPosition(): ChatPos {
  const w = Math.min(size.value.w, Math.max(320, window.innerWidth - 32))
  const h = Math.min(size.value.h, Math.max(260, window.innerHeight - 40))
  const y = Math.max(8, window.innerHeight - h - 20)
  if (props.globalAgent) return { x: 22, y }
  if (props.globalConversation) {
    return { x: Math.max(12, Math.min(480, window.innerWidth * 0.35)), y }
  }
  return { x: Math.max(8, window.innerWidth - w - 20), y }
}
const pos = ref<ChatPos>(defaultPosition())
{
  const saved = readPositions()[memoryKey.value]
  if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
    pos.value = clampPosition({ x: Number(saved.x), y: Number(saved.y) })
  }
}
function persistPosition(): void {
  if (!floating.value) return
  const map = readPositions()
  map[memoryKey.value] = { x: Math.round(pos.value.x), y: Math.round(pos.value.y) }
  try { localStorage.setItem(CHAT_POS_KEY, JSON.stringify(map)) } catch { /* ignore */ }
}
watch(memoryKey, (next) => {
  const savedPos = readPositions()[next]
  if (savedPos && Number.isFinite(savedPos.x) && Number.isFinite(savedPos.y)) {
    pos.value = clampPosition({ x: Number(savedPos.x), y: Number(savedPos.y) })
  }
  const sizes = readSizes()
  const savedSize = sizes[next] || (next === 'session' ? legacySize() : null)
  if (savedSize && Number.isFinite(savedSize.w) && Number.isFinite(savedSize.h)) size.value = { w: savedSize.w, h: savedSize.h }
})

const viewport = ref({ w: window.innerWidth, h: window.innerHeight })
function updateViewport(): void {
  viewport.value = { w: window.innerWidth, h: window.innerHeight }
}

const popupRect = computed(() => {
  const w = Math.min(Math.max(1, viewport.value.w - 16), Math.max(320, size.value.w))
  const h = Math.min(Math.max(1, viewport.value.h - 16), Math.max(260, size.value.h))
  return {
    w, h,
    x: Math.max(8, Math.min(pos.value.x, viewport.value.w - w - 8)),
    y: Math.max(8, Math.min(pos.value.y, viewport.value.h - h - 8)),
  }
})
const popupStyle = computed(() => {
  if (!floating.value) return undefined
  const { w, h, x, y } = popupRect.value
  return { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px`,
    zIndex: props.globalAgent || props.globalConversation ? 104 : 102 }
})

const dragging = ref(false)
const resizing = ref(false)
type ResizeEdge = 'right' | 'top' | 'bottom' | 'corner'
let stopResize: (() => void) | undefined
function startResize(event: PointerEvent, edge: ResizeEdge): void {
  if (!floating.value || event.button !== 0) return
  stopResize?.()
  const start = popupRect.value
  const startX = event.clientX
  const startY = event.clientY
  resizing.value = true
  const onMove = (move: PointerEvent): void => {
    if (move.pointerId !== event.pointerId) return
    const dx = move.clientX - startX
    const dy = move.clientY - startY
    const maxW = viewport.value.w - start.x - 8
    const maxH = edge === 'top' ? start.y + start.h - 8 : viewport.value.h - start.y - 8
    const w = edge === 'right' || edge === 'corner' ? Math.min(maxW, Math.max(320, start.w + dx)) : start.w
    const h = edge === 'right' ? start.h : Math.min(maxH, Math.max(260, start.h + (edge === 'top' ? -dy : dy)))
    size.value = { w, h }
    pos.value = { x: start.x, y: edge === 'top' ? start.y + start.h - h : start.y }
  }
  const onEnd = (end: PointerEvent): void => {
    if (end.pointerId === event.pointerId) stopResize?.()
  }
  stopResize = () => {
    resizing.value = false
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onEnd)
    window.removeEventListener('pointercancel', onEnd)
    persistSize()
    persistPosition()
    stopResize = undefined
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onEnd)
  window.addEventListener('pointercancel', onEnd)
}
let dragStart: { x: number; y: number; baseX: number; baseY: number } | null = null

function startDrag(event: PointerEvent): void {
  if (!floating.value) return
  const target = event.target as HTMLElement | null
  if (target?.closest('button, a, input, select, textarea, .chip')) return
  dragStart = { x: event.clientX, y: event.clientY, baseX: popupRect.value.x, baseY: popupRect.value.y }
  dragging.value = true
  window.addEventListener('pointermove', onDrag)
  window.addEventListener('pointerup', endDrag)
  window.addEventListener('pointercancel', endDrag)
}

function onDrag(event: PointerEvent): void {
  if (!dragStart) return
  const maxX = Math.max(8, window.innerWidth - popupRect.value.w - 8)
  const maxY = Math.max(8, window.innerHeight - popupRect.value.h - 8)
  pos.value = {
    x: Math.min(maxX, Math.max(8, dragStart.baseX + event.clientX - dragStart.x)),
    y: Math.min(maxY, Math.max(8, dragStart.baseY + event.clientY - dragStart.y)),
  }
  persistPosition()
}

function endDrag(): void {
  dragging.value = false
  dragStart = null
  window.removeEventListener('pointermove', onDrag)
  window.removeEventListener('pointerup', endDrag)
  window.removeEventListener('pointercancel', endDrag)
  persistPosition()
}

onMounted(() => {
  window.addEventListener('resize', updateViewport)
  if (chat.messages.value.length || chat.entries.value.length) settleBottom()
  taskTimer = window.setInterval(() => {
    if (!props.globalAgent) void loadBackgroundTasks()
    // 后台任务完成或工单自动续跑会新建一条 run：没有 SSE 连接时主动接上，
    // 否则要等用户手动刷新才能看到角色继续输出的内容。
    if (!chat.streaming.value) void chat.resumeRunning()
  }, 4000)
  if (!msgsEl.value || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(() => {
    updateScrollState()
    scheduleScroll()
  })
  resizeObserver.observe(msgsEl.value)
})

onBeforeUnmount(() => {
  stopResize?.()
  endDrag()
  window.removeEventListener('resize', updateViewport)
  window.clearInterval(taskTimer)
  resizeObserver?.disconnect()
  resizeObserver = null
  if (scrollRaf) cancelAnimationFrame(scrollRaf)
  clearSettleTimers()
})

const convId = computed(() => props.conversationId)

// 会话用量 = 已落库的轮次 + 运行中这一轮的实时快照（SSE usage 事件）。
const sessionUsage = computed(() => mergeUsage(
  conversation.value?.usage_total || conversation.value?.usage,
  chat.liveUsage.value,
))
const sessionUsageView = computed(() => usageSummary(sessionUsage.value))
const sessionTone = computed(() => cacheTone(
  sessionUsageView.value.cacheRate, sessionUsageView.value.input > 0))
const usagePopover = ref(false)
const sessionUsageTitle = computed(() => {
  const view = sessionUsageView.value
  return [
    `输入 ${view.input.toLocaleString('en-US')} tokens`,
    `输出 ${view.output.toLocaleString('en-US')} tokens`,
    `生成速度 ${view.speed || '—'} t/s`,
    `缓存命中 ${view.cacheRate}%（${view.cached.toLocaleString('en-US')}）`,
    '点击查看会话用量',
  ].join('\n')
})
const conversation = computed(() => chat.conversation.value as Conversation | null)
const title = computed(() => (conversation.value ? conversationTitle(conversation.value, data.teams, data.roles) : '会话'))
watch(title, (value) => emit('title', value), { immediate: true })
const meta = computed(() => (conversation.value ? conversationMeta(conversation.value, data.teams, data.roles) : { kind: '', sub: '' }))
// 共享群组（不是会话私有参与角色）：成员统一在群组设置里维护。
const isGroupChat = computed(() => Boolean(
  conversation.value && conversationIsSharedGroup(conversation.value, data.teams),
))
const groupId = computed(() => String(conversation.value?.team_id || ''))
const currentGroup = computed(() => data.groups.find(
  (team) => team.id === groupId.value,
) || null)
const model = computed(() => {
  const codes = data.models.map((item) => item.code)
  const saved = selectedModel.value
  if (saved && (!codes.length || codes.includes(saved))) return saved
  if (codes.includes(data.activeModel)) return data.activeModel
  return codes.includes('default') ? 'default' : codes[0] || ''
})
const roleModel = computed(() => {
  const roleId = conversation.value?.role_id || props.draftRoleId
  const role = data.roles.find((item) => item.id === roleId)
  const leader = currentGroup.value?.leader
  return role?.chat_model_code || role?.model_code || (leader?.id === roleId ? leader?.model_code : '') || ''
})
const effectiveModel = computed(() => {
  const code = roleModel.value || model.value
  return code === 'default' ? data.activeModel || code : code
})
const followLabel = computed(() => (isGroupChat.value ? '跟随群组' : '跟随角色'))
// 头部只留一行：去掉与标题重复的角色名，避免「陈思 · 角色：陈思 · CTO · 董事会」折成三行。
const headerMeta = computed(() => {
  let sub = String(meta.value.sub || '')
  if (sub.startsWith('角色：')) {
    sub = sub.slice(3)
    if (title.value && sub.startsWith(`${title.value} · `)) sub = sub.slice(title.value.length + 3)
  }
  return [meta.value.kind, sub].filter(Boolean).join(' · ')
})
const isGlobalZone = computed(() => String(conversation.value?.zone || '') === GLOBAL_AGENT_ZONE)
const avatar = computed(() => {
  const conv = conversation.value
  if (!conv) return ''
  // 全局 Agent 的头像只来自它自己的角色，绝不能跟随群组头像。
  if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) {
    return data.roles.find((item) => item.id === conv.role_id)?.avatar || ''
  }
  const team = data.teams.find((item) => item.id === conv.team_id)
  if (team) return team.avatar || ''
  return data.roles.find((item) => item.id === conv.role_id)?.avatar || ''
})
const members = computed(() => {
  const conv = conversation.value
  if (!conv?.team_id || isGlobalZone.value) return []
  const team = data.teams.find((item) => item.id === conv.team_id)
  const leader = team?.leader?.id ? [team.leader] : []
  const rows = [...leader, ...(team?.members || [])]
  const seen = new Set<string>()
  return rows.filter((member) => {
    const id = String(member.id || '')
    if (!id || seen.has(id)) return false
    seen.add(id)
    return true
  })
})
const queued = computed(() => chat.queue.value.filter((item) => item.id !== chat.runId.value))
// 「跟随」时实际会使用的工作区：群组优先于角色，和创建 / 执行链路一致。
const followWorkspace = computed(() => {
  const conv = conversation.value
  if (!conv) return null
  const groupWs = String(currentGroup.value?.workspace_id || '')
  if (groupWs) return data.workspaces.find((ws) => ws.id === groupWs) || null
  const role = data.roles.find((item) => item.id === conv.role_id)
  return data.workspaces.find((ws) => ws.id === String(role?.workspace_id || '')) || null
})
const workspaceSwitching = ref(false)

async function switchConversationWorkspace(wsId: string | null): Promise<void> {
  const conv = conversation.value
  if (!conv || workspaceSwitching.value) return
  const same = wsId
    ? Boolean(conv.workspace_custom) && String(conv.workspace_id || '') === wsId
    : !conv.workspace_custom
  if (same) return
  workspaceSwitching.value = true
  try {
    const body = wsId ? { workspace_id: wsId } : { workspace_follow: true }
    const updated = await api<Conversation>(
      `/conversations/${encodeURIComponent(conv.id)}/settings`,
      { method: 'PATCH', body },
    )
    chat.conversation.value = updated
    await data.loadAll()
    const name = wsId
      ? data.workspaces.find((ws) => ws.id === wsId)?.name || wsId
      : followWorkspace.value?.name || '默认工作区'
    app.toast(wsId ? `已切换到工作区「${name}」` : `已改为跟随角色（${name}）`, 'ok', 2400)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    workspaceSwitching.value = false
  }
}

async function loadBackgroundTasks(): Promise<void> {
  if (!convId.value) {
    backgroundTaskCount.value = 0
    continueTask.value = null
    conversationWork.value = []
    return
  }
  try {
    const [out, work] = await Promise.all([
      api<{ items?: Array<{ id?: string; title?: string; status?: string; can_continue?: boolean }>; running?: number }>(
        `/conversations/${encodeURIComponent(convId.value)}/team-tasks`,
      ),
      api<{ items?: WorkItem[] }>(
        `/work-items?conversation_id=${encodeURIComponent(convId.value)}&limit=50`,
      ).catch(() => ({ items: [] })),
    ])
    backgroundTaskCount.value = Number(out.running ?? (out.items || []).filter((item) => item.status === 'running').length)
    const done = (out.items || []).find((item) => item.can_continue)
    continueTask.value = done
      ? { id: String(done.id || ''), title: String(done.title || '后台任务') }
      : null
    conversationWork.value = work.items || []
  } catch {
    backgroundTaskCount.value = 0
    continueTask.value = null
    conversationWork.value = []
  }
}

function openTaskCenter(): void {
  if (!convId.value) return
  taskCenterOpen.value = true
}

async function continueTaskNow(): Promise<void> {
  const task = continueTask.value
  if (!task?.id || !convId.value) return
  try {
    await api(
      `/conversations/${encodeURIComponent(convId.value)}/team-tasks/${encodeURIComponent(task.id)}/continue`,
      { method: 'POST' },
    )
    continueTask.value = null
    // 交回主会话 = 后台任务结果已汇总成一条主持 run，重新打开会话即可看到。
    await chat.open(convId.value)
    void loadBackgroundTasks()
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  }
}

function openTempRole(): void {
  tempForm.name = ''
  tempForm.expertise = ''
  tempForm.persona = ''
  tempOpen.value = true
}

async function createTempRole(): Promise<void> {
  const conv = conversation.value
  if (!conv || !tempForm.name.trim()) return
  try {
    const role = await api<{ id: string; name: string }>('/roles', {
      method: 'POST',
      body: {
        name: tempForm.name.trim(),
        expertise: tempForm.expertise.trim(),
        persona: tempForm.persona.trim(),
        scope: 'temporary',
        session_id: conv.id,
      },
    })
    const team = data.teams.find((item) => item.id === conv.team_id)
    const roleIds = (team?.members || []).map((member) => String(member.id || '')).filter(Boolean)
    if (!roleIds.includes(role.id) && role.id !== conv.role_id) roleIds.push(role.id)
    await api(`/conversations/${encodeURIComponent(conv.id)}/participants`, {
      method: 'PUT',
      body: { role_ids: roleIds, leader_id: conv.role_id || undefined },
    })
    await data.loadAll()
    tempOpen.value = false
    app.toast(`已创建临时角色：${role.name}`, 'ok', 3000)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

watch(convId, async (id) => {
  if (isDraft.value) {
    chat.beginDraft(props.draftRoleId || '', props.draftWorkspaceId || '', props.draftTeamId || '')
    selectedModel.value = ''
    thinking.value = ''
    perm.value = normalizePerm('once')
    await loadCommands(props.draftWorkspaceId || '')
    await nextTick()
    settleBottom()
    return
  }
  if (!id) return
  // 草稿刚发出第一条消息：窗口 key 已提升为真实会话，这里只补状态，不重新拉历史。
  if (chat.conversation.value?.id === id) {
    void loadBackgroundTasks()
    return
  }
  await chat.open(id)
  void loadBackgroundTasks()
  const conv = chat.conversation.value
  selectedModel.value = conv?.model_code || ''
  thinking.value = conv?.thinking_level || ''
  let savedPerm = ''
  try {
    savedPerm = localStorage.getItem(`atlasx.perm.${id}`) || ''
  } catch {
    savedPerm = ''
  }
  perm.value = normalizePerm(String(conv?.permission_mode || savedPerm))
  await loadCommands(conv?.workspace_id || '')
  await nextTick()
  settleBottom()
}, { immediate: true })

watch(
  [
    () => chat.messages.value.length,
    () => chat.entries.value.length,
    () => chat.tasks.value.length,
    () => chat.approvals.value.length,
    () => chat.questions.value.length,
    () => chat.streaming.value,
  ],
  async () => {
    await nextTick()
    scheduleScroll()
  },
)

watch(() => chat.loading.value, async (loading) => {
  if (loading) return
  await nextTick()
  settleBottom()
})

function updateScrollState(): void {
  const el = scroller.value
  if (!el) return
  canScroll.value = el.scrollHeight > el.clientHeight + 2
  atTop.value = el.scrollTop <= 2
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 2
  stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

function onScroll(): void {
  updateScrollState()
}

function clearSettleTimers(): void {
  for (const timer of settleTimers) window.clearTimeout(timer)
  settleTimers = []
}

function settleBottom(): void {
  stickToBottom.value = true
  scheduleScroll(true)
  clearSettleTimers()
  // Markdown/图片/代码块会在下一两帧继续撑高内容，补几次贴底。
  for (const delay of [60, 180, 360]) {
    settleTimers.push(window.setTimeout(() => scheduleScroll(), delay))
  }
}

function scheduleScroll(force = false): void {
  if (force) stickToBottom.value = true
  if (!stickToBottom.value) return
  if (scrollRaf) cancelAnimationFrame(scrollRaf)
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0
    const el = scroller.value
    if (el && stickToBottom.value) {
      el.scrollTop = el.scrollHeight
      updateScrollState()
    }
  })
}

function scrollToBottom(force = false): void {
  scheduleScroll(force)
}

function scrollToTop(): void {
  stickToBottom.value = false
  const el = scroller.value
  if (!el) return
  el.scrollTo({ top: 0, behavior: 'smooth' })
}

async function loadCommands(workspaceId: string): Promise<void> {
  try {
    const data = await api<{ commands: SlashCommand[] }>(
      `/app/commands?workspace_id=${encodeURIComponent(workspaceId)}`,
    )
    commands.value = [...ACTION_COMMANDS, ...(data.commands || [])]
  } catch {
    commands.value = ACTION_COMMANDS
  }
}

const preparingSend = ref(false)
async function onSend(): Promise<void> {
  if (preparingSend.value || chat.sending.value) return
  preparingSend.value = true
  try {
    await sendDraft()
  } finally {
    preparingSend.value = false
  }
}

async function sendDraft(): Promise<void> {
  const text = draft.value.trim()
  if (!text && !chat.pendingAttachments.value.length) return
  if (text.startsWith('/')) {
    const match = /^\/([A-Za-z0-9_-]+)(?:\s+([\s\S]*))?$/.exec(text)
    if (match) {
      const name = match[1]
      const arg = String(match[2] || '').trim()
      if (name === 'new') {
        draft.value = ''
        await createConversation()
        return
      }
      if (name === 'compact') {
        draft.value = ''
        await chat.compact()
        return
      }
      if (name === 'clear') {
        draft.value = ''
        await chat.clearContext(arg.toLowerCase() === 'current' ? 'current' : 'all')
        return
      }
      if (name === 'clean') {
        draft.value = ''
        cleanOpen.value = true
        return
      }
      if (name === 'work') {
        draft.value = ''
        await createWorkItem(arg)
        return
      }
      if (name === 'stop') {
        draft.value = ''
        await chat.stop()
        return
      }
      if (name === 'attach') {
        draft.value = ''
        document.querySelector<HTMLButtonElement>('.composer .attach')?.click()
        return
      }
      if (name === 'model') {
        draft.value = ''
        document.querySelector<HTMLSelectElement>('.composer select.mini')?.focus()
        return
      }
      if (name === 'rename') {
        draft.value = ''
        await renameConversation(arg)
        return
      }
      if (name === 'delete') {
        draft.value = ''
        await deleteConversation()
        return
      }
      if (name === 'conv') {
        draft.value = ''
        emit('close')
        return
      }
      if (name === 'settings') {
        draft.value = ''
        router.push('/settings')
        return
      }
      if (name === 'about') {
        draft.value = ''
        router.push('/about')
        return
      }
      if (name === 'theme') {
        draft.value = ''
        app.toggleTheme()
        return
      }
    }
  }
  // 草稿会话：第一条消息发出时才真正创建后端会话。
  if (isDraft.value) {
    try {
      const conv = await api<Conversation>('/conversations', {
        method: 'POST',
        body: {
          role_id: props.draftRoleId || undefined,
          workspace_id: props.draftWorkspaceId || undefined,
          team_id: props.draftTeamId || undefined,
          model_code: model.value || undefined,
          thinking_level: thinking.value || undefined,
        },
      })
      // 群聊的成员由群组维护，不再把草稿里的参与角色写进会话私有团队。
      if (!props.draftTeamId && props.draftParticipants?.length) {
        await api(`/conversations/${encodeURIComponent(conv.id)}/participants`, {
          method: 'PUT',
          body: { role_ids: props.draftParticipants, leader_id: props.draftRoleId || undefined },
        })
      }
      chat.conversation.value = conv
      emit('promoted', conv.id)
      void data.loadAll()
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
      return
    }
  }
  await pendingPreferences
  if (model.value && chat.conversation.value?.model_code !== model.value
      && !await savePreferences({ model_code: model.value })) return
  const levels = data.models.find((item) => item.code === effectiveModel.value)?.thinking_levels
  const level = levels && !levels.includes(thinking.value) ? '' : thinking.value
  const ok = await chat.send(text, { perm: perm.value, thinking: level })
  if (ok) {
    draft.value = ''
    void data.loadAll()
    scheduleScroll(true)
  }
}

function createConversation(): void {
  // 新会话也是延迟创建：先开草稿，发出第一条消息才落到后端。
  const roleId = conversation.value?.role_id || ''
  if (!roleId || roleId === 'default') {
    app.toast('先选择一个角色再新建会话', 'bad')
    return
  }
  chatWindows.openDraft(roleId, { workspaceId: conversation.value?.workspace_id || '' })
  emit('close')
}

function openGlobalConversation(id: string): void {
  emit('close')
  router.push(`/conv/${id}`)
}

function openGroupSettings(): void {
  if (!groupId.value) return
  groupInfoOpen.value = true
}

function openGroupPage(): void {
  if (!groupId.value) return
  void router.push(`/group/${encodeURIComponent(groupId.value)}`)
}

async function renameConversation(nextTitle: string): Promise<void> {
  if (!conversation.value) return
  const value = nextTitle || window.prompt(isGroupChat.value ? '群名称' : '会话标题', title.value) || ''
  if (!value.trim() || value === title.value) return
  try {
    // 群聊的标题就是群名：改名要落到群组上，所有绑定会话一起变。
    if (isGroupChat.value && groupId.value) {
      await api(`/teams/${encodeURIComponent(groupId.value)}`, {
        method: 'PATCH',
        body: { name: value.trim() },
      })
      await data.loadAll()
      app.toast('群名称已更新')
      return
    }
    await api(`/conversations/${encodeURIComponent(conversation.value.id)}`, {
      method: 'PATCH',
      body: { title: value.trim() },
    })
    await data.loadAll()
    const conv = await api<Conversation>(`/conversations/${encodeURIComponent(conversation.value.id)}`)
    chat.conversation.value = conv
    app.toast('会话已重命名')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function createWorkItem(heading: string): Promise<void> {
  const clean = heading || `来自会话「${title.value}」`
  try {
    const item = await api<WorkItem>('/work-items', {
      method: 'POST',
      body: {
        title: clean,
        body: `来自会话「${title.value}」\n\n`,
        workspace_id: conversation.value?.workspace_id || undefined,
        role_id: conversation.value?.role_id || undefined,
        // 工单只挂共享群组；会话私有参与角色不跟着工单走。
        team_id: isGroupChat.value ? groupId.value : undefined,
      },
    })
    await data.loadWork()
    app.toast(`已创建工单：${item.title || clean}`, 'ok', 3000)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

type Preferences = { model_code?: string; thinking_level?: string }
let pendingPreferences = Promise.resolve(true)
function savePreferences(body: Preferences): Promise<boolean> {
  const id = convId.value
  if (!id) return Promise.resolve(true)
  pendingPreferences = pendingPreferences.then(async () => {
    try {
      const saved = await api<Preferences>(`/conversations/${encodeURIComponent(id)}/preferences`, {
        method: 'PUT',
        body,
      })
      if (chat.conversation.value?.id === id) Object.assign(chat.conversation.value, saved)
      return true
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
      return false
    }
  })
  return pendingPreferences
}

function chooseModel(value: string): void {
  selectedModel.value = value
  void savePreferences({ model_code: value })
}
const PERM_MODES = ['auto', 'once', 'session', 'always']

function normalizePerm(value: string): string {
  const mode = String(value || '').trim()
  if (mode === 'ask') return 'once'
  return PERM_MODES.includes(mode) ? mode : 'once'
}

async function setPerm(value: string): Promise<void> {
  const mode = normalizePerm(value)
  perm.value = mode
  try {
    localStorage.setItem(`atlasx.perm.${convId.value}`, mode)
  } catch {
    /* ignore */
  }
  if (!convId.value) return
  try {
    await api(`/conversations/${encodeURIComponent(convId.value)}/permission`, {
      method: 'PUT',
      body: { mode },
    })
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function confirmClean(): Promise<void> {
  if (cleaning.value) return
  cleaning.value = true
  try {
    const ok = await chat.cleanConversation()
    if (ok) {
      cleanOpen.value = false
      void data.loadAll()
      void loadBackgroundTasks()
    }
  } finally {
    cleaning.value = false
  }
}

async function deleteConversation(): Promise<void> {
  if (!conversation.value) return
  if (!window.confirm(`删除会话「${title.value}」？历史消息会一并删除。`)) return
  try {
    await api(`/conversations/${encodeURIComponent(conversation.value.id)}`, { method: 'DELETE' })
    await data.loadAll()
    emit('close')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

function onConversationSaved(updated: Conversation): void {
  chat.conversation.value = updated
  void data.loadAll()
  settingsOpen.value = false
}

function onConversationDeleted(): void {
  settingsOpen.value = false
  emit('close')
}

async function refreshConversation(): Promise<void> {
  const id = convId.value
  if (!id) return
  try {
    const conv = await api<Conversation>(`/conversations/${encodeURIComponent(id)}`)
    chat.conversation.value = conv
  } catch {
    /* 会话可能已被删除：保留现有视图，由关闭 / 刷新兜底 */
  }
}

async function onGroupSaved(): Promise<void> {
  groupInfoOpen.value = false
  await data.loadAll()
  await refreshConversation()
}

async function onGroupDeleted(): Promise<void> {
  groupInfoOpen.value = false
  await data.loadAll()
  // 群组解散后会话会被后端解绑：重新拉一次，头部从「群聊」退回单聊。
  await refreshConversation()
}
</script>

<template>
  <div
    class="chat-panel"
    :class="floating ? 'ga-win' : presentation === 'drawer' ? 'chat-shell' : 'chat-tab-panel'"
    :style="popupStyle"
    :data-dragging="(dragging || resizing) || undefined"
  >
    <header class="win-head chat-head" @pointerdown="startDrag">
      <button class="chat-avatar-btn" type="button" title="右键：更多会话操作" aria-label="会话头像，右键更多操作"
        @pointerdown.stop @click.stop @contextmenu.prevent.stop="openAvatarMenu">
        <Avatar :name="title" :avatar="avatar" round />
      </button>
      <span class="win-title">
        <b>{{ title }}</b>
        <span :title="headerMeta">{{ headerMeta }}</span>
      </span>
      <WorkspacePicker
        v-if="!globalAgent && !isDraft"
        :model-value="conversation?.workspace_custom ? String(conversation?.workspace_id || '') : ''"
        :workspaces="data.workspaces"
        variant="chip"
        title="切换会话工作区"
        :empty-label="followLabel"
        :empty-display="followWorkspace?.name || '默认工作区'"
        :empty-hint="`当前跟随：${followWorkspace?.name || '默认工作区'}`"
        :trigger-hint="`点击切换工作区（默认${followLabel}）`"
        :disabled="workspaceSwitching"
        @update:model-value="(value) => switchConversationWorkspace(value || null)"
      />
      <span v-if="chat.streaming.value" class="run-state">
        <span class="pulse" /><span>运行中</span>
      </span>
      <el-popover
        v-if="!globalAgent && !isDraft && sessionUsageView.total"
        v-model:visible="usagePopover"
        placement="bottom-end"
        :width="332"
        :show-arrow="false"
        trigger="click"
        popper-class="usage-popover"
      >
        <template #reference>
          <button class="head-usage" type="button" :title="sessionUsageTitle">
            <span
              class="usage-ring"
              :class="sessionTone"
              :style="{ '--pct': String(Math.min(100, sessionUsageView.cacheRate)) }"
            />
            <span class="usage-total"><b>{{ compactNumber(sessionUsageView.total) }}</b> tokens</span>
            <span v-if="sessionUsageView.speed" class="usage-speed"><b>{{ sessionUsageView.speed.toFixed(1) }}</b> t/s</span>
            <span class="usage-cache" :class="sessionTone">缓存 {{ sessionUsageView.cacheRate }}%</span>
          </button>
        </template>
        <div class="usage-popover-body">
          <TokenMeter :usage="sessionUsage" :context="chat.contextTokens.value" variant="panel" />
          <button class="btn small usage-open" type="button" @click="usagePopover = false; settingsOpen = true">
            打开会话管理
          </button>
        </div>
      </el-popover>
      <div class="head-menus">
        <select class="chat-presentation input" :value="presentation || 'popup'" aria-label="会话显示形态"
          @change="emit('update:presentation', ($event.target as HTMLSelectElement).value as ChatPresentation)">
          <option value="popup">弹框</option>
          <option value="drawer">抽屉</option>
          <option value="tab">标签页</option>
        </select>
        <button v-if="isGroupChat" class="head-btn" title="群资料 / 群成员" aria-label="群资料" @click="openGroupSettings">♚</button>
        <button v-else-if="!globalAgent && !isDraft" class="head-btn" title="管理参与角色" aria-label="管理参与角色" @click="participantsOpen = true">♙</button>
        <button
          v-if="!globalAgent && !isDraft && !isGroupChat && conversation?.team_id"
          class="head-btn"
          title="新建临时角色（仅本会话）"
          aria-label="新建临时角色"
          @click="openTempRole"
        >＋</button>
        <button v-if="!isDraft" class="head-btn" title="会话管理（角色 / 参与角色）" @click="settingsOpen = true">⚙</button>
        <button v-if="!isDraft" class="head-btn task-btn" title="任务中心（执行 / 工单）" aria-label="任务中心" @click="openTaskCenter">
          ◎<span v-if="taskCenterCount" class="head-badge">{{ taskCenterCount > 99 ? '99+' : taskCenterCount }}</span>
        </button>
        <button class="head-btn close-btn" title="关闭窗口" aria-label="关闭窗口" @click="emit('close')">×</button>
      </div>
    </header>

    <div v-if="globalAgent" class="ga-tabs">
      <button class="ga-tab" :class="{ on: globalTab === 'chat' }" @click="globalTab = 'chat'">对话</button>
      <button class="ga-tab" :class="{ on: globalTab === 'inbox' }" @click="globalTab = 'inbox'; attention.load()">
        收件箱
        <span v-if="inboxCount" class="ga-tab-badge">{{ inboxCount > 99 ? '99+' : inboxCount }}</span>
      </button>
    </div>

    <div v-if="globalAgent && globalTab === 'inbox'" class="ga-inbox-body">
      <GlobalInbox full @open="openGlobalConversation" />
    </div>

    <div v-show="!globalAgent || globalTab === 'chat'" ref="scroller" class="win-body" @scroll.passive="onScroll">
      <div ref="msgsEl" class="msgs">
        <div v-if="chat.loading.value" class="chat-empty"><b>正在打开会话…</b></div>
        <div v-else-if="!chat.messages.value.length && !chat.entries.value.length && !chat.streaming.value" class="chat-empty">
          <Avatar :name="title" :avatar="avatar" round />
          <b>{{ title }}</b>
          <span>发一条消息开始，或者输入 / 命令</span>
        </div>

        <button
          v-if="hiddenMessages"
          class="earlier-btn"
          type="button"
          @click="showEarlierMessages"
        >
          查看更多
          <span>还有 {{ hiddenMessages }} 条历史消息</span>
        </button>

        <MessageItem v-for="(message, index) in visibleMessages" :key="message.id || `m-${index + hiddenMessages}`" :message="message" :data-mid="message.id || ''" @continue-task="chat.continueTask" />

        <LiveMessage
          v-if="chat.streaming.value || chat.entries.value.length"
          :entries="chat.entries.value"
          :speakers="chat.speakers.value"
          :current="chat.speakerId.value"
          :started="chat.liveStarted.value"
          :wait-seconds="chat.waitSeconds.value"
          :error="chat.error.value"
        />

        <TaskCard :items="chat.tasks.value" />

        <ApprovalCard
          v-for="approval in chat.approvals.value.filter((item) => !item.status || item.status === 'pending')"
          :key="approval.id"
          :approval="approval"
          @decide="chat.decideApproval"
        />

        <AskCard
          v-for="question in chat.questions.value"
          :key="question.id"
          :question="question"
          @answer="chat.answerQuestion"
        />
      </div>
    </div>

    <div v-if="canScroll && (!globalAgent || globalTab === 'chat')" class="scroll-rail">
      <button v-if="!atTop" class="scroll-side" type="button" title="回到顶部" aria-label="回到顶部" @click="scrollToTop">
        <svg viewBox="0 0 24 24"><path d="m6 14 6-6 6 6" /></svg>
      </button>
      <button v-if="!atBottom" class="scroll-side" type="button" title="回到底部" aria-label="回到底部" @click="scrollToBottom(true)">
        <svg viewBox="0 0 24 24"><path d="m6 10 6 6 6-6" /></svg>
      </button>
    </div>

    <div v-if="backgroundTaskCount && (!globalAgent || globalTab === 'chat')" class="queue-bar bg-running-bar">
      <div class="queue-head">后台任务 · {{ backgroundTaskCount }} 个运行中</div>
      <div class="queue-item">
        <span class="queue-text">主会话可继续发送；任务完成后会自动交回对话继续。</span>
      </div>
    </div>

    <div v-if="continueTask && (!globalAgent || globalTab === 'chat')" class="queue-bar bg-done-bar">
      <div class="queue-head">后台任务已完成 · {{ continueTask.title }}</div>
      <div class="queue-item">
        <span class="queue-text">结果已就绪。自动交回失败时，可点「继续对话」手动交回主会话。</span>
        <button class="wbtn" @click="continueTaskNow">继续对话</button>
      </div>
    </div>

    <div v-if="queued.length && (!globalAgent || globalTab === 'chat')" class="queue-bar">
      <div class="queue-head">排队中 · {{ queued.length }}</div>
      <div v-for="(item, index) in queued" :key="item.id" class="queue-item">
        <span class="queue-text">{{ item.content || item.title || '' }}</span>
        <button class="wbtn" :disabled="index === 0" title="上移" @click="chat.moveQueued(item.id, 'up')">↑</button>
        <button class="wbtn" :disabled="index === queued.length - 1" title="下移" @click="chat.moveQueued(item.id, 'down')">↓</button>
        <button class="wbtn" :disabled="!chat.streaming.value" title="插队：停止当前回复并立即执行这条" @click="chat.injectQueued(item.id)">插队</button>
        <button class="wbtn" title="取消" @click="chat.cancelQueued(item.id)">×</button>
      </div>
    </div>

    <Composer
      v-show="!globalAgent || globalTab === 'chat'"
      v-model:draft="draft"
      :streaming="chat.streaming.value"
      :sending="preparingSend || chat.sending.value"
      :models="data.models"
      :subscriptions="data.subscriptions"
      :model="model"
      :role-model="roleModel ? effectiveModel : undefined"
      :effective-model="effectiveModel"
      :perm="perm"
      :thinking="thinking"
      :members="members"
      :attachments="chat.pendingAttachments.value"
      :commands="commands"
      @send="onSend"
      @stop="chat.stop"
      @files="(files) => chat.addFiles(files, effectiveModel)"
      @remove-attachment="chat.removeAttachment"
      @update:model="chooseModel"
      @update:thinking="(value) => { thinking = value; savePreferences({ thinking_level: value }) }"
      @update:perm="setPerm"
    />

    <ChatTaskCenter
      :open="taskCenterOpen"
      :conversation-id="convId"
      :title="title"
      @close="taskCenterOpen = false; loadBackgroundTasks()"
      @changed="loadBackgroundTasks"
    />

    <ConversationSettingsDrawer
      :open="settingsOpen"
      :conversation="conversation"
      @close="settingsOpen = false"
      @saved="onConversationSaved"
      @deleted="onConversationDeleted"
    />

    <GroupSettingsDrawer
      :open="groupInfoOpen && Boolean(currentGroup)"
      :team="currentGroup"
      @close="groupInfoOpen = false"
      @saved="onGroupSaved"
      @deleted="onGroupDeleted"
      @chat="groupInfoOpen = false"
    />

    <ParticipantsDrawer
      :open="participantsOpen"
      :conversation="conversation"
      @close="participantsOpen = false"
      @saved="(updated) => { chat.conversation.value = updated; participantsOpen = false }"
    />

    <SideDrawer :open="tempOpen" title="临时角色（仅本会话）" width="520px" @close="tempOpen = false">
      <div class="field"><label>名字</label><input v-model="tempForm.name" class="input" placeholder="例如：调研员" /></div>
      <div class="field"><label>专长（一句话）</label><input v-model="tempForm.expertise" class="input" placeholder="只查资料，不改代码" /></div>
      <div class="field"><label>人格 / 工作方式</label><textarea v-model="tempForm.persona" class="input" rows="5" /></div>
      <div class="hintline">临时角色只属于当前会话；会话删除后自动清理。</div>
      <template #footer>
        <button class="btn" @click="tempOpen = false">取消</button>
        <button class="btn primary" :disabled="!tempForm.name.trim()" @click="createTempRole">创建并入群</button>
      </template>
    </SideDrawer>

    <ContextMenu
      :open="avatarMenu.open"
      :x="avatarMenu.x"
      :y="avatarMenu.y"
      :items="avatarMenuItems"
      @close="avatarMenu.open = false"
      @select="onAvatarMenu"
    />

    <Modal :open="cleanOpen" title="清空会话" width="440px" @close="cleanOpen = false">
      <div class="clean-confirm">
        <p class="clean-lead">将<strong>彻底删除</strong>本会话的上下文与历史，会话本身保留。</p>
        <ul class="clean-list">
          <li>全部历史消息与模型上下文</li>
          <li>运行记录、后台任务、目标与待办</li>
          <li>待处理审批、会话级授权、附件与文件检查点</li>
        </ul>
        <p class="clean-note">会话标题、参与角色与工作区不变。此操作不可撤销。</p>
      </div>
      <template #footer>
        <div class="modal-actions">
          <button class="btn" @click="cleanOpen = false">取消</button>
          <button class="btn danger" :disabled="cleaning" @click="confirmClean">
            {{ cleaning ? '正在清空…' : '确认清空' }}
          </button>
        </div>
      </template>
    </Modal>

    <template v-if="floating">
      <span class="rs rs-top" title="拖拽上沿调整高度" @pointerdown.stop.prevent="startResize($event, 'top')" />
      <span class="rs rs-right" title="拖拽拉伸宽度" @pointerdown.stop.prevent="startResize($event, 'right')" />
      <span class="rs rs-bottom" title="拖拽拉伸高度" @pointerdown.stop.prevent="startResize($event, 'bottom')" />
      <span class="rs rs-corner" title="拖拽拉伸" @pointerdown.stop.prevent="startResize($event, 'corner')" />
    </template>
  </div>
</template>

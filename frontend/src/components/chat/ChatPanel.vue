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
import { conversationMeta, conversationTitle, GLOBAL_AGENT_ZONE } from '@/lib/derive'
import { compactNumber } from '@/lib/format'
import { cacheTone, mergeUsage, usageSummary } from '@/lib/usage'
import { useChat } from '@/composables/useChat'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { useChatWindowsStore } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'
import { useWindowsStore } from '@/stores/windows'
import AgentCanvas from './AgentCanvas.vue'
import ApprovalCard from './ApprovalCard.vue'
import AskCard from './AskCard.vue'
import Composer from './Composer.vue'
import ConversationSettingsDrawer from './ConversationSettingsDrawer.vue'
import GlobalInbox from './GlobalInbox.vue'
import LiveMessage from './LiveMessage.vue'
import MessageItem from './MessageItem.vue'
import ParticipantsDrawer from './ParticipantsDrawer.vue'
import TaskCard from './TaskCard.vue'

const router = useRouter()
const app = useAppStore()
const data = useDataStore()
const windows = useWindowsStore()
const attention = useAttentionStore()
const chatWindows = useChatWindowsStore()
const chat = useChat()

const props = defineProps<{
  conversationId: string
  floating?: boolean
  globalAgent?: boolean
  globalConversation?: boolean
  windowIndex?: number
  initialTab?: 'chat' | 'inbox'
  draftRoleId?: string
  draftParticipants?: string[]
  draftWorkspaceId?: string
}>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'focus'): void; (e: 'promoted', conversationId: string): void }>()

const BUILTIN_COMMANDS = ACTION_COMMANDS
const isDraft = computed(() => !props.conversationId && Boolean(props.draftRoleId))

const draft = ref('')
const model = ref('')
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
const canvasOpen = ref(false)
const settingsOpen = ref(false)
const participantsOpen = ref(false)
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
    { label: '参与角色', action: 'participants', disabled: Boolean(props.globalAgent) },
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
// 已结束但还没交回主会话的后台任务：默认由后端自动交回；自动交回失败时
// 这里给出「继续对话」手动兜底。
const continueTask = ref<{ id: string; title: string } | null>(null)
let taskTimer = 0
const globalTab = ref<'chat' | 'inbox'>(props.initialTab || 'chat')
// 角标只算必须由人处理的事项：待授权 + 待回答。
const inboxCount = computed(() => Number(attention.counts.authorizations || 0))
watch(() => props.initialTab, (tab) => { if (tab) globalTab.value = tab })
const GA_POS_KEY = 'atlasx.ga.pos.v2'
const dragX = ref(0)
const dragY = ref(0)
const dragging = ref(false)
const initialLeft = Math.max(12, window.innerWidth - 460 - (props.windowIndex || 0) * 450)
if (props.floating && props.globalAgent) {
  try {
    const saved = JSON.parse(localStorage.getItem(GA_POS_KEY) || '{}')
    if (Number.isFinite(saved.x)) dragX.value = Number(saved.x)
    if (Number.isFinite(saved.y)) dragY.value = Number(saved.y)
  } catch {
    /* ignore */
  }
}
const dragStyle = computed(() => (props.floating
  ? { transform: `translate(${dragX.value}px, ${dragY.value}px)`, ...(props.globalAgent ? { zIndex: 104 } : props.globalConversation ? { left: 'min(480px, 35vw)', zIndex: 104 } : { left: `${initialLeft}px`, zIndex: 102 + (props.windowIndex || 0) }) }
  : undefined))

// 浮窗尺寸：右 / 下 / 右下角三处可拖拽拉伸；尺寸全局记忆。
const CHAT_SIZE_KEY = 'atlasx.chat.size.v3'
const size = ref({ w: 440, h: 640 })
const resizing = ref(false)
if (props.floating) {
  try {
    const saved = JSON.parse(localStorage.getItem(CHAT_SIZE_KEY) || '{}')
    if (Number.isFinite(saved.w) && Number.isFinite(saved.h)) {
      size.value = { w: Number(saved.w), h: Number(saved.h) }
    }
  } catch { /* ignore */ }
}
const resizeStyle = computed(() => (props.floating
  ? { width: `${size.value.w}px`, height: `${size.value.h}px` }
  : undefined))

let resizeStart: { x: number; y: number; w: number; h: number } | null = null
function startResize(event: PointerEvent, direction: 'right' | 'bottom' | 'corner'): void {
  if (!props.floating) return
  event.stopPropagation()
  event.preventDefault()
  resizeStart = { x: event.clientX, y: event.clientY, w: size.value.w, h: size.value.h }
  resizing.value = true
  const onMove = (move: PointerEvent): void => {
    if (!resizeStart) return
    const dx = move.clientX - resizeStart.x
    const dy = move.clientY - resizeStart.y
    const maxW = Math.max(360, window.innerWidth - 48)
    const maxH = Math.max(300, window.innerHeight - 40)
    size.value = {
      w: direction === 'bottom' ? resizeStart.w : Math.min(maxW, Math.max(360, resizeStart.w + dx)),
      h: direction === 'right' ? resizeStart.h : Math.min(maxH, Math.max(300, resizeStart.h + dy)),
    }
  }
  const onUp = (): void => {
    resizeStart = null
    resizing.value = false
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    if (props.floating) {
      try { localStorage.setItem(CHAT_SIZE_KEY, JSON.stringify(size.value)) } catch { /* ignore */ }
    }
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}
let dragStart: { x: number; y: number; baseX: number; baseY: number } | null = null

function startDrag(event: PointerEvent): void {
  if (!props.floating) return
  const target = event.target as HTMLElement | null
  if (target?.closest('button, a, input, select, textarea, .chip')) return
  dragStart = { x: event.clientX, y: event.clientY, baseX: dragX.value, baseY: dragY.value }
  dragging.value = true
  window.addEventListener('pointermove', onDrag)
  window.addEventListener('pointerup', endDrag, { once: true })
}

function onDrag(event: PointerEvent): void {
  if (!dragStart) return
  dragX.value = dragStart.baseX + event.clientX - dragStart.x
  dragY.value = dragStart.baseY + event.clientY - dragStart.y
}

function endDrag(): void {
  dragging.value = false
  dragStart = null
  window.removeEventListener('pointermove', onDrag)
  if (props.floating && props.globalAgent) {
    try {
      localStorage.setItem(GA_POS_KEY, JSON.stringify({ x: dragX.value, y: dragY.value }))
    } catch {
      /* ignore */
    }
  }
}

onMounted(() => {
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
  window.removeEventListener('pointermove', onDrag)
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
const meta = computed(() => (conversation.value ? conversationMeta(conversation.value, data.teams, data.roles) : { kind: '', sub: '' }))
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
  return data.teams.find((item) => item.id === conv.team_id)?.members || []
})
const queued = computed(() => chat.queue.value.filter((item) => item.id !== chat.runId.value))
// 「跟随角色」时实际会使用的工作区。
const followWorkspace = computed(() => {
  const conv = conversation.value
  if (!conv) return null
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

function openWorkDetail(): void {
  windows.openTask(convId.value, title.value)
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
    chat.beginDraft(props.draftRoleId || '', props.draftWorkspaceId || '')
    const role = data.roles.find((item) => item.id === props.draftRoleId)
    model.value = role?.model_code || ''
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
  model.value = conv?.model_code || ''
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

async function onSend(): Promise<void> {
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
        },
      })
      if (props.draftParticipants?.length) {
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
  const ok = await chat.send(text, { perm: perm.value, thinking: thinking.value, model: model.value })
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

async function renameConversation(nextTitle: string): Promise<void> {
  if (!conversation.value) return
  const value = nextTitle || window.prompt('会话标题', title.value) || ''
  if (!value.trim() || value === title.value) return
  try {
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
        team_id: conversation.value?.team_id || undefined,
      },
    })
    await data.loadWork()
    app.toast(`已创建工单：${item.title || clean}`, 'ok', 3000)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function savePreferences(): Promise<void> {
  if (!convId.value) return
  try {
    await api(`/conversations/${encodeURIComponent(convId.value)}/preferences`, {
      method: 'PUT',
      body: { model_code: model.value || null, thinking_level: thinking.value || null },
    })
    if (chat.conversation.value) {
      chat.conversation.value.model_code = model.value
      chat.conversation.value.thinking_level = thinking.value
    }
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
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
</script>

<template>
  <div
    class="chat-panel"
    :class="floating ? 'ga-win' : 'chat-shell'"
    :style="[dragStyle, resizeStyle]"
    :data-dragging="(dragging || resizing) || undefined"
    @pointerdown="emit('focus')"
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
        empty-label="跟随角色"
        :empty-display="followWorkspace?.name || '默认工作区'"
        :empty-hint="`当前跟随：${followWorkspace?.name || '默认工作区'}`"
        trigger-hint="点击切换工作区（默认跟随角色）"
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
        <button v-if="!globalAgent && !isDraft" class="head-btn" title="管理参与角色" aria-label="管理参与角色" @click="participantsOpen = true">♙</button>
        <button v-if="!isDraft" class="head-btn" title="会话管理（角色 / 参与角色）" @click="settingsOpen = true">⚙</button>
        <button v-if="!isDraft" class="head-btn" title="会话任务 / 工单（浮窗）" @click="openWorkDetail">
          ▤<span v-if="openWorkCount" class="head-badge">{{ openWorkCount > 99 ? '99+' : openWorkCount }}</span>
        </button>
        <button v-if="!isDraft" class="head-btn task-btn" title="执行画布 / 后台任务" @click="canvasOpen = true">
          ◎<span v-if="backgroundTaskCount" class="head-badge">{{ backgroundTaskCount > 99 ? '99+' : backgroundTaskCount }}</span>
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
      :sending="chat.sending.value"
      :models="data.models"
      :subscriptions="data.subscriptions"
      :model="model"
      :perm="perm"
      :thinking="thinking"
      :members="members"
      :attachments="chat.pendingAttachments.value"
      :commands="commands"
      @send="onSend"
      @stop="chat.stop"
      @files="chat.addFiles"
      @remove-attachment="chat.removeAttachment"
      @update:model="(value) => { model = value; savePreferences() }"
      @update:thinking="(value) => { thinking = value; savePreferences() }"
      @update:perm="setPerm"
    />

    <AgentCanvas :open="canvasOpen" :conversation-id="convId" @close="canvasOpen = false; loadBackgroundTasks()" />

    <ConversationSettingsDrawer
      :open="settingsOpen"
      :conversation="conversation"
      @close="settingsOpen = false"
      @saved="onConversationSaved"
      @deleted="onConversationDeleted"
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
      <span class="rs rs-right" title="拖拽拉伸宽度" @pointerdown.stop.prevent="startResize($event, 'right')" />
      <span class="rs rs-bottom" title="拖拽拉伸高度" @pointerdown.stop.prevent="startResize($event, 'bottom')" />
      <span class="rs rs-corner" title="拖拽拉伸" @pointerdown.stop.prevent="startResize($event, 'corner')" />
    </template>
  </div>
</template>

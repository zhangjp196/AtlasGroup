<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import Modal from '@/components/Modal.vue'
import { api } from '@/api/client'
import type { Conversation, SlashCommand, WorkItem } from '@/api/types'
import { ACTION_COMMANDS } from '@/lib/commands'
import { conversationMeta, conversationTitle, GLOBAL_AGENT_ZONE } from '@/lib/derive'
import { useChat } from '@/composables/useChat'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
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
import TaskCard from './TaskCard.vue'

const router = useRouter()
const app = useAppStore()
const data = useDataStore()
const windows = useWindowsStore()
const attention = useAttentionStore()
const chat = useChat()

const props = defineProps<{ conversationId: string; floating?: boolean; globalAgent?: boolean; initialTab?: 'chat' | 'inbox' }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const BUILTIN_COMMANDS = ACTION_COMMANDS

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
const tempOpen = ref(false)
const tempForm = reactive({ name: '', expertise: '', persona: '' })
const canvasOpen = ref(false)
const settingsOpen = ref(false)
const cleanOpen = ref(false)
const cleaning = ref(false)
const backgroundTaskCount = ref(0)
// 会话关联工单：角色 / 群组的后台任务会自动建单，这里展示 + 打开详情窗口。
const conversationWork = ref<WorkItem[]>([])
const openWorkCount = computed(() => conversationWork.value.filter(
  (item) => ['inbox', 'working', 'needs_attention'].includes(String(item.status || '')),
).length)
// 已结束但还没交回主会话的后台任务：由用户点「继续对话」触发，不自动烧 token。
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
if (props.floating) {
  try {
    const saved = JSON.parse(localStorage.getItem(GA_POS_KEY) || '{}')
    if (Number.isFinite(saved.x)) dragX.value = Number(saved.x)
    if (Number.isFinite(saved.y)) dragY.value = Number(saved.y)
  } catch {
    /* ignore */
  }
}
const dragStyle = computed(() => (props.floating
  ? { transform: `translate(${dragX.value}px, ${dragY.value}px)` }
  : undefined))
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
  if (props.floating) {
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
const conversation = computed(() => chat.conversation.value as Conversation | null)
const title = computed(() => (conversation.value ? conversationTitle(conversation.value, data.teams, data.roles) : '会话'))
const meta = computed(() => (conversation.value ? conversationMeta(conversation.value, data.teams, data.roles) : { kind: '', sub: '' }))
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
const conversationWorkspace = computed(() => data.workspaces.find((ws) => ws.id === conversation.value?.workspace_id) || null)

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
  const team = data.teams.find((item) => item.id === conv?.team_id)
  if (!conv || !team || !tempForm.name.trim()) return
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
    const members = [...(team.members || []), { id: role.id, name: role.name }]
    await api(`/teams/${encodeURIComponent(team.id)}`, { method: 'PATCH', body: { members } })
    await data.loadAll()
    tempOpen.value = false
    app.toast(`已创建临时角色：${role.name}`, 'ok', 3000)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

watch(convId, async (id) => {
  if (!id) return
  await chat.open(id)
  void loadBackgroundTasks()
  const conv = chat.conversation.value
  model.value = conv?.model_code || data.activeModel || ''
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
  const ok = await chat.send(text, { perm: perm.value, thinking: thinking.value, model: model.value })
  if (ok) {
    draft.value = ''
    scheduleScroll(true)
  }
}

async function createConversation(): Promise<void> {
  try {
    const conv = await api<Conversation>('/conversations', {
      method: 'POST',
      body: {
        role_id: conversation.value?.role_id || undefined,
        team_id: conversation.value?.team_id || undefined,
      },
    })
    await data.loadAll()
    router.push(`/conv/${conv.id}`)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
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
    :style="dragStyle"
    :data-dragging="dragging || undefined"
  >
    <header class="win-head chat-head" @pointerdown="startDrag">
      <Avatar :name="title" :avatar="avatar" :round="meta.kind !== '群聊'" />
      <span class="win-title">
        <b>{{ title }}</b>
        <span>{{ meta.kind }} · {{ meta.sub }}</span>
      </span>
      <span v-if="!globalAgent" class="chip" title="工作区由角色 / 群组绑定">
        {{ conversationWorkspace?.name || '默认工作区' }}
      </span>
      <span v-if="chat.streaming.value" class="run-state">
        <span class="pulse" /><span>运行中</span>
      </span>
      <div class="head-menus">
        <button class="head-btn" title="会话管理（角色 / 群组）" @click="settingsOpen = true">⚙</button>
        <button class="head-btn" title="会话任务 / 工单（浮窗）" @click="openWorkDetail">
          ▤<span v-if="openWorkCount" class="head-badge">{{ openWorkCount > 99 ? '99+' : openWorkCount }}</span>
        </button>
        <button class="head-btn task-btn" title="执行画布 / 后台任务" @click="canvasOpen = true">
          ◎<span v-if="backgroundTaskCount" class="head-badge">{{ backgroundTaskCount > 99 ? '99+' : backgroundTaskCount }}</span>
        </button>
        <button v-if="conversation?.team_id && !isGlobalZone" class="head-btn" title="临时角色（仅本会话）" @click="openTempRole">＋</button>
        <button v-if="!globalAgent" class="head-btn danger" title="删除会话" @click="deleteConversation">⌫</button>
        <button class="head-btn" title="返回会话列表" @click="emit('close')">×</button>
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
          <Avatar :name="title" :avatar="avatar" :round="meta.kind !== '群聊'" />
          <b>{{ title }}</b>
          <span>发一条消息开始，或者输入 / 命令</span>
        </div>

        <MessageItem v-for="(message, index) in chat.messages.value" :key="message.id || `m-${index}`" :message="message" @continue-task="chat.continueTask" />

        <LiveMessage
          v-if="chat.streaming.value || chat.entries.value.length"
          :entries="chat.entries.value"
          :role-name="chat.roleName.value"
          :role-avatar="chat.roleAvatar.value"
          :role-sub="chat.roleSub.value"
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
        <span class="queue-text">主会话可继续发送；完成后点「继续对话」再交回，不等后台跑完。</span>
      </div>
    </div>

    <div v-if="continueTask && (!globalAgent || globalTab === 'chat')" class="queue-bar bg-done-bar">
      <div class="queue-head">后台任务已完成 · {{ continueTask.title }}</div>
      <div class="queue-item">
        <span class="queue-text">结果已就绪。点「继续对话」才交回主会话，不自动消耗 token。</span>
        <button class="wbtn" @click="continueTaskNow">继续对话</button>
      </div>
    </div>

    <div v-if="queued.length && (!globalAgent || globalTab === 'chat')" class="queue-bar">
      <div class="queue-head">排队中 · {{ queued.length }}</div>
      <div v-for="(item, index) in queued" :key="item.id" class="queue-item">
        <span class="queue-text">{{ item.content || item.title || '' }}</span>
        <button class="wbtn" :disabled="index === 0" title="上移" @click="chat.moveQueued(item.id, 'up')">↑</button>
        <button class="wbtn" :disabled="index === queued.length - 1" title="下移" @click="chat.moveQueued(item.id, 'down')">↓</button>
        <button class="wbtn" title="取消" @click="chat.cancelQueued(item.id)">×</button>
      </div>
    </div>

    <Composer
      v-show="!globalAgent || globalTab === 'chat'"
      v-model:draft="draft"
      :streaming="chat.streaming.value"
      :sending="chat.sending.value"
      :models="data.models"
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

    <Modal :open="cleanOpen" title="清空会话" width="440px" @close="cleanOpen = false">
      <div class="clean-confirm">
        <p class="clean-lead">将<strong>彻底删除</strong>本会话的上下文与历史，会话本身保留。</p>
        <ul class="clean-list">
          <li>全部历史消息与模型上下文</li>
          <li>运行记录、后台任务、目标与待办</li>
          <li>待处理审批、会话级授权、附件与文件检查点</li>
        </ul>
        <p class="clean-note">会话标题、所属群组与工作区不变。此操作不可撤销。</p>
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
  </div>
</template>

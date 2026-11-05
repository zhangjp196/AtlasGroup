<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import Modal from '@/components/Modal.vue'
import { api } from '@/api/client'
import type { Conversation, SlashCommand, WorkItem } from '@/api/types'
import { ACTION_COMMANDS } from '@/lib/commands'
import { conversationMeta, conversationTitle } from '@/lib/derive'
import { useChat } from '@/composables/useChat'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { useDataStore } from '@/stores/data'
import AgentCanvas from './AgentCanvas.vue'
import ApprovalCard from './ApprovalCard.vue'
import AskCard from './AskCard.vue'
import Composer from './Composer.vue'
import GlobalInbox from './GlobalInbox.vue'
import LiveMessage from './LiveMessage.vue'
import MessageItem from './MessageItem.vue'
import TaskCard from './TaskCard.vue'

const router = useRouter()
const app = useAppStore()
const data = useDataStore()
const attention = useAttentionStore()
const chat = useChat()

const props = defineProps<{ conversationId: string; floating?: boolean; globalAgent?: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const BUILTIN_COMMANDS = ACTION_COMMANDS

const draft = ref('')
const model = ref('')
const perm = ref('ask')
const thinking = ref('')
const commands = ref<SlashCommand[]>(BUILTIN_COMMANDS)
const scroller = ref<HTMLElement | null>(null)
const msgsEl = ref<HTMLElement | null>(null)
const stickToBottom = ref(true)
let scrollRaf = 0
let resizeObserver: ResizeObserver | null = null
const bindOpen = ref(false)
const bindWorkspaceId = ref('')
const tempOpen = ref(false)
const tempForm = reactive({ name: '', expertise: '', persona: '' })
const canvasOpen = ref(false)
const globalTab = ref<'chat' | 'inbox'>('chat')
const inboxCount = computed(() => Number(attention.counts.authorizations || 0) + Number(attention.counts.questions || 0) + Number(attention.counts.unread || 0))
const GA_POS_KEY = 'atlasx.ga.pos'
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
  if (!msgsEl.value || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(() => scheduleScroll())
  resizeObserver.observe(msgsEl.value)
})

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onDrag)
  resizeObserver?.disconnect()
  resizeObserver = null
  if (scrollRaf) cancelAnimationFrame(scrollRaf)
})

const convId = computed(() => props.conversationId)
const conversation = computed(() => chat.conversation.value as Conversation | null)
const title = computed(() => (conversation.value ? conversationTitle(conversation.value, data.teams, data.roles) : '会话'))
const meta = computed(() => (conversation.value ? conversationMeta(conversation.value, data.teams, data.roles) : { kind: '', sub: '' }))
const avatar = computed(() => {
  const conv = conversation.value
  if (!conv) return ''
  const team = data.teams.find((item) => item.id === conv.team_id)
  if (team) return team.avatar || ''
  return data.roles.find((item) => item.id === conv.role_id)?.avatar || ''
})
const members = computed(() => {
  const conv = conversation.value
  if (!conv?.team_id) return []
  return data.teams.find((item) => item.id === conv.team_id)?.members || []
})
const queued = computed(() => chat.queue.value.filter((item) => item.id !== chat.runId.value))
const boundWorkspace = computed(() => data.workspaces.find((ws) => ws.id === conversation.value?.bound_workspace_id) || null)

function openBind(): void {
  bindWorkspaceId.value = conversation.value?.bound_workspace_id || ''
  bindOpen.value = true
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

async function saveBind(): Promise<void> {
  if (!conversation.value) return
  try {
    const out = await api<{ bound_workspace_id?: string }>(
      `/conversations/${encodeURIComponent(conversation.value.id)}/workspace`,
      { method: 'PATCH', body: { workspace_id: bindWorkspaceId.value || null } },
    )
    conversation.value.bound_workspace_id = out?.bound_workspace_id || ''
    const idx = data.conversations.findIndex((item) => item.id === conversation.value?.id)
    if (idx >= 0) data.conversations[idx].bound_workspace_id = out?.bound_workspace_id || ''
    bindOpen.value = false
    app.toast('工作区绑定已更新')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

watch(convId, async (id) => {
  if (!id) return
  await chat.open(id)
  const conv = chat.conversation.value
  model.value = conv?.model_code || data.activeModel || ''
  thinking.value = conv?.thinking_level || ''
  try {
    perm.value = localStorage.getItem(`atlasx.perm.${id}`) || 'ask'
  } catch {
    perm.value = 'ask'
  }
  await loadCommands(conv?.bound_workspace_id || conv?.workspace_id || '')
  await nextTick()
  scrollToBottom(true)
}, { immediate: true })

watch(
  [
    () => chat.messages.value,
    () => chat.entries.value,
    () => chat.tasks.value,
    () => chat.approvals.value,
    () => chat.questions.value,
    () => chat.streaming.value,
  ],
  async () => {
    await nextTick()
    scheduleScroll()
  },
  { deep: true },
)

function onScroll(): void {
  const el = scroller.value
  if (!el) return
  stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

function scheduleScroll(force = false): void {
  if (force) stickToBottom.value = true
  if (!stickToBottom.value) return
  if (scrollRaf) cancelAnimationFrame(scrollRaf)
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0
    const el = scroller.value
    if (el && stickToBottom.value) el.scrollTop = el.scrollHeight
  })
}

function scrollToBottom(force = false): void {
  scheduleScroll(force)
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
        bound_workspace_id: conversation.value?.bound_workspace_id || undefined,
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
        workspace_id: conversation.value?.bound_workspace_id || undefined,
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

function setPerm(value: string): void {
  perm.value = value
  try {
    localStorage.setItem(`atlasx.perm.${convId.value}`, value)
  } catch {
    /* ignore */
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
      <button v-if="!globalAgent" class="chip" title="绑定工作区" @click="openBind">
        {{ boundWorkspace?.name || '未绑定工作区' }}
      </button>
      <span v-if="chat.streaming.value" class="run-state">
        <span class="pulse" /><span>运行中</span>
      </span>
      <div class="head-menus">
        <button class="head-btn" title="执行画布" @click="canvasOpen = true">◎</button>
        <button v-if="conversation?.team_id" class="head-btn" title="临时角色（仅本会话）" @click="openTempRole">＋</button>
        <button v-if="!globalAgent" class="head-btn danger" title="删除会话" @click="deleteConversation">⌫</button>
        <button class="head-btn" title="返回会话列表" @click="emit('close')">×</button>
      </div>
    </header>

    <div v-if="globalAgent" class="ga-tabs">
      <button class="ga-tab" :class="{ on: globalTab === 'chat' }" @click="globalTab = 'chat'">对话</button>
      <button class="ga-tab" :class="{ on: globalTab === 'inbox' }" @click="globalTab = 'inbox'; attention.load()">
        消息
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

        <MessageItem v-for="(message, index) in chat.messages.value" :key="message.id || `m-${index}`" :message="message" />

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

    <AgentCanvas :open="canvasOpen" :conversation-id="convId" @close="canvasOpen = false" />

    <Modal :open="tempOpen" title="临时角色（仅本会话）" @close="tempOpen = false">
      <div class="field"><label>名字</label><input v-model="tempForm.name" class="input" placeholder="例如：调研员" /></div>
      <div class="field"><label>专长（一句话）</label><input v-model="tempForm.expertise" class="input" placeholder="只查资料，不改代码" /></div>
      <div class="field"><label>人格 / 工作方式</label><textarea v-model="tempForm.persona" class="input" rows="5" /></div>
      <div class="hintline">临时角色只属于当前会话；会话删除后自动清理。</div>
      <template #footer>
        <button class="btn" @click="tempOpen = false">取消</button>
        <button class="btn primary" :disabled="!tempForm.name.trim()" @click="createTempRole">创建并入群</button>
      </template>
    </Modal>

    <Modal :open="bindOpen" title="绑定工作区" @close="bindOpen = false">
      <div class="field">
        <label>工作区只提供文件上下文，可随时解绑</label>
        <select v-model="bindWorkspaceId" class="input">
          <option value="">不绑定（纯聊天）</option>
          <option v-for="ws in data.activeWorkspaces" :key="ws.id" :value="ws.id">{{ ws.name }}</option>
        </select>
      </div>
      <template #footer>
        <button class="btn" @click="bindOpen = false">取消</button>
        <button class="btn primary" @click="saveBind">保存</button>
      </template>
    </Modal>
  </div>
</template>

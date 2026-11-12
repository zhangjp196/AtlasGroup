<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import { api } from '@/api/client'
import type { Conversation, Role, Team } from '@/api/types'
import { conversationParts, conversationTitle, GLOBAL_AGENT_ZONE, GLOBAL_CHAT_ZONE } from '@/lib/derive'
import { shortTime } from '@/lib/format'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { useChatWindowsStore } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const app = useAppStore()
const props = defineProps<{ collapsed?: boolean }>()
const emit = defineEmits<{ (e: 'toggle'): void; (e: 'resize', width: number): void; (e: 'open-chat'): void }>()
const attention = useAttentionStore()
const chatWindows = useChatWindowsStore()
const router = useRouter()

type Tab = 'sessions' | 'roles' | 'groups'
const tab = ref<Tab>('sessions')
const sessionQuery = ref('')

const unreadMap = computed(() => {
  const map = new Map<string, number>()
  for (const item of attention.data.unread || []) {
    const id = String(item.id || '')
    if (id) map.set(id, Math.max(1, Number(item.unread_count || 1)))
  }
  return map
})
const runningMap = computed(() => {
  const map = new Map<string, number>()
  for (const item of attention.data.running || []) {
    const id = String(item.conversation_id || '')
    if (id) map.set(id, (map.get(id) || 0) + 1)
  }
  return map
})

interface SessionRow {
  conv: Conversation
  title: string
  avatar: string
  role?: Role
  unread: number
  running: number
  open: boolean
}

const sessions = computed<SessionRow[]>(() => data.conversations
  .filter((conv) => ![
    GLOBAL_AGENT_ZONE, GLOBAL_CHAT_ZONE,
  ].includes(String(conv.zone || '')))
  .filter((conv) => Number(conv.message_count || 0) > 0 || conv.last_message)
  .map((conv) => {
    const { role } = conversationParts(conv, data.teams, data.roles)
    return {
      conv,
      title: conversationTitle(conv, data.teams, data.roles),
      avatar: role?.avatar || '',
      role: role || undefined,
      unread: unreadMap.value.get(conv.id) || Number(conv.unread_count || 0),
      running: runningMap.value.get(conv.id) || Number(conv.running_count || 0),
      open: chatWindows.isOpen(conv.id),
    }
  })
  .sort((a, b) => String(b.conv.updated_at || '').localeCompare(String(a.conv.updated_at || ''))))

const filteredSessions = computed(() => {
  const q = sessionQuery.value.trim().toLowerCase()
  if (!q) return sessions.value
  return sessions.value.filter((row) => [
    row.title, row.role?.name, row.role?.title, row.conv.last_message,
  ].join(' ').toLowerCase().includes(q))
})

const roles = computed(() => data.persistentRoles.filter((role) => role.status !== 'offline'))
const roleQuery = ref('')
const filteredRoles = computed(() => {
  const q = roleQuery.value.trim().toLowerCase()
  if (!q) return roles.value
  return roles.value.filter((role) => [
    role.name, role.title, role.department, role.expertise, role.model_code,
  ].join(' ').toLowerCase().includes(q))
})

const groupQuery = ref('')
const filteredGroups = computed(() => {
  const q = groupQuery.value.trim().toLowerCase()
  return data.groups.filter((group) => [group.name, group.description, group.leader?.name]
    .join(' ').toLowerCase().includes(q))
})

async function openGroupChat(group: Team): Promise<void> {
  if (await chatWindows.openGroup(group)) emit('open-chat')
}
function roleSessionCount(roleId: string): number {
  return sessions.value.filter((row) => row.role?.id === roleId).length
}

function roleModel(role: Role): string {
  if (!role.model_code) return '跟随系统模型'
  return data.models.find((item) => item.code === role.model_code)?.name || role.model_code
}

function openSession(conv: Conversation): void {
  chatWindows.openConversation(conv.id)
  emit('open-chat')
}

async function openRoleChat(role: Role): Promise<void> {
  menu.open = false
  const existing = sessions.value.find((row) => row.role?.id === role.id)
  if (existing) {
    openSession(existing.conv)
    return
  }
  chatWindows.openDraft(role.id)
  emit('open-chat')
}

function openRoleDetail(role: Role): void {
  menu.open = false
  void router.push(`/role/${encodeURIComponent(role.id)}`)
}

async function setOffline(role: Role): Promise<void> {
  menu.open = false
  try {
    await api(`/roles/${encodeURIComponent(role.id)}`, { method: 'PATCH', body: { status: 'offline' } })
    await data.loadAll()
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function markRead(conv: Conversation): Promise<void> {
  menu.open = false
  try {
    await api(`/conversations/${encodeURIComponent(conv.id)}/read`, { method: 'POST' })
    await data.loadAll()
    await attention.load()
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function removeSession(conv: Conversation): Promise<void> {
  menu.open = false
  const title = conversationTitle(conv, data.teams, data.roles)
  if (!window.confirm(`删除会话「${title}」？历史消息会一并删除。`)) return
  try {
    await api(`/conversations/${encodeURIComponent(conv.id)}`, { method: 'DELETE' })
    chatWindows.closeConversation(conv.id)
    await data.loadAll()
    await attention.load()
    app.toast('会话已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

const menu = reactive<{
  open: boolean
  x: number
  y: number
  kind: 'session' | 'role' | ''
  conv: Conversation | null
  role: Role | null
}>({ open: false, x: 0, y: 0, kind: '', conv: null, role: null })

const menuItems = computed<ContextMenuItem[]>(() => {
  if (menu.kind === 'session' && menu.conv) {
    const unread = unreadMap.value.get(menu.conv.id) || Number(menu.conv.unread_count || 0)
    return [
      { label: '打开会话', action: 'open' },
      { label: '标记已读', action: 'read', disabled: unread <= 0 },
      { label: '', action: '', separator: true },
      { label: '删除会话', action: 'delete', danger: true },
    ]
  }
  if (menu.kind === 'role' && menu.role) {
    return [
      { label: '打开聊天', action: 'chat', hint: '有会话就打开，没有就新建' },
      { label: '新建草稿', action: 'draft', hint: '发出第一条消息才创建' },
      { label: '角色详情', action: 'detail' },
      { label: '', action: '', separator: true },
      { label: '设为离线', action: 'offline', danger: true },
    ]
  }
  return []
})

function openSessionMenu(event: MouseEvent, conv: Conversation): void {
  menu.kind = 'session'
  menu.conv = conv
  menu.role = null
  menu.x = event.clientX
  menu.y = event.clientY
  menu.open = true
}

function openRoleMenu(event: MouseEvent, role: Role): void {
  menu.kind = 'role'
  menu.role = role
  menu.conv = null
  menu.x = event.clientX
  menu.y = event.clientY
  menu.open = true
}

async function onMenu(action: string): Promise<void> {
  if (menu.kind === 'session' && menu.conv) {
    if (action === 'open') openSession(menu.conv)
    else if (action === 'read') await markRead(menu.conv)
    else if (action === 'delete') await removeSession(menu.conv)
    return
  }
  if (menu.kind === 'role' && menu.role) {
    if (action === 'chat') await openRoleChat(menu.role)
    else if (action === 'draft') {
      menu.open = false
      chatWindows.openDraft(menu.role.id)
    } else if (action === 'detail') openRoleDetail(menu.role)
    else if (action === 'offline') await setOffline(menu.role)
  }
}

function closeMenu(): void { menu.open = false }

let dockStart: { x: number; w: number } | null = null
function startResize(event: PointerEvent): void {
  const aside = (event.currentTarget as HTMLElement | null)?.closest<HTMLElement>('.side-dock')
  if (!aside) return
  dockStart = { x: event.clientX, w: aside.getBoundingClientRect().width }
  const onMove = (move: PointerEvent): void => {
    if (!dockStart) return
    const next = Math.min(720, Math.max(320, dockStart.w + (dockStart.x - move.clientX)))
    emit('resize', next)
  }
  const onUp = (): void => {
    dockStart = null
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}
onMounted(() => document.addEventListener('click', closeMenu))
onBeforeUnmount(() => document.removeEventListener('click', closeMenu))
</script>

<template>
  <aside class="side-dock" :class="{ mini: collapsed }" aria-label="协作中心">
    <span class="rs rs-left" title="拖拽调整宽度" @pointerdown.prevent="startResize" />
    <div v-if="!collapsed" class="dock-title"><h2>协作中心</h2></div>
    <header class="dock-head">
      <button class="dock-toggle" type="button" :title="collapsed ? '展开侧栏' : '收起侧栏'"
        :aria-label="collapsed ? '展开侧栏' : '收起侧栏'" @click="emit('toggle')">
        <svg viewBox="0 0 24 24"><path :d="collapsed ? 'm9 6 6 6-6 6' : 'm15 6-6 6 6 6'" /></svg>
      </button>
      <div v-if="!collapsed" class="dock-tabs" role="tablist" aria-label="侧栏切换">
        <button class="dock-tab" :class="{ on: tab === 'sessions' }" type="button" role="tab"
          :aria-selected="tab === 'sessions'" @click="tab = 'sessions'">
          会话 <span>{{ sessions.length }}</span>
        </button>
        <button class="dock-tab" :class="{ on: tab === 'roles' }" type="button" role="tab"
          :aria-selected="tab === 'roles'" @click="tab = 'roles'">
          角色 <span>{{ roles.length }}</span>
        </button>
        <button class="dock-tab" :class="{ on: tab === 'groups' }" type="button" role="tab"
          :aria-selected="tab === 'groups'" @click="tab = 'groups'">
          群组 <span>{{ data.groups.length }}</span>
        </button>
      </div>
      <button v-if="!collapsed" class="dock-manage" type="button" title="打开管理页面" @click="router.push(tab === 'groups' ? '/group' : tab === 'roles' ? '/role' : '/conv')">管理</button>
    </header>

    <div v-if="!collapsed" class="dock-search">
      <input v-if="tab === 'sessions'" v-model="sessionQuery" class="input" aria-label="搜索会话" placeholder="搜索会话…" />
      <input v-else-if="tab === 'roles'" v-model="roleQuery" class="input" aria-label="搜索在线角色" placeholder="搜索在线角色…" />
      <input v-else v-model="groupQuery" class="input" aria-label="搜索群组" placeholder="搜索群组…" />
    </div>

    <div v-if="tab === 'sessions'" class="dock-list">
      <button v-for="row in filteredSessions" :key="row.conv.id" class="dock-row session-row"
        :class="{ open: row.open, unread: row.unread > 0 }" type="button"
        :title="`${row.title} · 点击聊天，右键更多操作`" @click="openSession(row.conv)"
        @contextmenu.prevent.stop="openSessionMenu($event, row.conv)">
        <span class="dock-avatar">
          <Avatar :name="row.title" :avatar="row.avatar" round :size="38" />
          <i v-if="row.role && row.role.status !== 'offline'" class="presence-dot" />
          <i v-if="row.unread" class="mini-unread">{{ row.unread > 9 ? '9+' : row.unread }}</i>
        </span>
        <span class="dock-main">
          <span class="dock-line">
            <b>{{ row.title }}</b>
            <time>{{ shortTime(row.conv.updated_at) }}</time>
          </span>
          <span class="dock-sub">
            <span class="dock-role">{{ row.role?.name || '未指定角色' }}</span>
            <span v-if="row.role?.title" class="dock-sep">·</span>
            <span v-if="row.role?.title">{{ row.role.title }}</span>
          </span>
          <span class="dock-preview">{{ row.conv.last_message || '还没有消息' }}</span>
          <span class="dock-badges">
            <span v-if="row.running" class="dock-badge run">运行 {{ row.running }}</span>
            <span v-if="row.open" class="dock-badge open">已打开</span>
            <span v-if="row.unread" class="dock-badge unread">{{ row.unread > 99 ? '99+' : row.unread }}</span>
          </span>
        </span>
      </button>
      <div v-if="!filteredSessions.length" class="dock-empty">
        {{ sessions.length ? '没有匹配的会话' : '还没有会话。点击角色开始聊天，发出第一条消息后才会创建会话。' }}
      </div>
    </div>

    <div v-else-if="tab === 'roles'" class="dock-list">
      <button v-for="role in filteredRoles" :key="role.id" class="dock-row role-row" type="button"
        :title="`${role.name} · 点击聊天，右键更多操作`" @click="openRoleChat(role)"
        @contextmenu.prevent.stop="openRoleMenu($event, role)">
        <span class="dock-avatar">
          <Avatar :name="role.name" :avatar="role.avatar" round :size="38" />
          <i class="presence-dot" />
        </span>
        <span class="dock-main">
          <span class="dock-line">
            <b>{{ role.name }}</b>
            <span class="dock-count">{{ roleSessionCount(role.id) }} 个会话</span>
          </span>
          <span class="dock-sub">
            <span>{{ role.title || role.expertise || '未填写岗位' }}</span>
            <template v-if="role.department"><span class="dock-sep">·</span><span>{{ role.department }}</span></template>
          </span>
          <span class="dock-preview">{{ role.expertise || '还没有补充专长' }}</span>
          <span class="dock-badges">
            <span class="dock-badge model">{{ roleModel(role) }}</span>
            <span v-if="role.scope === 'temporary'" class="dock-badge">临时</span>
          </span>
        </span>
      </button>
      <div v-if="!filteredRoles.length" class="dock-empty">
        {{ roles.length ? '没有匹配的在线角色' : '没有在线角色。到「组织与角色」里把角色设为在线。' }}
      </div>
    </div>

    <div v-else class="dock-list">
      <button v-for="group in filteredGroups" :key="group.id" class="dock-row" type="button"
        :title="`打开群聊：${group.name}`" @click="openGroupChat(group)">
        <Avatar :name="group.name" :avatar="group.avatar" :size="38" />
        <span class="dock-main">
          <span class="dock-line"><b>{{ group.name }}</b><span class="dock-count">{{ group.members?.length || 0 }} 位成员</span></span>
          <span v-if="group.description" class="dock-preview">{{ group.description }}</span>
        </span>
      </button>
      <div v-if="!filteredGroups.length" class="dock-empty">{{ data.groups.length ? '没有匹配的群组' : '还没有群组' }}</div>
    </div>

    <ContextMenu
      :open="menu.open"
      :x="menu.x"
      :y="menu.y"
      :items="menuItems"
      @close="menu.open = false"
      @select="onMenu"
    />
  </aside>
</template>

<style scoped>
.side-dock { position: relative; min-width: 0; height: 100%; display: flex; flex-direction: column; border-left: 1px solid var(--line-soft); background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 4%, var(--panel)), var(--panel)); box-shadow: -12px 0 32px color-mix(in srgb, #000 18%, transparent); }
.side-dock.mini { width: 64px; }

.rs { position: absolute; z-index: 8; touch-action: none; }
.rs-left { left: -3px; top: 0; bottom: 0; width: 8px; cursor: ew-resize; opacity: 0; }
.rs-left:hover, .rs-left:active { opacity: .9; background: color-mix(in srgb, var(--accent) 28%, transparent); border-radius: 4px; }

.dock-head { flex: none; display: flex; align-items: center; gap: 6px; padding: 12px 12px 8px; }
.dock-toggle { flex: none; width: 28px; height: 28px; border: 1px solid var(--line); border-radius: 9px; background: var(--bg-2); color: var(--dim); display: grid; place-items: center; cursor: pointer; }
.dock-toggle:hover { color: var(--text); border-color: var(--line-strong); }
.dock-toggle svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.dock-tabs { display: flex; gap: 4px; flex: 1; min-width: 0; background: var(--bg-2); border: 1px solid var(--line-soft); padding: 2px; border-radius: 10px; }
.dock-tab { flex: 1; white-space: nowrap; border: 0; border-radius: 8px; background: transparent; color: var(--dim); padding: 6px 6px; font-size: 12px; cursor: pointer; transition: background .15s, color .15s; }
.dock-tab span { color: var(--faint); font-size: 10px; margin-left: 3px; }
.dock-tab:hover { color: var(--text); }
.dock-tab.on { background: var(--panel); color: var(--text); font-weight: 650; box-shadow: 0 1px 6px color-mix(in srgb, #000 12%, transparent); }
.dock-manage { flex: none; border: 0; background: transparent; color: var(--dim); font-size: 11px; cursor: pointer; padding: 4px 6px; border-radius: 8px; }
.dock-manage:hover { color: var(--text); background: var(--bg-2); }

.dock-search { flex: none; padding: 4px 12px 10px; }
.dock-search .input { width: 100%; background: var(--bg-2); border-color: var(--line-soft); }
.dock-list { flex: 1; min-height: 0; overflow: auto; padding: 2px 8px 18px; }
.dock-row { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 8px; border: 1px solid transparent; border-radius: 13px; background: transparent; color: var(--text); text-align: left; cursor: pointer; transition: background .15s, border-color .15s, transform .15s; }
.dock-row:hover { background: color-mix(in srgb, var(--accent) 5%, var(--bg-2)); transform: translateY(-1px); }
.dock-row:active { transform: none; }
.dock-row.open { background: color-mix(in srgb, var(--accent) 9%, var(--panel)); border-color: color-mix(in srgb, var(--accent) 22%, var(--line)); }
.dock-avatar { position: relative; flex: none; }
.presence-dot { position: absolute; right: 0; bottom: 0; width: 10px; height: 10px; border: 2px solid var(--panel); border-radius: 50%; background: #27b66c; box-shadow: 0 0 0 2px color-mix(in srgb, #27b66c 18%, transparent); }
.dock-main { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 2px; }
.dock-line { display: flex; align-items: center; gap: 8px; min-width: 0; }
.dock-line b { font-size: 13px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dock-line time, .dock-count { margin-left: auto; flex: none; color: var(--faint); font-size: 10.5px; }
.dock-sub { display: flex; align-items: center; gap: 4px; color: var(--dim); font-size: 11px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.dock-role { color: var(--text); }
.dock-sep { color: var(--faint); }
.dock-preview { color: var(--faint); font-size: 11.5px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.dock-badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
.dock-badge { font-size: 10px; padding: 1px 6px; border-radius: 999px; color: var(--dim); background: var(--bg-2); border: 1px solid transparent; }
.dock-badge.unread { margin-left: auto; color: #fff; background: var(--danger); border-color: transparent; box-shadow: 0 0 0 2px color-mix(in srgb, var(--danger) 20%, transparent); }
.dock-badge.run { color: #b45309; background: #b4530914; }
.dock-badge.open { color: #15803d; background: #15803d14; }
.dock-badge.model { color: var(--dim); }
.dock-empty { padding: 34px 16px; text-align: center; color: var(--faint); font-size: 12px; line-height: 1.7; }

.side-dock.mini .dock-list { padding: 8px 10px; }
.side-dock.mini .dock-row { justify-content: center; padding: 6px 0; border-radius: 12px; }
.side-dock.mini .dock-main { display: none; }
.side-dock.mini .dock-empty { font-size: 10px; padding: 18px 6px; }
.side-dock.mini .dock-avatar :deep(.avatar) { width: 34px !important; height: 34px !important; }
.side-dock.mini .presence-dot { right: -2px; bottom: -2px; }
</style>
<style scoped>
.dock-avatar .mini-unread { display: none; }
.side-dock.mini .dock-avatar .mini-unread {
  display: grid; place-items: center;
  position: absolute; left: -3px; top: -3px;
  min-width: 15px; height: 15px; padding: 0 3px;
  border-radius: 8px; background: var(--danger); color: #fff;
  font-size: 9px; line-height: 1; box-shadow: 0 0 0 2px var(--panel);
}
</style>

<style scoped>
.side-dock { background: var(--bg-2); box-shadow: none; }
.dock-title { height: 54px; display: flex; align-items: center; padding: 0 16px; border-bottom: 1px solid var(--line-soft); }
.dock-title h2 { margin: 0; font-size: 13px; letter-spacing: .025em; }
.dock-head { padding: 14px 12px 9px; }
.dock-search { padding-bottom: 12px; }
.dock-search .input { height: 35px; background: var(--bg); }
.dock-list { padding-inline: 12px; }
.dock-row { padding: 11px 9px; border-color: var(--line-soft); margin-bottom: 7px; background: var(--panel); border-radius: 10px; }
.dock-row:hover { transform: none; border-color: var(--line-strong); }
.dock-tab.on { color: var(--accent-ink); background: color-mix(in srgb, var(--accent) 12%, var(--panel)); }
.dock-badge.run { color: var(--warn); background: color-mix(in srgb, var(--warn) 12%, var(--panel)); }
.dock-badge.open { color: var(--ok); background: color-mix(in srgb, var(--ok) 12%, var(--panel)); }
.side-dock.mini .dock-head { justify-content: center; }
@media (max-width: 840px) {
  .dock-title { display: none; }
  .rs, .dock-toggle { display: none; }
  .dock-tab span { display: none; }
}
</style>

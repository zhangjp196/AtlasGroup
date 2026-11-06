<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import CreateDialog from '@/components/CreateDialog.vue'
import ConversationSettingsDrawer from '@/components/chat/ConversationSettingsDrawer.vue'
import PageHeader from '@/components/PageHeader.vue'
import { api } from '@/api/client'
import type { Conversation } from '@/api/types'
import { conversationMeta, conversationParts, conversationTitle, GLOBAL_AGENT_ZONE } from '@/lib/derive'
import { shortTime } from '@/lib/format'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const app = useAppStore()
const router = useRouter()
const createOpen = ref(false)
const settingsOpen = ref(false)
const manageConv = ref<Conversation | null>(null)
const menu = reactive({ open: false, x: 0, y: 0, conv: null as Conversation | null })

// 会话卡片筛选：工作区标签 + 单聊 / 群聊 + 未读 / 运行中 + 关键词。
const filterWorkspace = ref('')
const filterKind = ref<'all' | 'single' | 'group'>('all')
const unreadOnly = ref(false)
const runningOnly = ref(false)
const keyword = ref('')

const menuItems = computed<ContextMenuItem[]>(() => {
  const conv = menu.conv
  return [
    { label: '打开会话', action: 'open' },
    { label: '重命名', action: 'rename' },
    { label: '会话管理（角色 / 群组）', action: 'manage' },
    { label: '', action: '', separator: true },
    { label: '复制 / 分支', action: 'fork', hint: '保留当前上下文' },
    { label: '标记已读', action: 'read', disabled: !conv?.unread },
    { label: '', action: '', separator: true },
    { label: '删除会话', action: 'delete', danger: true },
  ]
})

function openMenu(event: MouseEvent, conv: Conversation): void {
  menu.conv = conv
  menu.x = event.clientX
  menu.y = event.clientY
  menu.open = true
}

function openMenuAt(event: MouseEvent, conv: Conversation): void {
  const rect = (event.currentTarget as HTMLElement | null)?.getBoundingClientRect()
  openMenu({ clientX: rect ? rect.right - 8 : event.clientX, clientY: rect ? rect.bottom + 4 : event.clientY } as MouseEvent, conv)
}

async function onMenu(action: string): Promise<void> {
  const conv = menu.conv
  if (!conv) return
  if (action === 'open') return openConversation(conv.id)
  if (action === 'manage') {
    manageConv.value = conv
    settingsOpen.value = true
    return
  }
  if (action === 'rename') {
    const value = window.prompt('会话标题', conversationTitle(conv, data.teams, data.roles)) || ''
    if (!value.trim()) return
    try {
      await api(`/conversations/${encodeURIComponent(conv.id)}`, { method: 'PATCH', body: { title: value.trim() } })
      await data.loadAll()
      app.toast('会话已重命名')
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
    return
  }
  if (action === 'fork') {
    try {
      const next = await api<Conversation>(`/conversations/${encodeURIComponent(conv.id)}/fork`, {
        method: 'POST',
        body: {},
      })
      await data.loadAll()
      app.toast('已创建分支会话')
      void router.push(`/conv/${encodeURIComponent(next.id)}`)
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
    return
  }
  if (action === 'read') {
    try {
      await api(`/conversations/${encodeURIComponent(conv.id)}/read`, { method: 'POST' })
      await data.loadAll()
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
    return
  }
  if (action === 'delete') {
    if (!window.confirm(`删除会话「${conversationTitle(conv, data.teams, data.roles)}」？历史消息会一并删除。`)) return
    try {
      await api(`/conversations/${encodeURIComponent(conv.id)}`, { method: 'DELETE' })
      await data.loadAll()
      app.toast('会话已删除')
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
  }
}

function onManaged(updated: Conversation): void {
  manageConv.value = updated
  settingsOpen.value = false
  void data.loadAll()
}

function onManagedDeleted(): void {
  settingsOpen.value = false
  manageConv.value = null
  void data.loadAll()
}

const conversations = computed(() => {
  const list = data.conversations.filter((conv) => String(conv.zone || '') !== GLOBAL_AGENT_ZONE)
  return [...list].sort((a, b) => {
    const aLive = a.active || a.unread ? 1 : 0
    const bLive = b.active || b.unread ? 1 : 0
    if (aLive !== bLive) return bLive - aLive
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
  })
})

// 只列出会话实际用到的工作区，避免筛选器里塞满空工作区。
const workspaceOptions = computed(() => {
  const used = new Set(conversations.value.map((conv) => String(conv.workspace_id || '')))
  return data.workspaces
    .filter((ws) => used.has(ws.id))
    .sort((a, b) => Number(a.archived) - Number(b.archived) || a.name.localeCompare(b.name))
})

const filteredConversations = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  return conversations.value.filter((conv) => {
    const { team, role, isGroup } = conversationParts(conv, data.teams, data.roles)
    if (filterWorkspace.value && String(conv.workspace_id || '') !== filterWorkspace.value) return false
    if (filterKind.value === 'single' && isGroup) return false
    if (filterKind.value === 'group' && !isGroup) return false
    if (unreadOnly.value && !Number(conv.unread_count || 0)) return false
    if (runningOnly.value && !Number(conv.running_count || 0)) return false
    if (q) {
      const lead = isGroup ? team?.leader : role
      const haystack = [
        conversationTitle(conv, data.teams, data.roles),
        conv.last_message || '',
        team?.name || '',
        role?.name || '',
        lead?.title || '',
        workspaceName(conv.workspace_id),
      ].join(' ').toLowerCase()
      if (!haystack.includes(q)) return false
    }
    return true
  })
})

const filtersActive = computed(() => Boolean(
  filterWorkspace.value || filterKind.value !== 'all' || unreadOnly.value || runningOnly.value || keyword.value.trim(),
))

function clearFilters(): void {
  filterWorkspace.value = ''
  filterKind.value = 'all'
  unreadOnly.value = false
  runningOnly.value = false
  keyword.value = ''
}

function workspaceOf(conv: (typeof data.conversations)[number]): string {
  return workspaceName(conv.workspace_id)
}

function workspaceName(id?: string): string {
  const wsId = String(id || '')
  return data.workspaces.find((ws) => ws.id === wsId)?.name || '默认工作区'
}

// 会话列表显示在办工单数（角色 / 群组后台任务自动建单）。
const openWorkByConv = computed(() => {
  const map = new Map<string, number>()
  for (const item of data.workItems) {
    if (!['inbox', 'working', 'needs_attention'].includes(String(item.status || ''))) continue
    const cid = String(item.conversation_id || '')
    if (cid) map.set(cid, (map.get(cid) || 0) + 1)
  }
  return map
})

function titleOf(conv: (typeof data.conversations)[number]): string {
  return conversationTitle(conv, data.teams, data.roles)
}

function avatarOf(conv: (typeof data.conversations)[number]): string {
  const { team, role } = conversationParts(conv, data.teams, data.roles)
  return team?.avatar || role?.avatar || ''
}

function chipsOf(conv: (typeof data.conversations)[number]): string[] {
  const { team, role, isGroup } = conversationParts(conv, data.teams, data.roles)
  const chips = [isGroup ? '群聊' : '单聊']
  const lead = isGroup ? team?.leader : role
  if (lead?.title) chips.push(lead.title)
  if (lead?.department) chips.push(lead.department)
  return chips
}

function openConversation(id: string): void {
  void router.push(`/conv/${encodeURIComponent(id)}`)
}

function onCreated(_kind: string, id: string): void {
  createOpen.value = false
  void router.push(`/conv/${encodeURIComponent(id)}`)
}
</script>

<template>
  <PageHeader
    section="会话"
    title="全部会话"
    :chip="filtersActive ? `${filteredConversations.length} / ${conversations.length} 个` : `${conversations.length} 个`"
  >
    <el-button type="primary" @click="createOpen = true">新建会话</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="conv-filters">
      <input v-model="keyword" class="input conv-search" placeholder="搜索会话 / 角色 / 工作区…" />
      <select v-model="filterWorkspace" class="input conv-ws-filter" title="按工作区筛选">
        <option value="">全部工作区</option>
        <option v-for="ws in workspaceOptions" :key="ws.id" :value="ws.id">
          {{ ws.name }}{{ ws.archived ? '（已归档）' : '' }}
        </option>
      </select>
      <div class="seg" role="group" aria-label="会话类型">
        <button class="seg-btn" :class="{ on: filterKind === 'all' }" type="button" @click="filterKind = 'all'">全部</button>
        <button class="seg-btn" :class="{ on: filterKind === 'single' }" type="button" @click="filterKind = 'single'">单聊</button>
        <button class="seg-btn" :class="{ on: filterKind === 'group' }" type="button" @click="filterKind = 'group'">群聊</button>
      </div>
      <button class="work-filter" :class="{ on: unreadOnly }" type="button" @click="unreadOnly = !unreadOnly">未读</button>
      <button class="work-filter" :class="{ on: runningOnly }" type="button" @click="runningOnly = !runningOnly">运行中</button>
      <button v-if="filtersActive" class="mini-link" type="button" @click="clearFilters">清除筛选</button>
      <span class="conv-filter-count">{{ filteredConversations.length }} 个</span>
    </div>

    <div class="tile-grid">
      <article
        v-for="conv in filteredConversations"
        :key="conv.id"
        class="tile-card conv-tile"
        :class="{ unread: conv.unread }"
        @click="openConversation(conv.id)"
        @contextmenu.prevent.stop="openMenu($event, conv)"
      >
        <div class="tile-head">
          <Avatar :name="titleOf(conv)" :avatar="avatarOf(conv)" :round="!conv.team_id" />
          <div class="tile-title">
            <b>{{ titleOf(conv) }}</b>
            <span>{{ conversationMeta(conv, data.teams, data.roles).kind }} · {{ conversationMeta(conv, data.teams, data.roles).sub }}</span>
          </div>
          <button class="tile-more" type="button" title="更多操作" aria-label="更多操作" @click.stop="openMenuAt($event, conv)">⋯</button>
          <span v-if="Number(conv.running_count || 0) > 0" class="conv-badge run" title="进行中">
            {{ Number(conv.running_count) > 99 ? '99+' : conv.running_count }}
          </span>
          <span v-if="Number(conv.unread_count || 0) > 0" class="conv-badge unread" title="未读消息">
            {{ Number(conv.unread_count) > 99 ? '99+' : conv.unread_count }}
          </span>
        </div>
        <p class="tile-preview">{{ conv.last_message || '还没有消息' }}</p>
        <div class="tile-chips">
          <span class="chip ws-chip" :title="`工作区：${workspaceOf(conv)}`">{{ workspaceOf(conv) }}</span>
          <span v-if="openWorkByConv.get(conv.id)" class="chip work-chip">{{ openWorkByConv.get(conv.id) }} 工单</span>
          <span v-for="chip in chipsOf(conv)" :key="chip" class="chip">{{ chip }}</span>
        </div>
        <div class="tile-foot">
          <span>{{ shortTime(conv.updated_at) }}</span>
          <span v-if="Number(conv.unread_count || 0) > 0">{{ conv.unread_count }} 条未读</span>
          <span v-else-if="Number(conv.running_count || 0) > 0">进行中</span>
          <span v-else>打开会话</span>
        </div>
      </article>
    </div>
    <div v-if="!conversations.length" class="empty">还没有会话<br />点右上角「新建会话」开始</div>
    <div v-else-if="!filteredConversations.length" class="empty">
      没有符合筛选条件的会话
      <br />
      <button class="mini-link" type="button" @click="clearFilters">清除筛选</button>
    </div>
  </div>

  <CreateDialog :open="createOpen" kind="conv" @close="createOpen = false" @created="onCreated" />
  <ContextMenu
    :open="menu.open"
    :x="menu.x"
    :y="menu.y"
    :items="menuItems"
    @close="menu.open = false"
    @select="onMenu"
  />
  <ConversationSettingsDrawer
    :open="settingsOpen"
    :conversation="manageConv"
    @close="settingsOpen = false"
    @saved="onManaged"
    @deleted="onManagedDeleted"
  />
</template>

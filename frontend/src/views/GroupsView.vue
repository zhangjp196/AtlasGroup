<script setup lang="ts">
import { ElButton, ElInput } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import CreateDialog from '@/components/CreateDialog.vue'
import GroupSettingsDrawer from '@/components/GroupSettingsDrawer.vue'
import PageHeader from '@/components/PageHeader.vue'
import { api } from '@/api/client'
import type { Conversation, Team } from '@/api/types'
import { modeLabel } from '@/lib/derive'
import { compactNumber } from '@/lib/format'
import { usageSummary } from '@/lib/usage'
import { useAppStore } from '@/stores/app'
import { useChatWindowsStore } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const app = useAppStore()
const route = useRoute()
const router = useRouter()
const chatWindows = useChatWindowsStore()

const createOpen = ref(false)
const query = ref('')
const menu = reactive({ open: false, x: 0, y: 0, team: null as Team | null })

const menuItems = computed<ContextMenuItem[]>(() => [
  { label: '打开群资料', action: 'detail' },
  { label: '打开群聊', action: 'chat', disabled: !(menu.team?.members || []).length },
  { label: '新建群工单', action: 'work', disabled: !(menu.team?.members || []).length },
  { label: '复制群名', action: 'copy' },
  { label: '', action: '', separator: true },
  { label: '解散群聊', action: 'delete', danger: true },
])

function openMenu(event: MouseEvent, team: Team): void {
  menu.team = team
  menu.x = event.clientX
  menu.y = event.clientY
  menu.open = true
}

async function onMenu(action: string): Promise<void> {
  const team = menu.team
  if (!team) return
  if (action === 'detail') return openDetail(team)
  if (action === 'chat') return openChat(team)
  if (action === 'work') return openWork(team)
  if (action === 'copy') {
    try {
      await navigator.clipboard.writeText(team.name)
      app.toast('已复制群名')
    } catch {
      app.toast('复制失败', 'bad')
    }
    return
  }
  if (action === 'delete') {
    if (!window.confirm(`解散群组「${team.name}」？绑定会话会退回单聊，角色库不受影响。`)) return
    try {
      await api(`/teams/${encodeURIComponent(team.id)}`, { method: 'DELETE' })
      await data.loadAll()
      app.toast('群组已解散')
      if (drawerId.value === team.id) closeDetail()
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
  }
}

const teams = computed(() => {
  const q = query.value.trim().toLowerCase()
  return data.groups.filter((team) => !q
    || `${team.name} ${team.description} ${(team.members || []).map((m) => m.name).join(' ')}`
      .toLowerCase().includes(q))
})

// 群组卡片上的在办工单数：工单本身已经带 team_id。
const openWorkByTeam = computed(() => {
  const map = new Map<string, number>()
  for (const item of data.workItems) {
    if (!['inbox', 'working', 'needs_attention'].includes(String(item.status || ''))) continue
    const tid = String(item.team_id || '')
    if (tid) map.set(tid, (map.get(tid) || 0) + 1)
  }
  return map
})

// 绑定该群的会话：群组是 live 实体，可能同时服务多条会话。
const conversationsByTeam = computed(() => {
  const map = new Map<string, Conversation[]>()
  for (const conv of data.conversations) {
    if (['global-agent', 'global-chat'].includes(String(conv.zone || ''))) continue
    const tid = String(conv.team_id || '')
    if (!tid) continue
    const list = map.get(tid) || []
    list.push(conv)
    map.set(tid, list)
  }
  return map
})

function boundConversations(teamId: string): Conversation[] {
  return conversationsByTeam.value.get(teamId) || []
}

function teamTokens(teamId: string): number {
  return boundConversations(teamId).reduce(
    (sum, conv) => sum + usageSummary(conv.usage).total, 0,
  )
}

const drawerId = computed(() => String(route.params.id || ''))
const selectedTeam = computed(() => data.groups.find((team) => team.id === drawerId.value) || null)

watch(drawerId, (id) => {
  // 首屏还没拉到列表时不要误报「不存在」。
  if (id && !selectedTeam.value && !data.loading && data.groups.length) {
    app.toast('群组不存在或已删除', 'bad')
    void router.replace('/group')
  }
})

function teamById(id: string): Team | null {
  return data.groups.find((team) => team.id === id) || null
}

function workspaceName(id?: string): string {
  const ws = data.workspaces.find((item) => item.id === String(id || ''))
  return ws?.name || ''
}

function openDetail(team: Team): void {
  void router.push(`/group/${encodeURIComponent(team.id)}`)
}

function closeDetail(): void {
  if (drawerId.value) void router.push('/group')
}

function onCreated(kind: string, id: string): void {
  createOpen.value = false
  if (kind === 'team') void router.push(`/group/${encodeURIComponent(id)}`)
}

function openChat(team: Team): void {
  void chatWindows.openGroup(team)
}

function openDrawerChat(id: string): void {
  const team = teamById(id)
  if (team) void openChat(team)
}

function openWork(team: Team): void {
  void router.push(`/work?team_id=${encodeURIComponent(team.id)}`)
}

function onSaved(): void {
  closeDetail()
}
</script>

<template>
  <PageHeader section="群组" title="群组" chip="可复用角色集合 · 可绑定多个会话">
    <el-button type="primary" @click="createOpen = true">＋ 新建群组</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="group-hero">
      <el-input v-model="query" placeholder="搜索群名 / 简介 / 成员" clearable style="max-width:300px" />
      <span class="group-mode-hint">点击群卡打开群聊；右键查看群资料 / 群工单。</span>
    </div>

    <div class="group-gallery">
      <article
        v-for="team in teams"
        :key="team.id"
        class="group-card"
        role="button"
        tabindex="0"
        title="点击打开群聊；右键打开群资料 / 群工单"
        @click="openChat(team)"
        @keydown.enter.prevent="openChat(team)"
        @keydown.space.prevent="openChat(team)"
        @contextmenu.prevent.stop="openMenu($event, team)"
      >
        <div class="group-card-head">
          <Avatar :name="team.name" :avatar="team.avatar" :size="38" />
          <h3 class="group-card-name">
            {{ team.name }}
            <span class="tag grp">{{ modeLabel(team.mode) }}</span>
          </h3>
        </div>
        <p class="group-card-count">
          {{ (team.members || []).length }} 个角色
          <span v-if="boundConversations(team.id).length"> · {{ boundConversations(team.id).length }} 条群聊</span>
          <span v-if="openWorkByTeam.get(team.id)"> · {{ openWorkByTeam.get(team.id) }} 个工单在办</span>
          <span v-if="teamTokens(team.id)"> · {{ compactNumber(teamTokens(team.id)) }} tokens</span>
        </p>
        <div class="chips group-name-chips">
          <span v-if="workspaceName(team.workspace_id)" class="chip">工作区：{{ workspaceName(team.workspace_id) }}</span>
          <span v-for="member in (team.members || []).slice(0, 8)" :key="member.id || member.name" class="chip">
            {{ member.name }}
          </span>
          <span v-if="!(team.members || []).length" class="chip">暂无成员</span>
        </div>
        <div class="chips group-card-actions">
          <button class="btn" type="button" @click.stop="openChat(team)">打开群聊</button>
          <button class="btn" type="button" @click.stop="openDetail(team)">编辑</button>
        </div>
      </article>
    </div>
    <div v-if="!teams.length" class="empty">还没有群组，点右上角新建。</div>
  </div>

  <CreateDialog :open="createOpen" kind="team" @close="createOpen = false" @created="onCreated" />

  <GroupSettingsDrawer
    :open="Boolean(selectedTeam)"
    :team="selectedTeam"
    @close="closeDetail"
    @saved="onSaved"
    @deleted="onSaved"
    @chat="openDrawerChat"
  />

  <ContextMenu
    :open="menu.open"
    :x="menu.x"
    :y="menu.y"
    :items="menuItems"
    @close="menu.open = false"
    @select="onMenu"
  />
</template>

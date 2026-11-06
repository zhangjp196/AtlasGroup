<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import CreateDialog from '@/components/CreateDialog.vue'
import GroupSettingsDrawer from '@/components/GroupSettingsDrawer.vue'
import PageHeader from '@/components/PageHeader.vue'
import Avatar from '@/components/Avatar.vue'
import { api } from '@/api/client'
import type { Conversation, Team } from '@/api/types'
import { GLOBAL_AGENT_ZONE, modeLabel } from '@/lib/derive'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const app = useAppStore()
const route = useRoute()
const router = useRouter()
const createOpen = ref(false)
const query = ref('')
const menu = reactive({ open: false, x: 0, y: 0, team: null as Team | null })

const menuItems = computed<ContextMenuItem[]>(() => [
  { label: '打开群资料', action: 'detail' },
  { label: '打开群聊', action: 'chat', disabled: !(menu.team?.members || []).length },
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
    if (!window.confirm(`解散群组「${team.name}」？绑定会话会退回单聊。`)) return
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
  return data.teams.filter((team) => !q || `${team.name} ${team.description}`.toLowerCase().includes(q))
})

// 群组面板显示各自的在办工单数（后台任务会自动建单）。
const openWorkByTeam = computed(() => {
  const map = new Map<string, number>()
  for (const item of data.workItems) {
    if (!['inbox', 'working', 'needs_attention'].includes(String(item.status || ''))) continue
    const tid = String(item.team_id || '')
    if (tid) map.set(tid, (map.get(tid) || 0) + 1)
  }
  return map
})

const drawerId = computed(() => String(route.params.id || ''))
const selectedTeam = computed(() => data.teams.find((team) => team.id === drawerId.value) || null)

watch(drawerId, (id) => {
  if (id && !selectedTeam.value && !data.loading) {
    app.toast('群组不存在或已删除', 'bad')
    void router.replace('/group')
  }
})

function teamById(id: string): Team | null {
  return data.teams.find((team) => team.id === id) || null
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

function onCreated(_kind: string, id: string): void {
  createOpen.value = false
  void router.push(`/group/${encodeURIComponent(id)}`)
}

async function openChat(team: Team): Promise<void> {
  if (!(team.members || []).length) {
    app.toast('这个群组还没有成员，先打开群组加角色', 'bad')
    return
  }
  // 全局 Agent 的会话绝不是群聊：旧数据若把它绑到了群组，不能复用。
  const existing = data.conversations.find(
    (conv) => conv.team_id === team.id && String(conv.zone || '') !== GLOBAL_AGENT_ZONE,
  )
  if (existing) {
    void router.push(`/conv/${existing.id}`)
    return
  }
  try {
    const conv = await api<Conversation>('/conversations', {
      method: 'POST',
      body: { team_id: team.id, model_code: team.leader?.model_code || undefined },
    })
    await data.loadAll()
    void router.push(`/conv/${conv.id}`)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

function openDrawerChat(id: string): void {
  const team = teamById(id)
  if (team) void openChat(team)
}

function onSaved(): void {
  closeDetail()
}
</script>

<template>
  <PageHeader section="群组" title="群组" chip="角色集合 · 绑定工作区">
    <el-button type="primary" @click="createOpen = true">＋ 新建群组</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="group-hero">
      <el-input v-model="query" placeholder="搜索群名 / 简介" clearable style="max-width:240px" />
    </div>

    <div class="group-gallery">
      <article
        v-for="team in teams"
        :key="team.id"
        class="group-card"
        @click="openDetail(team)"
        @contextmenu.prevent.stop="openMenu($event, team)"
      >
        <div class="group-card-head">
          <Avatar :name="team.name" :avatar="team.avatar" :size="38" />
          <h3 class="group-card-name">
            {{ team.name }}
            <span class="tag grp">{{ modeLabel(team.mode) }}</span>
          </h3>
        </div>
        <p class="group-card-count">{{ (team.members || []).length }} 个角色<span v-if="openWorkByTeam.get(team.id)"> · {{ openWorkByTeam.get(team.id) }} 个工单在办</span></p>
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

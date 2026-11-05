<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CreateDialog from '@/components/CreateDialog.vue'
import GroupSettingsDrawer from '@/components/GroupSettingsDrawer.vue'
import PageHeader from '@/components/PageHeader.vue'
import { api } from '@/api/client'
import type { Conversation, Team } from '@/api/types'
import { modeLabel } from '@/lib/derive'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const app = useAppStore()
const route = useRoute()
const router = useRouter()
const createOpen = ref(false)
const query = ref('')

const teams = computed(() => {
  const q = query.value.trim().toLowerCase()
  return data.teams.filter((team) => !q || `${team.name} ${team.description}`.toLowerCase().includes(q))
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
  const existing = data.conversations.find((conv) => conv.team_id === team.id)
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
  <PageHeader section="群组" title="群组" chip="不绑定工作区">
    <el-button type="primary" @click="createOpen = true">＋ 新建群组</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="group-hero">
      <div>
        <h1>群组</h1>
        <p class="page-desc">群组 = 一组角色 + 协作方式，没有 workspace_id。需要文件上下文时，给某个会话单独绑定。</p>
      </div>
      <el-input v-model="query" placeholder="搜索群名 / 简介" clearable style="max-width:240px" />
    </div>

    <div class="group-gallery">
      <article
        v-for="team in teams"
        :key="team.id"
        class="group-card"
        @click="openDetail(team)"
      >
        <h3 class="group-card-name">
          {{ team.name }}
          <span class="tag grp">{{ modeLabel(team.mode) }}</span>
        </h3>
        <p class="group-card-count">{{ (team.members || []).length }} 个角色</p>
        <div class="chips group-name-chips">
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
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import CreateDialog from '@/components/CreateDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import { conversationMeta, conversationParts, conversationTitle, GLOBAL_AGENT_ZONE } from '@/lib/derive'
import { shortTime } from '@/lib/format'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const router = useRouter()
const createOpen = ref(false)

const conversations = computed(() => {
  const list = data.conversations.filter((conv) => String(conv.zone || '') !== GLOBAL_AGENT_ZONE)
  return [...list].sort((a, b) => {
    const aLive = a.active || a.unread ? 1 : 0
    const bLive = b.active || b.unread ? 1 : 0
    if (aLive !== bLive) return bLive - aLive
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
  })
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
  <PageHeader section="会话" title="全部会话" :chip="`${conversations.length} 个`">
    <el-button type="primary" @click="createOpen = true">新建会话</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="hero">
      <div>
        <h1>会话</h1>
        <p>点开一个会话直接在画布里聊天；右键可以重命名、绑定工作区或删除。</p>
      </div>
    </div>

    <div class="tile-grid">
      <article
        v-for="conv in conversations"
        :key="conv.id"
        class="tile-card conv-tile"
        :class="{ unread: conv.unread }"
        @click="openConversation(conv.id)"
      >
        <div class="tile-head">
          <Avatar :name="titleOf(conv)" :avatar="avatarOf(conv)" :round="!conv.team_id" />
          <div class="tile-title">
            <b>{{ titleOf(conv) }}</b>
            <span>{{ conversationMeta(conv, data.teams, data.roles).kind }} · {{ conversationMeta(conv, data.teams, data.roles).sub }}</span>
          </div>
          <span v-if="conv.active" class="dot run" title="运行中" />
          <span v-else-if="conv.unread" class="dot" title="未读" />
        </div>
        <p class="tile-preview">{{ conv.last_message || '还没有消息' }}</p>
        <div class="tile-chips">
          <span v-for="chip in chipsOf(conv)" :key="chip" class="chip">{{ chip }}</span>
        </div>
        <div class="tile-foot">
          <span>{{ shortTime(conv.updated_at) }}</span>
          <span>{{ conv.unread ? '未读' : '打开会话' }}</span>
        </div>
      </article>
    </div>
    <div v-if="!conversations.length" class="empty">还没有会话<br />点右上角「新建会话」开始</div>
  </div>

  <CreateDialog :open="createOpen" kind="conv" @close="createOpen = false" @created="onCreated" />
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppRail from '@/components/AppRail.vue'
import ChatPanel from '@/components/chat/ChatPanel.vue'
import { api } from '@/api/client'
import type { Conversation } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { useDataStore } from '@/stores/data'

const app = useAppStore()
const data = useDataStore()
const attention = useAttentionStore()
const route = useRoute()
const router = useRouter()

const globalAgentId = ref('')

const chatId = computed(() => {
  if (route.meta.section !== 'conv') return ''
  return String(route.params.id || '')
})

async function openGlobalAgent(): Promise<void> {
  const existing = data.conversations.find((conv) => String(conv.zone || '') === 'global-agent')
  if (existing) {
    globalAgentId.value = existing.id
    return
  }
  try {
    const conv = await api<Conversation>('/conversations', {
      method: 'POST',
      body: { zone: 'global-agent' },
    })
    await data.loadAll()
    globalAgentId.value = conv.id
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

onMounted(async () => {
  await app.initTheme()
  await Promise.all([data.loadAll(), data.loadWork()])
  attention.start()
  window.setInterval(() => {
    if (!document.hidden) void data.loadWork()
  }, 15_000)
})
</script>

<template>
  <div class="app flat">
    <AppRail />
    <main class="canvas">
      <router-view />
    </main>
  </div>

  <ChatPanel
    v-if="chatId"
    :conversation-id="chatId"
    @close="router.push('/conv')"
  />

  <ChatPanel
    v-if="globalAgentId"
    :conversation-id="globalAgentId"
    floating
    global-agent
    @close="globalAgentId = ''"
  />

  <button
    v-if="!globalAgentId"
    class="ga-fab"
    :title="`全局Agent · 待授权 ${attention.counts.authorizations || 0} · 未读 ${attention.counts.unread || 0} · 运行 ${attention.counts.running || 0}`"
    aria-label="全局Agent"
    @click="openGlobalAgent"
  >
    <svg viewBox="0 0 24 24"><rect x="4" y="8" width="16" height="11" rx="4" /><path d="M12 4v4M9 13h.01M15 13h.01M9 16h6" /></svg>
    <span v-if="Number(attention.counts.authorizations || 0) > 0" class="ga-badge ga-auth">{{ Number(attention.counts.authorizations) > 99 ? '99+' : attention.counts.authorizations }}</span>
    <span v-if="Number(attention.counts.unread || 0) > 0" class="ga-badge ga-note">{{ Number(attention.counts.unread) > 99 ? '99+' : attention.counts.unread }}</span>
  </button>

</template>

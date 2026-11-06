<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import AppRail from '@/components/AppRail.vue'
import NoticeStack from '@/components/NoticeStack.vue'
import StartupSplash from '@/components/StartupSplash.vue'
import TaskWindow from '@/components/TaskWindow.vue'
import { api } from '@/api/client'
import type { Conversation } from '@/api/types'
import { playSound } from '@/lib/sound'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { useDataStore } from '@/stores/data'
import { useNoticeStore } from '@/stores/notice'
import { useWindowsStore } from '@/stores/windows'

const app = useAppStore()
const data = useDataStore()
const attention = useAttentionStore()
const notice = useNoticeStore()
const windows = useWindowsStore()
const route = useRoute()
const router = useRouter()

const ChatPanel = defineAsyncComponent(() => import('@/components/chat/ChatPanel.vue'))
const globalAgentId = ref('')
const globalAgentTab = ref<'chat' | 'inbox'>('chat')
const ensuringGlobalAgent = ref(false)
// 启动动画：默认固定展示 5s（可点击跳过），后续界面在它背后并行加载。
// `?boot=<ms>` 仅用于截图 / 联调时延长动画，不传就是 5s。
const bootDuration = (() => {
  try {
    const raw = new URLSearchParams(window.location.search).get('boot')
    const n = Number(raw)
    return Number.isFinite(n) && n > 0 ? Math.min(60000, n) : 5000
  } catch {
    return 5000
  }
})()
const booting = ref(true)
// 需要人处理的入口：待授权 + 待回答（未读 / 运行中只展示，不顶在角标上）。
const pendingCount = computed(() => Number(attention.counts.authorizations || 0))
const lastCounts = { approvals: 0, questions: 0, unread: 0, running: 0 }
let attentionReady = false

const chatId = computed(() => {
  if (route.meta.section !== 'conv') return ''
  return String(route.params.id || '')
})

function openGlobalAgentInbox(): void {
  globalAgentTab.value = 'inbox'
  void openGlobalAgent()
}

function pushApprovalNotice(count: number): void {
  notice.push({
    title: '需要审批的权限',
    message: `${count} 个工具调用等待授权`,
    tone: 'warning',
    action: 'global-approvals',
    actionLabel: '去处理',
    duration: 10000,
  })
}

async function openGlobalAgent(): Promise<void> {
  if (globalAgentId.value || ensuringGlobalAgent.value) return
  ensuringGlobalAgent.value = true
  try {
    // 后端负责唯一性：不存在就创建，归档就恢复，重复就收敛。
    // 不能只看本地会话列表（只取最近 80 条），否则会反复新建全局 Agent。
    const conv = await api<Conversation>('/app/global-agent', { method: 'POST' })
    await data.loadAll()
    globalAgentId.value = conv.id
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    ensuringGlobalAgent.value = false
  }
}

watch(() => attention.counts, (counts) => {
  const next = {
    approvals: Number(counts.approvals || 0),
    questions: Number(counts.questions || 0),
    unread: Number(counts.unread || 0),
    running: Number(counts.running || 0),
  }
  if (!attentionReady) {
    attentionReady = true
    Object.assign(lastCounts, next)
    if (next.approvals > 0) {
      playSound('approval')
      pushApprovalNotice(next.approvals)
      openGlobalAgentInbox()
    }
    return
  }
  if (next.approvals > lastCounts.approvals) {
    playSound('approval')
    pushApprovalNotice(next.approvals)
    openGlobalAgentInbox()
  }
  if (next.questions > lastCounts.questions) playSound('question')
  if (next.unread > lastCounts.unread) {
    playSound('message')
    notice.push({ title: '新消息', message: '有新的助手回复', tone: 'info', duration: 10000 })
  }
  if (next.running < lastCounts.running) {
    playSound('done')
    notice.push({ title: '后台运行完成', message: '有运行已经结束', tone: 'success', duration: 10000 })
  }
  Object.assign(lastCounts, next)
}, { deep: true })

function onNoticeAction(action: string): void {
  if (action === 'global-approvals') openGlobalAgentInbox()
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
  <StartupSplash v-if="booting" :duration="bootDuration" @done="booting = false" />

  <el-config-provider :locale="zhCn">
  <div class="app flat">
    <AppRail :pending-count="pendingCount" :global-agent-open="Boolean(globalAgentId)" @global-agent="openGlobalAgent" />
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
    :initial-tab="globalAgentTab"
    floating
    global-agent
    @close="globalAgentId = ''; globalAgentTab = 'chat'"
  />

  <TaskWindow
    v-for="(win, index) in windows.tasks"
    :key="win.id"
    :id="win.id"
    :conversation-id="win.conversationId"
    :title="win.title"
    :x="win.x"
    :y="win.y"
    :order="index"
  />

  <NoticeStack @action="onNoticeAction" />
  </el-config-provider>
</template>

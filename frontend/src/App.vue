<script setup lang="ts">
import { ElConfigProvider } from 'element-plus'
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import AppRail from '@/components/AppRail.vue'
import NoticeStack from '@/components/NoticeStack.vue'
import QuickSearch from '@/components/QuickSearch.vue'
import WorkspaceTabs from '@/components/WorkspaceTabs.vue'
import OnlineRoles from '@/components/OnlineRoles.vue'
import StartupSplash from '@/components/StartupSplash.vue'
import { api } from '@/api/client'
import type { Conversation } from '@/api/types'
import { playSound } from '@/lib/sound'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { conversationTitle } from '@/lib/derive'
import { useChatWindowsStore, type ChatPresentation } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'
import { useNoticeStore } from '@/stores/notice'

const app = useAppStore()
const data = useDataStore()
const attention = useAttentionStore()
const chatWindows = useChatWindowsStore()
const notice = useNoticeStore()
const route = useRoute()
const router = useRouter()

const ChatPanel = defineAsyncComponent(() => import('@/components/chat/ChatPanel.vue'))
const globalAgentId = ref('')
const globalChatId = ref('')
const ensuringGlobalChat = ref(false)
const globalAgentTab = ref<'chat' | 'inbox'>('chat')
const ensuringGlobalAgent = ref(false)

const routeTabs = ref<Array<{ key: string; label: string; path: string }>>([])
const routeTabKey = computed(() => `page:${route.meta.section || 'work'}`)
const globalPanels = computed(() => [
  { key: 'global-agent', id: globalAgentId.value, label: '全局管理', agent: true },
  { key: 'global-chat', id: globalChatId.value, label: '全局对话', agent: false },
].filter((panel) => panel.id))
const globalModes = ref<Record<string, ChatPresentation>>({})
const workspaceTabs = computed(() => [
  ...routeTabs.value.map((tab) => ({ ...tab, closable: routeTabs.value.length > 1 })),
  ...chatWindows.windows.filter((item) => item.presentation === 'tab').map((item) => {
    const conv = data.conversations.find((entry) => entry.id === item.conversationId)
    const group = data.groups.find((entry) => entry.id === item.teamId)
    const role = data.roles.find((entry) => entry.id === item.roleId)
    return { key: item.key, label: conv ? conversationTitle(conv, data.teams, data.roles)
      : group?.name || role?.name || item.title || '会话', closable: true }
  }),
  ...globalPanels.value.filter((panel) => globalModes.value[panel.key] === 'tab')
    .map((panel) => ({ ...panel, closable: true })),
])
const activeWorkspaceTab = computed(() => chatWindows.activeTab || routeTabKey.value)

watch(() => route.fullPath, (path) => {
  if (!route.meta.section) return
  chatWindows.activeTab = ''
  const key = routeTabKey.value
  const hit = routeTabs.value.find((tab) => tab.key === key)
  if (hit) hit.path = path
  else routeTabs.value.push({ key, label: String(route.meta.title), path })
}, { immediate: true, flush: 'sync' })

function selectWorkspaceTab(key: string): void {
  const tab = routeTabs.value.find((item) => item.key === key)
  if (tab) {
    chatWindows.activeTab = ''
    if (route.fullPath !== tab.path) void router.push(tab.path)
  } else chatWindows.activeTab = key
}

function closeGlobalPanel(key: string): void {
  if (chatWindows.activeTab === key) chatWindows.activeTab = ''
  if (key === 'global-agent') {
    globalAgentId.value = ''
    globalAgentTab.value = 'chat'
  } else globalChatId.value = ''
}

function setGlobalPresentation(key: string, id: string, mode: ChatPresentation): void {
  globalModes.value[key] = mode
  chatWindows.rememberPresentation(id, mode)
  if (mode === 'tab') chatWindows.activeTab = key
  else if (chatWindows.activeTab === key) chatWindows.activeTab = ''
}

function closeWorkspaceTab(key: string): void {
  const index = routeTabs.value.findIndex((item) => item.key === key)
  if (index >= 0) {
    if (routeTabs.value.length < 2) return
    const wasCurrent = routeTabKey.value === key
    routeTabs.value.splice(index, 1)
    const replacement = routeTabs.value[Math.max(0, index - 1)]
    if (wasCurrent && replacement) void router.push(replacement.path)
  } else if (globalPanels.value.some((item) => item.key === key)) closeGlobalPanel(key)
  else chatWindows.close(key)
}


// 右侧会话 / 角色栏：默认 320px，可拖拽、可收起；状态跨刷新保留。
const DOCK_KEY = 'atlasx.dock.v1'
const dockCollapsed = ref(false)
const dockWidth = ref(320)
try {
  const saved = JSON.parse(localStorage.getItem(DOCK_KEY) || '{}')
  if (typeof saved.collapsed === 'boolean') dockCollapsed.value = saved.collapsed
  if (Number.isFinite(saved.width)) dockWidth.value = Math.min(720, Math.max(320, Number(saved.width)))
} catch { /* ignore */ }
const mobileDockOpen = ref(false)
watch(() => route.fullPath, () => { mobileDockOpen.value = false })
const dockStyleWidth = computed(() => (dockCollapsed.value ? '64px' : `${dockWidth.value}px`))

function saveDockState(): void {
  try {
    localStorage.setItem(DOCK_KEY, JSON.stringify({ collapsed: dockCollapsed.value, width: dockWidth.value }))
  } catch { /* ignore */ }
}

function toggleDock(): void {
  dockCollapsed.value = !dockCollapsed.value
  saveDockState()
}

function resizeDock(width: number): void {
  dockCollapsed.value = false
  dockWidth.value = Math.min(720, Math.max(320, Math.round(width)))
  saveDockState()
}
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
// 首屏数据（工作区 / 会话 / 角色 / 团队 / 模型 / 工单）就绪后提前关掉启动动画。
const bootReady = ref(false)
// 需要人处理的入口：待授权 + 待回答（未读 / 运行中只展示，不顶在角标上）。
const pendingCount = computed(() => Number(attention.counts.authorizations || 0))
const lastCounts = { approvals: 0, questions: 0 }
let attentionReady = false

const chatId = computed(() => {
  if (route.meta.section !== 'conv') return ''
  return String(route.params.id || '')
})

watch(chatId, (id) => {
  if (!id) return
  chatWindows.openConversation(id)
  const active = chatWindows.activeTab
  void router.replace('/conv').then(() => {
    if (active && chatWindows.windows.some((item) => item.key === active && item.presentation === 'tab')) {
      chatWindows.focus(active)
    }
  })
}, { immediate: true })

// 隐藏的标签继续接收消息，切回标签时再记为已读。
function openConversationIds(): Set<string> {
  const active = chatWindows.activeTab
  const ids = new Set<string>()
  for (const item of chatWindows.windows) {
    if (item.conversationId && (item.presentation !== 'tab' || item.key === active)) ids.add(item.conversationId)
  }
  for (const panel of globalPanels.value) {
    if (globalModes.value[panel.key] !== 'tab' || panel.key === active) ids.add(panel.id)
  }
  return ids
}

const markingRead = new Set<string>()
async function markOpenRead(id: string): Promise<void> {
  if (!id || markingRead.has(id)) return
  markingRead.add(id)
  try {
    await api(`/conversations/${encodeURIComponent(id)}/read`, { method: 'POST' })
    await attention.load()
  } catch {
    /* 已读失败不影响当前会话 */
  } finally {
    markingRead.delete(id)
  }
}

watch(
  () => [...openConversationIds()].sort().join(','),
  () => {
    for (const id of openConversationIds()) void markOpenRead(id)
  },
  { immediate: true },
)

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
    duration: 2500,
  })
}

async function openGlobalAgent(): Promise<void> {
  if (globalAgentId.value) {
    if (globalModes.value['global-agent'] === 'tab') chatWindows.activeTab = 'global-agent'
    return
  }
  if (ensuringGlobalAgent.value) return
  ensuringGlobalAgent.value = true
  try {
    // 后端负责唯一性：不存在就创建，归档就恢复，重复就收敛。
    // 不能只看本地会话列表（只取最近 80 条），否则会反复新建全局 Agent。
    const conv = await api<Conversation>('/app/global-agent', { method: 'POST' })
    await data.loadAll()
    await chatWindows.loadLayout(conv.id)
    globalAgentId.value = conv.id
    globalModes.value['global-agent'] = chatWindows.presentationFor(conv.id)
    if (globalModes.value['global-agent'] === 'tab') chatWindows.activeTab = 'global-agent'
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    ensuringGlobalAgent.value = false
  }
}

async function openGlobalChat(): Promise<void> {
  if (globalChatId.value) {
    if (globalModes.value['global-chat'] === 'tab') chatWindows.activeTab = 'global-chat'
    return
  }
  if (ensuringGlobalChat.value) return
  ensuringGlobalChat.value = true
  try {
    const conv = await api<Conversation>('/app/global-chat', { method: 'POST' })
    await data.loadAll()
    await chatWindows.loadLayout(conv.id)
    globalChatId.value = conv.id
    globalModes.value['global-chat'] = chatWindows.presentationFor(conv.id)
    if (globalModes.value['global-chat'] === 'tab') chatWindows.activeTab = 'global-chat'
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally { ensuringGlobalChat.value = false }
}

watch(() => attention.counts, (counts) => {
  const next = {
    approvals: Number(counts.approvals || 0),
    questions: Number(counts.questions || 0),
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
  Object.assign(lastCounts, next)
}, { deep: true })

// 每个会话的未读 / 运行状态不再进全局收件箱，只在会话列表显示数字角标；
// 右上角提示按会话合并，5 秒内同一会话只保留一条。
const lastUnread = new Map<string, { count: number; title: string }>()
const lastRunning = new Map<string, { count: number; title: string }>()
let activityReady = false

function replaceActivity(
  target: Map<string, { count: number; title: string }>,
  source: Map<string, { count: number; title: string }>,
): void {
  target.clear()
  for (const [id, info] of source) target.set(id, info)
}

watch(() => attention.data, (value) => {
  if (!value?.counts) return
  const unread = new Map<string, { count: number; title: string }>()
  for (const item of value.unread || []) {
    const id = String(item.id || '')
    if (!id) continue
    unread.set(id, {
      count: Math.max(1, Number(item.unread_count || 1)),
      title: String(item.title || '会话'),
    })
  }
  const running = new Map<string, { count: number; title: string }>()
  for (const item of value.running || []) {
    const id = String(item.conversation_id || '')
    if (!id) continue
    const hit = running.get(id)
    running.set(id, {
      count: (hit?.count || 0) + 1,
      title: String(item.title || hit?.title || '会话'),
    })
  }
  if (!activityReady) {
    activityReady = true
    replaceActivity(lastUnread, unread)
    replaceActivity(lastRunning, running)
    return
  }
  const openIds = openConversationIds()
  for (const id of unread.keys()) {
    if (openIds.has(id)) void markOpenRead(id)
  }
  for (const [id, info] of unread) {
    if (openIds.has(id)) continue
    const prev = lastUnread.get(id)?.count || 0
    if (info.count > prev) {
      playSound('message')
      notice.push({
        title: info.title,
        message: `${info.count - prev} 条新消息`,
        tone: 'info',
        duration: 2500,
        mergeKey: `conv:${id}`,
        mergeWindowMs: 5000,
      })
    }
  }
  for (const [id, prev] of lastRunning) {
    const current = running.get(id)
    const count = current?.count || 0
    if (prev.count > count) {
      if (count === 0) playSound('done')
      notice.push({
        title: current?.title || prev.title,
        message: count === 0 ? '运行完成' : '有后台任务结束',
        tone: 'success',
        duration: 2500,
        mergeKey: `conv:${id}`,
        mergeWindowMs: 5000,
      })
    }
  }
  replaceActivity(lastUnread, unread)
  replaceActivity(lastRunning, running)
}, { deep: true })

function onNoticeAction(action: string): void {
  if (action === 'global-approvals') openGlobalAgentInbox()
}

onMounted(async () => {
  await app.initTheme()
  try {
    await Promise.all([data.loadAll(), data.loadWork(), data.loadSchedules()])
  } finally {
    // 加载成功或失败都放行启动动画：失败时界面自己会提示，不该卡在启动页。
    bootReady.value = true
  }
  attention.start()
  window.setInterval(() => {
    if (!document.hidden) {
      void data.loadWork()
      void data.loadRoles()
      void data.loadConversations()
      void data.loadSchedules()
    }
  }, 15_000)
})
</script>

<template>
  <StartupSplash v-if="booting" :duration="bootDuration" :ready="bootReady" @done="booting = false" />

  <el-config-provider :locale="zhCn">
  <div class="app flat" :class="{ 'mobile-dock-open': mobileDockOpen }" :style="{ '--dock-width': dockStyleWidth }">
    <AppRail :pending-count="pendingCount" :global-agent-open="Boolean(globalAgentId)"
      :global-chat-open="Boolean(globalChatId)" @global-agent="openGlobalAgent" @global-chat="openGlobalChat"
      @navigate="chatWindows.activeTab = ''" />
    <main class="canvas">
      <div class="workspace-header">
        <WorkspaceTabs :tabs="workspaceTabs" :active="activeWorkspaceTab"
          @select="selectWorkspaceTab" @close="closeWorkspaceTab" />
        <div class="workspace-actions">
          <QuickSearch />
          <button class="mobile-dock-trigger btn" type="button" :aria-expanded="mobileDockOpen"
            aria-controls="activity-dock" @click="mobileDockOpen = !mobileDockOpen">
            {{ mobileDockOpen ? '关闭侧栏' : '协作中心' }}
          </button>
        </div>
      </div>
      <div id="workspace-content" class="workspace-content" role="tabpanel"
        :aria-labelledby="`workspace-tab-${activeWorkspaceTab}`">
        <div v-show="!chatWindows.activeTab" class="workspace-view"><router-view /></div>
        <div id="workspace-chat" v-show="chatWindows.activeTab" class="workspace-view" />
      </div>
    </main>
    <button v-if="mobileDockOpen" class="mobile-dock-backdrop" type="button" aria-label="关闭协作中心"
      @click="mobileDockOpen = false" />
    <OnlineRoles id="activity-dock" :collapsed="dockCollapsed && !mobileDockOpen"
      @toggle="toggleDock" @resize="resizeDock" @open-chat="mobileDockOpen = false" />
  </div>

  <Teleport v-for="win in chatWindows.windows" :key="win.key"
    :to="win.presentation === 'tab' ? '#workspace-chat' : 'body'">
    <ChatPanel v-show="win.presentation !== 'tab' || chatWindows.activeTab === win.key"
      :conversation-id="win.conversationId"
      :window-key="win.key"
      :session-root-id="win.sessionRootId"
      :tabs="win.tabs"
      :active-tab-key="win.activeTabKey"
      :draft-role-id="win.roleId || ''"
      :draft-participants="win.participantIds || []"
      :draft-workspace-id="win.workspaceId || ''"
      :draft-team-id="win.teamId || ''"
      :presentation="win.presentation"
      :popup="win.popup"
      @update:popup="(popup) => chatWindows.setPopup(win.key, popup)"
      @title="(title) => chatWindows.setTitle(win.key, title)"
      @update:presentation="(mode) => chatWindows.setPresentation(win.key, mode)"
      @switch-tab="(tabKey) => chatWindows.activateTab(win.key, tabKey)"
      @add-tab="chatWindows.addTab(win.key)"
      @draft-text="(text) => chatWindows.rememberTabText(win.key, text)"
      @close="chatWindows.close(win.key)"
      @promoted="(id) => chatWindows.promote(win.key, id)"
      @draft-workspace="(id) => win.workspaceId = id"
    />
  </Teleport>

  <Teleport v-for="panel in globalPanels" :key="panel.key"
    :to="globalModes[panel.key] === 'tab' ? '#workspace-chat' : 'body'">
    <ChatPanel v-show="globalModes[panel.key] !== 'tab' || chatWindows.activeTab === panel.key"
      :conversation-id="panel.id" :initial-tab="panel.agent ? globalAgentTab : 'chat'"
      :global-agent="panel.agent" :global-conversation="!panel.agent"
      :presentation="globalModes[panel.key]"
      :popup="chatWindows.popupFor(panel.id)"
      @update:popup="(popup) => chatWindows.rememberPopup(panel.id, popup)"
      @update:presentation="(mode) => setGlobalPresentation(panel.key, panel.id, mode)"
      @close="closeGlobalPanel(panel.key)"
    />
  </Teleport>

  <NoticeStack @action="onNoticeAction" />
  </el-config-provider>
</template>

<style scoped>
.app.flat { grid-template-columns: 196px minmax(0, 1fr) min(var(--dock-width), 36vw); }
@media (max-width: 1210px) {
  .app.flat { grid-template-columns: 64px minmax(0, 1fr) min(var(--dock-width), 33vw); }
}
@media (max-width: 840px) {
  .app.flat { grid-template-columns: 52px minmax(0, 1fr); }
  #activity-dock { display: none; }
  .mobile-dock-open #activity-dock {
    display: flex;
    position: fixed;
    inset: var(--workspace-header-height) 0 0 auto;
    width: min(360px, calc(100vw - 52px));
    height: auto;
    z-index: 9;
  }
}
</style>

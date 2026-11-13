import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'
import type { Conversation, ListResponse, Team } from '@/api/types'
import { GLOBAL_AGENT_ZONE, GLOBAL_CHAT_ZONE } from '@/lib/derive'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

export type ChatPresentation = 'popup' | 'drawer' | 'tab'

export type ChatPopup = { x: number; y: number; w: number; h: number }
type ChatLayout = { presentation?: ChatPresentation; popup?: ChatPopup }

/**
 * 栏目：会话窗口左侧的一列独立对话。
 *
 * 一个会话（窗口）可以有无穷多个栏目；每个栏目就是一条独立会话记录，
 * 消息 / 上下文 / 运行互不影响。`sessionRootId` 为空时是还没落库的新会话草稿。
 */
export interface ChatTabState {
  key: string
  conversationId: string
  draft: boolean
  title?: string
  roleId?: string
  teamId?: string
  workspaceId?: string
  participantIds?: string[]
  createdAt?: string
  updatedAt?: string
  archived?: boolean
  running?: number
  unread?: number
  /** 未发出的草稿正文：切栏目再切回来时不丢。 */
  text?: string
}

export interface ChatWindowState {
  /** 稳定 key（通常 conv:<会话根 id>）。提升草稿时不换 key，避免组件重挂载。 */
  key: string
  /** 会话根 id；草稿会话为空。 */
  sessionRootId: string
  tabs: ChatTabState[]
  activeTabKey: string
  presentation: ChatPresentation
  popup?: ChatPopup
  title?: string
  /** 以下字段同步自当前栏目，供 App / ChatPanel 直接读取。 */
  conversationId: string
  draft: boolean
  roleId?: string
  teamId?: string
  workspaceId?: string
  participantIds?: string[]
}

const LAYOUT_KEY = 'atlasx.chat.layout'
function readLayouts(): Record<string, ChatLayout> {
  try {
    const stored = localStorage.getItem(LAYOUT_KEY)
    if (stored) return JSON.parse(stored) || {}
    const legacy = JSON.parse(localStorage.getItem('atlasx.chat.presentation') || '{}')
    return Object.fromEntries(Object.entries(legacy.conversations || {})
      .filter(([, mode]) => mode === 'popup' || mode === 'drawer' || mode === 'tab')
      .map(([id, presentation]) => [id, { presentation }])) as Record<string, ChatLayout>
  } catch {
    return {}
  }
}

function tabKeyFor(id: string): string {
  return `conv:${id}`
}

export const useChatWindowsStore = defineStore('chatWindows', () => {
  const windows = ref<ChatWindowState[]>([])
  const activeTab = ref('')
  let draftSequence = 0
  let openSequence = 0
  const layouts = ref(readLayouts())
  const loads = new Map<string, Promise<void>>()
  const writes = new Map<string, Promise<void>>()
  const refreshes = new Map<string, number>()

  function presentationFor(id: string): ChatPresentation {
    return layouts.value[id]?.presentation || 'popup'
  }

  function popupFor(id: string): ChatPopup | undefined {
    return layouts.value[id]?.popup
  }

  function cacheLayout(id: string, layout: ChatLayout): void {
    layouts.value[id] = layout
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layouts.value)) } catch { /* ignore */ }
  }

  function saveLayout(id: string, patch: ChatLayout): void {
    if (!id) return
    cacheLayout(id, { ...layouts.value[id], ...patch })
    const write = (writes.get(id) || Promise.resolve()).then(async () => {
      await api(`/conversations/${encodeURIComponent(id)}/window-layout`, { method: 'PATCH', body: patch })
    }).catch(() => {
      loads.delete(id)
    })
    writes.set(id, write)
    void write.then(() => { if (writes.get(id) === write) writes.delete(id) })
  }

  function loadLayout(id: string): Promise<void> {
    const existing = loads.get(id)
    if (existing) return existing
    const before = layouts.value[id]
    const load = api<ChatLayout>(`/conversations/${encodeURIComponent(id)}/window-layout`).then((saved) => {
      if (layouts.value[id] !== before || writes.has(id)) return
      if (saved.presentation || saved.popup) cacheLayout(id, { ...before, ...saved })
      else if (before) saveLayout(id, before)
    }).catch(() => { loads.delete(id) })
    loads.set(id, load)
    return load
  }

  function rememberPresentation(id: string, presentation: ChatPresentation): void {
    saveLayout(id, { presentation })
  }

  function rememberPopup(id: string, popup: ChatPopup): void {
    saveLayout(id, { popup })
  }

  /** 窗口真正落盘的会话 id：有根会话就用根，草稿等提升后再写。 */
  function layoutTarget(hit: ChatWindowState): string {
    return hit.sessionRootId || hit.conversationId
  }

  /** 把当前栏目同步回窗口的扁平字段（App.vue / ChatPanel 直接读这些）。 */
  function sync(hit: ChatWindowState): void {
    if (!hit.tabs.some((tab) => tab.key === hit.activeTabKey)) {
      hit.activeTabKey = hit.tabs[0]?.key || ''
    }
    const tab = hit.tabs.find((item) => item.key === hit.activeTabKey)
    if (!tab) {
      hit.conversationId = ''
      hit.draft = true
      hit.title = ''
      return
    }
    hit.conversationId = tab.conversationId
    hit.draft = tab.draft
    hit.roleId = tab.roleId
    hit.teamId = tab.teamId
    hit.workspaceId = tab.workspaceId
    hit.participantIds = tab.participantIds
    hit.title = tab.title || hit.title
  }

  function tabFromConversation(conv: Conversation): ChatTabState {
    return {
      key: tabKeyFor(conv.id),
      conversationId: conv.id,
      draft: false,
      // 栏目名只用会话自己的标题；没有标题时由界面显示时间，不再回落到角色名。
      title: String(conv.title || ''),
      roleId: String(conv.role_id || ''),
      teamId: String(conv.team_id || ''),
      workspaceId: String(conv.workspace_id || ''),
      createdAt: String(conv.created_at || ''),
      updatedAt: String(conv.updated_at || conv.created_at || ''),
      archived: Boolean(conv.archived),
      running: Number(conv.running_count || 0),
      unread: Number(conv.unread_count || 0),
    }
  }

  function makeDraftTab(hit: ChatWindowState, title = '新栏目'): ChatTabState {
    const root = hit.tabs.find((tab) => tab.conversationId === hit.sessionRootId)
      || hit.tabs.find((tab) => !tab.draft)
    return {
      key: `draft:${++draftSequence}`,
      conversationId: '',
      draft: true,
      title,
      roleId: root?.roleId || hit.roleId || '',
      teamId: root?.teamId || hit.teamId || '',
      workspaceId: root?.workspaceId || hit.workspaceId || '',
      participantIds: hit.participantIds || [],
      text: '',
    }
  }

  function focus(key: string): void {
    const hit = windows.value.find((item) => item.key === key)
    if (hit?.presentation === 'tab') activeTab.value = key
  }

  function setTitle(key: string, title: string): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit) return
    hit.title = title
    const tab = hit.tabs.find((item) => item.key === hit.activeTabKey)
    if (tab) tab.title = title
  }

  function setPresentation(key: string, presentation: ChatPresentation): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit) return
    hit.presentation = presentation
    rememberPresentation(layoutTarget(hit), presentation)
    if (presentation === 'tab') activeTab.value = key
    else {
      if (activeTab.value === key) activeTab.value = ''
      windows.value = windows.value.filter((item) => item.key === key || item.presentation === 'tab')
    }
  }

  function push(next: ChatWindowState): void {
    const same = windows.value.find((item) => item.key === next.key
      || item.tabs.some((tab) => tab.conversationId
        && next.tabs.some((other) => other.conversationId === tab.conversationId)))
    if (same) {
      focus(same.key)
      return
    }
    if (next.presentation !== 'tab') {
      windows.value = windows.value.filter((item) => item.presentation === 'tab')
    }
    windows.value.push(next)
    focus(next.key)
  }

  function findWindow(conversationId: string): ChatWindowState | undefined {
    return windows.value.find((item) => item.tabs.some(
      (tab) => tab.conversationId && tab.conversationId === conversationId,
    ))
  }

  async function resolveConversation(id: string): Promise<Partial<Conversation>> {
    const data = useDataStore()
    const local = data.conversations.find((item) => item.id === id)
    if (local) return local
    try {
      return await api<Conversation>(`/conversations/${encodeURIComponent(id)}?messages=0`)
    } catch {
      return { id }
    }
  }

  /** 从后端拉整棵会话的栏目（根 + 所有子栏目），保留尚未落库的草稿栏目。 */
  async function refreshWindow(key: string, activateId = ''): Promise<void> {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit || !hit.sessionRootId) return
    const seq = (refreshes.get(key) || 0) + 1
    refreshes.set(key, seq)
    try {
      const out = await api<ListResponse<Conversation>>(
        `/conversations?session_id=${encodeURIComponent(hit.sessionRootId)}`
        + '&limit=200&include_archived=true',
      )
      if (refreshes.get(key) !== seq) return
      const items = out.items || []
      if (Array.isArray(out.items) && !items.length) {
        // 会话根已经不存在（在别处删了）：这个窗口没有可显示的内容了。
        close(key)
        return
      }
      const draftTabs = hit.tabs.filter((tab) => tab.draft)
      const before = new Map(hit.tabs.map((tab) => [tab.conversationId, tab]))
      const tabs = items
        .map((item) => {
          const tab = tabFromConversation(item)
          const prev = before.get(tab.conversationId)
          if (prev) {
            // 刷新不丢未发出的正文 / 草稿参与角色。
            tab.text = prev.text
            tab.participantIds = prev.participantIds
          }
          return tab
        })
        .sort((a, b) => String(b.updatedAt || b.createdAt || '')
          .localeCompare(String(a.updatedAt || a.createdAt || '')))
      if (!tabs.length) {
        const active = hit.tabs.find((tab) => tab.conversationId === activateId
          || tab.conversationId === hit.conversationId)
        if (active) tabs.push(active)
      }
      hit.tabs = [...tabs, ...draftTabs]
      if (activateId) {
        const target = hit.tabs.find((tab) => tab.conversationId === activateId)
        if (target) hit.activeTabKey = target.key
      }
      sync(hit)
    } catch {
      /* 拉取失败保留现有栏目，不影响聊天 */
    }
  }

  async function openConversation(conversationId: string): Promise<void> {
    const id = String(conversationId || '').trim()
    if (!id) return
    const current = findWindow(id)
    if (current) {
      const tab = current.tabs.find((item) => item.conversationId === id)
      if (tab) current.activeTabKey = tab.key
      sync(current)
      focus(current.key)
      return
    }
    const meta = await resolveConversation(id)
    const root = String(meta.session_root_id || meta.session_id || id)
    const sameSession = windows.value.find((item) => item.sessionRootId === root)
    if (sameSession) {
      await refreshWindow(sameSession.key, id)
      focus(sameSession.key)
      return
    }
    const sequence = ++openSequence
    await loadLayout(root)
    if (sequence !== openSequence) return
    const tab = tabFromConversation({ ...meta, id } as Conversation)
    const hit: ChatWindowState = {
      key: `conv:${root}`,
      sessionRootId: root,
      tabs: [tab],
      activeTabKey: tab.key,
      presentation: presentationFor(root),
      popup: popupFor(root),
      conversationId: tab.conversationId,
      draft: false,
    }
    sync(hit)
    push(hit)
    void refreshWindow(hit.key, id)
  }

  function openDraft(roleId: string, options: {
    participantIds?: string[]
    workspaceId?: string
    teamId?: string
  } = {}): void {
    const rid = String(roleId || '').trim()
    const tid = String(options.teamId || '').trim()
    if (!rid && !tid) return
    openSequence += 1
    const existing = windows.value.find((item) => {
      if (item.sessionRootId) return false
      if (options.workspaceId !== undefined && item.workspaceId !== options.workspaceId) return false
      return tid ? item.teamId === tid : !item.teamId && item.roleId === rid
    })
    if (existing) {
      focus(existing.key)
      return
    }
    const challenge: ChatWindowState = {
      key: `draft:${tid || rid}:${++draftSequence}`,
      sessionRootId: '',
      tabs: [],
      activeTabKey: '',
      presentation: presentationFor(''),
      roleId: rid,
      teamId: tid,
      workspaceId: options.workspaceId || '',
      participantIds: tid ? [] : (options.participantIds || []),
      conversationId: '',
      draft: true,
    }
    const tab = makeDraftTab(challenge)
    challenge.tabs = [tab]
    challenge.activeTabKey = tab.key
    sync(challenge)
    push(challenge)
  }

  const openingGroups = new Set<string>()
  async function openGroup(team: Team): Promise<boolean> {
    if (!team.members?.length || !team.leader?.id) {
      useAppStore().toast('请先在群组资料中设置群主和成员', 'bad')
      return false
    }
    if (openingGroups.has(team.id)) return false
    openingGroups.add(team.id)
    try {
      const matches = (conv: Conversation) => conv.team_id === team.id
        && ![GLOBAL_AGENT_ZONE, GLOBAL_CHAT_ZONE].includes(String(conv.zone || ''))
      const ws = String(team.workspace_id || '')
      const preferred = (conv: Conversation) => String(conv.workspace_id || '') === ws
      let existing = useDataStore().conversations.filter(matches).find(preferred)
        || useDataStore().conversations.find(matches)
      if (!existing) {
        const result = await api<ListResponse<Conversation>>(
          `/conversations?team_id=${encodeURIComponent(team.id)}&roots_only=1&limit=20`,
        ).catch(() => null)
        const items = (result?.items || []).filter(matches)
        existing = items.find(preferred) || items[0]
      }
      if (existing) await openConversation(existing.id)
      else openDraft(team.leader.id, { teamId: team.id })
      return true
    } finally {
      openingGroups.delete(team.id)
    }
  }

  /** 草稿发出第一条消息后替换为真实会话；保留窗口位置和焦点顺序。 */
  function promote(key: string, conversationId: string, participantIds?: string[]): void {
    const id = String(conversationId || '').trim()
    if (!id) return
    const hit = windows.value.find((item) => item.key === key)
    if (!hit) {
      void openConversation(id)
      return
    }
    const tab = hit.tabs.find((item) => item.key === hit.activeTabKey && item.draft)
      || hit.tabs.find((item) => item.draft)
    if (tab) {
      tab.conversationId = id
      tab.draft = false
      tab.key = tabKeyFor(id)
      if (participantIds) tab.participantIds = participantIds
      hit.activeTabKey = tab.key
    } else {
      const fallback: ChatTabState = {
        key: tabKeyFor(id), conversationId: id, draft: false,
        roleId: hit.roleId, teamId: hit.teamId, workspaceId: hit.workspaceId,
      }
      hit.tabs.push(fallback)
      hit.activeTabKey = fallback.key
    }
    // 草稿窗口的第一条消息就是会话根；再开栏目才挂到它下面。
    if (!hit.sessionRootId) hit.sessionRootId = id
    saveLayout(layoutTarget(hit), { presentation: hit.presentation, popup: hit.popup })
    const duplicate = windows.value.find((item) => item.key !== key
      && item.tabs.some((other) => other.conversationId === id))
    if (duplicate) {
      if (activeTab.value === duplicate.key) activeTab.value = hit.presentation === 'tab' ? key : ''
      windows.value = windows.value.filter((item) => item.key === key
        || !item.tabs.some((other) => other.conversationId === id))
    }
    sync(hit)
    void refreshWindow(key, id)
  }

  /** 新增栏目：在当前会话根下再开一个独立对话（第一条消息发出时才落库）。 */
  function addTab(key: string): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit || !hit.sessionRootId) return
    const existingDraft = hit.tabs.find((tab) => tab.draft)
    if (existingDraft) {
      hit.activeTabKey = existingDraft.key
    } else {
      const tab = makeDraftTab(hit)
      hit.tabs.push(tab)
      hit.activeTabKey = tab.key
    }
    sync(hit)
  }

  function activateTab(key: string, tabKey: string): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit || !hit.tabs.some((tab) => tab.key === tabKey)) return
    hit.activeTabKey = tabKey
    sync(hit)
    focus(hit.key)
  }

  function rememberTabText(key: string, text: string): void {
    const hit = windows.value.find((item) => item.key === key)
    const tab = hit?.tabs.find((item) => item.key === hit.activeTabKey)
    if (tab) tab.text = text
  }

  function close(key: string): void {
    if (activeTab.value === key) activeTab.value = ''
    windows.value = windows.value.filter((item) => item.key !== key)
  }

  function closeTab(key: string, tabKey: string): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit) return
    const tab = hit.tabs.find((item) => item.key === tabKey)
    if (!tab) return
    // 会话根本身不能当作普通栏目关掉：删会话由后端负责，这里直接关窗口。
    if (!tab.draft && tab.conversationId && tab.conversationId === hit.sessionRootId) {
      close(key)
      return
    }
    hit.tabs = hit.tabs.filter((item) => item.key !== tabKey)
    if (!hit.tabs.length) {
      close(key)
      return
    }
    if (hit.activeTabKey === tabKey) {
      const index = Math.max(0, hit.tabs.findIndex((item) => item.conversationId === hit.conversationId))
      hit.activeTabKey = (hit.tabs[index] || hit.tabs[0]).key
    }
    sync(hit)
  }

  function closeConversation(conversationId: string): void {
    for (const hit of windows.value) {
      const tab = hit.tabs.find((item) => item.conversationId === conversationId)
      if (tab) {
        closeTab(hit.key, tab.key)
        return
      }
    }
  }

  function isOpen(conversationId: string): boolean {
    return Boolean(findWindow(conversationId))
  }

  function clear(): void {
    openSequence += 1
    windows.value = []
    activeTab.value = ''
  }

  function setPopup(key: string, popup: ChatPopup): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit) return
    hit.popup = popup
    rememberPopup(layoutTarget(hit), popup)
  }

  return {
    windows,
    activeTab,
    presentationFor,
    popupFor,
    loadLayout,
    setPopup,
    rememberPopup,
    rememberPresentation,
    setPresentation,
    setTitle,
    focus,
    openConversation,
    openDraft,
    openGroup,
    promote,
    addTab,
    activateTab,
    rememberTabText,
    close,
    closeTab,
    closeConversation,
    isOpen,
    refreshWindow,
    clear,
  }
})

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

export interface ChatWindowState {
  key: string
  conversationId: string
  draft: boolean
  presentation: ChatPresentation
  popup?: ChatPopup
  title?: string
  roleId?: string
  participantIds?: string[]
  workspaceId?: string
  /** 群聊草稿：发出第一条消息时把会话绑定到该群组 */
  teamId?: string
}

export const useChatWindowsStore = defineStore('chatWindows', () => {
  const windows = ref<ChatWindowState[]>([])
  const activeTab = ref('')
  let draftSequence = 0
  let openSequence = 0
  const layouts = ref(readLayouts())
  const loads = new Map<string, Promise<void>>()
  const writes = new Map<string, Promise<void>>()

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

  function setPopup(key: string, popup: ChatPopup): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit) return
    hit.popup = popup
    rememberPopup(hit.conversationId, popup)
  }

  function focus(key: string): void {
    const hit = windows.value.find((item) => item.key === key)
    if (hit?.presentation === 'tab') activeTab.value = key
  }

  function setTitle(key: string, title: string): void {
    const hit = windows.value.find((item) => item.key === key)
    if (hit) hit.title = title
  }

  function setPresentation(key: string, presentation: ChatPresentation): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit) return
    hit.presentation = presentation
    rememberPresentation(hit.conversationId, presentation)
    if (presentation === 'tab') activeTab.value = key
    else {
      if (activeTab.value === key) activeTab.value = ''
      windows.value = windows.value.filter((item) => item.key === key || item.presentation === 'tab')
    }
  }

  function push(next: ChatWindowState): void {
    const same = windows.value.find((item) => item.key === next.key
      || (next.conversationId && item.conversationId === next.conversationId))
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

  async function openConversation(conversationId: string): Promise<void> {
    const id = String(conversationId || '').trim()
    if (!id) return
    const sequence = ++openSequence
    const current = windows.value.find((item) => item.conversationId === id)
    if (current) {
      focus(current.key)
      return
    }
    await loadLayout(id)
    if (sequence !== openSequence) return
    push({
      key: `conv:${id}`, conversationId: id, draft: false,
      presentation: presentationFor(id), popup: popupFor(id),
    })
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
      if (!item.draft) return false
      if (options.workspaceId !== undefined && item.workspaceId !== options.workspaceId) return false
      return tid ? item.teamId === tid : !item.teamId && item.roleId === rid
    })
    if (existing) {
      focus(existing.key)
      return
    }
    push({
      key: `draft:${tid || rid}:${++draftSequence}`,
      conversationId: '',
      draft: true,
      presentation: presentationFor(''),
      roleId: rid,
      participantIds: tid ? [] : (options.participantIds || []),
      workspaceId: options.workspaceId || '',
      teamId: tid,
    })
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
      let existing = useDataStore().conversations.find(matches)
      if (!existing) {
        const result = await api<ListResponse<Conversation>>(
          `/conversations?team_id=${encodeURIComponent(team.id)}&limit=20`,
        ).catch(() => null)
        existing = result?.items?.find(matches)
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
    hit.conversationId = id
    hit.draft = false
    saveLayout(id, { presentation: hit.presentation, popup: hit.popup })
    if (participantIds) hit.participantIds = participantIds
    const duplicate = windows.value.find((item) => item.key !== key && item.conversationId === id)
    if (duplicate && activeTab.value === duplicate.key) activeTab.value = hit.presentation === 'tab' ? key : ''
    // 另一个窗口已经打开同一会话：合并掉当前草稿。key 保持不变，避免组件重挂载。
    windows.value = windows.value.filter(
      (item) => item.key === key || item.conversationId !== id,
    )
  }

  function close(key: string): void {
    if (activeTab.value === key) activeTab.value = ''
    windows.value = windows.value.filter((item) => item.key !== key)
  }

  function closeConversation(conversationId: string): void {
    for (const item of windows.value.filter((win) => win.conversationId === conversationId)) close(item.key)
  }

  function isOpen(conversationId: string): boolean {
    return windows.value.some((item) => item.conversationId === conversationId)
  }

  function clear(): void {
    openSequence += 1
    windows.value = []
    activeTab.value = ''
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
    close,
    closeConversation,
    isOpen,
    clear,
  }
})

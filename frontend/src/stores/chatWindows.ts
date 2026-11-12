import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'
import type { Conversation, ListResponse, Team } from '@/api/types'
import { GLOBAL_AGENT_ZONE, GLOBAL_CHAT_ZONE } from '@/lib/derive'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

export type ChatPresentation = 'popup' | 'drawer' | 'tab'

const PRESENTATION_KEY = 'atlasx.chat.presentation'
function isPresentation(value: unknown): value is ChatPresentation {
  return value === 'popup' || value === 'drawer' || value === 'tab'
}

function readPresentation(): { last: ChatPresentation; conversations: Record<string, ChatPresentation> } {
  try {
    const saved = JSON.parse(localStorage.getItem(PRESENTATION_KEY) || '{}')
    return { last: isPresentation(saved?.last) ? saved.last : 'popup', conversations: saved?.conversations && typeof saved.conversations === 'object' ? saved.conversations : {} }
  } catch {
    return { last: 'popup', conversations: {} }
  }
}
export interface ChatWindowState {
  key: string
  conversationId: string
  draft: boolean
  presentation: ChatPresentation
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
  const preferences = ref(readPresentation())

  function presentationFor(id: string): ChatPresentation {
    const saved = preferences.value.conversations[id]
    return isPresentation(saved) ? saved : preferences.value.last
  }

  function rememberPresentation(id: string, presentation: ChatPresentation): void {
    preferences.value.last = presentation
    if (id) preferences.value.conversations = { ...preferences.value.conversations, [id]: presentation }
    try { localStorage.setItem(PRESENTATION_KEY, JSON.stringify(preferences.value)) } catch { /* ignore */ }
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

  function openConversation(conversationId: string): void {
    const id = String(conversationId || '').trim()
    if (!id) return
    push({ key: `conv:${id}`, conversationId: id, draft: false, presentation: presentationFor(id) })
  }

  function openDraft(roleId: string, options: {
    participantIds?: string[]
    workspaceId?: string
    teamId?: string
  } = {}): void {
    const rid = String(roleId || '').trim()
    const tid = String(options.teamId || '').trim()
    if (!rid && !tid) return
    const existing = tid
      ? windows.value.find((item) => item.draft && item.teamId === tid)
      : windows.value.find((item) => item.draft && !item.teamId && item.roleId === rid)
    if (existing) {
      focus(existing.key)
      return
    }
    push({
      key: tid ? `draft:team:${tid}` : `draft:${rid}:${Date.now()}`,
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
      if (existing) openConversation(existing.id)
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
      openConversation(id)
      return
    }
    hit.conversationId = id
    hit.draft = false
    rememberPresentation(id, hit.presentation)
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
    windows.value = []
    activeTab.value = ''
  }

  return {
    windows,
    activeTab,
    presentationFor,
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

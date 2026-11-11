import { ref } from 'vue'
import { defineStore } from 'pinia'

/** 普通会话浮窗：草稿会话（还没有后端记录）与已落库会话共用一套窗口。 */
export interface ChatWindowState {
  key: string
  conversationId: string
  draft: boolean
  roleId?: string
  participantIds?: string[]
  workspaceId?: string
}

const MAX_WINDOWS = 2

/**
 * 普通会话浮窗最多 2 个。超过时新窗口顶掉最早打开的那个（数组头部）。
 * 全局管理 / 全局对话不在这个 store 里，不计入上限。
 */
export const useChatWindowsStore = defineStore('chatWindows', () => {
  const windows = ref<ChatWindowState[]>([])

  function focus(key: string): void {
    const hit = windows.value.find((item) => item.key === key)
    if (!hit) return
    windows.value = [...windows.value.filter((item) => item.key !== key), hit]
  }

  function push(next: ChatWindowState): void {
    const same = next.conversationId
      ? windows.value.find((item) => item.conversationId === next.conversationId)
      : windows.value.find((item) => item.key === next.key)
    if (same) {
      focus(same.key)
      return
    }
    windows.value = [...windows.value, next]
    if (windows.value.length > MAX_WINDOWS) {
      windows.value = windows.value.slice(windows.value.length - MAX_WINDOWS)
    }
  }

  function openConversation(conversationId: string): void {
    const id = String(conversationId || '').trim()
    if (!id) return
    push({ key: `conv:${id}`, conversationId: id, draft: false })
  }

  function openDraft(roleId: string, options: { participantIds?: string[]; workspaceId?: string } = {}): void {
    const rid = String(roleId || '').trim()
    if (!rid) return
    const existing = windows.value.find((item) => item.draft && item.roleId === rid)
    if (existing) {
      focus(existing.key)
      return
    }
    push({
      key: `draft:${rid}:${Date.now()}`,
      conversationId: '',
      draft: true,
      roleId: rid,
      participantIds: options.participantIds || [],
      workspaceId: options.workspaceId || '',
    })
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
    if (participantIds) hit.participantIds = participantIds
    // 另一个窗口已经打开同一会话：合并掉当前草稿。key 保持不变，避免组件重挂载。
    windows.value = windows.value.filter(
      (item) => item.key === key || item.conversationId !== id,
    )
  }

  function close(key: string): void {
    windows.value = windows.value.filter((item) => item.key !== key)
  }

  function closeConversation(conversationId: string): void {
    windows.value = windows.value.filter((item) => item.conversationId !== conversationId)
  }

  function isOpen(conversationId: string): boolean {
    return windows.value.some((item) => item.conversationId === conversationId)
  }

  function clear(): void {
    windows.value = []
  }

  return {
    windows,
    focus,
    openConversation,
    openDraft,
    promote,
    close,
    closeConversation,
    isOpen,
    clear,
  }
})

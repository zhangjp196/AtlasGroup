import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatWindowsStore } from './chatWindows'

const backend = vi.hoisted(() => new Map<string, Record<string, unknown>>())
vi.mock('@/api/client', () => ({
  api: async (path: string, options?: { body: Record<string, unknown> }) => {
    if (options?.body) backend.set(path, { ...backend.get(path), ...options.body })
    return backend.get(path) || {}
  },
}))

describe('chat windows', () => {
  beforeEach(() => {
    localStorage.clear()
    backend.clear()
    setActivePinia(createPinia())
  })

  it('keeps only one window and replaces the current one', async () => {
    const store = useChatWindowsStore()
    await store.openConversation('c1')
    await store.openConversation('c2')
    await store.openConversation('c3')
    expect(store.windows.map((item) => item.conversationId)).toEqual(['c3'])
  })

  it('opens a draft and promotes it without changing its key', () => {
    const store = useChatWindowsStore()
    store.openDraft('role-1')
    const key = store.windows[0].key
    expect(store.windows[0].draft).toBe(true)
    store.promote(key, 'conv-1')
    expect(store.windows[0].key).toBe(key)
    expect(store.windows[0].conversationId).toBe('conv-1')
    expect(store.windows[0].draft).toBe(false)
  })

  it('keeps drafts for the same role separate when their workspaces differ', () => {
    const store = useChatWindowsStore()
    store.openDraft('role-1', { workspaceId: 'ws-a' })
    store.setPresentation(store.windows[0].key, 'tab')
    store.openDraft('role-1', { workspaceId: '' })
    expect(store.windows[1].presentation).toBe('popup')
    expect(store.windows.map((item) => item.workspaceId)).toEqual(['ws-a', ''])
    expect(new Set(store.windows.map((item) => item.key)).size).toBe(2)
  })

  it('replaces the current conversation when another one opens', async () => {
    const store = useChatWindowsStore()
    await store.openConversation('c1')
    await store.openConversation('c2')
    await store.openConversation('c1')
    expect(store.windows.map((item) => item.conversationId)).toEqual(['c1'])
  })

  it('retains multiple chat tabs and remembers presentation for each conversation', async () => {
    const store = useChatWindowsStore()
    await store.openConversation('c1')
    store.setPresentation('conv:c1', 'tab')
    await store.openConversation('c2')
    expect(store.windows[1].presentation).toBe('popup')
    store.setPresentation('conv:c2', 'tab')
    expect(store.activeTab).toBe('conv:c2')

    store.setPresentation('conv:c2', 'drawer')
    expect(store.windows.map((item) => item.presentation)).toEqual(['tab', 'drawer'])
    expect(store.activeTab).toBe('')
    await store.openConversation('c1')
    expect(store.activeTab).toBe('conv:c1')

    setActivePinia(createPinia())
    const reopened = useChatWindowsStore()
    await reopened.openConversation('c1')
    await reopened.openConversation('c2')
    expect(reopened.windows.map((item) => item.presentation)).toEqual(['tab', 'drawer'])
  })

  it('restores layout from the conversation after local storage is cleared', async () => {
    const store = useChatWindowsStore()
    const popup = { x: 20, y: 32, w: 560, h: 480 }
    store.openDraft('role-1')
    const key = store.windows[0].key
    store.setPopup(key, popup)
    store.setPresentation(key, 'drawer')
    store.promote(key, 'c3')
    await vi.waitFor(() => expect(backend.get('/conversations/c3/window-layout'))
      .toEqual({ presentation: 'drawer', popup }))

    localStorage.clear()
    setActivePinia(createPinia())
    const reopened = useChatWindowsStore()
    await reopened.openConversation('c3')
    expect(reopened.windows[0]).toMatchObject({ presentation: 'drawer', popup })
  })

})

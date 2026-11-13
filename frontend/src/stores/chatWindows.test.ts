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

  it('opens a child conversation as a lane in the same session window', async () => {
    const thread = (id: string, session?: string) => ({
      id, session_id: session || id, session_root_id: session || id, title: id,
    })
    backend.set('/conversations/c2?messages=0', thread('c2', 'c1'))
    backend.set('/conversations?session_id=c1&limit=200&include_archived=true',
      { items: [thread('c1'), thread('c2', 'c1')] })
    const store = useChatWindowsStore()
    await store.openConversation('c2')
    expect(store.windows).toHaveLength(1)
    expect(store.windows[0].key).toBe('conv:c1')
    expect(store.windows[0].sessionRootId).toBe('c1')
    expect(store.windows[0].tabs.map((tab) => tab.conversationId).sort())
      .toEqual(['c1', 'c2'])
  })

  it('adds a lane to the current session instead of opening a new session', async () => {
    backend.set('/conversations?session_id=c1&limit=200&include_archived=true',
      { items: [{ id: 'c1', session_id: 'c1', session_root_id: 'c1' }] })
    const store = useChatWindowsStore()
    await store.openConversation('c1')
    store.addTab('conv:c1')
    expect(store.windows).toHaveLength(1)
    expect(store.windows[0].tabs.some((tab) => tab.draft)).toBe(true)
    const draftKey = store.windows[0].activeTabKey
    expect(store.windows[0].tabs.find((tab) => tab.key === draftKey)?.draft).toBe(true)

    store.promote('conv:c1', 'c4')
    expect(store.windows[0].sessionRootId).toBe('c1')
    expect(store.windows[0].tabs.some((tab) => tab.conversationId === 'c4' && !tab.draft))
      .toBe(true)
  })

  it('refreshes lanes in place without losing text or the active lane', async () => {
    const key = '/conversations?session_id=c1&limit=200&include_archived=true'
    backend.set(key, { items: [{ id: 'c1', session_id: 'c1', session_root_id: 'c1' }] })
    const store = useChatWindowsStore()
    await store.openConversation('c1')
    store.rememberTabText('conv:c1', '还没发出去的正文')

    backend.set(key, { items: [
      { id: 'c1', session_id: 'c1', session_root_id: 'c1' },
      { id: 'c-new', session_id: 'c1', session_root_id: 'c1', title: '机器人栏目' },
    ] })
    await store.refreshWindow('conv:c1')
    expect(store.windows).toHaveLength(1)
    expect(store.windows[0].activeTabKey).toBe('conv:c1')
    expect(store.windows[0].tabs.map((tab) => tab.conversationId))
      .toEqual(['c1', 'c-new'])
    expect(store.windows[0].tabs.find((tab) => tab.conversationId === 'c1')?.text)
      .toBe('还没发出去的正文')
    expect(store.windows[0].tabs.find((tab) => tab.conversationId === 'c-new')?.title)
      .toBe('机器人栏目')
  })

  it('sort lanes by latest activity time descending', async () => {
    const key = '/conversations?session_id=c1&limit=200&include_archived=true'
    backend.set(key, { items: [{ id: 'c1', session_id: 'c1', session_root_id: 'c1' }] })
    const store = useChatWindowsStore()
    await store.openConversation('c1')
    backend.set(key, { items: [
      { id: 'c1', session_id: 'c1', session_root_id: 'c1', updated_at: '2026-01-01 10:00:00' },
      { id: 'c2', session_id: 'c1', session_root_id: 'c1', updated_at: '2026-03-01 10:00:00' },
      { id: 'c3', session_id: 'c1', session_root_id: 'c1', updated_at: '2026-02-01 10:00:00' },
    ] })
    await store.refreshWindow('conv:c1')
    expect(store.windows[0].tabs.map((tab) => tab.conversationId))
      .toEqual(['c2', 'c3', 'c1'])
  })

  it('closes the window when its session root was removed elsewhere', async () => {
    const key = '/conversations?session_id=c1&limit=200&include_archived=true'
    backend.set(key, { items: [{ id: 'c1', session_id: 'c1', session_root_id: 'c1' }] })
    const store = useChatWindowsStore()
    await store.openConversation('c1')
    expect(store.windows).toHaveLength(1)

    backend.set(key, { items: [] })
    await store.refreshWindow('conv:c1')
    expect(store.windows).toHaveLength(0)
  })

})

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatWindowsStore } from './chatWindows'

describe('chat windows', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('keeps at most two windows and evicts the oldest', () => {
    const store = useChatWindowsStore()
    store.openConversation('c1')
    store.openConversation('c2')
    store.openConversation('c3')
    expect(store.windows.map((item) => item.conversationId)).toEqual(['c2', 'c3'])
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

  it('focuses an existing conversation instead of duplicating it', () => {
    const store = useChatWindowsStore()
    store.openConversation('c1')
    store.openConversation('c2')
    store.openConversation('c1')
    expect(store.windows.map((item) => item.conversationId)).toEqual(['c2', 'c1'])
  })
})

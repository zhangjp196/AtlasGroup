import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatWindowsStore } from './chatWindows'

describe('chat windows', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('keeps only one window and replaces the current one', () => {
    const store = useChatWindowsStore()
    store.openConversation('c1')
    store.openConversation('c2')
    store.openConversation('c3')
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

  it('replaces the current conversation when another one opens', () => {
    const store = useChatWindowsStore()
    store.openConversation('c1')
    store.openConversation('c2')
    store.openConversation('c1')
    expect(store.windows.map((item) => item.conversationId)).toEqual(['c1'])
  })

  it('retains multiple chat tabs and remembers presentation for each conversation', () => {
    const store = useChatWindowsStore()
    store.openConversation('c1')
    store.setPresentation('conv:c1', 'tab')
    store.openConversation('c2')
    expect(store.activeTab).toBe('conv:c2')
    expect(store.windows.map((item) => item.presentation)).toEqual(['tab', 'tab'])

    store.setPresentation('conv:c2', 'drawer')
    expect(store.windows.map((item) => item.presentation)).toEqual(['tab', 'drawer'])
    expect(store.activeTab).toBe('')
    store.openConversation('c1')
    expect(store.activeTab).toBe('conv:c1')

    setActivePinia(createPinia())
    const reopened = useChatWindowsStore()
    reopened.openConversation('c1')
    reopened.openConversation('c2')
    expect(reopened.windows.map((item) => item.presentation)).toEqual(['tab', 'drawer'])
  })

})

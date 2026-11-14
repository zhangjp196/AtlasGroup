import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import WorkspaceChangesPanel from './WorkspaceChangesPanel.vue'

vi.mock('@/api/client', () => ({ api: vi.fn() }))
const apiMock = vi.mocked(api)

describe('WorkspaceChangesPanel', () => {
  beforeEach(() => {
    apiMock.mockReset()
    apiMock.mockImplementation(async (path) => {
      if (path.endsWith('/checkpoints')) {
        return { items: [{ id: 'cp-1', run_id: 'run-1' }] } as never
      }
      return {
        available: true,
        checkpoint: { id: 'cp-1', run_id: 'run-1' },
        counts: { added: 1, modified: 1, deleted: 0 },
        added: [{ path: 'src/new.py' }],
        modified: [{ path: 'src/main.py' }],
        deleted: [],
        truncated: false,
      } as never
    })
  })

  it('shows added and modified files since the latest checkpoint', async () => {
    const wrapper = mount(WorkspaceChangesPanel, {
      props: { open: true, conversationId: 'conv-1' },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('新增 1')
    expect(wrapper.text()).toContain('src/new.py')
    expect(wrapper.text()).toContain('src/main.py')
    await wrapper.get('.wc-file button[title="将文件路径插入消息输入框"]').trigger('click')
    expect(wrapper.emitted('reference')?.[0]).toEqual(['src/new.py'])
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await wrapper.get('.wc-file button[title="恢复此文件到检查点状态"]').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/conversations/conv-1/checkpoints/cp-1/rewind', {
      method: 'POST',
      body: { files: true, conversation: false, delete_added: true, paths: ['src/new.py'] },
    })
    expect(apiMock).toHaveBeenCalledWith('/conversations/conv-1/checkpoints')
    expect(apiMock).toHaveBeenCalledWith('/conversations/conv-1/checkpoints/cp-1/diff')
  })

  it('refreshes while open and stops refreshing after close', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = mount(WorkspaceChangesPanel, {
        props: { open: true, conversationId: 'conv-1' },
        global: { plugins: [createPinia()] },
      })
      await flushPromises()
      const initialCount = apiMock.mock.calls.length

      await vi.advanceTimersByTimeAsync(5000)
      await flushPromises()
      expect(apiMock.mock.calls.length).toBeGreaterThan(initialCount)

      await wrapper.setProps({ open: false })
      const closedCount = apiMock.mock.calls.length
      await vi.advanceTimersByTimeAsync(10000)
      await flushPromises()
      expect(apiMock.mock.calls.length).toBe(closedCount)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('clears the previous conversation diff when the active session changes', async () => {
    const wrapper = mount(WorkspaceChangesPanel, {
      props: { open: true, conversationId: 'conv-1' },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('src/main.py')

    apiMock.mockImplementation(async (path) => {
      if (path.endsWith('/checkpoints')) return { items: [] } as never
      return null as never
    })
    await wrapper.setProps({ conversationId: 'conv-2' })
    await flushPromises()

    expect(wrapper.text()).not.toContain('src/main.py')
    expect(wrapper.text()).toContain('此会话还没有文件检查点')
    wrapper.unmount()
  })
})

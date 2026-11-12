import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { VueFlow } from '@vue-flow/core'
import { api } from '@/api/client'
import AgentCanvas from './AgentCanvas.vue'

vi.mock('@/api/client', () => ({ api: vi.fn() }))

describe('AgentCanvas', () => {
  it('retains completed nodes while removing completed tasks from active controls', async () => {
    const root = { id: 'root', name: '主持', status: 'running' }
    const subagent = { id: 'sub-1', name: '子代理', status: 'running' }
    const member = { role_id: 'member-1', role_name: '成员', status: 'running' }
    const task = { id: 'task-1', title: '后台任务', status: 'running', members: [member] }

    vi.mocked(api).mockImplementation(async (path) => path.endsWith('/graph')
      ? { root, nodes: [subagent] }
      : { items: [task] })

    const wrapper = shallowMount(AgentCanvas, {
      props: { open: true, conversationId: 'conv-1' },
      global: { plugins: [createPinia()] },
    })
    try {
      await flushPromises()
      expect(wrapper.get('.agent-task-card').text()).toContain('后台任务')

      member.status = 'done'
      await wrapper.findAll('button').find((button) => button.text() === '刷新')!.trigger('click')
      await flushPromises()
      expect(wrapper.getComponent(VueFlow).props('nodes')).toEqual(expect.arrayContaining([
        expect.objectContaining({ data: expect.objectContaining({ name: '成员', status: 'done' }) }),
      ]))

      task.status = root.status = subagent.status = 'done'
      await wrapper.findAll('button').find((button) => button.text() === '刷新')!.trigger('click')
      await flushPromises()
      expect(wrapper.find('.agent-task-card').exists()).toBe(false)
      expect(wrapper.getComponent(VueFlow).props('nodes')).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: 'root', data: expect.objectContaining({ status: 'done' }) }),
        expect.objectContaining({ id: 'graph-sub-1', data: expect.objectContaining({ status: 'done' }) }),
      ]))
    } finally {
      wrapper.unmount()
    }
  })
})

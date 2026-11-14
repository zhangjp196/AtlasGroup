import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import MessageItem from './MessageItem.vue'

describe('MessageItem continuation action', () => {
  it('offers a draft continuation for interrupted assistant messages', async () => {
    const wrapper = mount(MessageItem, {
      props: { message: { id: 'm1', by: 'assistant', interrupted: true, content: 'partial result' } },
      global: { plugins: [createPinia()] },
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('continue-message')).toHaveLength(1)
  })

  it('does not offer continuation for a completed assistant message', () => {
    const wrapper = mount(MessageItem, {
      props: { message: { id: 'm2', by: 'assistant', content: 'done' } },
      global: { plugins: [createPinia()] },
    })

    expect(wrapper.text()).not.toContain('放入续办提示')
  })
})

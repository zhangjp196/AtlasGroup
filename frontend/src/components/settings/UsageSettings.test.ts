import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import UsageSettings from './UsageSettings.vue'

describe('UsageSettings', () => {
  it('renders usage and emits period, refresh, and navigation actions', async () => {
    const wrapper = mount(UsageSettings, {
      props: {
        usage: {
          generated_at: '2026-11-14 10:00',
          periods: {
            '7d': {
              totals: { total_tokens: 12000, prompt_tokens: 8000, completion_tokens: 4000 },
              top_model: { name: 'model-a', total_tokens: 12000, share: 100 },
              models: [{ name: 'model-a', total_tokens: 12000, share: 100 }],
            },
          },
          daily: [{ date: '2026-11-14', prompt_tokens: 8000, completion_tokens: 4000, total_tokens: 12000, messages: 2 }],
        },
        loading: false,
        period: '7d',
      },
      global: {
        stubs: {
          ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        },
      },
    })

    expect(wrapper.text()).toContain('统计于 2026-11-14 10:00')
    expect(wrapper.text()).toContain('12,000')
    expect(wrapper.text()).toContain('model-a')
    await wrapper.get('.work-filter').trigger('click')
    expect(wrapper.emitted('update:period')?.[0]).toEqual(['1d'])

  })
})

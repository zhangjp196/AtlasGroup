import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'
import type { ModelProfile, Subscription } from '@/api/types'
import ModelCascader from './ModelCascader.vue'

const models = [
  { code: 'default' },
  { code: 'gpt-6', name: 'GPT-6', subscription_id: 'cc', subscription_name: 'Command Code' },
  { code: 'claude', name: 'Claude', subscription_id: 'cc', subscription_name: 'Command Code' },
] as ModelProfile[]

const subscriptions: Subscription[] = [{ id: 'cc', name: 'Command Code' }]

function mountPicker(modelValue: string) {
  return mount(ModelCascader, {
    props: { models, subscriptions, modelValue },
    global: { plugins: [ElementPlus] },
  })
}

describe('ModelCascader', () => {
  it('shows only the selected model name in the input', async () => {
    const wrapper = mountPicker('gpt-6')
    await nextTick()
    const input = wrapper.find('input').element as HTMLInputElement
    expect(input.value).toBe('GPT-6')
  })

  it('shows the default label when model code is empty', async () => {
    const wrapper = mountPicker('')
    await nextTick()
    const input = wrapper.find('input').element as HTMLInputElement
    expect(input.value).toBe('默认模型')
  })

  it('exposes two-level options with subscriptions first', async () => {
    const wrapper = mountPicker('')
    await nextTick()
    const vm = wrapper.vm as unknown as { options: Array<{ label: string; children?: Array<{ label: string }> }> }
    expect(vm.options.map((item) => item.label)).toEqual(['默认模型', 'Command Code'])
    expect(vm.options[1].children?.map((item) => item.label)).toEqual(['GPT-6', 'Claude'])
  })
})

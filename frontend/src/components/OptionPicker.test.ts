import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'
import OptionPicker from './OptionPicker.vue'

const items = Array.from({ length: 10 }, (_, index) => ({
  value: `item-${index + 1}`,
  label: `选项 ${index + 1}`,
  description: `说明 ${index + 1}`,
}))

function mountPicker(modelValue = '') {
  return mount(OptionPicker, {
    props: {
      modelValue,
      items,
      emptyLabel: '不绑定',
      pageSize: 4,
      title: '选择角色',
    },
    global: {
      plugins: [ElementPlus],
      stubs: { teleport: true },
    },
  })
}

describe('OptionPicker', () => {
  it('shows selected label and paginates filtered options', async () => {
    const wrapper = mountPicker('item-3')
    expect(wrapper.find('.ws-picker-value').text()).toBe('选项 3')

    await wrapper.find('.ws-picker-trigger').trigger('click')
    await nextTick()
    expect(wrapper.findAll('.ws-picker-row')).toHaveLength(5)
    expect(wrapper.find('.list-pager-btn.active').text()).toBe('1')

    await wrapper.find('.list-pager-btn[aria-label="下一页"]').trigger('click')
    expect(wrapper.find('.list-pager-btn.active').text()).toBe('2')
  })

  it('selects an option and emits its value', async () => {
    const wrapper = mountPicker('')
    await wrapper.find('.ws-picker-trigger').trigger('click')
    await nextTick()
    await wrapper.findAll('.ws-picker-row')[1].trigger('click')
    await wrapper.find('.btn.primary').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['item-1'])
  })

  it('can clear back to the empty option', async () => {
    const wrapper = mountPicker('item-2')
    await wrapper.find('.ws-picker-trigger').trigger('click')
    await nextTick()
    await wrapper.findAll('.ws-picker-row')[0].trigger('click')
    await wrapper.find('.btn.primary').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([''])
  })
})

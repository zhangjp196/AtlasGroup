import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'
import type { Workspace } from '@/api/types'
import WorkspacePicker from './WorkspacePicker.vue'

const workspaces: Workspace[] = Array.from({ length: 10 }, (_, index) => ({
  id: `ws-${index + 1}`,
  name: `项目 ${index + 1}`,
  root_path: `/work/ws-${index + 1}`,
}))
workspaces[9].archived = true

function mountPicker(modelValue = '') {
  return mount(WorkspacePicker, {
    props: {
      modelValue,
      workspaces,
      emptyLabel: '默认工作区',
      pageSize: 4,
    },
    global: {
      plugins: [ElementPlus],
      stubs: { teleport: true },
    },
  })
}

describe('WorkspacePicker', () => {
  it('shows empty label and opens a paginated filtered list', async () => {
    const wrapper = mountPicker()
    expect(wrapper.find('.ws-picker-value').text()).toBe('默认工作区')

    await wrapper.find('.ws-picker-trigger').trigger('click')
    await nextTick()

    // 第一页 4 个使用中工作区 + 「默认工作区」空选项
    expect(wrapper.findAll('.ws-picker-row')).toHaveLength(5)
    expect(wrapper.find('.list-pager').exists()).toBe(true)
    expect(wrapper.find('.list-pager-btn.active').text()).toBe('1')

    const next = wrapper.find('.list-pager-btn[aria-label="下一页"]')
    await next.trigger('click')
    expect(wrapper.find('.list-pager-btn.active').text()).toBe('2')
  })

  it('selects a workspace and emits its id', async () => {
    const wrapper = mountPicker('')
    await wrapper.find('.ws-picker-trigger').trigger('click')
    await nextTick()

    const target = wrapper.findAll('.ws-picker-row')[1]
    await target.trigger('click')
    await wrapper.find('.btn.primary').trigger('click')

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['ws-1'])
  })

  it('disables archived rows but keeps the current archived value selectable', async () => {
    const wrapper = mountPicker('ws-10')
    await wrapper.find('.ws-picker-trigger').trigger('click')
    await nextTick()

    expect(wrapper.text()).toContain('已归档')
    const archivedRow = wrapper.findAll('.ws-picker-row').find((row) => row.text().includes('项目 10'))
    expect(archivedRow?.attributes('disabled')).toBeUndefined()
  })
})

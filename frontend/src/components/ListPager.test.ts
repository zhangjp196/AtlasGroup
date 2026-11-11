import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ListPager from './ListPager.vue'

describe('ListPager', () => {
  it('stays hidden when there is nothing to page', () => {
    const wrapper = mount(ListPager, { props: { page: 1, total: 0 } })
    expect(wrapper.find('.list-pager').exists()).toBe(false)
  })

  it('stays hidden on a single page, without a total footer', () => {
    const wrapper = mount(ListPager, { props: { page: 1, total: 12, pageSize: 12 } })
    expect(wrapper.find('.list-pager').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('共')
  })

  it('shows numbered pages once the list exceeds one page', () => {
    const wrapper = mount(ListPager, { props: { page: 1, total: 13, pageSize: 12 } })
    const buttons = wrapper.findAll('.list-pager-btn')
    expect(buttons.map((button) => button.text())).toEqual(['‹', '1', '2', '›'])
    expect(wrapper.find('.list-pager-btn.active').text()).toBe('1')
  })

  it('emits the target page and disables the edges', () => {
    const wrapper = mount(ListPager, { props: { page: 2, total: 120, pageSize: 12 } })
    const numbers = wrapper.findAll('.list-pager-btn').filter((button) => /^\d+$/.test(button.text()))
    expect(numbers.map((button) => button.text())).toEqual(['1', '2', '3', '10'])

    const pages = wrapper.findAll('.list-pager-btn')
    pages[0].trigger('click')
    pages[pages.length - 1].trigger('click')
    expect(wrapper.emitted('update:page')).toEqual([[1], [3]])
  })

  it('collapses long page ranges around the current page', () => {
    const wrapper = mount(ListPager, { props: { page: 10, total: 600, pageSize: 12 } })
    expect(wrapper.text()).toContain('…')
    const numbers = wrapper.findAll('.list-pager-btn').filter((button) => /^\d+$/.test(button.text()))
    expect(numbers.map((button) => button.text())).toEqual(['1', '9', '10', '11', '50'])
  })

  it('clamps out-of-range pages when rendering', () => {
    const wrapper = mount(ListPager, { props: { page: 99, total: 50, pageSize: 10 } })
    expect(wrapper.find('.list-pager-btn.active').text()).toBe('5')
    const next = wrapper.findAll('.list-pager-btn')[wrapper.findAll('.list-pager-btn').length - 1]
    expect((next?.element as HTMLButtonElement).disabled).toBe(true)
  })
})

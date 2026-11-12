import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { LiveEntry } from '@/composables/useChat'
import type { LiveSpeaker } from '@/lib/live'
import LiveMessage from './LiveMessage.vue'

const speakers: Record<string, LiveSpeaker> = {
  a: { id: 'a', name: '主持', avatar: '', sub: '' },
  b: { id: 'b', name: '后端', avatar: '', sub: '工程师' },
}

function entry(key: string, text: string, speaker: string): LiveEntry {
  return { key, kind: 'text', text, final: true, speaker }
}

// Markdown 正文按 rAF 渲染；测试里换成同步的桩，断言的是「哪一段挂在谁名下」。
const stubs = {
  Markdown: { props: ['text'], template: '<div class="md">{{ text }}</div>' },
}

describe('LiveMessage', () => {
  it('renders one block per speaker instead of one mixed bubble', () => {
    const wrapper = mount(LiveMessage, {
      props: {
        entries: [entry('1', '我先说', 'a'), entry('2', '我补充', 'b')],
        speakers,
        current: 'b',
        started: true,
      },
      global: { stubs },
    })
    const blocks = wrapper.findAll('.msg.assistant')
    expect(blocks).toHaveLength(2)
    expect(blocks[0].text()).toContain('主持')
    expect(blocks[0].text()).toContain('我先说')
    expect(blocks[1].text()).toContain('后端')
    expect(blocks[1].text()).toContain('我补充')
  })

  it('keeps progress text above its tool call and the later reply', async () => {
    const entries: LiveEntry[] = [
      { key: '1', kind: 'text', text: '检查会话样式', speaker: 'a' },
      { key: '2', kind: 'tool', name: 'read_file', args: { path: 'app.css' }, status: 'running', speaker: 'a' },
    ]
    const wrapper = mount(LiveMessage, {
      props: { entries, speakers, streaming: true },
      global: { stubs },
    })
    expect(wrapper.findAll('.reply-entry').map((row) => row.find('.md, .activity-tool').text()))
      .toEqual(['检查会话样式', 'read_file'])
    expect(wrapper.find('.streaming').exists()).toBe(false)

    await wrapper.setProps({ entries: [...entries, entry('3', '已调整样式', 'a')] })
    expect(wrapper.find('.streaming').text()).toBe('已调整样式')
  })

  it('shows the expected speaker while preparing, never a bare 助手', () => {
    const wrapper = mount(LiveMessage, {
      props: { entries: [], speakers, current: 'a', started: false },
    })
    expect(wrapper.findAll('.msg.assistant')).toHaveLength(1)
    expect(wrapper.text()).toContain('主持')
    expect(wrapper.text()).toContain('正在准备这一轮…')
    expect(wrapper.text()).not.toContain('助手')
  })

  it('switches to the thinking hint once start has arrived', () => {
    const wrapper = mount(LiveMessage, {
      props: { entries: [], speakers, current: 'a', started: true, waitSeconds: 8 },
    })
    expect(wrapper.text()).toContain('正在思考…')
    // 首 token 等待时间要可见，避免「静止几秒」像卡死。
    expect(wrapper.find('.live-wait-seconds').text()).toBe('8s')
  })
})

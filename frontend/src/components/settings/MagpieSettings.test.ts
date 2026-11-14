import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MagpieSettings from './MagpieSettings.vue'

describe('MagpieSettings', () => {
  it('shows local redaction status, rule count, and preview result', () => {
    const wrapper = mount(MagpieSettings, {
      props: {
        form: { enabled: true, secrets: true, pii: true, paths: false },
        testText: 'example input',
        status: {
          enabled: true,
          settings: { enabled: true, secrets: true, pii: true, paths: false },
          stats: { redacted_total: 3, by_kind: {} },
          categories: [],
        },
        saving: false,
        testing: false,
        result: { masked: 'masked input', redacted: 2, by_kind: { EMAIL: 2 } },
        totalTokens: 1000,
        modelName: 'model-a',
        modelTokens: 800,
      },
    })

    expect(wrapper.text()).toContain('2 类规则已启用')
    expect(wrapper.text()).toContain('敏感信息不出这台电脑')
    expect(wrapper.text()).toContain('已脱敏 2 处 · 邮箱 2')
    expect(wrapper.text()).toContain('masked input')
  })
})

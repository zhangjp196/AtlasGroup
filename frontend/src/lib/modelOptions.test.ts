import { describe, expect, it } from 'vitest'
import type { ModelProfile, Subscription } from '@/api/types'
import { buildModelOptions } from './modelOptions'

const models: ModelProfile[] = [
  { code: 'default', name: 'Default' },
  { code: 'gpt-6', name: 'GPT-6', subscription_id: 'cc', subscription_name: 'Command Code' },
  { code: 'claude', name: 'Claude', subscription_id: 'cc', subscription_name: 'Command Code' },
  { code: 'fast', name: 'Fast', channel_id: 'oc', channel_name: 'OpenCode Go' },
  { code: 'orphan', name: 'Orphan' },
]

const subscriptions: Subscription[] = [
  { id: 'cc', name: 'Command Code' },
  { id: 'oc', name: 'OpenCode Go' },
  { id: 'empty', name: 'Empty' },
]

describe('buildModelOptions', () => {
  it('groups models by subscription and keeps default first', () => {
    const options = buildModelOptions(models, subscriptions)
    expect(options[0]).toEqual({ value: '__default__', label: '默认模型' })
    expect(options.map((item) => item.label)).toEqual([
      '默认模型', 'Command Code', 'OpenCode Go', '未绑定订阅',
    ])
    expect(options[1].children?.map((item) => item.value)).toEqual(['gpt-6', 'claude'])
    expect(options[2].children?.map((item) => item.value)).toEqual(['fast'])
    expect(options[3].children?.map((item) => item.value)).toEqual(['orphan'])
  })

  it('skips empty subscriptions and the built-in default profile', () => {
    const options = buildModelOptions(models, subscriptions)
    expect(options.some((item) => item.label === 'Empty')).toBe(false)
    for (const group of options) {
      expect(group.children?.some((item) => item.value === 'default')).toBeFalsy()
    }
  })

  it('uses custom default label and adds unknown selected code', () => {
    const options = buildModelOptions(models, subscriptions, '继承默认', 'legacy-model')
    expect(options[0]).toEqual({ value: '__default__', label: '继承默认' })
    expect(options[options.length - 1]).toEqual({
      value: '__unknown__',
      label: '当前模型',
      children: [{ value: 'legacy-model', label: 'legacy-model' }],
    })
  })

  it('does not add an unknown group for default or known codes', () => {
    expect(buildModelOptions(models, subscriptions, '默认', '').some((item) => item.value === '__unknown__')).toBe(false)
    expect(buildModelOptions(models, subscriptions, '默认', 'default').some((item) => item.value === '__unknown__')).toBe(false)
    expect(buildModelOptions(models, subscriptions, '默认', 'gpt-6').some((item) => item.value === '__unknown__')).toBe(false)
  })

  it('falls back to the model subscription name when the subscription list is stale', () => {
    const options = buildModelOptions([
      { code: 'x', name: 'X', subscription_id: 'gone', subscription_name: 'Gone Sub' },
    ], subscriptions)
    expect(options[1]).toMatchObject({ label: 'Gone Sub' })
  })
})

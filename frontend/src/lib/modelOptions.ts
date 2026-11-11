import type { ModelProfile, Subscription } from '@/api/types'

/** 二级模型选择：第一级订阅，第二级该订阅下的模型。 */
export interface ModelOption {
  value: string
  label: string
  children?: ModelOption[]
}

/**
 * 把扁平的模型档案列表整理成「订阅 → 模型」两级选项。
 *
 * - 第一项固定是「默认 / 继承」叶子节点，值为 `__default__`（对应空 model_code）。
 * - 没有模型的订阅不展示；没有订阅归属的模型放进「未绑定订阅」。
 * - 已选 code 不在当前列表时补一个「当前模型」分组，避免编辑旧数据时选择框空白。
 */
export function buildModelOptions(
  models: ModelProfile[],
  subscriptions: Subscription[] = [],
  defaultLabel = '默认模型',
  selectedCode = '',
): ModelOption[] {
  const groups = new Map<string, ModelOption>()
  for (const sub of subscriptions) {
    const id = String(sub.id || '').trim()
    if (!id) continue
    groups.set(`sub:${id}`, {
      value: `sub:${id}`,
      label: String(sub.name || id),
      children: [],
    })
  }

  for (const model of models) {
    const code = String(model.code || '').trim()
    if (!code || code === 'default') continue
    const sid = String(model.subscription_id || model.channel_id || '').trim()
    const key = sid ? `sub:${sid}` : '__none__'
    let group = groups.get(key)
    if (!group) {
      group = {
        value: key,
        label: String(model.subscription_name || model.channel_name || (sid || '未绑定订阅')),
        children: [],
      }
      groups.set(key, group)
    }
    group.children?.push({ value: code, label: String(model.name || code) })
  }

  const result: ModelOption[] = [{ value: '__default__', label: defaultLabel }]
  for (const group of groups.values()) {
    if (group.children?.length) result.push(group)
  }

  const selected = String(selectedCode || '')
  if (selected && selected !== 'default' && !models.some((model) => model.code === selected)) {
    result.push({
      value: '__unknown__',
      label: '当前模型',
      children: [{ value: selected, label: selected }],
    })
  }
  return result
}

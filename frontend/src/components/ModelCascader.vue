<script setup lang="ts">
import { ElCascader } from 'element-plus'
import type { CascaderOption } from 'element-plus'
import { computed } from 'vue'
import type { ModelProfile, Subscription } from '@/api/types'
import { buildModelOptions } from '@/lib/modelOptions'

const props = withDefaults(defineProps<{
  modelValue?: string
  models: ModelProfile[]
  subscriptions?: Subscription[]
  placeholder?: string
  defaultLabel?: string
  hideDefault?: boolean
  explicitDefault?: boolean
  size?: 'small' | 'default' | 'large'
  width?: string
}>(), {
  modelValue: '',
  subscriptions: () => [],
  placeholder: '默认模型',
  defaultLabel: '默认模型',
  size: 'default',
  width: '100%',
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

// buildModelOptions 返回的是自己的轻量结构；Cascader 只读 value/label/children，
// 这里按组件类型收窄，避免模板里再放宽成 any。
const options = computed<CascaderOption[]>(() => buildModelOptions(
  props.models,
  props.subscriptions,
  props.defaultLabel,
  String(props.modelValue || ''),
).filter((option) => !props.hideDefault || option.value !== '__default__') as unknown as CascaderOption[])

const value = computed(() => {
  const code = String(props.modelValue || '')
  return code === 'default' ? '__default__' : code || (props.hideDefault ? '' : '__default__')
})

const cascaderProps = {
  expandTrigger: 'hover' as const,
  emitPath: false,
}

function onChange(raw: unknown): void {
  const leaf = Array.isArray(raw) ? raw[raw.length - 1] : raw
  const code = leaf == null ? '' : String(leaf)
  emit('update:modelValue', code === '__default__' ? (props.explicitDefault ? 'default' : '') : code)
}
</script>

<template>
  <el-cascader
    :model-value="value"
    :options="options"
    :props="cascaderProps"
    :show-all-levels="false"
    :size="size"
    :placeholder="placeholder"
    :style="{ width }"
    popper-class="model-cascader-popper"
    filterable
    @update:model-value="onChange"
  />
</template>

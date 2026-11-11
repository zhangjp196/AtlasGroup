<script setup lang="ts">
import { ElInput, ElTag } from 'element-plus'
import { computed, ref, watch } from 'vue'
import ListPager from '@/components/ListPager.vue'
import Modal from '@/components/Modal.vue'
import { DEFAULT_PAGE_SIZE } from '@/lib/paging'
import { pageCount as calcPageCount, pageSlice } from '@/lib/workspaceOptions'

export interface PickerOption {
  value: string
  label: string
  description?: string
  tag?: string
  disabled?: boolean
}

const props = withDefaults(defineProps<{
  modelValue?: string
  items: PickerOption[]
  title?: string
  placeholder?: string
  emptyLabel?: string
  emptyHint?: string
  pageSize?: number
  disabled?: boolean
}>(), {
  modelValue: '',
  title: '选择',
  placeholder: '请选择',
  emptyLabel: '',
  emptyHint: '',
  pageSize: DEFAULT_PAGE_SIZE,
  disabled: false,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const open = ref(false)
const query = ref('')
const page = ref(1)
const pending = ref('')

const selected = computed(() => props.items.find((item) => item.value === props.modelValue) || null)
const selectedLabel = computed(() => {
  if (selected.value) return selected.value.label
  if (props.modelValue) return `${props.modelValue}（不存在）`
  return props.emptyLabel || props.placeholder
})
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return props.items.filter((item) => {
    if (!q) return true
    return `${item.label || ''} ${item.description || ''} ${item.value || ''}`
      .toLowerCase()
      .includes(q)
  })
})
const paged = computed(() => pageSlice(filtered.value, page.value, props.pageSize))
const pages = computed(() => calcPageCount(filtered.value.length, props.pageSize))
const showEmpty = computed(() => Boolean(props.emptyLabel) && !query.value.trim())

function openDialog(): void {
  if (props.disabled) return
  query.value = ''
  pending.value = String(props.modelValue || '')
  page.value = 1
  const index = filtered.value.findIndex((item) => item.value === pending.value)
  if (index >= 0) page.value = Math.floor(index / props.pageSize) + 1
  open.value = true
}

function choose(value: string): void {
  pending.value = value
}

function chooseAndConfirm(value: string): void {
  const item = props.items.find((entry) => entry.value === value)
  if (item?.disabled) return
  pending.value = value
  confirm()
}

function confirm(): void {
  emit('update:modelValue', pending.value)
  open.value = false
}

watch(query, () => { page.value = 1 })
watch(pages, (total) => {
  if (page.value > total) page.value = total
})
watch(() => props.items, () => { page.value = 1 })
</script>

<template>
  <div class="ws-picker opt-picker">
    <button
      type="button"
      class="input ws-picker-trigger"
      :class="{ 'is-empty': !selected && !modelValue }"
      :disabled="disabled"
      :title="selected?.description || ''"
      @click="openDialog"
    >
      <span class="ws-picker-value">{{ selectedLabel }}</span>
      <span class="ws-picker-caret">▾</span>
    </button>

    <Modal :open="open" :title="title" width="min(640px, 94vw)" @close="open = false">
      <div class="ws-picker-toolbar">
        <el-input v-model="query" placeholder="搜索名称 / 说明" clearable />
      </div>

      <div class="ws-picker-list">
        <button
          v-if="showEmpty"
          type="button"
          class="ws-picker-row"
          :class="{ active: pending === '' }"
          @click="choose('')"
        >
          <span class="ws-picker-check">{{ pending === '' ? '✓' : '' }}</span>
          <span class="ws-picker-main">
            <b>{{ emptyLabel }}</b>
            <small v-if="emptyHint">{{ emptyHint }}</small>
          </span>
        </button>

        <button
          v-for="item in paged"
          :key="item.value"
          type="button"
          class="ws-picker-row"
          :class="{ active: pending === item.value, muted: item.disabled }"
          :disabled="item.disabled"
          :title="item.description || ''"
          @click="choose(item.value)"
          @dblclick="chooseAndConfirm(item.value)"
        >
          <span class="ws-picker-check">{{ pending === item.value ? '✓' : '' }}</span>
          <span class="ws-picker-main">
            <span class="ws-picker-name">
              <b>{{ item.label }}</b>
              <el-tag v-if="item.tag" size="small" type="info" effect="plain">{{ item.tag }}</el-tag>
            </span>
            <small v-if="item.description">{{ item.description }}</small>
          </span>
        </button>

        <div v-if="!paged.length" class="empty">没有匹配的选项</div>
      </div>

      <ListPager
        :page="page"
        :total="filtered.length"
        :page-size="pageSize"
        @update:page="page = $event"
      />

      <template #footer>
        <button class="btn" type="button" @click="open = false">取消</button>
        <button class="btn primary" type="button" @click="confirm">确定</button>
      </template>
    </Modal>
  </div>
</template>

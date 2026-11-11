<script setup lang="ts">
import { ElInput, ElTag } from 'element-plus'
import { computed, ref, watch } from 'vue'
import ListPager from '@/components/ListPager.vue'
import Modal from '@/components/Modal.vue'
import type { Workspace } from '@/api/types'
import { DEFAULT_PAGE_SIZE } from '@/lib/paging'
import { filterWorkspaces, matchesWorkspace, pageCount as calcPageCount, pageSlice, type WorkspaceStatusFilter } from '@/lib/workspaceOptions'

const props = withDefaults(defineProps<{
  modelValue?: string
  workspaces: Workspace[]
  placeholder?: string
  emptyLabel?: string
  emptyHint?: string
  title?: string
  disabled?: boolean
  allowArchived?: boolean
  pageSize?: number
  variant?: 'input' | 'chip'
  emptyDisplay?: string
  triggerHint?: string
}>(), {
  modelValue: '',
  placeholder: '选择工作区',
  emptyLabel: '',
  emptyHint: '',
  title: '选择工作区',
  disabled: false,
  allowArchived: false,
  pageSize: DEFAULT_PAGE_SIZE,
  variant: 'input',
  emptyDisplay: '',
  triggerHint: '',
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const open = ref(false)
const query = ref('')
const status = ref<WorkspaceStatusFilter>('active')
const page = ref(1)
const pending = ref('')

const selected = computed(() => props.workspaces.find((item) => item.id === props.modelValue) || null)
const emptyText = computed(() => props.emptyDisplay || props.emptyLabel || props.placeholder)
const selectedLabel = computed(() => {
  if (selected.value) {
    return `${selected.value.name || selected.value.id}${selected.value.archived ? '（已归档）' : ''}`
  }
  if (props.modelValue) return `${props.modelValue}（不存在）`
  return emptyText.value
})
const triggerTitle = computed(() => selected.value?.root_path || props.triggerHint || '')

const searched = computed(() => props.workspaces.filter((item) => matchesWorkspace(item, query.value)))
const activeCount = computed(() => searched.value.filter((item) => !item.archived).length)
const filtered = computed(() => filterWorkspaces(props.workspaces, query.value, status.value))
const paged = computed(() => pageSlice(filtered.value, page.value, props.pageSize))
const pages = computed(() => calcPageCount(filtered.value.length, props.pageSize))
const showEmpty = computed(() => Boolean(props.emptyLabel) && !query.value.trim())

function isDisabled(workspace: Workspace): boolean {
  return Boolean(workspace.archived) && !props.allowArchived && workspace.id !== props.modelValue
}

function openDialog(): void {
  if (props.disabled) return
  const current = props.workspaces.find((item) => item.id === props.modelValue)
  query.value = ''
  status.value = current?.archived || !props.workspaces.some((item) => !item.archived)
    ? 'all'
    : 'active'
  pending.value = String(props.modelValue || '')
  page.value = 1
  const index = filtered.value.findIndex((item) => item.id === pending.value)
  if (index >= 0) page.value = Math.floor(index / props.pageSize) + 1
  open.value = true
}

function choose(id: string): void {
  pending.value = id
}

function chooseAndConfirm(id: string): void {
  const workspace = props.workspaces.find((item) => item.id === id)
  if (workspace && isDisabled(workspace)) return
  pending.value = id
  confirm()
}

function confirm(): void {
  emit('update:modelValue', pending.value)
  open.value = false
}

watch([query, status], () => { page.value = 1 })
watch(pages, (total) => {
  if (page.value > total) page.value = total
})
watch(() => props.workspaces, () => { page.value = 1 })
</script>

<template>
  <div class="ws-picker">
    <button
      v-if="variant === 'chip'"
      type="button"
      class="chip ws-picker-chip"
      :class="{ 'is-empty': !selected && !modelValue }"
      :disabled="disabled"
      :title="triggerTitle"
      @click="openDialog"
    >{{ selectedLabel }}</button>
    <button
      v-else
      type="button"
      class="input ws-picker-trigger"
      :class="{ 'is-empty': !selected && !modelValue }"
      :disabled="disabled"
      :title="triggerTitle"
      @click="openDialog"
    >
      <span class="ws-picker-value">{{ selectedLabel }}</span>
      <span class="ws-picker-caret">▾</span>
    </button>

    <Modal :open="open" :title="title" width="min(700px, 94vw)" @close="open = false">
      <div class="ws-picker-toolbar">
        <el-input v-model="query" placeholder="搜索名称 / 路径 / id" clearable />
        <div class="ws-picker-tabs" role="group" aria-label="工作区状态">
          <button
            type="button"
            class="ws-picker-tab"
            :class="{ on: status === 'active' }"
            @click="status = 'active'"
          >使用中 {{ activeCount }}</button>
          <button
            type="button"
            class="ws-picker-tab"
            :class="{ on: status === 'all' }"
            @click="status = 'all'"
          >全部 {{ searched.length }}</button>
        </div>
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
          v-for="workspace in paged"
          :key="workspace.id"
          type="button"
          class="ws-picker-row"
          :class="{ active: pending === workspace.id, muted: isDisabled(workspace) }"
          :disabled="isDisabled(workspace)"
          :title="isDisabled(workspace) ? '已归档工作区不能绑定' : workspace.root_path"
          @click="choose(workspace.id)"
          @dblclick="chooseAndConfirm(workspace.id)"
        >
          <span class="ws-picker-check">{{ pending === workspace.id ? '✓' : '' }}</span>
          <span class="ws-picker-main">
            <span class="ws-picker-name">
              <b>{{ workspace.name || workspace.id }}</b>
              <el-tag v-if="workspace.is_default" size="small" type="success" effect="plain">默认</el-tag>
              <el-tag v-if="workspace.archived" size="small" type="info" effect="plain">已归档</el-tag>
            </span>
            <small>{{ workspace.root_path || '聊天工作区（托管目录）' }}<template v-if="workspace.conversation_count"> · {{ workspace.conversation_count }} 个会话</template></small>
          </span>
        </button>

        <div v-if="!paged.length" class="empty">没有匹配的工作区</div>
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

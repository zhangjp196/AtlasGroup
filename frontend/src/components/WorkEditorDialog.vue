<script setup lang="ts">
import { ElButton } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import SideDrawer from '@/components/SideDrawer.vue'
import OptionPicker from '@/components/OptionPicker.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { WorkItem } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

export interface WorkSeed {
  title?: string
  body?: string
  workspace_id?: string
  role_id?: string
}

const props = defineProps<{
  open: boolean
  item?: WorkItem | null
  seed?: WorkSeed | null
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', id: string): void
}>()

const roleOptions = computed(() => data.persistentRoles.map((role) => ({
  value: role.id,
  label: role.name || role.id,
  description: [role.title, role.expertise].filter(Boolean).join(' · '),
  tag: role.scope === 'temporary' ? '临时' : '',
})))

const app = useAppStore()
const data = useDataStore()
const saving = ref(false)
const form = reactive({
  title: '',
  body: '',
  status: 'inbox',
  workspace_id: '',
  role_id: '',
  priority: false,
})

const isNew = computed(() => !props.item)
const title = computed(() => props.item ? '编辑工单' : '新建工单')

const statuses = [
  { value: 'inbox', label: '待分派' },
  { value: 'working', label: '进行中' },
  { value: 'needs_attention', label: '需要你' },
  { value: 'done', label: '已完成' },
  { value: 'closed', label: '已关闭' },
]

watch(() => [props.open, props.item?.id], () => {
  if (!props.open) return
  const item = props.item
  const seed = props.seed || {}
  form.title = item?.title || seed.title || ''
  form.body = item?.body || seed.body || ''
  form.status = item?.status || 'inbox'
  form.workspace_id = item?.workspace_id || seed.workspace_id || ''
  form.role_id = item?.role_id || seed.role_id || ''
  form.priority = Number(item?.priority || 0) > 0
}, { immediate: true })

async function save(): Promise<void> {
  if (saving.value) return
  const title = form.title.trim()
  if (!title) {
    app.toast('工单标题不能为空', 'bad')
    return
  }
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      title,
      body: form.body.trim(),
      workspace_id: form.workspace_id || null,
      role_id: form.role_id || null,
      priority: form.priority ? 1 : 0,
    }
    if (!isNew.value) body.status = form.status
    const item = props.item
      ? await api<WorkItem>(`/work-items/${encodeURIComponent(props.item.id)}`, { method: 'PATCH', body })
      : await api<WorkItem>('/work-items', { method: 'POST', body })
    await data.loadWork()
    app.toast(props.item ? '工单已更新' : '工单已创建')
    emit('saved', item.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <SideDrawer :open="open" :title="title" width="620px" @close="emit('close')">
    <div class="field">
      <label>标题</label>
      <input v-model="form.title" class="input" placeholder="例如：修复登录超时" maxlength="200" />
    </div>
    <div class="field">
      <label>说明</label>
      <textarea v-model="form.body" class="input" rows="5" placeholder="背景、验收标准、相关链接……" />
    </div>
    <div class="work-settings-grid">
      <div v-if="!isNew" class="field">
        <label>状态</label>
        <select v-model="form.status" class="input">
          <option v-for="status in statuses" :key="status.value" :value="status.value">{{ status.label }}</option>
        </select>
      </div>
      <div class="field">
        <label>工作区</label>
        <WorkspacePicker
          v-model="form.workspace_id"
          :workspaces="data.workspaces"
          empty-label="默认工作区"
          placeholder="默认工作区"
        />
      </div>
      <div class="field">
        <label>角色</label>
        <OptionPicker
          v-model="form.role_id"
          :items="roleOptions"
          title="选择角色"
          placeholder="默认角色"
          empty-label="默认角色"
          empty-hint="使用工单策略里的默认角色"
        />
      </div>
      <div class="field">
        <label>优先级</label>
        <label class="switch">
          <input v-model="form.priority" type="checkbox" />
          <span class="track" />
          <span class="switch-label">高优先级</span>
        </label>
      </div>
    </div>
    <template #footer>
      <el-button @click="emit('close')">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!form.title.trim()" @click="save">保存</el-button>
    </template>
  </SideDrawer>
</template>

<script setup lang="ts">
import { ElButton } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import AvatarField from '@/components/AvatarField.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import { api } from '@/api/client'
import type { Department } from '@/api/types'
import { departmentPath, departmentSubtree, orderedDepartments } from '@/lib/derive'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const props = defineProps<{
  open: boolean
  dept?: Department | null
  parentId?: string
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const saving = ref(false)
const form = reactive({ name: '', avatar: '', parent_id: '' })

const isNew = computed(() => !props.dept)
const title = computed(() => props.dept ? `编辑部门「${props.dept.name}」` : '新建部门')

const parentOptions = computed(() => {
  const exclude = props.dept ? departmentSubtree(props.dept.id, data.departments) : new Set<string>()
  return orderedDepartments(data.departments).filter((dept) => !exclude.has(dept.id))
})

watch(() => [props.open, props.dept?.id, props.parentId], () => {
  if (!props.open) return
  form.name = props.dept?.name || ''
  form.avatar = props.dept?.avatar || ''
  form.parent_id = props.dept ? (props.dept.parent_id || '') : (props.parentId || '')
}, { immediate: true })

async function save(): Promise<void> {
  if (saving.value) return
  const name = form.name.trim()
  if (!name) {
    app.toast('部门名称不能为空', 'bad')
    return
  }
  saving.value = true
  try {
    const body = {
      name,
      avatar: form.avatar || '',
      parent_id: form.parent_id || null,
    }
    const dept = props.dept
      ? await api<Department>(`/departments/${encodeURIComponent(props.dept.id)}`, { method: 'PATCH', body })
      : await api<Department>('/departments', { method: 'POST', body })
    await data.loadAll()
    app.toast(props.dept ? '部门已保存' : '部门已创建')
    emit('saved', dept.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <SideDrawer :open="open" :title="title" width="560px" @close="emit('close')">
    <div class="field">
      <label>部门头像</label>
      <AvatarField v-model="form.avatar" scope="department" :name="form.name || '部'" />
    </div>
    <div class="field">
      <label>部门名称</label>
      <input v-model="form.name" class="input" placeholder="例如：产品部" maxlength="120" />
    </div>
    <div class="field">
      <label>上级部门</label>
      <select v-model="form.parent_id" class="input">
        <option value="">无（根部门）</option>
        <option v-for="dept in parentOptions" :key="dept.id" :value="dept.id">
          {{ departmentPath(dept.id, data.departments) }}
        </option>
      </select>
      <small class="role-line">选择某个部门即成为它的子部门；部门支持多层。</small>
    </div>
    <template #footer>
      <el-button @click="emit('close')">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!form.name.trim()" @click="save">保存</el-button>
    </template>
  </SideDrawer>
</template>

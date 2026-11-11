<script setup lang="ts">
import { ElAlert, ElButton, ElForm, ElFormItem, ElInput, ElOption, ElSelect } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import ModelCascader from '@/components/ModelCascader.vue'
import OptionPicker from '@/components/OptionPicker.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { Role, Workspace, WorkItem } from '@/api/types'
import { hasNativeBridge, pickFolder } from '@/composables/useNative'
import { useAppStore } from '@/stores/app'
import { useChatWindowsStore } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'

export type CreateKind = 'conv' | 'ws' | 'role' | 'work'

const props = defineProps<{ open: boolean; kind: CreateKind }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', kind: CreateKind, id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const chatWindows = useChatWindowsStore()
const submitting = ref(false)

const convForm = reactive({ role_id: '', participant_ids: [] as string[], workspace_id: '' })
const wsForm = reactive({ name: '', path: '' })
const roleForm = reactive({ name: '', workspace_id: '', title: '', department_id: '', expertise: '', model_code: '' })
const workForm = reactive({ title: '', body: '', workspace_id: '', role_id: '' })

const roleOptions = computed(() => data.persistentRoles.map((role) => ({
  value: role.id,
  label: role.name || role.id,
  description: [role.title, role.department, role.expertise].filter(Boolean).join(' · '),
  tag: role.scope === 'temporary' ? '临时' : '',
})))

const participantOptions = computed(() => data.persistentRoles
  .filter((role) => role.id !== 'default')
  .map((role) => ({
    value: role.id,
    label: role.name || role.id,
    description: [role.title, role.department, role.expertise].filter(Boolean).join(' · '),
  })))

const departmentOptions = computed(() => data.departments.map((dept) => ({
  value: dept.id,
  label: dept.name || dept.id,
  description: dept.parent_id ? '子部门' : '一级部门',
})))

// 会话工作区默认跟随角色；也可以在这里直接指定（只影响这条会话）。
const followWorkspace = computed(() => {
  const roleId = convForm.role_id || 'default'
  const role = data.roles.find((item) => item.id === roleId)
  return data.workspaces.find((ws) => ws.id === String(role?.workspace_id || '')) || null
})
const convWorkspace = computed(() =>
  data.workspaces.find((ws) => ws.id === convForm.workspace_id) || followWorkspace.value)
const workspaceHint = computed(() => {
  const name = convWorkspace.value?.name || '默认工作区'
  return convForm.workspace_id
    ? `本会话固定使用「${name}」，之后切换角色不会覆盖`
    : `默认跟随角色：「${name}」`
})

const labels: Record<CreateKind, string> = {
  conv: '新建会话',
  ws: '添加工作区',
  role: '新建角色',
  work: '新建工单',
}

watch(() => [props.open, props.kind], () => {
  if (!props.open) return
  convForm.role_id = ''
  convForm.participant_ids = []
  convForm.workspace_id = ''
  wsForm.name = ''
  wsForm.path = ''
  roleForm.name = ''
  roleForm.workspace_id = ''
  roleForm.title = ''
  roleForm.department_id = ''
  roleForm.expertise = ''
  roleForm.model_code = ''
  workForm.title = ''
  workForm.body = ''
  workForm.workspace_id = ''
  workForm.role_id = ''
}, { immediate: true })

async function chooseFolder(): Promise<void> {
  if (!hasNativeBridge()) {
    app.toast('浏览器里请直接填绝对路径；桌面端可用原生选择器')
    return
  }
  const path = await pickFolder()
  if (path) {
    wsForm.path = path
    if (!wsForm.name) wsForm.name = path.split('/').filter(Boolean).pop() || ''
  }
}

async function submit(): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  try {
    if (props.kind === 'conv') {
      if (!convForm.role_id) {
        app.toast('先选择一个角色', 'bad')
        return
      }
      // 会话延迟创建：先开草稿窗口，发出第一条消息时才写入后端。
      chatWindows.openDraft(convForm.role_id, {
        participantIds: convForm.participant_ids,
        workspaceId: convForm.workspace_id,
      })
      emit('close')
      return
    } else if (props.kind === 'ws') {
      const ws = await api<Workspace>('/workspaces', {
        method: 'POST',
        body: { name: wsForm.name, path: wsForm.path },
      })
      await data.loadAll()
      emit('created', 'ws', ws.id)
    } else if (props.kind === 'role') {
      const role = await api<Role>('/roles', { method: 'POST', body: roleForm })
      await data.loadAll()
      emit('created', 'role', role.id)
    } else {
      const item = await api<WorkItem>('/work-items', {
        method: 'POST',
        body: {
          title: workForm.title,
          body: workForm.body,
          workspace_id: workForm.workspace_id || undefined,
          role_id: workForm.role_id || undefined,
        },
      })
      await data.loadWork()
      emit('created', 'work', item.id)
    }
    emit('close')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <SideDrawer :open="open" :title="labels[kind]" subtitle="填写后创建，随时可以在详情里继续调整" width="600px" @close="emit('close')">
    <el-form label-position="top" class="atlas-form">
      <template v-if="kind === 'conv'">
        <el-form-item label="主持角色">
          <OptionPicker
            v-model="convForm.role_id"
            :items="roleOptions"
            title="选择主持角色"
            placeholder="选择角色"
          />
        </el-form-item>
        <el-form-item label="参与角色（可选，多角色协作）">
          <el-select
            v-model="convForm.participant_ids"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            :multiple-limit="12"
            placeholder="先只和主持角色对话"
            style="width:100%"
          >
            <el-option v-for="role in participantOptions" :key="role.value" :label="role.label" :value="role.value">
              <span>{{ role.label }}</span>
              <small v-if="role.description" class="role-line" style="margin-left:8px">{{ role.description }}</small>
            </el-option>
          </el-select>
          <small class="role-line">参与角色只加入本会话，可随时在聊天头部「参与角色」里增删或新建临时角色。</small>
        </el-form-item>
        <el-form-item label="工作区">
          <WorkspacePicker
            v-model="convForm.workspace_id"
            :workspaces="data.workspaces"
            empty-label="跟随角色（默认）"
            empty-hint="切换主持角色时自动更新"
            placeholder="跟随角色（默认）"
          />
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          :title="workspaceHint"
        />
      </template>

      <template v-else-if="kind === 'ws'">
        <el-form-item label="本地文件夹绝对路径">
          <div style="display:flex;gap:8px;width:100%">
            <el-input v-model="wsForm.path" placeholder="/Users/you/project" />
            <el-button @click="chooseFolder">选择文件夹</el-button>
          </div>
        </el-form-item>
        <el-form-item label="名称（可选）">
          <el-input v-model="wsForm.name" placeholder="留空使用文件夹名" />
        </el-form-item>
      </template>

      <template v-else-if="kind === 'role'">
        <el-form-item label="名字"><el-input v-model="roleForm.name" /></el-form-item>
        <el-form-item label="岗位"><el-input v-model="roleForm.title" /></el-form-item>
        <el-form-item label="工作区">
          <WorkspacePicker
            v-model="roleForm.workspace_id"
            :workspaces="data.workspaces"
            empty-label="不绑定（默认工作区）"
            placeholder="不绑定（默认工作区）"
          />
        </el-form-item>
        <el-form-item label="部门">
          <OptionPicker
            v-model="roleForm.department_id"
            :items="departmentOptions"
            title="选择部门"
            placeholder="未安排"
            empty-label="未安排"
            empty-hint="留空表示暂不归属部门"
          />
        </el-form-item>
        <el-form-item label="专长"><el-input v-model="roleForm.expertise" /></el-form-item>
        <el-form-item label="模型（可选）">
          <ModelCascader
            v-model="roleForm.model_code"
            :models="data.models"
            :subscriptions="data.subscriptions"
            default-label="继承默认"
            placeholder="继承默认"
          />
        </el-form-item>
      </template>

      <template v-else>
        <el-form-item label="标题"><el-input v-model="workForm.title" /></el-form-item>
        <el-form-item label="正文"><el-input v-model="workForm.body" type="textarea" :rows="5" /></el-form-item>
        <el-form-item label="工作区（可选）">
          <WorkspacePicker
            v-model="workForm.workspace_id"
            :workspaces="data.workspaces"
            empty-label="不绑定"
            placeholder="不绑定"
          />
        </el-form-item>
      </template>
    </el-form>

    <template #footer>
      <el-button @click="emit('close')">取消</el-button>
      <el-button
        type="primary"
        :loading="submitting"
        :disabled="kind === 'ws' ? !wsForm.path : kind === 'role' ? !roleForm.name : kind === 'work' ? !workForm.title : kind === 'conv' ? !convForm.role_id : false"
        @click="submit"
      >
        {{ kind === 'ws' ? '添加' : '创建' }}
      </el-button>
    </template>
  </SideDrawer>
</template>

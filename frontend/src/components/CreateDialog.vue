<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import Avatar from '@/components/Avatar.vue'
import AvatarField from '@/components/AvatarField.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import { api } from '@/api/client'
import type { Conversation, Role, Team, Workspace, WorkItem } from '@/api/types'
import { hasNativeBridge, pickFolder } from '@/composables/useNative'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

export type CreateKind = 'conv' | 'ws' | 'role' | 'team' | 'work'

const props = defineProps<{ open: boolean; kind: CreateKind }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', kind: CreateKind, id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const submitting = ref(false)

const convForm = reactive({ role_id: '', team_id: '' })
const wsForm = reactive({ name: '', path: '' })
const roleForm = reactive({ name: '', workspace_id: '', title: '', department_id: '', expertise: '', model_code: '' })
const teamForm = reactive({ name: '', workspace_id: '', avatar: '', mode: 'orchestrate', member_ids: [] as string[] })
const workForm = reactive({ title: '', body: '', workspace_id: '', role_id: '', team_id: '' })

const selectedMembers = computed(() => teamForm.member_ids
  .map((id) => data.persistentRoles.find((role) => role.id === id))
  .filter((role): role is Role => Boolean(role)))

// 会话工作区由所选角色 / 群组决定，不再在会话上单独选择。
const convWorkspace = computed(() => {
  const team = data.teams.find((item) => item.id === convForm.team_id)
  const teamWs = String(team?.workspace_id || '')
  if (teamWs) return data.workspaces.find((ws) => ws.id === teamWs) || null
  const roleId = convForm.role_id || 'default'
  const role = data.roles.find((item) => item.id === roleId)
  return data.workspaces.find((ws) => ws.id === String(role?.workspace_id || '')) || null
})

const labels: Record<CreateKind, string> = {
  conv: '新建会话',
  ws: '添加工作区',
  role: '新建角色',
  team: '新建群组',
  work: '新建工单',
}

watch(() => [props.open, props.kind], () => {
  if (!props.open) return
  convForm.role_id = ''
  convForm.team_id = ''
  wsForm.name = ''
  wsForm.path = ''
  roleForm.name = ''
  roleForm.workspace_id = ''
  roleForm.title = ''
  roleForm.department_id = ''
  roleForm.expertise = ''
  roleForm.model_code = ''
  teamForm.name = ''
  teamForm.workspace_id = ''
  teamForm.avatar = ''
  teamForm.mode = 'orchestrate'
  teamForm.member_ids = []
  workForm.title = ''
  workForm.body = ''
  workForm.workspace_id = ''
  workForm.role_id = ''
  workForm.team_id = ''
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
      const conv = await api<Conversation>('/conversations', {
        method: 'POST',
        body: {
          role_id: convForm.role_id || undefined,
          team_id: convForm.team_id || undefined,
        },
      })
      await data.loadAll()
      emit('created', 'conv', conv.id)
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
    } else if (props.kind === 'team') {
      const team = await api<Team>('/teams', {
        method: 'POST',
        body: {
          name: teamForm.name,
          workspace_id: teamForm.workspace_id || undefined,
          avatar: teamForm.avatar || '',
          mode: teamForm.mode,
          members: selectedMembers.value.map((role) => ({
            id: role.id,
            name: role.name,
            expertise: role.expertise || '',
          })),
        },
      })
      await data.loadAll()
      emit('created', 'team', team.id)
    } else {
      const item = await api<WorkItem>('/work-items', {
        method: 'POST',
        body: {
          title: workForm.title,
          body: workForm.body,
          workspace_id: workForm.workspace_id || undefined,
          role_id: workForm.role_id || undefined,
          team_id: workForm.team_id || undefined,
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
  <SideDrawer :open="open" :title="labels[kind]" subtitle="填写后创建，随时可以在详情里继续调整" :width="kind === 'team' ? '680px' : '600px'" @close="emit('close')">
    <el-form label-position="top" class="atlas-form">
      <template v-if="kind === 'conv'">
        <el-form-item label="单聊 · 选一个角色">
          <el-select v-model="convForm.role_id" placeholder="通用助手（默认）" style="width:100%">
            <el-option label="通用助手（默认）" value="default" />
            <el-option
              v-for="role in data.persistentRoles.filter((item) => item.id !== 'default')"
              :key="role.id"
              :label="`${role.name}${role.title ? ` · ${role.title}` : ''}`"
              :value="role.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="或群聊（优先于单聊）">
          <el-select v-model="convForm.team_id" placeholder="不开群聊" style="width:100%">
            <el-option label="不开群聊" value="" />
            <el-option
              v-for="team in data.teams.filter((t) => (t.members || []).length)"
              :key="team.id"
              :label="`${team.name} · ${(team.members || []).length} 个角色`"
              :value="team.id"
            />
          </el-select>
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          :title="`工作区：${convWorkspace?.name || '默认工作区'}（由所选角色 / 群组绑定）`"
        />
        <el-alert type="info" :closable="false" show-icon title="单聊就是一个角色和你聊天；需要多角色协作时再开群聊。" />
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
          <el-select v-model="roleForm.workspace_id" placeholder="不绑定（默认工作区）" style="width:100%">
            <el-option label="不绑定（默认工作区）" value="" />
            <el-option v-for="ws in data.activeWorkspaces" :key="ws.id" :label="ws.name" :value="ws.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="roleForm.department_id" placeholder="未安排" style="width:100%">
            <el-option label="未安排" value="" />
            <el-option v-for="dept in data.departments" :key="dept.id" :label="dept.name" :value="dept.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="专长"><el-input v-model="roleForm.expertise" /></el-form-item>
        <el-form-item label="模型（可选）">
          <el-select v-model="roleForm.model_code" placeholder="继承默认" style="width:100%">
            <el-option label="继承默认" value="" />
            <el-option v-for="model in data.models" :key="model.code" :label="model.name || model.code" :value="model.code" />
          </el-select>
        </el-form-item>
      </template>

      <template v-else-if="kind === 'team'">
        <el-form-item label="群名">
          <el-input v-model="teamForm.name" placeholder="例如：研发群" maxlength="120" />
        </el-form-item>
        <el-form-item label="群头像">
          <AvatarField v-model="teamForm.avatar" scope="team" :name="teamForm.name || '群'" />
        </el-form-item>
        <el-form-item label="绑定工作区">
          <el-select v-model="teamForm.workspace_id" placeholder="默认工作区" style="width:100%">
            <el-option label="默认工作区" value="" />
            <el-option v-for="ws in data.activeWorkspaces" :key="ws.id" :label="ws.name" :value="ws.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="协作方式">
          <el-select v-model="teamForm.mode" style="width:100%">
            <el-option label="主持编排" value="orchestrate" />
            <el-option label="圆桌" value="roundtable" />
            <el-option label="单角色" value="single" />
          </el-select>
        </el-form-item>
        <el-form-item label="成员">
          <div class="team-pick-list">
            <label v-for="role in data.persistentRoles" :key="role.id" class="member member-pick">
              <input v-model="teamForm.member_ids" type="checkbox" :value="role.id" />
              <Avatar :name="role.name || '角'" :avatar="role.avatar" round />
              <span class="row-main">
                <span class="row-title">{{ role.name }}</span>
                <span class="row-sub">{{ [role.title, role.department, role.expertise].filter(Boolean).join(' · ') || '没有补充说明' }}</span>
              </span>
            </label>
            <div v-if="!data.persistentRoles.length" class="empty">角色库还是空的，先到组织与角色里添加角色。</div>
          </div>
        </el-form-item>
      </template>

      <template v-else>
        <el-form-item label="标题"><el-input v-model="workForm.title" /></el-form-item>
        <el-form-item label="正文"><el-input v-model="workForm.body" type="textarea" :rows="5" /></el-form-item>
        <el-form-item label="工作区（可选）">
          <el-select v-model="workForm.workspace_id" placeholder="不绑定" style="width:100%">
            <el-option label="不绑定" value="" />
            <el-option v-for="ws in data.activeWorkspaces" :key="ws.id" :label="ws.name" :value="ws.id" />
          </el-select>
        </el-form-item>
      </template>
    </el-form>

    <template #footer>
      <el-button @click="emit('close')">取消</el-button>
      <el-button
        type="primary"
        :loading="submitting"
        :disabled="kind === 'ws' ? !wsForm.path : kind === 'role' ? !roleForm.name : kind === 'team' ? !teamForm.name : kind === 'work' ? !workForm.title : false"
        @click="submit"
      >
        {{ kind === 'team' ? '保存' : '创建' }}
      </el-button>
    </template>
  </SideDrawer>
</template>

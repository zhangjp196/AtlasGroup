<script setup lang="ts">
import { ElAlert, ElButton, ElForm, ElFormItem, ElInput, ElOption, ElSelect } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import Avatar from '@/components/Avatar.vue'
import AvatarField from '@/components/AvatarField.vue'
import ModelCascader from '@/components/ModelCascader.vue'
import OptionPicker from '@/components/OptionPicker.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { Role, Team, Workspace, WorkItem } from '@/api/types'
import { hasNativeBridge, pickFolder } from '@/composables/useNative'
import { useAppStore } from '@/stores/app'
import { useChatWindowsStore } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'

export type CreateKind = 'conv' | 'ws' | 'role' | 'team' | 'work'

const props = defineProps<{ open: boolean; kind: CreateKind }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', kind: CreateKind, id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const chatWindows = useChatWindowsStore()
const submitting = ref(false)

const convForm = reactive({ team_id: '', role_id: '', participant_ids: [] as string[], workspace_id: '' })
const wsForm = reactive({ name: '', path: '' })
const roleForm = reactive({ name: '', workspace_id: '', title: '', department_id: '', expertise: '', model_code: '' })
const teamForm = reactive({ name: '', workspace_id: '', avatar: '', description: '', mode: 'orchestrate', max_speakers: 4, member_ids: [] as string[] })
const workForm = reactive({ title: '', body: '', workspace_id: '', role_id: '', team_id: '' })

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

const groupOptions = computed(() => data.groups
  .filter((team) => (team.members || []).length)
  .map((team) => ({
    value: team.id,
    label: `${team.name} · ${(team.members || []).length} 个角色`,
    description: [team.mode, team.description].filter(Boolean).join(' · '),
  })))
const selectedGroup = computed(() => data.groups.find(
  (team) => team.id === convForm.team_id,
) || null)

const labels: Record<CreateKind, string> = {
  conv: '新建会话',
  ws: '添加工作区',
  role: '新建角色',
  team: '新建群组',
  work: '新建工单',
}

watch(() => [props.open, props.kind], () => {
  if (!props.open) return
  convForm.team_id = ''
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
  teamForm.name = ''
  teamForm.workspace_id = ''
  teamForm.avatar = ''
  teamForm.description = ''
  teamForm.mode = 'orchestrate'
  teamForm.max_speakers = 4
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
      const group = selectedGroup.value
      if (!group && !convForm.role_id) {
        app.toast('先选择一个角色或群组', 'bad')
        return
      }
      if (group) {
        chatWindows.openDraft(group.leader?.id || group.members?.[0]?.id || '', {
          teamId: group.id,
          workspaceId: convForm.workspace_id,
        })
        emit('close')
        return
      }
      // 会话延迟创建：先开草稿窗口，发出第一条消息时才写入后端。
      chatWindows.openDraft(convForm.role_id, {
        participantIds: convForm.participant_ids,
        workspaceId: convForm.workspace_id,
      })
      emit('close')
      return
    } else if (props.kind === 'team') {
      const team = await api<Team>('/teams', {
        method: 'POST',
        body: {
          name: teamForm.name,
          workspace_id: teamForm.workspace_id || undefined,
          avatar: teamForm.avatar || '',
          description: teamForm.description || '',
          mode: teamForm.mode,
          max_speakers: teamForm.max_speakers,
          members: teamForm.member_ids.map((id) => {
            const role = data.persistentRoles.find((item) => item.id === id)
            return { id, name: role?.name || '', expertise: role?.expertise || '' }
          }),
        },
      })
      await data.loadAll()
      emit('created', 'team', team.id)
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
  <SideDrawer :open="open" :title="labels[kind]" subtitle="填写后创建，随时可以在详情里继续调整" :width="kind === 'team' ? '680px' : '600px'" @close="emit('close')">
    <el-form label-position="top" class="atlas-form">
      <template v-if="kind === 'conv'">
        <el-form-item label="群聊（可选，优先级高于单聊）">
          <el-select v-model="convForm.team_id" clearable placeholder="不开群聊，按角色单聊 / 多角色" style="width:100%">
            <el-option label="不开群聊" value="" />
            <el-option v-for="group in groupOptions" :key="group.value" :label="group.label" :value="group.value">
              <span>{{ group.label }}</span>
              <small v-if="group.description" class="role-line" style="margin-left:8px">{{ group.description }}</small>
            </el-option>
          </el-select>
          <small class="role-line">群聊成员由群组统一维护；已有群聊会话会直接打开。</small>
        </el-form-item>

        <template v-if="!convForm.team_id">
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
        </template>

        <el-alert
          v-if="selectedGroup"
          type="info"
          :closable="false"
          show-icon
          :title="`群聊：${selectedGroup.name} · ${(selectedGroup.members || []).length} 个角色 · ${selectedGroup.mode || 'orchestrate'}`"
        />

        <el-form-item label="工作区">
          <WorkspacePicker
            v-model="convForm.workspace_id"
            :workspaces="data.workspaces"
            empty-label="无工作区"
            placeholder="无工作区"
          />
        </el-form-item>
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
            empty-label="无工作区"
            placeholder="无工作区"
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

      <template v-else-if="kind === 'team'">
        <el-form-item label="群名">
          <el-input v-model="teamForm.name" placeholder="例如：研发群" maxlength="120" />
        </el-form-item>
        <el-form-item label="群头像">
          <AvatarField v-model="teamForm.avatar" scope="team" :name="teamForm.name || '群'" />
        </el-form-item>
        <el-form-item label="群介绍">
          <el-input v-model="teamForm.description" type="textarea" :rows="2" maxlength="1000" placeholder="这个群一起做什么" />
        </el-form-item>
        <el-form-item label="绑定工作区">
          <WorkspacePicker
            v-model="teamForm.workspace_id"
            :workspaces="data.workspaces"
            empty-label="无工作区"
            placeholder="无工作区"
          />
        </el-form-item>
        <el-form-item label="协作方式">
          <el-select v-model="teamForm.mode" style="width:100%">
            <el-option label="主持编排（默认）" value="orchestrate" />
            <el-option label="圆桌（成员各自应答）" value="roundtable" />
            <el-option label="单角色（只作预设）" value="single" />
          </el-select>
        </el-form-item>
        <el-form-item label="圆桌发言上限">
          <el-input v-model.number="teamForm.max_speakers" type="number" min="1" max="12" style="width:120px" />
          <small class="role-line" style="margin-left:8px">1–12，默认 4</small>
        </el-form-item>
        <el-form-item label="成员（群主由系统默认主持，可在群资料里更换）">
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
        :disabled="kind === 'ws' ? !wsForm.path : kind === 'role' ? !roleForm.name : kind === 'team' ? !teamForm.name : kind === 'work' ? !workForm.title : kind === 'conv' ? (!convForm.role_id && !convForm.team_id) : false"
        @click="submit"
      >
        {{ kind === 'ws' ? '添加' : kind === 'team' ? '保存' : '创建' }}
      </el-button>
    </template>
  </SideDrawer>
</template>

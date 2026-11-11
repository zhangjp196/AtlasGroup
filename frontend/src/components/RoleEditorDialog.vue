<script setup lang="ts">
import { ElButton } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import AvatarField from '@/components/AvatarField.vue'
import ListPager from '@/components/ListPager.vue'
import ModelCascader from '@/components/ModelCascader.vue'
import OptionPicker from '@/components/OptionPicker.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { Role, ToolItem } from '@/api/types'
import { departmentPath } from '@/lib/derive'
import { DEFAULT_PAGE_SIZE, pageSlice } from '@/lib/paging'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const props = defineProps<{
  open: boolean
  role?: Role | null
  presetDepartmentId?: string
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const tools = ref<ToolItem[]>([])
const toolSearch = ref('')
const saving = ref(false)
const toolsLoaded = ref(false)

const form = reactive({
  name: '',
  workspace_id: '',
  title: '',
  department_id: '',
  expertise: '',
  persona: '',
  avatar: '',
  tools: [] as string[],
  model_code: '',
})

const isNew = computed(() => !props.role)
const title = computed(() => props.role ? `编辑「${props.role.name}」` : '新建角色')

const departmentOptions = computed(() => data.departments.map((dept) => ({
  value: dept.id,
  label: departmentPath(dept.id, data.departments),
  description: dept.parent_id ? '子部门' : '一级部门',
})))

const filteredTools = computed(() => {
  const q = toolSearch.value.trim().toLowerCase()
  if (!q) return tools.value
  return tools.value.filter((tool) => `${tool.code || ''} ${tool.name || ''} ${tool.description || ''}`.toLowerCase().includes(q))
})

// 工具可能几十个：按每页 12 个分页，搜索后回到第一页。
const toolPage = ref(1)
const pagedTools = computed(() => pageSlice(filteredTools.value, toolPage.value, DEFAULT_PAGE_SIZE))
const toolPages = computed(() => Math.max(1, Math.ceil(filteredTools.value.length / DEFAULT_PAGE_SIZE)))
watch([toolSearch, filteredTools], () => { toolPage.value = 1 })
watch(toolPages, (total) => { toolPage.value = Math.min(toolPage.value, total) })

watch(() => [props.open, props.role?.id], () => {
  if (!props.open) return
  const role = props.role
  form.name = role?.name || ''
  form.workspace_id = role?.workspace_id || ''
  form.title = role?.title || ''
  form.department_id = role?.department_id || props.presetDepartmentId || ''
  form.expertise = role?.expertise || ''
  form.persona = role?.persona || ''
  form.avatar = role?.avatar || ''
  form.tools = [...(role?.tools || [])]
  form.model_code = role?.model_code || ''
  toolSearch.value = ''
  if (!toolsLoaded.value) void loadTools()
}, { immediate: true })

async function loadTools(): Promise<void> {
  try {
    const out = await api<{ items: ToolItem[] }>('/app/tools')
    tools.value = out.items || []
    toolsLoaded.value = true
  } catch {
    tools.value = []
  }
}

function toggleTool(code?: string): void {
  if (!code) return
  form.tools = form.tools.includes(code)
    ? form.tools.filter((item) => item !== code)
    : [...form.tools, code]
}

function setRawTools(event: Event): void {
  const text = (event.target as HTMLInputElement).value
  form.tools = text.split(',').map((item) => item.trim()).filter(Boolean)
}

function selectAll(): void {
  form.tools = filteredTools.value.map((tool) => String(tool.code || '')).filter(Boolean)
}

function clearTools(): void {
  form.tools = []
}

async function save(): Promise<void> {
  if (saving.value) return
  const name = form.name.trim()
  if (!name) {
    app.toast('角色名不能为空', 'bad')
    return
  }
  saving.value = true
  try {
    const body = {
      name,
      workspace_id: form.workspace_id || '',
      title: form.title.trim(),
      department_id: form.department_id || undefined,
      expertise: form.expertise.trim(),
      persona: form.persona.trim(),
      avatar: form.avatar || '',
      tools: form.tools,
      model_code: form.model_code || undefined,
    }
    const role = props.role
      ? await api<Role>(`/roles/${encodeURIComponent(props.role.id)}`, { method: 'PATCH', body })
      : await api<Role>('/roles', { method: 'POST', body })
    await data.loadAll()
    app.toast('角色已保存')
    emit('saved', role.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <SideDrawer :open="open" :title="title" width="660px" @close="emit('close')">
    <div class="field">
      <label>角色头像</label>
      <AvatarField v-model="form.avatar" scope="role" :name="form.name || '角'" />
    </div>
    <div class="field">
      <label>名称（谁）</label>
      <input v-model="form.name" class="input" placeholder="例如：张三 / 审阅者" maxlength="120" />
    </div>
    <div class="field">
      <label>岗位（做什么）</label>
      <input v-model="form.title" class="input" placeholder="例如：后端工程师 / 合同审阅" maxlength="120" />
    </div>
    <div class="field">
      <label>绑定工作区 <span class="role-line">决定这个角色的文件和终端上下文</span></label>
      <WorkspacePicker
        v-model="form.workspace_id"
        :workspaces="data.workspaces"
        empty-label="不绑定（使用默认工作区）"
        placeholder="不绑定（使用默认工作区）"
      />
      <small class="role-line">会话使用这个角色时，会自动在该工作区里运行。</small>
    </div>
    <div class="field">
      <label>所属部门 <span class="role-line">可留空 = 未安排</span></label>
      <OptionPicker
        v-model="form.department_id"
        :items="departmentOptions"
        title="选择部门"
        placeholder="未安排"
        empty-label="未安排"
        empty-hint="留空表示暂不归属部门"
      />
      <small class="role-line">部门支持多级；在「组织与角色」里可以新建子部门。</small>
    </div>
    <div class="field">
      <label>专长（一句话）</label>
      <input v-model="form.expertise" class="input" maxlength="1000" />
    </div>
    <div class="field">
      <label>人格 / 工作方式</label>
      <textarea v-model="form.persona" class="input" rows="5" maxlength="4000" placeholder="说话方式、工作习惯、注意事项……" />
    </div>
    <div class="field">
      <label>工具权限 <span class="role-line">不选 = 全部工具</span></label>
      <input v-if="tools.length" v-model="toolSearch" class="input" placeholder="搜索工具…" autocomplete="off" />
      <input
        v-else
        class="input mono"
        :value="form.tools.join(', ')"
        placeholder="read_file, shell（留空 = 全部）"
        @input="setRawTools"
      />
      <div v-if="filteredTools.length" class="tool-picker">
        <label v-for="tool in pagedTools" :key="tool.code" class="tool-choice" :title="tool.description || ''">
          <input type="checkbox" :checked="form.tools.includes(String(tool.code))" @change="toggleTool(tool.code)" />
          <span>{{ tool.name || tool.code }}</span>
          <span class="tool-choice-code">{{ tool.code }}</span>
        </label>
      </div>
      <div v-else class="tool-picker"><span class="role-line">没有匹配的工具</span></div>
      <ListPager
        :page="toolPage"
        :total="filteredTools.length"
        :page-size="DEFAULT_PAGE_SIZE"
        @update:page="toolPage = $event"
      />
      <div class="tool-picker-actions">
        <button class="mini-link" type="button" @click="selectAll">全选</button>
        <button class="mini-link" type="button" @click="clearTools">清空</button>
        <span>{{ form.tools.length ? `已选 ${form.tools.length} 个` : '未选 = 全部工具' }}</span>
      </div>
    </div>
    <div class="field">
      <label>模型档案</label>
      <ModelCascader
        v-model="form.model_code"
        :models="data.models"
        :subscriptions="data.subscriptions"
        default-label="未指定（跟随系统）"
        placeholder="未指定（跟随系统）"
      />
      <small class="role-line">不指定时跟随系统模型；指定后优先使用该角色的模型。</small>
    </div>

    <template #footer>
      <el-button @click="emit('close')">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!form.name.trim()" @click="save">保存</el-button>
    </template>
  </SideDrawer>
</template>

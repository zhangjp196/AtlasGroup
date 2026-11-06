<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import Avatar from '@/components/Avatar.vue'
import Markdown from '@/components/chat/Markdown.vue'
import Modal from '@/components/Modal.vue'
import { api } from '@/api/client'
import type { Role, Skill, ToolItem } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const props = defineProps<{ open: boolean; role: Role | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'edit'): void
  (e: 'saved'): void
  (e: 'deleted', id: string): void
  (e: 'chat', id: string): void
  (e: 'work', id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const tools = ref<ToolItem[]>([])
const skills = ref<Skill[]>([])
const skillsLoading = ref(false)
const deleting = ref(false)
const workspaceSaving = ref(false)
const workspaceDraft = ref('')

const skillOpen = ref(false)
const skillSaving = ref(false)
const skillForm = reactive({ name: '', description: '', content: '' })

const mergeOpen = ref(false)
const mergeSaving = ref(false)
const mergePicked = ref<string[]>([])
const mergeForm = reactive({ name: '', description: '' })

const toolsLoaded = ref(false)

const temporary = computed(() => props.role?.scope === 'temporary')
const toolNames = computed(() => {
  const map = new Map<string, string>()
  for (const tool of tools.value) {
    if (tool.code) map.set(String(tool.code), String(tool.name || tool.code))
  }
  return map
})
const roleTools = computed(() => props.role?.tools || [])

function toolName(code: string): string {
  return toolNames.value.get(code) || code
}

watch(() => [props.open, props.role?.id], () => {
  if (!props.open || !props.role) return
  workspaceDraft.value = String(props.role.workspace_id || '')
  skills.value = []
  void loadSkills(props.role.id)
  if (!toolsLoaded.value) void loadTools()
}, { immediate: true })

// 外部（编辑弹窗 / 其他页面）改了绑定工作区时，选择器保持同步。
watch(() => props.role?.workspace_id, (id) => {
  workspaceDraft.value = String(id || '')
})

async function loadTools(): Promise<void> {
  try {
    const out = await api<{ items: ToolItem[] }>('/app/tools')
    tools.value = out.items || []
    toolsLoaded.value = true
  } catch {
    tools.value = []
  }
}

async function loadSkills(roleId: string): Promise<void> {
  skillsLoading.value = true
  try {
    const out = await api<{ items: Skill[] }>(`/app/skills?role_id=${encodeURIComponent(roleId)}&include_global=false`)
    if (props.role?.id === roleId) skills.value = out.items || []
  } catch (error) {
    skills.value = []
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    skillsLoading.value = false
  }
}

async function switchWorkspace(event: Event): Promise<void> {
  const role = props.role
  if (!role || workspaceSaving.value) return
  const wsId = (event.target as HTMLSelectElement).value
  const previous = String(role.workspace_id || '')
  if (wsId === previous) return
  workspaceSaving.value = true
  try {
    await api(`/roles/${encodeURIComponent(role.id)}`, {
      method: 'PATCH',
      body: { workspace_id: wsId },
    })
    await data.loadAll()
    emit('saved')
    app.toast(wsId ? `工作区已切换为「${workspaceName(wsId)}」` : '已改为默认工作区')
  } catch (error) {
    workspaceDraft.value = previous
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    workspaceSaving.value = false
  }
}

function workspaceName(id?: string): string {
  const wsId = String(id || '')
  return data.workspaces.find((ws) => ws.id === wsId)?.name || '默认工作区'
}

function close(): void {
  emit('close')
}

function openChat(): void {
  if (props.role) emit('chat', props.role.id)
}

function openWork(): void {
  if (props.role) emit('work', props.role.id)
}

function openSkillEditor(): void {
  skillForm.name = ''
  skillForm.description = ''
  skillForm.content = ''
  skillOpen.value = true
}

async function saveSkill(): Promise<void> {
  if (!props.role || skillSaving.value) return
  if (!skillForm.name.trim() || !skillForm.content.trim()) {
    app.toast('技能名称和正文不能为空', 'bad')
    return
  }
  skillSaving.value = true
  try {
    await api('/app/skills', {
      method: 'POST',
      body: {
        name: skillForm.name.trim(),
        description: skillForm.description.trim(),
        content: skillForm.content.trim(),
        role_id: props.role.id,
      },
    })
    await loadSkills(props.role.id)
    skillOpen.value = false
    app.toast('专属技能已创建')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    skillSaving.value = false
  }
}

function openMerge(): void {
  if (!props.role || skills.value.length < 2) {
    app.toast('至少需要 2 个专属技能才能合并', 'bad')
    return
  }
  mergePicked.value = []
  mergeForm.name = ''
  mergeForm.description = ''
  mergeOpen.value = true
}

function toggleMerge(id: string): void {
  mergePicked.value = mergePicked.value.includes(id)
    ? mergePicked.value.filter((item) => item !== id)
    : [...mergePicked.value, id]
}

async function mergeSkills(): Promise<void> {
  if (!props.role || mergeSaving.value) return
  if (!mergeForm.name.trim()) {
    app.toast('合并后的名称不能为空', 'bad')
    return
  }
  if (mergePicked.value.length < 2) {
    app.toast('至少选择 2 个技能', 'bad')
    return
  }
  mergeSaving.value = true
  try {
    await api('/app/skills/merge', {
      method: 'POST',
      body: {
        role_id: props.role.id,
        skill_ids: mergePicked.value,
        name: mergeForm.name.trim(),
        description: mergeForm.description.trim(),
      },
    })
    await loadSkills(props.role.id)
    mergeOpen.value = false
    app.toast('技能已合并')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    mergeSaving.value = false
  }
}

async function deleteSkill(skill: Skill): Promise<void> {
  if (!props.role) return
  if (!window.confirm(`删除专属技能「${skill.name}」？`)) return
  try {
    await api(`/app/skills/${encodeURIComponent(skill.id)}`, { method: 'DELETE' })
    await loadSkills(props.role.id)
    app.toast('技能已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function promote(): Promise<void> {
  if (!props.role) return
  try {
    await api(`/roles/${encodeURIComponent(props.role.id)}/promote`, { method: 'POST' })
    await data.loadAll()
    app.toast('已存为持久角色')
    emit('saved')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function removeRole(): Promise<void> {
  if (!props.role || deleting.value) return
  if (props.role.system) {
    app.toast('默认角色不能删除', 'bad')
    return
  }
  if (!window.confirm(`删除角色「${props.role.name}」？`)) return
  deleting.value = true
  try {
    await api(`/roles/${encodeURIComponent(props.role.id)}`, { method: 'DELETE' })
    await data.loadAll()
    app.toast('角色已删除')
    emit('deleted', props.role.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-show="open" class="drawer-scrim" @click="close" />
    <aside v-show="open" class="side-drawer" aria-label="角色资料">
      <header class="side-drawer-head">
        <div>
          <h2>角色资料</h2>
          <p>
            {{ [role?.name, role?.title, role?.department, temporary ? '临时角色' : '持久角色'].filter(Boolean).join(' · ') }}
          </p>
        </div>
        <button class="iconbtn" type="button" title="关闭" aria-label="关闭" @click="close">
          <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>

      <div v-if="role" class="side-drawer-body">
        <div class="group-identity">
          <Avatar :name="role.name || '角'" :avatar="role.avatar" round :size="48" />
          <div class="group-identity-main">
            <div class="role-detail-name">
              {{ role.name || '角色' }}
              <span class="tag" :class="{ tmp: temporary }">{{ temporary ? '临时' : '持久' }}</span>
            </div>
            <div class="role-line"><b>{{ role.title || '未填写岗位' }}</b></div>
            <div class="role-line">
              <template v-if="role.department">部门：{{ role.department }}</template>
              <span v-else class="role-warn">未安排部门</span>
            </div>
            <div class="role-line">{{ role.expertise || '没有填写专长' }}</div>
            <div class="role-line mono">模型：{{ role.model_code || '未指定' }}</div>
            <div class="role-line role-ws-line">
              <span>工作区</span>
              <select
                class="role-ws-select"
                :value="workspaceDraft"
                :disabled="workspaceSaving"
                :title="workspaceSaving ? '正在切换工作区…' : '直接切换角色绑定的工作区'"
                @change="switchWorkspace"
              >
                <option value="">默认工作区（不绑定）</option>
                <option v-for="ws in data.activeWorkspaces" :key="ws.id" :value="ws.id">{{ ws.name }}</option>
                <option
                  v-if="workspaceDraft && !data.activeWorkspaces.some((ws) => ws.id === workspaceDraft)"
                  :value="workspaceDraft"
                >{{ workspaceName(workspaceDraft) }}（已归档）</option>
              </select>
              <span v-if="workspaceSaving" class="role-line">切换中…</span>
            </div>
          </div>
        </div>

        <section class="group-section">
          <div class="group-section-head"><h3>人格 / 工作方式</h3></div>
          <div class="work-body">
            <Markdown v-if="role.persona" :text="role.persona" />
            <span v-else>（未填写）</span>
          </div>
        </section>

        <section class="group-section">
          <div class="group-section-head">
            <h3>工具权限</h3>
            <span>{{ roleTools.length ? `${roleTools.length} 个工具` : '全部工具' }}</span>
          </div>
          <div class="chips">
            <span v-for="code in roleTools" :key="code" class="chip" :title="code">{{ toolName(code) }}</span>
            <span v-if="!roleTools.length" class="chip">全部工具</span>
          </div>
        </section>

        <section class="group-section">
          <div class="group-section-head">
            <h3>专属技能</h3>
            <span>{{ skillsLoading ? '加载中…' : `${skills.length} / 20` }}</span>
          </div>
          <p class="group-mode-hint" style="margin:0">
            只属于「{{ role.name || '当前角色' }}」；其他角色看不到，也不会出现在它们的技能清单里。可通过文件、图片、网页搜索等材料整理后创建。
          </p>
          <div class="role-skill-list">
            <div v-for="skill in skills" :key="skill.id" class="role-skill-row">
              <div class="role-skill-main">
                <b>{{ skill.name }}</b>
                <span>{{ skill.description || '无描述' }}</span>
              </div>
              <button class="iconbtn" type="button" title="删除技能" aria-label="删除技能" @click="deleteSkill(skill)">×</button>
            </div>
            <div v-if="!skills.length && !skillsLoading" class="group-empty">还没有专属技能</div>
          </div>
          <div class="avatar-actions">
            <button class="btn small" type="button" @click="openSkillEditor">新建专属技能</button>
            <button class="btn small" type="button" :disabled="skills.length < 2" @click="openMerge">合并所选…</button>
          </div>
        </section>

        <section class="group-section">
          <div class="group-section-head"><h3>角色信息</h3></div>
          <div class="work-meta" style="border:0;padding:0">
            <span>ID <b>{{ role.id }}</b></span>
            <span>来源 <b>{{ role.source || 'user' }}</b></span>
            <span>创建 <b>{{ role.created_at || '' }}</b></span>
          </div>
        </section>

        <section v-if="!role.system" class="group-section group-danger">
          <div class="group-section-head"><h3>删除角色</h3></div>
          <p class="group-mode-hint" style="margin:0">只删除角色本身，已有会话保留；临时角色删除后不可恢复。</p>
          <button class="btn danger" type="button" :disabled="deleting" @click="removeRole">{{ deleting ? '删除中…' : '删除角色' }}</button>
        </section>
      </div>

      <footer class="side-drawer-foot">
        <button class="btn primary" type="button" @click="openChat">单聊</button>
        <button class="btn" type="button" @click="emit('edit')">编辑</button>
        <button class="btn" type="button" @click="openWork">新建工单</button>
        <button v-if="temporary" class="btn" type="button" @click="promote">存为持久角色</button>
        <span class="spacer" />
        <button class="btn" type="button" @click="close">关闭</button>
      </footer>
    </aside>

    <Modal :open="skillOpen" title="新建专属技能" @close="skillOpen = false">
      <div class="field">
        <label>技能名称</label>
        <input v-model="skillForm.name" class="input" placeholder="例如：合同条款审阅" maxlength="200" />
      </div>
      <div class="field">
        <label>一句话描述</label>
        <input v-model="skillForm.description" class="input" placeholder="什么时候用、能做什么" maxlength="1000" />
      </div>
      <div class="field">
        <label>SKILL.md 正文</label>
        <textarea v-model="skillForm.content" class="input mono" rows="10" placeholder="适用场景、步骤、检查项、注意事项……" />
      </div>
      <p class="role-line">只能由该角色使用；可先用文件、图片或网页搜索整理材料，再把结论写进正文。</p>
      <template #footer>
        <el-button @click="skillOpen = false">取消</el-button>
        <el-button type="primary" :loading="skillSaving" :disabled="!skillForm.name.trim() || !skillForm.content.trim()" @click="saveSkill">创建</el-button>
      </template>
    </Modal>

    <Modal :open="mergeOpen" title="合并专属技能" @close="mergeOpen = false">
      <div class="field">
        <label>选择要合并的技能（至少 2 个）</label>
        <div class="role-skill-picker">
          <label v-for="skill in skills" :key="skill.id" class="role-skill-choice">
            <input type="checkbox" :checked="mergePicked.includes(skill.id)" @change="toggleMerge(skill.id)" />
            <span>{{ skill.name }}<small>{{ skill.description || '' }}</small></span>
          </label>
        </div>
      </div>
      <div class="field">
        <label>合并后的名称</label>
        <input v-model="mergeForm.name" class="input" placeholder="例如：综合审阅技能" maxlength="200" />
      </div>
      <div class="field">
        <label>合并后的描述</label>
        <input v-model="mergeForm.description" class="input" placeholder="合并后的适用场景" maxlength="1000" />
      </div>
      <p class="role-line">来源技能会被删除，合并后的技能仍只属于该角色。</p>
      <template #footer>
        <el-button @click="mergeOpen = false">取消</el-button>
        <el-button type="primary" :loading="mergeSaving" :disabled="mergePicked.length < 2 || !mergeForm.name.trim()" @click="mergeSkills">合并</el-button>
      </template>
    </Modal>
  </Teleport>
</template>

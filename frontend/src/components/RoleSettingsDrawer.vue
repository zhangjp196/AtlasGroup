<script setup lang="ts">
import { ElButton } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import Avatar from '@/components/Avatar.vue'
import ListPager from '@/components/ListPager.vue'
import Markdown from '@/components/chat/Markdown.vue'
import SessionList from '@/components/chat/SessionList.vue'
import Modal from '@/components/Modal.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { Role, Skill, ToolItem } from '@/api/types'
import { DEFAULT_PAGE_SIZE, pageSlice } from '@/lib/paging'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const props = defineProps<{ open: boolean; role: Role | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'edit'): void
  (e: 'saved'): void
  (e: 'deleted', id: string): void
  (e: 'chat', id: string): void
  (e: 'new-chat', id: string): void
  (e: 'session', id: string): void
  (e: 'work', id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
// 详情按「栏目」分页：概览 / 会话 / 能力 / 信息，避免所有内容堆在一个滚动区里。
const tab = ref<'overview' | 'sessions' | 'ability' | 'info'>('overview')
const tools = ref<ToolItem[]>([])
const skills = ref<Skill[]>([])
const skillsLoading = ref(false)
const deleting = ref(false)
const workspaceSaving = ref(false)
const workspaceDraft = ref('')
interface ScopeGrant { id: string; tool: string; scope?: string; created_at?: string }
const grants = ref<ScopeGrant[]>([])
const grantsLoading = ref(false)

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

// 角色详情的工具 / 授权 / 技能都可能超过 12 条，统一按每页 12 条分页。
const toolPage = ref(1)
const grantPage = ref(1)
const skillPage = ref(1)
const mergePage = ref(1)
const pagedRoleTools = computed(
  () => pageSlice(roleTools.value, toolPage.value, DEFAULT_PAGE_SIZE),
)
const pagedGrants = computed(
  () => pageSlice(grants.value, grantPage.value, DEFAULT_PAGE_SIZE),
)
const pagedSkills = computed(
  () => pageSlice(skills.value, skillPage.value, DEFAULT_PAGE_SIZE),
)
const pagedMergeSkills = computed(
  () => pageSlice(skills.value, mergePage.value, DEFAULT_PAGE_SIZE),
)
watch(roleTools, () => { toolPage.value = 1 })
watch(grants, () => { grantPage.value = 1 })
watch(skills, () => { skillPage.value = 1; mergePage.value = 1 })

function toolName(code: string): string {
  return toolNames.value.get(code) || code
}

watch(() => [props.open, props.role?.id], () => {
  if (!props.open || !props.role) return
  tab.value = 'overview'
  workspaceDraft.value = String(props.role.workspace_id || '')
  skills.value = []
  void loadGrants(props.role.id)
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

async function loadGrants(roleId: string): Promise<void> {
  grantsLoading.value = true
  try {
    const out = await api<{ items: ScopeGrant[] }>(
      `/app/permissions/grants?role_id=${encodeURIComponent(roleId)}`)
    if (props.role?.id === roleId) grants.value = out.items || []
  } catch {
    if (props.role?.id === roleId) grants.value = []
  } finally {
    grantsLoading.value = false
  }
}

async function revokeGrant(grantId: string): Promise<void> {
  try {
    await api(`/app/permissions/grants/${encodeURIComponent(grantId)}`, { method: 'DELETE' })
    if (props.role?.id) await loadGrants(props.role.id)
    app.toast('永久授权已撤销')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
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

async function switchWorkspace(value?: string): Promise<void> {
  const role = props.role
  if (!role || workspaceSaving.value) return
  const wsId = String(value || '')
  const previous = String(role.workspace_id || '')
  if (wsId === previous) return
  workspaceDraft.value = wsId
  workspaceSaving.value = true
  try {
    await api(`/roles/${encodeURIComponent(role.id)}`, {
      method: 'PATCH',
      body: { workspace_id: wsId },
    })
    await data.loadAll()
    emit('saved')
    app.toast(wsId ? `工作区已切换为「${workspaceName(wsId)}」` : '已改为无工作区')
  } catch (error) {
    workspaceDraft.value = previous
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    workspaceSaving.value = false
  }
}

function workspaceName(id?: string): string {
  const wsId = String(id || '')
  return data.workspaces.find((ws) => ws.id === wsId)?.name || '无工作区'
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
        <div class="seg drawer-tabs" role="tablist" aria-label="角色详情栏目">
          <button class="seg-btn" :class="{ on: tab === 'overview' }" type="button" @click="tab = 'overview'">概览</button>
          <button v-if="!temporary" class="seg-btn" :class="{ on: tab === 'sessions' }" type="button" @click="tab = 'sessions'">会话</button>
          <button class="seg-btn" :class="{ on: tab === 'ability' }" type="button" @click="tab = 'ability'">能力</button>
          <button class="seg-btn" :class="{ on: tab === 'info' }" type="button" @click="tab = 'info'">信息</button>
        </div>

        <template v-if="tab === 'overview'">
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
              <WorkspacePicker
                :model-value="workspaceDraft"
                :workspaces="data.workspaces"
                :disabled="workspaceSaving"
                empty-label="无工作区"
                placeholder="无工作区"
                :title="workspaceSaving ? '正在切换工作区…' : '直接切换角色绑定的工作区'"
                @update:model-value="switchWorkspace"
              />
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
        </template>

        <section v-if="tab === 'sessions' && !temporary" class="group-section">
          <div class="group-section-head">
            <h3>会话</h3>
            <span>这个角色的历史会话</span>
            <button class="btn small" type="button" @click="emit('new-chat', role.id)">新会话</button>
          </div>
          <SessionList
            :role-id="role.id"
            :page-size="6"
            empty-text="还没有会话。点「新会话」开始第一条。"
            @open="emit('session', $event)"
          />
        </section>

        <section v-if="tab === 'ability'" class="group-section">
          <div class="group-section-head">
            <h3>工具权限</h3>
            <span>{{ roleTools.length ? `${roleTools.length} 个工具` : '全部工具' }}</span>
          </div>
          <div class="chips">
            <span v-for="code in pagedRoleTools" :key="code" class="chip" :title="code">{{ toolName(code) }}</span>
            <span v-if="!roleTools.length" class="chip">全部工具</span>
          </div>
          <ListPager
            :page="toolPage"
            :total="roleTools.length"
            :page-size="DEFAULT_PAGE_SIZE"
            @update:page="toolPage = $event"
          />
        </section>

        <section v-if="tab === 'ability'" class="group-section">
          <div class="group-section-head">
            <h3>永久工具授权</h3>
            <span>{{ grantsLoading ? '加载中…' : (grants.length ? `${grants.length} 条` : '未设置') }}</span>
          </div>
          <div v-if="grants.length" class="grant-list">
            <div v-for="grant in pagedGrants" :key="grant.id" class="grant-row">
              <span class="grant-main">
                <b>{{ toolName(grant.tool) }}</b>
                <small>{{ grant.tool }}</small>
              </span>
              <button class="iconbtn" type="button" title="撤销永久授权" aria-label="撤销永久授权" @click="revokeGrant(grant.id)">×</button>
            </div>
          </div>
          <div v-else class="group-empty">在审批弹窗里选「永久（本角色）」后会出现在这里，跨会话生效。</div>
          <ListPager
            :page="grantPage"
            :total="grants.length"
            :page-size="DEFAULT_PAGE_SIZE"
            @update:page="grantPage = $event"
          />
        </section>

        <section v-if="tab === 'ability'" class="group-section">
          <div class="group-section-head">
            <h3>专属技能</h3>
            <span>{{ skillsLoading ? '加载中…' : `${skills.length} / 20` }}</span>
          </div>
          <p class="group-mode-hint" style="margin:0">
            只属于「{{ role.name || '当前角色' }}」；其他角色看不到，也不会出现在它们的技能清单里。可通过文件、图片、网页搜索等材料整理后创建。
          </p>
          <div class="role-skill-list">
            <div v-for="skill in pagedSkills" :key="skill.id" class="role-skill-row">
              <div class="role-skill-main">
                <b>{{ skill.name }}</b>
                <span>{{ skill.description || '无描述' }}</span>
              </div>
              <button class="iconbtn" type="button" title="删除技能" aria-label="删除技能" @click="deleteSkill(skill)">×</button>
            </div>
            <div v-if="!skills.length && !skillsLoading" class="group-empty">还没有专属技能</div>
          </div>
          <ListPager
            :page="skillPage"
            :total="skills.length"
            :page-size="DEFAULT_PAGE_SIZE"
            @update:page="skillPage = $event"
          />
          <div class="avatar-actions">
            <button class="btn small" type="button" @click="openSkillEditor">新建专属技能</button>
            <button class="btn small" type="button" :disabled="skills.length < 2" @click="openMerge">合并所选…</button>
          </div>
        </section>

        <section v-if="tab === 'info'" class="group-section">
          <div class="group-section-head"><h3>角色信息</h3></div>
          <div class="work-meta" style="border:0;padding:0">
            <span>ID <b>{{ role.id }}</b></span>
            <span>来源 <b>{{ role.source || 'user' }}</b></span>
            <span>创建 <b>{{ role.created_at || '' }}</b></span>
          </div>
        </section>

        <section v-if="tab === 'info' && !role.system" class="group-section group-danger">
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
          <label v-for="skill in pagedMergeSkills" :key="skill.id" class="role-skill-choice">
            <input type="checkbox" :checked="mergePicked.includes(skill.id)" @change="toggleMerge(skill.id)" />
            <span>{{ skill.name }}<small>{{ skill.description || '' }}</small></span>
          </label>
        </div>
        <ListPager
          :page="mergePage"
          :total="skills.length"
          :page-size="DEFAULT_PAGE_SIZE"
          @update:page="mergePage = $event"
        />
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

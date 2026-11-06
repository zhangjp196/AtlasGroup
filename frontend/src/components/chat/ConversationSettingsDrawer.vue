<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import Avatar from '@/components/Avatar.vue'
import GroupSettingsDrawer from '@/components/GroupSettingsDrawer.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import { api } from '@/api/client'
import type { Conversation, Role, Team, TeamMember } from '@/api/types'
import { GLOBAL_AGENT_ZONE } from '@/lib/derive'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const props = defineProps<{ open: boolean; conversation: Conversation | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', conversation: Conversation): void
  (e: 'deleted', id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const saving = ref(false)
const removing = ref(false)
const tempRoles = ref<Role[]>([])
const tempLoading = ref(false)
const groupOpen = ref(false)
const tempOpen = ref(false)
const tempSaving = ref(false)
const tempForm = reactive({ name: '', expertise: '', persona: '' })

const draft = reactive({
  title: '',
  role_id: '',
  team_id: '',
})

const isGlobal = computed(() => String(props.conversation?.zone || '') === GLOBAL_AGENT_ZONE)
const selectedTeam = computed<Team | null>(() => data.teams.find((team) => team.id === draft.team_id) || null)
const groupTeams = computed(() => data.teams.filter((team) => (team.members || []).length))
const availableRoles = computed(() => [
  ...data.persistentRoles.filter((role) => role.id !== 'default'),
  ...tempRoles.value.filter((role) => !data.persistentRoles.some((item) => item.id === role.id)),
])
const memberIds = computed(() => new Set((selectedTeam.value?.members || []).map((member) => String(member.id || ''))))
const conversationWorkspace = computed(() => {
  const wsId = String(props.conversation?.workspace_id || '')
  return data.workspaces.find((ws) => ws.id === wsId) || null
})

function loadDraft(): void {
  const conv = props.conversation
  draft.title = conv?.title || ''
  draft.role_id = conv?.role_id || 'default'
  draft.team_id = conv?.team_id || ''
}

watch(() => [props.open, props.conversation?.id], () => {
  if (!props.open) {
    groupOpen.value = false
    tempOpen.value = false
    return
  }
  loadDraft()
  void loadTempRoles()
}, { immediate: true })

async function loadTempRoles(): Promise<void> {
  const conv = props.conversation
  if (!conv?.id) return
  tempLoading.value = true
  try {
    const out = await api<{ items: Role[] }>(
      `/roles?include_temporary=true&session_id=${encodeURIComponent(conv.id)}`,
    )
    tempRoles.value = (out.items || []).filter((role) => role.scope === 'temporary')
  } catch {
    tempRoles.value = []
  } finally {
    tempLoading.value = false
  }
}

function memberBrief(role: Role): TeamMember {
  return {
    id: role.id,
    name: role.name,
    title: role.title || '',
    department: role.department || '',
    avatar: role.avatar || '',
    expertise: role.expertise || '',
    model_code: role.model_code || '',
  }
}

function openTempRole(): void {
  tempForm.name = ''
  tempForm.expertise = ''
  tempForm.persona = ''
  tempOpen.value = true
}

async function createTempRole(): Promise<void> {
  const conv = props.conversation
  const name = tempForm.name.trim()
  if (!conv || !name || tempSaving.value) return
  tempSaving.value = true
  try {
    const role = await api<Role>('/roles', {
      method: 'POST',
      body: {
        name,
        expertise: tempForm.expertise.trim(),
        persona: tempForm.persona.trim(),
        scope: 'temporary',
        session_id: conv.id,
      },
    })
    const team = selectedTeam.value
    if (team) {
      const members = [...(team.members || []), memberBrief(role)]
      await api(`/teams/${encodeURIComponent(team.id)}`, { method: 'PATCH', body: { members } })
    }
    await Promise.all([data.loadAll(), loadTempRoles()])
    tempOpen.value = false
    app.toast(`已创建临时角色：${role.name}`, 'ok', 2600)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    tempSaving.value = false
  }
}

async function addTempToTeam(role: Role): Promise<void> {
  const team = selectedTeam.value
  if (!team) {
    app.toast('先给会话绑定一个群组，再加入群聊', 'bad')
    return
  }
  if (memberIds.value.has(role.id)) {
    app.toast('这个角色已经在群里了', 'ok', 1800)
    return
  }
  try {
    const members = [...(team.members || []), memberBrief(role)]
    await api(`/teams/${encodeURIComponent(team.id)}`, { method: 'PATCH', body: { members } })
    await data.loadAll()
    app.toast(`已把「${role.name}」加入群聊`)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function removeTempRole(role: Role): Promise<void> {
  if (!window.confirm(`删除临时角色「${role.name}」？`)) return
  try {
    const team = selectedTeam.value
    if (team && memberIds.value.has(role.id)) {
      const members = (team.members || []).filter((member) => String(member.id || '') !== role.id)
      await api(`/teams/${encodeURIComponent(team.id)}`, { method: 'PATCH', body: { members } })
    }
    await api(`/roles/${encodeURIComponent(role.id)}`, { method: 'DELETE' })
    await Promise.all([data.loadAll(), loadTempRoles()])
    app.toast('临时角色已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function promoteTempRole(role: Role): Promise<void> {
  try {
    await api(`/roles/${encodeURIComponent(role.id)}/promote`, { method: 'POST' })
    await Promise.all([data.loadAll(), loadTempRoles()])
    app.toast(`「${role.name}」已转为持久角色`, 'ok', 2600)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function save(): Promise<void> {
  const conv = props.conversation
  if (!conv || saving.value) return
  const title = draft.title.trim()
  if (!title) {
    app.toast('会话标题不能为空', 'bad')
    return
  }
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      title,
    }
    if (!isGlobal.value) {
      if (draft.team_id) {
        body.team_id = draft.team_id
      } else {
        body.team_id = ''
        body.role_id = draft.role_id || ''
      }
    }
    const updated = await api<Conversation>(
      `/conversations/${encodeURIComponent(conv.id)}/settings`,
      { method: 'PATCH', body },
    )
    await data.loadAll()
    emit('saved', updated)
    app.toast('会话设置已保存')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    saving.value = false
  }
}

async function removeConversation(): Promise<void> {
  const conv = props.conversation
  if (!conv || removing.value) return
  if (!window.confirm(`删除会话「${draft.title || conv.title || conv.id}」？历史消息会一并删除。`)) return
  removing.value = true
  try {
    await api(`/conversations/${encodeURIComponent(conv.id)}`, { method: 'DELETE' })
    await data.loadAll()
    emit('deleted', conv.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    removing.value = false
  }
}

function onGroupSaved(): void {
  groupOpen.value = false
  void data.loadAll()
}
</script>

<template>
  <Teleport to="body">
    <div v-show="open" class="drawer-scrim" @click="emit('close')" />
    <aside v-show="open" class="side-drawer" aria-label="会话管理">
      <header class="side-drawer-head">
        <div>
          <h2>会话管理</h2>
          <p>{{ draft.title || conversation?.title || '未命名会话' }} · {{ isGlobal ? '全局 Agent' : '角色与群组' }}</p>
        </div>
        <button class="iconbtn" type="button" title="关闭" aria-label="关闭" @click="emit('close')">
          <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>

      <div class="side-drawer-body">
        <section class="group-section">
          <div class="group-section-head">
            <h3>基本</h3>
            <span>标题与文件上下文</span>
          </div>
          <div class="field">
            <label>会话标题</label>
            <input v-model="draft.title" class="input" maxlength="200" placeholder="给会话起个名字" />
          </div>
          <div class="field">
            <label>工作区</label>
            <input class="input" :value="conversationWorkspace?.name || '默认工作区'" readonly />
            <small class="role-line">工作区由所选角色 / 群组决定，切换角色或群组会自动更新。</small>
          </div>
        </section>

        <template v-if="!isGlobal">
          <section class="group-section">
            <div class="group-section-head">
              <h3>角色 / 群组</h3>
              <span>单聊选角色，群聊选群组</span>
            </div>
            <div class="field">
              <label>群组（优先于单聊）</label>
              <select v-model="draft.team_id" class="input">
                <option value="">不开群聊（单聊）</option>
                <option v-for="team in groupTeams" :key="team.id" :value="team.id">
                  {{ team.name }} · {{ (team.members || []).length }} 个角色
                </option>
              </select>
            </div>
            <div v-if="!draft.team_id" class="field">
              <label>单聊角色</label>
              <select v-model="draft.role_id" class="input">
                <option value="default">通用助手（默认）</option>
                <option v-for="role in availableRoles" :key="role.id" :value="role.id">
                  {{ role.name }}{{ role.title ? ` · ${role.title}` : '' }}{{ role.scope === 'temporary' ? '（临时）' : '' }}
                </option>
              </select>
            </div>

            <div v-if="selectedTeam" class="conv-group-preview">
              <div class="group-section-head">
                <h3>群成员 · {{ (selectedTeam.members || []).length }} 人</h3>
                <button class="btn small" type="button" @click="groupOpen = true">编辑群资料</button>
              </div>
              <div class="group-member-list">
                <div v-for="member in selectedTeam.members || []" :key="member.id || member.name" class="group-member">
                  <Avatar :name="member.name || '角'" :avatar="member.avatar" round />
                  <div class="group-member-main">
                    <div class="group-member-name"><span>{{ member.name }}</span></div>
                    <div class="group-member-sub">{{ [member.title, member.department, member.expertise].filter(Boolean).join(' · ') || '群成员' }}</div>
                  </div>
                </div>
                <div v-if="!(selectedTeam.members || []).length" class="group-empty">这个群还没有成员，先编辑群资料添加。</div>
              </div>
            </div>
          </section>

          <section class="group-section">
            <div class="group-section-head">
              <h3>临时角色</h3>
              <span>只属于本会话</span>
              <button class="btn small" type="button" @click="openTempRole">＋ 新建</button>
            </div>
            <div class="group-member-list">
              <div v-for="role in tempRoles" :key="role.id" class="group-member">
                <Avatar :name="role.name || '角'" :avatar="role.avatar" round />
                <div class="group-member-main">
                  <div class="group-member-name"><span>{{ role.name }}</span><span class="tag grp">临时</span></div>
                  <div class="group-member-sub">{{ [role.title, role.expertise].filter(Boolean).join(' · ') || '本会话临时角色' }}</div>
                </div>
                <div class="group-member-actions">
                  <button v-if="selectedTeam && !memberIds.has(role.id)" class="btn small" type="button" @click="addTempToTeam(role)">入群</button>
                  <button class="btn small" type="button" @click="promoteTempRole(role)">保存为持久</button>
                  <button class="iconbtn" type="button" title="删除" aria-label="删除" @click="removeTempRole(role)">
                    <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13" /></svg>
                  </button>
                </div>
              </div>
              <div v-if="tempLoading" class="group-empty">加载临时角色…</div>
              <div v-else-if="!tempRoles.length" class="group-empty">还没有临时角色。新建后可以加入当前群聊。</div>
            </div>
          </section>
        </template>

        <section v-else class="group-section">
          <div class="group-section-head"><h3>全局 Agent</h3><span>AtlasX 仅在此会话使用</span></div>
          <p class="group-mode-hint" style="margin:0">全局 Agent 负责跨会话授权、未读、运行状态与组织管理；普通会话不会出现 AtlasX。</p>
        </section>

        <section class="group-section group-danger">
          <div class="group-section-head"><h3>删除会话</h3></div>
          <p class="group-mode-hint" style="margin:0">删除后历史消息一并移除，且不可恢复。</p>
          <button class="btn danger" type="button" :disabled="removing" @click="removeConversation">{{ removing ? '删除中…' : '删除会话' }}</button>
        </section>
      </div>

      <footer class="side-drawer-foot">
        <button class="btn" type="button" @click="emit('close')">取消</button>
        <span class="spacer" />
        <button class="btn primary" type="button" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存修改' }}</button>
      </footer>
    </aside>

    <SideDrawer :open="tempOpen" title="新建临时角色（仅本会话）" width="520px" @close="tempOpen = false">
      <div class="field"><label>名字</label><input v-model="tempForm.name" class="input" placeholder="例如：调研员" /></div>
      <div class="field"><label>专长（一句话）</label><input v-model="tempForm.expertise" class="input" placeholder="只查资料，不改代码" /></div>
      <div class="field"><label>人格 / 工作方式</label><textarea v-model="tempForm.persona" class="input" rows="5" /></div>
      <div class="hintline">临时角色只属于当前会话；会话删除后自动清理。</div>
      <template #footer>
        <button class="btn" @click="tempOpen = false">取消</button>
        <button class="btn primary" :disabled="!tempForm.name.trim() || tempSaving" @click="createTempRole">{{ tempSaving ? '创建中…' : '创建' }}</button>
      </template>
    </SideDrawer>

    <GroupSettingsDrawer
      :open="groupOpen"
      :team="selectedTeam"
      @close="groupOpen = false"
      @saved="onGroupSaved"
      @deleted="() => { groupOpen = false; draft.team_id = ''; void data.loadAll() }"
    />
  </Teleport>
</template>

<script setup lang="ts">
import { ElButton } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import AvatarField from '@/components/AvatarField.vue'
import Modal from '@/components/Modal.vue'
import WorkEditorDialog, { type WorkSeed } from '@/components/WorkEditorDialog.vue'
import SessionList from '@/components/chat/SessionList.vue'
import { api } from '@/api/client'
import type { Conversation, ListResponse, Role, Team, TeamMember } from '@/api/types'
import { compactNumber } from '@/lib/format'
import { usageSummary } from '@/lib/usage'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

interface ScopeGrant {
  id: string
  tool: string
  scope?: string
  created_at?: string
}

const props = defineProps<{ open: boolean; team: Team | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', id: string): void
  (e: 'deleted', id: string): void
  (e: 'chat', id: string): void
  (e: 'new-chat', id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const router = useRouter()
// 详情按栏目分页：基本 / 成员 / 会话 / 授权 / 危险操作。
const tab = ref<'basic' | 'members' | 'sessions' | 'grants' | 'danger'>('basic')
const saving = ref(false)
const removing = ref(false)
const grants = ref<ScopeGrant[]>([])
const grantsLoading = ref(false)
// 群聊会话不只看本地最近 80 条：打开群资料时按 team_id 拉全量（最多 50 条）。
const loadedConversations = ref<Conversation[]>([])

const draft = reactive({
  name: '',
  workspace_id: '',
  avatar: '',
  description: '',
  mode: 'orchestrate',
  max_speakers: 4,
  leader: null as TeamMember | null,
  members: [] as TeamMember[],
})

const memberOpen = ref(false)
const memberSearch = ref('')
const selectedIds = ref<string[]>([])
const workOpen = ref(false)
const workSeed = ref<WorkSeed | null>(null)

function nameKey(name?: string): string {
  return String(name || '').trim().toLowerCase()
}

function roleBrief(member: TeamMember | Role): TeamMember {
  return {
    id: member.id,
    name: member.name || '',
    title: member.title || '',
    department: member.department || '',
    avatar: member.avatar || '',
    expertise: member.expertise || '',
    model_code: member.model_code || '',
  }
}

function loadDraft(team: Team): void {
  const leader = team.leader ? roleBrief(team.leader) : null
  const leaderId = leader?.id || ''
  const leaderName = nameKey(leader?.name)
  const seenIds = new Set<string>()
  const seenNames = new Set<string>()
  const members = (team.members || [])
    .map(roleBrief)
    .filter((member) => {
      const key = nameKey(member.name)
      const id = member.id || ''
      if (!id || id === leaderId || (key && key === leaderName)) return false
      if (seenIds.has(id) || (key && seenNames.has(key))) return false
      seenIds.add(id)
      if (key) seenNames.add(key)
      return true
    })
  draft.name = team.name || ''
  draft.workspace_id = team.workspace_id || ''
  draft.avatar = team.avatar || ''
  draft.description = team.description || ''
  draft.mode = team.mode || 'orchestrate'
  draft.max_speakers = Number(team.max_speakers) || 4
  draft.leader = leader
  draft.members = members
}

watch(() => [props.open, props.team?.id], () => {
  if (props.open && props.team) {
    tab.value = 'basic'
    loadDraft(props.team)
    void loadGrants(props.team.id)
    void loadConversations(props.team.id)
  } else if (!props.open) {
    loadedConversations.value = []
  }
}, { immediate: true })

const allMembers = computed(() => {
  const rows: Array<TeamMember & { isLeader?: boolean }> = []
  if (draft.leader?.id) rows.push({ ...draft.leader, isLeader: true })
  for (const member of draft.members) {
    if (!member.id || member.id === draft.leader?.id) continue
    if (rows.some((row) => row.id === member.id
      || (nameKey(row.name) && nameKey(row.name) === nameKey(member.name)))) continue
    rows.push(member)
  }
  return rows
})

const totalPeople = computed(() => allMembers.value.length)

const boundConversations = computed<Conversation[]>(() => {
  const tid = String(props.team?.id || '')
  if (!tid) return []
  const rows = [
    ...loadedConversations.value,
    ...data.conversations.filter((conv) => conv.team_id === tid),
  ]
  const seen = new Set<string>()
  return rows.filter((conv) => {
    if (!conv.id || seen.has(conv.id)) return false
    if (['global-agent', 'global-chat'].includes(String(conv.zone || ''))) return false
    seen.add(conv.id)
    return true
  })
})

const groupTokens = computed(() => boundConversations.value.reduce(
  (sum, conv) => sum + usageSummary(conv.usage).total, 0,
))

const modeHint = computed(() => {
  if (draft.mode === 'roundtable') {
    return `成员可各自应答，@成员 点名；每条消息最多 ${draft.max_speakers || 4} 个角色发言。`
  }
  if (draft.mode === 'single') return '群只作为角色预设，消息由主持角色单独处理。'
  return '主持接收消息，按需把子任务委派给成员；只有被委派时成员才行动。'
})

const candidates = computed(() => {
  const usedIds = new Set(allMembers.value.map((member) => member.id).filter(Boolean))
  const usedNames = new Set(allMembers.value.map((member) => nameKey(member.name)).filter(Boolean))
  const q = memberSearch.value.trim().toLowerCase()
  return data.persistentRoles.filter((role) => {
    if (!role.id || usedIds.has(role.id) || usedNames.has(nameKey(role.name))) return false
    if (!q) return true
    return `${role.name} ${role.title} ${role.department} ${role.expertise}`.toLowerCase().includes(q)
  })
})

const remaining = computed(() => Math.max(0, 12 - draft.members.length))

async function loadConversations(teamId: string): Promise<void> {
  try {
    const out = await api<ListResponse<Conversation>>(
      `/conversations?team_id=${encodeURIComponent(teamId)}&limit=50`,
    )
    if (props.team?.id === teamId) loadedConversations.value = out.items || []
  } catch {
    if (props.team?.id === teamId) loadedConversations.value = []
  }
}

async function loadGrants(teamId: string): Promise<void> {
  grantsLoading.value = true
  try {
    const out = await api<{ items: ScopeGrant[] }>(
      `/app/permissions/grants?team_id=${encodeURIComponent(teamId)}`,
    )
    if (props.team?.id === teamId) grants.value = out.items || []
  } catch {
    if (props.team?.id === teamId) grants.value = []
  } finally {
    grantsLoading.value = false
  }
}

async function revokeGrant(grantId: string): Promise<void> {
  try {
    await api(`/app/permissions/grants/${encodeURIComponent(grantId)}`, { method: 'DELETE' })
    if (props.team?.id) await loadGrants(props.team.id)
    app.toast('群组永久授权已撤销')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

function close(): void {
  emit('close')
}

function openMembers(): void {
  memberSearch.value = ''
  selectedIds.value = []
  memberOpen.value = true
}

function toggleMember(id: string): void {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((item) => item !== id)
    : [...selectedIds.value, id]
}

function addMembers(): void {
  const picked = selectedIds.value
    .map((id) => data.persistentRoles.find((role) => role.id === id))
    .filter((role): role is Role => Boolean(role))
  let added = 0
  for (const role of picked) {
    if (draft.members.length >= 12) {
      app.toast(`最多还能添加 ${remaining.value} 个成员`, 'bad')
      break
    }
    if (draft.leader?.id === role.id || nameKey(draft.leader?.name) === nameKey(role.name)) continue
    if (draft.members.some((member) => member.id === role.id || nameKey(member.name) === nameKey(role.name))) continue
    draft.members.push(roleBrief(role))
    added += 1
  }
  memberOpen.value = false
  if (added) app.toast(`已添加 ${added} 个成员`)
}

function removeMember(member: TeamMember): void {
  draft.members = draft.members.filter((item) => item.id !== member.id)
}

function makeLeader(member: TeamMember): void {
  const target = draft.members.find((item) => item.id === member.id)
  if (!target) return
  const oldLeader = draft.leader
  draft.members = draft.members.filter((item) => item.id !== target.id)
  if (oldLeader?.id && oldLeader.id !== target.id
    && !draft.members.some((item) => item.id === oldLeader.id)) {
    draft.members.push(oldLeader)
  }
  draft.leader = target
}

async function save(): Promise<void> {
  if (!props.team || saving.value) return
  const name = draft.name.trim()
  if (!name) {
    app.toast('群名不能为空', 'bad')
    return
  }
  saving.value = true
  try {
    await api<Team>(`/teams/${encodeURIComponent(props.team.id)}`, {
      method: 'PATCH',
      body: {
        name,
        workspace_id: draft.workspace_id || '',
        avatar: draft.avatar || '',
        description: draft.description.trim(),
        mode: draft.mode,
        max_speakers: draft.max_speakers,
        leader: draft.leader?.id ? roleBrief(draft.leader) : undefined,
        members: draft.members.filter((member) => member.id).map(roleBrief),
      },
    })
    await data.loadAll()
    app.toast('群聊设置已保存，已同步到所有群聊会话')
    emit('saved', props.team.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    saving.value = false
  }
}

async function removeTeam(): Promise<void> {
  if (!props.team || removing.value) return
  const count = boundConversations.value.length
  if (!window.confirm(`解散群组「${props.team.name}」？${count ? `\n${count} 条群聊会话会退回单聊。` : ''}\n群成员角色不会被删除。`)) return
  removing.value = true
  try {
    await api(`/teams/${encodeURIComponent(props.team.id)}`, { method: 'DELETE' })
    await data.loadAll()
    app.toast('群组已解散')
    emit('deleted', props.team.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    removing.value = false
  }
}

function openChat(): void {
  if (props.team) emit('chat', props.team.id)
}

function openConversation(id: string): void {
  close()
  void router.push(`/conv/${encodeURIComponent(id)}`)
}

/** 会话列表里删除 / 分支 / 已读后刷新群组维度的统计。 */
function onSessionsChanged(): void {
  void data.loadAll()
}

function openWork(): void {
  if (!props.team) return
  const teamId = props.team.id
  const teamName = draft.name || props.team.name || ''
  close()
  workSeed.value = {
    team_id: teamId,
    body: `来自群组「${teamName}」\n\n`,
  }
  workOpen.value = true
}

function onWorkSaved(): void {
  workOpen.value = false
}
</script>

<template>
  <Teleport to="body">
    <div v-show="open" class="drawer-scrim" @click="close" />
    <aside v-show="open" class="side-drawer" aria-label="群聊设置">
      <header class="side-drawer-head">
        <div>
          <h2>群聊设置</h2>
          <p>{{ draft.name || team?.name || '' }} · {{ totalPeople }} 人 · {{ boundConversations.length }} 条群聊</p>
        </div>
        <button class="iconbtn" type="button" title="关闭" aria-label="关闭" @click="close">
          <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>

      <div class="side-drawer-body">
        <div class="seg drawer-tabs" role="tablist" aria-label="群组栏目">
          <button class="seg-btn" :class="{ on: tab === 'basic' }" type="button" @click="tab = 'basic'">基本</button>
          <button class="seg-btn" :class="{ on: tab === 'members' }" type="button" @click="tab = 'members'">成员 {{ totalPeople }}</button>
          <button class="seg-btn" :class="{ on: tab === 'sessions' }" type="button" @click="tab = 'sessions'">会话 {{ boundConversations.length }}</button>
          <button class="seg-btn" :class="{ on: tab === 'grants' }" type="button" @click="tab = 'grants'">授权</button>
          <button class="seg-btn" :class="{ on: tab === 'danger' }" type="button" @click="tab = 'danger'">危险</button>
        </div>

        <div v-if="tab === 'basic'" class="group-mode-hint">
          群组是所有群聊会话共享的花名册：改成员 / 协作方式 / 工作区会同步到
          {{ boundConversations.length }} 条已绑定会话；不会影响单聊和其他群组。
        </div>

        <div v-if="tab === 'basic'" class="group-identity">
          <AvatarField v-model="draft.avatar" scope="team" :name="draft.name || team?.name || '群'" />
          <div class="group-identity-main">
            <div class="field">
              <label>群名称</label>
              <input v-model="draft.name" class="input" maxlength="120" />
            </div>
            <div class="field">
              <label>群介绍</label>
              <textarea v-model="draft.description" class="input" rows="2" maxlength="1000" placeholder="这个群一起做什么" />
            </div>
          </div>
        </div>

        <section v-if="tab === 'members'" class="group-section">
          <div class="group-section-head">
            <h3>群成员</h3>
            <span>{{ totalPeople }} / 13 人 · 群主 1 人</span>
            <button class="btn small" type="button" @click="openMembers">＋ 添加成员</button>
          </div>
          <div class="group-member-list">
            <div v-for="member in allMembers" :key="member.id || member.name" class="group-member">
              <Avatar :name="member.name || '角'" :avatar="member.avatar" round />
              <div class="group-member-main">
                <div class="group-member-name">
                  <span>{{ member.name }}</span>
                  <span v-if="member.isLeader" class="tag grp">群主</span>
                </div>
                <div class="group-member-sub">
                  {{ [member.title, member.department, member.expertise].filter(Boolean).join(' · ') || (member.model_code ? `模型：${member.model_code}` : '群成员') }}
                </div>
              </div>
              <div v-if="!member.isLeader" class="group-member-actions">
                <button class="btn small" type="button" @click="makeLeader(member)">设为主持</button>
                <button class="iconbtn" type="button" title="移出群聊" aria-label="移出群聊" @click="removeMember(member)">
                  <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13" /></svg>
                </button>
              </div>
            </div>
            <div v-if="!allMembers.length" class="group-empty">还没有群成员。点击「添加成员」从角色库邀请。</div>
          </div>
        </section>

        <section v-if="tab === 'basic'" class="group-section">
          <div class="group-section-head">
            <h3>群设置</h3>
            <span>协作方式与发言上限</span>
          </div>
          <div class="field">
            <label>绑定工作区</label>
            <select v-model="draft.workspace_id" class="input">
              <option value="">无工作区</option>
              <option v-for="ws in data.activeWorkspaces" :key="ws.id" :value="ws.id">{{ ws.name }}</option>
            </select>
          </div>
          <div class="field">
            <label>协作方式</label>
            <select v-model="draft.mode" class="input">
              <option value="orchestrate">主持编排（默认）</option>
              <option value="roundtable">圆桌（成员各自应答）</option>
              <option value="single">单角色（只作预设）</option>
            </select>
          </div>
          <div class="field range-field">
            <span><span>圆桌发言上限</span><output>{{ draft.max_speakers }}</output></span>
            <input v-model.number="draft.max_speakers" class="range-input" type="range" min="1" max="12" step="1" />
          </div>
          <div class="group-mode-hint">{{ modeHint }}</div>
        </section>

        <section v-if="tab === 'sessions'" class="group-section">
          <div class="group-section-head">
            <h3>群聊会话</h3>
            <span>{{ boundConversations.length }} 条 · {{ compactNumber(groupTokens) }} tokens</span>
            <button class="btn small" type="button" :disabled="!allMembers.length" @click="emit('new-chat', team?.id || '')">新会话</button>
            <button class="btn small" type="button" :disabled="!allMembers.length" @click="openChat">打开群聊</button>
          </div>
          <SessionList
            v-if="team"
            :team-id="team.id"
            :page-size="6"
            empty-text="还没有群聊会话。点「新会话」开始第一条。"
            @open="openConversation"
            @changed="onSessionsChanged"
          />
        </section>

        <section v-if="tab === 'grants'" class="group-section">
          <div class="group-section-head">
            <h3>群组永久授权</h3>
            <span>{{ grantsLoading ? '加载中…' : (grants.length ? `${grants.length} 条` : '未设置') }}</span>
          </div>
          <div v-if="grants.length" class="grant-list">
            <div v-for="grant in grants" :key="grant.id" class="grant-row">
              <span class="grant-main">
                <b>{{ grant.tool }}</b>
                <small>{{ grant.scope || 'team' }}</small>
              </span>
              <button class="iconbtn" type="button" title="撤销群组永久授权" aria-label="撤销群组永久授权" @click="revokeGrant(grant.id)">×</button>
            </div>
          </div>
          <p v-else class="group-mode-hint" style="margin:0">
            在群聊会话里选择「永久（本群组）」后，授权会出现在这里；群内所有成员会话都生效。
          </p>
        </section>

        <section v-if="tab === 'danger'" class="group-section group-danger">
          <div class="group-section-head"><h3>解散群聊</h3></div>
          <p class="group-mode-hint" style="margin:0">删除群组后，绑定会话会退回单聊；群成员角色本身不会被删除。</p>
          <button class="btn danger" type="button" :disabled="removing" @click="removeTeam">{{ removing ? '解散中…' : '解散群聊' }}</button>
        </section>
      </div>

      <footer class="side-drawer-foot">
        <button class="btn" type="button" @click="openChat">打开群聊</button>
        <button class="btn" type="button" @click="openWork">新建工单</button>
        <span class="spacer" />
        <button class="btn" type="button" @click="close">取消</button>
        <button class="btn primary" type="button" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存修改' }}</button>
      </footer>
    </aside>

    <Modal :open="memberOpen" title="添加群成员" @close="memberOpen = false">
      <div class="field">
        <label>从角色库选择成员</label>
        <input v-model="memberSearch" class="input" placeholder="搜索角色…" autocomplete="off" />
      </div>
      <div class="member-pick-list" style="margin-top:10px">
        <label v-for="role in candidates" :key="role.id" class="member member-pick">
          <input type="checkbox" :checked="selectedIds.includes(role.id)" @change="toggleMember(role.id)" />
          <Avatar :name="role.name || '角'" :avatar="role.avatar" round />
          <span class="row-main">
            <span class="row-title">{{ role.name }}</span>
            <span class="row-sub">{{ [role.title, role.department, role.expertise || role.persona].filter(Boolean).join(' · ') || '没有补充说明' }}</span>
          </span>
        </label>
        <div v-if="!candidates.length" class="group-empty">角色库里的角色都已加入。</div>
      </div>
      <p class="group-mode-hint">还可添加 {{ remaining }} 个成员；已在群里的角色不会出现在这里。</p>
      <template #footer>
        <el-button @click="memberOpen = false">取消</el-button>
        <el-button type="primary" :disabled="!selectedIds.length" @click="addMembers">添加</el-button>
      </template>
    </Modal>

    <WorkEditorDialog :open="workOpen" :seed="workSeed" @close="workOpen = false" @saved="onWorkSaved" />
  </Teleport>
</template>

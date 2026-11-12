<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElOption, ElSelect } from 'element-plus'
import Avatar from '@/components/Avatar.vue'
import ListPager from '@/components/ListPager.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import OptionPicker from '@/components/OptionPicker.vue'
import TokenMeter from '@/components/TokenMeter.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { Conversation, Role, TokenUsage } from '@/api/types'
import { GLOBAL_AGENT_ZONE, modeLabel } from '@/lib/derive'
import { DEFAULT_PAGE_SIZE, pageSlice } from '@/lib/paging'
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
const tempOpen = ref(false)
const tempSaving = ref(false)
const tempForm = reactive({ name: '', expertise: '', persona: '' })
// 用量走轻量接口：不拉转录，只取聚合后的 token 数字。
const usage = ref<TokenUsage | null>(null)
const usageLoading = ref(false)

const draft = reactive({
  title: '',
  team_id: '',
  role_id: '',
  participant_ids: [] as string[],
  workspace_id: '',
  workspace_follow: true,
})

const isGlobal = computed(() => String(props.conversation?.zone || '') === GLOBAL_AGENT_ZONE)
const sharedGroup = computed(() => data.groups.find(
  (team) => team.id === draft.team_id,
) || null)
const groupOptions = computed(() => data.groups.map((team) => ({
  value: team.id,
  label: `${team.name} · ${(team.members || []).length} 个角色`,
  description: [team.mode, team.description].filter(Boolean).join(' · '),
})))

// 角色 / 临时角色同样每页 12 条，避免抽屉里一次铺满。
const tempPage = ref(1)
const pagedTempRoles = computed(
  () => pageSlice(tempRoles.value, tempPage.value, DEFAULT_PAGE_SIZE),
)
watch(tempRoles, () => { tempPage.value = 1 })
const availableRoles = computed(() => [
  ...data.persistentRoles.filter((role) => role.id !== 'default'),
  ...tempRoles.value.filter((role) => !data.persistentRoles.some((item) => item.id === role.id)),
])

const roleOptions = computed(() => [
  // 旧会话可能还绑着兜底角色：允许原样保留，但不作为新建选项。
  ...(draft.role_id === 'default'
    ? [{ value: 'default', label: '未指定角色（旧会话）', description: '请选择一个真实角色' }]
    : []),
  ...availableRoles.value.map((role) => ({
    value: role.id,
    label: role.name || role.id,
    description: [role.title, role.expertise].filter(Boolean).join(' · '),
    tag: role.scope === 'temporary' ? '临时' : '',
  })),
])

// 参与角色 = 私有参与团队里的成员（不含主持角色）。
const participantOptions = computed(() => availableRoles.value
  .filter((role) => role.id !== draft.role_id)
  .map((role) => ({
    value: role.id,
    label: role.name || role.id,
    description: [role.title, role.expertise].filter(Boolean).join(' · '),
    tag: role.scope === 'temporary' ? '临时' : '',
  })))

// 「跟随」时实际会使用的工作区：群组优先于角色。
const followWorkspace = computed(() => {
  const groupWs = String(sharedGroup.value?.workspace_id || '')
  if (groupWs) return data.workspaces.find((ws) => ws.id === groupWs) || null
  const roleId = draft.role_id || 'default'
  const role = data.roles.find((item) => item.id === roleId)
  return data.workspaces.find((ws) => ws.id === String(role?.workspace_id || '')) || null
})
const conversationWorkspace = computed(() => (draft.workspace_follow
  ? followWorkspace.value
  : data.workspaces.find((ws) => ws.id === draft.workspace_id) || null))

// 跟随模式下切换角色：选择器与提示同步到新的默认工作区。
watch(followWorkspace, (ws) => {
  if (draft.workspace_follow) draft.workspace_id = ws?.id || ''
})

function loadDraft(): void {
  const conv = props.conversation
  const team = data.teams.find((item) => item.id === conv?.team_id)
  draft.title = conv?.title || ''
  // 只有共享群组走 team_id；participants-* 仍是本会话的参与角色。
  draft.team_id = String(team?.kind || '') === 'group' ? String(team?.id || '') : ''
  draft.role_id = conv?.role_id || String(team?.leader?.id || '') || 'default'
  draft.participant_ids = (team?.members || [])
    .map((member) => String(member.id || ''))
    .filter((id) => Boolean(id) && id !== draft.role_id)
  draft.workspace_id = conv?.workspace_id || ''
  draft.workspace_follow = !conv?.workspace_custom
}

function onFollowChange(): void {
  if (draft.workspace_follow) draft.workspace_id = followWorkspace.value?.id || draft.workspace_id
}

function onGroupChange(): void {
  const group = sharedGroup.value
  if (!group) return
  draft.role_id = String(group.leader?.id || draft.role_id || '')
  draft.participant_ids = (group.members || [])
    .map((member) => String(member.id || ''))
    .filter((id) => Boolean(id) && id !== draft.role_id)
  if (draft.workspace_follow) draft.workspace_id = followWorkspace.value?.id || ''
}

function onWorkspaceChange(): void {
  draft.workspace_follow = false
}

watch(() => [props.open, props.conversation?.id], () => {
  if (!props.open) {
    tempOpen.value = false
    return
  }
  loadDraft()
  void loadTempRoles()
  void loadUsage()
}, { immediate: true })

async function loadUsage(): Promise<void> {
  const conv = props.conversation
  if (!conv?.id) {
    usage.value = null
    return
  }
  usageLoading.value = true
  try {
    usage.value = await api<TokenUsage>(`/conversations/${encodeURIComponent(conv.id)}/usage`)
  } catch {
    usage.value = conv.usage || conv.usage_total || null
  } finally {
    usageLoading.value = false
  }
}

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
    await Promise.all([data.loadAll(), loadTempRoles()])
    if (!draft.participant_ids.includes(role.id) && role.id !== draft.role_id) {
      draft.participant_ids = [...draft.participant_ids, role.id]
    }
    tempOpen.value = false
    app.toast(`已创建临时角色：${role.name}`, 'ok', 2600)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    tempSaving.value = false
  }
}

function addTempToConversation(role: Role): void {
  if (role.id === draft.role_id) {
    app.toast('主持角色已经在本会话中', 'ok', 1800)
    return
  }
  if (draft.participant_ids.includes(role.id)) {
    app.toast('这个角色已经是参与角色', 'ok', 1800)
    return
  }
  if (draft.participant_ids.length >= 12) {
    app.toast('参与角色最多 12 个', 'bad')
    return
  }
  draft.participant_ids = [...draft.participant_ids, role.id]
}

async function removeTempRole(role: Role): Promise<void> {
  if (!window.confirm(`删除临时角色「${role.name}」？`)) return
  try {
    draft.participant_ids = draft.participant_ids.filter((id) => id !== role.id)
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
    const body: Record<string, unknown> = { title }
    if (!isGlobal.value) {
      body.team_id = draft.team_id || ''
      body.role_id = draft.role_id || ''
      // 工作区默认跟随角色 / 群组；用户手动选择后只作用于本会话，
      // 同一角色 / 群组的其他会话仍然可以各自使用不同工作区。
      body.workspace_follow = draft.workspace_follow
      if (!draft.workspace_follow) body.workspace_id = draft.workspace_id
    }
    await api<Conversation>(
      `/conversations/${encodeURIComponent(conv.id)}/settings`,
      { method: 'PATCH', body },
    )
    let updated: Conversation = conv
    // 群聊的成员由群组统一维护；只有非群聊会话才写会话私有参与角色。
    if (!isGlobal.value && !draft.team_id) {
      const result = await api<{ conversation: Conversation }>(
        `/conversations/${encodeURIComponent(conv.id)}/participants`,
        {
          method: 'PUT',
          body: {
            role_ids: draft.participant_ids,
            leader_id: draft.role_id || undefined,
          },
        },
      )
      updated = result.conversation || conv
    }
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

</script>

<template>
  <Teleport to="body">
    <div v-show="open" class="drawer-scrim" @click="emit('close')" />
    <aside v-show="open" class="side-drawer" aria-label="会话管理">
      <header class="side-drawer-head">
        <div>
          <h2>会话管理</h2>
          <p>{{ draft.title || conversation?.title || '未命名会话' }} · {{ isGlobal ? '全局 Agent' : '角色与参与角色' }}</p>
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
            <template v-if="!isGlobal">
              <WorkspacePicker
                v-model="draft.workspace_id"
                :workspaces="data.workspaces"
                :disabled="draft.workspace_follow"
                empty-label="默认工作区"
                placeholder="默认工作区"
                title="选择本会话使用的工作区"
                @update:model-value="onWorkspaceChange"
              />
              <label class="conv-follow">
                <input v-model="draft.workspace_follow" type="checkbox" @change="onFollowChange" />
                跟随角色
              </label>
              <small v-if="draft.workspace_follow" class="role-line">
                切换角色时自动更新；当前为「{{ followWorkspace?.name || '默认工作区' }}」。
              </small>
              <small v-else class="role-line">
                本会话固定使用「{{ conversationWorkspace?.name || '默认工作区' }}」，切换角色不会覆盖。
              </small>
            </template>
            <template v-else>
              <input class="input" value="默认工作区" readonly />
              <small class="role-line">全局 Agent 固定使用默认工作区。</small>
            </template>
          </div>
        </section>

        <section class="group-section">
          <div class="group-section-head">
            <h3>Token 用量</h3>
            <span>{{ usageLoading ? '统计中…' : '输入 / 输出 / 速度 / 缓存' }}</span>
          </div>
          <TokenMeter :usage="usage || conversation?.usage" variant="panel" headless />
        </section>

        <section v-if="!isGlobal" class="group-section">
          <div class="group-section-head">
            <h3>群组</h3>
            <span>群聊成员由群组统一维护</span>
          </div>
          <div class="field">
            <label>绑定群组</label>
            <select v-model="draft.team_id" class="input" @change="onGroupChange">
              <option value="">不使用群组（本会话私有参与角色）</option>
              <option v-for="group in groupOptions" :key="group.value" :value="group.value">
                {{ group.label }}
              </option>
            </select>
            <small v-if="sharedGroup" class="role-line">
              当前群：{{ modeLabel(sharedGroup.mode) }} · {{ (sharedGroup.members || []).length }} 个角色；
              成员变更会同步到所有绑定该群的会话。
            </small>
            <small v-else class="role-line">
              不使用群组时，下面的参与角色只属于本会话。
            </small>
          </div>
        </section>

        <template v-if="!isGlobal">
          <section v-if="!draft.team_id" class="group-section">
            <div class="group-section-head">
              <h3>角色</h3>
              <span>主持角色 + 本会话参与角色</span>
            </div>
            <div class="field">
              <label>主持角色</label>
              <OptionPicker
                v-model="draft.role_id"
                :items="roleOptions"
                title="选择主持角色"
                placeholder="选择角色"
              />
            </div>
            <div class="field">
              <label>参与角色（最多 12 个）</label>
              <ElSelect
                v-model="draft.participant_ids"
                multiple
                filterable
                collapse-tags
                collapse-tags-tooltip
                :multiple-limit="12"
                placeholder="只有主持角色参与"
                style="width:100%"
              >
                <ElOption
                  v-for="role in participantOptions"
                  :key="role.value"
                  :label="role.label"
                  :value="role.value"
                >
                  <span>{{ role.label }}</span>
                  <small v-if="role.description" class="role-line" style="margin-left:8px">{{ role.description }}</small>
                </ElOption>
              </ElSelect>
              <small class="role-line">参与角色只属于本会话；可以 @ 调用，也可以派遣后台任务。</small>
            </div>
          </section>

          <section v-if="!draft.team_id" class="group-section">
            <div class="group-section-head">
              <h3>临时角色</h3>
              <span>只属于本会话</span>
              <button class="btn small" type="button" @click="openTempRole">＋ 新建</button>
            </div>
            <div class="group-member-list">
              <div v-for="role in pagedTempRoles" :key="role.id" class="group-member">
                <Avatar :name="role.name || '角'" :avatar="role.avatar" round />
                <div class="group-member-main">
                  <div class="group-member-name"><span>{{ role.name }}</span><span class="tag grp">临时</span></div>
                  <div class="group-member-sub">{{ [role.title, role.expertise].filter(Boolean).join(' · ') || '本会话临时角色' }}</div>
                </div>
                <div class="group-member-actions">
                  <button v-if="role.id !== draft.role_id && !draft.participant_ids.includes(role.id)" class="btn small" type="button" @click="addTempToConversation(role)">加入会话</button>
                  <button class="btn small" type="button" @click="promoteTempRole(role)">保存为持久</button>
                  <button class="iconbtn" type="button" title="删除" aria-label="删除" @click="removeTempRole(role)">
                    <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13" /></svg>
                  </button>
                </div>
              </div>
              <div v-if="tempLoading" class="group-empty">加载临时角色…</div>
              <div v-else-if="!tempRoles.length" class="group-empty">还没有临时角色。新建后可以加入当前会话。</div>
            </div>
            <ListPager
              :page="tempPage"
              :total="tempRoles.length"
              :page-size="DEFAULT_PAGE_SIZE"
              @update:page="tempPage = $event"
            />
          </section>

          <section v-if="draft.team_id" class="group-section">
            <div class="group-section-head">
              <h3>群成员</h3>
              <span>由群组统一维护</span>
            </div>
            <div class="group-member-list">
              <div v-if="sharedGroup?.leader?.id" class="group-member">
                <Avatar :name="sharedGroup?.leader?.name || '群主'" :avatar="sharedGroup?.leader?.avatar" round />
                <div class="group-member-main">
                  <div class="group-member-name"><span>{{ sharedGroup?.leader?.name }}</span><span class="tag grp">群主</span></div>
                  <div class="group-member-sub">{{ sharedGroup?.leader?.expertise || '负责接收消息与编排' }}</div>
                </div>
              </div>
              <div v-for="member in (sharedGroup?.members || [])" :key="member.id || member.name" class="group-member">
                <Avatar :name="member.name || '角色'" :avatar="member.avatar" round />
                <div class="group-member-main">
                  <div class="group-member-name"><span>{{ member.name }}</span></div>
                  <div class="group-member-sub">{{ [member.title, member.expertise].filter(Boolean).join(' · ') || '群成员' }}</div>
                </div>
              </div>
            </div>
            <small class="role-line">改成员 / 群主 / 协作方式 / 发言上限请点聊天头部「群资料」；这里只切换本会话绑定的群。</small>
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
  </Teleport>
</template>

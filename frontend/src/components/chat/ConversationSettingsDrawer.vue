<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElOption, ElSelect } from 'element-plus'
import Avatar from '@/components/Avatar.vue'
import ListPager from '@/components/ListPager.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import OptionPicker from '@/components/OptionPicker.vue'
import TokenMeter from '@/components/TokenMeter.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { Conversation, ListResponse, Role, TokenUsage } from '@/api/types'
import { GLOBAL_AGENT_ZONE, conversationTitle, modeLabel } from '@/lib/derive'
import { DEFAULT_PAGE_SIZE, pageSlice } from '@/lib/paging'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const props = defineProps<{
  open: boolean
  conversation: Conversation | null
  botConversationId?: string
  botLaneId?: string
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', conversation: Conversation): void
  (e: 'deleted', id: string): void
  (e: 'open', id: string): void
  (e: 'bots-changed'): void
}>()

const app = useAppStore()
const data = useDataStore()
const saving = ref(false)
const removing = ref(false)
// 会话管理按栏目分页：基本 / 成员与群组 / 分支 / 更多，避免和角色、群组设置堆在一起。
const tab = ref<'basic' | 'members' | 'branch' | 'more'>('basic')
const tempRoles = ref<Role[]>([])
const tempLoading = ref(false)
const tempOpen = ref(false)
const tempSaving = ref(false)
const tempForm = reactive({ name: '', expertise: '', persona: '' })
// 用量走轻量接口：不拉转录，只取聚合后的 token 数字。
const usage = ref<TokenUsage | null>(null)
const usageLoading = ref(false)
// 分支：来源会话 + 直接子分支（两跳，够定位上下文，不建树表）。
const forkSource = ref<Conversation | null>(null)
const branches = ref<Conversation[]>([])
const forksLoading = ref(false)

interface BotInfo {
  id: string
  provider: 'feishu' | 'wecom'
  bot_id: string
  status: string
  error?: string
  conversation_id: string
  session_root_id?: string
  lane_id?: string
  peer_id: string
  bound: boolean
}

// 机器人绑定是全局的：会话管理里只切换「本会话」，换绑会解绑旧会话。
const bots = ref<BotInfo[]>([])
const botsLoading = ref(false)
const botBusy = ref('')
const botError = ref('')
let botTimer = 0
const botStatuses: Record<string, string> = {
  connecting: '连接中', connected: '已连接', reconnecting: '正在重连', error: '连接失败',
}
const botTargetId = computed(() => props.botConversationId || props.conversation?.id || '')
const botTargetLaneId = computed(() => props.botLaneId || props.conversation?.id || '')

const draft = reactive({
  title: '',
  team_id: '',
  role_id: '',
  participant_ids: [] as string[],
  workspace_id: '',
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

// 已绑定本会话的机器人排在最前，方便在抽屉里快速解绑。
const drawerBots = computed(() => {
  const current = botTargetId.value
  return [...bots.value].sort((a, b) =>
    Number((b.session_root_id || b.conversation_id) === current)
      - Number((a.session_root_id || a.conversation_id) === current),
  )
})

function isBoundHere(bot: BotInfo): boolean {
  return Boolean(bot.bound) && (bot.session_root_id || bot.conversation_id) === botTargetId.value
}

function isBoundToCurrentLane(bot: BotInfo): boolean {
  return isBoundHere(bot) && (bot.lane_id || bot.conversation_id) === botTargetLaneId.value
}

function boundLaneTitle(bot: BotInfo): string {
  const id = bot.lane_id || bot.conversation_id
  return data.conversations.find((conv) => conv.id === id)?.title || id
}

function boundTitle(bot: BotInfo): string {
  const id = bot.session_root_id || bot.conversation_id
  return data.conversations.find((conv) => conv.id === id)?.title || id
}

function botStatusClass(status: string): string {
  if (status === 'connected') return 'tag ok'
  if (status === 'error' || status === 'reconnecting') return 'tag tmp'
  return 'tag'
}

async function loadBots(): Promise<void> {
  if (!botTargetId.value) {
    bots.value = []
    return
  }
  botsLoading.value = true
  try {
    const out = await api<{ items: BotInfo[] }>('/bots')
    bots.value = out.items || []
    botError.value = ''
  } catch (cause) {
    bots.value = []
    botError.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    botsLoading.value = false
  }
}

function stopBotTimer(): void {
  if (botTimer) {
    window.clearInterval(botTimer)
    botTimer = 0
  }
}

async function bindBot(bot: BotInfo): Promise<void> {
  const targetId = botTargetId.value
  if (!targetId || botBusy.value) return
  if (bot.bound && bot.conversation_id && (bot.session_root_id || bot.conversation_id) !== targetId) {
    const current = boundTitle(bot)
    if (!window.confirm(`「${bot.bot_id}」当前绑定在「${current}」，改绑后旧会话自动解绑。继续？`)) return
  }
  botBusy.value = bot.id
  botError.value = ''
  try {
    await api(`/bots/${encodeURIComponent(bot.id)}/bind`, {
      method: 'POST',
      body: { conversation_id: targetId, lane_id: botTargetLaneId.value },
    })
    app.toast('机器人已绑定到本会话')
    await loadBots()
    emit('bots-changed')
  } catch (cause) {
    botError.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    botBusy.value = ''
  }
}

async function unbindBot(bot: BotInfo): Promise<void> {
  if (botBusy.value) return
  botBusy.value = bot.id
  botError.value = ''
  try {
    await api(`/bots/${encodeURIComponent(bot.id)}/route`, { method: 'DELETE' })
    app.toast('机器人已解绑')
    await loadBots()
    emit('bots-changed')
  } catch (cause) {
    botError.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    botBusy.value = ''
  }
}

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
}

function onGroupChange(): void {
  const group = sharedGroup.value
  if (!group) return
  draft.role_id = String(group.leader?.id || draft.role_id || '')
  draft.participant_ids = (group.members || [])
    .map((member) => String(member.id || ''))
    .filter((id) => Boolean(id) && id !== draft.role_id)

}

watch(() => [props.open, props.conversation?.id, botTargetId.value, botTargetLaneId.value], () => {
  if (!props.open) {
    tempOpen.value = false
    stopBotTimer()
    return
  }
  loadDraft()
  tab.value = 'basic'
  void loadTempRoles()
  void loadUsage()
  void loadForks()
  botError.value = ''
  void loadBots()
  stopBotTimer()
  botTimer = window.setInterval(() => void loadBots(), 4000)
}, { immediate: true })

onBeforeUnmount(stopBotTimer)

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

async function loadForks(): Promise<void> {
  const conv = props.conversation
  forkSource.value = null
  branches.value = []
  if (!conv?.id) return
  forksLoading.value = true
  try {
    const fromId = String(conv.forked_from_id || '')
    if (fromId) {
      forkSource.value = data.conversations.find((item) => item.id === fromId)
        || await api<Conversation>(`/conversations/${encodeURIComponent(fromId)}`)
          .catch(() => null)
    }
    const out = await api<ListResponse<Conversation>>(
      `/conversations?forked_from_id=${encodeURIComponent(conv.id)}&limit=20&include_archived=true`,
    )
    branches.value = out.items || []
  } catch {
    branches.value = []
  } finally {
    forksLoading.value = false
  }
}

function titleOf(conv: Conversation): string {
  return conversationTitle(conv, data.teams, data.roles)
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
    }
    if (draft.workspace_id !== (conv.workspace_id || '')) {
      body.workspace_id = draft.workspace_id
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
        <div class="seg drawer-tabs" role="tablist" aria-label="会话管理栏目">
          <button class="seg-btn" :class="{ on: tab === 'basic' }" type="button" @click="tab = 'basic'">基本</button>
          <button v-if="!isGlobal" class="seg-btn" :class="{ on: tab === 'members' }" type="button" @click="tab = 'members'">成员与群组</button>
          <button
            v-if="conversation?.forked_from_id || branches.length"
            class="seg-btn"
            :class="{ on: tab === 'branch' }"
            type="button"
            @click="tab = 'branch'"
          >分支</button>
          <button class="seg-btn" :class="{ on: tab === 'more' }" type="button" @click="tab = 'more'">更多</button>
        </div>

        <section v-if="tab === 'basic'" class="group-section">
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
            <WorkspacePicker
              v-model="draft.workspace_id"
              :workspaces="data.workspaces"
              empty-label="无工作区"
              placeholder="无工作区"
              title="选择本会话使用的工作区"
            />
          </div>
        </section>

        <section v-if="tab === 'branch' && (conversation?.forked_from_id || branches.length)" class="group-section">
          <div class="group-section-head">
            <h3>会话分支</h3>
            <span>{{ forksLoading ? '加载中…' : (branches.length ? `${branches.length} 条子分支` : '没有子分支') }}</span>
          </div>
          <div class="group-member-list">
            <button
              v-if="forkSource"
              class="group-member"
              type="button"
              style="width:100%;text-align:left;cursor:pointer"
              @click="emit('open', forkSource.id)"
            >
              <Avatar :name="titleOf(forkSource)" :avatar="forkSource.team_id ? '' : data.roleAvatar(forkSource.role_id)" round />
              <div class="group-member-main">
                <div class="group-member-name">
                  <span>{{ titleOf(forkSource) }}</span>
                  <span class="chip">来源</span>
                </div>
                <div class="group-member-sub">本会话从这里分支出来</div>
              </div>
            </button>
            <button
              v-for="child in branches"
              :key="child.id"
              class="group-member"
              type="button"
              style="width:100%;text-align:left;cursor:pointer"
              @click="emit('open', child.id)"
            >
              <Avatar :name="titleOf(child)" :avatar="child.team_id ? '' : data.roleAvatar(child.role_id)" round />
              <div class="group-member-main">
                <div class="group-member-name">
                  <span>{{ titleOf(child) }}</span>
                  <span class="chip">分支</span>
                </div>
                <div class="group-member-sub">{{ child.last_message || '还没有消息' }}</div>
              </div>
            </button>
          </div>
        </section>

        <section v-if="tab === 'basic'" class="group-section">
          <div class="group-section-head">
            <h3>Token 用量</h3>
            <span>{{ usageLoading ? '统计中…' : '输入 / 输出 / 速度 / 缓存' }}</span>
          </div>
          <TokenMeter :usage="usage || conversation?.usage" variant="panel" headless />
        </section>

        <section v-if="tab === 'members' && !isGlobal" class="group-section">
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

        <template v-if="tab === 'members' && !isGlobal">
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

        <section v-if="tab === 'members' && isGlobal" class="group-section">
          <div class="group-section-head"><h3>全局 Agent</h3><span>AtlasX 仅在此会话使用</span></div>
          <p class="group-mode-hint" style="margin:0">全局 Agent 负责跨会话授权、未读、运行状态与组织管理；普通会话不会出现 AtlasX。</p>
        </section>

        <section v-if="tab === 'more'" class="group-section">
          <div class="group-section-head">
            <h3>机器人</h3>
            <span>机器人绑定会话，并一次路由到一个栏目</span>
          </div>
          <p v-if="botError" class="run-error" role="alert">{{ botError }}</p>
          <div v-if="botsLoading && !bots.length" class="group-empty">正在读取机器人状态…</div>
          <div v-else-if="!bots.length" class="group-empty">
            还没有机器人。到「设置 → 机器人」添加后，可绑定到本会话并选择当前栏目作为唯一消息入口。
          </div>
          <div v-else class="group-member-list">
            <div v-for="bot in drawerBots" :key="bot.id" class="group-member">
              <div class="group-member-main">
                <div class="group-member-name">
                  <span>{{ bot.provider === 'feishu' ? '飞书' : '企业微信' }} · {{ bot.bot_id }}</span>
                  <span :class="botStatusClass(bot.status)" :title="bot.error || ''">{{ botStatuses[bot.status] || bot.status }}</span>
                  <span v-if="isBoundToCurrentLane(bot)" class="tag grp">当前栏目</span>
                  <span v-else-if="isBoundHere(bot)" class="tag">本会话</span>
                </div>
                <div class="group-member-sub">
                  <template v-if="isBoundToCurrentLane(bot)">消息进入当前栏目，回复发回原聊天。</template>
                  <template v-else-if="isBoundHere(bot)">当前路由栏目：{{ boundLaneTitle(bot) }}。绑定当前栏目会切换唯一消息入口。</template>
                  <template v-else-if="bot.bound">当前绑定：{{ boundTitle(bot) }}；改绑后旧会话自动解绑。</template>
                  <template v-else>尚未绑定会话；未绑定时收到的消息会被忽略。</template>
                </div>
              </div>
              <div class="group-member-actions">
                <span v-if="botBusy === bot.id" class="role-line">处理中…</span>
                <button v-else-if="isBoundToCurrentLane(bot)" class="btn small" type="button" @click="unbindBot(bot)">解绑</button>
                <button v-else class="btn small primary" type="button" @click="bindBot(bot)">
                  {{ isBoundHere(bot) ? '绑定当前栏目' : '绑定会话 / 当前栏目' }}
                </button>
              </div>
            </div>
          </div>
          <small v-if="bots.length" class="role-line">
            一个机器人同一时间绑定一个会话，并只路由到其中一个栏目；切换会自动改绑原栏目。
          </small>
        </section>

        <section v-if="tab === 'more'" class="group-section group-danger">
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

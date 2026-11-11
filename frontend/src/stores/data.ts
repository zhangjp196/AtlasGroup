import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'
import type {
  AppInfo,
  Conversation,
  Department,
  ListResponse,
  ModelProfile,
  ModelsResponse,
  Role,
  ScheduleListResponse,
  ScheduledTask,
  Subscription,
  Team,
  Workspace,
  WorkItemsResponse,
} from '@/api/types'

export const useDataStore = defineStore('data', () => {
  const workspaces = ref<Workspace[]>([])
  const conversations = ref<Conversation[]>([])
  const roles = ref<Role[]>([])
  const teams = ref<Team[]>([])
  const departments = ref<Department[]>([])
  const models = ref<ModelProfile[]>([])
  const subscriptions = ref<Subscription[]>([])
  const activeModel = ref('')
  const info = ref<AppInfo | null>(null)
  const workItems = ref<WorkItemsResponse['items']>([])
  const workStats = ref<WorkItemsResponse['stats']>({})
  const workPolicy = ref<Record<string, unknown>>({})
  const schedules = ref<ScheduledTask[]>([])
  const scheduleStats = ref<ScheduleListResponse['stats']>({})
  const loading = ref(false)
  const lastError = ref('')

  // 默认兜底角色「通用助手」和全局 AtlasX 不进入普通角色视图 / 在线角色栏。
  const persistentRoles = computed(() => roles.value.filter(
    (role) => role.scope !== 'temporary' && role.id !== 'default' && role.id !== 'global-agent',
  ))
  const activeWorkspaces = computed(() => workspaces.value.filter((workspace) => !workspace.archived))
  const archivedWorkspaces = computed(() => workspaces.value.filter((workspace) => workspace.archived))

  // 角色头像的「实时」来源：持久角色 + 群组里的主持 / 成员（含临时角色）。
  // 群聊消息里存的是回复那一刻的头像快照；角色换头像后历史消息不能一直用旧图，
  // 所以展示时优先按 role_id 查这里，快照只作为角色已删除时的兜底。
  const roleAvatarMap = computed(() => {
    const map = new Map<string, string>()
    for (const role of roles.value) {
      const id = String(role.id || '')
      if (id) map.set(id, String(role.avatar || ''))
    }
    for (const team of teams.value) {
      const people = [team.leader, ...(team.members || [])]
      for (const person of people) {
        const id = String(person?.id || '')
        if (!id) continue
        const avatar = String(person?.avatar || '')
        // 持久角色是权威来源；群组成员只补角色库里没有的（临时角色 / 已离开角色库）。
        if (!map.has(id) || (!map.get(id) && avatar)) map.set(id, avatar)
      }
    }
    return map
  })

  function roleAvatar(roleId?: string, fallback = ''): string {
    const id = String(roleId || '')
    const current = id ? roleAvatarMap.value.get(id) : ''
    return current || fallback || ''
  }

  async function loadAll(): Promise<void> {
    loading.value = true
    lastError.value = ''
    try {
      const [ws, conv, roleList, teamList, deptList, modelData] = await Promise.all([
        api<ListResponse<Workspace>>('/workspaces?include_archived=true').catch(() => ({ items: [] })),
        api<ListResponse<Conversation>>('/conversations?limit=80').catch(() => ({ items: [] })),
        api<ListResponse<Role>>('/roles').catch(() => ({ items: [] })),
        api<ListResponse<Team>>('/teams').catch(() => ({ items: [] })),
        api<ListResponse<Department>>('/departments').catch(() => ({ items: [] })),
        api<ModelsResponse>('/app/models').catch(() => ({ items: [], active: '', subscriptions: [] })),
      ])
      const next = {
        workspaces: ws.items || [],
        conversations: conv.items || [],
        roles: roleList.items || [],
        teams: teamList.items || [],
        departments: deptList.items || [],
        models: modelData.items || [],
        subscriptions: modelData.subscriptions || [],
      }
      // 只替换真正变化的列表，避免每次 loadAll 都让整页组件重渲染。
      if (JSON.stringify(workspaces.value) !== JSON.stringify(next.workspaces)) workspaces.value = next.workspaces
      if (JSON.stringify(conversations.value) !== JSON.stringify(next.conversations)) conversations.value = next.conversations
      if (JSON.stringify(roles.value) !== JSON.stringify(next.roles)) roles.value = next.roles
      if (JSON.stringify(teams.value) !== JSON.stringify(next.teams)) teams.value = next.teams
      if (JSON.stringify(departments.value) !== JSON.stringify(next.departments)) departments.value = next.departments
      if (JSON.stringify(models.value) !== JSON.stringify(next.models)) models.value = next.models
      if (JSON.stringify(subscriptions.value) !== JSON.stringify(next.subscriptions)) subscriptions.value = next.subscriptions
      if (activeModel.value !== (modelData.active || '')) activeModel.value = modelData.active || ''
    } finally {
      loading.value = false
    }
  }

  async function loadWork(): Promise<void> {
    try {
      const next = await api<WorkItemsResponse>('/work-items?limit=120')
      const items = next.items || []
      const stats = next.stats || {}
      const policy = next.policy || {}
      // 后台轮询很频繁，数据没变时保持旧引用，避免整页重复渲染。
      if (JSON.stringify(items) !== JSON.stringify(workItems.value)) workItems.value = items
      if (JSON.stringify(stats) !== JSON.stringify(workStats.value)) workStats.value = stats
      if (JSON.stringify(policy) !== JSON.stringify(workPolicy.value)) workPolicy.value = policy
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
    }
  }

  async function loadSchedules(): Promise<void> {
    try {
      const next = await api<ScheduleListResponse>('/schedules')
      const items = next.items || []
      if (JSON.stringify(items) !== JSON.stringify(schedules.value)) schedules.value = items
      if (JSON.stringify(next.stats || {}) !== JSON.stringify(scheduleStats.value)) {
        scheduleStats.value = next.stats || {}
      }
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
    }
  }

  async function loadConversations(): Promise<void> {
    try {
      const result = await api<ListResponse<Conversation>>('/conversations?limit=80')
      const items = result.items || []
      if (JSON.stringify(conversations.value) !== JSON.stringify(items)) conversations.value = items
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
    }
  }

  async function loadRoles(): Promise<void> {    try {
      const result = await api<ListResponse<Role>>('/roles')
      const items = result.items || []
      if (JSON.stringify(roles.value) !== JSON.stringify(items)) roles.value = items
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
    }
  }

  async function loadInfo(): Promise<void> {
    try {
      info.value = await api<AppInfo>('/app/info')
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
    }
  }

  async function refreshConversation(id: string): Promise<Conversation> {
    const conversation = await api<Conversation>(`/conversations/${encodeURIComponent(id)}`)
    const index = conversations.value.findIndex((item) => item.id === id)
    if (index >= 0) conversations.value[index] = { ...conversations.value[index], ...conversation }
    return conversation
  }

  async function activateModel(code: string): Promise<void> {
    await api('/app/models/active', { method: 'POST', body: { code } })
    activeModel.value = code
  }

  return {
    workspaces,
    conversations,
    roles,
    teams,
    departments,
    models,
    subscriptions,
    activeModel,
    info,
    workItems,
    workStats,
    workPolicy,
    loading,
    lastError,
    schedules,
    scheduleStats,
    persistentRoles,
    activeWorkspaces,
    archivedWorkspaces,
    roleAvatarMap,
    roleAvatar,
    loadAll,
    loadRoles,
    loadConversations,
    loadWork,
    loadSchedules,
    loadInfo,
    refreshConversation,
    activateModel,
  }
})

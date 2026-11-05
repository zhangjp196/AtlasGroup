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
  const loading = ref(false)
  const lastError = ref('')

  const persistentRoles = computed(() => roles.value.filter((role) => role.scope !== 'temporary'))
  const activeWorkspaces = computed(() => workspaces.value.filter((workspace) => !workspace.archived))
  const archivedWorkspaces = computed(() => workspaces.value.filter((workspace) => workspace.archived))

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
      workspaces.value = ws.items || []
      conversations.value = conv.items || []
      roles.value = roleList.items || []
      teams.value = teamList.items || []
      departments.value = deptList.items || []
      models.value = modelData.items || []
      activeModel.value = modelData.active || ''
      subscriptions.value = modelData.subscriptions || []
    } finally {
      loading.value = false
    }
  }

  async function loadWork(): Promise<void> {
    try {
      const data = await api<WorkItemsResponse>('/work-items?limit=120')
      workItems.value = data.items || []
      workStats.value = data.stats || {}
      workPolicy.value = data.policy || {}
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
    persistentRoles,
    activeWorkspaces,
    archivedWorkspaces,
    loadAll,
    loadWork,
    loadInfo,
    refreshConversation,
    activateModel,
  }
})

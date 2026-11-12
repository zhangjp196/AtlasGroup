<script setup lang="ts">
import { ArrowRight, Check, Clock, Document, MoreFilled, Plus, Search, Setting, Warning } from '@element-plus/icons-vue'
import { ElButton, ElInput } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import PageHeader from '@/components/PageHeader.vue'
import WorkEditorDialog from '@/components/WorkEditorDialog.vue'
import WorkSettingsDrawer from '@/components/WorkSettingsDrawer.vue'
import ListPager from '@/components/ListPager.vue'
import OptionPicker from '@/components/OptionPicker.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import TokenMeter from '@/components/TokenMeter.vue'
import { api } from '@/api/client'
import type { WorkItem } from '@/api/types'
import { shortTime } from '@/lib/format'
import { clampPage, DEFAULT_PAGE_SIZE, pageCount, pageSlice } from '@/lib/paging'
import { usageFromCounts } from '@/lib/usage'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const app = useAppStore()
const attention = useAttentionStore()
const route = useRoute()
const router = useRouter()

const createOpen = ref(false)
const filter = ref('open')
const sort = ref<'recent' | 'priority'>('recent')
const listHeading = ref<HTMLHeadingElement>()
const metrics = [
  { key: 'open', label: '待办工单', icon: Document, tone: 'neutral' },
  { key: 'working', label: '正在执行', icon: Clock, tone: 'accent' },
  { key: 'attention', label: '需要处理', icon: Warning, tone: 'warning' },
  { key: 'done', label: '已完成', icon: Check, tone: 'success' },
]

function focusFilter(key: string): void {
  filter.value = key
  listHeading.value?.focus()
}
const policyOpen = ref(false)
const policySaving = ref(false)
const roleOptions = computed(() => data.persistentRoles.map((role) => ({
  value: role.id,
  label: role.name || role.id,
  description: [role.title, role.expertise].filter(Boolean).join(' · '),
  tag: role.scope === 'temporary' ? '临时' : '',
})))

const policyForm = reactive({
  mode: 'observe',
  permission_mode: 'auto',
  default_workspace_id: '',
  default_role_id: '',
  auto_execute: false,
})
const workMenu = reactive({ open: false, x: 0, y: 0, item: null as WorkItem | null })

const workMenuItems = computed<ContextMenuItem[]>(() => {
  const item = workMenu.item
  const done = ['done', 'closed'].includes(String(item?.status || ''))
  return [
    { label: '打开工单', action: 'open' },
    { label: '复制标题', action: 'copy' },
    { label: '', action: '', separator: true },
    { label: '派活 / 重新派活', action: 'dispatch', disabled: done },
    { label: '标记完成', action: 'done', disabled: done },
    { label: '', action: '', separator: true },
    { label: '删除工单', action: 'delete', danger: true },
  ]
})

function openWorkMenu(event: MouseEvent, item: WorkItem): void {
  workMenu.item = item
  const anchor = (event.currentTarget as HTMLElement).getBoundingClientRect()
  workMenu.x = event.detail || event.type === 'contextmenu' ? event.clientX : anchor.left
  workMenu.y = event.detail || event.type === 'contextmenu' ? event.clientY : anchor.bottom
  workMenu.open = true
}

async function onWorkMenu(action: string): Promise<void> {
  const item = workMenu.item
  if (!item) return
  if (action === 'open') return openItem(item)
  if (action === 'copy') {
    try {
      await navigator.clipboard.writeText(item.title || item.id)
      app.toast('已复制标题')
    } catch {
      app.toast('复制失败', 'bad')
    }
    return
  }
  try {
    if (action === 'dispatch') {
      await api(`/work-items/${encodeURIComponent(item.id)}/dispatch`, { method: 'POST' })
      await data.loadWork()
      app.toast('工单已派活')
    } else if (action === 'done') {
      await api(`/work-items/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: { status: 'done' } })
      await data.loadWork()
      app.toast('工单已标记完成')
    } else if (action === 'delete') {
      if (!window.confirm(`删除工单「${item.title || item.id}」？`)) return
      await api(`/work-items/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
      await data.loadWork()
      app.toast('工单已删除')
    }
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  }
}

const filters = [
  { key: 'open', label: '待办' },
  { key: 'attention', label: '需要你' },
  { key: 'inbox', label: '待分派' },
  { key: 'working', label: '进行中' },
  { key: 'done', label: '已完成' },
  { key: 'all', label: '全部' },
]

const statusLabels: Record<string, string> = {
  inbox: '待分派',
  working: '进行中',
  needs_attention: '需要你',
  done: '已完成',
  closed: '已关闭',
}

const stats = computed(() => data.workStats || {})
// 工单页头部的 token 汇总（与列表卡片同一口径：按任务 / 会话归集后求和）。
const workReady = computed(() => stats.value.total !== undefined)
const workUsage = computed(() => usageFromCounts(stats.value))
const hasWorkUsage = computed(() => Number(stats.value.tokens || 0) > 0)
const policy = computed(() => data.workPolicy || {})
const mode = computed(() => String(policy.value.mode || 'observe'))
const modeText = computed(() => ({ off: '手动模式', observe: '观察模式', auto: '自动模式' } as Record<string, string>)[mode.value] || '观察模式')
const permissionText = computed(() => ({
  auto: '自动判断',
  once: '每次询问',
  session: '会话允许',
  always: '完全允许',
} as Record<string, string>)[String(policy.value.permission_mode || 'auto')] || '自动判断')
const defaultWorkspace = computed(() => data.workspaces.find((ws) => ws.id === policy.value.default_workspace_id))
const defaultWorkspaceText = computed(() => defaultWorkspace.value?.name || String(policy.value.default_workspace_id || '默认'))

const keyword = ref('')
const teamFilter = ref(String(route.query.team_id || ''))
const page = ref(1)
const PAGE_SIZE = DEFAULT_PAGE_SIZE

watch(() => route.query.team_id, (value) => {
  teamFilter.value = String(value || '')
  page.value = 1
})

const teamFilterName = computed(() => data.groups.find(
  (team) => team.id === teamFilter.value,
)?.name || teamFilter.value)

const scopedItems = computed(() => teamFilter.value
  ? data.workItems.filter((item) => String(item.team_id || '') === teamFilter.value)
  : data.workItems)

function matchesStatus(item: WorkItem, key: string): boolean {
  if (key === 'all') return true
  if (key === 'open') return ['inbox', 'working', 'needs_attention'].includes(String(item.status || ''))
  return item.status === (key === 'attention' ? 'needs_attention' : key)
}
const filteredItems = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  const items = scopedItems.value.filter((item) => matchesStatus(item, filter.value)
    && (!q || [
      item.title, item.body, item.attention_reason, item.source,
      item.source_ref, item.role_name, item.team_name, item.workspace_name,
    ].map((value) => String(value || '')).join(' ').toLowerCase().includes(q)))
  return items.sort((a, b) => {
    const priority = sort.value === 'priority' ? Number(b.priority || 0) - Number(a.priority || 0) : 0
    return priority || (Date.parse((b.updated_at || '').replace(' ', 'T')) || 0) - (Date.parse((a.updated_at || '').replace(' ', 'T')) || 0)
  })
})

const pagedItems = computed(() => pageSlice(filteredItems.value, page.value, PAGE_SIZE))
const pages = computed(() => pageCount(filteredItems.value.length, PAGE_SIZE))
const filtersActive = computed(() => filter.value !== 'open'
  || Boolean(keyword.value.trim()) || Boolean(teamFilter.value))

watch([filter, keyword, teamFilter, sort], () => { page.value = 1 })
watch(pages, (total) => { page.value = clampPage(page.value, total) })

function clearTeamFilter(): void {
  teamFilter.value = ''
  if (route.query.team_id) {
    const query = { ...route.query }
    delete query.team_id
    void router.replace({ query })
  }
}

function clearFilters(): void {
  filter.value = 'open'
  keyword.value = ''
  clearTeamFilter()
}

const filterCounts = computed<Record<string, number>>(() => ({
  open: Number(stats.value.open || 0),
  attention: Number(stats.value.attention || 0),
  inbox: data.workItems.filter((item) => item.status === 'inbox').length,
  working: Number(stats.value.working || 0),
  done: Number(stats.value.done || 0),
  all: data.workItems.length,
}))

function countFor(key: string): number {
  if (teamFilter.value) return scopedItems.value.filter((item) => matchesStatus(item, key)).length
  return filterCounts.value[key] || 0
}

const attentionItems = computed(() => {
  const rows: Array<{ kind: 'approval' | 'question'; id: string; title: string; sub: string; conversation_id?: string }> = []
  for (const approval of attention.data.approvals || []) {
    rows.push({
      kind: 'approval',
      id: approval.id,
      title: approval.tool || approval.name || '工具授权',
      sub: approval.note || '需要你批准后才能继续',
      conversation_id: approval.conversation_id,
    })
  }
  for (const question of attention.data.questions || []) {
    rows.push({
      kind: 'question',
      id: question.id,
      title: question.question || '代理在等你回答',
      sub: '回答后代理继续推进',
      conversation_id: question.conversation_id,
    })
  }
  return rows
})

const workId = computed(() => String(route.params.id || ''))
const selectedItem = computed(() => data.workItems.find((item) => item.id === workId.value) || null)

onMounted(() => {
  void attention.load()
})

function openItem(item: WorkItem): void {
  void router.push(`/work/${encodeURIComponent(item.id)}`)
}

function closeDetail(): void {
  if (workId.value) void router.push('/work')
}

function onCreated(id: string): void {
  createOpen.value = false
  void router.push(`/work/${encodeURIComponent(id)}`)
}

function onDeleted(): void {
  void router.push('/work')
}

function openPolicy(): void {
  policyForm.mode = String(policy.value.mode || 'observe')
  policyForm.permission_mode = String(policy.value.permission_mode || 'auto')
  policyForm.default_workspace_id = String(policy.value.default_workspace_id || '')
  policyForm.default_role_id = String(policy.value.default_role_id || '')
  policyForm.auto_execute = Boolean(policy.value.auto_execute)
  policyOpen.value = true
}

async function savePolicy(): Promise<void> {
  if (policySaving.value) return
  policySaving.value = true
  try {
    await api('/work-items/settings', {
      method: 'PUT',
      body: {
        mode: policyForm.mode,
        permission_mode: policyForm.permission_mode,
        default_workspace_id: policyForm.default_workspace_id || null,
        default_role_id: policyForm.default_role_id || null,
        auto_execute: policyForm.auto_execute,
      },
    })
    await data.loadWork()
    policyOpen.value = false
    app.toast('工单策略已保存')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    policySaving.value = false
  }
}

function openAttentionConversation(conversationId?: string): void {
  if (!conversationId) return
  void router.push(`/conv/${encodeURIComponent(conversationId)}`)
}
</script>

<template>
  <PageHeader section="工单" title="工单总览">
    <el-button :icon="Setting" @click="openPolicy">工单策略</el-button>
    <el-button type="primary" :icon="Plus" @click="createOpen = true">新建工单</el-button>
  </PageHeader>

  <div class="canvas-body operations-body">
    <div class="sa-dashboard">
      <section class="operations-hero" aria-labelledby="operations-title">
        <div class="orbital-field" aria-hidden="true">
          <i class="orbit orbit-outer" /><i class="orbit orbit-middle" /><i class="orbit orbit-inner" />
          <i class="orbital-core" /><i class="orbital-point" />
        </div>
        <div class="operations-copy">
          <span class="sa-pill"><i class="sa-dot" :class="{ on: mode === 'auto' }" />{{ modeText }}</span>
          <h2 id="operations-title">Agent 任务中心</h2>
          <p>跟踪工单执行、处理授权与提问。</p>
          <TokenMeter v-if="hasWorkUsage" :usage="workUsage" variant="hero" />
        </div>
      </section>

      <div class="operations-metrics" aria-label="按工单状态筛选">
        <button v-for="metric in metrics" :key="metric.key" class="operations-metric" type="button"
          :data-tone="metric.tone" :aria-pressed="filter === metric.key" @click="focusFilter(metric.key)">
          <span class="metric-icon"><component :is="metric.icon" aria-hidden="true" /></span>
          <ArrowRight class="metric-arrow" aria-hidden="true" />
          <strong>{{ workReady ? countFor(metric.key) : '—' }}</strong>
          <span>{{ metric.label }}</span>
        </button>
      </div>

      <section class="sa-attention" :class="{ 'sa-attention-empty': !attentionItems.length }">
        <span v-if="!attentionItems.length" class="sa-attention-ico"><component :is="attention.data.counts ? Check : Clock" aria-hidden="true" /></span>
        <div v-if="!attentionItems.length">
          <b>{{ attention.data.counts ? '没有待处理的授权或提问' : '暂未获取授权与提问状态' }}</b>
        </div>
        <template v-else>
          <div class="sa-section-head">
            <h2>待处理的授权与提问</h2>
            <span class="sa-count danger">{{ attentionItems.length }}</span>
          </div>
          <div class="sa-attention-list">
            <div v-for="item in attentionItems" :key="`${item.kind}-${item.id}`" class="sa-attention-row">
              <span class="sa-attention-type" :class="item.kind">{{ item.kind === 'approval' ? '授权' : '提问' }}</span>
              <span class="sa-attention-main"><b>{{ item.title }}</b><span>{{ item.sub }}</span></span>
              <button v-if="item.conversation_id" class="btn small" type="button" @click="openAttentionConversation(item.conversation_id)">打开会话</button>
            </div>
          </div>
        </template>
      </section>

      <div class="sa-columns">
        <section class="sa-section">
          <div class="sa-section-head">
            <h2 ref="listHeading" tabindex="-1">工单列表</h2>
            <span class="sa-count" aria-live="polite">{{ filteredItems.length }} 条结果</span>
          </div>
          <div class="list-toolbar">
            <el-input v-model="keyword" :prefix-icon="Search" aria-label="搜索工单" placeholder="搜索工单" clearable />
            <select v-model="sort" class="input work-sort" aria-label="工单排序">
              <option value="recent">最近更新</option>
              <option value="priority">优先级优先</option>
            </select>
            <button v-if="teamFilter" class="work-filter on" type="button" title="清除群组筛选" @click="clearTeamFilter">
              群组：{{ teamFilterName }} ×
            </button>
            <button v-if="filtersActive" class="mini-link" type="button" @click="clearFilters">清除筛选</button>
          </div>
          <div class="work-filters">
            <button
              v-for="item in filters"
              :key="item.key"
              class="work-filter"
              :class="{ on: filter === item.key }"
              :aria-pressed="filter === item.key"
              type="button"
              @click="filter = item.key"
            >
              {{ item.label }}<template v-if="countFor(item.key)"> {{ countFor(item.key) }}</template>
            </button>
          </div>

          <div v-if="data.workError" class="work-load-error" role="status">
            <span>{{ workReady ? '工单刷新失败，正在显示上次结果' : '工单加载失败' }}</span>
            <button class="btn small" type="button" @click="data.loadWork()">重试</button>
          </div>
          <div v-if="filteredItems.length" class="work-queue">
            <article
              v-for="item in pagedItems"
              :key="item.id"
              class="tile-card work-tile work-queue-item"
              :class="{ attention: item.status === 'needs_attention' }"
              @contextmenu.prevent.stop="openWorkMenu($event, item)"
            >
              <div class="tile-head">
                <span class="work-status" :class="item.status" />
                <div class="tile-title">
                  <router-link class="work-queue-link" :to="`/work/${encodeURIComponent(item.id)}`">
                    {{ item.title || '未命名工单' }}
                  </router-link>
                  <span>{{ statusLabels[String(item.status || '')] || item.status || '待分派' }} · {{ item.source || 'manual' }}{{ item.source_ref ? ` · ${item.source_ref}` : '' }}</span>
                </div>
                <span v-if="Number(item.priority || 0) > 0" class="tag">高优先级</span>
                <button class="iconbtn work-queue-menu" type="button" :aria-label="`工单操作：${item.title || item.id}`"
                  @click="openWorkMenu($event, item)"><MoreFilled aria-hidden="true" /></button>
              </div>
              <p class="tile-preview">{{ item.body || item.attention_reason || '没有补充说明' }}</p>
              <TokenMeter :usage="item.usage" variant="tile" />
              <div class="tile-foot"><span>{{ shortTime(item.updated_at) }}</span></div>
            </article>
          </div>
          <div v-else-if="data.workItems.length" class="empty">
            没有符合条件的工单
            <br />
            <button class="mini-link" type="button" @click="clearFilters">清除筛选</button>
          </div>
          <div v-else-if="workReady" class="work-empty">
            <Document aria-hidden="true" />
            <b>还没有工单</b>
            <button class="btn primary" type="button" @click="createOpen = true">创建工单</button>
          </div>
          <ListPager :page="page" :pages="pages" :total="filteredItems.length" :page-size="PAGE_SIZE" @update:page="page = $event" />
        </section>

        <section class="sa-section sa-policy-card">
          <div class="sa-section-head"><h2>执行策略</h2><Setting aria-hidden="true" /></div>
          <div class="sa-policy-row"><span>模式</span><b>{{ modeText }}</b></div>
          <div class="sa-policy-row"><span>新会话权限</span><b>{{ permissionText }}</b></div>
          <div class="sa-policy-row"><span>默认工作区</span><b>{{ defaultWorkspaceText }}</b></div>
          <button class="btn" type="button" @click="openPolicy">调整策略</button>
        </section>
      </div>
    </div>
  </div>

  <WorkEditorDialog :open="createOpen" @close="createOpen = false" @saved="onCreated" />
  <WorkSettingsDrawer
    :open="Boolean(selectedItem)"
    :item="selectedItem"
    @close="closeDetail"
    @deleted="onDeleted"
  />

  <SideDrawer :open="policyOpen" title="工单策略" subtitle="决定工单何时自动推进" width="560px" @close="policyOpen = false">
    <div class="work-settings-grid">
      <div class="field">
        <label>运行模式</label>
        <select v-model="policyForm.mode" class="input">
          <option value="off">关闭：只手动创建工单</option>
          <option value="observe">观察：只记录，不自动派活</option>
          <option value="auto">自动：新建工单后自动派活</option>
        </select>
      </div>
      <div class="field">
        <label>新会话权限</label>
        <select v-model="policyForm.permission_mode" class="input">
          <option value="auto">自动判断（高危才问）</option>
          <option value="once">每次询问</option>
          <option value="session">会话允许</option>
          <option value="always">完全允许</option>
        </select>
      </div>
      <div class="field">
        <label>默认工作区</label>
        <WorkspacePicker
          v-model="policyForm.default_workspace_id"
          :workspaces="data.workspaces"
          empty-label="默认"
          placeholder="默认"
        />
      </div>
      <div class="field">
        <label>默认角色</label>
        <OptionPicker
          v-model="policyForm.default_role_id"
          :items="roleOptions"
          title="选择默认角色"
          placeholder="默认"
          empty-label="默认"
          empty-hint="使用系统默认角色"
        />
      </div>
    </div>
    <div class="field" style="margin-top:10px">
      <label>执行策略</label>
      <label class="switch">
        <input v-model="policyForm.auto_execute" type="checkbox" />
        <span class="track" />
        <span class="switch-label">跳过常规确认直接执行（高风险仍受权限档位约束）</span>
      </label>
    </div>
    <template #footer>
      <el-button @click="policyOpen = false">取消</el-button>
      <el-button type="primary" :loading="policySaving" @click="savePolicy">保存</el-button>
    </template>
  </SideDrawer>

  <ContextMenu
    :open="workMenu.open"
    :x="workMenu.x"
    :y="workMenu.y"
    :items="workMenuItems"
    @close="workMenu.open = false"
    @select="onWorkMenu"
  />
</template>

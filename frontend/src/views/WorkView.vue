<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import PageHeader from '@/components/PageHeader.vue'
import WorkEditorDialog from '@/components/WorkEditorDialog.vue'
import WorkSettingsDrawer from '@/components/WorkSettingsDrawer.vue'
import { api } from '@/api/client'
import type { WorkItem } from '@/api/types'
import { shortTime } from '@/lib/format'
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
const policyOpen = ref(false)
const policySaving = ref(false)
const policyForm = reactive({
  mode: 'observe',
  permission_mode: 'auto',
  default_workspace_id: '',
  default_role_id: '',
  default_team_id: '',
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
  workMenu.x = event.clientX
  workMenu.y = event.clientY
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

const filteredItems = computed(() => {
  const key = filter.value
  if (key === 'all') return data.workItems
  if (key === 'open') return data.workItems.filter((item) => ['inbox', 'working', 'needs_attention'].includes(String(item.status || '')))
  if (key === 'attention') return data.workItems.filter((item) => item.status === 'needs_attention')
  return data.workItems.filter((item) => item.status === key)
})

const filterCounts = computed<Record<string, number>>(() => ({
  open: Number(stats.value.open || 0),
  attention: Number(stats.value.attention || 0),
  inbox: data.workItems.filter((item) => item.status === 'inbox').length,
  working: Number(stats.value.working || 0),
  done: Number(stats.value.done || 0),
  all: data.workItems.length,
}))

function countFor(key: string): number {
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
  policyForm.default_team_id = String(policy.value.default_team_id || '')
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
        default_team_id: policyForm.default_team_id || null,
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
  <PageHeader section="工单" title="注意力与工单" :chip="`${stats.open || 0} 个待办`">
    <el-button type="primary" @click="createOpen = true">新建工单</el-button>
    <el-button @click="openPolicy">策略</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="sa-dashboard">
      <section class="sa-hero">
        <div class="sa-orb">
          <svg viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="12" rx="4" /><path d="M12 3v4M9 12h.01M15 12h.01M9 16h6" /></svg>
        </div>
        <div class="sa-hero-main">
          <div class="sa-eyebrow">工单 · 主动执行层</div>
          <div class="sa-pills">
            <span class="sa-pill"><i class="sa-dot" :class="{ on: mode === 'auto' }" />{{ modeText }}</span>
            <span class="sa-pill"><i class="sa-dot" :class="{ on: Number(stats.working || 0) > 0 }" />{{ stats.working || 0 }} 个运行中</span>
            <span class="sa-pill" :class="{ danger: Number(stats.attention || 0) > 0 }">
              <i class="sa-dot" :class="{ danger: Number(stats.attention || 0) > 0 }" />{{ stats.attention || 0 }} 个需要你
            </span>
          </div>
        </div>
      </section>

      <section v-if="!attentionItems.length" class="sa-attention sa-attention-empty">
        <span class="sa-attention-ico">
          <svg viewBox="0 0 24 24"><path d="m5 12 4 4L19 6" /></svg>
        </span>
        <div><b>现在没有需要你处理的事</b><span>工单会在需要授权或回答时回来。</span></div>
      </section>
      <section v-else class="sa-attention">
        <div class="sa-section-head">
          <div><h2>需要你处理</h2><p>这些事停下来等你决定，其余工作继续在后台推进。</p></div>
          <span class="sa-count danger">{{ attentionItems.length }}</span>
        </div>
        <div class="sa-attention-list">
          <div v-for="item in attentionItems" :key="`${item.kind}-${item.id}`" class="sa-attention-row">
            <span class="sa-attention-type" :class="item.kind">{{ item.kind === 'approval' ? '授权' : '提问' }}</span>
            <span class="sa-attention-main"><b>{{ item.title }}</b><span>{{ item.sub }}</span></span>
            <button v-if="item.conversation_id" class="btn small" type="button" @click="openAttentionConversation(item.conversation_id)">打开会话</button>
          </div>
        </div>
      </section>

      <div class="work-stats sa-stats">
        <div class="work-stat sa-stat">
          <span class="sa-stat-ico"><svg viewBox="0 0 24 24"><path d="M5 4h14v17H5z" /><path d="M8 8h8M8 12h8M8 16h5" /></svg></span>
          <div><b>{{ stats.open || 0 }}</b><span>待办（含进行中）</span></div>
        </div>
        <div class="work-stat sa-stat">
          <span class="sa-stat-ico accent"><svg viewBox="0 0 24 24"><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /><circle cx="12" cy="12" r="5" /></svg></span>
          <div><b>{{ stats.working || 0 }}</b><span>正在执行</span></div>
        </div>
        <div class="work-stat attention sa-stat">
          <span class="sa-stat-ico danger"><svg viewBox="0 0 24 24"><path d="M12 4 3 20h18Z" /><path d="M12 10v4M12 17h.01" /></svg></span>
          <div><b>{{ stats.attention || 0 }}</b><span>需要你</span></div>
        </div>
        <div class="work-stat sa-stat">
          <span class="sa-stat-ico ok"><svg viewBox="0 0 24 24"><path d="m5 12 4 4L19 6" /></svg></span>
          <div><b>{{ stats.done || 0 }}</b><span>已完成</span></div>
        </div>
      </div>

      <div class="sa-columns">
        <section class="sa-section">
          <div class="sa-section-head">
            <div><h2>全部工单</h2><p>点开工单查看详情或派活；按状态筛选。</p></div>
            <span class="sa-count">{{ stats.total || data.workItems.length }}</span>
          </div>
          <div class="work-filters">
            <button
              v-for="item in filters"
              :key="item.key"
              class="work-filter"
              :class="{ on: filter === item.key }"
              type="button"
              @click="filter = item.key"
            >
              {{ item.label }}<template v-if="countFor(item.key)"> {{ countFor(item.key) }}</template>
            </button>
          </div>

          <div v-if="filteredItems.length" class="tile-grid work-grid">
            <article
              v-for="item in filteredItems"
              :key="item.id"
              class="tile-card work-tile"
              :class="{ attention: item.status === 'needs_attention' }"
              @click="openItem(item)"
              @contextmenu.prevent.stop="openWorkMenu($event, item)"
            >
              <div class="tile-head">
                <span class="work-status" :class="item.status" />
                <div class="tile-title">
                  <b>{{ item.title || '未命名工单' }}</b>
                  <span>{{ statusLabels[String(item.status || '')] || item.status || '待分派' }} · {{ item.source || 'manual' }}{{ item.source_ref ? ` · ${item.source_ref}` : '' }}</span>
                </div>
                <span v-if="item.priority" class="tag">高</span>
              </div>
              <p class="tile-preview">{{ item.body || item.attention_reason || '没有补充说明' }}</p>
              <div class="tile-foot"><span>{{ shortTime(item.updated_at) }}</span><span>{{ statusLabels[String(item.status || '')] || item.status }}</span></div>
            </article>
          </div>
          <div v-else class="sa-onboard">
            <div class="sa-onboard-step"><b>1</b><span>新建一个工单，写清要做什么</span></div>
            <div class="sa-onboard-step"><b>2</b><span>派活后工单在隔离会话里执行</span></div>
            <div class="sa-onboard-step"><b>3</b><span>只有需要授权 / 回答 / 失败时才回来</span></div>
            <button class="btn primary" type="button" @click="createOpen = true">创建第一个工单</button>
          </div>
        </section>

        <section class="sa-section sa-policy-card">
          <div class="sa-section-head"><div><h2>工作方式</h2><p>决定工单何时自动推进。</p></div></div>
          <div class="sa-policy-row"><span>模式</span><b>{{ modeText }}</b></div>
          <div class="sa-policy-row"><span>新会话权限</span><b>{{ permissionText }}</b></div>
          <div class="sa-policy-row"><span>默认工作区</span><b>{{ defaultWorkspaceText }}</b></div>
          <p class="sa-policy-note">会话、角色、群组、工作区都可以「新建工单」；工单完成后结果会回写到这里。</p>
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
        <select v-model="policyForm.default_workspace_id" class="input">
          <option value="">默认</option>
          <option v-for="ws in data.activeWorkspaces" :key="ws.id" :value="ws.id">{{ ws.name }}</option>
        </select>
      </div>
      <div class="field">
        <label>默认角色</label>
        <select v-model="policyForm.default_role_id" class="input">
          <option value="">默认</option>
          <option v-for="role in data.roles" :key="role.id" :value="role.id">{{ role.name }}</option>
        </select>
      </div>
      <div class="field">
        <label>默认群组</label>
        <select v-model="policyForm.default_team_id" class="input">
          <option value="">不绑定</option>
          <option v-for="team in data.teams" :key="team.id" :value="team.id">{{ team.name }}</option>
        </select>
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

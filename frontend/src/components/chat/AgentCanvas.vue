<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Handle, MarkerType, Position, VueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import { api } from '@/api/client'
import type { AgentActivity, AgentGraph, AgentNode, TeamTask, TeamTaskMember } from '@/api/types'
import { durationText, firstLine } from '@/lib/format'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'
import { useRunStream } from '@/composables/useRunStream'
import Avatar from '@/components/Avatar.vue'
import ProcessList from './ProcessList.vue'

const props = defineProps<{ conversationId: string; open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const app = useAppStore()
const dataStore = useDataStore()
const data = ref<AgentGraph | null>(null)
const tasks = ref<TeamTask[]>([])
const loading = ref(false)
const tasksLoading = ref(false)
const ACTIVE_STATUSES = new Set(['running', 'queued', 'paused'])
const selectedId = ref('')
const selectedTaskId = ref('')
const selectedRoleId = ref('')
const selectedRunId = ref('')
const taskBusy = ref('')
const flowKey = ref(0)
const flowNodes = ref<any[]>([])
const flowEdges = ref<any[]>([])
const vfNodes = computed(() => flowNodes.value)
const vfEdges = computed(() => flowEdges.value)
let timer: number | null = null
let flowSignature = ''

const {
  run: streamRun,
  entries: streamEntries,
  streaming: streamStreaming,
  error: streamError,
  open: openStream,
  close: closeStream,
} = useRunStream()

const title = computed(() => data.value?.conversation?.title || '执行画布')
const activeTasks = computed(() => tasks.value.filter((task) => task.status === 'running' || task.status === 'paused'))
const continueTasks = computed(() => tasks.value.filter((task) => task.can_continue))
const activeTask = computed(() => activeTasks.value.find((task) => task.id === selectedTaskId.value) || null)
const selectedMember = computed<TeamTaskMember | null>(() => {
  if (!activeTask.value || !selectedRoleId.value) return null
  return visibleTaskMembers.value.find((member) => member.role_id === selectedRoleId.value) || null
})
const selectedNodeData = computed<Record<string, any> | null>(() => {
  const node = flowNodes.value.find((item) => item.id === selectedId.value)
  return (node?.data as Record<string, any>) || null
})
const stats = computed(() => data.value?.stats || {})
const runStatus = computed(() => activeTask.value?.status || data.value?.root?.status || data.value?.run?.status || 'idle')
const activeAgentNames = computed(() => {
  const names = new Set<string>()
  for (const task of activeTasks.value) {
    for (const member of task.members || []) {
      if (ACTIVE_STATUSES.has(memberStatus(member))) names.add(String(member.role_name || member.role_id || ''))
    }
  }
  for (const node of data.value?.nodes || []) {
    if (isActiveStatus(node.status)) names.add(String(node.name || ''))
  }
  return names
})
const activity = computed(() => (data.value?.activity || [])
  .filter((item) => activeAgentNames.value.has(String(item.agent || '')))
  .slice()
  .sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0))
  .slice(0, 60))
const runningTasks = computed(() => activeTasks.value.filter((task) => task.status === 'running').length)
const activeToolCount = computed(() => flowNodes.value.reduce((sum, node) => sum + ((node.data?.tools || []).length), 0))

const graphNodeByRun = computed(() => {
  const out = new Map<string, AgentNode>()
  for (const node of data.value?.nodes || []) {
    if (node.run_id) out.set(node.run_id, node)
  }
  return out
})

const visibleTaskMembers = computed<TeamTaskMember[]>(() =>
  (activeTask.value?.members || []).filter((member) => ACTIVE_STATUSES.has(memberStatus(member))),
)

function memberStatus(member: TeamTaskMember): string {
  return String(member.status || member.run_status || 'queued')
}

function isActiveStatus(status?: string): boolean {
  return ACTIVE_STATUSES.has(String(status || ''))
}

function statusLabel(status?: string): string {
  return ({
    running: '运行中', queued: '排队', paused: '已暂停', done: '完成',
    failed: '失败', stopped: '已结束', idle: '待命',
  } as Record<string, string>)[String(status || '')] || '待命'
}

function statusTone(status?: string): string {
  const value = String(status || '')
  if (value === 'running' || value === 'queued') return 'running'
  if (value === 'paused') return 'paused'
  if (value === 'done') return 'done'
  if (value === 'failed') return 'failed'
  if (value === 'stopped') return 'idle'
  return 'idle'
}

function taskTone(status?: string): string {
  return statusTone(status)
}

function kindLabel(kind?: string): string {
  if (kind === 'leader') return '主持 / 派发人'
  if (kind === 'member') return '团队成员 · 并行'
  if (kind === 'root') return '主持'
  if (kind === 'team') return '团队成员 · 待命'
  return '子代理'
}

function progressFor(status?: string): number {
  if (status === 'done' || status === 'failed' || status === 'stopped') return 100
  if (status === 'queued') return 6
  if (status === 'paused') return 50
  if (status === 'running') return 58
  return 0
}

function activityTone(item: AgentActivity): string {
  if (item.status === 'failed') return 'failed'
  if (item.status === 'running' || item.status === 'queued') return 'running'
  return 'done'
}

function timeText(item: AgentActivity): string {
  if (!item.ts) return ''
  const n = Number(item.ts)
  return new Date(n * (n < 1e12 ? 1000 : 1)).toLocaleTimeString()
}

function buildTaskFlow(task: TeamTask): { nodes: any[]; edges: any[] } {
  const members = visibleTaskMembers.value
  const leaderId = `leader-${task.id}`
  const leaderY = Math.max(0, (members.length - 1) * 82)
  const nodes: any[] = [{
    id: leaderId,
    type: 'agent',
    position: { x: 0, y: leaderY },
    data: {
      kind: 'leader',
      name: task.leader_name || data.value?.root?.name || '主持',
      goal: task.goal || task.title || '',
      status: task.status,
      current: `${task.active_runs || 0} 个成员并行 · ${task.done_members || 0}/${task.total_members || 0} 完成`,
      tools: [],
      elapsed_ms: 0,
      runId: '',
      taskId: task.id,
      roleId: task.leader_role_id || '',
      avatar: dataStore.roleAvatar(task.leader_role_id || ''),
    },
  }]
  const edges: any[] = []
  members.forEach((member, index) => {
    const status = memberStatus(member)
    const graph = graphNodeByRun.value.get(String(member.run_id || ''))
    const id = `member-${task.id}-${member.role_id || index}`
    nodes.push({
      id,
      type: 'agent',
      position: { x: 360, y: index * 164 },
      data: {
        kind: 'member',
        name: member.role_name || member.role_id || '成员',
        goal: member.goal || '',
        status,
        current: graph?.current || (status === 'queued' ? '等待执行' : ''),
        tools: graph?.tools || [],
        elapsed_ms: graph?.elapsed_ms || 0,
        error: graph?.error || member.error || '',
        runId: member.run_id || '',
        roleId: member.role_id || '',
        avatar: dataStore.roleAvatar(member.role_id || ''),
        taskId: task.id,
      },
    })
    edges.push({
      id: `edge-${leaderId}-${id}`,
      source: leaderId,
      target: id,
      type: 'smoothstep',
      animated: status === 'running' || status === 'queued',
      label: status === 'running' ? '并行执行' : '',
      markerEnd: MarkerType.ArrowClosed,
      style: {
        stroke: status === 'running' ? 'var(--accent)' : 'var(--line-strong)',
        strokeWidth: 1.5,
      },
    })
  })
  return { nodes, edges }
}

function buildConversationFlow(): { nodes: any[]; edges: any[] } {
  const root = data.value?.root
  const children = (data.value?.nodes || []).filter((node) => isActiveStatus(node.status))
  if (!root) return { nodes: [], edges: [] }
  if (!children.length && !isActiveStatus(root.status)) return { nodes: [], edges: [] }
  const nodes: any[] = [{
    id: 'root',
    type: 'agent',
    position: { x: 0, y: Math.max(0, (children.length - 1) * 82) },
    data: {
      kind: 'root',
      name: root.name || '主持',
      goal: root.goal || '',
      status: root.status,
      current: root.current || '',
      tools: root.tools || [],
      elapsed_ms: root.elapsed_ms || 0,
      error: root.error || '',
      runId: root.run_id || '',
      roleId: root.role_id || '',
      avatar: root.avatar || dataStore.roleAvatar(root.role_id || ''),
    },
  }]
  const edges: any[] = []
  const columns = children.length > 4 ? 2 : 1
  children.forEach((node, index) => {
    const col = Math.floor(index / Math.ceil(children.length / columns))
    const row = index % Math.ceil(children.length / columns)
    const id = `graph-${node.id}`
    nodes.push({
      id,
      type: 'agent',
      position: { x: 360 + col * 300, y: row * 164 },
      data: {
        kind: node.kind || 'subagent',
        name: node.name || node.id,
        goal: node.goal || '',
        status: node.status,
        current: node.current || '',
        tools: node.tools || [],
        elapsed_ms: node.elapsed_ms || 0,
        error: node.error || '',
        runId: node.run_id || '',
        roleId: node.role_id || '',
        avatar: node.avatar || dataStore.roleAvatar(node.role_id || ''),
      },
    })
    edges.push({
      id: `edge-root-${id}`,
      source: 'root',
      target: id,
      type: 'smoothstep',
      animated: node.status === 'running' || node.status === 'queued',
      markerEnd: MarkerType.ArrowClosed,
      style: {
        stroke: node.status === 'running' ? 'var(--accent)' : 'var(--line-strong)',
        strokeWidth: 1.5,
      },
    })
  })
  return { nodes, edges }
}

function buildFlow(): void {
  const built = activeTask.value ? buildTaskFlow(activeTask.value) : buildConversationFlow()
  const signature = built.nodes.map((node) => node.id).join('|')
  flowNodes.value = built.nodes
  flowEdges.value = built.edges
  if (signature !== flowSignature) {
    flowSignature = signature
    flowKey.value += 1
  }
}

function selectTask(taskId: string): void {
  selectedTaskId.value = taskId
}

function selectMember(member: TeamTaskMember): void {
  selectedRoleId.value = String(member.role_id || '')
  selectedId.value = `member-${activeTask.value?.id || ''}-${member.role_id || ''}`
  const runId = String(member.run_id || '')
  selectedRunId.value = runId
  if (runId) openStream(runId)
  else closeStream()
}

function onNodeClick(payload: any): void {
  const node = payload?.node || payload
  const nodeData = (node?.data || {}) as Record<string, any>
  selectedId.value = String(node?.id || '')
  const runId = String(nodeData.runId || '')
  if (runId) {
    selectedRoleId.value = String(nodeData.roleId || '')
    selectedRunId.value = runId
    openStream(runId)
  } else {
    selectedRoleId.value = ''
    selectedRunId.value = ''
    closeStream()
  }
}

async function taskAction(task: TeamTask, action: 'pause' | 'resume' | 'stop' | 'continue'): Promise<void> {
  if (taskBusy.value) return
  if (action === 'stop' && !window.confirm(`结束后台任务「${task.title || task.id}」？正在执行的成员会被停止。`)) return
  taskBusy.value = `${task.id}:${action}`
  try {
    await api(`/conversations/${encodeURIComponent(props.conversationId)}/team-tasks/${encodeURIComponent(task.id)}/${action}`, {
      method: 'POST',
    })
    await refresh()
    app.toast(action === 'pause' ? '后台任务已暂停'
      : action === 'resume' ? '后台任务已继续'
      : action === 'continue' ? '已交回主会话，结果会汇总成一条回复'
      : '后台任务已结束')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    taskBusy.value = ''
  }
}

function syncSelectedRun(): void {
  if (!selectedRoleId.value) return
  const member = visibleTaskMembers.value.find((item) => item.role_id === selectedRoleId.value)
  if (!member) {
    selectedRoleId.value = ''
    selectedRunId.value = ''
    closeStream()
    return
  }
  const runId = String(member.run_id || '')
  if (!runId) {
    selectedRunId.value = ''
    closeStream()
    return
  }
  if (runId !== selectedRunId.value) {
    selectedRunId.value = runId
    openStream(runId)
  }
}

async function refresh(): Promise<void> {
  if (!props.conversationId) return
  loading.value = !data.value
  tasksLoading.value = !data.value
  try {
    const [graph, taskData] = await Promise.all([
      api<AgentGraph>(`/conversations/${encodeURIComponent(props.conversationId)}/graph`),
      api<{ items: TeamTask[]; running?: number }>(`/conversations/${encodeURIComponent(props.conversationId)}/team-tasks`),
    ])
    data.value = graph
    tasks.value = taskData.items || []
    const active = tasks.value.filter((task) => task.status === 'running' || task.status === 'paused')
    if (selectedTaskId.value && selectedTaskId.value !== '__all__' && !active.some((task) => task.id === selectedTaskId.value)) {
      selectedTaskId.value = ''
      selectedRoleId.value = ''
      selectedRunId.value = ''
      selectedId.value = ''
      closeStream()
    }
    if (!selectedTaskId.value) {
      const next = active.find((task) => task.status === 'running') || active[0]
      if (next) selectedTaskId.value = next.id
    }
    buildFlow()
    syncSelectedRun()
  } catch (error) {
    data.value = {
      conversation: { title: '画布加载失败' },
      nodes: [],
      activity: [{ text: error instanceof Error ? error.message : String(error) }],
    }
    tasks.value = []
  } finally {
    loading.value = false
    tasksLoading.value = false
  }
}

function stop(): void {
  if (timer) {
    window.clearInterval(timer)
    timer = null
  }
  closeStream()
}

watch(() => [props.open, props.conversationId], ([open]) => {
  if (open) {
    void refresh()
    if (timer) window.clearInterval(timer)
    timer = window.setInterval(() => {
      if (props.open) void refresh()
    }, 2000)
  } else {
    stop()
  }
}, { immediate: true })

watch(selectedTaskId, () => {
  selectedRoleId.value = ''
  selectedRunId.value = ''
  selectedId.value = ''
  closeStream()
  buildFlow()
})

watch([data, tasks], () => buildFlow())

onBeforeUnmount(stop)
</script>

<template>
  <div v-if="open" class="agent-canvas">
    <header class="agent-canvas-head">
      <div>
        <h2>执行画布</h2>
        <p>
          {{ activeTask?.title || title }} · {{ statusLabel(runStatus) }} ·
          <template v-if="activeTask">
            {{ activeTask.done_members || 0 }}/{{ activeTask.total_members || 0 }} 完成 ·
            {{ activeTask.active_runs || 0 }} 个成员并行
          </template>
          <template v-else>
            {{ flowNodes.length }} 个运行中节点 · {{ activeToolCount }} 个工具
          </template>
          <span v-if="runningTasks" class="agent-parallel">后台任务 {{ runningTasks }}</span>
        </p>
      </div>
      <div class="agent-canvas-actions">
        <button class="btn small" @click="refresh">刷新</button>
        <button class="iconbtn" title="关闭" aria-label="关闭" @click="emit('close')">×</button>
      </div>
    </header>

    <div class="agent-canvas-body">
      <aside class="agent-task-rail">
        <div class="agent-task-head">
          <b>后台任务</b>
          <span>{{ activeTasks.length }}</span>
        </div>
        <div class="agent-task-hint">运行中 / 已暂停的任务在这里控制；已完成的可在下方交回主会话。</div>
        <button class="agent-task-all" :class="{ active: !selectedTaskId || selectedTaskId === '__all__' }" type="button" @click="selectedTaskId = '__all__'">全部执行</button>
        <div class="agent-task-list">
          <div
            v-for="task in activeTasks"
            :key="task.id"
            class="agent-task-card"
            :class="[taskTone(task.status), { active: selectedTaskId === task.id }]"
            @click="selectTask(task.id)"
          >
            <div class="agent-task-title">
              <b>{{ task.title || task.id }}</b>
              <span class="agent-task-status" :class="taskTone(task.status)">{{ statusLabel(task.status) }}</span>
            </div>
            <small>{{ task.done_members || 0 }}/{{ task.total_members || 0 }} 完成 · {{ task.active_runs || 0 }} 运行中</small>
            <div class="agent-task-actions">
              <button v-if="task.can_pause" class="btn small" :disabled="taskBusy === `${task.id}:pause`" @click.stop="taskAction(task, 'pause')">暂停</button>
              <button v-if="task.can_resume" class="btn small" :disabled="taskBusy === `${task.id}:resume`" @click.stop="taskAction(task, 'resume')">继续</button>
              <button v-if="task.can_stop" class="btn small danger" :disabled="taskBusy === `${task.id}:stop`" @click.stop="taskAction(task, 'stop')">结束</button>
            </div>
          </div>
          <div v-if="tasksLoading" class="agent-feed-empty">加载后台任务…</div>
          <div v-else-if="!activeTasks.length" class="agent-feed-empty">当前没有运行中的后台任务。</div>
        </div>

        <div v-if="continueTasks.length" class="agent-task-head agent-task-head-done">
          <b>已完成 · 待交回主会话</b>
          <span>{{ continueTasks.length }}</span>
        </div>
        <div v-if="continueTasks.length" class="agent-task-list">
          <div
            v-for="task in continueTasks"
            :key="task.id"
            class="agent-task-card"
            :class="taskTone(task.status)"
          >
            <div class="agent-task-title">
              <b>{{ task.title || task.id }}</b>
              <span class="agent-task-status" :class="taskTone(task.status)">{{ statusLabel(task.status) }}</span>
            </div>
            <small>{{ task.done_members || 0 }}/{{ task.total_members || 0 }} 完成</small>
            <div class="agent-task-actions">
              <button class="btn small" :disabled="taskBusy === `${task.id}:continue`" @click.stop="taskAction(task, 'continue')">继续对话</button>
            </div>
          </div>
        </div>
      </aside>

      <div class="agent-flow-wrap">
        <div v-if="loading" class="empty">加载中…</div>
        <div v-else-if="!flowNodes.length" class="empty agent-flow-empty">当前没有运行中的代理</div>
        <VueFlow
          v-else
          :key="flowKey"
          :nodes="vfNodes"
          :edges="vfEdges"
          :fit-view-on-init="true"
          :nodes-draggable="false"
          :min-zoom="0.2"
          :max-zoom="1.6"
          class="agent-flow"
          @node-click="onNodeClick"
        >
          <Background :gap="18" :size="1" />
          <Controls />
          <MiniMap />
          <template #node-agent="nodeProps">
            <div
              class="flow-agent-node"
              :class="[nodeProps.data.status, { selected: selectedId === nodeProps.id, leader: nodeProps.data.kind === 'leader' || nodeProps.data.kind === 'root' }]"
            >
              <Handle type="target" :position="Position.Left" />
              <div class="flow-agent-head">
                <Avatar :name="nodeProps.data.name" :avatar="nodeProps.data.avatar" />
                <div class="flow-agent-name">
                  <b>{{ nodeProps.data.name }}</b>
                  <span>{{ kindLabel(nodeProps.data.kind) }}</span>
                </div>
                <span class="agent-status" :class="statusTone(nodeProps.data.status)">{{ statusLabel(nodeProps.data.status) }}</span>
              </div>
              <p class="flow-agent-goal">{{ firstLine(nodeProps.data.current || nodeProps.data.goal || '等待中', 90) }}</p>
              <div class="flow-progress"><i :style="{ width: `${progressFor(nodeProps.data.status)}%` }" /></div>
              <div class="flow-agent-meta">
                <span v-if="nodeProps.data.tools?.length">{{ nodeProps.data.tools.length }} 工具</span>
                <span v-if="nodeProps.data.elapsed_ms">{{ durationText(nodeProps.data.elapsed_ms) }}</span>
                <span v-if="nodeProps.data.runId">可看实时</span>
                <span v-if="nodeProps.data.error" class="fail">{{ firstLine(nodeProps.data.error, 30) }}</span>
              </div>
              <Handle type="source" :position="Position.Right" />
            </div>
          </template>
        </VueFlow>
      </div>

      <aside class="agent-stream">
        <template v-if="selectedRoleId">
          <div class="agent-stream-head">
            <div>
              <b>{{ selectedMember?.role_name || streamRun?.role_id || '成员执行过程' }}</b>
              <span>{{ selectedMember?.goal || '' }}</span>
            </div>
            <span class="agent-status" :class="statusTone(streamRun?.status || selectedMember?.status)">{{ statusLabel(streamRun?.status || selectedMember?.status) }}</span>
          </div>
          <div class="agent-stream-body">
            <ProcessList v-if="streamEntries.length" :entries="streamEntries" :live="streamStreaming" />
            <div v-else class="agent-feed-empty">{{ streamStreaming ? '等待成员输出…' : '还没有输出' }}</div>
            <div v-if="streamError" class="run-error">{{ streamError }}</div>
          </div>
        </template>

        <template v-else>
          <section v-if="activeTask" class="agent-detail">
            <h4>{{ activeTask.title || activeTask.id }} · {{ statusLabel(activeTask.status) }}</h4>
            <p>{{ activeTask.goal }}</p>
            <div class="agent-task-members">
              <button
                v-for="member in visibleTaskMembers"
                :key="member.role_id"
                class="agent-task-member"
                type="button"
                @click="selectMember(member)"
              >
                <Avatar :name="member.role_name" :avatar="dataStore.roleAvatar(member.role_id || '')" />
                <span class="agent-task-member-main">
                  <b>{{ member.role_name || member.role_id }}</b>
                  <small>{{ firstLine(member.goal || '', 80) }}</small>
                </span>
                <span class="agent-status" :class="statusTone(memberStatus(member))">{{ statusLabel(memberStatus(member)) }}</span>
              </button>
              <div v-if="!visibleTaskMembers.length" class="agent-feed-empty">当前没有运行中的成员。</div>
            </div>
          </section>

          <section v-else-if="selectedNodeData" class="agent-detail">
            <h4>{{ selectedNodeData.name }} · {{ statusLabel(selectedNodeData.status) }}</h4>
            <p>{{ selectedNodeData.goal || '没有补充目标' }}</p>
            <div class="agent-detail-tools">
              <div v-for="tool in selectedNodeData.tools || []" :key="tool.id || tool.name" class="agent-detail-tool" :class="tool.status === 'running' ? 'running' : (tool.ok === false ? 'failed' : 'done')">
                <i /><span>{{ tool.name }}</span><span>{{ tool.status || 'done' }}</span>
              </div>
              <div v-if="!(selectedNodeData.tools || []).length" class="agent-feed-empty">还没有工具调用</div>
            </div>
            <p v-if="selectedNodeData.text">{{ firstLine(selectedNodeData.text, 420) }}</p>
            <p v-if="selectedNodeData.error" style="color:var(--danger)">{{ firstLine(selectedNodeData.error, 220) }}</p>
          </section>

          <section>
            <h3>最近活动</h3>
            <div class="agent-feed">
              <div v-for="(item, index) in activity" :key="index" class="agent-feed-row" :class="activityTone(item)">
                <span class="agent-dot" :class="activityTone(item)" />
                <div class="agent-feed-main">
                  <b>{{ item.agent || item.agent_id || '代理' }}</b>
                  <span>{{ firstLine(item.text || item.kind || '', 140) }}</span>
                </div>
                <span class="agent-feed-meta">{{ timeText(item) }}</span>
              </div>
              <div v-if="!activity.length" class="agent-feed-empty">还没有活动</div>
            </div>
          </section>
        </template>
      </aside>
    </div>
  </div>
</template>

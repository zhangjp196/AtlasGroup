<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import TokenMeter from '@/components/TokenMeter.vue'
import { api } from '@/api/client'
import type { Conversation, Team, TeamTask, WorkItem } from '@/api/types'
import { compactNumber } from '@/lib/format'
import { hasUsage, usageSummary } from '@/lib/usage'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'
import { useWindowsStore } from '@/stores/windows'

const props = defineProps<{
  id: string
  conversationId: string
  title: string
  x: number
  y: number
  order: number
}>()

const app = useAppStore()
const dataStore = useDataStore()
const windows = useWindowsStore()
const router = useRouter()

const items = ref<WorkItem[]>([])
const tasks = ref<TeamTask[]>([])
const workDetail = ref<WorkItem | null>(null)
// 工单详情抽屉只在点开时用得到，但它静态引入会带进 Markdown/编辑器一大串依赖；
// 改成异步 + v-if，首屏包小一截，第一次打开时再拉这个 chunk。
const WorkSettingsDrawer = defineAsyncComponent(
  () => import('@/components/WorkSettingsDrawer.vue'),
)
const loading = ref(false)
const conv = ref<Conversation | null>(null)
const teamInfo = ref<Team | null>(null)
const memberId = ref('')
const goal = ref('')
const dispatching = ref(false)
let timer = 0

const members = computed(() => (teamInfo.value?.members || []).filter((m) => m.id))
const assigneeName = computed(() => {
  if (members.value.length) {
    const hit = members.value.find((m) => m.id === memberId.value)
    return String(hit?.name || members.value[0]?.name || '成员')
  }
  const rid = String(conv.value?.role_id || '')
  return String(dataStore.roles.find((r) => r.id === rid)?.name || '当前角色')
})

watch(members, (list) => {
  if (list.length && !list.some((m) => m.id === memberId.value)) {
    memberId.value = String(list[0].id || '')
  }
}, { immediate: true })

async function loadMeta(): Promise<void> {
  try {
    conv.value = await api<Conversation>(`/conversations/${encodeURIComponent(props.conversationId)}`)
  } catch {
    return
  }
  const tid = String(conv.value?.team_id || '')
  if (!tid) {
    teamInfo.value = null
    return
  }
  teamInfo.value = dataStore.teams.find((t) => t.id === tid) || null
  if (!teamInfo.value) {
    teamInfo.value = await api<Team>(`/teams/${encodeURIComponent(tid)}`).catch(() => null)
  }
}

const openCount = computed(() => items.value.filter(
  (item) => ['inbox', 'working', 'needs_attention'].includes(String(item.status || '')),
).length)
const activeTasks = computed(() => tasks.value.filter((task) => {
  const status = String(task.status || '')
  return ['running', 'paused', 'queued'].includes(status) || Boolean(task.can_continue)
}))
// 浮窗里的 token 合计：任务 = 成员 run 之和；工单 = 该工单的会话 / 任务用量。
const taskTokens = computed(() => activeTasks.value.reduce(
  (sum, task) => sum + usageSummary(task.usage).total, 0,
))
const workTokens = computed(() => items.value.reduce(
  (sum, item) => sum + usageSummary(item.usage).total, 0,
))


const STATUS_LABEL: Record<string, string> = {
  inbox: '待分派', working: '进行中', needs_attention: '需要你',
  done: '已完成', closed: '已关闭',
  running: '运行中', paused: '已暂停', stopped: '已结束', failed: '失败',
}

function label(status?: string): string {
  return STATUS_LABEL[String(status || '')] || String(status || '')
}

async function refresh(): Promise<void> {
  loading.value = true
  try {
    const work = await api<{ items?: WorkItem[] }>(`/work-items?conversation_id=${encodeURIComponent(props.conversationId)}&limit=50`)
      .catch(() => ({ items: [] }))
    let taskData: { items?: TeamTask[] } = { items: [] }
    try {
      taskData = await api<{ items?: TeamTask[] }>(
        `/conversations/${encodeURIComponent(props.conversationId)}/team-tasks`)
    } catch (error) {
      // 会话已被删除：恢复出来的旧窗口自动关闭，不留一个空壳。
      if ((error as { status?: number }).status === 404) {
        windows.closeTask(props.id)
        return
      }
    }
    items.value = work.items || []
    tasks.value = taskData.items || []
    // 详情抽屉可能开着：让它的 usage 跟着 5s 轮询一起刷新。
    if (workDetail.value) {
      const fresh = items.value.find((entry) => entry.id === workDetail.value?.id)
      if (fresh) workDetail.value = fresh
    }
  } finally {
    loading.value = false
  }
}

async function continueTask(taskId: string): Promise<void> {  try {
    await api(`/conversations/${encodeURIComponent(props.conversationId)}/team-tasks/${encodeURIComponent(taskId)}/continue`, {
      method: 'POST',
    })
    app.toast('已把任务结果交回对话', 'ok', 3000)
    await refresh()
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  }
}

async function dispatchTask(): Promise<void> {
  const text = goal.value.trim()
  if (!text || dispatching.value) return
  dispatching.value = true
  try {
    await api(`/conversations/${encodeURIComponent(props.conversationId)}/team-tasks`, {
      method: 'POST',
      body: {
        goal: text,
        member_id: members.value.length ? memberId.value : undefined,
      },
    })
    goal.value = ''
    app.toast(`已派发给 ${assigneeName.value}，后台执行中`, 'ok', 3000)
    await refresh()
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    dispatching.value = false
  }
}

function openConversation(): void {
  windows.closeTask(props.id)
  void router.push(`/conv/${props.conversationId}`)
}

function close(): void {
  windows.closeTask(props.id)
}

function focus(): void {
  windows.focusTask(props.id)
}

// ── 拖动 ────────────────────────────────────────────────
let drag: { x: number; y: number; baseX: number; baseY: number } | null = null

function startDrag(event: PointerEvent): void {
  const target = event.target as HTMLElement | null
  if (target?.closest('button, a, input, textarea')) return
  drag = { x: event.clientX, y: event.clientY, baseX: props.x, baseY: props.y }
  window.addEventListener('pointermove', onDrag)
  window.addEventListener('pointerup', endDrag, { once: true })
}
function onDrag(event: PointerEvent): void {
  if (!drag) return
  const maxX = Math.max(8, window.innerWidth - 120)
  const maxY = Math.max(8, window.innerHeight - 80)
  windows.moveTask(props.id,
    Math.min(maxX, Math.max(8, drag.baseX + event.clientX - drag.x)),
    Math.min(maxY, Math.max(8, drag.baseY + event.clientY - drag.y)))
}
function endDrag(): void {
  drag = null
  window.removeEventListener('pointermove', onDrag)
  windows.commitTask(props.id)
}

onMounted(() => {
  void loadMeta()
  void refresh()
  timer = window.setInterval(refresh, 5000)
})
onBeforeUnmount(() => {
  window.clearInterval(timer)
  endDrag()
})
</script>

<template>
  <section
    class="task-window"
    :style="{ left: x + 'px', top: y + 'px', zIndex: 105 }"
    @pointerdown="focus"
  >
    <header class="task-window-head" @pointerdown="startDrag">
      <span class="task-window-mark">▤</span>
      <div class="task-window-copy">
        <b>{{ title }}</b>
        <span>任务 · 工单{{ openCount ? ` · ${openCount} 在办` : '' }}</span>
      </div>
      <div class="task-window-actions">
        <button type="button" title="打开会话" @click="openConversation">↗</button>
        <button type="button" title="关闭" @click="close">×</button>
      </div>
    </header>

    <div class="task-window-body">
      <template v-if="activeTasks.length">
        <div class="tw-head">
          后台任务
          <span>{{ activeTasks.length }}<template v-if="taskTokens"> · {{ compactNumber(taskTokens) }} tokens</template></span>
        </div>
        <div v-for="task in activeTasks" :key="task.id" class="tw-row" :class="task.status">
          <span class="tw-dot" />
          <span class="tw-main">
            <b>{{ task.title || task.id }}</b>
            <small class="tw-meta">
              <span>{{ label(task.status) }}</span>
              <span class="tw-sep">·</span>
              <span>{{ task.done_members || 0 }}/{{ task.total_members || 0 }} 完成</span>
              <TokenMeter v-if="hasUsage(task.usage)" :usage="task.usage" variant="inline" />
            </small>
          </span>
          <button v-if="task.can_continue" class="tw-action" type="button" @click.stop="continueTask(task.id)">继续对话</button>
        </div>
      </template>

      <div class="tw-head">
        工单
        <span>{{ items.length }}<template v-if="workTokens"> · {{ compactNumber(workTokens) }} tokens</template></span>
      </div>
      <button
        v-for="item in items"
        :key="item.id"
        class="tw-row"
        :class="item.status"
        type="button"
        @click="workDetail = item"
      >
        <span class="tw-dot" />
        <span class="tw-main">
          <b>{{ item.title }}</b>
          <small class="tw-meta">
            <span>{{ label(item.status) }}</span>
            <span class="tw-sep">·</span>
            <span>{{ item.role_name || item.team_name || '会话' }}</span>
            <TokenMeter v-if="hasUsage(item.usage)" :usage="item.usage" variant="inline" />
          </small>
        </span>
        <span class="tw-go">›</span>
      </button>

      <div v-if="!items.length && !activeTasks.length" class="tw-empty">
        {{ loading ? '加载中…' : '这个会话还没有任务。派发后台任务会自动建单。' }}
      </div>

      <div class="tw-dispatch">
        <div class="tw-dispatch-head">派发新任务 <span>后台执行 · 完成回触发器</span></div>
        <select v-if="members.length" v-model="memberId" class="tw-select">
          <option v-for="member in members" :key="member.id" :value="member.id">{{ member.name }}</option>
        </select>
        <div v-else class="tw-assignee">执行者：{{ assigneeName }}</div>
        <textarea v-model="goal" class="tw-goal" rows="2" placeholder="要后台完成什么？" />
        <button
          class="tw-dispatch-btn"
          type="button"
          :disabled="!goal.trim() || dispatching || (members.length > 0 && !memberId)"
          @click="dispatchTask"
        >
          {{ dispatching ? '派发中…' : '派发到后台' }}
        </button>
      </div>
    </div>

    <WorkSettingsDrawer
      v-if="workDetail"
      :open="Boolean(workDetail)"
      :item="workDetail"
      @close="workDetail = null"
      @saved="workDetail = null; refresh()"
      @deleted="workDetail = null; refresh()"
    />
  </section>
</template>

<style scoped>
.task-window {
  position: fixed;
  width: min(420px, calc(100vw - 24px));
  max-height: min(70vh, 560px);
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--bg);
  box-shadow: 0 24px 70px rgba(0, 0, 0, .5);
  overflow: hidden;
}
.task-window-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 10px;
  cursor: move;
  user-select: none;
  border-bottom: 1px solid var(--line-soft);
  background: linear-gradient(140deg, color-mix(in srgb, var(--accent) 8%, var(--panel)), var(--bg-2));
}
.task-window-mark {
  width: 26px; height: 26px; flex: none; border-radius: 8px;
  display: grid; place-items: center; font-size: 13px;
  color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, var(--panel));
  border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--line));
}
.task-window-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.task-window-copy b { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-window-copy span { font-size: 10px; color: var(--faint); }
.task-window-actions { display: flex; gap: 4px; flex: none; }
.task-window-actions button {
  width: 24px; height: 24px; border-radius: 7px; cursor: pointer;
  border: 1px solid var(--line); background: var(--panel); color: var(--dim);
  display: grid; place-items: center; font-size: 13px;
}
.task-window-actions button:hover { color: var(--text); border-color: var(--line-strong); }
.task-window-body { flex: 1; min-height: 0; overflow: auto; padding: 10px; display: flex; flex-direction: column; gap: 6px; }
.tw-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 6px 2px 2px; font-size: 10px; letter-spacing: .02em; color: var(--faint); }
.tw-head span { color: var(--dim); font-variant-numeric: tabular-nums; }
.tw-row {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
  border: 1px solid var(--line-soft); border-radius: 10px; background: var(--panel);
  padding: 8px 9px; color: var(--text); cursor: pointer;
  transition: border-color .15s, background .15s;
}
.tw-row:hover { border-color: var(--line-strong); background: var(--panel-2); }
.tw-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--faint); }
.tw-row.running .tw-dot, .tw-row.working .tw-dot { background: var(--accent); box-shadow: 0 0 9px color-mix(in srgb, var(--accent) 70%, transparent); }
.tw-row.needs_attention .tw-dot, .tw-row.failed .tw-dot { background: var(--danger); }
.tw-row.done .tw-dot { background: var(--ok); }
.tw-row.paused .tw-dot { background: var(--warn); }
.tw-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.tw-main b { font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tw-main small { font-size: 9.5px; color: var(--faint); }
.tw-go { color: var(--faint); font-size: 14px; flex: none; }
.tw-action { flex: none; border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--line)); background: color-mix(in srgb, var(--accent) 12%, var(--panel)); color: var(--accent-ink); border-radius: 7px; padding: 3px 7px; font-size: 10px; cursor: pointer; }
.tw-action:hover { background: color-mix(in srgb, var(--accent) 20%, var(--panel)); }
.tw-empty { padding: 16px 8px; text-align: center; color: var(--faint); font-size: 10.5px; }
.tw-dispatch {
  margin-top: 4px; padding-top: 9px; border-top: 1px dashed var(--line-soft);
  display: flex; flex-direction: column; gap: 6px;
}
.tw-dispatch-head { display: flex; align-items: baseline; justify-content: space-between; font-size: 10.5px; color: var(--dim); }
.tw-dispatch-head span { font-size: 9px; color: var(--faint); }
.tw-select, .tw-goal {
  width: 100%; border: 1px solid var(--line); border-radius: 9px;
  background: var(--bg-2); color: var(--text); font: 11px var(--font);
  padding: 6px 8px; outline: none;
}
.tw-select { appearance: none; -webkit-appearance: none; padding-right: 26px;
  background-image: linear-gradient(45deg, transparent 50%, var(--faint) 50%), linear-gradient(135deg, var(--faint) 50%, transparent 50%);
  background-position: calc(100% - 14px) calc(50% + 1px), calc(100% - 9px) calc(50% + 1px);
  background-size: 5px 5px, 5px 5px; background-repeat: no-repeat; cursor: pointer;
}
.tw-goal { resize: vertical; min-height: 46px; max-height: 120px; line-height: 1.5; }
.tw-select:focus, .tw-goal:focus { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 14%, transparent); }
.tw-assignee { font-size: 10.5px; color: var(--faint); padding: 2px 1px; }
.tw-dispatch-btn {
  border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--line));
  background: color-mix(in srgb, var(--accent) 16%, var(--panel));
  color: var(--accent-ink); border-radius: 8px; padding: 6px 10px;
  font-size: 11px; cursor: pointer; transition: background .15s, transform .15s;
}
.tw-dispatch-btn:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 26%, var(--panel)); transform: translateY(-1px); }
.tw-dispatch-btn:disabled { opacity: .5; cursor: not-allowed; }
</style>

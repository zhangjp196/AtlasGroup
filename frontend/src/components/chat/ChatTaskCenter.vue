<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Avatar from '@/components/Avatar.vue'
import TokenMeter from '@/components/TokenMeter.vue'
import WorkSettingsDrawer from '@/components/WorkSettingsDrawer.vue'
import { api } from '@/api/client'
import type { Conversation, Team, WorkItem } from '@/api/types'
import { compactNumber, firstLine } from '@/lib/format'
import { hasUsage } from '@/lib/usage'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'
import AgentCanvas from './AgentCanvas.vue'

const props = defineProps<{ open: boolean; conversationId: string; title?: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const app = useAppStore()
const dataStore = useDataStore()
const tab = ref<'runs' | 'work'>('runs')
const workItems = ref<WorkItem[]>([])
const conv = ref<Conversation | null>(null)
const workLoading = ref(false)
const workDetail = ref<WorkItem | null>(null)
const dispatchGoal = ref('')
const dispatchMember = ref('')
const dispatching = ref(false)
let timer = 0

const team = computed<Team | null>(() => {
  const tid = String(conv.value?.team_id || '')
  return tid ? dataStore.teams.find((item) => item.id === tid) || null : null
})
const members = computed(() => (team.value?.members || []).filter((member) => member.id))
const assigneeName = computed(() => {
  if (members.value.length) {
    const hit = members.value.find((member) => member.id === dispatchMember.value)
    return String(hit?.name || members.value[0]?.name || '成员')
  }
  return String(dataStore.roles.find((role) => role.id === conv.value?.role_id)?.name || '当前角色')
})

const openItems = computed(() => workItems.value.filter(
  (item) => ['inbox', 'working', 'needs_attention'].includes(String(item.status || '')),
))
const attentionItems = computed(() => workItems.value.filter((item) => item.status === 'needs_attention'))
const doneItems = computed(() => workItems.value.filter(
  (item) => ['done', 'closed'].includes(String(item.status || '')),
))
const workTokens = computed(() => workItems.value.reduce(
  (sum, item) => sum + Number(item.usage?.total_tokens || item.usage?.total || 0), 0,
))

const STATUS_LABEL: Record<string, string> = {
  inbox: '待分派', working: '进行中', needs_attention: '需要你',
  done: '已完成', closed: '已关闭',
}

function statusLabel(status?: string): string {
  return STATUS_LABEL[String(status || '')] || String(status || '待处理')
}

async function loadMeta(): Promise<void> {
  const known = dataStore.conversations.find((item) => item.id === props.conversationId)
  if (known) conv.value = known
  try {
    conv.value = await api<Conversation>(`/conversations/${encodeURIComponent(props.conversationId)}`)
  } catch {
    /* 保留列表里的快照 */
  }
  if (!members.value.length) dispatchMember.value = ''
  else if (!members.value.some((member) => member.id === dispatchMember.value)) {
    dispatchMember.value = String(members.value[0].id || '')
  }
}

async function refreshWork(): Promise<void> {
  if (!props.conversationId) return
  workLoading.value = !workItems.value.length
  try {
    const out = await api<{ items?: WorkItem[] }>(
      `/work-items?conversation_id=${encodeURIComponent(props.conversationId)}&limit=80`,
    )
    workItems.value = out.items || []
    if (workDetail.value) {
      const fresh = workItems.value.find((item) => item.id === workDetail.value?.id)
      if (fresh) workDetail.value = fresh
    }
  } catch {
    workItems.value = []
  } finally {
    workLoading.value = false
  }
}

async function refresh(): Promise<void> {
  await Promise.all([loadMeta(), refreshWork()])
}

async function dispatchTask(): Promise<void> {
  const goal = dispatchGoal.value.trim()
  if (!goal || dispatching.value) return
  dispatching.value = true
  try {
    await api(`/conversations/${encodeURIComponent(props.conversationId)}/team-tasks`, {
      method: 'POST',
      body: {
        goal,
        member_id: members.value.length ? dispatchMember.value : undefined,
      },
    })
    dispatchGoal.value = ''
    app.toast(`已派发给 ${assigneeName.value}，后台执行中`, 'ok', 3000)
    await refreshWork()
    emit('changed')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    dispatching.value = false
  }
}

function openWork(item: WorkItem): void {
  workDetail.value = item
}

function close(): void {
  emit('close')
}

watch(() => [props.open, props.conversationId], ([open]) => {
  if (open) {
    tab.value = 'runs'
    void refresh()
    if (timer) window.clearInterval(timer)
    timer = window.setInterval(() => {
      if (props.open) void refreshWork()
    }, 5000)
  } else {
    window.clearInterval(timer)
    timer = 0
    workDetail.value = null
  }
}, { immediate: true })

onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div v-if="open" class="task-center">
    <header class="tc-head">
      <span class="tc-mark">▦</span>
      <div class="tc-title">
        <b>任务中心</b>
        <span>{{ title || '当前会话' }} · 执行与工单统一查看</span>
      </div>
      <div class="tc-head-actions">
        <span v-if="attentionItems.length" class="tc-chip danger">{{ attentionItems.length }} 需要你</span>
        <span v-if="openItems.length" class="tc-chip">{{ openItems.length }} 在办</span>
        <button class="iconbtn" type="button" title="关闭任务中心" aria-label="关闭任务中心" @click="close">×</button>
      </div>
    </header>

    <div class="tc-tabs" role="tablist" aria-label="任务中心切换">
      <button class="tc-tab" :class="{ on: tab === 'runs' }" type="button" role="tab"
        :aria-selected="tab === 'runs'" @click="tab = 'runs'">
        <span class="tc-tab-dot run" />执行
      </button>
      <button class="tc-tab" :class="{ on: tab === 'work' }" type="button" role="tab"
        :aria-selected="tab === 'work'" @click="tab = 'work'">
        <span class="tc-tab-dot work" />工单
        <span v-if="openItems.length" class="tc-tab-count">{{ openItems.length }}</span>
      </button>
    </div>

    <div class="tc-body">
      <AgentCanvas
        v-if="tab === 'runs'"
        :open="true"
        embedded
        :conversation-id="conversationId"
        @close="tab = 'work'"
      />

      <div v-else class="tc-work">
        <div class="tc-work-stats">
          <div class="tc-stat"><b>{{ openItems.length }}</b><span>在办</span></div>
          <div class="tc-stat" :class="{ danger: attentionItems.length }"><b>{{ attentionItems.length }}</b><span>需要你</span></div>
          <div class="tc-stat"><b>{{ doneItems.length }}</b><span>已完成</span></div>
          <div class="tc-stat"><b>{{ workTokens ? compactNumber(workTokens) : '—' }}</b><span>tokens</span></div>
        </div>

        <div class="tc-work-list">
          <button v-for="item in workItems" :key="item.id" class="tc-work-row" :class="item.status"
            type="button" @click="openWork(item)">
            <span class="tc-work-dot" />
            <span class="tc-work-main">
              <span class="tc-work-title">
                <b>{{ item.title || item.id }}</b>
                <span class="tc-work-status">{{ statusLabel(item.status) }}</span>
              </span>
              <span class="tc-work-sub">
                {{ item.role_name || item.team_name || '当前会话' }}
                <template v-if="item.attention_reason"> · {{ firstLine(item.attention_reason, 70) }}</template>
                <template v-else-if="item.result_summary"> · {{ firstLine(item.result_summary, 70) }}</template>
              </span>
            </span>
            <span class="tc-work-side">
              <TokenMeter v-if="hasUsage(item.usage)" :usage="item.usage" variant="inline" />
              <span class="tc-work-go">›</span>
            </span>
          </button>
          <div v-if="workLoading" class="tc-empty">加载工单…</div>
          <div v-else-if="!workItems.length" class="tc-empty">
            这个会话还没有工单。派发后台任务会自动建单。
          </div>
        </div>

        <div class="tc-dispatch">
          <div class="tc-dispatch-head">
            <b>派发后台任务</b>
            <span>完成后自动回会话</span>
          </div>
          <div class="tc-dispatch-line">
            <select v-if="members.length" v-model="dispatchMember" class="input tc-select">
              <option v-for="member in members" :key="member.id" :value="member.id">
                {{ member.name || member.id }}{{ member.title ? ` · ${member.title}` : '' }}
              </option>
            </select>
            <span v-else class="tc-assignee">
              <Avatar :name="assigneeName" :avatar="dataStore.roles.find((role) => role.id === conv?.role_id)?.avatar" :size="20" round />
              {{ assigneeName }}
            </span>
            <button class="btn primary small" type="button" :disabled="!dispatchGoal.trim() || dispatching" @click="dispatchTask">
              {{ dispatching ? '派发中…' : '派发' }}
            </button>
          </div>
          <textarea v-model="dispatchGoal" class="input tc-goal" rows="2" placeholder="要后台完成什么？" />
        </div>
      </div>
    </div>

    <WorkSettingsDrawer
      v-if="workDetail"
      :open="Boolean(workDetail)"
      :item="workDetail"
      @close="workDetail = null"
      @saved="workDetail = null; refreshWork(); emit('changed')"
      @deleted="workDetail = null; refreshWork(); emit('changed')"
    />
  </div>
</template>

<style scoped>
.task-center { position: absolute; inset: 0; z-index: 18; display: flex; flex-direction: column; background: var(--bg); border-radius: inherit; overflow: hidden; animation: tcIn .18s ease; }
@keyframes tcIn { from { opacity: 0; transform: translateY(6px) scale(.995); } to { opacity: 1; transform: none; } }
.tc-head { flex: none; display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--line-soft); background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 7%, var(--panel)), var(--bg-2)); }
.tc-mark { width: 30px; height: 30px; flex: none; display: grid; place-items: center; border-radius: 9px; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, var(--panel)); border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--line)); font-size: 15px; }
.tc-title { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.tc-title b { font-size: 13px; }
.tc-title span { font-size: 10px; color: var(--faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tc-head-actions { display: flex; align-items: center; gap: 5px; flex: none; }
.tc-chip { font-size: 10px; padding: 2px 7px; border-radius: 999px; color: var(--dim); background: var(--bg-2); border: 1px solid var(--line-soft); white-space: nowrap; }
.tc-chip.danger { color: var(--danger); background: color-mix(in srgb, var(--danger) 10%, var(--panel)); border-color: color-mix(in srgb, var(--danger) 26%, var(--line)); }
.tc-tabs { flex: none; display: flex; gap: 4px; padding: 6px 10px; border-bottom: 1px solid var(--line-soft); background: var(--bg-2); }
.tc-tab { flex: 1; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--faint); padding: 6px 8px; font-size: 11.5px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px; }
.tc-tab:hover { color: var(--text); background: var(--panel); }
.tc-tab.on { color: var(--text); background: var(--panel); border-color: var(--line); box-shadow: 0 1px 5px color-mix(in srgb, #000 10%, transparent); }
.tc-tab-dot { width: 6px; height: 6px; border-radius: 50%; }
.tc-tab-dot.run { background: var(--accent); }
.tc-tab-dot.work { background: var(--ok); }
.tc-tab-count { min-width: 15px; height: 15px; padding: 0 4px; border-radius: 8px; background: var(--bg-2); color: var(--dim); font-size: 9px; line-height: 15px; }
.tc-body { flex: 1; min-height: 0; display: flex; }
.tc-work { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.tc-work-stats { flex: none; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; background: var(--line-soft); border-bottom: 1px solid var(--line-soft); }
.tc-stat { padding: 8px 10px; background: var(--bg); display: flex; flex-direction: column; gap: 1px; }
.tc-stat b { font-size: 14px; font-variant-numeric: tabular-nums; }
.tc-stat span { font-size: 9.5px; color: var(--faint); }
.tc-stat.danger b { color: var(--danger); }
.tc-work-list { flex: 1; min-height: 0; overflow: auto; padding: 8px 10px; display: flex; flex-direction: column; gap: 5px; }
.tc-work-row { display: flex; align-items: center; gap: 9px; width: 100%; padding: 8px 9px; border: 1px solid var(--line-soft); border-radius: 10px; background: var(--panel); color: var(--text); text-align: left; cursor: pointer; transition: border-color .15s, background .15s, transform .15s; }
.tc-work-row:hover { border-color: var(--line-strong); background: var(--panel-2); transform: translateY(-1px); }
.tc-work-row.needs_attention { border-color: color-mix(in srgb, var(--danger) 35%, var(--line)); }
.tc-work-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--faint); }
.tc-work-row.working .tc-work-dot { background: var(--accent); box-shadow: 0 0 8px color-mix(in srgb, var(--accent) 65%, transparent); }
.tc-work-row.needs_attention .tc-work-dot { background: var(--danger); }
.tc-work-row.done .tc-work-dot { background: var(--ok); }
.tc-work-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.tc-work-title { display: flex; align-items: center; gap: 7px; min-width: 0; }
.tc-work-title b { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tc-work-status { flex: none; font-size: 9px; color: var(--faint); border: 1px solid var(--line-soft); border-radius: 999px; padding: 1px 5px; }
.tc-work-sub { font-size: 10px; color: var(--faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tc-work-side { flex: none; display: flex; align-items: center; gap: 7px; }
.tc-work-go { color: var(--faint); font-size: 14px; }
.tc-empty { padding: 32px 16px; text-align: center; color: var(--faint); font-size: 11.5px; line-height: 1.7; }
.tc-dispatch { flex: none; padding: 9px 10px 10px; border-top: 1px solid var(--line-soft); background: var(--bg-2); display: flex; flex-direction: column; gap: 6px; }
.tc-dispatch-head { display: flex; align-items: baseline; justify-content: space-between; font-size: 10.5px; color: var(--dim); }
.tc-dispatch-head span { color: var(--faint); font-size: 9.5px; }
.tc-dispatch-line { display: flex; align-items: center; gap: 7px; }
.tc-select { flex: 1; min-width: 0; height: 28px; padding: 4px 8px; font-size: 11px; }
.tc-assignee { flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; color: var(--dim); font-size: 11px; }
.tc-goal { resize: vertical; min-height: 46px; max-height: 100px; font-size: 11px; }
</style>

<script setup lang="ts">
import { ElButton, ElOption, ElSelect } from 'element-plus'
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import OptionPicker from '@/components/OptionPicker.vue'
import WorkspacePicker from '@/components/WorkspacePicker.vue'
import { api } from '@/api/client'
import type { ScheduledTask } from '@/api/types'
import { shortTime } from '@/lib/format'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const app = useAppStore()
const router = useRouter()

const drawerOpen = ref(false)
const editing = ref<ScheduledTask | null>(null)
const saving = ref(false)
const busyId = ref('')
const filter = ref<'all' | 'active' | 'smart' | 'paused'>('all')

const emptyForm = () => ({
  title: '',
  prompt: '',
  trigger_mode: 'scheduled',
  schedule_kind: 'daily',
  schedule_time: '09:00',
  schedule_weekday: 0,
  schedule_minutes: 60,
  schedule_at: '',
  condition: '',
  eval_minutes: 30,
  role_id: '',
  workspace_id: '',
  run_mode: 'chat',
  auto_execute: false,
  max_runs: 0,
})
const form = reactive(emptyForm())

const roleOptions = computed(() => data.persistentRoles.map((role) => ({
  value: role.id,
  label: role.name || role.id,
  description: [role.title, role.expertise].filter(Boolean).join(' · '),
})))

const tasks = computed(() => data.schedules.filter((task) => {
  if (filter.value === 'all') return true
  if (filter.value === 'smart') return task.trigger_mode === 'smart'
  return task.status === filter.value
}))

const stats = computed(() => data.scheduleStats || {})

const triggerLabels: Record<string, string> = {
  scheduled: '定时',
  smart: '自由触发',
  manual: '手动',
}
const kindLabels: Record<string, string> = {
  once: '一次',
  interval: '间隔',
  daily: '每天',
  weekly: '每周',
  smart: '模型判断',
}
const statusLabels: Record<string, string> = {
  active: '已启用',
  paused: '已暂停',
  done: '已结束',
  running: '执行中',
  queued: '排队中',
  failed: '失败',
  stopped: '已停止',
}
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

function triggerText(task: ScheduledTask): string {
  const schedule = task.schedule || {}
  if (task.trigger_mode === 'smart' || task.schedule_kind === 'smart') {
    return `每 ${schedule.every_minutes || 30} 分钟评估一次`
  }
  if (task.trigger_mode === 'manual') return '仅手动运行'
  if (task.schedule_kind === 'once') return `一次 · ${schedule.at || '未设置'}`
  if (task.schedule_kind === 'interval') return `每 ${schedule.every_minutes || 60} 分钟`
  if (task.schedule_kind === 'daily') return `每天 ${schedule.time || '09:00'}`
  if (task.schedule_kind === 'weekly') {
    return `每${weekdays[Number(schedule.weekday || 0)] || '周一'} ${schedule.time || '09:00'}`
  }
  return task.schedule_kind || ''
}

function roleName(task: ScheduledTask): string {
  return data.roles.find((role) => role.id === task.role_id)?.name || '未指定角色'
}

function workspaceName(task: ScheduledTask): string {
  return data.workspaces.find((ws) => ws.id === task.workspace_id)?.name || '默认工作区'
}

function openCreate(): void {
  editing.value = null
  Object.assign(form, emptyForm())
  drawerOpen.value = true
}

function openEdit(task: ScheduledTask): void {
  editing.value = task
  const schedule = task.schedule || {}
  Object.assign(form, emptyForm(), {
    title: task.title || '',
    prompt: task.prompt || '',
    trigger_mode: task.trigger_mode || 'scheduled',
    schedule_kind: task.schedule_kind || 'daily',
    schedule_time: String(schedule.time || '09:00'),
    schedule_weekday: Number(schedule.weekday || 0),
    schedule_minutes: Number(schedule.every_minutes || 60),
    schedule_at: String(schedule.at || '').replace(' ', 'T').slice(0, 16),
    condition: task.condition || '',
    eval_minutes: Number(schedule.every_minutes || 30),
    role_id: task.role_id || '',
    workspace_id: task.workspace_id || '',
    run_mode: task.run_mode || 'chat',
    auto_execute: Boolean(task.auto_execute),
    max_runs: Number(task.max_runs || 0),
  })
  drawerOpen.value = true
}

function schedulePayload(): Record<string, unknown> {
  if (form.trigger_mode === 'smart') return { every_minutes: Number(form.eval_minutes) || 30 }
  if (form.schedule_kind === 'once') {
    return { at: String(form.schedule_at || '').replace('T', ' ') }
  }
  if (form.schedule_kind === 'interval') {
    return { every_minutes: Number(form.schedule_minutes) || 60 }
  }
  if (form.schedule_kind === 'daily') return { time: form.schedule_time || '09:00' }
  if (form.schedule_kind === 'weekly') {
    return { time: form.schedule_time || '09:00', weekday: Number(form.schedule_weekday) }
  }
  return {}
}

async function save(): Promise<void> {
  if (saving.value) return
  if (!form.title.trim() || !form.prompt.trim()) {
    app.toast('标题和任务内容不能为空', 'bad')
    return
  }
  if (!form.role_id) {
    app.toast('先选择一个执行角色', 'bad')
    return
  }
  saving.value = true
  try {
    const body = {
      title: form.title.trim(),
      prompt: form.prompt.trim(),
      trigger_mode: form.trigger_mode,
      schedule_kind: form.trigger_mode === 'smart' ? 'smart' : form.schedule_kind,
      schedule: schedulePayload(),
      condition: form.condition.trim(),
      role_id: form.role_id || undefined,
      workspace_id: form.workspace_id || undefined,
      run_mode: form.run_mode,
      auto_execute: form.auto_execute,
      max_runs: Number(form.max_runs) || 0,
    }
    if (editing.value) {
      await api(`/schedules/${encodeURIComponent(editing.value.id)}`, { method: 'PATCH', body })
    } else {
      await api('/schedules', { method: 'POST', body })
    }
    await data.loadSchedules()
    drawerOpen.value = false
    app.toast(editing.value ? '任务已更新' : '任务已创建')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    saving.value = false
  }
}

async function runNow(task: ScheduledTask): Promise<void> {
  busyId.value = task.id
  try {
    const updated = await api<ScheduledTask>(`/schedules/${encodeURIComponent(task.id)}/run`, { method: 'POST' })
    await data.loadSchedules()
    app.toast('任务已开始执行')
    const convId = updated?.last_conversation_id || updated?.conversation_id
      || task.last_conversation_id || task.conversation_id
    if (convId) void router.push(`/conv/${encodeURIComponent(convId)}`)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    busyId.value = ''
  }
}

async function togglePause(task: ScheduledTask): Promise<void> {
  busyId.value = task.id
  try {
    await api(`/schedules/${encodeURIComponent(task.id)}/pause`, {
      method: 'POST',
      body: { paused: task.status !== 'paused' },
    })
    await data.loadSchedules()
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    busyId.value = ''
  }
}

async function remove(task: ScheduledTask): Promise<void> {
  if (!window.confirm(`删除任务「${task.title}」？`)) return
  busyId.value = task.id
  try {
    await api(`/schedules/${encodeURIComponent(task.id)}`, { method: 'DELETE' })
    await data.loadSchedules()
    app.toast('任务已删除')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad', 5000)
  } finally {
    busyId.value = ''
  }
}

function openConversation(task: ScheduledTask): void {
  const id = task.last_conversation_id || task.conversation_id
  if (id) void router.push(`/conv/${encodeURIComponent(id)}`)
}

void data.loadSchedules()
</script>

<template>
  <PageHeader section="定时" title="定时 / 主动任务" :chip="`${stats.active || 0} 个已启用 · ${stats.smart || 0} 个自由触发`">
    <el-button type="primary" @click="openCreate">新建任务</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="sched-filters">
      <div class="seg" role="group" aria-label="任务筛选">
        <button class="seg-btn" :class="{ on: filter === 'all' }" type="button" @click="filter = 'all'">全部</button>
        <button class="seg-btn" :class="{ on: filter === 'active' }" type="button" @click="filter = 'active'">已启用</button>
        <button class="seg-btn" :class="{ on: filter === 'smart' }" type="button" @click="filter = 'smart'">自由触发</button>
        <button class="seg-btn" :class="{ on: filter === 'paused' }" type="button" @click="filter = 'paused'">已暂停</button>
      </div>
      <span class="sched-count">{{ tasks.length }} 个任务</span>
    </div>

    <div class="tile-grid">
      <article v-for="task in tasks" :key="task.id" class="tile-card sched-card">
        <div class="tile-head">
          <span class="sched-icon" :class="{ smart: task.trigger_mode === 'smart' }">◷</span>
          <div class="tile-title">
            <b>{{ task.title }}</b>
            <span>{{ triggerLabels[task.trigger_mode] || task.trigger_mode }} · {{ triggerText(task) }}</span>
          </div>
          <span class="sched-status" :class="task.status">{{ statusLabels[task.status] || task.status }}</span>
        </div>
        <p class="tile-preview">{{ task.prompt }}</p>
        <p v-if="task.trigger_mode === 'smart' && task.condition" class="sched-condition">
          触发条件：{{ task.condition }}
        </p>
        <div class="tile-chips">
          <span class="chip">{{ roleName(task) }}</span>
          <span class="chip">{{ workspaceName(task) }}</span>
          <span class="chip">{{ task.run_mode === 'autonomous' ? '自主执行' : '对话' }}</span>
          <span v-if="task.max_runs" class="chip">{{ task.run_count || 0 }} / {{ task.max_runs }} 次</span>
        </div>
        <div class="sched-meta">
          <span>下次：{{ task.next_run_at ? shortTime(task.next_run_at) : '—' }}</span>
          <span>上次：{{ task.last_run_at ? shortTime(task.last_run_at) : '—' }}
            <template v-if="task.last_status"> · {{ statusLabels[task.last_status] || task.last_status }}</template>
          </span>
        </div>
        <div class="sched-actions">
          <button class="btn small primary" type="button" :disabled="busyId === task.id" @click="runNow(task)">立即运行</button>
          <button class="btn small" type="button" :disabled="busyId === task.id || task.status === 'done'" @click="togglePause(task)">
            {{ task.status === 'paused' ? '恢复' : '暂停' }}
          </button>
          <button class="btn small" type="button" @click="openEdit(task)">编辑</button>
          <button v-if="task.last_conversation_id || task.conversation_id" class="btn small" type="button" @click="openConversation(task)">打开会话</button>
          <button class="btn small danger" type="button" :disabled="busyId === task.id" @click="remove(task)">删除</button>
        </div>
      </article>
    </div>

    <div v-if="!data.schedules.length" class="empty">
      还没有定时任务<br />
      可以创建每天固定执行的任务，也可以让模型在合适时机自由触发。
    </div>
    <div v-else-if="!tasks.length" class="empty">没有符合筛选条件的任务</div>
  </div>

  <SideDrawer :open="drawerOpen" :title="editing ? '编辑任务' : '新建任务'" subtitle="定时执行，或由模型判断时机主动触发" width="640px" @close="drawerOpen = false">
    <div class="field">
      <label>任务标题</label>
      <input v-model="form.title" class="input" maxlength="200" placeholder="例如：每日巡检" />
    </div>
    <div class="field">
      <label>任务内容</label>
      <textarea v-model="form.prompt" class="input" rows="5" placeholder="每次触发时发给角色的完整指令" />
    </div>
    <div class="field">
      <label>触发方式</label>
      <div class="seg" role="group" aria-label="触发方式">
        <button class="seg-btn" :class="{ on: form.trigger_mode === 'scheduled' }" type="button" @click="form.trigger_mode = 'scheduled'">定时</button>
        <button class="seg-btn" :class="{ on: form.trigger_mode === 'smart' }" type="button" @click="form.trigger_mode = 'smart'">自由触发</button>
        <button class="seg-btn" :class="{ on: form.trigger_mode === 'manual' }" type="button" @click="form.trigger_mode = 'manual'">仅手动</button>
      </div>
      <small class="role-line">
        自由触发不是固定 cron：到达评估时间后，由模型结合触发条件和上次结果判断现在是否值得执行。
      </small>
    </div>

    <template v-if="form.trigger_mode === 'scheduled'">
      <div class="field">
        <label>时间规则</label>
        <el-select v-model="form.schedule_kind" style="width:100%">
          <el-option label="一次" value="once" />
          <el-option label="按间隔" value="interval" />
          <el-option label="每天" value="daily" />
          <el-option label="每周" value="weekly" />
        </el-select>
      </div>
      <div v-if="form.schedule_kind === 'once'" class="field">
        <label>执行时间</label>
        <input v-model="form.schedule_at" class="input" type="datetime-local" />
      </div>
      <div v-else-if="form.schedule_kind === 'interval'" class="field">
        <label>间隔（分钟）</label>
        <input v-model.number="form.schedule_minutes" class="input" type="number" min="1" max="10080" />
      </div>
      <div v-else class="sched-row">
        <div v-if="form.schedule_kind === 'weekly'" class="field">
          <label>星期</label>
          <el-select v-model="form.schedule_weekday" style="width:140px">
            <el-option v-for="(label, index) in weekdays" :key="label" :label="label" :value="index" />
          </el-select>
        </div>
        <div class="field">
          <label>时间</label>
          <input v-model="form.schedule_time" class="input" type="time" />
        </div>
      </div>
    </template>

    <template v-else-if="form.trigger_mode === 'smart'">
      <div class="field">
        <label>触发条件</label>
        <textarea v-model="form.condition" class="input" rows="3" placeholder="例如：如果今天新增了错误日志，或上次结论仍待验证，就主动推进" />
      </div>
      <div class="field">
        <label>评估间隔（分钟）</label>
        <input v-model.number="form.eval_minutes" class="input" type="number" min="5" max="10080" />
        <small class="role-line">最短 5 分钟；模型判断不触发时会继续等待下一次评估。</small>
      </div>
    </template>

    <div class="sched-row">
      <div class="field">
        <label>执行角色</label>
        <OptionPicker v-model="form.role_id" :items="roleOptions" title="选择执行角色" placeholder="选择执行角色" />
      </div>
      <div class="field">
        <label>执行工作区</label>
        <WorkspacePicker v-model="form.workspace_id" :workspaces="data.workspaces" empty-label="默认工作区" placeholder="默认工作区" />
      </div>
    </div>

    <div class="sched-row">
      <div class="field">
        <label>会话模式</label>
        <el-select v-model="form.run_mode" style="width:160px">
          <el-option label="普通对话" value="chat" />
          <el-option label="自主执行" value="autonomous" />
        </el-select>
      </div>
      <div class="field">
        <label>最多运行次数（0=不限）</label>
        <input v-model.number="form.max_runs" class="input" type="number" min="0" max="9999" />
      </div>
    </div>
    <div class="field">
      <label class="conv-follow">
        <input v-model="form.auto_execute" type="checkbox" />
        自主执行时跳过工具确认（更自动，但风险更高）
      </label>
    </div>

    <template #footer>
      <button class="btn" type="button" @click="drawerOpen = false">取消</button>
      <button class="btn primary" type="button" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
    </template>
  </SideDrawer>
</template>

<style scoped>
.sched-filters { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.sched-count { color: var(--dim); font-size: 12px; margin-left: auto; }
.sched-card { cursor: default; }
.sched-icon { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; background: var(--bg-2); color: var(--dim); font-size: 17px; flex: none; }
.sched-icon.smart { color: #d97706; background: #d9770614; }
.sched-status { margin-left: auto; padding: 2px 8px; border-radius: 999px; font-size: 11px; color: var(--dim); background: var(--bg-2); }
.sched-status.active { color: #15803d; background: #15803d14; }
.sched-status.paused { color: #b45309; background: #b4530914; }
.sched-status.done { color: var(--faint); }
.sched-condition { margin: 6px 0 0; padding: 7px 9px; border-radius: 8px; background: var(--bg-2); color: var(--dim); font-size: 12px; }
.sched-meta { display: flex; justify-content: space-between; gap: 10px; color: var(--faint); font-size: 11px; margin-top: 8px; }
.sched-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.sched-row { display: flex; gap: 12px; flex-wrap: wrap; }
.sched-row > .field { flex: 1 1 220px; }
</style>

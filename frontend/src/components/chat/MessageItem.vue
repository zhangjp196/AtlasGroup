<script setup lang="ts">
import { computed } from 'vue'
import Avatar from '@/components/Avatar.vue'
import type { Message } from '@/api/types'
import { compactNumber, durationText } from '@/lib/format'
import { usageSummary } from '@/lib/usage'
import { entriesFromMessage, splitEntries } from '@/lib/trace'
import { useDataStore } from '@/stores/data'
import Attachments from './Attachments.vue'
import ProcessList from './ProcessList.vue'

const props = defineProps<{ message: Message }>()
const emit = defineEmits<{
  (e: 'continue-task', taskId: string): void
  (e: 'fork', message: Message): void
}>()
const data = useDataStore()

const entries = computed(() => entriesFromMessage(props.message))
const parts = computed(() => splitEntries(entries.value))
const by = computed(() => String(props.message.display_by || props.message.by || ''))
const isSystem = computed(() => by.value === 'system')
// 历史用户消息的 by 是 local / user 两种写法；只有 assistant 是模型侧。
const isUser = computed(() => by.value !== 'assistant' && by.value !== 'system')
const isClear = computed(() => props.message.kind === 'clear_context')
// 头像以角色当前值为准，消息里的 role_avatar 只是回复那一刻的快照。
const avatarSrc = computed(() => data.roleAvatar(
  props.message.role_id, props.message.role_avatar || ''))
const taskStatus = computed(() => {
  const labels: Record<string, string> = {
    done: '已完成', failed: '失败', stopped: '已结束', paused: '已暂停',
  }
  return labels[String(props.message.task_status || '')] || '已结束'
})
const taskSummary = computed(() => {
  const text = String(props.message.summary || '').trim()
  return text.length > 260 ? `${text.slice(0, 260)}…` : text
})
const meta = computed(() => {
  const out: string[] = []
  const ms = durationText(props.message.duration_ms)
  if (ms) out.push(ms)
  if (props.message.interrupted) out.push('已停止')
  if (props.message.error) out.push('失败')
  return out.join(' · ')
})
// 单条回复的 token：比会话 / 任务汇总更细，方便回看哪一轮吃掉了上下文。
const turnUsage = computed(() => {
  const view = usageSummary(props.message.usage)
  return {
    view,
    visible: view.input > 0 || view.output > 0,
    title: [
      `输入 ${view.input.toLocaleString('en-US')} tokens`,
      `输出 ${view.output.toLocaleString('en-US')} tokens`,
      view.speed ? `生成速度 ${view.speed} t/s` : '',
      view.input ? `缓存命中 ${view.cacheRate}%` : '',
    ].filter(Boolean).join('\n'),
  }
})
</script>

<template>
  <div v-if="isClear" class="ctx-clear">
    <span class="ctx-clear-line" />
    <span class="ctx-clear-copy">
      <b>上下文已清空</b>
      <span>此前的消息仍保留在历史中，模型不会再读取它们</span>
    </span>
    <span class="ctx-clear-line" />
  </div>

  <div v-else-if="message.kind === 'task_done'" class="task-done-card">
    <div class="task-done-head">
      <span class="task-done-ico" :class="{ bad: message.task_status === 'failed' }">✓</span>
      <div class="task-done-main">
        <b>后台任务{{ taskStatus }}</b>
        <span>{{ message.task_title || message.content }}</span>
      </div>
    </div>
    <p v-if="taskSummary" class="task-done-summary">{{ taskSummary }}</p>
    <div class="task-done-foot">
      <span v-if="message.task_continued" class="task-done-continued">结果已交回对话，角色接着处理。</span>
      <span v-else>任务在后台完成，结果还没有进入对话。</span>
      <button v-if="!message.task_continued" class="btn primary" @click="emit('continue-task', message.task_id || '')">继续对话</button>
    </div>
  </div>

  <div v-else-if="isSystem" class="sys">{{ message.content }}</div>

  <div v-else-if="isUser" class="msg user">
    <div class="msg-body">
      <div v-if="message.content" class="bubble">{{ message.content }}</div>
      <Attachments :items="message.attachments" />
      <div v-if="message.id" class="msg-ops">
        <button class="msg-op" type="button" title="复制到此消息为止，另起一条分支会话" @click="emit('fork', message)">
          从此处分支
        </button>
      </div>
    </div>
  </div>

  <div v-else class="msg assistant">
    <Avatar :name="message.role_name || '助手'" :avatar="avatarSrc" round />
    <div class="msg-body">
      <div class="msg-who">
        <b>{{ message.role_name || '助手' }}</b>
        <span v-if="message.role_title || message.role_department" class="msg-role">
          {{ [message.role_title, message.role_department].filter(Boolean).join(' · ') }}
        </span>
        <span v-if="meta" class="msg-meta">{{ meta }}</span>
        <span
          v-if="turnUsage.visible"
          class="msg-tokens"
          :title="turnUsage.title"
        >↑{{ compactNumber(turnUsage.view.input) }} ↓{{ compactNumber(turnUsage.view.output) }}</span>
      </div>

      <ProcessList v-if="entries.length" :entries="entries" :final="parts.final" />

      <Attachments :items="message.attachments" />
      <div v-if="message.error" class="run-error">{{ message.error }}</div>
      <div v-if="message.id" class="msg-ops">
        <button class="msg-op" type="button" title="复制到此消息为止，另起一条分支会话" @click="emit('fork', message)">
          从此处分支
        </button>
      </div>
    </div>
  </div>
</template>

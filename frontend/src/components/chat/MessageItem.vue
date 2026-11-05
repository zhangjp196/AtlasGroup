<script setup lang="ts">
import { computed } from 'vue'
import Avatar from '@/components/Avatar.vue'
import type { Message } from '@/api/types'
import { durationText } from '@/lib/format'
import { entriesFromMessage, splitEntries } from '@/lib/trace'
import Attachments from './Attachments.vue'
import Markdown from './Markdown.vue'
import ProcessList from './ProcessList.vue'

const props = defineProps<{ message: Message }>()

const entries = computed(() => entriesFromMessage(props.message))
const parts = computed(() => splitEntries(entries.value))
const isSystem = computed(() => props.message.by === 'system')
const isUser = computed(() => props.message.by === 'user')
const isClear = computed(() => props.message.kind === 'clear_context')
const meta = computed(() => {
  const out: string[] = []
  const ms = durationText(props.message.duration_ms)
  if (ms) out.push(ms)
  if (props.message.interrupted) out.push('已停止')
  if (props.message.error) out.push('失败')
  return out.join(' · ')
})
const processMeta = computed(() => {
  const thoughts = parts.value.process.filter((entry) => entry.kind === 'thinking').length
  const tools = parts.value.process.filter((entry) => entry.kind === 'tool').length
  const out: string[] = []
  if (thoughts) out.push(`思考 ${thoughts}`)
  if (tools) out.push(`工具 ${tools}`)
  return out.join(' · ')
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

  <div v-else-if="isSystem" class="sys">{{ message.content }}</div>

  <div v-else-if="isUser" class="msg user">
    <Avatar name="我" round />
    <div class="msg-body">
      <div class="msg-who"><b>我</b></div>
      <div v-if="message.content" class="bubble">{{ message.content }}</div>
      <Attachments :items="message.attachments" />
    </div>
  </div>

  <div v-else class="msg assistant">
    <Avatar :name="message.role_name || 'AtlasX'" :avatar="message.role_avatar" round />
    <div class="msg-body">
      <div class="msg-who">
        <b>{{ message.role_name || 'AtlasX' }}</b>
        <span v-if="message.role_title || message.role_department" class="msg-role">
          {{ [message.role_title, message.role_department].filter(Boolean).join(' · ') }}
        </span>
        <span v-if="meta" class="msg-meta">{{ meta }}</span>
      </div>

      <details v-if="parts.process.length" class="turn-fold history-fold">
        <summary class="turn-fold-bar">
          <span class="fold-caret">›</span>
          <span class="turn-fold-title">过程</span>
          <span class="turn-fold-meta">{{ processMeta }}</span>
        </summary>
        <div class="turn-fold-body">
          <ProcessList :entries="parts.process" />
        </div>
      </details>

      <div v-if="parts.final" class="bubble assistant-text">
        <Markdown :text="parts.final.text" />
      </div>

      <Attachments :items="message.attachments" />
      <div v-if="message.error" class="run-error">{{ message.error }}</div>
    </div>
  </div>
</template>

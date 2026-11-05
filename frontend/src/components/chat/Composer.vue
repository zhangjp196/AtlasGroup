<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { ModelProfile, SlashCommand, TeamMember } from '@/api/types'
import type { PendingAttachment } from '@/composables/useChat'
import { bytes } from '@/lib/format'

const props = defineProps<{
  streaming: boolean
  sending: boolean
  models: ModelProfile[]
  model: string
  perm: string
  thinking: string
  members: TeamMember[]
  attachments: PendingAttachment[]
  commands: SlashCommand[]
}>()

const emit = defineEmits<{
  (e: 'send'): void
  (e: 'stop'): void
  (e: 'files', files: File[]): void
  (e: 'remove-attachment', index: number): void
  (e: 'update:model', value: string): void
  (e: 'update:perm', value: string): void
  (e: 'update:thinking', value: string): void
}>()

interface PopupItem {
  name: string
  label: string
  description: string
}

interface Popup {
  kind: 'mention' | 'slash'
  items: PopupItem[]
  index: number
  start: number
  end: number
}

const draft = defineModel<string>('draft', { default: '' })
const textarea = ref<HTMLTextAreaElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const composing = ref(false)
const dragOver = ref(false)
const popup = ref<Popup | null>(null)

const activeModel = computed(() => props.models.find((item) => item.code === props.model))
const thinkingLevels = computed(() => activeModel.value?.thinking_levels || [])

function grow(): void {
  const el = textarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(180, Math.max(28, el.scrollHeight))}px`
}

function mentionHit(): { start: number; end: number; query: string } | null {
  const el = textarea.value
  if (!el) return null
  const caret = el.selectionStart == null ? el.value.length : el.selectionStart
  const before = el.value.slice(0, caret)
  const match = /(^|\s)@([A-Za-z0-9_\-\u4e00-\u9fff]{0,24})$/.exec(before)
  if (!match) return null
  return { start: caret - match[2].length - 1, end: caret, query: match[2] }
}

function slashHit(): { start: number; end: number; query: string } | null {
  const el = textarea.value
  if (!el) return null
  const caret = el.selectionStart == null ? el.value.length : el.selectionStart
  const before = el.value.slice(0, caret)
  const match = /^\/([A-Za-z0-9_-]*)$/.exec(before)
  if (!match) return null
  return { start: 0, end: caret, query: match[1].toLowerCase() }
}

function updatePopup(): void {
  const mention = mentionHit()
  if (mention && props.members.length) {
    const q = mention.query.toLowerCase()
    const items = props.members
      .filter((member) => !q || String(member.name || '').toLowerCase().includes(q))
      .slice(0, 8)
      .map((member) => ({
        name: String(member.name || ''),
        label: String(member.name || ''),
        description: [member.title, member.department, member.expertise].filter(Boolean).join(' · '),
      }))
    popup.value = items.length ? { kind: 'mention', items, index: 0, start: mention.start, end: mention.end } : null
    return
  }
  const slash = slashHit()
  if (slash) {
    const q = slash.query
    const items = props.commands
      .filter((command) => !q || String(command.name).toLowerCase().includes(q))
      .slice(0, 50)
      .map((command) => ({
        name: command.name,
        label: `/${command.name}`,
        description: command.description || command.argument_hint || '',
      }))
    popup.value = items.length ? { kind: 'slash', items, index: 0, start: slash.start, end: slash.end } : null
    return
  }
  popup.value = null
}

function pick(index: number): void {
  if (!popup.value) return
  popup.value.index = index
  choose()
}

function choose(): void {
  const current = popup.value
  const el = textarea.value
  if (!current || !el) return
  const item = current.items[current.index]
  if (!item) return
  if (current.kind === 'mention') {
    const before = draft.value.slice(0, current.start)
    const after = draft.value.slice(current.end)
    draft.value = `${before}@${item.name} ${after}`
    const pos = before.length + item.name.length + 2
    popup.value = null
    nextTick(() => {
      el.focus()
      el.setSelectionRange(pos, pos)
    })
  } else {
    draft.value = `${item.label} `
    popup.value = null
    nextTick(() => {
      el.focus()
      el.setSelectionRange(draft.value.length, draft.value.length)
      grow()
    })
  }
}

function movePopup(delta: number): void {
  const current = popup.value
  if (!current) return
  const count = current.items.length
  current.index = (current.index + delta + count) % count
}

function keydown(event: KeyboardEvent): void {
  if (popup.value) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      movePopup(event.key === 'ArrowDown' ? 1 : -1)
      return
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault()
      choose()
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      popup.value = null
      return
    }
  }
  if (event.key !== 'Enter') return
  if (event.shiftKey || composing.value || event.isComposing) return
  event.preventDefault()
  emit('send')
}

function onInput(): void {
  grow()
  updatePopup()
}

function onBlur(): void {
  window.setTimeout(() => {
    popup.value = null
  }, 150)
}

function onPaste(event: ClipboardEvent): void {
  const files = Array.from(event.clipboardData?.files || [])
  if (files.length) {
    event.preventDefault()
    emit('files', files)
  }
}

function onDrop(event: DragEvent): void {
  dragOver.value = false
  const files = Array.from(event.dataTransfer?.files || [])
  if (files.length) {
    event.preventDefault()
    emit('files', files)
  }
}

function pickFiles(): void {
  fileInput.value?.click()
}

function onFileInput(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (files.length) emit('files', files)
  input.value = ''
}

function focus(): void {
  textarea.value?.focus()
}

defineExpose({ focus })

watch(draft, () => nextTick(grow))
watch(() => props.model, () => nextTick(grow))
</script>

<template>
  <footer class="composer">
    <div v-if="attachments.length" class="attach-bar">
      <div v-for="(item, index) in attachments" :key="item.localId" class="att-pending" :class="{ 'is-image': item.kind === 'image' }">
        <span class="att-pending-thumb">
          <img v-if="item.preview" :src="item.preview" alt="" />
          <svg v-else viewBox="0 0 24 24"><path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7Z" /><path d="M14 3v4h4" /></svg>
        </span>
        <span class="att-pending-main">
          <b :title="item.name">{{ item.name }}</b>
          <span>{{ bytes(item.size) }}{{ item.kind === 'image' ? ' · 图片' : '' }}</span>
        </span>
        <button type="button" class="wbtn att-pending-remove" title="移除附件" aria-label="移除附件" @click="emit('remove-attachment', index)">×</button>
      </div>
    </div>

    <div v-if="popup" class="mention-pop">
      <div class="mention-head">{{ popup.kind === 'mention' ? '@ 成员' : '/ 命令' }}</div>
      <button
        v-for="(item, index) in popup.items"
        :key="`${item.label}-${index}`"
        class="mention-item"
        :class="{ active: index === popup.index }"
        type="button"
        @mousedown.prevent="pick(index)"
      >
        <b>{{ item.label }}</b>
        <span>{{ item.description }}</span>
      </button>
    </div>

    <div class="box" :class="{ 'drag-over': dragOver }" @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop="onDrop">
      <el-button text class="attach" :title="attachments.length >= 8 ? '最多 8 个附件' : '添加图片或文档'" aria-label="添加附件" @click="pickFiles">📎</el-button>
      <input ref="fileInput" type="file" class="file-input" hidden multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.md,.txt,.csv,.json,.html,.css,.js,.ts,.py,.yaml,.yml,.toml,.log" @change="onFileInput" />
      <textarea
        ref="textarea"
        v-model="draft"
        rows="1"
        :placeholder="streaming ? '生成中… 可以继续输入，发送会排队' : (members.length ? '发消息…  @ 选择成员，/ 命令，Enter 发送' : '发消息…  / 命令，Enter 发送')"
        aria-label="消息"
        @input="onInput"
        @keydown="keydown"
        @paste="onPaste"
        @blur="onBlur"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      />
      <el-button v-if="!streaming" type="primary" class="send" title="发送 (Enter)" :disabled="sending || (!draft.trim() && !attachments.length)" @click="emit('send')">发送</el-button>
      <el-button v-else type="danger" plain class="stop" title="停止" @click="emit('stop')">停止</el-button>
    </div>
    <div class="tools">
      <el-select :model-value="model" size="small" style="width:150px" placeholder="默认模型" @update:model-value="emit('update:model', String($event))">
        <el-option label="默认模型" value="" />
        <el-option v-for="item in models" :key="item.code" :label="item.name || item.code" :value="item.code" />
      </el-select>
      <el-select
        v-if="thinkingLevels.length"
        :model-value="thinking"
        size="small"
        style="width:110px"
        placeholder="思考强度"
        @update:model-value="emit('update:thinking', String($event))"
      >
        <el-option label="默认" value="" />
        <el-option v-for="level in thinkingLevels" :key="level" :label="level" :value="level" />
      </el-select>
      <el-radio-group :model-value="perm" size="small" @update:model-value="emit('update:perm', String($event))">
        <el-radio-button value="ask">询问</el-radio-button>
        <el-radio-button value="auto">自动</el-radio-button>
      </el-radio-group>
    </div>
  </footer>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { API_PREFIX } from '@/api/client'
import { TerminalBuffer } from '@/lib/terminalBuffer'

const props = defineProps<{ workspaceId: string }>()
const surface = ref<HTMLElement | null>(null)
const input = ref<HTMLTextAreaElement | null>(null)
const displayOutput = ref('')
const status = ref('正在连接…')
const stickToBottom = ref(true)
const buffer = new TerminalBuffer(120, 2500)
let socket: WebSocket | null = null
let observer: ResizeObserver | null = null
let renderFrame = 0
let composing = false
const decoder = new TextDecoder()

function scheduleRender(): void {
  if (renderFrame) return
  renderFrame = requestAnimationFrame(() => {
    renderFrame = 0
    displayOutput.value = buffer.toString()
    if (stickToBottom.value) {
      requestAnimationFrame(() => {
        const el = surface.value
        if (el) el.scrollTop = el.scrollHeight
      })
    }
  })
}

function send(data: string): void {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'input', data }))
}

function resize(): void {
  const el = surface.value
  if (!el || socket?.readyState !== WebSocket.OPEN) return
  const cols = Math.max(30, Math.floor(el.clientWidth / 8.2))
  const rows = Math.max(8, Math.floor(el.clientHeight / 18))
  buffer.resize(cols)
  socket.send(JSON.stringify({ type: 'resize', cols, rows }))
}

function onKeydown(event: KeyboardEvent): void {
  if (event.isComposing || event.keyCode === 229) return
  let data = ''
  if (event.ctrlKey && event.key.toLowerCase() === 'c') data = '\x03'
  else if (event.ctrlKey && event.key.toLowerCase() === 'd') data = '\x04'
  else if (event.ctrlKey && event.key.toLowerCase() === 'z') data = '\x1a'
  else if (event.ctrlKey && event.key.toLowerCase() === 'l') data = '\x0c'
  else if (event.key === 'Enter') data = '\r'
  else if (event.key === 'Backspace') data = '\x7f'
  else if (event.key === 'Delete') data = '\x1b[3~'
  else if (event.key === 'Tab') data = '\t'
  else if (event.key === 'ArrowUp') data = '\x1b[A'
  else if (event.key === 'ArrowDown') data = '\x1b[B'
  else if (event.key === 'ArrowRight') data = '\x1b[C'
  else if (event.key === 'ArrowLeft') data = '\x1b[D'
  else if (event.key === 'Home') data = '\x1b[H'
  else if (event.key === 'End') data = '\x1b[F'
  else if (event.key === 'PageUp') data = '\x1b[5~'
  else if (event.key === 'PageDown') data = '\x1b[6~'
  else if (event.key === 'Escape') data = '\x1b'
  if (data) {
    event.preventDefault()
    send(data)
  }
}

function onTextInput(event: Event): void {
  const target = event.target as HTMLTextAreaElement
  if (composing || (event as InputEvent).isComposing || !target.value) return
  send(target.value)
  target.value = ''
}

function onCompositionStart(): void {
  composing = true
}

function onCompositionEnd(event: CompositionEvent): void {
  composing = false
  const target = event.target as HTMLTextAreaElement
  if (!target.value) return
  send(target.value)
  target.value = ''
}

function onPaste(event: ClipboardEvent): void {
  const text = event.clipboardData?.getData('text')
  if (text) {
    event.preventDefault()
    send(text)
  }
}

function clear(): void {
  send('\x0c')
  buffer.clear()
  displayOutput.value = ''
  stickToBottom.value = true
  focusInput()
}
function updateScrollFollow(): void {
  const el = surface.value
  if (el) stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 28
}
function focusInput(): void {
  input.value?.focus({ preventScroll: true })
}

onMounted(() => {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${scheme}//${location.host}${API_PREFIX}/local/terminal/ws?workspace_id=${encodeURIComponent(props.workspaceId)}`
  socket = new WebSocket(url)
  socket.binaryType = 'arraybuffer'
  socket.onopen = () => {
    status.value = '已连接'
    resize()
    focusInput()
  }
  socket.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
    const raw = typeof event.data === 'string' ? event.data : decoder.decode(event.data, { stream: true })
    buffer.write(raw)
    scheduleRender()
  }
  socket.onerror = () => { status.value = '连接失败' }
  socket.onclose = (event) => {
    status.value = event.reason === 'shell exited'
      ? 'Shell 已退出'
      : event.reason || (event.code === 1000 ? '终端已断开' : '连接中断')
  }
  if (surface.value) {
    observer = new ResizeObserver(resize)
    observer.observe(surface.value)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  socket?.close()
  if (renderFrame) cancelAnimationFrame(renderFrame)
})
</script>

<template>
  <div class="terminal-session">
    <div class="terminal-toolbar">
      <span :class="{ connected: status.startsWith('已连接') }" :title="status.startsWith('已连接') ? `工作区：${workspaceId}` : status">{{ status }}</span>
      <span class="terminal-input-hint">点击输出区输入命令 · 支持中文输入法和粘贴</span>
      <span class="spacer" />
      <button class="mini-link" type="button" @click="clear">清屏</button>
    </div>
    <pre ref="surface" class="terminal-surface" tabindex="-1" aria-label="终端输出" @scroll="updateScrollFollow" @pointerdown="focusInput">{{ displayOutput }}</pre>
    <textarea ref="input" class="terminal-input" aria-label="终端命令输入" inputmode="text" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" rows="1" @keydown="onKeydown" @input="onTextInput" @compositionstart="onCompositionStart" @compositionend="onCompositionEnd" @paste="onPaste" />
  </div>
</template>

<style scoped>
.terminal-session{position:relative;height:100%;min-height:0;display:flex;flex-direction:column;overflow:hidden;background:#101318}
.terminal-toolbar{display:flex;align-items:center;gap:8px;min-height:32px;padding:6px 10px;border-bottom:1px solid #2b3039;color:#a7b0bf;font-size:10px}
.terminal-toolbar .connected{color:#7bd7a3}
.terminal-input-hint{color:#8997a8;font-size:9px;white-space:nowrap}
.terminal-surface{flex:1;min-height:0;overflow:auto;margin:0;padding:11px;color:#e0e5ec;outline:0;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"PingFang SC","Microsoft YaHei",monospace;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:2}
.terminal-input{position:absolute;left:12px;bottom:12px;width:1px;height:1px;min-width:0;min-height:0;margin:0;padding:0;border:0;outline:0;opacity:0;resize:none;overflow:hidden;pointer-events:none}
@media(max-width:520px){.terminal-input-hint{display:none}}
</style>

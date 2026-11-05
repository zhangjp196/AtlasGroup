import { onBeforeUnmount, ref } from 'vue'
import { api, sseUrl } from '@/api/client'
import type {
  Approval,
  Conversation,
  Message,
  Question,
  QueueResponse,
  RunEvent,
  RunStartResponse,
} from '@/api/types'
import type { ProcessEntry } from '@/lib/trace'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

export interface LiveEntry extends ProcessEntry {
  key: string
  closed?: boolean
}

export interface TaskItem {
  content: string
  status: string
}

export interface PendingAttachment {
  localId: string
  name: string
  mime: string
  size: number
  kind: 'image' | 'file'
  file: File
  preview: string
}

const MAX_ATTACHMENTS = 8
const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
const MAX_ATTACHMENTS_TOTAL = 50 * 1024 * 1024
const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|bmp|avif)$/i
const DOC_EXTENSIONS = [
  '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.pdf',
  '.txt', '.text', '.md', '.markdown', '.mdx', '.rst', '.log', '.csv', '.tsv',
  '.json', '.jsonl', '.xml', '.yaml', '.yml', '.toml', '.ini', '.conf', '.cfg',
  '.properties', '.html', '.htm', '.css', '.scss', '.less', '.js', '.mjs', '.cjs',
  '.jsx', '.ts', '.tsx', '.vue', '.svelte', '.py', '.java', '.kt', '.scala',
  '.cs', '.c', '.h', '.cc', '.cpp', '.hpp', '.go', '.rs', '.rb', '.php', '.swift',
  '.dart', '.lua', '.r', '.pl', '.sh', '.bash', '.zsh', '.fish', '.bat', '.cmd',
  '.ps1', '.sql', '.gradle', '.tex', '.proto',
]

export function useChat() {
  const app = useAppStore()
  const data = useDataStore()

  const conversation = ref<Conversation | null>(null)
  const messages = ref<Message[]>([])
  const entries = ref<LiveEntry[]>([])
  const tasks = ref<TaskItem[]>([])
  const approvals = ref<Approval[]>([])
  const questions = ref<Question[]>([])
  const pendingAttachments = ref<PendingAttachment[]>([])
  const queue = ref<Array<{ id: string; content?: string; title?: string }>>([])
  const loading = ref(false)
  const sending = ref(false)
  const streaming = ref(false)
  const runId = ref('')
  const error = ref('')
  const roleName = ref('AtlasX')
  const roleAvatar = ref('')
  const roleSub = ref('')
  const cursor = ref(0)

  let source: EventSource | null = null
  let entrySeq = 0
  let queueTimer: number | null = null

  function newKey(): string {
    entrySeq += 1
    return `live-${entrySeq}`
  }

  function resetLive(): void {
    entries.value = []
    tasks.value = []
    error.value = ''
  }

  async function open(id: string): Promise<void> {
    loading.value = true
    error.value = ''
    closeStream()
    try {
      const conv = await api<Conversation>(`/conversations/${encodeURIComponent(id)}`)
      conversation.value = conv
      messages.value = conv.messages || []
      resetLive()
      clearAttachments()
      queue.value = []
      await loadPending()
      await resumeRunning()
      await api(`/conversations/${encodeURIComponent(id)}/read`, { method: 'POST' }).catch(() => {})
      void data.loadAll()
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      loading.value = false
    }
  }

  async function resumeRunning(): Promise<void> {
    if (!conversation.value) return
    try {
      const data = await api<QueueResponse>(
        `/conversations/${encodeURIComponent(conversation.value.id)}/queue`,
      )
      queue.value = data.items || []
      if (data.running?.id) openStream(data.running.id)
      if (queue.value.length || data.running) scheduleQueuePoll()
    } catch {
      /* ignore */
    }
  }

  function scheduleQueuePoll(): void {
    if (queueTimer) window.clearTimeout(queueTimer)
    queueTimer = window.setTimeout(() => {
      void pollQueue()
    }, 2000)
  }

  async function pollQueue(): Promise<void> {
    if (!conversation.value) return
    try {
      const data = await api<QueueResponse>(
        `/conversations/${encodeURIComponent(conversation.value.id)}/queue`,
      )
      queue.value = data.items || []
      if (!streaming.value && data.running?.id) openStream(data.running.id)
      if (queue.value.length || data.running) scheduleQueuePoll()
    } catch {
      /* ignore */
    }
  }

  async function loadPending(): Promise<void> {
    if (!conversation.value) return
    const id = encodeURIComponent(conversation.value.id)
    const [appr, qs] = await Promise.all([
      api<{ items: Approval[] }>(`/conversations/${id}/approvals?status=pending`).catch(() => ({ items: [] })),
      api<{ items: Question[] }>(`/conversations/${id}/questions`).catch(() => ({ items: [] })),
    ])
    approvals.value = appr.items || []
    questions.value = qs.items || []
  }

  async function send(text: string, options: { perm?: string; thinking?: string; model?: string } = {}): Promise<boolean> {
    if (!conversation.value || sending.value) return false
    const content = text.trim()
    const files = pendingAttachments.value.slice()
    if (!content && !files.length) return false
    const convId = conversation.value.id
    const optimisticId = `local-${Date.now()}`
    messages.value.push({
      id: optimisticId,
      by: 'user',
      content,
      attachments: files.map((item) => ({
        id: item.localId,
        name: item.name,
        mime: item.mime,
        size: item.size,
        kind: item.kind,
        url: item.preview,
      })),
    })
    resetLive()
    sending.value = true
    streaming.value = true
    try {
      const attachmentPayloads = []
      for (const item of files) {
        attachmentPayloads.push({
          name: item.name,
          mime: item.mime,
          size: item.size,
          data: await fileToBase64(item.file),
        })
      }
      const run = await api<RunStartResponse>(
        `/conversations/${encodeURIComponent(convId)}/messages/run`,
        {
          method: 'POST',
          body: {
            content,
            mode: 'autonomous',
            auto_execute: options.perm === 'auto',
            thinking_level: options.thinking || '',
            mentions: mentionsOf(content),
            attachments: attachmentPayloads.length ? attachmentPayloads : undefined,
          },
        },
      )
      clearAttachments()
      queue.value = run.queue || []
      if (queue.value.length) scheduleQueuePoll()
      runId.value = run.id
      openStream(run.id)
      return true
    } catch (err) {
      messages.value = messages.value.filter((message) => message.id !== optimisticId)
      streaming.value = false
      sending.value = false
      error.value = err instanceof Error ? err.message : String(err)
      app.toast(error.value, 'bad', 5000)
      return false
    }
  }

  async function fileToBase64(file: File): Promise<string> {
    const buffer = await file.arrayBuffer()
    const bytes = new Uint8Array(buffer)
    let binary = ''
    const chunk = 0x8000
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
    }
    return btoa(binary)
  }

  function isImageFile(file: File): boolean {
    return String(file.type || '').startsWith('image/') || IMAGE_EXT_RE.test(file.name || '')
  }

  function isDocumentFile(file: File): boolean {
    const name = String(file.name || '').toLowerCase()
    const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : ''
    if (DOC_EXTENSIONS.includes(ext)) return true
    const base = name.split('/').pop() || ''
    if (['dockerfile', 'makefile', 'rakefile', 'gemfile', 'procfile'].includes(base)) return true
    return String(file.type || '').startsWith('text/')
  }

  function supportsVision(): boolean {
    const conv = conversation.value
    const code = conv?.model_code || data.activeModel || ''
    const model = data.models.find((item) => item.code === code)
    if (!model) return true
    if (typeof model.effective_vision === 'boolean') return model.effective_vision
    return Boolean(model.vision)
  }

  function addFiles(files: FileList | File[]): void {
    const incoming = Array.from(files || [])
    if (!incoming.length) return
    const vision = supportsVision()
    let total = pendingAttachments.value.reduce((sum, item) => sum + Number(item.size || 0), 0)
    let added = 0
    for (const file of incoming) {
      if (pendingAttachments.value.length >= MAX_ATTACHMENTS) {
        app.toast(`每条消息最多 ${MAX_ATTACHMENTS} 个附件`, 'bad', 4000)
        break
      }
      const image = isImageFile(file)
      if (image && !vision) {
        app.toast('当前模型未开启图片支持，请切换模型或在设置中开启', 'bad', 5000)
        continue
      }
      if (!image && !isDocumentFile(file)) {
        app.toast(`不支持的文件：${file.name}`, 'bad', 4000)
        continue
      }
      if (file.size > MAX_ATTACHMENT_BYTES) {
        app.toast(`${file.name} 超过 25 MB`, 'bad', 4200)
        continue
      }
      if (total + file.size > MAX_ATTACHMENTS_TOTAL) {
        app.toast('附件总大小超过 50 MB', 'bad', 4200)
        break
      }
      total += file.size
      pendingAttachments.value.push({
        localId: `att-local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: file.name,
        mime: file.type || '',
        size: file.size,
        kind: image ? 'image' : 'file',
        file,
        preview: image ? URL.createObjectURL(file) : '',
      })
      added += 1
    }
    if (added) app.toast(`已添加 ${added} 个附件`, 'ok', 1200)
  }

  function removeAttachment(index: number): void {
    const item = pendingAttachments.value[index]
    if (item?.preview) URL.revokeObjectURL(item.preview)
    pendingAttachments.value.splice(index, 1)
  }

  function clearAttachments(): void {
    pendingAttachments.value = []
  }

  async function moveQueued(id: string, direction: 'up' | 'down'): Promise<void> {
    if (!conversation.value) return
    try {
      await api(`/conversations/${encodeURIComponent(conversation.value.id)}/queue/${encodeURIComponent(id)}/move`, {
        method: 'POST',
        body: { direction },
      })
      await pollQueue()
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad')
    }
  }

  async function cancelQueued(id: string): Promise<void> {
    if (!conversation.value) return
    try {
      await api(`/conversations/${encodeURIComponent(conversation.value.id)}/queue/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      })
      await pollQueue()
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad')
    }
  }

  async function compact(): Promise<void> {
    if (!conversation.value) return
    try {
      const out = await api<{ compressed?: boolean; before_tokens?: number; after_tokens?: number; reason?: string }>(
        `/conversations/${encodeURIComponent(conversation.value.id)}/compact`,
        { method: 'POST' },
      )
      if (out?.compressed) {
        app.toast(`上下文已压缩：${out.before_tokens} → ${out.after_tokens} tokens`, 'ok', 4500)
      } else {
        const hint = out?.reason === 'under' ? '上下文还很短，暂时不用压缩'
          : out?.reason === 'no_window' ? '当前模型没有配置上下文窗口'
            : '没有可折叠的历史'
        app.toast(hint, 'ok', 3000)
      }
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad')
    }
  }

  async function clearContext(scope: 'all' | 'current'): Promise<void> {
    if (!conversation.value) return
    try {
      const out = await api<{ cleared?: number }>(
        `/conversations/${encodeURIComponent(conversation.value.id)}/clear`,
        { method: 'POST', body: { scope } },
      )
      const conv = await api<Conversation>(`/conversations/${encodeURIComponent(conversation.value.id)}`)
      conversation.value = conv
      messages.value = conv.messages || []
      resetLive()
      app.toast(scope === 'all'
        ? `已清空 ${Number(out?.cleared) || 0} 个会话的上下文（历史消息保留）`
        : '当前会话上下文已清空（历史消息保留）', 'ok', 3000)
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad')
    }
  }

  function mentionsOf(text: string): string[] {
    const out: string[] = []
    const re = /@([A-Za-z0-9_\-\u4e00-\u9fff]{1,32})/g
    let match: RegExpExecArray | null
    while ((match = re.exec(text)) !== null) {
      const name = match[1].trim()
      if (name && !out.includes(name)) out.push(name)
      if (out.length >= 16) break
    }
    return out
  }

  function openStream(id: string): void {
    closeStream()
    runId.value = id
    cursor.value = 0
    streaming.value = true
    const es = new EventSource(sseUrl(`/runs/${encodeURIComponent(id)}/stream?since=0`))
    source = es

    es.addEventListener('run', (event) => {
      let payload: { events?: RunEvent[]; last_seq?: number } = {}
      try {
        payload = JSON.parse((event as MessageEvent).data)
      } catch {
        return
      }
      for (const evt of payload.events || []) {
        const seq = Number(evt?._seq || 0)
        if (seq && seq <= cursor.value) continue
        applyEvent(evt)
        if (seq) cursor.value = seq
      }
      if (payload.last_seq && payload.last_seq > cursor.value) cursor.value = payload.last_seq
    })

    es.addEventListener('run_end', () => {
      closeStream()
      streaming.value = false
      sending.value = false
      void finishRun()
    })

    es.addEventListener('error', () => {
      if (es.readyState === EventSource.CLOSED) {
        streaming.value = false
        sending.value = false
      }
    })
  }

  async function finishRun(): Promise<void> {
    if (!conversation.value) return
    try {
      const conv = await api<Conversation>(`/conversations/${encodeURIComponent(conversation.value.id)}`)
      conversation.value = conv
      messages.value = conv.messages || []
      resetLive()
      await loadPending()
    } catch {
      /* keep the live transcript if reload fails */
    }
    void data.loadAll()
    void pollQueue()
  }

  function closeStream(): void {
    if (source) {
      source.close()
      source = null
    }
  }

  function applyEvent(evt: RunEvent): void {
    if (evt.start) {
      roleName.value = evt.role_name || 'AtlasX'
      roleAvatar.value = ''
      const role = data.roles.find((item) => item.id === evt.role_id)
      if (role) {
        roleAvatar.value = role.avatar || ''
        roleSub.value = [role.title, role.department].filter(Boolean).join(' · ')
      }
      return
    }
    if (evt.thinking !== undefined) {
      const piece = String(evt.thinking || '')
      if (piece) appendText('thinking', piece, evt.round)
      return
    }
    if (evt.delta !== undefined) {
      const piece = String(evt.delta || '')
      if (piece) appendText('text', piece, evt.round)
      return
    }
    if (evt.tool_call) {
      const call = evt.tool_call
      const id = call.id || call.name || ''
      const existing = entries.value.find((entry) => entry.kind === 'tool' && entry.id === id)
      if (existing) {
        existing.name = call.name || existing.name
        existing.status = call.status || 'running'
        existing.args = call.args ?? existing.args
        existing.display = call.display ?? existing.display
      } else {
        entries.value.push({
          key: newKey(),
          kind: 'tool',
          id,
          name: call.name || 'tool',
          status: call.status || 'running',
          ok: true,
          args: call.args,
          display: call.display,
        })
      }
      return
    }
    if (evt.tool_result) {
      const result = evt.tool_result
      const id = result.id || result.name || ''
      const existing = entries.value.find((entry) => entry.kind === 'tool' && entry.id === id)
      if (existing) {
        existing.status = 'done'
        existing.ok = result.ok !== false
        existing.duration_ms = result.duration_ms
        existing.args = result.args ?? existing.args
        existing.display = result.display ?? existing.display
      } else {
        entries.value.push({
          key: newKey(),
          kind: 'tool',
          id,
          name: result.name || 'tool',
          status: 'done',
          ok: result.ok !== false,
          duration_ms: result.duration_ms,
          args: result.args,
          display: result.display,
        })
      }
      return
    }
    if (evt.todo_update) {
      tasks.value = evt.todo_update.items || []
      return
    }
    if (evt.tool_approval) {
      if (!approvals.value.some((item) => item.id === evt.tool_approval?.id)) {
        approvals.value.push(evt.tool_approval)
      }
      return
    }
    if (evt.approval_decided) {
      const hit = approvals.value.find((item) => item.id === evt.approval_decided?.id)
      if (hit) hit.status = evt.approval_decided.status || 'denied'
      return
    }
    if (evt.ask_user) {
      if (!questions.value.some((item) => item.id === evt.ask_user?.id)) {
        questions.value.push(evt.ask_user)
      }
      return
    }
    if (evt.ask_user_done) {
      questions.value = questions.value.filter((item) => item.id !== evt.ask_user_done?.id)
      return
    }
    if (evt.error) {
      error.value = typeof evt.error === 'string' ? evt.error : (evt.error.message || '运行失败')
      return
    }
    if (evt.done) {
      const content = evt.content || evt.text || ''
      if (content) {
        const last = entries.value[entries.value.length - 1]
        if (last && last.kind === 'text' && !last.final) {
          last.text = content
          last.final = true
          last.round = evt.round
        } else {
          entries.value.push({
            key: newKey(),
            kind: 'text',
            text: content,
            final: true,
            round: evt.round,
          })
        }
      }
    }
    if (evt.org_update || evt.role_update || evt.app_update) {
      void data.loadAll()
    }
  }

  function appendText(kind: 'thinking' | 'text', piece: string, round?: number): void {
    const last = entries.value[entries.value.length - 1]
    if (last && last.kind === kind && last.round === round && !last.closed) {
      last.text = `${last.text || ''}${piece}`
      return
    }
    entries.value.push({ key: newKey(), kind, text: piece, round })
  }

  async function decideApproval(id: string, action: string): Promise<void> {
    try {
      const out = await api<{ status?: string }>(`/approvals/${encodeURIComponent(id)}`, {
        method: 'POST',
        body: { action },
      })
      const hit = approvals.value.find((item) => item.id === id)
      if (hit) hit.status = out?.status || (action === 'deny' ? 'denied' : 'approved')
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad')
    }
  }

  async function answerQuestion(id: string, payload: Record<string, unknown>): Promise<void> {
    try {
      await api(`/questions/${encodeURIComponent(id)}`, { method: 'POST', body: payload })
      questions.value = questions.value.filter((item) => item.id !== id)
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad')
    }
  }

  async function stop(): Promise<void> {
    if (!runId.value) return
    try {
      await api(`/runs/${encodeURIComponent(runId.value)}/stop`, { method: 'POST' })
      app.toast('已请求停止')
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad')
    }
  }

  function dispose(): void {
    closeStream()
    if (queueTimer) {
      window.clearTimeout(queueTimer)
      queueTimer = null
    }
  }

  onBeforeUnmount(dispose)

  return {
    conversation,
    messages,
    entries,
    tasks,
    approvals,
    questions,
    pendingAttachments,
    queue,
    loading,
    sending,
    streaming,
    runId,
    error,
    roleName,
    roleAvatar,
    roleSub,
    open,
    send,
    stop,
    decideApproval,
    answerQuestion,
    loadPending,
    addFiles,
    removeAttachment,
    clearAttachments,
    moveQueued,
    cancelQueued,
    compact,
    clearContext,
    pollQueue,
    dispose,
  }
}

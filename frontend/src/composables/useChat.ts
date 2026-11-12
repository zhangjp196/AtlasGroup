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
  TokenUsage,
} from '@/api/types'
import {
  expectedSpeaker,
  findPerson,
  REVEAL_INTERVAL_MS,
  revealStep,
  speakerOf,
  type LiveSpeaker,
} from '@/lib/live'
import type { ProcessEntry } from '@/lib/trace'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

export interface LiveEntry extends ProcessEntry {
  key: string
  closed?: boolean
  /** 这条轨迹属于哪个发言角色（群聊一轮里会有多个）。 */
  speaker?: string
  /** 已经收到、但还没被「打字机」推出来的全文（text 是当前已展示的部分）。 */
  full?: string
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
  // 本轮发言者：`speakers` 记每个角色的展示信息，`speakerId` 是当前正在说的那个。
  // 群聊一轮里会有多个角色，流式轨迹按 speaker 分组渲染（见 LiveMessage）。
  const speakers = ref<Record<string, LiveSpeaker>>({})
  const speakerId = ref('')
  const liveStarted = ref(false)
  const cursor = ref(0)
  // 运行中这一轮的实时用量：库里只统计已结束的轮次，这里叠加当轮快照。
  const liveUsage = ref<TokenUsage | null>(null)
  const contextTokens = ref(0)

  let source: EventSource | null = null
  let entrySeq = 0
  let queueTimer: number | null = null

  function newKey(): string {
    entrySeq += 1
    return `live-${entrySeq}`
  }

  // ── 打字机平滑 ────────────────────────────────────────────────
  // 上游经常「静默几秒 → 一次吐一大段」，直接渲染就是整块闪出。这里把收到的
  // 全文放进 entry.full，按固定节拍把字符推进 entry.text（真正渲染的部分）：
  // 小段跟得上，大段在约 0.5s 内追平，观感稳定。
  let revealTimer: number | null = null

  function stopReveal(): void {
    if (revealTimer) {
      window.clearTimeout(revealTimer)
      revealTimer = null
    }
  }

  function scheduleReveal(): void {
    if (revealTimer) return
    revealTimer = window.setTimeout(revealTick, REVEAL_INTERVAL_MS)
  }

  function revealTick(): void {
    revealTimer = null
    let more = false
    for (const entry of entries.value) {
      if (entry.kind !== 'text' && entry.kind !== 'thinking') continue
      const full = String(entry.full || '')
      const shown = String(entry.text || '')
      if (full.length <= shown.length) continue
      entry.text = full.slice(0, shown.length + revealStep(full.length - shown.length))
      more = true
    }
    if (more) scheduleReveal()
  }

  function revealBacklog(): number {
    let total = 0
    for (const entry of entries.value) {
      if (entry.kind !== 'text' && entry.kind !== 'thinking') continue
      total += Math.max(0, String(entry.full || '').length - String(entry.text || '').length)
    }
    return total
  }

  /** 收尾时等打字机把最后一段放完（有上限），避免「刚打到一半整段跳出」。 */
  async function drainReveal(maxMs = 700): Promise<void> {
    const deadline = Date.now() + maxMs
    while (revealBacklog() > 0 && Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, REVEAL_INTERVAL_MS))
    }
  }

  // ── 首字等待计时 ──────────────────────────────────────────────
  // 模型首 token 可能等好几秒；把「等了多久」显示出来，比一个静态的「正在思考」
  // 更不容易被当成卡死。
  const waitSeconds = ref(0)
  let waitTimer: number | null = null
  let waitSince = 0

  function stopWaitClock(): void {
    if (waitTimer) {
      window.clearInterval(waitTimer)
      waitTimer = null
    }
  }

  function startWaitClock(): void {
    if (waitTimer) return
    waitSince = Date.now()
    waitSeconds.value = 0
    waitTimer = window.setInterval(() => {
      if (!streaming.value) {
        stopWaitClock()
        return
      }
      waitSeconds.value = Math.max(0, Math.floor((Date.now() - waitSince) / 1000))
      const hasContent = entries.value.some((entry) =>
        (entry.kind === 'text' || entry.kind === 'thinking')
        && String(entry.full || entry.text || '').length > 0)
      if (hasContent) stopWaitClock()
    }, 1000)
  }

  function resetLive(): void {
    entries.value = []
    tasks.value = []
    error.value = ''
    liveUsage.value = null
    contextTokens.value = 0
    speakers.value = {}
    speakerId.value = ''
    liveStarted.value = false
    waitSeconds.value = 0
    stopWaitClock()
    stopReveal()
  }

  /**
   * start 事件到达前，先把气泡挂到「这一轮最可能开口的角色」上。
   *
   * 以前这里留空，LiveMessage 就退回兜底的「助手」，等服务端把身份事件送上来
   * 才换成真实角色——群聊里看起来就是「先显示助手，再变成指定 / 通用角色」。
   * 现在一发送就按会话绑定（群组主持 / 被 @ 的成员 / 角色）预置身份，之后由
   * start 事件纠正。
   */
  function primeSpeaker(mentions: string[] = []): void {
    const expected = expectedSpeaker(conversation.value, data.teams, data.roles, mentions)
    if (!expected) return
    const key = expected.id || 'pending'
    speakers.value = {
      [key]: { ...expected, avatar: data.roleAvatar(expected.id, expected.avatar) },
    }
    speakerId.value = key
  }

  function userQueue(items?: Array<{ id: string; content?: string; title?: string; target_role_id?: string }>): Array<{ id: string; content?: string; title?: string }> {
    // 团队成员的后台并行 run 不属于用户排队消息；只在执行画布里展示。
    return (items || []).filter((item) => !item.target_role_id)
  }

  /** 打开一条还没有后端记录的草稿会话：先只展示角色和空对话。 */
  function beginDraft(roleId: string, workspaceId = '', teamId = ''): void {
    closeStream()
    loading.value = false
    error.value = ''
    conversation.value = {
      id: '',
      role_id: roleId,
      workspace_id: workspaceId || undefined,
      team_id: teamId || undefined,
    }
    messages.value = []
    entries.value = []
    tasks.value = []
    approvals.value = []
    questions.value = []
    queue.value = []
    resetLive()
    primeSpeaker()
    clearAttachments()
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
      primeSpeaker()
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
    if (!conversation.value?.id) return
    try {
      const data = await api<QueueResponse>(
        `/conversations/${encodeURIComponent(conversation.value.id)}/queue`,
      )
      queue.value = userQueue(data.items)
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
    if (!conversation.value?.id) return
    try {
      const data = await api<QueueResponse>(
        `/conversations/${encodeURIComponent(conversation.value.id)}/queue`,
      )
      queue.value = userQueue(data.items)
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

  async function send(text: string, options: { perm?: string; thinking?: string } = {}): Promise<boolean> {
    if (!conversation.value || sending.value) return false
    const content = text.trim()
    const files = pendingAttachments.value.slice()
    if (!content && !files.length) return false
    const convId = conversation.value.id
    // 正在生成时再发送 = 入队：不打断当前回复，也不切换直播流。
    // 当前 run 结束后 worker 自动认领队列，pollQueue 再把流接到下一条。
    const enqueue = streaming.value
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
    if (!enqueue) {
      streaming.value = true
      startWaitClock()
    }
    sending.value = true
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
      queue.value = userQueue(run.queue)
      if (queue.value.length || enqueue) scheduleQueuePoll()
      if (enqueue) {
        // 已排在当前 run 后面：保持现有直播流不动，队列条会即时显示。
        app.toast('当前回复结束后执行（可在队列条上插队）', 'ok', 2600)
      } else {
        runId.value = run.id
        openStream(run.id)
        primeSpeaker(mentionsOf(content))
      }
      return true
    } catch (err) {
      messages.value = messages.value.filter((message) => message.id !== optimisticId)
      if (!enqueue) {
        streaming.value = false
        resetLive()
      }
      error.value = err instanceof Error ? err.message : String(err)
      app.toast(error.value, 'bad', 5000)
      return false
    } finally {
      // sending 只表示「这次 HTTP 提交还没回来」；流式期间也允许继续入队。
      sending.value = false
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

  function isSupportedFile(file: File): boolean {
    const name = String(file.name || '').toLowerCase()
    const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : ''
    if (DOC_EXTENSIONS.includes(ext)) return true
    const base = name.split('/').pop() || ''
    if (['dockerfile', 'makefile', 'rakefile', 'gemfile', 'procfile'].includes(base)) return true
    return /^(text|audio|video)\//.test(file.type || '')
  }

  function supportsVision(effectiveModel?: string): boolean {
    const conv = conversation.value
    const code = effectiveModel || conv?.model_code || data.activeModel || ''
    const model = data.models.find((item) => item.code === code)
    if (!model) return true
    if (typeof model.effective_vision === 'boolean') return model.effective_vision
    return Boolean(model.vision)
  }

  function addFiles(files: FileList | File[], effectiveModel?: string): void {
    const incoming = Array.from(files || [])
    if (!incoming.length) return
    const vision = supportsVision(effectiveModel)
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
      if (!image && !isSupportedFile(file)) {
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

  /** 插队 = 停止当前回复，并让这条排队消息成为下一轮（后端 /queue/{id}/inject）。 */
  async function injectQueued(id: string): Promise<void> {
    if (!conversation.value) return
    const convId = conversation.value.id
    try {
      await api(`/conversations/${encodeURIComponent(convId)}/queue/${encodeURIComponent(id)}/inject`, {
        method: 'POST',
      })
      closeStream()
      streaming.value = false
      sending.value = false
      runId.value = ''
      resetLive()
      // 被停止的这一轮已由后端收尾落库：重读转录，再交给队列轮询接上新的流。
      const conv = await api<Conversation>(`/conversations/${encodeURIComponent(convId)}`)
      if (conversation.value?.id === convId) {
        conversation.value = conv
        messages.value = conv.messages || []
      }
      await pollQueue()
      app.toast('已停止当前回复，插队消息即将执行', 'ok', 2600)
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

  async function cleanConversation(): Promise<boolean> {
    if (!conversation.value) return false
    try {
      const out = await api<{ nodes?: number }>(
        `/conversations/${encodeURIComponent(conversation.value.id)}/clean`,
        { method: 'POST' },
      )
      const removed = Number(out?.nodes) || 0
      // 服务端已经停掉活跃 run：本地流也一起关掉，否则界面还停在「运行中」。
      closeStream()
      streaming.value = false
      sending.value = false
      runId.value = ''
      cursor.value = 0
      const conv = await api<Conversation>(`/conversations/${encodeURIComponent(conversation.value.id)}`)
      conversation.value = conv
      messages.value = conv.messages || []
      resetLive()
      queue.value = []
      app.toast(removed ? `会话已清空（删除 ${removed} 条记录）` : '会话已清空', 'ok', 3200)
      return true
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad', 5000)
      return false
    }
  }

  async function continueTask(taskId: string): Promise<void> {
    if (!conversation.value || !taskId) return
    const convId = conversation.value.id
    try {
      await api(
        `/conversations/${encodeURIComponent(convId)}/team-tasks/${encodeURIComponent(taskId)}/continue`,
        { method: 'POST' },
      )
      // 结果已汇总成一条主持 run：重新打开会话，续上这条 run 的流。
      await open(convId)
      void app.toast('已把任务结果交回对话', 'ok', 3000)
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), 'bad', 5000)
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
    // 新的 run = 新的直播区：队列里下一条开始时，不能把上一轮的实时输出带进来。
    resetLive()
    runId.value = id
    cursor.value = 0
    streaming.value = true
    startWaitClock()
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
    const convId = conversation.value.id
    // 先把直播区的打字机放完（最多几百毫秒），再切换成落库消息：
    // 否则最后一段会「啪」地整块跳出，又变回不稳定的观感。
    await drainReveal()
    // 等待期间可能已经切了会话：不要用旧会话的结果覆盖当前界面。
    if (!conversation.value || conversation.value.id !== convId) return
    try {
      const conv = await api<Conversation>(`/conversations/${encodeURIComponent(convId)}`)
      conversation.value = conv
      // 即使队列里的下一条已经开始（streaming=true），也要把刚结束这轮的落库
      // 消息补进来；只是不能清掉新一轮的直播条目。
      messages.value = conv.messages || []
      if (!streaming.value) resetLive()
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
      const id = String(evt.role_id || '')
      // 群组里的临时角色 / 成员不在 /roles 列表里：名字用事件里的快照，
      // 头像 / 职务再从团队来源兜底。
      const person = findPerson(data.roles, data.teams, id)
      const meta = speakerOf(person, String(evt.role_name || '').trim() || '助手')
      const key = id || 'default'
      speakers.value = {
        ...speakers.value,
        [key]: {
          id,
          name: String(evt.role_name || '').trim() || meta.name,
          avatar: data.roleAvatar(id, meta.avatar),
          sub: meta.sub,
        },
      }
      speakerId.value = key
      liveStarted.value = true
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
          speaker: speakerId.value,
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
          speaker: speakerId.value,
        })
      }
      return
    }
    // 后端每轮模型请求后推一次累计快照（输入 / 输出 / 缓存 / 速度）；收尾的
    // done 事件也带 usage/context_tokens，所以这里**不能 return**，要继续走 done 分支。
    if (evt.usage) liveUsage.value = evt.usage
    if (evt.context_tokens !== undefined) {
      contextTokens.value = Number(evt.context_tokens) || 0
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
          // 定稿：正文以这里为准，但**不打断打字机**——最后一段继续按拍推进，
          // run_end 收尾时会等它放完再换成落库消息。只有后端正文和已输出的
          // 前缀对不上（裁剪 / 改写）时才直接跳过去。
          const shown = String(last.text || '')
          last.full = content
          if (shown && !content.startsWith(shown)) last.text = content
          last.final = true
          last.round = evt.round
          if (String(last.text || '').length < content.length) scheduleReveal()
        } else {
          entries.value.push({
            key: newKey(),
            kind: 'text',
            text: content,
            full: content,
            final: true,
            round: evt.round,
            speaker: speakerId.value,
          })
        }
      }
    }
    if (evt.org_update || evt.role_update || evt.app_update) {
      void data.loadAll()
    }
    if (evt.schedule_update) {
      void data.loadSchedules()
    }
  }

  function appendText(kind: 'thinking' | 'text', piece: string, round?: number): void {
    const last = entries.value[entries.value.length - 1]
    if (last && last.kind === kind && last.round === round && last.speaker === speakerId.value
      && !last.closed) {
      last.full = `${last.full ?? last.text ?? ''}${piece}`
    } else {
      entries.value.push({
        key: newKey(), kind, text: '', full: piece, round, speaker: speakerId.value,
      })
    }
    scheduleReveal()
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
    stopReveal()
    stopWaitClock()
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
    speakers,
    speakerId,
    liveStarted,
    waitSeconds,
    liveUsage,
    contextTokens,
    open,
    beginDraft,
    send,
    stop,
    decideApproval,
    answerQuestion,
    loadPending,
    addFiles,
    removeAttachment,
    clearAttachments,
    moveQueued,
    injectQueued,
    cancelQueued,
    compact,
    clearContext,
    cleanConversation,
    continueTask,
    pollQueue,
    resumeRunning,
    dispose,
  }
}

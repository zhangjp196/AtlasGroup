import type { Message, TraceItem } from '@/api/types'

export interface ProcessEntry {
  kind: 'thinking' | 'text' | 'tool' | 'retry' | 'compress' | string
  text?: string
  id?: string
  name?: string
  status?: string
  ok?: boolean
  duration_ms?: number
  args?: unknown
  display?: { diff?: string; [key: string]: unknown }
  final?: boolean
  round?: number
}

function normalizeTrace(trace?: TraceItem[]): TraceItem[] {
  const items: TraceItem[] = []
  const tools = new Map<string, TraceItem>()
  for (const raw of trace || []) {
    if (!raw || typeof raw !== 'object') continue
    const item = { ...raw }
    if (item.kind === 'tool') {
      const id = item.id || item.name || ''
      if (id && tools.has(id)) {
        const existing = tools.get(id) as TraceItem
        Object.assign(existing, item)
        if (item.status === undefined
          && (item.phase === 'end' || item.ok !== undefined || item.duration_ms !== undefined)) {
          existing.status = 'done'
        }
        continue
      }
      if (item.status === undefined) {
        item.status = item.phase === 'end' || item.ok !== undefined || item.duration_ms !== undefined
          ? 'done'
          : 'running'
      }
      items.push(item)
      if (id) tools.set(id, item)
    } else {
      items.push(item)
    }
  }
  const timed = items.length > 0 && items.every((item) => Number.isFinite(Number(item.ts)))
  if (timed) items.sort((a, b) => Number(a.ts) - Number(b.ts))
  return items
}

export function entriesFromMessage(message: Message): ProcessEntry[] {
  const entries: ProcessEntry[] = []
  for (const item of normalizeTrace(message.trace)) {
    if (!item.kind) continue
    if (item.kind === 'thinking') {
      const text = String(item.text || '').trim()
      if (!text) continue
      const last = entries[entries.length - 1]
      if (last && last.kind === 'thinking') last.text = `${last.text}\n${text}`
      else entries.push({ kind: 'thinking', text })
    } else if (item.kind === 'tool') {
      entries.push({ ...item, kind: 'tool' })
    } else if (item.kind === 'text') {
      const text = String(item.text || '').trim()
      if (text) entries.push({ kind: 'text', text, round: item.round })
    } else if (item.kind === 'retry' || item.kind === 'compress') {
      entries.push({ kind: item.kind, text: String(item.text || '') })
    }
  }
  if (message.reasoning && !entries.some((entry) => entry.kind === 'thinking')) {
    entries.unshift({ kind: 'thinking', text: String(message.reasoning) })
  }
  if (Array.isArray(message.tool_trace) && !entries.some((entry) => entry.kind === 'tool')) {
    for (const tool of message.tool_trace) entries.push({ ...tool, kind: 'tool' })
  }
  markFinal(message, entries)
  return entries
}

function markFinal(message: Message, entries: ProcessEntry[]): void {
  const body = String(message.content || '').trim()
  if (body) {
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      const entry = entries[i]
      if (entry.kind === 'text' && String(entry.text || '').trim() === body) {
        entry.final = true
        return
      }
    }
    entries.push({ kind: 'text', text: body, final: true })
    return
  }
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (entries[i].kind === 'text' && String(entries[i].text || '').trim()) {
      entries[i].final = true
      return
    }
  }
}

export function splitEntries(entries: ProcessEntry[]): {
  process: ProcessEntry[]
  final: ProcessEntry | null
} {
  let final: ProcessEntry | null = null
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    if (entries[i].kind === 'text' && entries[i].final) {
      final = entries[i]
      break
    }
  }
  if (!final) {
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      if (entries[i].kind === 'text' && String(entries[i].text || '').trim()) {
        // 只把这条当成本次的「正文」，**不回写 final 标记**：渲染是只读操作，
        // 回写会让 done 事件以为正文已经定稿，从而再 push 一条重复的最终正文。
        final = entries[i]
        break
      }
    }
  }
  return { process: entries.filter((entry) => entry !== final), final }
}

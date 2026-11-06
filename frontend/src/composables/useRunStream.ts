import { onBeforeUnmount, ref } from 'vue'
import { sseUrl } from '@/api/client'
import type { RunEvent, RunRecord } from '@/api/types'
import type { ProcessEntry } from '@/lib/trace'

export interface RunStreamEntry extends ProcessEntry {
  key: string
  closed?: boolean
}

/** 单独观察一个后台 run 的实时流（不绑定当前对话的聊天流）。 */
export function useRunStream() {
  const run = ref<RunRecord | null>(null)
  const entries = ref<RunStreamEntry[]>([])
  const streaming = ref(false)
  const error = ref('')
  let source: EventSource | null = null
  let cursor = 0
  let seq = 0

  function newKey(): string {
    seq += 1
    return `run-${seq}`
  }

  function close(): void {
    if (source) {
      source.close()
      source = null
    }
    streaming.value = false
  }

  function open(runId: string): void {
    close()
    if (!runId) return
    run.value = null
    entries.value = []
    error.value = ''
    cursor = 0
    streaming.value = true
    const es = new EventSource(sseUrl(`/runs/${encodeURIComponent(runId)}/stream?since=0`))
    source = es

    es.addEventListener('run', (event) => {
      let payload: { run?: RunRecord; events?: RunEvent[]; last_seq?: number } = {}
      try {
        payload = JSON.parse((event as MessageEvent).data)
      } catch {
        return
      }
      if (payload.run) run.value = payload.run
      for (const evt of payload.events || []) {
        const at = Number(evt?._seq || 0)
        if (at && at <= cursor) continue
        apply(evt)
        if (at) cursor = at
      }
      if (payload.last_seq && payload.last_seq > cursor) cursor = payload.last_seq
    })

    es.addEventListener('run_end', (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data)
        if (payload?.run) run.value = payload.run
      } catch {
        /* ignore */
      }
      close()
    })

    es.addEventListener('error', () => {
      if (es.readyState === EventSource.CLOSED) streaming.value = false
    })
  }

  function apply(evt: RunEvent): void {
    if (evt.start) return
    if (evt.thinking !== undefined) {
      appendText('thinking', String(evt.thinking || ''))
      return
    }
    if (evt.delta !== undefined) {
      appendText('text', String(evt.delta || ''))
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
        existing.display = call.display as ProcessEntry['display'] ?? existing.display
      } else {
        entries.value.push({
          key: newKey(), kind: 'tool', id, name: call.name || 'tool',
          status: call.status || 'running', ok: true,
          args: call.args, display: call.display as ProcessEntry['display'],
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
        existing.display = result.display as ProcessEntry['display'] ?? existing.display
      } else {
        entries.value.push({
          key: newKey(), kind: 'tool', id, name: result.name || 'tool',
          status: 'done', ok: result.ok !== false,
          duration_ms: result.duration_ms, args: result.args,
          display: result.display as ProcessEntry['display'],
        })
      }
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
        } else {
          entries.value.push({ key: newKey(), kind: 'text', text: content, final: true })
        }
      }
    }
  }

  function appendText(kind: 'thinking' | 'text', piece: string): void {
    if (!piece) return
    const last = entries.value[entries.value.length - 1]
    if (last && last.kind === kind && !last.closed) {
      last.text = `${last.text || ''}${piece}`
      return
    }
    entries.value.push({ key: newKey(), kind, text: piece })
  }

  onBeforeUnmount(close)
  return { run, entries, streaming, error, open, close }
}

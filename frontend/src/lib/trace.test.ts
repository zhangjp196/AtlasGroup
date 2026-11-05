import { describe, expect, it } from 'vitest'
import { entriesFromMessage, splitEntries } from './trace'

describe('trace entries', () => {
  it('merges tool start/end and keeps final text', () => {
    const entries = entriesFromMessage({
      content: 'done',
      trace: [
        { kind: 'thinking', text: 'plan', round: 1 },
        { kind: 'tool', id: 't1', name: 'read_file', phase: 'start', round: 1 },
        { kind: 'tool', id: 't1', phase: 'end', ok: true, duration_ms: 12 },
        { kind: 'text', text: 'done', round: 1 },
      ],
    })
    const tools = entries.filter((entry) => entry.kind === 'tool')
    expect(tools).toHaveLength(1)
    expect(tools[0].status).toBe('done')
    const { process, final } = splitEntries(entries)
    expect(final?.text).toBe('done')
    expect(process.some((entry) => entry.kind === 'thinking')).toBe(true)
  })

  it('falls back to reasoning and tool_trace for old messages', () => {
    const entries = entriesFromMessage({
      content: '',
      reasoning: 'legacy thought',
      tool_trace: [{ kind: 'tool', name: 'shell', ok: false, duration_ms: 3 }],
    })
    expect(entries[0]).toMatchObject({ kind: 'thinking', text: 'legacy thought' })
    expect(entries.some((entry) => entry.kind === 'tool')).toBe(true)
  })
})

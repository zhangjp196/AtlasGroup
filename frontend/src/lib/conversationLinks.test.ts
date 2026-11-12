import { describe, expect, it } from 'vitest'
import { resolveConversationLink } from './conversationLinks'

const origin = 'http://127.0.0.1:10210'
const resolve = (href: string) => resolveConversationLink(href, '', 'conv-1', 'ws-1', origin)

describe('conversation links', () => {
  it('routes relative paths and file URIs to the current conversation', () => {
    expect(resolve('.atlas/reports/a%20b.md')).toMatchObject({ preview: expect.stringContaining('path=.atlas%2Freports%2Fa%20b.md') })
    expect(resolve('file:///Users/local/a%20b.md')).toMatchObject({ preview: expect.stringContaining('path=%2FUsers%2Flocal%2Fa%20b.md') })
  })

  it('uses the attachment endpoint only for attachments belonging to the conversation', () => {
    expect(resolve('/api/agent-zero/conversations/conv-1/attachments/att-1'))
      .toMatchObject({ preview: '/conversations/conv-1/attachments/att-1/preview' })
    expect(() => resolve('/api/agent-zero/conversations/conv-2/attachments/att-1')).toThrow()
  })

  it('keeps HTTP pages external to the local file resolver and rejects other protocols', () => {
    expect(resolve('https://example.com/report')).toMatchObject({ kind: 'web' })
    expect(() => resolve('javascript:alert(1)')).toThrow()
  })
})

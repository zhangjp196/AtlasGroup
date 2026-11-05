import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

describe('markdown rendering', () => {
  it('renders GFM-ish blocks', () => {
    const html = renderMarkdown('# 标题\n\n- a\n- b\n\n`code`')
    expect(html).toContain('<h1>')
    expect(html).toContain('<ul>')
    expect(html).toContain('<code>code</code>')
  })

  it('strips scripts and unsafe attributes', () => {
    const html = renderMarkdown('<script>alert(1)</script>\n\n[x](javascript:alert(1))')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('href="javascript:')
  })

  it('opens external links in a new tab', () => {
    const html = renderMarkdown('[OpenAI](https://openai.com)')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })
})

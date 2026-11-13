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
  it('retains local file links as inert preview targets', () => {
    const html = renderMarkdown('[报告](file:///Users/local/report.md)')
    expect(html).toContain('data-preview-href="file:///Users/local/report.md"')
    expect(html).toContain('href="#"')
  })

  it('routes conversation images through the file resolver and leaves unsupported files clickable', () => {
    const html = renderMarkdown('![图](art/chart.png) ![矢量图](art/chart.svg)', (src) => (
      src.endsWith('.png') ? '/api/agent-zero/conversations/c1/link-file?path=art%2Fchart.png' : ''
    ))
    const template = document.createElement('template')
    template.innerHTML = html
    expect(template.content.querySelector('img')?.getAttribute('src'))
      .toBe('/api/agent-zero/conversations/c1/link-file?path=art%2Fchart.png')
    expect(template.content.querySelector('a')?.getAttribute('data-preview-href')).toBe('art/chart.svg')
  })
})

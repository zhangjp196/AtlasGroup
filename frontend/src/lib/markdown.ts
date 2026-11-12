import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

const defaultValidateLink = md.validateLink.bind(md)
md.validateLink = (url) => /^file:\/\/\//i.test(url) || defaultValidateLink(url)

// 会话捕获链接并打开应用内预览。其他 Markdown 仍可在浏览器打开网页。
const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const href = token.attrGet('href') || ''
  if (/^file:\/\/\//i.test(href)) {
    token.attrSet('data-preview-href', href)
    token.attrSet('href', '#')
  } else if (/^https?:/i.test(href)) {
    token.attrSet('target', '_blank')
    token.attrSet('rel', 'noopener noreferrer')
  }
  return defaultLinkOpen(tokens, idx, options, env, self)
}

export function renderMarkdown(text?: string | null): string {
  const raw = String(text || '')
  if (!raw) return ''
  return DOMPurify.sanitize(md.render(raw), {
    ADD_ATTR: ['target', 'rel', 'data-lang'],
  })
}

export function escapeHtml(value: unknown): string {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[c] as string))
}

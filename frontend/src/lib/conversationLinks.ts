import { API_PREFIX, apiUrl } from '@/api/client'

export type ConversationLink =
  | { kind: 'web'; title: string; url: string }
  | { kind: 'file'; title: string; url: string; preview: string; download: string }

export function resolveConversationLink(
  href: string, title: string, conversationId: string, workspaceId: string, origin: string,
): ConversationLink {
  const raw = href.trim()
  const url = new URL(raw, origin)
  const conv = `/conversations/${encodeURIComponent(conversationId)}`
  const name = decodeURIComponent(url.pathname.split('/').pop() || '') || '链接'
  const label = title.trim() || name
  const isHttp = url.protocol === 'http:' || url.protocol === 'https:'
  if (isHttp && url.origin !== origin) return { kind: 'web', title: label, url: url.href }
  if (!isHttp && (url.protocol !== 'file:' || url.host)) throw new Error('不支持此链接类型')
  if (!conversationId || !raw || raw.startsWith('#')) throw new Error('此链接没有可预览的文件')

  const attachmentPrefix = `${API_PREFIX}${conv}/attachments/`
  if (isHttp && url.pathname.startsWith(attachmentPrefix)
    && /^[^/]+$/.test(url.pathname.slice(attachmentPrefix.length))) {
    return {
      kind: 'file', title: label, url: url.pathname,
      preview: url.pathname.slice(API_PREFIX.length) + '/preview',
      download: url.pathname + '?download=true',
    }
  }

  let path = decodeURIComponent(/^(file|https?):/i.test(raw) ? url.pathname : raw.split(/[?#]/, 1)[0])
  if (isHttp && (url.pathname === `${API_PREFIX}/workspace/file/download`
    || url.pathname === `${API_PREFIX}/workspace/file/preview`)) {
    if (url.searchParams.get('workspace_id') !== workspaceId) throw new Error('此文件不在会话工作区内')
    path = url.searchParams.get('path') || ''
  } else if (isHttp && url.pathname.startsWith(`${API_PREFIX}/`)) {
    throw new Error('不支持预览此应用链接')
  }
  const query = `?path=${encodeURIComponent(path)}`
  const endpoint = `${conv}/link-file`
  return {
    kind: 'file', title: label,
    url: apiUrl(endpoint + query),
    preview: endpoint + '/preview' + query,
    download: apiUrl(endpoint + query + '&download=true'),
  }
}

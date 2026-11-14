export function appendWorkspaceFileReference(draft: string, path: string): string {
  const relativePath = String(path || '').trim().replace(/\\/g, '/')
  if (!relativePath || /[\r\n\u0000]/.test(relativePath) || relativePath.startsWith('/')
    || /^[a-z]:\//i.test(relativePath)
    || relativePath.split('/').includes('..')) {
    return draft
  }
  const safePath = relativePath.replace(/`/g, '\\`')
  const reference = `请结合工作区文件 \`${safePath}\` 处理。`
  const current = String(draft || '').trimEnd()
  if (current.includes(reference)) return current
  return current ? `${current}\n${reference}` : reference
}

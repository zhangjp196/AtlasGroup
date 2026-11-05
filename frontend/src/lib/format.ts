export function shortTime(value?: string | null): string {
  const raw = String(value || '').trim()
  if (!raw) return ''
  const s = raw.replace('T', ' ')
  const date = new Date(s.replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return s.slice(0, 16)
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  if (sameDay) return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) return '昨天'
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function durationText(ms?: number | null): string {
  const value = Number(ms || 0)
  if (!value) return ''
  if (value < 1000) return `${Math.round(value)}ms`
  if (value < 60_000) return `${(value / 1000).toFixed(1)}s`
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`
}

export function firstLine(text: string, max = 110): string {
  const line = String(text || '').split('\n').find((x) => x.trim()) || ''
  return line.length > max ? `${line.slice(0, max)}…` : line
}

export function bytes(n?: number | null): string {
  const value = Number(n || 0)
  if (value > 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value > 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

export function hueOf(value: string): number {
  let n = 0
  const s = String(value || '')
  for (let i = 0; i < s.length; i += 1) n = (n * 31 + s.charCodeAt(i)) >>> 0
  return n % 360
}

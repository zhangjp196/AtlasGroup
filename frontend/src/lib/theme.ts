export const THEMES = [
  { id: 'dark', label: '深色' },
  { id: 'light', label: '浅色' },
  { id: 'midnight', label: '午夜' },
  { id: 'slate', label: '石板' },
  { id: 'ocean', label: '海洋' },
  { id: 'forest', label: '森林' },
  { id: 'sepia', label: '复古' },
  { id: 'violet', label: '紫罗兰' },
] as const

export type ThemeId = (typeof THEMES)[number]['id']

export interface ThemeSchedule {
  mode: 'manual' | 'daynight' | 'rotate'
  day: ThemeId
  night: ThemeId
  interval: number
  dayStart: string
  nightStart: string
}

export const DEFAULT_SCHEDULE: ThemeSchedule = {
  mode: 'manual',
  day: 'light',
  night: 'dark',
  interval: 15,
  dayStart: '06:00',
  nightStart: '19:00',
}

export const THEME_KEY = 'az-theme'
export const SCHEDULE_KEY = 'az-theme-schedule'

const THEME_IDS = new Set<string>(THEMES.map((t) => t.id))
const THEME_MODE: Record<string, 'dark' | 'light'> = {
  dark: 'dark',
  light: 'light',
  midnight: 'dark',
  slate: 'dark',
  ocean: 'light',
  forest: 'dark',
  sepia: 'light',
  violet: 'dark',
}
const THEME_BG: Record<string, string> = {
  dark: '#0d0d0e',
  light: '#fbfbfa',
  midnight: '#0e1216',
  slate: '#121416',
  ocean: '#f4f8fb',
  forest: '#0e1310',
  sepia: '#f6f1e7',
  violet: '#121016',
}

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && THEME_IDS.has(value)
}

function readCookie(name: string): string | null {
  try {
    const hit = document.cookie.split('; ').find((row) => row.startsWith(name + '='))
    return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null
  } catch {
    return null
  }
}

function writeCookie(name: string, value: string): void {
  try {
    document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=31536000; SameSite=Lax`
  } catch {
    /* ignore */
  }
}

export function readTheme(): ThemeId {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (isThemeId(saved)) return saved
  } catch {
    /* ignore */
  }
  const cookie = readCookie(THEME_KEY)
  return isThemeId(cookie) ? cookie : 'dark'
}

function timeToMinutes(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim())
  if (!match) return null
  const hours = Number(match[1])
  const minutes = Number(match[2])
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null
  return hours * 60 + minutes
}

function clockText(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

export function normalizeSchedule(raw: unknown): ThemeSchedule {
  const src = raw && typeof raw === 'object' ? (raw as Partial<ThemeSchedule>) : {}
  const dayStart = timeToMinutes(String(src.dayStart || ''))
  const nightStart = timeToMinutes(String(src.nightStart || ''))
  return {
    mode: ['manual', 'daynight', 'rotate'].includes(String(src.mode))
      ? (src.mode as ThemeSchedule['mode'])
      : DEFAULT_SCHEDULE.mode,
    day: isThemeId(src.day) ? src.day : DEFAULT_SCHEDULE.day,
    night: isThemeId(src.night) ? src.night : DEFAULT_SCHEDULE.night,
    interval: Math.max(5, Math.min(720, Number(src.interval) || DEFAULT_SCHEDULE.interval)),
    dayStart: dayStart == null ? DEFAULT_SCHEDULE.dayStart : clockText(dayStart),
    nightStart: nightStart == null ? DEFAULT_SCHEDULE.nightStart : clockText(nightStart),
  }
}

export function readSchedule(): ThemeSchedule {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(SCHEDULE_KEY)
  } catch {
    /* ignore */
  }
  if (!raw) raw = readCookie(SCHEDULE_KEY)
  try {
    return normalizeSchedule(raw ? JSON.parse(raw) : null)
  } catch {
    return normalizeSchedule(null)
  }
}

export function writeThemePrefs(theme: ThemeId, schedule?: ThemeSchedule): void {
  try {
    localStorage.setItem(THEME_KEY, theme)
    if (schedule) localStorage.setItem(SCHEDULE_KEY, JSON.stringify(normalizeSchedule(schedule)))
  } catch {
    /* ignore */
  }
  writeCookie(THEME_KEY, theme)
  if (schedule) writeCookie(SCHEDULE_KEY, JSON.stringify(normalizeSchedule(schedule)))
}

export function isDaytime(schedule: ThemeSchedule, now = new Date()): boolean {
  const start = timeToMinutes(schedule.dayStart)
  const end = timeToMinutes(schedule.nightStart)
  if (start == null || end == null || start === end) return false
  const current = now.getHours() * 60 + now.getMinutes()
  return start < end ? current >= start && current < end : current >= start || current < end
}

export function scheduledTheme(schedule: ThemeSchedule, current: ThemeId, now = new Date()): ThemeId {
  if (schedule.mode === 'manual') return current
  if (schedule.mode === 'daynight') return isDaytime(schedule, now) ? schedule.day : schedule.night
  const step = Math.max(5, schedule.interval || DEFAULT_SCHEDULE.interval)
  const index = Math.floor(now.getTime() / (step * 60_000)) % THEMES.length
  return THEMES[index].id
}

export function applyTheme(theme: ThemeId): void {
  const id = isThemeId(theme) ? theme : 'dark'
  const mode = THEME_MODE[id] || 'dark'
  const root = document.documentElement
  root.dataset.theme = id
  root.dataset.themeMode = mode
  // Element Plus 的暗色变量挂在 html.dark 上
  root.classList.toggle('dark', mode === 'dark')
  try {
    root.style.colorScheme = mode
  } catch {
    /* ignore */
  }
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', THEME_BG[id] || THEME_BG.dark)
}

export function themeMode(theme: ThemeId): 'dark' | 'light' {
  return THEME_MODE[theme] || 'dark'
}

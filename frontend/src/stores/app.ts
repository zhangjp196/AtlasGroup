import { ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { api } from '@/api/client'
import {
  applyTheme,
  DEFAULT_SCHEDULE,
  normalizeSchedule,
  normalizeTheme,
  readSchedule,
  readTheme,
  scheduledTheme,
  type ThemeId,
  type ThemeSchedule,
  writeThemePrefs,
} from '@/lib/theme'

export type ToastTone = 'ok' | 'bad' | ''

export const useAppStore = defineStore('app', () => {
  const theme = ref<ThemeId>(readTheme())
  const schedule = ref<ThemeSchedule>(readSchedule())
  const serverSynced = ref(false)
  let themeTimer: number | null = null
  let syncTimer: number | null = null

  function toast(message: string, tone: ToastTone = 'ok', ttl = 3000): void {
    ElMessage({
      message,
      type: tone === 'bad' ? 'error' : 'success',
      duration: ttl,
      grouping: true,
      showClose: true,
    })
  }

  function persist(): void {
    writeThemePrefs(theme.value, schedule.value)
    if (syncTimer) window.clearTimeout(syncTimer)
    syncTimer = window.setTimeout(() => {
      fetch('/api/agent-zero/app/theme', {
        method: 'PUT',
        keepalive: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: theme.value, themeSchedule: schedule.value }),
      }).catch(() => {})
    }, 120)
  }

  function apply(next: ThemeId, persistPrefs = true): void {
    theme.value = next
    applyTheme(next)
    if (persistPrefs) persist()
  }

  async function initTheme(): Promise<void> {
    applyTheme(theme.value)
    const hasLocal = (() => {
      try {
        return Boolean(localStorage.getItem('az-theme'))
      } catch {
        return false
      }
    })()
    if (!hasLocal) {
      try {
        const data = await api<{ theme?: string; themeSchedule?: unknown }>('/app/theme')
        if (data?.theme) {
          schedule.value = normalizeSchedule(data.themeSchedule || DEFAULT_SCHEDULE)
          theme.value = normalizeTheme(data.theme) ?? 'dark'
          writeThemePrefs(theme.value, schedule.value)
          applyTheme(theme.value)
        }
      } catch {
        /* ignore */
      }
    }
    writeThemePrefs(theme.value, schedule.value)
    startSchedule()
    serverSynced.value = true
  }

  function tick(): void {
    if (schedule.value.mode === 'manual') return
    const next = scheduledTheme(schedule.value, theme.value)
    if (next !== theme.value) apply(next, false)
  }

  function startSchedule(): void {
    if (themeTimer) window.clearInterval(themeTimer)
    tick()
    if (schedule.value.mode !== 'manual') {
      themeTimer = window.setInterval(tick, 20_000)
    }
  }

  function setTheme(next: ThemeId): void {
    apply(next)
    startSchedule()
  }

  function setSchedule(next: ThemeSchedule): void {
    schedule.value = normalizeSchedule(next)
    persist()
    startSchedule()
  }

  function toggleTheme(): void {
    const wasAuto = schedule.value.mode !== 'manual'
    const next: ThemeId = (document.documentElement.dataset.themeMode === 'light') ? 'dark' : 'light'
    schedule.value.mode = 'manual'
    apply(next)
    startSchedule()
    toast(wasAuto ? `已切换为${next === 'light' ? '浅色' : '深色'}主题，自动切换已关闭` : '主题已切换')
  }

  return {
    theme,
    schedule,
    serverSynced,
    toast,
    initTheme,
    setTheme,
    setSchedule,
    toggleTheme,
    startSchedule,
  }
})

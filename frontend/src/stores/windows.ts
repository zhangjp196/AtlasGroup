import { ref } from 'vue'
import { defineStore } from 'pinia'

export interface TaskWindowState {
  id: string
  conversationId: string
  title: string
  x: number
  y: number
}

const STORAGE_KEY = 'atlasx.taskWindows'
const MAX_WINDOWS = 8

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(min, value), max)
}

function loadSaved(): TaskWindowState[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    if (!Array.isArray(raw)) return []
    const width = typeof window !== 'undefined' ? window.innerWidth : 1400
    const height = typeof window !== 'undefined' ? window.innerHeight : 900
    const seen = new Set<string>()
    const out: TaskWindowState[] = []
    for (const item of raw) {
      const cid = String((item || {}).conversationId || '')
      if (!cid || seen.has(cid)) continue
      seen.add(cid)
      out.push({
        id: `taskwin-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        conversationId: cid,
        title: String((item || {}).title || '会话任务'),
        x: clamp(Number((item || {}).x) || 80, 8, Math.max(8, width - 120)),
        y: clamp(Number((item || {}).y) || 90, 8, Math.max(8, height - 90)),
      })
      if (out.length >= MAX_WINDOWS) break
    }
    return out
  } catch {
    return []
  }
}

/** 任务 / 工单浮窗：同一会话只开一个；位置与打开状态跨刷新恢复。 */
export const useWindowsStore = defineStore('windows', () => {
  const tasks = ref<TaskWindowState[]>(loadSaved())

  function persist(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks.value.map((item) => ({
        conversationId: item.conversationId,
        title: item.title,
        x: item.x,
        y: item.y,
      }))))
    } catch {
      /* localStorage 不可用：只影响恢复，不影响当前会话 */
    }
  }

  function openTask(conversationId: string, title: string): void {
    const cid = String(conversationId || '')
    if (!cid) return
    const existing = tasks.value.find((item) => item.conversationId === cid)
    if (existing) {
      // 已开着就提到最前（数组末尾 = 最上层）。
      tasks.value = [...tasks.value.filter((item) => item.id !== existing.id), existing]
      persist()
      return
    }
    const offset = tasks.value.length * 26
    const width = typeof window !== 'undefined' ? window.innerWidth : 1400
    const height = typeof window !== 'undefined' ? window.innerHeight : 900
    tasks.value.push({
      id: `taskwin-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      conversationId: cid,
      title: title || '会话任务',
      x: clamp(width - 440 - offset, 8, Math.max(8, width - 120)),
      y: clamp(90 + offset, 8, Math.max(8, height - 90)),
    })
    if (tasks.value.length > MAX_WINDOWS) tasks.value = tasks.value.slice(-MAX_WINDOWS)
    persist()
  }

  function closeTask(id: string): void {
    tasks.value = tasks.value.filter((item) => item.id !== id)
    persist()
  }

  function moveTask(id: string, x: number, y: number): void {
    const item = tasks.value.find((entry) => entry.id === id)
    if (!item) return
    item.x = x
    item.y = y
  }

  /** 拖动结束再落盘：拖动过程中不写 localStorage。 */
  function commitTask(id: string): void {
    if (tasks.value.some((item) => item.id === id)) persist()
  }

  function focusTask(id: string): void {
    const item = tasks.value.find((entry) => entry.id === id)
    if (!item) return
    tasks.value = [...tasks.value.filter((entry) => entry.id !== id), item]
    persist()
  }

  return { tasks, openTask, closeTask, moveTask, commitTask, focusTask }
})

import { ref } from 'vue'
import { defineStore } from 'pinia'

export type NoticeTone = 'info' | 'success' | 'warning' | 'error'

export interface NoticeItem {
  id: string
  title: string
  message?: string
  tone: NoticeTone
  action?: string
  actionLabel?: string
  duration: number
  mergeKey?: string
  mergeWindowMs?: number
  count?: number
  updatedAt?: number
}

export const useNoticeStore = defineStore('notice', () => {
  const items = ref<NoticeItem[]>([])
  const timers = new Map<string, number>()
  let seq = 0

  function remove(id: string): void {
    const timer = timers.get(id)
    if (timer) window.clearTimeout(timer)
    timers.delete(id)
    items.value = items.value.filter((item) => item.id !== id)
  }

  function push(input: Omit<NoticeItem, 'id' | 'tone' | 'duration'> & {
    tone?: NoticeTone
    duration?: number
  }): string {
    const now = Date.now()
    const mergeWindow = Math.max(0, Number(input.mergeWindowMs || 5000))
    if (input.mergeKey) {
      const hit = items.value.find((item) => (
        item.mergeKey === input.mergeKey
        && now - Number(item.updatedAt || 0) <= mergeWindow
      ))
      if (hit) {
        // 同一会话的连续通知合并成一条，重置倒计时，避免右上角堆叠。
        hit.title = input.title ?? hit.title
        hit.message = input.message ?? hit.message
        hit.tone = input.tone || hit.tone
        hit.action = input.action ?? hit.action
        hit.actionLabel = input.actionLabel ?? hit.actionLabel
        hit.count = Number(hit.count || 1) + 1
        hit.updatedAt = now
        const duration = Math.max(2000, Number(input.duration || hit.duration || 2500))
        hit.duration = duration
        const oldTimer = timers.get(hit.id)
        if (oldTimer) window.clearTimeout(oldTimer)
        timers.set(hit.id, window.setTimeout(() => remove(hit.id), duration))
        return hit.id
      }
    }
    seq += 1
    const id = `notice-${seq}`
    const duration = Math.max(2000, Number(input.duration || 2500))
    items.value = [...items.value, {
      id,
      title: input.title,
      message: input.message,
      tone: input.tone || 'info',
      action: input.action,
      actionLabel: input.actionLabel,
      duration,
      mergeKey: input.mergeKey,
      mergeWindowMs: mergeWindow,
      count: 1,
      updatedAt: now,
    }]
    const timer = window.setTimeout(() => remove(id), duration)
    timers.set(id, timer)
    return id
  }

  function clear(): void {
    for (const id of [...timers.keys()]) remove(id)
    items.value = []
  }

  return { items, push, remove, clear }
})

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api/client'

export interface AttentionData {
  counts?: {
    approvals?: number
    questions?: number
    authorizations?: number
    unread?: number
    running?: number
    sessions?: number
  }
  approvals?: Array<{
    id: string
    tool?: string
    name?: string
    risk?: string
    note?: string
    conversation_id?: string
  }>
  questions?: Array<{
    id: string
    question?: string
    conversation_id?: string
    options?: Array<string | { label?: string; value?: string }>
  }>
  unread?: Array<{
    id: string
    title?: string
    last_message?: string
    updated_at?: string
  }>
  running?: Array<{
    id?: string
    conversation_id?: string
    title?: string
    status?: string
    workspace_name?: string
  }>
  sessions?: Array<{ id: string; title?: string; permission_mode?: string; updated_at?: string }>
  policy?: Record<string, unknown>
}

export const useAttentionStore = defineStore('attention', () => {
  const data = ref<AttentionData>({})
  const loading = ref(false)
  const counts = computed(() => data.value.counts || {})
  const badgeCount = computed(() => Number(counts.value.approvals || 0))
  let timer: number | null = null

  async function load(): Promise<void> {
    loading.value = true
    try {
      const next = await api<AttentionData>('/app/attention?limit=100')
      // 轮询结果没变时不要替换引用，避免全局组件每 5 秒重渲染一遍。
      if (JSON.stringify(next) !== JSON.stringify(data.value)) data.value = next
    } catch {
      /* 全局通知失败不影响主界面 */
    } finally {
      loading.value = false
    }
  }

  function start(intervalMs = 5000): void {
    void load()
    if (timer) window.clearInterval(timer)
    timer = window.setInterval(() => {
      if (!document.hidden) void load()
    }, intervalMs)
  }

  function stop(): void {
    if (timer) window.clearInterval(timer)
    timer = null
  }

  return { data, loading, counts, badgeCount, load, start, stop }
})

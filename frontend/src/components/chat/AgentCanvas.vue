<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { AgentActivity, AgentGraph, AgentNode } from '@/api/types'
import { durationText, firstLine } from '@/lib/format'

const props = defineProps<{ conversationId: string; open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const data = ref<AgentGraph | null>(null)
const loading = ref(false)
const filter = ref<'all' | 'running' | 'failed'>('all')
let timer: number | null = null

const title = computed(() => data.value?.conversation?.title || '执行画布')
const runStatus = computed(() => data.value?.run?.status || 'idle')
const stats = computed(() => data.value?.stats || {})
const nodes = computed(() => {
  const list = data.value ? [data.value.root, ...(data.value.nodes || [])].filter(Boolean) as AgentNode[] : []
  if (filter.value === 'all') return list
  if (filter.value === 'running') return list.filter((node) => node.status === 'running' || node.status === 'queued')
  return list.filter((node) => node.status === 'failed')
})
const activity = computed(() => (data.value?.activity || []).slice().sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0)).slice(0, 50))
const upcoming = computed(() => data.value?.upcoming || [])

watch(() => [props.open, props.conversationId], ([open]) => {
  if (open) {
    void refresh()
    if (timer) window.clearInterval(timer)
    timer = window.setInterval(() => {
      if (props.open) void refresh()
    }, 2000)
  } else {
    stop()
  }
}, { immediate: true })

onBeforeUnmount(stop)

function stop(): void {
  if (timer) {
    window.clearInterval(timer)
    timer = null
  }
}

async function refresh(): Promise<void> {
  if (!props.conversationId) return
  loading.value = !data.value
  try {
    data.value = await api<AgentGraph>(`/conversations/${encodeURIComponent(props.conversationId)}/graph`)
  } catch (error) {
    data.value = {
      conversation: { title: '画布加载失败' },
      nodes: [],
      activity: [{ text: error instanceof Error ? error.message : String(error) }],
    }
  } finally {
    loading.value = false
  }
}

function nodeTone(node: AgentNode): string {
  if (node.status === 'running' || node.status === 'queued') return 'running'
  if (node.status === 'failed') return 'failed'
  if (node.status === 'done') return 'done'
  return 'idle'
}

function activityTone(item: AgentActivity): string {
  if (item.status === 'failed') return 'failed'
  if (item.status === 'running' || item.status === 'queued') return 'running'
  return 'done'
}
</script>

<template>
  <div v-if="open" class="agent-canvas">
    <header class="agent-canvas-head">
      <div>
        <h2>执行画布</h2>
        <p>{{ title }} · {{ runStatus }} · {{ stats.agents || 1 }} 个代理 · {{ stats.tokens || 0 }} token</p>
      </div>
      <div class="agent-canvas-actions">
        <div class="seg">
          <button class="seg-btn" :class="{ on: filter === 'all' }" @click="filter = 'all'">全部</button>
          <button class="seg-btn" :class="{ on: filter === 'running' }" @click="filter = 'running'">运行中</button>
          <button class="seg-btn" :class="{ on: filter === 'failed' }" @click="filter = 'failed'">失败</button>
        </div>
        <button class="btn small" @click="refresh">刷新</button>
        <button class="iconbtn" title="关闭" aria-label="关闭" @click="emit('close')">×</button>
      </div>
    </header>

    <div class="agent-canvas-body">
      <div class="agent-canvas-wrap">
        <div v-if="loading" class="empty">加载中…</div>
        <div v-else class="agent-grid">
          <div
            v-for="node in nodes"
            :key="node.id"
            class="agent-card"
            :class="nodeTone(node)"
          >
            <div class="agent-node-head">
              <span class="avatar" :style="{ '--h': node.kind === 'root' ? 220 : 200 }">{{ (node.name || '·').slice(0, 1) }}</span>
              <div class="agent-node-name">
                <b>{{ node.name || node.id }}</b>
                <span>{{ node.kind === 'root' ? '主持' : node.kind === 'team' ? '团队成员' : '子代理' }} · {{ node.status || 'idle' }}</span>
              </div>
              <span class="tag" :class="{ ok: node.status === 'done', tmp: node.status === 'failed' }">{{ node.status || 'idle' }}</span>
            </div>
            <div class="agent-card-body">
              <p>{{ firstLine(node.current || node.text || node.goal || '等待中', 180) }}</p>
              <div class="agent-card-meta">
                <span v-if="node.tools?.length">{{ node.tools.length }} 个工具</span>
                <span v-if="node.elapsed_ms">{{ durationText(node.elapsed_ms) }}</span>
                <span v-if="node.error" class="fail">{{ firstLine(node.error, 80) }}</span>
              </div>
            </div>
          </div>
          <div v-if="!nodes.length" class="empty">还没有代理活动</div>
        </div>

        <div v-if="upcoming.length" class="agent-upcoming">
          <h3>排队中</h3>
          <div v-for="item in upcoming" :key="item.id" class="agent-feed-row queued">
            <span class="agent-dot queued" />
            <div class="agent-feed-main"><b>等待执行</b><span>{{ firstLine(item.content || '', 120) }}</span></div>
          </div>
        </div>
      </div>

      <aside class="agent-activity">
        <section>
          <h3>最近活动</h3>
          <div class="agent-feed">
            <div v-for="(item, index) in activity" :key="index" class="agent-feed-row" :class="activityTone(item)">
              <span class="agent-dot" :class="activityTone(item)" />
              <div class="agent-feed-main">
                <b>{{ item.agent || item.agent_id || '代理' }}</b>
                <span>{{ firstLine(item.text || item.kind || '', 140) }}</span>
              </div>
              <span class="agent-feed-meta">{{ item.ts ? new Date(Number(item.ts) * (Number(item.ts) < 1e12 ? 1000 : 1)).toLocaleTimeString() : '' }}</span>
            </div>
            <div v-if="!activity.length" class="agent-feed-empty">还没有活动</div>
          </div>
        </section>
      </aside>
    </div>
  </div>
</template>

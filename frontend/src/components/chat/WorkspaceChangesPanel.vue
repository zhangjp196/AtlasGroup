<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/client'
import { useAppStore } from '@/stores/app'

interface Checkpoint {
  id: string
  run_id: string
  label?: string
  created_at?: string
}
interface FileChange { path: string; size?: number; old_size?: number }
interface WorkspaceDiff {
  available: boolean
  reason?: string
  checkpoint?: Checkpoint
  counts: { added: number; modified: number; deleted: number }
  added: FileChange[]
  modified: FileChange[]
  deleted: FileChange[]
  truncated: boolean
}

const props = defineProps<{ open: boolean; conversationId: string }>()
const emit = defineEmits<{ (event: 'reference', path: string): void }>()
const app = useAppStore()
const loading = ref(false)
const error = ref('')
const diff = ref<WorkspaceDiff | null>(null)
let generation = 0
let refreshTimer = 0

async function refresh(): Promise<void> {
  const convId = props.conversationId
  if (!props.open || !convId) return
  const requestId = ++generation
  loading.value = true
  error.value = ''
  try {
    const result = await api<{ items?: Checkpoint[] }>(
      `/conversations/${encodeURIComponent(convId)}/checkpoints`,
    )
    const checkpoint = (result.items || []).find((item) => item.id)
    if (!checkpoint) {
      if (requestId === generation && convId === props.conversationId) diff.value = null
      return
    }
    const current = await api<WorkspaceDiff>(
      `/conversations/${encodeURIComponent(convId)}/checkpoints/${encodeURIComponent(checkpoint.id)}/diff`,
    )
    if (requestId === generation && convId === props.conversationId) diff.value = current
  } catch (cause) {
    if (requestId === generation) {
      error.value = cause instanceof Error ? cause.message : String(cause)
    }
  } finally {
    if (requestId === generation) loading.value = false
  }
}

async function copyPath(path: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(path)
    app.toast('已复制相对路径')
  } catch (cause) {
    app.toast(cause instanceof Error ? cause.message : '无法访问剪贴板', 'bad')
  }
}

async function restoreFile(path: string): Promise<void> {
  const checkpointId = diff.value?.checkpoint?.id
  if (!checkpointId) return
  const accepted = window.confirm(
    `仅恢复「${path}」到本会话检查点的状态。新增文件会被删除，修改或删除的文件会被覆盖 / 恢复。继续吗？`,
  )
  if (!accepted) return
  try {
    await api(`/conversations/${encodeURIComponent(props.conversationId)}/checkpoints/${encodeURIComponent(checkpointId)}/rewind`, {
      method: 'POST',
      body: { files: true, conversation: false, delete_added: true, paths: [path] },
    })
    app.toast(`已恢复 ${path}`)
    await refresh()
  } catch (cause) {
    app.toast(cause instanceof Error ? cause.message : String(cause), 'bad')
  }
}

watch(() => [props.open, props.conversationId], ([open]) => {
  generation += 1
  window.clearInterval(refreshTimer)
  refreshTimer = 0
  if (open) {
    diff.value = null
    error.value = ''
    loading.value = false
    void refresh()
    refreshTimer = window.setInterval(() => { void refresh() }, 5000)
  } else {
    diff.value = null
    loading.value = false
  }
}, { immediate: true })

onBeforeUnmount(() => {
  generation += 1
  window.clearInterval(refreshTimer)
  refreshTimer = 0
})
</script>

<template>
  <section class="workspace-changes" aria-label="工作区改动">
    <header class="wc-head">
      <div><b>工作区改动</b><small>与最近一次运行开始时的检查点比较</small></div>
      <button class="mini-link" type="button" :disabled="loading" @click="refresh">刷新</button>
    </header>
    <div v-if="loading && !diff" class="wc-empty">读取检查点…</div>
    <div v-else-if="error && !diff" class="wc-empty">{{ error }}</div>
    <div v-else-if="!diff" class="wc-empty">此会话还没有文件检查点。发送消息后会自动记录。</div>
    <div v-else-if="!diff.available" class="wc-empty">{{ diff.reason || '当前无法比较工作区' }}</div>
    <template v-else>
      <div class="wc-counts">
        <span>新增 {{ diff.counts.added }}</span>
        <span>修改 {{ diff.counts.modified }}</span>
        <span>删除 {{ diff.counts.deleted }}</span>
      </div>
      <div class="wc-list">
        <template v-for="group in [
          { label: '新增', tone: 'added', items: diff.added },
          { label: '修改', tone: 'modified', items: diff.modified },
          { label: '删除', tone: 'deleted', items: diff.deleted },
        ]" :key="group.tone">
          <div v-for="item in group.items" :key="`${group.tone}:${item.path}`" class="wc-file">
            <span :class="group.tone">{{ group.label }}</span>
            <code :title="item.path">{{ item.path }}</code>
            <button class="mini-link" type="button" title="恢复此文件到检查点状态" @click="restoreFile(item.path)">恢复</button>
            <button class="mini-link" type="button" title="将文件路径插入消息输入框" @click="emit('reference', item.path)">引用</button>
            <button class="mini-link" type="button" title="复制相对路径" @click="copyPath(item.path)">复制路径</button>
          </div>
        </template>
        <div v-if="!diff.counts.added && !diff.counts.modified && !diff.counts.deleted" class="wc-empty">
          最近一次运行后没有文件变化。
        </div>
        <div v-if="diff.truncated" class="wc-note">文件较多，当前只显示部分结果。</div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.workspace-changes{display:flex;flex:1;min-width:0;min-height:0;flex-direction:column;background:var(--bg)}
.wc-head{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid var(--line-soft)}
.wc-head div{display:flex;flex-direction:column;gap:2px}.wc-head b{font-size:12px}.wc-head small,.wc-note{color:var(--faint);font-size:10px}
.wc-counts{display:flex;gap:12px;padding:8px 12px;border-bottom:1px solid var(--line-soft);font-size:10px;color:var(--dim)}
.wc-list{flex:1;overflow:auto;padding:8px 12px}.wc-file{display:flex;align-items:center;gap:8px;min-height:30px;border-bottom:1px solid var(--line-soft)}
.wc-file span{width:34px;flex:none;font-size:9px}.wc-file .added{color:var(--ok)}.wc-file .modified{color:var(--accent)}.wc-file .deleted{color:var(--danger)}
.wc-file code{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px}
.wc-empty{display:grid;flex:1;min-height:90px;place-items:center;padding:16px;text-align:center;color:var(--faint);font-size:11px}.wc-note{padding:8px}
</style>

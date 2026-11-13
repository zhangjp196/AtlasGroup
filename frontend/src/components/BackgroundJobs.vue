<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/client'
import { durationText } from '@/lib/format'
import { useDataStore } from '@/stores/data'
import Modal from './Modal.vue'

interface Job {
  job_id: string
  name: string
  status: string
  started_at: number
  finished_at: number
  elapsed_s: number
  args?: { cmd?: string; cwd?: string }
  stdout?: string
  stderr?: string
  error?: string
  result?: string
  exit_code?: number | null
  output_truncated?: boolean
}

const props = defineProps<{ open: boolean; conversationId?: string; workspaceId?: string }>()
const emit = defineEmits<{ close: [] }>()
const data = useDataStore()
const workspaces = computed(() => data.workspaces.filter((item) => item.root_exists && !item.archived))
const scope = computed(() => props.conversationId
  ? '?conversation_id=' + encodeURIComponent(props.conversationId) : '')
const base = '/local/background-jobs'
const items = ref<Job[]>([])
const selected = ref('')
const detail = ref<Job | null>(null)
const workspace = ref('')
const targetWorkspace = computed(() => props.conversationId ? props.workspaceId || '' : workspace.value)
const name = ref('')
const command = ref('')
const busy = ref(false)
const error = ref('')
const loadError = ref('')
const stateLabel: Record<string, string> = { running: '运行中', done: '已完成', failed: '失败', killed: '已停止' }
let timer = 0
let request = 0

function date(value: number): string {
  return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'
}

async function refresh(): Promise<void> {
  if (!props.open) return
  const id = ++request
  const jobId = selected.value
  try {
    const [list, output] = await Promise.all([
      api<{ items: Job[] }>(base + scope.value),
      jobId ? api<Job>(base + '/' + encodeURIComponent(jobId)).catch(() => null) : null,
    ])
    if (id !== request) return
    loadError.value = ''
    items.value = list.items
    detail.value = output
    if (jobId && !output && !items.value.some((item) => item.job_id === jobId)) selected.value = ''
  } catch (cause) {
    if (id === request) loadError.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function run(): Promise<void> {
  if (busy.value || !targetWorkspace.value || !command.value.trim()) return
  error.value = ''
  busy.value = true
  try {
    const job = await api<Job>(base, { method: 'POST', body: {
      name: name.value, command: command.value,
      conversation_id: props.conversationId, workspace_id: targetWorkspace.value,
    } })
    selected.value = job.job_id
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

async function stop(job: Job): Promise<void> {
  error.value = ''
  try {
    await api(base + '/' + encodeURIComponent(job.job_id) + '/stop', { method: 'POST' })
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

watch([() => props.open, () => props.conversationId], ([open]) => {
  window.clearInterval(timer)
  request += 1
  items.value = []
  detail.value = null
  selected.value = ''
  loadError.value = ''
  error.value = ''
  if (open) {
    void refresh()
    timer = window.setInterval(() => void refresh(), 2000)
  }
}, { immediate: true })
watch(selected, () => { detail.value = null; void refresh() })
onBeforeUnmount(() => { window.clearInterval(timer); request += 1 })
</script>

<template>
  <Modal :open="open" title="后台任务" width="min(900px, 94vw)" @close="emit('close')">
    <div class="background-jobs">
      <form class="script-form" @submit.prevent="run">
        <div class="script-fields">
          <label v-if="!conversationId" class="field">工作区
            <select v-model="workspace" class="input" required>
              <option value="" disabled>选择工作区</option>
              <option v-for="item in workspaces" :key="item.id" :value="item.id">{{ item.name }}</option>
            </select>
          </label>
          <label class="field">任务名称<input v-model="name" class="input" maxlength="160" placeholder="可选" /></label>
        </div>
        <label class="field">命令<textarea v-model="command" class="input script-command" rows="2" maxlength="20000" required /></label>
        <div class="script-actions">
          <button class="btn primary" type="submit" :disabled="busy || !targetWorkspace || !command.trim()">后台运行</button>
          <span v-if="conversationId && !targetWorkspace" class="hintline">为会话绑定工作区后即可运行脚本。</span>
          <span v-else-if="targetWorkspace" class="hintline">{{ workspaces.find((item) => item.id === targetWorkspace)?.root_path }}</span>
        </div>
      </form>
      <p v-if="error || loadError" class="run-error" role="alert">{{ error || loadError }}</p>
      <div v-if="!items.length && !loadError" class="empty">暂无后台任务</div>
      <ul v-else class="script-list">
        <li v-for="job in items" :key="job.job_id">
          <button class="script-row" type="button" :aria-expanded="selected === job.job_id"
            @click="selected = selected === job.job_id ? '' : job.job_id">
            <strong>{{ job.name }}</strong>
            <span>{{ stateLabel[job.status] || job.status }}</span>
            <span>开始 {{ date(job.started_at) }}</span>
            <span v-if="job.finished_at">结束 {{ date(job.finished_at) }}</span>
            <span>耗时 {{ durationText(job.elapsed_s * 1000) || '0s' }}</span>
            <code v-if="job.args?.cmd">{{ job.args.cmd }}</code>
          </button>
          <button v-if="job.status === 'running'" class="btn" type="button" @click="stop(job)">停止</button>
          <section v-if="selected === job.job_id && detail" class="script-output">
            <span v-if="job.args?.cwd">目录 {{ job.args.cwd }}</span>
            <span v-if="detail.exit_code != null">退出码 {{ detail.exit_code }}</span>
            <span v-if="detail.output_truncated">显示最新输出</span>
            <pre v-if="detail.stdout">{{ detail.stdout }}</pre>
            <pre v-if="detail.stderr" class="run-error">{{ detail.stderr }}</pre>
            <pre v-if="detail.error || detail.result">{{ detail.error || detail.result }}</pre>
            <span v-if="!detail.stdout && !detail.stderr && !detail.result && !detail.error" class="hintline">暂无输出</span>
          </section>
        </li>
      </ul>
    </div>
  </Modal>
</template>

<style scoped>
.background-jobs{max-height:72vh;overflow:auto}
.script-form{padding-bottom:14px;border-bottom:1px solid var(--line-soft)}
.script-fields,.script-actions{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.script-fields .field{flex:1 1 180px}
.script-command{font-family:monospace;resize:vertical}
.script-list{list-style:none;padding:0;margin:0}
.script-list li{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid var(--line-soft)}
.script-row{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;text-align:left;padding:5px;background:none;color:inherit;border:0;cursor:pointer}
.script-row span,.script-output span{color:var(--dim);font-size:12px}
.script-row code{width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.script-output{grid-column:1/-1;min-width:0;overflow-wrap:anywhere}
.script-output span+span{margin-left:10px}
.script-output pre{max-height:240px;overflow:auto;white-space:pre-wrap}
</style>

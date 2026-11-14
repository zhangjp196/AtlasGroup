<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, apiUrl } from '@/api/client'
import SideDrawer from '@/components/SideDrawer.vue'
import WorkspaceFileContent, { type WorkspaceFilePreviewData } from '@/components/WorkspaceFileContent.vue'
import { bytes } from '@/lib/format'
import { useAppStore } from '@/stores/app'

interface FileEntry {
  name: string
  path: string
  type: 'dir' | 'file'
  size: number
}

const props = defineProps<{
  open: boolean
  workspaceId: string
  workspaceName: string
  chatOnly?: boolean
}>()
const emit = defineEmits<{
  (event: 'close'): void
  (event: 'reference', path: string): void
}>()

const app = useAppStore()
const path = ref('.')
const entries = ref<FileEntry[]>([])
const preview = ref<WorkspaceFilePreviewData | null>(null)
const query = ref('')
const showHidden = ref(false)
const loading = ref(false)
const previewLoading = ref(false)
let generation = 0
let listRequest: AbortController | null = null
let previewRequest: AbortController | null = null

const visibleEntries = computed(() => entries.value.filter((entry) => {
  if (!showHidden.value && entry.name.startsWith('.')) return false
  return entry.name.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())
}))
const pathParts = computed(() => path.value === '.' ? [] : path.value.split('/').filter(Boolean))
const downloadUrl = computed(() => preview.value
  ? apiUrl(`/workspace/file/download?workspace_id=${encodeURIComponent(props.workspaceId)}&path=${encodeURIComponent(preview.value.path)}`)
  : '')

watch([() => props.open, () => props.workspaceId], ([open, workspaceId]) => {
  generation += 1
  listRequest?.abort()
  previewRequest?.abort()
  listRequest = null
  previewRequest = null
  query.value = ''
  path.value = '.'
  entries.value = []
  preview.value = null
  loading.value = false
  previewLoading.value = false
  if (open && workspaceId) void loadDirectory('.')
}, { immediate: true })

async function loadDirectory(nextPath: string): Promise<void> {
  const workspaceId = props.workspaceId
  if (!workspaceId) return
  const requestGeneration = ++generation
  listRequest?.abort()
  previewRequest?.abort()
  previewLoading.value = false
  const controller = new AbortController()
  listRequest = controller
  loading.value = true
  path.value = nextPath
  preview.value = null
  try {
    const result = await api<FileEntry[]>(`/workspace/list?workspace_id=${encodeURIComponent(workspaceId)}&path=${encodeURIComponent(nextPath)}`, { signal: controller.signal })
    if (requestGeneration === generation && workspaceId === props.workspaceId) entries.value = result
  } catch (error) {
    if (controller.signal.aborted) return
    if (requestGeneration === generation) {
      entries.value = []
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
  } finally {
    if (requestGeneration === generation) loading.value = false
  }
}

async function openEntry(entry: FileEntry): Promise<void> {
  if (entry.type === 'dir') {
    await loadDirectory(entry.path)
    return
  }
  await loadPreview(entry.path)
}

async function loadPreview(filePath: string): Promise<void> {
  const workspaceId = props.workspaceId
  const requestGeneration = ++generation
  listRequest?.abort()
  loading.value = false
  previewRequest?.abort()
  const controller = new AbortController()
  previewRequest = controller
  preview.value = null
  previewLoading.value = true
  try {
    const result = await api<WorkspaceFilePreviewData>(`/workspace/file/preview?workspace_id=${encodeURIComponent(workspaceId)}&path=${encodeURIComponent(filePath)}`, { signal: controller.signal })
    if (requestGeneration === generation && workspaceId === props.workspaceId) preview.value = result
  } catch (error) {
    if (!controller.signal.aborted && requestGeneration === generation) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
  } finally {
    if (requestGeneration === generation) previewLoading.value = false
  }
}

function refreshCurrent(): void {
  if (preview.value) void loadPreview(preview.value.path)
  else void loadDirectory(path.value)
}

function goToBreadcrumb(index: number): void {
  const next = pathParts.value.slice(0, index + 1).join('/') || '.'
  void loadDirectory(next)
}

function goUp(): void {
  if (!pathParts.value.length) return
  void loadDirectory(pathParts.value.slice(0, -1).join('/') || '.')
}

async function copyPath(): Promise<void> {
  if (!preview.value) return
  try {
    await navigator.clipboard.writeText(preview.value.path)
    app.toast('已复制相对路径')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : '无法访问剪贴板', 'bad')
  }
}

async function revealFile(): Promise<void> {
  if (!preview.value) return
  try {
    await api('/workspace/reveal', { method: 'POST', body: { workspace_id: props.workspaceId, path: preview.value.path } })
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}
</script>

<template>
  <SideDrawer
    :open="open"
    title="会话工作区文件"
    :subtitle="workspaceName || workspaceId"
    width="min(960px, 94vw)"
    @close="emit('close')"
  >
    <div class="session-files">
      <aside class="session-files-list">
        <div class="session-files-location">
          <button class="mini-link" type="button" :disabled="path === '.'" @click="goUp">上级</button>
          <button class="mini-link" type="button" @click="loadDirectory('.')">根目录</button>
          <button class="mini-link" type="button" :disabled="loading || previewLoading" @click="refreshCurrent">刷新</button>
          <template v-for="(part, index) in pathParts" :key="index">
            <span>/</span>
            <button class="mini-link" type="button" @click="goToBreadcrumb(index)">{{ part }}</button>
          </template>
        </div>
        <input v-model="query" class="input session-files-search" placeholder="筛选当前目录" aria-label="筛选当前目录" />
        <label class="session-files-hidden"><input v-model="showHidden" type="checkbox" /> 显示隐藏文件</label>
        <p v-if="chatOnly" class="session-files-note">这是会话托管目录，文件保存在应用数据目录中。</p>
        <div class="session-files-entries" aria-live="polite">
          <div v-if="loading" class="ws-empty">读取目录…</div>
          <button
            v-for="entry in visibleEntries"
            :key="entry.path"
            class="session-files-entry"
            :class="{ directory: entry.type === 'dir', selected: preview?.path === entry.path }"
            type="button"
            :title="entry.path"
            @click="openEntry(entry)"
          >
            <span class="session-files-icon">{{ entry.type === 'dir' ? '▸' : '·' }}</span>
            <span class="session-files-name">{{ entry.name }}</span>
            <small v-if="entry.type === 'file'">{{ bytes(entry.size) }}</small>
          </button>
          <div v-if="!loading && !visibleEntries.length" class="ws-empty">
            {{ entries.length ? '没有匹配的文件' : '此目录为空' }}
          </div>
        </div>
      </aside>

      <section class="session-files-preview">
        <header v-if="preview" class="session-files-preview-head">
          <b :title="preview.path">{{ preview.path }}</b>
          <span>{{ bytes(preview.size) }}</span>
          <button class="mini-link" type="button" title="将文件路径插入消息输入框" @click="emit('reference', preview.path)">引用到输入框</button>
          <button class="mini-link" type="button" @click="copyPath">复制路径</button>
          <button class="mini-link" type="button" @click="revealFile">定位</button>
          <a class="mini-link" :href="downloadUrl" target="_blank" rel="noopener">下载</a>
        </header>
        <div v-if="previewLoading" class="ws-empty">读取文件…</div>
        <div v-else-if="!preview" class="ws-empty">选择文件查看预览</div>
        <WorkspaceFileContent v-else :preview="preview" :workspace-id="workspaceId" />
      </section>
    </div>
    <template #footer>
      <span class="spacer" />
      <button class="btn" type="button" @click="emit('close')">关闭</button>
    </template>
  </SideDrawer>
</template>

<style scoped>
.session-files{display:grid;grid-template-columns:minmax(220px,32%) minmax(0,1fr);height:100%;min-height:0;gap:12px}
.session-files-list,.session-files-preview{display:flex;min-width:0;min-height:0;flex-direction:column;border:1px solid var(--line-soft);border-radius:10px;overflow:hidden}
.session-files-location{display:flex;align-items:center;gap:4px;min-height:38px;padding:0 9px;border-bottom:1px solid var(--line-soft);overflow:auto;white-space:nowrap}
.session-files-search{box-sizing:border-box;width:calc(100% - 16px);margin:8px}
.session-files-hidden{display:flex;align-items:center;gap:6px;padding:0 10px 8px;color:var(--faint);font-size:11px}
.session-files-note{margin:0;padding:0 10px 8px;color:var(--faint);font-size:10px}
.session-files-entries{flex:1;overflow:auto;padding:4px}
.session-files-entry{display:flex;align-items:center;gap:7px;width:100%;padding:6px 8px;border:0;border-radius:6px;background:transparent;color:var(--text);text-align:left;font:12px/1.4 var(--sans);cursor:pointer}
.session-files-entry:hover,.session-files-entry.selected{background:var(--bg-2)}
.session-files-entry.directory .session-files-icon{color:var(--accent)}
.session-files-icon{width:12px;color:var(--faint)}
.session-files-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.session-files-entry small,.session-files-preview-head span{color:var(--faint);font-size:10px}
.session-files-preview-head{display:flex;align-items:center;gap:9px;min-height:38px;padding:0 10px;border-bottom:1px solid var(--line-soft)}
.session-files-preview-head b{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:11px var(--mono)}
@media(max-width:700px){.session-files{grid-template-columns:1fr;grid-template-rows:minmax(190px,42%) minmax(0,1fr)}.session-files-preview :deep(.workspace-file-media iframe){min-height:240px}}
</style>

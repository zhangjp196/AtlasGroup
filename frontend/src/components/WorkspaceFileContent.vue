<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { apiUrl } from '@/api/client'

export interface WorkspaceFilePreviewData {
  path: string
  mime?: string
  kind?: string
  html?: string
  text?: string
  rows?: string[][]
  size: number
  truncated?: boolean
}

const props = defineProps<{
  preview: WorkspaceFilePreviewData
  workspaceId: string
  editing?: boolean
  modelValue?: string
}>()
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()

const fileExt = computed(() => /\.([a-z0-9]+)$/i.exec(props.preview.path)?.[1]?.toLowerCase() || '')
const tableRows = computed(() => {
  if (props.preview.rows) return props.preview.rows
  if (!['csv', 'tsv'].includes(fileExt.value) || props.preview.text === undefined) return []
  const delimiter = fileExt.value === 'tsv' ? '\t' : ','
  return props.preview.text.split(/\r?\n/).filter(Boolean).slice(0, 300).map((line) => line.split(delimiter).slice(0, 40))
})
const mediaUrl = computed(() => apiUrl(
  `/workspace/file/download?workspace_id=${encodeURIComponent(props.workspaceId)}&path=${encodeURIComponent(props.preview.path)}`,
))
const safeHtml = computed(() => DOMPurify.sanitize(props.preview.html || '', {
  WHOLE_DOCUMENT: false,
  ADD_TAGS: ['style'],
  FORBID_TAGS: ['script', 'base', 'meta', 'iframe', 'object', 'embed', 'form'],
  FORBID_ATTR: ['onerror', 'onclick', 'onload'],
}))
</script>

<template>
  <textarea
    v-if="editing"
    class="workspace-file-editor"
    :value="modelValue || ''"
    spellcheck="false"
    :aria-label="`编辑 ${preview.path}`"
    @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
  />
  <div v-else-if="preview.kind === 'image'" class="workspace-file-media"><img :src="mediaUrl" :alt="preview.path" /></div>
  <div v-else-if="preview.kind === 'pdf'" class="workspace-file-media"><iframe :src="mediaUrl" :title="preview.path" /></div>
  <div v-else-if="['audio', 'video'].includes(preview.kind || '')" class="workspace-file-media">
    <video v-if="preview.kind === 'video'" :src="mediaUrl" controls />
    <audio v-else :src="mediaUrl" controls />
  </div>
  <div v-else-if="tableRows.length" class="workspace-file-table">
    <table><tbody>
      <tr v-for="(row, rowIndex) in tableRows" :key="rowIndex">
        <component :is="rowIndex === 0 ? 'th' : 'td'" v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</component>
      </tr>
    </tbody></table>
  </div>
  <iframe v-else-if="preview.html" class="workspace-file-html" :srcdoc="safeHtml" :title="preview.path" sandbox="" />
  <pre v-else-if="preview.text !== undefined" class="workspace-file-text">{{ preview.text }}</pre>
  <div v-else class="workspace-file-empty">此格式暂无在线预览，可下载后打开。</div>
  <p v-if="preview.truncated" class="workspace-file-note">预览内容已截断，完整文件不能在线编辑。</p>
</template>

<style scoped>
.workspace-file-editor{box-sizing:border-box;flex:1;min-height:280px;width:100%;resize:vertical;border:0;outline:0;padding:12px;background:var(--code-bg);color:var(--text);font:11.5px/1.6 var(--mono);tab-size:2;white-space:pre;overflow:auto}
.workspace-file-text{flex:1;min-height:0;overflow:auto;margin:0;padding:12px;background:var(--code-bg);color:var(--text);font:11px/1.6 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere}
.workspace-file-media{display:grid;flex:1;min-height:0;place-items:center;overflow:auto;padding:10px}
.workspace-file-media img,.workspace-file-media video{max-width:100%;max-height:100%;object-fit:contain}
.workspace-file-media iframe{width:100%;height:100%;min-height:390px;border:0;background:#fff}
.workspace-file-media audio{width:min(100%,420px)}
.workspace-file-html{flex:1;width:100%;min-height:0;border:0;background:#fff}
.workspace-file-table{flex:1;overflow:auto;padding:8px}
.workspace-file-table table{border-collapse:collapse;font-size:11px}
.workspace-file-table th,.workspace-file-table td{border:1px solid var(--line);padding:4px 7px;text-align:left;white-space:pre-wrap}
.workspace-file-table th{background:var(--bg-2)}
.workspace-file-empty{display:grid;flex:1;place-items:center;min-height:180px;color:var(--faint);font-size:11px}
.workspace-file-note{margin:0;padding:4px 10px;color:var(--faint);font-size:10px}
</style>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '@/api/client'
import Modal from '@/components/Modal.vue'
import { openExternal } from '@/composables/useNative'
import type { ConversationLink } from '@/lib/conversationLinks'
import Markdown from './Markdown.vue'

interface FilePreview {
  name?: string
  path?: string
  mime?: string
  kind?: string
  text?: string
  html?: string
  truncated?: boolean
}

const props = defineProps<{ link: ConversationLink | null }>()
const emit = defineEmits<{ (event: 'close'): void; (event: 'link-click', mouse: MouseEvent): void }>()
const preview = ref<FilePreview | null>(null)
const loading = ref(false)
const error = ref('')
const mediaError = ref(false)

watch(() => props.link, async (link, _, onCleanup) => {
  preview.value = null
  error.value = ''
  mediaError.value = false
  loading.value = link?.kind === 'file'
  if (link?.kind !== 'file') return
  const request = new AbortController()
  onCleanup(() => request.abort())
  try {
    const result = await api<FilePreview>(link.preview, { signal: request.signal })
    if (!request.signal.aborted) preview.value = result
  } catch (err) {
    if (!request.signal.aborted) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}, { immediate: true })

const extension = computed(() => {
  const path = preview.value?.name || preview.value?.path || (props.link ? new URL(props.link.url, location.origin).pathname : '')
  return /\.([a-z0-9]+)$/i.exec(path)?.[1]?.toLowerCase() || ''
})
const kind = computed(() => {
  const mime = preview.value?.mime || ''
  if (mime.startsWith('video/') || /^(mp4|webm|mov|m4v|ogv)$/.test(extension.value)) return 'video'
  if (mime.startsWith('audio/') || /^(mp3|wav|ogg|m4a|aac|flac|opus)$/.test(extension.value)) return 'audio'
  if (preview.value?.kind === 'image' || /^(png|jpe?g|gif|webp|bmp|avif)$/.test(extension.value)) return 'image'
  if (extension.value === 'pdf') return 'pdf'
  if (props.link?.kind === 'web') return 'web'
  if (preview.value?.html && preview.value.kind === 'office' || /^(html?|xhtml|svg)$/.test(extension.value)) return 'document'
  if (/^(md|markdown|mdx)$/.test(extension.value)) return 'markdown'
  if (/^(csv|tsv)$/.test(extension.value) && preview.value?.text) return 'table'
  return 'text'
})
const rows = computed(() => kind.value === 'table'
  ? (preview.value?.text || '').split(/\r?\n/).filter(Boolean).slice(0, 200)
    .map((line) => line.split(extension.value === 'tsv' ? '\t' : ',').slice(0, 20))
  : [])
const documentHtml = computed(() => {
  const content = preview.value?.html || preview.value?.text || ''
  return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'"><style>
  body{font:15px/1.65 system-ui,sans-serif;margin:24px;color:#182333;background:#fff;overflow-wrap:anywhere}
  table{border-collapse:collapse} th,td{border:1px solid #cbd5e1;padding:5px 9px} th{background:#eef2f6}
  img,svg{max-width:100%} pre{white-space:pre-wrap}
  </style>${content}`
})
</script>

<template>
  <Modal :open="Boolean(link)" :title="link?.title || '链接预览'" width="min(1020px, 94vw)" @close="emit('close')">
    <div v-if="link" class="link-preview" @click.capture="emit('link-click', $event)" @auxclick.capture="emit('link-click', $event)">
      <div v-if="loading" class="empty" role="status">正在加载预览…</div>
      <div v-else-if="error" class="run-error" role="alert">{{ error }}</div>
      <template v-else>
        <template v-if="kind === 'web'">
          <div class="link-preview-address" :title="link.url">{{ link.url }}</div>
          <iframe :key="link.url" :src="link.url" :title="link.title" sandbox="" referrerpolicy="no-referrer" />
          <p class="hintline">如果网页禁止嵌入或需要运行脚本，可以在浏览器中打开。</p>
        </template>
        <template v-else-if="kind === 'video' || kind === 'audio'">
          <video v-if="kind === 'video'" :key="link.url" :src="link.url" controls playsinline preload="metadata" @error="mediaError = true" />
          <audio v-else :key="link.url" :src="link.url" controls preload="metadata" @error="mediaError = true" />
          <p v-if="mediaError" class="hintline">当前播放器无法播放此文件，请下载或在浏览器中打开。</p>
        </template>
        <img v-else-if="kind === 'image'" :src="link.url" :alt="link.title" />
        <iframe v-else-if="kind === 'pdf'" :src="link.url" :title="link.title" :sandbox="link.kind === 'web' ? '' : undefined" referrerpolicy="no-referrer" />
        <iframe v-else-if="kind === 'document' && (preview?.html || preview?.text)" :srcdoc="documentHtml" :title="link.title" sandbox="" />
        <Markdown v-else-if="kind === 'markdown'" class="reply-text" :text="preview?.text" />
        <div v-else-if="kind === 'table'" class="reply-text">
          <div class="md-table-wrap">
            <table>
              <tbody>
                <tr v-for="(row, index) in rows" :key="index">
                  <component :is="index === 0 ? 'th' : 'td'" v-for="(cell, col) in row" :key="col">{{ cell }}</component>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <pre v-else-if="preview?.text" class="file-preview">{{ preview.text }}</pre>
        <div v-else class="empty">此格式可下载后查看。</div>
        <p v-if="preview?.truncated" class="hintline">预览已截断，下载原文件可查看完整内容。</p>
      </template>
    </div>
    <template #footer>
      <button v-if="link?.kind === 'web'" class="btn" type="button" @click="openExternal(link.url)">在浏览器打开</button>
      <a v-else-if="link && !error" class="btn" :href="link.download" download>下载原文件</a>
      <button class="btn" type="button" @click="emit('close')">关闭</button>
    </template>
  </Modal>
</template>

<style scoped>
.link-preview{min-width:0;max-height:68vh;overflow:auto}
.link-preview iframe{display:block;width:100%;height:56vh;border:1px solid var(--line-soft);border-radius:8px;background:white}
.link-preview img,.link-preview video{display:block;max-width:100%;max-height:62vh;margin:auto}
.link-preview audio{width:100%;margin:16px 0}
.link-preview-address{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:8px;color:var(--dim)}
</style>

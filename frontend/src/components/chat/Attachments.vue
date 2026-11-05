<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Attachment } from '@/api/types'
import { bytes } from '@/lib/format'
import Modal from '@/components/Modal.vue'
import Markdown from './Markdown.vue'

const props = defineProps<{ items?: Attachment[] }>()

interface PreviewData {
  id?: string
  name?: string
  mime?: string
  size?: number
  kind?: string
  text?: string
  truncated?: boolean
  url?: string
}

const open = ref(false)
const loading = ref(false)
const error = ref('')
const current = ref<Attachment | null>(null)
const preview = ref<PreviewData | null>(null)

const ext = computed(() => {
  const name = String(current.value?.name || preview.value?.name || '')
  return name.includes('.') ? name.slice(name.lastIndexOf('.') + 1).toLowerCase() : ''
})
const isMarkdown = computed(() => ['md', 'markdown', 'mdx'].includes(ext.value))
const isHtml = computed(() => ['html', 'htm', 'xhtml'].includes(ext.value))
const isPdf = computed(() => ext.value === 'pdf' || current.value?.kind === 'pdf')
const isTable = computed(() => ['csv', 'tsv'].includes(ext.value))
const isJson = computed(() => ['json', 'jsonl'].includes(ext.value))
const originalUrl = computed(() => preview.value?.url || current.value?.url || '')
const rows = computed(() => {
  if (!isTable.value) return []
  const delimiter = ext.value === 'tsv' ? '\t' : ','
  return String(preview.value?.text || '')
    .split(/\r?\n/)
    .filter((line) => line.length)
    .slice(0, 200)
    .map((line) => line.split(delimiter).slice(0, 20))
})
const prettyJson = computed(() => {
  const text = String(preview.value?.text || '')
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
})

function isImage(item: Attachment): boolean {
  return String(item.mime || '').startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(item.name || '')
}

async function openPreview(item: Attachment): Promise<void> {
  if (isImage(item) || !item.url) return
  current.value = item
  preview.value = null
  error.value = ''
  open.value = true
  loading.value = true
  try {
    const res = await fetch(`${item.url}/preview`, { headers: { Accept: 'application/json' } })
    const payload = await res.json().catch(() => null)
    if (!res.ok) throw new Error(payload?.detail || `HTTP ${res.status}`)
    preview.value = payload?.data || {}
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div v-if="items?.length" class="attachments">
    <template v-for="(item, index) in items" :key="item.id || index">
      <a v-if="isImage(item)" class="att-image" :href="item.url || item.preview" target="_blank" rel="noreferrer">
        <img :src="item.preview || item.url" :alt="item.name || ''" loading="lazy" />
      </a>
      <button v-else class="att-file" type="button" :title="item.name" @click="openPreview(item)">
        <span class="att-icon">▤</span>
        <span class="att-main"><b>{{ item.name || '附件' }}</b><small>{{ bytes(item.size) }} · 点击预览</small></span>
      </button>
    </template>
  </div>

  <Modal :open="open" :title="current?.name || '附件预览'" @close="open = false">
    <div v-if="loading" class="empty">加载预览…</div>
    <div v-else-if="error" class="run-error">{{ error }}</div>
    <template v-else-if="preview">
      <iframe v-if="isPdf" class="settings-embed" :src="originalUrl" :title="preview.name || 'PDF'" />
      <iframe v-else-if="isHtml" class="settings-embed" sandbox="" :srcdoc="preview.text || ''" :title="preview.name || 'HTML'" />
      <div v-else-if="isMarkdown" class="att-md"><Markdown :text="preview.text" /></div>
      <div v-else-if="isTable" class="md-table-wrap">
        <table>
          <tbody>
            <tr v-for="(row, rowIndex) in rows" :key="rowIndex">
              <component :is="rowIndex === 0 ? 'th' : 'td'" v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</component>
            </tr>
          </tbody>
        </table>
      </div>
      <pre v-else-if="isJson" class="file-preview">{{ prettyJson }}</pre>
      <pre v-else class="file-preview">{{ preview.text || '没有可预览的文本，请打开原文件。' }}</pre>
      <div v-if="preview.truncated" class="hintline">预览已截断，请打开原文件查看完整内容。</div>
    </template>
    <template #footer>
      <a v-if="originalUrl" class="btn" :href="originalUrl" target="_blank" rel="noreferrer">打开原文件</a>
      <button class="btn" @click="open = false">关闭</button>
    </template>
  </Modal>
</template>

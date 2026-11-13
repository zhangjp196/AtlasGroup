<script setup lang="ts">
import type { Attachment } from '@/api/types'
import { bytes } from '@/lib/format'

defineProps<{ items?: Attachment[] }>()

function isImage(item: Attachment): boolean {
  if (item.mime === 'image/svg+xml' || /\.svg$/i.test(item.name || '')) return false
  return String(item.mime || '').startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(item.name || '')
}
</script>

<template>
  <div v-if="items?.length" class="attachments">
    <template v-for="(item, index) in items" :key="item.id || index">
      <a v-if="isImage(item)" class="att-image" :href="item.url || item.preview" :aria-label="item.name || '预览图片'">
        <img :src="item.preview || item.url" :alt="item.name || ''" loading="lazy" />
      </a>
      <component :is="item.url ? 'a' : 'span'" v-else class="att-file" :href="item.url || undefined"
        :title="item.name" :aria-label="item.name || '预览附件'">
        <span class="att-icon">▤</span>
        <span class="att-main"><b>{{ item.name || '附件' }}</b><small>{{ bytes(item.size) }}<template v-if="item.url"> · 点击预览</template></small></span>
      </component>
    </template>
  </div>
</template>

<style scoped>
.attachments{display:flex;align-items:flex-start;flex-wrap:wrap;gap:6px;margin-top:4px}
.att-image{display:block;max-width:100%;border-radius:8px;overflow:hidden}
.att-image img{display:block;max-width:min(300px,100%);max-height:220px;object-fit:contain}
.att-main{display:grid;min-width:0;gap:2px}
</style>

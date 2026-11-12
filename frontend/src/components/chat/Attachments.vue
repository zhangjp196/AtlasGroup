<script setup lang="ts">
import type { Attachment } from '@/api/types'
import { bytes } from '@/lib/format'

defineProps<{ items?: Attachment[] }>()

function isImage(item: Attachment): boolean {
  return String(item.mime || '').startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(item.name || '')
}
</script>

<template>
  <div v-if="items?.length" class="attachments">
    <template v-for="(item, index) in items" :key="item.id || index">
      <a v-if="isImage(item)" class="att-image" :href="item.url || item.preview" :aria-label="item.name || '预览图片'">
        <img :src="item.preview || item.url" :alt="item.name || ''" loading="lazy" />
      </a>
      <a v-else class="att-file" :href="item.url" :title="item.name" :aria-label="item.name || '预览附件'">
        <span class="att-icon">▤</span>
        <span class="att-main"><b>{{ item.name || '附件' }}</b><small>{{ bytes(item.size) }} · 点击预览</small></span>
      </a>
    </template>
  </div>
</template>

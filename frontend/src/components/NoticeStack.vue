<script setup lang="ts">
import type { NoticeItem } from '@/stores/notice'
import { useNoticeStore } from '@/stores/notice'

const notice = useNoticeStore()
const emit = defineEmits<{ (e: 'action', action: string, item: NoticeItem): void }>()

function run(item: NoticeItem): void {
  if (!item.action) return
  emit('action', item.action, item)
  notice.remove(item.id)
}
</script>

<template>
  <div class="notice-stack" aria-live="polite">
    <TransitionGroup name="notice">
      <div v-for="item in notice.items" :key="item.id" class="notice" :class="item.tone">
        <div class="notice-head">
          <b>{{ item.title }}</b>
          <button class="notice-close" type="button" title="关闭" aria-label="关闭" @click="notice.remove(item.id)">×</button>
        </div>
        <p v-if="item.message">
          {{ item.message }}<span v-if="Number(item.count || 1) > 1" class="notice-merge"> · {{ item.count }} 次</span>
        </p>
        <button v-if="item.action" class="notice-action" type="button" @click="run(item)">
          {{ item.actionLabel || '查看' }}
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

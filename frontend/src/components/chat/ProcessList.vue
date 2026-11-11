<script setup lang="ts">
import type { ProcessEntry } from '@/lib/trace'
import { durationText, firstLine } from '@/lib/format'
import Markdown from './Markdown.vue'

defineProps<{ entries: ProcessEntry[]; live?: boolean }>()
</script>

<template>
  <div class="proc-list">
    <template v-for="(entry, index) in entries" :key="`${entry.kind}-${entry.id || index}`">
      <details v-if="entry.kind === 'thinking'" class="think-step" :open="live && index === entries.length - 1">
        <summary>
          <span class="proc-caret">›</span>
          <b>think</b>
          <span class="think-brief">{{ firstLine(entry.text || '', 120) }}</span>
          <span v-if="live && index === entries.length - 1" class="live-dots"><i /><i /><i /></span>
        </summary>
        <div class="think-body">{{ entry.text }}</div>
      </details>

      <div v-else-if="entry.kind === 'tool'" class="proc-tool">
        <div class="tool-line">
          <div class="tool-head">
            <span class="tool-ico">▤</span>
            <span class="tool-name">{{ entry.name || 'tool' }}</span>
            <span v-if="entry.duration_ms" class="tool-ms">{{ durationText(entry.duration_ms) }}</span>
            <span v-if="entry.status === 'running' || entry.status === 'preparing'" class="sp" />
            <span v-else :class="entry.ok === false ? 'fail' : 'ok'">{{ entry.ok === false ? '!' : '' }}</span>
          </div>
          <details v-if="entry.display?.diff || entry.args" class="tool-args">
            <summary>{{ entry.display?.diff ? '改动' : '参数' }}</summary>
            <pre>{{ entry.display?.diff || JSON.stringify(entry.args, null, 2) }}</pre>
          </details>
        </div>
      </div>

      <div v-else-if="entry.kind === 'text'" class="proc-text">
        <Markdown :text="entry.text" />
        <span v-if="live && index === entries.length - 1" class="streaming-caret" />
      </div>

      <div v-else class="proc-note">
        <b>{{ entry.kind === 'retry' ? '自动重试' : '上下文压缩' }}</b>
        <template v-if="entry.text"> · {{ firstLine(entry.text, 160) }}</template>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ProcessEntry } from '@/lib/trace'
import { durationText, firstLine } from '@/lib/format'
import Markdown from './Markdown.vue'

const props = defineProps<{ entries: ProcessEntry[]; final?: ProcessEntry | null; live?: boolean }>()

function toolRunning(entry: ProcessEntry): boolean {
  return entry.status === 'running' || entry.status === 'preparing'
}

function toolTarget(entry: ProcessEntry): string {
  const args = entry.args && typeof entry.args === 'object'
    ? entry.args as Record<string, unknown>
    : {}
  const target = entry.display?.path || args.path || args.cmd || args.command
  return typeof target === 'string' ? firstLine(target, 100) : ''
}

const groups = computed(() => {
  const groups: Array<{ start: number; entries: ProcessEntry[]; activity: boolean; tools: number }> = []
  for (const [index, entry] of props.entries.entries()) {
    const activity = !props.live && (entry.kind === 'thinking'
      || (entry.kind === 'tool' && entry.ok !== false && !toolRunning(entry)))
    const previous = groups[groups.length - 1]
    if (activity && previous?.activity) {
      previous.entries.push(entry)
      previous.tools += Number(entry.kind === 'tool')
    } else {
      groups.push({ start: index, entries: [entry], activity, tools: Number(entry.kind === 'tool') })
    }
  }
  return groups.map((group) => ({ ...group, folded: group.activity && group.entries.length > 1 }))
})
</script>

<template>
  <div class="reply-flow">
    <component
      :is="group.folded ? 'details' : 'div'"
      v-for="group in groups"
      :key="group.start"
      :class="group.folded ? 'activity-fold' : 'reply-entry'"
    >
      <summary v-if="group.folded" class="activity-summary">
        <span class="activity-caret" aria-hidden="true" />
        <span>{{ group.tools ? `执行了 ${group.tools} 次工具调用` : '思考过程' }}</span>
      </summary>

      <div :class="{ 'activity-list': group.folded }">
        <template v-for="(entry, index) in group.entries" :key="index">
          <details v-if="entry.kind === 'thinking'" class="activity-row">
            <summary class="activity-summary">
              <span class="activity-caret" aria-hidden="true" />
              <span>{{ live && group.start === entries.length - 1 ? '正在思考…' : '思考过程' }}</span>
            </summary>
            <div class="activity-detail">{{ entry.text }}</div>
          </details>

          <component
            :is="entry.display?.diff || entry.args ? 'details' : 'div'"
            v-else-if="entry.kind === 'tool'"
            class="activity-row"
          >
            <component
              :is="entry.display?.diff || entry.args ? 'summary' : 'div'"
              class="activity-summary"
              :class="{ 'activity-failed': entry.ok === false }"
            >
              <span v-if="entry.display?.diff || entry.args" class="activity-caret" aria-hidden="true" />
              <span v-else class="activity-dot" aria-hidden="true" />
              <span class="activity-tool">{{ entry.name || '工具' }}</span>
              <span class="activity-target">{{ toolTarget(entry) }}</span>
              <span v-if="entry.ok === false" class="activity-status">失败</span>
              <span v-else-if="toolRunning(entry)" class="activity-status">{{ live ? '运行中' : '未完成' }}</span>
              <span v-else-if="entry.duration_ms" class="activity-status">{{ durationText(entry.duration_ms) }}</span>
            </component>
            <pre v-if="entry.display?.diff || entry.args" class="activity-detail">{{ entry.display?.diff || JSON.stringify(entry.args, null, 2) }}</pre>
          </component>

          <div
            v-else-if="entry.kind === 'text'"
            class="reply-text"
            :class="{ 'reply-progress': entry !== final, streaming: live && group.start === entries.length - 1 }"
          >
            <Markdown :text="entry.text" />
          </div>

          <div v-else class="activity-note">
            <b>{{ entry.kind === 'retry' ? '自动重试' : '上下文压缩' }}</b>
            <span v-if="entry.text">{{ entry.text }}</span>
          </div>
        </template>
      </div>
    </component>
  </div>
</template>

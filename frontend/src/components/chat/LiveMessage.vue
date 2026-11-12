<script setup lang="ts">
import { computed } from 'vue'
import Avatar from '@/components/Avatar.vue'
import type { LiveEntry } from '@/composables/useChat'
import { groupBySpeaker, type LiveSpeaker } from '@/lib/live'
import ProcessList from './ProcessList.vue'

const props = defineProps<{
  entries: LiveEntry[]
  speakers: Record<string, LiveSpeaker>
  current?: string
  started?: boolean
  streaming?: boolean
  waitSeconds?: number
  error?: string
}>()

// 群聊一轮里主持 / 成员会依次开口：按「发言者」切成连续的块，每块独立渲染
// 头像和名字。以前整轮共用一个气泡，角色名会中途跳变、几个人的正文混在一起，
// 看起来就像「先说助手，再变成指定角色，然后一堆内容一起冒出来」。
const groups = computed(() => {
  const blocks = groupBySpeaker(props.entries)
  return blocks.map((group, index) => {
    const last = group.entries[group.entries.length - 1]
    return {
      key: `${group.speaker || 'unknown'}-${index}`,
      meta: props.speakers[group.speaker] || null,
      entries: group.entries,
      final: last?.kind === 'text' ? last : null,
      active: props.streaming && index === blocks.length - 1,
    }
  })
})

// start 事件到达前也已经有「预期发言者」（会话绑定角色 / 群组主持 / 被 @ 成员），
// 所以准备阶段就能显示正确的名字和头像，不再退回兜底的「助手」。
const pending = computed<LiveSpeaker | null>(
  () => props.speakers[String(props.current || '')] || null,
)
const waitingText = computed(() => (props.started ? '正在思考…' : '正在准备这一轮…'))
</script>

<template>
  <template v-for="group in groups" :key="group.key">
    <div class="msg assistant">
      <Avatar :name="group.meta?.name || '助手'" :avatar="group.meta?.avatar" round />
      <div class="msg-body">
        <div class="msg-who">
          <b>{{ group.meta?.name || '助手' }}</b>
          <span v-if="group.meta?.sub" class="msg-role">{{ group.meta.sub }}</span>
        </div>
        <ProcessList
          :entries="group.entries"
          :final="group.final"
          :live="group.active"
        />
      </div>
    </div>
  </template>

  <div v-if="!groups.length" class="msg assistant live-waiting">
    <Avatar :name="pending?.name || '正在启动…'" :avatar="pending?.avatar" round />
    <div class="msg-body">
      <div class="msg-who">
        <b>{{ pending?.name || '正在启动…' }}</b>
        <span v-if="pending?.sub" class="msg-role">{{ pending.sub }}</span>
      </div>
      <div class="live-wait" role="status">
        <span class="live-dots"><i /><i /><i /></span>
        <span>{{ waitingText }}</span>
        <span v-if="waitSeconds" class="live-wait-seconds">{{ waitSeconds }}s</span>
      </div>
    </div>
  </div>

  <div v-if="error" class="run-error">{{ error }}</div>
</template>

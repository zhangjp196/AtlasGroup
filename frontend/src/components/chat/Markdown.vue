<script setup lang="ts">
import { inject, onBeforeUnmount, ref, watch } from 'vue'
import { conversationImageSource } from '@/lib/conversationLinks'
import { renderMarkdown } from '@/lib/markdown'

const props = defineProps<{ text?: string }>()
const html = ref('')
const resolveImage = inject(conversationImageSource, undefined)
let frame = 0

// 流式输出时每个 token 都会改 text；合并到每帧最多渲染一次，避免反复解析整段 Markdown。
watch(() => props.text, () => {
  if (frame) return
  frame = requestAnimationFrame(() => {
    frame = 0
    html.value = renderMarkdown(props.text, resolveImage)
  })
}, { immediate: true })

onBeforeUnmount(() => {
  if (frame) cancelAnimationFrame(frame)
})
</script>

<template>
  <div class="md" v-html="html" />
</template>

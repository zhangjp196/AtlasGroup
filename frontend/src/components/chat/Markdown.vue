<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { renderMarkdown } from '@/lib/markdown'

const props = defineProps<{ text?: string }>()
const html = ref('')
let frame = 0

function render(): void {
  html.value = renderMarkdown(props.text)
}

// 流式输出时每个 token 都会改 text；合并到每帧最多渲染一次，避免反复解析整段 Markdown。
watch(() => props.text, () => {
  if (frame) return
  frame = requestAnimationFrame(() => {
    frame = 0
    render()
  })
}, { immediate: true })

onBeforeUnmount(() => {
  if (frame) cancelAnimationFrame(frame)
})
</script>

<template>
  <div class="md" v-html="html" />
</template>

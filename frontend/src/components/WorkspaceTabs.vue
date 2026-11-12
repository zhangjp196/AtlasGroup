<script setup lang="ts">
defineProps<{
  tabs: { key: string; label: string; closable?: boolean }[]
  active: string
}>()
const emit = defineEmits<{
  select: [key: string]
  close: [key: string]
}>()

function move(event: KeyboardEvent): void {
  const buttons = Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]'))
  const index = buttons.indexOf(event.target as HTMLButtonElement)
  if (index < 0) return
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
    : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
  buttons[next]?.focus()
  buttons[next]?.click()
}
</script>

<template>
  <div class="workspace-tabs" role="tablist" aria-label="工作区标签"
    @keydown.left.prevent="move" @keydown.right.prevent="move"
    @keydown.home.prevent="move" @keydown.end.prevent="move">
    <div v-for="tab in tabs" :key="tab.key" class="workspace-tab" :class="{ active: tab.key === active }">
      <button type="button" role="tab" :id="`workspace-tab-${tab.key}`" :aria-selected="tab.key === active"
        :aria-controls="tab.key === active ? 'workspace-content' : undefined" :tabindex="tab.key === active ? 0 : -1"
        :title="tab.label" @click="emit('select', tab.key)">{{ tab.label }}</button>
      <button v-if="tab.closable" type="button" class="workspace-tab-close" :aria-label="`关闭标签 ${tab.label}`"
        @click="emit('close', tab.key)">×</button>
    </div>
  </div>
</template>

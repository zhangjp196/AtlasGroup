<script setup lang="ts">
defineProps<{ items: Array<{ content: string; status: string }> }>()
</script>

<template>
  <div v-if="items.length" class="task-card">
    <div class="task-head">
      <b>任务</b>
      <span>{{ items.filter((i) => i.status === 'in_progress').length }} 进行中 · {{ items.filter((i) => i.status !== 'completed' && i.status !== 'in_progress').length }} 待处理</span>
    </div>
    <div v-for="(item, index) in items" :key="index" class="task-item" :class="{ done: item.status === 'completed', doing: item.status === 'in_progress' }">
      <span class="mark">{{ item.status === 'completed' ? '✓' : '' }}</span>
      <span>{{ item.content }}</span>
    </div>
  </div>
</template>

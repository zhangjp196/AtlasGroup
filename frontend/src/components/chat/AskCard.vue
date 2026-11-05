<script setup lang="ts">
import { ref } from 'vue'
import type { Question } from '@/api/types'

const props = defineProps<{ question: Question }>()
const emit = defineEmits<{ (e: 'answer', id: string, payload: Record<string, unknown>): void }>()
const other = ref('')

function pick(option: string | { label?: string; value?: string }): void {
  const value = typeof option === 'string' ? option : (option.value || option.label || '')
  emit('answer', props.question.id, { selected: [String(value)] })
}
</script>

<template>
  <div class="ask-card">
    <div class="approval-head"><b>需要你决定</b></div>
    <div class="ask-question">{{ question.question }}</div>
    <div v-if="question.options?.length" class="ask-options">
      <button v-for="(option, index) in question.options" :key="index" class="btn" @click="pick(option)">
        {{ typeof option === 'string' ? option : (option.label || option.value) }}
      </button>
    </div>
    <div v-else class="ask-other">
      <input v-model="other" class="input" placeholder="输入回答…" @keyup.enter="other && emit('answer', question.id, { other })" />
      <button class="btn primary" :disabled="!other" @click="emit('answer', question.id, { other })">回答</button>
    </div>
    <div class="approval-actions">
      <button class="btn ghost" @click="emit('answer', question.id, { ignored: true })">跳过</button>
    </div>
  </div>
</template>

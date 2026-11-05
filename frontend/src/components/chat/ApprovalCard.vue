<script setup lang="ts">
import type { Approval } from '@/api/types'

defineProps<{ approval: Approval }>()
const emit = defineEmits<{ (e: 'decide', id: string, action: string): void }>()
</script>

<template>
  <div class="approval-card" :class="`risk-${approval.risk || 'medium'}`" :data-status="approval.status">
    <div class="approval-head">
      <b>需要确认</b>
      <span>{{ approval.tool || approval.name || '工具' }}</span>
      <span class="tag">{{ approval.risk === 'high' ? '高风险' : approval.risk === 'low' ? '低风险' : '中风险' }}</span>
    </div>
    <div v-if="approval.note" class="approval-note">{{ approval.note }}</div>
    <div v-if="!approval.status || approval.status === 'pending'" class="approval-actions">
      <button class="btn primary" @click="emit('decide', approval.id, 'approve_once')">允许一次</button>
      <button class="btn" @click="emit('decide', approval.id, 'approve_session')">本会话允许</button>
      <button class="btn" @click="emit('decide', approval.id, 'approve_always')">始终允许</button>
      <button class="btn danger" @click="emit('decide', approval.id, 'deny')">拒绝</button>
    </div>
    <div v-else class="approval-done">{{ approval.status === 'approved' ? '已允许' : approval.status === 'denied' ? '已拒绝' : approval.status }}</div>
  </div>
</template>

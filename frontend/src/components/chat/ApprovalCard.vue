<script setup lang="ts">
import { computed } from 'vue'
import type { Approval } from '@/api/types'
import { useDataStore } from '@/stores/data'

const props = defineProps<{ approval: Approval }>()
const emit = defineEmits<{ (e: 'decide', id: string, action: string): void }>()
const data = useDataStore()
// 共享群组的 team_id 是长期实体，永久授权覆盖整个群；会话私有参与角色只覆盖本会话。
const teamKind = computed(() => {
  const team = data.teams.find((item) => item.id === props.approval.team_id)
  return String(team?.kind || '')
})
const teamScopeLabel = computed(() => (teamKind.value === 'group' ? '永久（本群组）' : '永久（本会话）'))
const teamScopeName = computed(() => (teamKind.value === 'group' ? '群组' : '会话'))
</script>

<template>
  <div class="approval-card" :class="`risk-${approval.risk || 'medium'}`" :data-status="approval.status">
    <div class="approval-head">
      <b>需要确认</b>
      <span>{{ approval.tool || approval.name || '工具' }}</span>
      <span class="tag">{{ approval.risk === 'high' ? '高风险' : approval.risk === 'low' ? '低风险' : '中风险' }}</span>
    </div>
    <div v-if="approval.note" class="approval-note">{{ approval.note }}</div>
    <div v-if="approval.role_name || approval.team_name" class="approval-scope">
      授权对象：<b>{{ approval.role_name || approval.role_id }}</b><template v-if="approval.team_name"> · {{ teamScopeName }} <b>{{ approval.team_name }}</b></template>
    </div>
    <div v-if="!approval.status || approval.status === 'pending'" class="approval-actions">
      <button class="btn primary" @click="emit('decide', approval.id, 'approve_once')">仅本次</button>
      <button class="btn" @click="emit('decide', approval.id, 'approve_session')">本会话</button>
      <button v-if="approval.role_id" class="btn" @click="emit('decide', approval.id, 'approve_role')">永久（本角色）</button>
      <button v-if="approval.team_id" class="btn" @click="emit('decide', approval.id, 'approve_team')">{{ teamScopeLabel }}</button>
      <button v-if="!approval.role_id && !approval.team_id" class="btn" @click="emit('decide', approval.id, 'approve_always')">始终允许</button>
      <button class="btn danger" @click="emit('decide', approval.id, 'deny')">拒绝</button>
    </div>
    <div v-else class="approval-done">{{ approval.status === 'approved' ? '已允许' : approval.status === 'denied' ? '已拒绝' : approval.status }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import SideDrawer from '@/components/SideDrawer.vue'
import { api } from '@/api/client'
import type { Conversation, Role, Team } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const props = defineProps<{ open: boolean; conversation: Conversation | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'saved', conversation: Conversation): void }>()
const app = useAppStore()
const data = useDataStore()
const selected = ref<string[]>([])
const temporary = ref<Role[]>([])
const name = ref('')
const saving = ref(false)
const leaderId = computed(() => {
  const team = data.teams.find((item) => item.id === props.conversation?.team_id)
  return String(team?.leader?.id || props.conversation?.role_id || 'default')
})
const options = computed(() => [...data.persistentRoles, ...temporary.value]
  .filter((role) => role.id !== leaderId.value))

watch(() => [props.open, props.conversation?.id], async () => {
  if (!props.open || !props.conversation) return
  const conv = props.conversation
  const team = data.teams.find((item) => item.id === conv.team_id)
  selected.value = (team?.members || []).map((member) => String(member.id || '')).filter(Boolean)
  name.value = ''
  try {
    const result = await api<{ items: Role[] }>(`/roles?include_temporary=true&session_id=${encodeURIComponent(conv.id)}`)
    temporary.value = (result.items || []).filter((role) => role.scope === 'temporary')
  } catch (error) {
    temporary.value = []
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}, { immediate: true })

async function createTemporary(): Promise<void> {
  if (!props.conversation || !name.value.trim() || saving.value) return
  saving.value = true
  try {
    const role = await api<Role>('/roles', { method: 'POST', body: {
      name: name.value.trim(), scope: 'temporary', session_id: props.conversation.id,
    } })
    temporary.value.push(role)
    selected.value.push(role.id)
    name.value = ''
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally { saving.value = false }
}

async function save(): Promise<void> {
  if (!props.conversation || saving.value) return
  saving.value = true
  try {
    const result = await api<{ conversation: Conversation; team: Team }>(
      `/conversations/${encodeURIComponent(props.conversation.id)}/participants`,
      { method: 'PUT', body: { role_ids: selected.value, leader_id: props.conversation.role_id || undefined } },
    )
    await data.loadAll()
    emit('saved', result.conversation)
    app.toast('参与角色已更新')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally { saving.value = false }
}
</script>

<template>
  <SideDrawer :open="open" title="参与角色" subtitle="角色仅加入当前会话" width="480px" @close="emit('close')">
    <div class="field">
      <label>参与角色（最多 12 个）</label>
      <el-select v-model="selected" multiple filterable collapse-tags collapse-tags-tooltip
        :multiple-limit="12" placeholder="选择角色" style="width: 100%">
        <el-option v-for="role in options" :key="role.id" :value="role.id"
          :label="`${role.name}${role.scope === 'temporary' ? '（临时）' : ''}`" />
      </el-select>
      <small class="hintline">主持角色保持不变；添加的角色可通过 @ 调用或派发后台任务。</small>
    </div>
    <div class="field">
      <label>新建临时角色</label>
      <div style="display: flex; gap: 8px">
        <input v-model="name" class="input" maxlength="120" placeholder="角色名称" @keyup.enter="createTemporary" />
        <button class="btn" type="button" :disabled="!name.trim() || saving || selected.length >= 12" @click="createTemporary">添加</button>
      </div>
      <small class="hintline">临时角色仅属于本会话；创建后请保存参与角色。</small>
    </div>
    <template #footer>
      <button class="btn" @click="emit('close')">取消</button>
      <button class="btn primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
    </template>
  </SideDrawer>
</template>

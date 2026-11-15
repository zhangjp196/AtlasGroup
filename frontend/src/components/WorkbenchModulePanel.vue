<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { Conversation, ScheduledTask, Workspace } from '@/api/types'
import { conversationTitle } from '@/lib/derive'
import { useDataStore } from '@/stores/data'

type ModuleKind = 'conversations' | 'schedules' | 'workspaces' | 'organization'

const props = defineProps<{ kind: ModuleKind }>()
const data = useDataStore()
const router = useRouter()

interface Entry { key: string; title: string; detail: string; path: string }

const title = computed(() => ({
  conversations: '会话', schedules: '定时任务', workspaces: '工作区', organization: '组织',
}[props.kind]))

const entries = computed<Entry[]>(() => {
  if (props.kind === 'conversations') return [...data.conversations]
    .filter((item) => !['global-agent', 'global-chat'].includes(String(item.zone || '')))
    .sort((a, b) => Number(Boolean(b.active)) - Number(Boolean(a.active))
      || Date.parse(b.updated_at || '') - Date.parse(a.updated_at || ''))
    .slice(0, 6)
    .map((item: Conversation) => ({
      key: item.id,
      title: conversationTitle(item, data.teams, data.roles),
      detail: item.run_status === 'running' ? '运行中' : item.last_message || `${item.message_count || 0} 条消息`,
      path: `/conv/${encodeURIComponent(item.id)}`,
    }))

  if (props.kind === 'schedules') return [...data.schedules]
    .sort((a, b) => Number(b.status === 'active') - Number(a.status === 'active')
      || Date.parse(b.updated_at || '') - Date.parse(a.updated_at || ''))
    .slice(0, 6)
    .map((item: ScheduledTask) => ({
      key: item.id, title: item.title || '未命名任务',
      detail: item.status === 'active' ? '已启用' : item.status === 'paused' ? '已暂停' : '已结束',
      path: '/schedule',
    }))

  if (props.kind === 'workspaces') return [...data.workspaces]
    .filter((item) => !item.archived)
    .slice(0, 6)
    .map((item: Workspace) => ({
      key: item.id, title: item.name, detail: item.root_path,
      path: `/ws/${encodeURIComponent(item.id)}`,
    }))

  return [
    ...data.persistentRoles.slice(0, 4).map((item) => ({
      key: `role:${item.id}`, title: item.name, detail: [item.title, item.department].filter(Boolean).join(' · ') || '角色',
      path: `/organization/roles/${encodeURIComponent(item.id)}`,
    })),
    ...data.groups.slice(0, 4).map((item) => ({
      key: `group:${item.id}`, title: item.name, detail: `${item.members?.length || 0} 个成员`,
      path: `/organization/groups/${encodeURIComponent(item.id)}`,
    })),
  ].slice(0, 8)
})

const count = computed(() => props.kind === 'conversations' ? data.conversations.length
  : props.kind === 'schedules' ? data.schedules.length
    : props.kind === 'workspaces' ? data.workspaces.filter((item) => !item.archived).length
      : data.persistentRoles.length + data.groups.length)

function open(path: string): void {
  void router.push(path)
}
</script>

<template>
  <section class="workbench-module-panel">
    <header><b>{{ title }}</b><span>{{ count }} 项</span></header>
    <div v-if="entries.length" class="workbench-module-entries">
      <button v-for="entry in entries" :key="entry.key" type="button" @click="open(entry.path)">
        <span><b>{{ entry.title }}</b><small>{{ entry.detail }}</small></span><i aria-hidden="true">›</i>
      </button>
    </div>
    <div v-else class="workbench-module-empty">暂无{{ title }}内容</div>
    <footer><button type="button" @click="open(props.kind === 'organization' ? '/organization/roles'
      : props.kind === 'conversations' ? '/conv'
        : props.kind === 'schedules' ? '/schedule' : '/ws')">打开{{ title }}完整页面</button></footer>
  </section>
</template>

<style scoped>
.workbench-module-panel{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;overflow:hidden}
.workbench-module-panel>header{display:flex;flex:none;align-items:center;justify-content:space-between;padding:9px 10px;border-bottom:1px solid var(--line-soft);font-size:11px}
.workbench-module-panel>header span{color:var(--faint);font-size:9px}
.workbench-module-entries{flex:1;min-height:0;overflow:auto;padding:5px}
.workbench-module-entries>button{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;min-width:0;padding:7px 8px;border:0;border-radius:6px;background:transparent;color:var(--text);text-align:left;cursor:pointer}
.workbench-module-entries>button:hover{background:var(--panel-2)}
.workbench-module-entries>button>span{display:flex;flex-direction:column;gap:3px;min-width:0}
.workbench-module-entries b{overflow:hidden;font-size:10px;text-overflow:ellipsis;white-space:nowrap}
.workbench-module-entries small{overflow:hidden;color:var(--faint);font-size:9px;text-overflow:ellipsis;white-space:nowrap}
.workbench-module-entries i{flex:none;color:var(--faint);font-size:15px;font-style:normal}
.workbench-module-empty{display:grid;flex:1;place-items:center;min-height:80px;color:var(--faint);font-size:10px}
.workbench-module-panel>footer{flex:none;padding:6px 8px;border-top:1px solid var(--line-soft)}
.workbench-module-panel>footer button{width:100%;padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--bg-2);color:var(--dim);cursor:pointer;font-size:9px}
.workbench-module-panel>footer button:hover{color:var(--text);border-color:var(--line-strong)}
</style>

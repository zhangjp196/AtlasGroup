<script setup lang="ts">
import { computed } from 'vue'
import type { Conversation, Workspace } from '@/api/types'
import BrowserPanel from '@/components/chat/BrowserPanel.vue'
import ChatTaskCenter from '@/components/chat/ChatTaskCenter.vue'
import TerminalSession from '@/components/chat/TerminalSession.vue'
import WorkbenchModulePanel from '@/components/WorkbenchModulePanel.vue'

export type WorkspaceModuleKind = 'conversations' | 'schedules' | 'workspaces' | 'organization'
export type WorkspacePanelKind = 'browser' | 'terminal' | 'tasks' | WorkspaceModuleKind

export type WorkspaceToolPanelState = {
  key: string
  kind: WorkspacePanelKind
  desktopId: string
  workspaceId?: string
  conversationId?: string
  hidden?: boolean
  minimized?: boolean
  maximized?: boolean
}

const props = defineProps<{
  panel: WorkspaceToolPanelState
  desktops: { id: string; name: string }[]
  workspaces: Workspace[]
  conversations: Conversation[]
}>()
const emit = defineEmits<{
  patch: [key: string, patch: Partial<WorkspaceToolPanelState>]
  hide: [key: string]
  close: [key: string]
}>()

const title = computed(() => props.panel.kind === 'browser' ? '浏览器'
  : props.panel.kind === 'terminal' ? '终端'
    : props.panel.kind === 'tasks' ? '协作中心'
      : props.panel.kind === 'conversations' ? '会话'
        : props.panel.kind === 'schedules' ? '定时任务'
          : props.panel.kind === 'workspaces' ? '工作区' : '组织')
const availableWorkspaces = computed(() => props.workspaces.filter((item) => !item.chat_only))
const activeConversation = computed(() => props.conversations.find((item) => item.id === props.panel.conversationId))

function changeWorkspace(event: Event): void {
  emit('patch', props.panel.key, { workspaceId: (event.target as HTMLSelectElement).value })
}
function changeConversation(event: Event): void {
  emit('patch', props.panel.key, { conversationId: (event.target as HTMLSelectElement).value })
}
function changeDesktop(event: Event): void {
  emit('patch', props.panel.key, { desktopId: (event.target as HTMLSelectElement).value })
}
function startPanelDrag(event: DragEvent): void {
  if (!event.dataTransfer) return
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', props.panel.key)
}
</script>

<template>
  <section class="workspace-tool-panel" :data-board-panel-key="panel.key"
    :class="{ 'tool-minimized': panel.minimized, 'tool-maximized': panel.maximized }">
    <header class="workspace-tool-head">
      <button class="panel-drag-handle" type="button" draggable="true" title="拖动排序或移动到其他桌面"
        aria-label="拖动排序或移动到其他桌面" @dragstart="startPanelDrag">⠿</button>
      <span class="workspace-tool-title"><i :class="`tool-kind-${panel.kind}`" />{{ title }}</span>
      <select class="workspace-tool-select" :value="panel.desktopId" aria-label="选择桌面" @change="changeDesktop">
        <option v-for="desktop in desktops" :key="desktop.id" :value="desktop.id">{{ desktop.name }}</option>
      </select>
      <select v-if="panel.kind === 'terminal'" class="workspace-tool-select context-select" :value="panel.workspaceId || ''" aria-label="终端工作区" @change="changeWorkspace">
        <option value="">选择工作区</option>
        <option v-for="workspace in availableWorkspaces" :key="workspace.id" :value="workspace.id">{{ workspace.name }}</option>
      </select>
      <select v-else-if="panel.kind === 'tasks'" class="workspace-tool-select context-select" :value="panel.conversationId || ''" aria-label="任务所属会话" @change="changeConversation">
        <option value="">选择会话</option>
        <option v-for="conversation in conversations" :key="conversation.id" :value="conversation.id">
          {{ conversation.title || conversation.last_message || conversation.id }}
        </option>
      </select>
      <span class="tool-head-spacer" />
      <button type="button" :title="panel.minimized ? '展开面板' : '最小化面板'" :aria-label="panel.minimized ? '展开面板' : '最小化面板'"
        @click="emit('patch', panel.key, { minimized: !panel.minimized })">{{ panel.minimized ? '▣' : '−' }}</button>
      <button type="button" :title="panel.maximized ? '还原面板' : '最大化面板'" :aria-label="panel.maximized ? '还原面板' : '最大化面板'"
        :aria-pressed="Boolean(panel.maximized)" @click="emit('patch', panel.key, { maximized: !panel.maximized })">⛶</button>
      <button type="button" title="隐藏面板" aria-label="隐藏面板" @click="emit('hide', panel.key)">×</button>
      <button type="button" title="移除面板" aria-label="移除面板" @click="emit('close', panel.key)">⌫</button>
    </header>
    <div v-show="!panel.minimized" class="workspace-tool-body">
      <BrowserPanel v-if="panel.kind === 'browser'" :open="true" />
      <TerminalSession v-else-if="panel.kind === 'terminal' && panel.workspaceId" :key="`${panel.key}:${panel.workspaceId}`" :workspace-id="panel.workspaceId" />
      <ChatTaskCenter v-else-if="panel.kind === 'tasks' && panel.conversationId" :open="true" :conversation-id="panel.conversationId"
        :title="activeConversation?.title || activeConversation?.last_message || '协作中心'" @close="emit('hide', panel.key)" />
      <WorkbenchModulePanel v-else-if="['conversations', 'schedules', 'workspaces', 'organization'].includes(panel.kind)"
        :kind="panel.kind as WorkspaceModuleKind" />
      <div v-else class="workspace-tool-empty">
        {{ panel.kind === 'terminal' ? '请先选择工作区' : panel.kind === 'tasks' ? '请先选择会话' : '' }}
      </div>
    </div>
  </section>
</template>

<style scoped>
.workspace-tool-panel{display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--text)}
.workspace-tool-head{display:flex;flex:none;align-items:center;gap:5px;min-height:34px;padding:4px 6px;border-bottom:1px solid var(--line-soft);background:var(--bg-2)}
.workspace-tool-title{display:flex;align-items:center;gap:6px;min-width:0;font-size:10px;font-weight:650;white-space:nowrap}
.workspace-tool-title i{width:7px;height:7px;border-radius:50%;background:var(--accent);flex:none}
.workspace-tool-title .tool-kind-terminal{background:var(--accent-2)}
.workspace-tool-title .tool-kind-tasks{background:var(--warn)}
.workspace-tool-select{max-width:110px;min-width:68px;height:24px;padding:1px 4px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--dim);font-size:9px}
.workspace-tool-select.context-select{max-width:150px;flex:1}
.tool-head-spacer{flex:1;min-width:0}
.workspace-tool-head>button{flex:none;width:23px;height:23px;border:0;border-radius:6px;background:transparent;color:var(--faint);cursor:pointer}
.workspace-tool-head>button:hover{background:var(--panel);color:var(--text)}
.workspace-tool-head>.panel-drag-handle{cursor:grab;font-size:14px;touch-action:none}
.workspace-tool-head>.panel-drag-handle:active{cursor:grabbing}
.workspace-tool-body{display:flex;flex:1;min-width:0;min-height:0;overflow:hidden}
.workspace-tool-body>:deep(*){flex:1;min-width:0;min-height:0}
.workspace-tool-empty{display:grid;place-items:center;width:100%;color:var(--faint);font-size:11px}
</style>

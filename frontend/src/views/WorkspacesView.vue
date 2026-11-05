<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CreateDialog from '@/components/CreateDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import WorkspaceSettingsDrawer from '@/components/WorkspaceSettingsDrawer.vue'
import { shortTime } from '@/lib/format'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const route = useRoute()
const router = useRouter()
const createOpen = ref(false)

const workspaces = computed(() => data.activeWorkspaces)
const workspaceId = computed(() => String(route.params.id || ''))
const selectedWorkspace = computed(() => data.workspaces.find((ws) => ws.id === workspaceId.value) || null)

function openWorkspace(id: string): void {
  void router.push(`/ws/${encodeURIComponent(id)}`)
}

function closeWorkspace(): void {
  if (workspaceId.value) void router.push('/ws')
}

function onCreated(_kind: string, id: string): void {
  createOpen.value = false
  void router.push(`/ws/${encodeURIComponent(id)}`)
}

function onDeleted(): void {
  void router.push('/ws')
}
</script>

<template>
  <PageHeader section="工作区" title="全部工作区" :chip="`${workspaces.length} 个`">
    <el-button type="primary" @click="createOpen = true">＋ 添加工作区</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="hero">
      <div>
        <h1>工作区</h1>
        <p>工作区只提供文件与终端上下文。点开一个块查看目录、文件预览和绑定操作。</p>
      </div>
    </div>

    <div class="tile-grid">
      <article
        v-for="ws in workspaces"
        :key="ws.id"
        class="tile-card ws-tile"
        @click="openWorkspace(ws.id)"
      >
        <div class="tile-head">
          <span class="ws-tile-icon">
            <svg viewBox="0 0 24 24"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z" /></svg>
          </span>
          <div class="tile-title">
            <b>{{ ws.name }}</b>
            <span>{{ ws.chat_only ? '纯聊天工作区' : (ws.root_path || ws.kind || '托管目录') }}</span>
          </div>
        </div>
        <div class="tile-chips">
          <span class="chip">{{ ws.conversation_count || 0 }} 个会话</span>
          <span v-if="ws.last_conv_updated" class="chip">最近 {{ shortTime(ws.last_conv_updated) }}</span>
          <span v-else class="chip">还没有会话</span>
        </div>
        <div class="tile-foot">
          <span>点击查看目录与文件</span>
          <span>{{ ws.archived ? '已归档' : '打开详情' }}</span>
        </div>
      </article>
    </div>
    <div v-if="!workspaces.length" class="empty">还没有工作区<br />点右上角「＋ 添加工作区」选择一个文件夹</div>
  </div>

  <CreateDialog :open="createOpen" kind="ws" @close="createOpen = false" @created="onCreated" />

  <WorkspaceSettingsDrawer
    :open="Boolean(selectedWorkspace)"
    :workspace="selectedWorkspace"
    @close="closeWorkspace"
    @deleted="onDeleted"
  />
</template>

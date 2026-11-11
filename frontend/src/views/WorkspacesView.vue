<script setup lang="ts">
import { ElButton, ElInput } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import CreateDialog from '@/components/CreateDialog.vue'
import ListPager from '@/components/ListPager.vue'
import PageHeader from '@/components/PageHeader.vue'
import WorkspaceSettingsDrawer from '@/components/WorkspaceSettingsDrawer.vue'
import { clampPage, DEFAULT_PAGE_SIZE, pageCount, pageSlice } from '@/lib/paging'
import { api } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const data = useDataStore()
const app = useAppStore()
const route = useRoute()
const router = useRouter()
const createOpen = ref(false)
const menu = reactive({ open: false, x: 0, y: 0, ws: null as (typeof data.workspaces)[number] | null })

const menuItems = computed<ContextMenuItem[]>(() => [
  { label: '打开工作区', action: 'open' },
  { label: '复制路径', action: 'copy', disabled: !menu.ws?.root_path },
  { label: '', action: '', separator: true },
  { label: '归档工作区', action: 'archive', danger: true, disabled: Boolean(menu.ws?.archived) },
])

function openMenu(event: MouseEvent, ws: (typeof data.workspaces)[number]): void {
  menu.ws = ws
  menu.x = event.clientX
  menu.y = event.clientY
  menu.open = true
}

async function onMenu(action: string): Promise<void> {
  const ws = menu.ws
  if (!ws) return
  if (action === 'open') return openWorkspace(ws.id)
  if (action === 'copy') {
    try {
      await navigator.clipboard.writeText(ws.root_path || ws.name)
      app.toast('已复制路径')
    } catch {
      app.toast('复制失败', 'bad')
    }
    return
  }
  if (action === 'archive') {
    if (!window.confirm(`归档工作区「${ws.name}」？归档后不再出现在会话选择里。`)) return
    try {
      await api(`/workspaces/${encodeURIComponent(ws.id)}`, { method: 'PATCH', body: { archived: true } })
      await data.loadAll()
      app.toast('工作区已归档')
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
  }
}

const query = ref('')
const page = ref(1)
const PAGE_SIZE = DEFAULT_PAGE_SIZE

const workspaces = computed(() => {
  const q = query.value.trim().toLowerCase()
  return data.activeWorkspaces.filter((ws) => {
    if (!q) return true
    return `${ws.name || ''} ${ws.root_path || ''} ${ws.kind || ''} ${ws.id || ''}`
      .toLowerCase()
      .includes(q)
  })
})
const pagedWorkspaces = computed(() => pageSlice(workspaces.value, page.value, PAGE_SIZE))
const pages = computed(() => pageCount(workspaces.value.length, PAGE_SIZE))
watch(query, () => { page.value = 1 })
watch(pages, (total) => { page.value = clampPage(page.value, total) })
const roleCountByWs = computed(() => {
  const map = new Map<string, number>()
  for (const role of data.persistentRoles) {
    const id = String(role.workspace_id || '')
    if (id) map.set(id, (map.get(id) || 0) + 1)
  }
  return map
})
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
    <div class="list-toolbar">
      <el-input v-model="query" placeholder="搜索名称 / 路径 / id" clearable />
      <button v-if="query.trim()" class="mini-link" type="button" @click="query = ''">清除搜索</button>
    </div>
    <div class="tile-grid">
      <article
        v-for="ws in pagedWorkspaces"
        :key="ws.id"
        class="tile-card ws-tile"
        @click="openWorkspace(ws.id)"
        @contextmenu.prevent.stop="openMenu($event, ws)"
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
          <span class="chip">{{ roleCountByWs.get(ws.id) || 0 }} 个角色</span>
        </div>
        <div class="tile-foot">
          <span>角色绑定到此工作区</span>
          <span>{{ ws.archived ? '已归档' : '打开详情' }}</span>
        </div>
      </article>
    </div>
    <div v-if="!workspaces.length" class="empty">
      <template v-if="query.trim()">没有匹配「{{ query }}」的工作区<br /><button class="mini-link" type="button" @click="query = ''">清除搜索</button></template>
      <template v-else>还没有工作区<br />点右上角「＋ 添加工作区」选择一个文件夹</template>
    </div>
    <ListPager :page="page" :pages="pages" :total="workspaces.length" :page-size="PAGE_SIZE" @update:page="page = $event" />
  </div>

  <CreateDialog :open="createOpen" kind="ws" @close="createOpen = false" @created="onCreated" />

  <WorkspaceSettingsDrawer
    :open="Boolean(selectedWorkspace)"
    :workspace="selectedWorkspace"
    @close="closeWorkspace"
    @deleted="onDeleted"
  />

  <ContextMenu
    :open="menu.open"
    :x="menu.x"
    :y="menu.y"
    :items="menuItems"
    @close="menu.open = false"
    @select="onMenu"
  />
</template>

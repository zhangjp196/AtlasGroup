<script setup lang="ts">
/**
 * 角色 / 群组详情里的会话列表。
 *
 * 会话是「角色或群组」的一个维度：同一个角色/群组可以有多条会话（含分支）。
 * 这里按 `role_id` / `team_id` 从后端拉全量（本地 store 只有最近 80 条），
 * 进行中优先、其余按更新时间倒序，分页展示；归档会话默认隐藏。
 */
import { computed, reactive, ref, watch } from 'vue'
import Avatar from '@/components/Avatar.vue'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import ListPager from '@/components/ListPager.vue'
import { api } from '@/api/client'
import type { Conversation, ListResponse } from '@/api/types'
import { GLOBAL_AGENT_ZONE, GLOBAL_CHAT_ZONE } from '@/lib/derive'
import { firstLine, shortTime } from '@/lib/format'
import { clampPage, pageCount, pageSlice } from '@/lib/paging'
import { useAppStore } from '@/stores/app'
import { useChatWindowsStore } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'

const props = withDefaults(defineProps<{
  roleId?: string
  teamId?: string
  /** 当前打开的会话：列表里高亮并标记「当前」。 */
  activeId?: string
  pageSize?: number
  emptyText?: string
}>(), {
  roleId: '',
  teamId: '',
  activeId: '',
  pageSize: 6,
  emptyText: '还没有会话',
})

const emit = defineEmits<{
  (e: 'open', id: string): void
  (e: 'changed'): void
}>()

const app = useAppStore()
const data = useDataStore()
const chatWindows = useChatWindowsStore()

const loaded = ref<Conversation[] | null>(null)
const loading = ref(false)
const showArchived = ref(false)
const page = ref(1)
const menu = reactive({ open: false, x: 0, y: 0, conv: null as Conversation | null })
let requestSeq = 0

const scopeId = computed(() => props.roleId || props.teamId || '')

/** 后端返回前先用 store 里已加载的会话兜底，避免抽屉打开瞬间空白。 */
const rows = computed<Conversation[]>(() => {
  const source = loaded.value ?? data.conversations.filter((conv) => {
    if (props.roleId) return String(conv.role_id || '') === props.roleId
    if (props.teamId) return String(conv.team_id || '') === props.teamId
    return false
  })
  const list = source.filter((conv) => {
    const zone = String(conv.zone || '')
    if (zone === GLOBAL_AGENT_ZONE || zone === GLOBAL_CHAT_ZONE) return false
    // 共享群聊的 role_id 是群主：群聊会话归群组，不混进角色的单聊历史。
    if (props.roleId && conv.team_id) {
      const team = data.teams.find((item) => item.id === conv.team_id)
      if (String(team?.kind || '') === 'group') return false
    }
    if (!showArchived.value && conv.archived) return false
    return true
  })
  return [...list].sort((a, b) => {
    const aLive = isLive(a) ? 1 : 0
    const bLive = isLive(b) ? 1 : 0
    if (aLive !== bLive) return bLive - aLive
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
  })
})

const pages = computed(() => pageCount(rows.value.length, props.pageSize))
const pagedRows = computed(() => pageSlice(rows.value, page.value, props.pageSize))
const archivedCount = computed(() => (loaded.value || []).filter((conv) => conv.archived).length)

const team = computed(() => (props.teamId
  ? data.teams.find((item) => item.id === props.teamId) || null
  : null))

function isLive(conv: Conversation): boolean {
  return Boolean(conv.active || conv.unread_count || conv.running_count || conv.run_status)
}

function rowTitle(conv: Conversation): string {
  return conv.title
    || team.value?.name
    || firstLine(conv.last_message || '', 24)
    || '新会话'
}

function rowAvatar(conv: Conversation): string {
  if (team.value) return team.value.avatar || ''
  return data.roleAvatar(conv.role_id, '')
}

function rowSub(conv: Conversation): string {
  const preview = firstLine(conv.last_message || '', 60)
  return preview || '还没有消息'
}

const menuItems = computed<ContextMenuItem[]>(() => [
  { label: '打开会话', action: 'open' },
  { label: '从这里分支', action: 'fork', hint: '复制整段上下文' },
  { label: '标记已读', action: 'read', disabled: !Number(menu.conv?.unread_count || 0) },
  { label: '', action: '', separator: true },
  { label: '删除会话', action: 'delete', danger: true },
])

function openMenu(event: MouseEvent, conv: Conversation): void {
  menu.conv = conv
  menu.x = event.clientX
  menu.y = event.clientY
  menu.open = true
}

async function load(): Promise<void> {
  if (!scopeId.value) {
    loaded.value = []
    return
  }
  const seq = ++requestSeq
  loading.value = true
  try {
    const query = props.roleId
      ? `role_id=${encodeURIComponent(props.roleId)}`
      : `team_id=${encodeURIComponent(props.teamId)}`
    const out = await api<ListResponse<Conversation>>(
      `/conversations?${query}&limit=100&roots_only=1&include_archived=${showArchived.value ? 'true' : 'false'}`,
    )
    if (seq === requestSeq) loaded.value = out.items || []
  } catch {
    if (seq === requestSeq) loaded.value = null
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

async function onMenu(action: string): Promise<void> {
  const conv = menu.conv
  if (!conv) return
  if (action === 'open') {
    emit('open', conv.id)
    return
  }
  if (action === 'fork') {
    try {
      const next = await api<Conversation>(
        `/conversations/${encodeURIComponent(conv.id)}/fork`,
        { method: 'POST', body: {} },
      )
      app.toast('已创建分支会话')
      await load()
      emit('changed')
      if (next?.id) emit('open', next.id)
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
    return
  }
  if (action === 'read') {
    try {
      await api(`/conversations/${encodeURIComponent(conv.id)}/read`, { method: 'POST' })
      await load()
      emit('changed')
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
    return
  }
  if (action === 'delete') {
    if (!window.confirm(`删除会话「${rowTitle(conv)}」？历史消息会一并删除。`)) return
    try {
      await api(`/conversations/${encodeURIComponent(conv.id)}`, { method: 'DELETE' })
      chatWindows.closeConversation(conv.id)
      app.toast('会话已删除')
      await load()
      emit('changed')
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
  }
}

watch([scopeId, showArchived], () => {
  page.value = 1
  loaded.value = null
  void load()
}, { immediate: true })
watch(pages, (total) => { page.value = clampPage(page.value, total) })
// 外部新建 / 分支 / 删除会话后，store 会刷新；这里跟着重新拉一次。
watch(() => data.conversations.length, () => { void load() })

defineExpose({ reload: load })
</script>

<template>
  <div class="session-list">
    <div class="group-member-list">
      <button
        v-for="conv in pagedRows"
        :key="conv.id"
        class="group-member session-row"
        :class="{ unread: Number(conv.unread_count || 0) > 0, current: conv.id === activeId }"
        type="button"
        :title="rowTitle(conv)"
        @click="emit('open', conv.id)"
        @contextmenu.prevent.stop="openMenu($event, conv)"
      >
        <Avatar :name="rowTitle(conv)" :avatar="rowAvatar(conv)" round />
        <div class="group-member-main">
          <div class="group-member-name">
            <span>{{ rowTitle(conv) }}</span>
            <span v-if="conv.forked_from_id" class="chip session-fork" title="分支会话">分支</span>
            <span v-if="conv.archived" class="chip session-archived">已归档</span>
            <span v-if="conv.id === activeId" class="chip session-current">当前</span>
          </div>
          <div class="group-member-sub">{{ rowSub(conv) }}</div>
        </div>
        <span v-if="Number(conv.running_count || 0) > 0" class="chip session-run">运行中</span>
        <span v-else class="session-time">{{ shortTime(conv.updated_at) }}</span>
        <span v-if="Number(conv.unread_count || 0) > 0" class="conv-badge unread">
          {{ Number(conv.unread_count) > 99 ? '99+' : conv.unread_count }}
        </span>
      </button>
      <div v-if="!pagedRows.length" class="group-empty">
        {{ loading ? '加载中…' : emptyText }}
      </div>
    </div>

    <div v-if="rows.length > pageSize" class="session-foot">
      <ListPager
        :page="page"
        :pages="pages"
        :total="rows.length"
        :page-size="pageSize"
        @update:page="page = $event"
      />
    </div>

    <div class="session-tools">
      <span>{{ rows.length }} 条{{ archivedCount ? ` · ${archivedCount} 条已归档` : '' }}</span>
      <button class="mini-link" type="button" @click="showArchived = !showArchived">
        {{ showArchived ? '隐藏已归档' : '显示已归档' }}
      </button>
    </div>

    <ContextMenu
      :open="menu.open"
      :x="menu.x"
      :y="menu.y"
      :items="menuItems"
      @close="menu.open = false"
      @select="onMenu"
    />
  </div>
</template>

<style scoped>
.session-list { display: flex; flex-direction: column; gap: 8px; }
.session-row { width: 100%; text-align: left; cursor: pointer; }
.session-row:hover { border-color: var(--line); background: var(--panel); }
.session-row.unread { border-color: color-mix(in srgb, var(--primary) 40%, var(--line-soft)); }
.session-row.current { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, var(--panel)); }
.session-current { font-size: 9px; padding: 1px 5px; color: var(--accent-ink); border-color: color-mix(in srgb, var(--accent) 40%, var(--line)); }
.session-fork { font-size: 9px; padding: 1px 5px; }
.session-archived { font-size: 9px; padding: 1px 5px; opacity: .7; }
.session-run { font-size: 9px; padding: 1px 5px; color: var(--success); }
.session-time { font-size: 10px; color: var(--faint); flex: none; }
.session-foot { display: flex; justify-content: center; }
.session-tools { display: flex; align-items: center; gap: 8px; font-size: 10px; color: var(--faint); }
.session-tools .mini-link { margin-left: auto; font-size: 10px; }
</style>

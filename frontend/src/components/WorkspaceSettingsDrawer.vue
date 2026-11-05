<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Modal from '@/components/Modal.vue'
import WorkEditorDialog, { type WorkSeed } from '@/components/WorkEditorDialog.vue'
import { api } from '@/api/client'
import type { Workspace } from '@/api/types'
import { bytes, shortTime } from '@/lib/format'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

interface FileEntry {
  name: string
  path: string
  type: 'dir' | 'file'
  size: number
}

const props = defineProps<{ open: boolean; workspace: Workspace | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved'): void
  (e: 'deleted', id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const route = useRoute()
const router = useRouter()

const path = ref('.')
const items = ref<FileEntry[]>([])
const file = ref<{ path: string; text?: string; content?: string; size: number; truncated?: boolean } | null>(null)
const loading = ref(false)
const busy = ref(false)
const renameOpen = ref(false)
const renameName = ref('')
const workOpen = ref(false)
const workSeed = ref<WorkSeed | null>(null)

const pathLabel = computed(() => path.value === '.' ? '/' : `/${path.value}`)
const breadcrumbs = computed(() => path.value === '.' ? [] : path.value.split('/').filter(Boolean))
const fileText = computed(() => file.value?.text ?? file.value?.content ?? '')

watch(() => props.workspace?.id, (id, old) => {
  if (!props.open || !id) return
  if (id === old) return
  path.value = '.'
  file.value = null
  items.value = []
  void loadDir()
}, { immediate: true })

watch(() => props.open, (open) => {
  if (open && props.workspace && !items.value.length) void loadDir()
})

async function loadDir(): Promise<void> {
  const ws = props.workspace
  if (!ws) return
  loading.value = true
  try {
    items.value = await api<FileEntry[]>(`/workspace/list?workspace_id=${encodeURIComponent(ws.id)}&path=${encodeURIComponent(path.value)}`)
    file.value = null
  } catch (error) {
    items.value = []
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    loading.value = false
  }
}

async function openEntry(entry: FileEntry): Promise<void> {
  const ws = props.workspace
  if (!ws) return
  if (entry.type === 'dir') {
    path.value = entry.path
    await loadDir()
    return
  }
  try {
    file.value = await api(`/workspace/file?workspace_id=${encodeURIComponent(ws.id)}&path=${encodeURIComponent(entry.path)}`)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function up(): Promise<void> {
  if (!breadcrumbs.value.length) return
  const parts = breadcrumbs.value.slice(0, -1)
  path.value = parts.length ? parts.join('/') : '.'
  await loadDir()
}

function close(): void {
  emit('close')
}

function openRename(): void {
  if (!props.workspace) return
  renameName.value = props.workspace.name
  renameOpen.value = true
}

async function rename(): Promise<void> {
  const ws = props.workspace
  if (!ws || busy.value) return
  const name = renameName.value.trim()
  if (!name) {
    app.toast('工作区名称不能为空', 'bad')
    return
  }
  busy.value = true
  try {
    await api(`/workspaces/${encodeURIComponent(ws.id)}`, { method: 'PATCH', body: { name } })
    await data.loadAll()
    renameOpen.value = false
    emit('saved')
    app.toast('工作区已重命名')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = false
  }
}

async function toggleArchive(): Promise<void> {
  const ws = props.workspace
  if (!ws || busy.value) return
  const archived = !ws.archived
  if (archived && !window.confirm(`归档工作区「${ws.name}」？目录与会话都会保留。`)) return
  busy.value = true
  try {
    await api(`/workspaces/${encodeURIComponent(ws.id)}`, { method: 'PATCH', body: { archived } })
    await data.loadAll()
    emit('saved')
    app.toast(archived ? '工作区已归档' : '工作区已恢复')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = false
  }
}

async function removeWorkspace(): Promise<void> {
  const ws = props.workspace
  if (!ws || busy.value) return
  if (!window.confirm(`删除工作区「${ws.name}」？目录本身不会被删除。`)) return
  busy.value = true
  try {
    await api(`/workspaces/${encodeURIComponent(ws.id)}`, { method: 'DELETE' })
    await data.loadAll()
    app.toast('工作区已删除')
    emit('deleted', ws.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = false
  }
}

async function reveal(): Promise<void> {
  const ws = props.workspace
  if (!ws) return
  try {
    await api('/workspace/reveal', { method: 'POST', body: { workspace_id: ws.id, path: '.' } })
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function bindToCurrent(): Promise<void> {
  const ws = props.workspace
  if (!ws) return
  const convId = route.meta.section === 'conv' ? String(route.params.id || '') : ''
  if (!convId) {
    app.toast('先打开一个会话再绑定工作区', 'bad')
    return
  }
  try {
    await api(`/conversations/${encodeURIComponent(convId)}/workspace`, {
      method: 'PATCH',
      body: { workspace_id: ws.id },
    })
    await data.loadAll()
    app.toast('已绑定到当前会话')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

function openWork(): void {
  const ws = props.workspace
  if (!ws) return
  close()
  workSeed.value = {
    workspace_id: ws.id,
    body: `来自工作区「${ws.name}」\n\n`,
  }
  workOpen.value = true
}

function onWorkSaved(): void {
  workOpen.value = false
}

function openConversation(id: string): void {
  close()
  void router.push(`/conv/${encodeURIComponent(id)}`)
}
</script>

<template>
  <Teleport to="body">
    <div v-show="open" class="drawer-scrim" @click="close" />
    <aside v-show="open" class="side-drawer" aria-label="工作区详情">
      <header class="side-drawer-head">
        <div>
          <h2>工作区</h2>
          <p>{{ workspace?.name || '' }} · {{ pathLabel }}</p>
        </div>
        <button class="iconbtn" type="button" title="关闭" aria-label="关闭" @click="close">
          <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>

      <div v-if="workspace" class="side-drawer-body">
        <div class="group-identity">
          <span class="ws-tile-icon lg">
            <svg viewBox="0 0 24 24"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z" /></svg>
          </span>
          <div class="group-identity-main">
            <div class="role-detail-name">{{ workspace.name }}</div>
            <div class="role-line mono">{{ workspace.chat_only ? '纯聊天工作区，没有本地目录' : (workspace.root_path || '托管目录') }}</div>
            <div class="chips">
              <span class="chip">{{ workspace.conversation_count || 0 }} 个会话</span>
              <span v-if="workspace.last_conv_updated" class="chip">最近 {{ shortTime(workspace.last_conv_updated) }}</span>
              <span v-if="workspace.archived" class="chip">已归档</span>
            </div>
          </div>
        </div>

        <section class="group-section">
          <div class="group-section-head">
            <h3>目录</h3>
            <span>{{ pathLabel }}</span>
            <button class="btn small" type="button" @click="loadDir">刷新</button>
          </div>
          <div class="ws-browser">
            <aside class="ws-file-list">
              <button v-if="breadcrumbs.length" class="btn small" type="button" @click="up">↑ 上一级</button>
              <div v-if="loading" class="ws-empty">加载中…</div>
              <template v-else>
                <button
                  v-for="entry in items"
                  :key="entry.path"
                  class="ws-file-row"
                  type="button"
                  @click="openEntry(entry)"
                >
                  <span class="ws-file-ico">{{ entry.type === 'dir' ? '▸' : '·' }}</span>
                  <span>{{ entry.name }}</span>
                </button>
                <div v-if="!items.length" class="ws-empty">空目录</div>
              </template>
            </aside>
            <article class="ws-file-preview">
              <template v-if="file">
                <div class="ws-preview-head">
                  <b>{{ file.path }}</b>
                  <span>{{ bytes(file.size) }}</span>
                  <button class="mini-link" type="button" @click="file = null">关闭</button>
                </div>
                <pre>{{ fileText }}{{ file.truncated ? '\n\n…（已截断）' : '' }}</pre>
              </template>
              <div v-else class="ws-empty">选择左侧文件查看只读预览</div>
            </article>
          </div>
        </section>

        <section class="group-section">
          <div class="group-section-head"><h3>管理</h3></div>
          <div class="avatar-actions">
            <button class="btn small" type="button" @click="openRename">重命名</button>
            <button class="btn small" type="button" :disabled="busy" @click="toggleArchive">{{ workspace.archived ? '恢复工作区' : '归档工作区' }}</button>
            <button class="btn small danger" type="button" :disabled="busy" @click="removeWorkspace">删除工作区</button>
          </div>
          <p class="group-mode-hint" style="margin:0">工作区只提供文件与终端上下文；删除工作区不会删除本地目录。</p>
        </section>

        <section v-if="workspace.recent_conversations?.length" class="group-section">
          <div class="group-section-head"><h3>最近会话</h3><span>{{ workspace.recent_conversations.length }} 个</span></div>
          <div class="sa-recent-list">
            <button
              v-for="conv in workspace.recent_conversations"
              :key="conv.id"
              class="sa-recent-row"
              type="button"
              @click="openConversation(conv.id)"
            >
              <span class="sa-recent-main"><b>{{ conv.title || conv.id }}</b><span>{{ shortTime(conv.updated_at) }}</span></span>
            </button>
          </div>
        </section>
      </div>

      <footer class="side-drawer-foot">
        <button class="btn" type="button" @click="bindToCurrent">绑定到当前会话</button>
        <button class="btn" type="button" @click="reveal">打开文件夹</button>
        <button class="btn" type="button" @click="openWork">新建工单</button>
        <span class="spacer" />
        <button class="btn" type="button" @click="close">关闭</button>
      </footer>
    </aside>

    <Modal :open="renameOpen" title="重命名工作区" @close="renameOpen = false">
      <div class="field">
        <label>工作区名称</label>
        <input v-model="renameName" class="input" maxlength="120" />
      </div>
      <template #footer>
        <el-button @click="renameOpen = false">取消</el-button>
        <el-button type="primary" :loading="busy" :disabled="!renameName.trim()" @click="rename">保存</el-button>
      </template>
    </Modal>

    <WorkEditorDialog :open="workOpen" :seed="workSeed" @close="workOpen = false" @saved="onWorkSaved" />
  </Teleport>
</template>

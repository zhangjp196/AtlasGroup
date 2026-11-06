<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '@/components/Modal.vue'
import WorkEditorDialog, { type WorkSeed } from '@/components/WorkEditorDialog.vue'
import { api } from '@/api/client'
import type { Workspace } from '@/api/types'
import { bytes } from '@/lib/format'
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
const boundRoles = computed(() => data.persistentRoles.filter(
  (role) => String(role.workspace_id || '') === String(props.workspace?.id || ''),
))
const boundTeams = computed(() => data.teams.filter(
  (team) => String(team.workspace_id || '') === String(props.workspace?.id || ''),
))

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
  const bindHint = (boundRoles.value.length || boundTeams.value.length)
    ? `有 ${boundRoles.value.length} 个角色 / ${boundTeams.value.length} 个群组绑定此工作区，归档后它们会回退到默认工作区。`
    : ''
  if (archived && !window.confirm(`归档工作区「${ws.name}」？${bindHint}`)) return
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
  const bindHint = (boundRoles.value.length || boundTeams.value.length)
    ? `有 ${boundRoles.value.length} 个角色 / ${boundTeams.value.length} 个群组绑定此工作区，之后会回退到默认工作区。`
    : ''
  if (!window.confirm(`删除工作区「${ws.name}」？目录本身不会被删除。${bindHint}`)) return
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

function openRole(id: string): void {
  close()
  void router.push(`/role/${encodeURIComponent(id)}`)
}

function openTeam(id: string): void {
  close()
  void router.push(`/group/${encodeURIComponent(id)}`)
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
              <span class="chip">{{ boundRoles.length }} 个角色</span>
              <span class="chip">{{ boundTeams.length }} 个群组</span>
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
          <div class="group-section-head"><h3>绑定的角色</h3><span>{{ boundRoles.length }} 个</span></div>
          <div class="sa-recent-list">
            <button
              v-for="role in boundRoles"
              :key="role.id"
              class="sa-recent-row"
              type="button"
              @click="openRole(role.id)"
            >
              <span class="sa-recent-main">
                <b>{{ role.name || role.id }}</b>
                <span>{{ [role.title, role.department, role.expertise].filter(Boolean).join(' · ') || '角色' }}</span>
              </span>
            </button>
            <div v-if="!boundRoles.length" class="ws-empty">还没有角色绑定这个工作区</div>
          </div>
        </section>

        <section class="group-section">
          <div class="group-section-head"><h3>绑定的群组</h3><span>{{ boundTeams.length }} 个</span></div>
          <div class="sa-recent-list">
            <button
              v-for="team in boundTeams"
              :key="team.id"
              class="sa-recent-row"
              type="button"
              @click="openTeam(team.id)"
            >
              <span class="sa-recent-main">
                <b>{{ team.name || team.id }}</b>
                <span>{{ (team.members || []).length }} 个角色</span>
              </span>
            </button>
            <div v-if="!boundTeams.length" class="ws-empty">还没有群组绑定这个工作区</div>
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
      </div>

      <footer class="side-drawer-foot">
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

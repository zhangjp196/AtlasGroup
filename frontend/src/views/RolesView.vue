<script setup lang="ts">
import { ElButton, ElDropdown, ElDropdownItem, ElDropdownMenu, ElInput, ElTree } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import DepartmentEditorDialog from '@/components/DepartmentEditorDialog.vue'
import ListPager from '@/components/ListPager.vue'
import PageHeader from '@/components/PageHeader.vue'
import RoleEditorDialog from '@/components/RoleEditorDialog.vue'
import RoleSettingsDrawer from '@/components/RoleSettingsDrawer.vue'
import WorkEditorDialog, { type WorkSeed } from '@/components/WorkEditorDialog.vue'
import { api } from '@/api/client'
import type { Conversation, Department, ListResponse, Role } from '@/api/types'
import { departmentPath, departmentRoleCounts, departmentSubtree, orderedDepartments } from '@/lib/derive'
import { clampPage, DEFAULT_PAGE_SIZE, pageCount, pageSlice } from '@/lib/paging'
import { useAppStore } from '@/stores/app'
import { useChatWindowsStore } from '@/stores/chatWindows'
import { useDataStore } from '@/stores/data'

interface DeptNode {
  id: string
  label: string
  count: number
  avatar?: string
  parent_id?: string
  children: DeptNode[]
}

const data = useDataStore()
const app = useAppStore()
const chatWindows = useChatWindowsStore()
const route = useRoute()
const router = useRouter()

const selectedDept = ref('all')
const editorOpen = ref(false)
const editingRole = ref<Role | null>(null)
const presetDept = ref('')

const deptEditorOpen = ref(false)
const editingDept = ref<Department | null>(null)
const deptParent = ref('')

const workOpen = ref(false)
const workSeed = ref<WorkSeed | null>(null)
const menu = reactive({ open: false, x: 0, y: 0, role: null as Role | null })

const menuItems = computed<ContextMenuItem[]>(() => {
  const role = menu.role
  return [
    { label: '打开角色详情', action: 'detail' },
    { label: '编辑角色', action: 'edit' },
    { label: '发起单聊', action: 'chat' },
    { label: role?.status === 'offline' ? '设为在线' : '设为离线', action: 'presence' },
    { label: '复制名字', action: 'copy' },
    { label: '', action: '', separator: true },
    { label: '删除角色', action: 'delete', danger: true, disabled: Boolean(role?.system) },
  ]
})

function openMenu(event: MouseEvent, role: Role): void {
  menu.role = role
  menu.x = event.clientX
  menu.y = event.clientY
  menu.open = true
}

async function onMenu(action: string): Promise<void> {
  const role = menu.role
  if (!role) return
  if (action === 'detail') return openRole(role)
  if (action === 'edit') {
    editingRole.value = role
    editorOpen.value = true
    return
  }
  if (action === 'chat') return startRoleChat(role.id)
  if (action === 'presence') {
    try {
      await api(`/roles/${encodeURIComponent(role.id)}`, {
        method: 'PATCH',
        body: { status: role.status === 'offline' ? 'online' : 'offline' },
      })
      await data.loadRoles()
      app.toast(role.status === 'offline' ? '角色已上线' : '角色已离线')
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
    return
  }
  if (action === 'copy') {
    try {
      await navigator.clipboard.writeText(role.name)
      app.toast('已复制角色名')
    } catch {
      app.toast('复制失败', 'bad')
    }
    return
  }
  if (action === 'delete') {
    if (role.system) {
      app.toast('系统角色不能删除', 'bad')
      return
    }
    if (!window.confirm(`删除角色「${role.name}」？引用它的会话会失去该角色。`)) return
    try {
      await api(`/roles/${encodeURIComponent(role.id)}`, { method: 'DELETE' })
      await data.loadAll()
      app.toast('角色已删除')
      if (roleId.value === role.id) closeRole()
    } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
    }
  }
}

const depts = computed(() => orderedDepartments(data.departments))
const deptTree = computed<DeptNode[]>(() => {
  // 父部门计数 = 本部门 + 全部子部门（与右侧列表口径一致）。
  const counts = departmentRoleCounts(data.departments, data.roles)
  const nodes = new Map<string, DeptNode>()
  for (const dept of data.departments) {
    nodes.set(dept.id, {
      id: dept.id,
      label: dept.name,
      avatar: dept.avatar,
      parent_id: dept.parent_id,
      count: counts.get(dept.id) || 0,
      children: [],
    })
  }
  const roots: DeptNode[] = []
  for (const dept of data.departments) {
    const node = nodes.get(dept.id)
    if (!node) continue
    const parent = dept.parent_id ? nodes.get(dept.parent_id) : null
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  return roots
})

function onDeptNode(node: DeptNode): void {
  selectedDept.value = node.id
}

// 角色列表的筛选：部门（左侧树）+ 关键词 + 持久/临时，再分页。
// 角色多起来以后，一屏铺几十张卡片既找不到人、也不好翻，必须能筛能翻页。
const query = ref('')
const scope = ref<'all' | 'persistent' | 'temporary'>('all')
const page = ref(1)
const PAGE_SIZE = DEFAULT_PAGE_SIZE

const orgRoles = computed(() => data.roles.filter(
  (role) => role.id !== 'default' && role.id !== 'global-agent',
))
const scopedRoles = computed(() => orgRoles.value.filter((role) => {
  if (selectedDept.value === 'all') return true
  if (selectedDept.value === '') return !role.department_id
  return departmentSubtree(selectedDept.value, data.departments).has(role.department_id || '')
}))

const visibleRoles = computed(() => {
  const q = query.value.trim().toLowerCase()
  return scopedRoles.value.filter((role) => {
    if (scope.value === 'persistent' && role.scope === 'temporary') return false
    if (scope.value === 'temporary' && role.scope !== 'temporary') return false
    if (!q) return true
    const haystack = [
      role.name, role.title, role.expertise, role.department,
      role.model_code, workspaceName(role.workspace_id),
      ...(role.tools || []),
    ].map((value) => String(value || '')).join(' ').toLowerCase()
    return haystack.includes(q)
  })
})

const pagedRoles = computed(() => pageSlice(visibleRoles.value, page.value, PAGE_SIZE))
const pages = computed(() => pageCount(visibleRoles.value.length, PAGE_SIZE))
const filtersActive = computed(() => Boolean(query.value.trim()) || scope.value !== 'all')

watch([selectedDept, query, scope], () => { page.value = 1 })
watch(pages, (total) => { page.value = clampPage(page.value, total) })

function clearFilters(): void {
  query.value = ''
  scope.value = 'all'
}

// 角色面板显示各自的在办工单数（后台任务会自动建单）。
const openWorkByRole = computed(() => {
  const map = new Map<string, number>()
  for (const item of data.workItems) {
    if (!['inbox', 'working', 'needs_attention'].includes(String(item.status || ''))) continue
    const rid = String(item.role_id || '')
    if (rid) map.set(rid, (map.get(rid) || 0) + 1)
  }
  return map
})

const headTitle = computed(() => {
  if (selectedDept.value === 'all') return '全部角色'
  if (selectedDept.value === '') return '未安排部门'
  return departmentPath(selectedDept.value, data.departments)
})
const headHint = computed(() => {
  const action = '点击角色卡直接开始聊天；右键查看更多操作。'
  if (selectedDept.value === 'all') return `按部门管理角色；角色也可以不安排部门，归入「未安排」。${action}`
  if (selectedDept.value === '') return `这些角色还没有安排部门，编辑角色即可归档到部门。${action}`
  return `包含该部门及其子部门的角色。${action}`
})

const roleId = computed(() => String(route.params.id || ''))
const selectedRole = computed(() => data.roles.find((role) => role.id === roleId.value) || null)

function workspaceName(id?: string): string {
  return data.workspaces.find((ws) => ws.id === String(id || ''))?.name || ''
}

function openRole(role: Role): void {
  void router.push(`/role/${encodeURIComponent(role.id)}`)
}

function closeRole(): void {
  if (roleId.value) void router.push('/role')
}

function openNewRole(): void {
  editingRole.value = null
  presetDept.value = selectedDept.value && selectedDept.value !== 'all' ? selectedDept.value : ''
  editorOpen.value = true
}

function openEditRole(): void {
  if (!selectedRole.value) return
  editingRole.value = selectedRole.value
  editorOpen.value = true
}

function onRoleSaved(id: string): void {
  editorOpen.value = false
  void router.replace(`/role/${encodeURIComponent(id)}`)
}

function onRoleDeleted(): void {
  closeRole()
}

function isRoleChat(conv: Conversation): boolean {
  if (conv.zone) return false
  if (!conv.team_id) return true
  const team = data.teams.find((item) => item.id === conv.team_id)
  // 共享群聊的 role_id 是群主；点击群主角色不应该把它当成单聊打开。
  // 团队信息缺失时也按「不是单聊」处理，宁可新建草稿也不误开群聊。
  return String(team?.kind || '') === 'participants'
}

function hasMessages(conv: Conversation): boolean {
  return Number(conv.message_count || 0) > 0 || Boolean(conv.last_message)
}

const openingRole = ref('')

async function startRoleChat(id: string): Promise<void> {
  // 连点同一张角色卡只跑一次查询 / 打开；openDraft 本身也会去重。
  if (openingRole.value === id) return
  openingRole.value = id
  try {
    let role = data.roles.find((item) => item.id === id)
    if (!role && !data.loading) {
      // 数据可能刚被其他窗口刷新过：再拉一次，避免点了没反应。
      await data.loadAll()
      role = data.roles.find((item) => item.id === id)
    }
    if (!role) {
      app.toast('角色不存在或已删除', 'bad')
      return
    }
    if (role.scope === 'temporary') {
      if (role.session_id) {
        chatWindows.openConversation(role.session_id)
        return
      }
      app.toast('临时角色属于某个会话，请在该会话里使用', 'bad')
      return
    }
    const rid = role.id
    const localChats = data.conversations.filter(
      (conv) => conv.role_id === rid && isRoleChat(conv),
    )
    let existing = localChats.find(hasMessages) || localChats[0] || null
    if (!existing) {
      // 最近 80 条里没有时再按 role_id 查一次：避免重复建单聊。
      try {
        const out = await api<ListResponse<Conversation>>(
          `/conversations?role_id=${encodeURIComponent(rid)}&limit=50`,
        )
        const chats = (out.items || []).filter(isRoleChat)
        existing = chats.find(hasMessages) || chats[0] || null
      } catch {
        // 查询失败不阻塞：退回到草稿窗口，消息发出后仍可正常落库。
      }
    }
    if (existing) {
      chatWindows.openConversation(existing.id)
      return
    }
    chatWindows.openDraft(rid)
  } finally {
    openingRole.value = ''
  }
}

function startRoleWork(id: string): void {
  const role = data.roles.find((item) => item.id === id)
  closeRole()
  workSeed.value = { role_id: id, body: `来自角色「${role?.name || id}」\n\n` }
  workOpen.value = true
}

function openDeptRoot(): void {
  editingDept.value = null
  deptParent.value = ''
  deptEditorOpen.value = true
}

function openChildDept(node: DeptNode): void {
  editingDept.value = null
  deptParent.value = node.id
  deptEditorOpen.value = true
}

function editDept(node: DeptNode): void {
  editingDept.value = data.departments.find((dept) => dept.id === node.id) || null
  deptParent.value = ''
  deptEditorOpen.value = true
}

async function deleteDept(node: DeptNode): Promise<void> {
  if (!window.confirm(`删除部门「${node.label}」？\n子部门会移到上一级，部门内角色会变成「未安排」。`)) return
  try {
    const out = await api<{ children_moved?: number; roles_unassigned?: number }>(`/departments/${encodeURIComponent(node.id)}`, { method: 'DELETE' })
    if (selectedDept.value === node.id) selectedDept.value = 'all'
    await data.loadAll()
    const moved = Number(out?.children_moved || 0)
    const unassigned = Number(out?.roles_unassigned || 0)
    app.toast(`部门已删除${moved ? ` · ${moved} 个子部门已上移` : ''}${unassigned ? ` · ${unassigned} 个角色转为未安排` : ''}`)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

function onDeptCommand(command: string, node: DeptNode): void {
  if (command === 'child') openChildDept(node)
  else if (command === 'edit') editDept(node)
  else if (command === 'delete') void deleteDept(node)
}

function onDeptSaved(id: string): void {
  deptEditorOpen.value = false
  selectedDept.value = id
}

function onWorkSaved(): void {
  workOpen.value = false
}
</script>

<template>
  <PageHeader section="组织与角色" :title="headTitle" :chip="`${visibleRoles.length} 个角色`">
    <el-button @click="openDeptRoot">＋ 部门</el-button>
    <el-button type="primary" @click="openNewRole">＋ 新建角色</el-button>
  </PageHeader>

  <div class="canvas-body">
    <div class="org-layout">
      <aside class="org-tree">
        <div class="org-tree-head">
          <b>组织</b>
          <button class="mini-link" type="button" @click="openDeptRoot">＋ 根部门</button>
        </div>
        <button class="dept-flat" :class="{ active: selectedDept === 'all' }" type="button" @click="selectedDept = 'all'">
          <span class="dept-name">全部角色</span><span class="dept-count">{{ orgRoles.length }}</span>
        </button>
        <button class="dept-flat" :class="{ active: selectedDept === '' }" type="button" @click="selectedDept = ''">
          <span class="dept-name">未安排</span><span class="dept-count">{{ orgRoles.filter((r) => !r.department_id).length }}</span>
        </button>
        <div class="org-tree-sep" />
        <el-tree
          v-if="deptTree.length"
          :data="deptTree"
          node-key="id"
          default-expand-all
          highlight-current
          :expand-on-click-node="false"
          :current-node-key="selectedDept === 'all' || selectedDept === '' ? '' : selectedDept"
          @node-click="onDeptNode"
        >
          <template #default="{ data: node }">
            <span class="dept-node">
              <Avatar :name="node.label" :avatar="node.avatar" xs />
              <span class="dept-name">{{ node.label }}</span>
              <span class="dept-count">{{ node.count }}</span>
              <el-dropdown trigger="click" @command="(cmd: string) => onDeptCommand(cmd, node)">
                <button class="dept-more" type="button" title="部门操作" aria-label="部门操作" @click.stop>⋯</button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="child">新建子部门</el-dropdown-item>
                    <el-dropdown-item command="edit">编辑部门</el-dropdown-item>
                    <el-dropdown-item command="delete" divided>删除部门</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </span>
          </template>
        </el-tree>
        <div v-else class="org-tree-empty">还没有部门<br />用「＋ 根部门」开始</div>
      </aside>

      <section class="org-main">
        <div class="org-main-head">
          <div>
            <h2>{{ headTitle }}</h2>
            <p>{{ headHint }}</p>
          </div>
          <span class="org-count">{{ visibleRoles.length }} 个角色</span>
        </div>

        <div class="list-toolbar">
          <el-input v-model="query" placeholder="搜索名字 / 岗位 / 部门 / 模型 / 工具" clearable />
          <div class="seg" role="group" aria-label="角色范围">
            <button class="seg-btn" :class="{ on: scope === 'all' }" type="button" @click="scope = 'all'">全部</button>
            <button class="seg-btn" :class="{ on: scope === 'persistent' }" type="button" @click="scope = 'persistent'">持久</button>
            <button class="seg-btn" :class="{ on: scope === 'temporary' }" type="button" @click="scope = 'temporary'">临时</button>
          </div>
          <button v-if="filtersActive" class="mini-link" type="button" @click="clearFilters">清除筛选</button>
        </div>

        <div class="role-grid">
          <article
            v-for="role in pagedRoles"
            :key="role.id"
            class="role-card"
            role="button"
            tabindex="0"
            title="点击开始聊天；右键查看更多操作"
            @click="startRoleChat(role.id)"
            @keydown.enter.prevent="startRoleChat(role.id)"
            @keydown.space.prevent="startRoleChat(role.id)"
            @contextmenu.prevent.stop="openMenu($event, role)"
          >
            <div class="role-top">
              <Avatar :name="role.name" :avatar="role.avatar" round />
              <span style="min-width:0">
                <b>{{ role.name }}</b><br />
                <span class="role-line">{{ role.title || role.expertise || '未填写岗位' }}</span>
              </span>
              <span class="role-presence" :class="{ off: role.status === 'offline' }" :title="role.status === 'offline' ? '离线' : '在线'">
                <i />{{ role.status === 'offline' ? '离线' : '在线' }}
              </span>
              <span class="tag" :class="{ tmp: role.scope === 'temporary' }" style="margin-left:auto">
                {{ role.scope === 'temporary' ? '临时' : (role.source === 'llm' ? 'LLM' : '持久') }}
              </span>
            </div>
            <div class="role-line">{{ role.department ? `部门：${role.department}` : '未安排部门' }}</div>
            <div class="role-line">模型：{{ role.model_code || '未指定' }}</div>
            <div class="role-line">工作区：{{ workspaceName(role.workspace_id) || '默认工作区' }}</div>
            <div class="chips">
              <span v-if="openWorkByRole.get(role.id)" class="chip work-chip">{{ openWorkByRole.get(role.id) }} 个工单在办</span>
              <span v-for="tool in (role.tools || []).slice(0, 6)" :key="tool" class="chip">{{ tool }}</span>
              <span v-if="!(role.tools || []).length" class="chip">全部工具</span>
            </div>
          </article>
        </div>
        <div v-if="!visibleRoles.length" class="empty">
          <template v-if="filtersActive">没有符合筛选条件的角色<br /><button class="mini-link" type="button" @click="clearFilters">清除筛选</button></template>
          <template v-else>{{ orgRoles.length ? '这个范围还没有角色' : '还没有角色，点右上角新建' }}</template>
        </div>
        <ListPager :page="page" :pages="pages" :total="visibleRoles.length" :page-size="PAGE_SIZE" @update:page="page = $event" />
      </section>
    </div>
  </div>

  <RoleEditorDialog
    :open="editorOpen"
    :role="editingRole"
    :preset-department-id="presetDept"
    @close="editorOpen = false"
    @saved="onRoleSaved"
  />

  <RoleSettingsDrawer
    :open="Boolean(selectedRole)"
    :role="selectedRole"
    @close="closeRole"
    @edit="openEditRole"
    @saved="onRoleSaved(roleId)"
    @deleted="onRoleDeleted"
    @chat="startRoleChat"
    @work="startRoleWork"
  />

  <DepartmentEditorDialog
    :open="deptEditorOpen"
    :dept="editingDept"
    :parent-id="deptParent"
    @close="deptEditorOpen = false"
    @saved="onDeptSaved"
  />

  <WorkEditorDialog :open="workOpen" :seed="workSeed" @close="workOpen = false" @saved="onWorkSaved" />

  <ContextMenu
    :open="menu.open"
    :x="menu.x"
    :y="menu.y"
    :items="menuItems"
    @close="menu.open = false"
    @select="onMenu"
  />
</template>

<style scoped>
.role-presence { display: inline-flex; align-items: center; gap: 4px; color: #15803d; font-size: 11px; }
.role-presence i { width: 6px; height: 6px; border-radius: 50%; background: #22c55e; }
.role-presence.off { color: var(--faint); }
.role-presence.off i { background: var(--faint); }
</style>

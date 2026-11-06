<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Avatar from '@/components/Avatar.vue'
import ContextMenu, { type ContextMenuItem } from '@/components/ContextMenu.vue'
import DepartmentEditorDialog from '@/components/DepartmentEditorDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import RoleEditorDialog from '@/components/RoleEditorDialog.vue'
import RoleSettingsDrawer from '@/components/RoleSettingsDrawer.vue'
import WorkEditorDialog, { type WorkSeed } from '@/components/WorkEditorDialog.vue'
import { api } from '@/api/client'
import type { Conversation, Department, Role } from '@/api/types'
import { departmentPath, departmentRoleCounts, departmentSubtree, orderedDepartments } from '@/lib/derive'
import { useAppStore } from '@/stores/app'
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
    if (!window.confirm(`删除角色「${role.name}」？引用它的群组 / 会话会失去该角色。`)) return
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

const visibleRoles = computed(() => {
  const scoped = data.roles.filter((role) => {
    if (selectedDept.value === 'all') return true
    if (selectedDept.value === '') return !role.department_id
    return departmentSubtree(selectedDept.value, data.departments).has(role.department_id || '')
  })
  return scoped
})

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
  if (selectedDept.value === 'all') return '按部门管理角色；角色也可以不安排部门，归入「未安排」。'
  if (selectedDept.value === '') return '这些角色还没有安排部门，编辑角色即可归档到部门。'
  return '包含该部门及其子部门的角色。'
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

async function startRoleChat(id: string): Promise<void> {
  const role = data.roles.find((item) => item.id === id)
  if (!role) return
  try {
    const conv = await api<Conversation>('/conversations', { method: 'POST', body: { role_id: role.id } })
    await data.loadAll()
    void router.push(`/conv/${conv.id}`)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
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
          <span class="dept-name">全部角色</span><span class="dept-count">{{ data.roles.length }}</span>
        </button>
        <button class="dept-flat" :class="{ active: selectedDept === '' }" type="button" @click="selectedDept = ''">
          <span class="dept-name">未安排</span><span class="dept-count">{{ data.roles.filter((r) => !r.department_id).length }}</span>
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

        <div class="role-grid">
          <article v-for="role in visibleRoles" :key="role.id" class="role-card" @click="openRole(role)" @contextmenu.prevent.stop="openMenu($event, role)">
            <div class="role-top">
              <Avatar :name="role.name" :avatar="role.avatar" round />
              <span style="min-width:0">
                <b>{{ role.name }}</b><br />
                <span class="role-line">{{ role.title || role.expertise || '未填写岗位' }}</span>
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
        <div v-if="!visibleRoles.length" class="empty">{{ data.roles.length ? '这个范围还没有角色' : '还没有角色，点右上角新建' }}</div>
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

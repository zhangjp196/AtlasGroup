import type { Conversation, Department, Role, Team } from '@/api/types'

export const GLOBAL_AGENT_ZONE = 'global-agent'
export const GLOBAL_CHAT_ZONE = 'global-chat'

export const MODE_LABEL: Record<string, string> = {
  orchestrate: '主持编排',
  roundtable: '圆桌',
  single: '单角色',
}

export function modeLabel(mode?: string): string {
  return MODE_LABEL[String(mode || '')] || '主持编排'
}

export function roleLabel(role?: Role | null): string {
  if (!role) return ''
  const bits = [role.title, role.department].filter(Boolean)
  return bits.length ? `${role.name || role.id} · ${bits.join(' · ')}` : (role.name || role.id || '')
}

export function conversationParts(conv: Conversation, teams: Team[], roles: Role[]) {
  const team = teams.find((item) => item.id === conv.team_id) || null
  const members = team?.members || []
  const role = roles.find((item) => item.id === conv.role_id) || null
  // 共享群组即使暂时没有成员也仍是群聊（只是空群）；私有参与团队没有成员则按单聊。
  const isGroup = Boolean(team && (members.length || String(team.kind || '') === 'group'))
  return { team, members, role, isGroup }
}

export function conversationTitle(conv: Conversation, teams: Team[], roles: Role[]): string {
  if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) return '全局Agent'
  if (String(conv.zone || '') === GLOBAL_CHAT_ZONE) return '全局对话'
  const { team, role, isGroup } = conversationParts(conv, teams, roles)
  if (isGroup && team) return team.name
  // 兜底角色不占标题：单角色会话优先显示用户/AI 生成的会话标题。
  if (role && role.id !== 'default') return role.name
  return conv.title || role?.name || '未命名会话'
}

export function conversationMeta(conv: Conversation, teams: Team[], roles: Role[]): { kind: string; sub: string } {
  if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) {
    return { kind: '全局Agent', sub: '全局消息 · 跨会话通知' }
  }
  if (String(conv.zone || '') === GLOBAL_CHAT_ZONE) {
    const { members } = conversationParts(conv, teams, roles)
    return { kind: '全局对话', sub: `${members.length} 个参与角色` }
  }
  const { team, role, members, isGroup } = conversationParts(conv, teams, roles)
  if (isGroup && team) {
    const shared = String(team.kind || '') === 'group'
    return {
      kind: shared ? '群聊' : '多角色',
      sub: shared
        ? `${members.length} 个角色 · ${modeLabel(team.mode)}`
        : `${members.length} 个角色`,
    }
  }
  return { kind: '单角色', sub: role ? `角色：${roleLabel(role)}` : '未指定角色' }
}

export function conversationIsGroup(conv: Conversation, teams: Team[]): boolean {
  const team = teams.find((item) => item.id === conv.team_id) || null
  return Boolean(team && ((team.members || []).length || String(team.kind || '') === 'group'))
}

/** 是否绑定到用户可管理的共享群组（participants-* 只是会话私有参与角色）。 */
export function conversationIsSharedGroup(conv: Conversation, teams: Team[]): boolean {
  const team = teams.find((item) => item.id === conv.team_id) || null
  return String(team?.kind || '') === 'group'
}

export function departmentPath(id: string | undefined, departments: Department[]): string {
  const seen = new Set<string>()
  const names: string[] = []
  let current = departments.find((item) => item.id === id)
  while (current && !seen.has(current.id)) {
    seen.add(current.id)
    names.push(current.name)
    current = departments.find((item) => item.id === current?.parent_id)
  }
  return names.reverse().join(' / ')
}

export function orderedDepartments(departments: Department[]): Array<Department & { depth: number }> {
  const out: Array<Department & { depth: number }> = []
  const walk = (parentId: string, depth: number) => {
    departments
      .filter((item) => (item.parent_id || '') === parentId)
      .forEach((item) => {
        out.push({ ...item, depth })
        walk(item.id, depth + 1)
      })
  }
  walk('', 0)
  return out
}

/** 每个部门（含所有子部门）的角色数。
 *
 * 组织树里的父部门不能只显示直属角色：右侧列表选父部门时本来就包含子部门，
 * 两边口径必须一致，否则会出现「父部门 0、子部门各有角色」的怪计数。
 */
export function departmentRoleCounts(
  departments: Department[],
  roles: Array<{ department_id?: string }>,
): Map<string, number> {
  const children = new Map<string, string[]>()
  for (const dept of departments) {
    const pid = String(dept.parent_id || '')
    if (!pid) continue
    const list = children.get(pid) || []
    list.push(dept.id)
    children.set(pid, list)
  }
  const direct = new Map<string, number>()
  for (const role of roles) {
    const did = String(role.department_id || '')
    if (did) direct.set(did, (direct.get(did) || 0) + 1)
  }
  const memo = new Map<string, number>()
  const visiting = new Set<string>()
  const count = (id: string): number => {
    const cached = memo.get(id)
    if (cached !== undefined) return cached
    if (visiting.has(id)) return 0          // 脏数据成环：停止递归，避免栈溢出
    visiting.add(id)
    let total = direct.get(id) || 0
    for (const child of children.get(id) || []) total += count(child)
    visiting.delete(id)
    memo.set(id, total)
    return total
  }
  const out = new Map<string, number>()
  for (const dept of departments) out.set(dept.id, count(dept.id))
  return out
}

export function departmentSubtree(id: string, departments: Department[]): Set<string> {
  const out = new Set<string>([String(id)])
  let changed = true
  while (changed) {
    changed = false
    for (const item of departments) {
      if (!out.has(item.id) && out.has(item.parent_id || '')) {
        out.add(item.id)
        changed = true
      }
    }
  }
  return out
}

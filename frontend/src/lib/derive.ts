import type { Conversation, Department, Role, Team } from '@/api/types'

export const GLOBAL_AGENT_ZONE = 'global-agent'

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
  return { team, members, role, isGroup: Boolean(team && members.length) }
}

export function conversationTitle(conv: Conversation, teams: Team[], roles: Role[]): string {
  if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) return '全局Agent'
  const { team, role, isGroup } = conversationParts(conv, teams, roles)
  if (isGroup && team) return team.name
  if (role) return role.name
  return conv.title || '未命名会话'
}

export function conversationMeta(conv: Conversation, teams: Team[], roles: Role[]): { kind: string; sub: string } {
  if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) {
    return { kind: '全局Agent', sub: '全局消息 · 跨会话通知' }
  }
  const { team, role, members, isGroup } = conversationParts(conv, teams, roles)
  if (isGroup && team) {
    return { kind: '群聊', sub: `${members.length} 个角色 · ${modeLabel(team.mode)}` }
  }
  return { kind: '单聊', sub: role ? `角色：${roleLabel(role)}` : '默认角色' }
}

export function conversationIsGroup(conv: Conversation, teams: Team[]): boolean {
  const team = teams.find((item) => item.id === conv.team_id) || null
  return Boolean(team && (team.members || []).length)
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

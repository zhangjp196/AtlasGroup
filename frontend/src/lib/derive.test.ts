import { describe, expect, it } from 'vitest'
import { conversationMeta, conversationTitle, departmentPath, departmentRoleCounts } from './derive'
import type { Conversation, Department, Role, Team } from '@/api/types'

const roles: Role[] = [{ id: 'r1', name: '后端', title: '工程师', department: '研发' }]
const teams: Team[] = [{
  id: 't1',
  name: '产品群',
  mode: 'roundtable',
  members: [{ id: 'r1', name: '后端' }, { id: 'r2', name: '前端' }],
}]
const departments: Department[] = [
  { id: 'd1', name: '研发' },
  { id: 'd2', name: '平台', parent_id: 'd1' },
]

describe('conversation derivation', () => {
  it('uses team name for multi-role chats', () => {
    const conv: Conversation = { id: 'c1', team_id: 't1' }
    expect(conversationTitle(conv, teams, roles)).toBe('产品群')
    expect(conversationMeta(conv, teams, roles).kind).toBe('多角色')
  })

  it('uses role name for single-role chats', () => {
    const conv: Conversation = { id: 'c2', role_id: 'r1' }
    expect(conversationTitle(conv, teams, roles)).toBe('后端')
    expect(conversationMeta(conv, teams, roles).kind).toBe('单角色')
  })

  it('prefers the conversation title over the generic default role', () => {
    const conv: Conversation = { id: 'c3', role_id: 'default', title: '重构登录' }
    expect(conversationTitle(conv, teams, roles)).toBe('重构登录')
    expect(conversationMeta(conv, teams, roles).sub).toBe('未指定角色')
  })

  it('keeps the global agent identity even if a legacy team is bound', () => {
    const conv: Conversation = {
      id: 'g1', zone: 'global-agent', role_id: 'global-agent', team_id: 't1',
    }
    expect(conversationTitle(conv, teams, roles)).toBe('全局Agent')
    expect(conversationMeta(conv, teams, roles).kind).toBe('全局Agent')
  })

  it('builds department paths', () => {
    expect(departmentPath('d2', departments)).toBe('研发 / 平台')
  })
})

describe('department role counts', () => {
  it('counts sub-departments into their parents', () => {
    const tree: Department[] = [
      { id: 'root', name: '技术研发部' },
      { id: 'fe', name: '前端组', parent_id: 'root' },
      { id: 'be', name: '后端组', parent_id: 'root' },
      { id: 'bff', name: 'BFF', parent_id: 'be' },
    ]
    const members = [
      { department_id: 'root' },
      { department_id: 'fe' },
      { department_id: 'be' },
      { department_id: 'bff' },
      { department_id: '' },
      {},
    ]
    const counts = departmentRoleCounts(tree, members)
    expect(counts.get('root')).toBe(4)
    expect(counts.get('be')).toBe(2)
    expect(counts.get('bff')).toBe(1)
    expect(counts.get('fe')).toBe(1)
  })

  it('does not loop forever on malformed cycles', () => {
    const tree: Department[] = [
      { id: 'a', name: 'A', parent_id: 'b' },
      { id: 'b', name: 'B', parent_id: 'a' },
    ]
    const counts = departmentRoleCounts(tree, [{ department_id: 'a' }])
    expect(counts.get('a')).toBe(1)
    // 环上的反向边被截断：b 只统计自己的直属角色，不会无限递归。
    expect(counts.get('b')).toBe(0)
  })
})

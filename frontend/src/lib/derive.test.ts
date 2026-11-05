import { describe, expect, it } from 'vitest'
import { conversationMeta, conversationTitle, departmentPath } from './derive'
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
  it('uses team name for group chats', () => {
    const conv: Conversation = { id: 'c1', team_id: 't1' }
    expect(conversationTitle(conv, teams, roles)).toBe('产品群')
    expect(conversationMeta(conv, teams, roles).kind).toBe('群聊')
  })

  it('uses role name for single chats', () => {
    const conv: Conversation = { id: 'c2', role_id: 'r1' }
    expect(conversationTitle(conv, teams, roles)).toBe('后端')
    expect(conversationMeta(conv, teams, roles).kind).toBe('单聊')
  })

  it('builds department paths', () => {
    expect(departmentPath('d2', departments)).toBe('研发 / 平台')
  })
})

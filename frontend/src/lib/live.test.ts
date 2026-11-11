import { describe, expect, it } from 'vitest'
import {
  expectedSpeaker,
  findPerson,
  groupBySpeaker,
  REVEAL_MIN_CHARS,
  revealStep,
  speakerOf,
} from './live'
import type { Conversation, Role, Team } from '@/api/types'

const roles: Role[] = [
  { id: 'host', name: '主持人', title: '主持' },
  { id: 'be', name: '后端', title: '工程师', department: '研发' },
]
const teams: Team[] = [{
  id: 't1',
  name: '产品群',
  mode: 'roundtable',
  leader: { id: 'host', name: '主持人', title: '主持' },
  members: [{ id: 'be', name: '后端', title: '工程师', department: '研发' }],
}]

describe('live speaker', () => {
  it('formats role name / subtitle', () => {
    expect(speakerOf(roles[1])).toEqual({
      id: 'be', name: '后端', avatar: '', sub: '工程师 · 研发',
    })
  })

  it('falls back to a provided name then to the generic assistant', () => {
    expect(speakerOf(null)).toEqual({ id: '', name: '助手', avatar: '', sub: '' })
  })

  it('finds temporary team members that are not in the role list', () => {
    const temp: Team = { ...teams[0], members: [{ id: 'tmp', name: '临时顾问' }] }
    expect(findPerson(roles, [temp], 'tmp')?.name).toBe('临时顾问')
    expect(findPerson(roles, [temp], 'missing')).toBeNull()
  })

  it('expects the mentioned member, else the group leader', () => {
    const conv: Conversation = { id: 'c1', team_id: 't1' }
    expect(expectedSpeaker(conv, teams, roles)?.name).toBe('主持人')
    expect(expectedSpeaker(conv, teams, roles, ['后端'])?.name).toBe('后端')
    // 名字大小写 / 空格不影响匹配
    expect(expectedSpeaker(conv, teams, roles, [' 后端 '])?.name).toBe('后端')
  })

  it('uses the bound role for single chats and never fabricates 助手 from a group', () => {
    expect(expectedSpeaker({ id: 'c2', role_id: 'be' }, teams, roles)?.name).toBe('后端')
    expect(expectedSpeaker({ id: 'c3' }, teams, roles)).toBeNull()
    const empty: Team = { id: 't9', name: '空群', members: [] }
    expect(expectedSpeaker({ id: 'c4', team_id: 't9' }, [empty], roles)?.name).toBe('空群')
  })
})

describe('groupBySpeaker', () => {
  it('keeps one block per consecutive speaker and preserves order', () => {
    const groups = groupBySpeaker([
      { speaker: 'a', value: 1 },
      { speaker: 'a', value: 2 },
      { speaker: 'b', value: 3 },
      { speaker: 'a', value: 4 },
    ])
    expect(groups.map((group) => group.speaker)).toEqual(['a', 'b', 'a'])
    expect(groups.map((group) => group.entries.length)).toEqual([2, 1, 1])
    expect(groups[2].entries[0].value).toBe(4)
  })

  it('treats missing speaker as its own block (start 事件之前)', () => {
    const groups = groupBySpeaker([{ value: 1 }, { speaker: 'a', value: 2 }])
    expect(groups.map((group) => group.speaker)).toEqual(['', 'a'])
  })
})

describe('revealStep', () => {
  it('reveals nothing when caught up', () => {
    expect(revealStep(0)).toBe(0)
    expect(revealStep(-5)).toBe(0)
  })

  it('keeps small chunks readable and still drains them', () => {
    // 几个字的增量：当拍就显示出来，不拖延、也不一次吐超过已到达的量。
    expect(revealStep(1)).toBe(1)
    expect(revealStep(3)).toBe(3)
    expect(revealStep(8)).toBe(REVEAL_MIN_CHARS)
  })

  it('catches up a 120-char burst in about 0.5s of ticks', () => {
    let left = 120
    let ticks = 0
    while (left > 0 && ticks < 50) {
      left -= revealStep(left)
      ticks += 1
    }
    // 40ms 一拍：≤12 拍 = 480ms，肉眼是连续的，不会整块闪出。
    expect(ticks).toBeLessThanOrEqual(12)
  })

  it('never reveals more than what has arrived', () => {
    expect(revealStep(1)).toBeLessThanOrEqual(1)
    expect(revealStep(5)).toBeLessThanOrEqual(5)
  })
})

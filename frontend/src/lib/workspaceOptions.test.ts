import { describe, expect, it } from 'vitest'
import type { Workspace } from '@/api/types'
import { filterWorkspaces, matchesWorkspace, pageCount, pageSlice } from './workspaceOptions'

const workspaces: Workspace[] = [
  { id: 'ws-1', name: '产品', root_path: '/work/product' },
  { id: 'ws-2', name: '研发', root_path: '/work/dev', archived: true },
  { id: 'ws-3', name: '设计', root_path: '/work/design' },
]

describe('workspaceOptions', () => {
  it('matches name, path and id', () => {
    expect(matchesWorkspace(workspaces[0], '产品')).toBe(true)
    expect(matchesWorkspace(workspaces[0], 'product')).toBe(true)
    expect(matchesWorkspace(workspaces[0], 'ws-1')).toBe(true)
    expect(matchesWorkspace(workspaces[0], '研发')).toBe(false)
    expect(matchesWorkspace(workspaces[0], '  ')).toBe(true)
  })

  it('filters archived workspaces in active scope', () => {
    expect(filterWorkspaces(workspaces, '', 'active').map((item) => item.id)).toEqual(['ws-1', 'ws-3'])
    expect(filterWorkspaces(workspaces, '', 'all').map((item) => item.id)).toEqual(['ws-1', 'ws-2', 'ws-3'])
    expect(filterWorkspaces(workspaces, '研发', 'active')).toEqual([])
    expect(filterWorkspaces(workspaces, '研发', 'all').map((item) => item.id)).toEqual(['ws-2'])
  })

  it('paginates and clamps page count', () => {
    expect(pageSlice(workspaces, 1, 2).map((item) => item.id)).toEqual(['ws-1', 'ws-2'])
    expect(pageSlice(workspaces, 2, 2).map((item) => item.id)).toEqual(['ws-3'])
    expect(pageSlice(workspaces, 99, 2)).toEqual([])
    expect(pageCount(9, 4)).toBe(3)
    expect(pageCount(0, 4)).toBe(1)
    expect(pageCount(9, 0)).toBe(9)
  })
})

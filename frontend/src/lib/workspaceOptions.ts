import type { Workspace } from '@/api/types'

export type WorkspaceStatusFilter = 'active' | 'all'

/** 用统一名称展示未指定工作区和已删除工作区，避免不同页面语义不一致。 */
export function workspaceLabel(id: string | undefined, workspaces: Workspace[]): string {
  const workspaceId = String(id || '').trim()
  if (!workspaceId || workspaceId === 'default') return '默认工作区'
  return workspaces.find((workspace) => workspace.id === workspaceId)?.name || '工作区不存在'
}

/** 工作区筛选：名称 / 路径 / id 模糊匹配。 */
export function matchesWorkspace(workspace: Workspace, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return `${workspace.name || ''} ${workspace.root_path || ''} ${workspace.id || ''}`
    .toLowerCase()
    .includes(q)
}

export function filterWorkspaces(
  workspaces: Workspace[],
  query: string,
  status: WorkspaceStatusFilter,
): Workspace[] {
  return workspaces.filter((workspace) => {
    if (status === 'active' && workspace.archived) return false
    return matchesWorkspace(workspace, query)
  })
}

// 分页是通用能力，实现放在 lib/paging；这里保持原有导出路径不变。
export { pageCount, pageSlice } from './paging'

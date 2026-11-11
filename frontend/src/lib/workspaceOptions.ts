import type { Workspace } from '@/api/types'

export type WorkspaceStatusFilter = 'active' | 'all'

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

/** 列表页通用分页（纯函数，前端本地分页）。 */

/** 所有列表统一分页：每页最多 12 条，超过 12 条必须翻页。 */
export const DEFAULT_PAGE_SIZE = 12

export function pageSlice<T>(items: T[], page: number, size: number): T[] {
  const safeSize = Math.max(1, Math.floor(size))
  const safePage = Math.max(1, Math.floor(page) || 1)
  return items.slice((safePage - 1) * safeSize, safePage * safeSize)
}

export function pageCount(total: number, size: number): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, Math.floor(size))))
}

/** 把页码夹到 [1, pages]，避免筛选后页码越界显示空白。 */
export function clampPage(page: number, pages: number): number {
  return Math.min(Math.max(1, Math.floor(page) || 1), Math.max(1, Math.floor(pages) || 1))
}

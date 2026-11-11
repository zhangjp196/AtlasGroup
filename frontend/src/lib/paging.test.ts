import { describe, expect, it } from 'vitest'
import { clampPage, DEFAULT_PAGE_SIZE, pageCount, pageSlice } from './paging'

describe('paging', () => {
  it('slices pages without overlap and handles the last page', () => {
    const items = Array.from({ length: 25 }, (_, i) => i)
    expect(pageSlice(items, 1, 10)).toHaveLength(10)
    expect(pageSlice(items, 2, 10)).toEqual([10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
    expect(pageSlice(items, 3, 10)).toEqual([20, 21, 22, 23, 24])
    expect(pageSlice(items, 9, 10)).toEqual([])
  })

  it('falls back to page 1 for invalid page numbers', () => {
    const items = [1, 2, 3]
    expect(pageSlice(items, 0, 10)).toEqual([1, 2, 3])
    expect(pageSlice(items, -3, 10)).toEqual([1, 2, 3])
    expect(pageSlice(items, Number.NaN, 10)).toEqual([1, 2, 3])
  })

  it('computes page count with a single empty page', () => {
    expect(pageCount(0, 10)).toBe(1)
    expect(pageCount(1, 10)).toBe(1)
    expect(pageCount(10, 10)).toBe(1)
    expect(pageCount(11, 10)).toBe(2)
    expect(pageCount(25, DEFAULT_PAGE_SIZE)).toBe(3)
  })

  it('clamps pages into range after a filter shrinks the list', () => {
    expect(clampPage(5, 2)).toBe(2)
    expect(clampPage(0, 2)).toBe(1)
    expect(clampPage(-1, 0)).toBe(1)
    expect(clampPage(2, 2)).toBe(2)
  })
})

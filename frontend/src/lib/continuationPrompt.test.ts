import { describe, expect, it } from 'vitest'
import { continuationPrompt } from './continuationPrompt'

describe('continuationPrompt', () => {
  it('asks to inspect existing work before continuing an interrupted reply', () => {
    const prompt = continuationPrompt({ interrupted: true })
    expect(prompt).toContain('上一条回复被中断')
    expect(prompt).toContain('检查当前工作区和已有改动')
    expect(prompt).toContain('避免重复执行有副作用的操作')
  })

  it('identifies a failed reply', () => {
    expect(continuationPrompt({ error: 'network error' })).toContain('上一条回复执行失败')
  })
})

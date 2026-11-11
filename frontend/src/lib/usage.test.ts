import { describe, expect, it } from 'vitest'
import { compactNumber } from './format'
import { cacheTone, hasUsage, mergeUsage, usageFromCounts, usageSummary } from './usage'

describe('compactNumber', () => {
  it('keeps small counts readable', () => {
    expect(compactNumber(0)).toBe('0')
    expect(compactNumber(999)).toBe('999')
  })

  it('shortens thousands and millions', () => {
    expect(compactNumber(1200)).toBe('1.2k')
    expect(compactNumber(12500)).toBe('12.5k')
    expect(compactNumber(123456)).toBe('123k')
    expect(compactNumber(1234567)).toBe('1.2M')
  })
})

describe('usageSummary', () => {
  it('derives cache rate and speed from raw counters', () => {
    const view = usageSummary({
      prompt_tokens: 3000,
      completion_tokens: 500,
      cached_tokens: 1100,
      llm_ms: 10_000,
      rounds: 5,
      turns: 2,
    })
    expect(view.input).toBe(3000)
    expect(view.output).toBe(500)
    expect(view.total).toBe(3500)
    expect(view.cached).toBe(1100)
    expect(view.cacheRate).toBe(36.7)
    expect(view.speed).toBe(50)
    expect(view.turns).toBe(2)
  })

  it('trusts backend derived values when raw counters are missing', () => {
    const view = usageSummary({ cache_rate: 42.5, speed: 18.2, max_context_tokens: 9000 })
    expect(view.cacheRate).toBe(42.5)
    expect(view.speed).toBe(18.2)
    expect(view.context).toBe(9000)
  })

  it('does not report usage for an empty snapshot', () => {
    expect(hasUsage(null)).toBe(false)
    expect(hasUsage({})).toBe(false)
    expect(hasUsage({ total_tokens: 12 })).toBe(true)
  })
})

describe('mergeUsage', () => {
  it('adds the in-flight round on top of persisted usage', () => {
    const merged = mergeUsage(
      { prompt_tokens: 1000, completion_tokens: 100, cached_tokens: 600, llm_ms: 4000, turns: 1 },
      { prompt_tokens: 2000, completion_tokens: 300, cached_tokens: 500, llm_ms: 6000, rounds: 3 },
    )!
    const view = usageSummary(merged)
    expect(view.input).toBe(3000)
    expect(view.output).toBe(400)
    expect(view.total).toBe(3400)
    expect(view.cached).toBe(1100)
    expect(view.speed).toBe(40)
    expect(view.turns).toBe(1)
    expect(view.rounds).toBe(3)
  })

  it('keeps the live context size while running', () => {
    const merged = mergeUsage(
      { prompt_tokens: 500, completion_tokens: 50, last_prompt_tokens: 500 },
      { prompt_tokens: 700, completion_tokens: 20, last_prompt_tokens: 700 },
    )!
    expect(usageSummary(merged).context).toBe(700)
  })

  it('returns null when there is nothing to show', () => {
    expect(mergeUsage(null, null)).toBeNull()
  })
})

describe('cacheTone', () => {
  it('grades cache hit rate into good / mid / low', () => {
    expect(cacheTone(65)).toBe('good')
    expect(cacheTone(50)).toBe('good')
    expect(cacheTone(33.3)).toBe('mid')
    expect(cacheTone(19.9)).toBe('low')
  })

  it('reports no cache axis when there was no prompt', () => {
    expect(cacheTone(0, false)).toBe('none')
  })
})

describe('usageFromCounts', () => {
  it('turns aggregated work stats into a usage object', () => {
    const usage = usageFromCounts({
      prompt_tokens: 1000,
      completion_tokens: 100,
      cached_tokens: 500,
      llm_ms: 2000,
      rounds: 3,
      turns: 2,
    })
    const view = usageSummary(usage)
    expect(view.total).toBe(1100)
    expect(view.cacheRate).toBe(50)
    expect(view.speed).toBe(50)
    expect(view.rounds).toBe(3)
  })
})

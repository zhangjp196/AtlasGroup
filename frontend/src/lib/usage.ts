import type { TokenUsage } from '@/api/types'

/** 前端统一口径的用量视图（缺失字段按 0 兜底，派生值就地重算）。 */
export interface UsageSummary {
  input: number
  output: number
  total: number
  cached: number
  cacheRate: number
  speed: number
  rounds: number
  turns: number
  context: number
  maxContext: number
}

const NUMERIC_KEYS = [
  'prompt_tokens',
  'completion_tokens',
  'cached_tokens',
  'llm_ms',
  'rounds',
  'turns',
] as const

function num(value: unknown): number {
  const n = Number(value || 0)
  return Number.isFinite(n) && n > 0 ? n : 0
}

/** 把后端字段（可能来自旧版本 / 全局统计）归一成展示口径。 */
export function usageSummary(usage?: TokenUsage | null): UsageSummary {
  const input = num(usage?.prompt_tokens)
  const output = num(usage?.completion_tokens)
  const total = Math.max(input + output, num(usage?.total_tokens))
  const cached = num(usage?.cached_tokens)
  const llmMs = num(usage?.llm_ms)
  const turns = num(usage?.turns)
  const cacheRate = input
    ? Math.round((Math.min(cached, input) / input) * 1000) / 10
    : num(usage?.cache_rate)
  const speed = llmMs
    ? Math.round((output / (llmMs / 1000)) * 10) / 10
    : num(usage?.speed) || num(usage?.tokens_per_second)
  return {
    input,
    output,
    total,
    cached,
    cacheRate,
    speed,
    rounds: num(usage?.rounds),
    turns,
    context: num(usage?.last_prompt_tokens) || num(usage?.max_context_tokens),
    maxContext: num(usage?.max_context_tokens),
  }
}

export function hasUsage(usage?: TokenUsage | null): boolean {
  if (!usage) return false
  const s = usageSummary(usage)
  return s.total > 0 || s.rounds > 0 || s.turns > 0
}

/**
 * 已落库用量 + 运行中实时快照。
 *
 * 库里只统计已结束的轮次；运行中的这一轮由 SSE 的 usage 事件实时推送。
 * 两者相加才是「此刻」的会话用量，派生值（速度 / 缓存率）按合计重算。
 */
export function mergeUsage(
  base?: TokenUsage | null,
  live?: TokenUsage | null,
): TokenUsage | null {
  if (!base && !live) return null
  const out: TokenUsage = {}
  for (const key of NUMERIC_KEYS) {
    out[key] = num(base?.[key]) + num(live?.[key])
  }
  out.total_tokens = num(out.prompt_tokens) + num(out.completion_tokens)
  const livePrompt = num(live?.last_prompt_tokens)
  const basePrompt = num(base?.last_prompt_tokens) || num(base?.max_context_tokens)
  if (livePrompt || basePrompt) out.last_prompt_tokens = livePrompt || basePrompt
  const maxContext = Math.max(num(base?.max_context_tokens), num(live?.max_context_tokens))
  if (maxContext) out.max_context_tokens = maxContext
  return out
}

export type UsageTone = 'good' | 'mid' | 'low' | 'none'

/** 缓存命中率的视觉分级：≥50% 绿、≥20% 黄、其余红。 */
export function cacheTone(rate: number, hasInput = true): UsageTone {
  if (!hasInput) return 'none'
  const value = Number(rate || 0)
  if (value >= 50) return 'good'
  if (value >= 20) return 'mid'
  return 'low'
}

/** 工单统计（prompt/completion/cached/llm_ms/rounds）→ 用量对象。 */
export function usageFromCounts(counts?: Record<string, number | undefined> | null): TokenUsage {
  const value = (key: string): number => Number(counts?.[key] || 0)
  return {
    prompt_tokens: value('prompt_tokens'),
    completion_tokens: value('completion_tokens'),
    cached_tokens: value('cached_tokens'),
    llm_ms: value('llm_ms'),
    rounds: value('rounds'),
    turns: value('turns'),
  }
}

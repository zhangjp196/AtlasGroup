export interface UsageTotals {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  cached_tokens?: number
  messages?: number
  rounds?: number
  llm_ms?: number
  cache_rate?: number
  avg_tokens_per_message?: number
  avg_tokens_per_day?: number
  tokens_per_second?: number
  active_days?: number
  max_context_tokens?: number
}

export interface UsageRow {
  name?: string
  total_tokens?: number
  prompt_tokens?: number
  completion_tokens?: number
  cache_rate?: number
  messages?: number
  share?: number
  tokens_per_second?: number
}

export interface UsageDay {
  date: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  messages: number
}

export interface UsagePeriod {
  days?: number
  totals?: UsageTotals
  top_model?: UsageRow | null
  peak_day?: { date?: string; total_tokens?: number } | null
  models?: UsageRow[]
  channels?: UsageRow[]
}

export interface UsageStats {
  generated_at?: string
  periods?: Record<string, UsagePeriod>
  daily?: UsageDay[]
}

export type UsagePeriodKey = '1d' | '7d' | '30d' | 'all'

<script setup lang="ts">
import { computed } from 'vue'
import { ElButton } from 'element-plus'
import type { UsagePeriodKey, UsageStats } from '@/types/usage'

const props = defineProps<{
  usage: UsageStats | null
  loading: boolean
  period: UsagePeriodKey
}>()

const emit = defineEmits<{
  'update:period': [value: UsagePeriodKey]
  refresh: []
  openMagpie: []
}>()

const periods: Array<{ key: UsagePeriodKey; label: string }> = [
  { key: '1d', label: '1 天' },
  { key: '7d', label: '7 天' },
  { key: '30d', label: '30 天' },
  { key: 'all', label: '全部' },
]
const summary = computed(() => props.usage?.periods?.[props.period] || null)
const totals = computed(() => summary.value?.totals || {})
const daily = computed(() => props.usage?.daily || [])
const maxDailyTokens = computed(() => Math.max(1, ...daily.value.map((day) => Number(day.total_tokens || 0))))

function fmtTokens(value?: number): string {
  const n = Number(value || 0)
  if (n >= 100_000_000) return `${(n / 100_000_000).toFixed(2)} 亿`
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)} 万`
  return String(Math.round(n))
}

function fmtNumber(value?: number): string {
  return Number(value || 0).toLocaleString('zh-CN')
}

function barHeight(total: number): string {
  return `${Math.max(2, Math.round(Number(total || 0) / maxDailyTokens.value * 100))}%`
}
</script>

<template>
  <div v-if="loading && !usage" class="detail-section"><div class="empty">加载用量…</div></div>
  <template v-else>
    <div class="detail-section">
      <div class="toolbar" style="margin-bottom:0">
        <h3 style="margin:0">用量</h3>
        <span class="role-line">{{ usage?.generated_at ? `统计于 ${usage.generated_at}` : '本地用量统计' }}</span>
        <div class="usage-periods">
          <button
            v-for="item in periods"
            :key="item.key"
            class="work-filter"
            :class="{ on: period === item.key }"
            type="button"
            @click="emit('update:period', item.key)"
          >
            {{ item.label }}
          </button>
        </div>
        <el-button size="small" style="margin-left:auto" @click="emit('openMagpie')">本机脱敏</el-button>
        <el-button size="small" :loading="loading" @click="emit('refresh')">刷新</el-button>
      </div>
    </div>

    <div class="usage-grid">
      <div class="usage-stat"><span>总 Tokens</span><b>{{ fmtTokens(totals.total_tokens) }}</b><small>{{ fmtNumber(totals.total_tokens) }}</small></div>
      <div class="usage-stat"><span>输入 Tokens</span><b>{{ fmtTokens(totals.prompt_tokens) }}</b><small>{{ fmtNumber(totals.prompt_tokens) }}</small></div>
      <div class="usage-stat"><span>输出 Tokens</span><b>{{ fmtTokens(totals.completion_tokens) }}</b><small>{{ fmtNumber(totals.completion_tokens) }}</small></div>
      <div class="usage-stat"><span>缓存命中率</span><b>{{ totals.cache_rate ?? 0 }}%</b><small>{{ fmtTokens(totals.cached_tokens) }} 缓存</small></div>
      <div class="usage-stat"><span>消息数</span><b>{{ fmtNumber(totals.messages) }}</b><small>平均 {{ fmtNumber(totals.avg_tokens_per_message) }} tok/条</small></div>
      <div class="usage-stat"><span>平均每日</span><b>{{ fmtTokens(totals.avg_tokens_per_day) }}</b><small>活跃 {{ totals.active_days ?? 0 }} 天</small></div>
      <div class="usage-stat"><span>轮次</span><b>{{ fmtNumber(totals.rounds) }}</b><small>累计调用</small></div>
      <div class="usage-stat"><span>生成速度</span><b>{{ totals.tokens_per_second ?? 0 }} tok/s</b><small>{{ fmtNumber(totals.llm_ms) }} ms</small></div>
    </div>

    <div class="usage-coverage">
      <div class="usage-cover"><span>覆盖模型</span><b>{{ summary?.top_model?.name || '—' }}</b><small>{{ summary?.top_model ? `${fmtTokens(summary.top_model.total_tokens)} · ${summary.top_model.share ?? 0}%` : '还没有调用记录' }}</small></div>
      <div class="usage-cover"><span>最近活跃</span><b>{{ summary?.peak_day?.date || '—' }}</b><small>{{ summary?.peak_day ? `${fmtTokens(summary.peak_day.total_tokens)} tokens` : '还没有活跃记录' }}</small></div>
      <div class="usage-cover"><span>活跃天数</span><b>{{ totals.active_days ?? 0 }} 天</b><small>按有调用记录的日期统计</small></div>
    </div>

    <div class="detail-section">
      <div class="toolbar" style="margin-bottom:8px">
        <h3 style="margin:0">最近 30 天</h3>
        <span class="role-line" style="margin-left:auto">输入 / 输出合计</span>
      </div>
      <div class="usage-chart">
        <div v-for="(day, index) in daily" :key="day.date" class="usage-bar-col">
          <div class="usage-bar" :style="{ height: barHeight(day.total_tokens) }" :title="`${day.date} · ${fmtNumber(day.total_tokens)} tokens`" />
          <small v-if="index % 5 === 0 || index === daily.length - 1">{{ day.date.slice(5) }}</small>
        </div>
        <div v-if="!daily.length" class="empty">还没有用量记录</div>
      </div>
    </div>

    <div class="detail-section">
      <div class="toolbar" style="margin-bottom:8px">
        <h3 style="margin:0">模型排行</h3>
        <span class="role-line" style="margin-left:auto">{{ (summary?.models || []).length }} 个模型</span>
      </div>
      <div class="usage-rank-list">
        <div v-for="row in summary?.models || []" :key="row.name" class="usage-rank-row">
          <b>{{ row.name || '未知模型' }}</b>
          <span>{{ fmtTokens(row.total_tokens) }}</span>
          <small>{{ row.share ?? 0 }}% · 缓存 {{ row.cache_rate ?? 0 }}%</small>
        </div>
        <div v-if="!(summary?.models || []).length" class="empty">还没有调用记录</div>
      </div>
    </div>
  </template>
</template>

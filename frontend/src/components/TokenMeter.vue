<script setup lang="ts">
import { computed } from 'vue'
import type { TokenUsage } from '@/api/types'
import { compactNumber } from '@/lib/format'
import { cacheTone, usageSummary } from '@/lib/usage'

const props = withDefaults(defineProps<{
  usage?: TokenUsage | null
  /**
   * tile：卡片里的 4 列指标条（会话 / 工单卡片）；
   * inline：列表行内文字版；
   * hero：工单页头部的汇总卡（大数字 + 缓存环 + 速度）；
   * panel：抽屉 / 弹层里的完整面板。
   */
  variant?: 'tile' | 'inline' | 'hero' | 'panel'
  /** 当前上下文占用（会话详情才有；列表里用 last_prompt_tokens 兜底）。 */
  context?: number
  label?: string
  /** 外层已有标题时隐藏面板自带的标题行（抽屉里用）。 */
  headless?: boolean
}>(), { variant: 'tile', context: 0, label: '', headless: false })

const summary = computed(() => usageSummary(props.usage))
const contextTokens = computed(() => Number(props.context || summary.value.context || 0))
const visible = computed(() => summary.value.total > 0 || summary.value.rounds > 0)
const tone = computed(() => cacheTone(summary.value.cacheRate, summary.value.input > 0))
const ringStyle = computed(() => ({
  '--pct': String(Math.min(100, Math.max(0, summary.value.cacheRate))),
}))
const cacheWidth = computed(() => `${Math.min(100, Math.max(0, summary.value.cacheRate))}%`)
const speedText = computed(() => (summary.value.speed ? summary.value.speed.toFixed(1) : '—'))
const rounds = computed(() => summary.value.rounds || summary.value.turns)

function full(value: number | undefined): string {
  return Number(value || 0).toLocaleString('en-US')
}
</script>

<template>
  <!-- 卡片：4 列指标 + 缓存进度 -->
  <div v-if="variant === 'tile' && visible" class="tok-row tok-tile">
    <div class="tok-metrics">
      <div class="tok-metric">
        <span>输入</span>
        <b>{{ compactNumber(summary.input) }}</b>
      </div>
      <div class="tok-metric">
        <span>输出</span>
        <b>{{ compactNumber(summary.output) }}</b>
      </div>
      <div class="tok-metric">
        <span>速度</span>
        <b>{{ speedText }}<em>t/s</em></b>
      </div>
      <div class="tok-metric" :class="tone">
        <span>缓存</span>
        <b>{{ summary.cacheRate }}<em>%</em></b>
      </div>
    </div>
    <div class="tok-bar"><span :class="tone" :style="{ width: cacheWidth }" /></div>
  </div>

  <!-- 行内文字版（任务浮窗等窄行） -->
  <span v-else-if="variant === 'inline'" class="tok-inline">
    <span class="tok-inline-item">入 <b>{{ compactNumber(summary.input) }}</b></span>
    <i class="tok-sep">·</i>
    <span class="tok-inline-item">出 <b>{{ compactNumber(summary.output) }}</b></span>
    <template v-if="summary.speed">
      <i class="tok-sep">·</i>
      <span class="tok-inline-item"><b>{{ speedText }}</b> t/s</span>
    </template>
    <template v-if="summary.input">
      <i class="tok-sep">·</i>
      <span class="tok-inline-item" :class="tone">缓存 <b>{{ summary.cacheRate }}%</b></span>
    </template>
  </span>

  <!-- 工单页头部汇总 -->
  <div v-else-if="variant === 'hero'" class="tok-hero">
    <div class="tok-hero-main">
      <span class="tok-eyebrow">{{ label || 'Token 消耗' }}</span>
      <b class="tok-hero-total">{{ compactNumber(summary.total) }}</b>
      <span class="tok-hero-sub">
        入 {{ compactNumber(summary.input) }} · 出 {{ compactNumber(summary.output) }}
        <template v-if="rounds"> · {{ rounds }} 轮</template>
      </span>
    </div>
    <div class="tok-ring tok-ring-lg" :class="tone" :style="ringStyle">
      <span><b>{{ summary.cacheRate }}%</b><small>缓存</small></span>
    </div>
    <div class="tok-hero-speed">
      <b>{{ speedText }}<em>t/s</em></b>
      <small>生成速度</small>
    </div>
  </div>

  <!-- 抽屉 / 弹层 -->
  <div v-else-if="variant === 'panel'" class="tok-panel">
    <div class="tok-panel-head">
      <span v-if="!headless" class="tok-panel-title">{{ label || 'Token 用量' }}</span>
      <span v-else class="tok-panel-title" />
      <span v-if="visible" class="tok-panel-total"><b>{{ full(summary.total) }}</b> tokens</span>
    </div>
    <template v-if="visible">
      <div class="tok-panel-top">
        <div class="tok-ring" :class="tone" :style="ringStyle">
          <span><b>{{ summary.cacheRate }}%</b><small>缓存</small></span>
        </div>
        <div class="tok-panel-grid">
          <div class="tok-stat"><span>输入</span><b>{{ full(summary.input) }}</b></div>
          <div class="tok-stat"><span>输出</span><b>{{ full(summary.output) }}</b></div>
          <div class="tok-stat"><span>速度</span><b>{{ speedText }}<em>t/s</em></b></div>
          <div class="tok-stat"><span>轮次</span><b>{{ rounds }}</b></div>
        </div>
      </div>
      <div class="tok-bar tok-bar-lg"><span :class="tone" :style="{ width: cacheWidth }" /></div>
      <div class="tok-panel-foot">
        <span>缓存命中 {{ full(summary.cached) }} / {{ full(summary.input) }} tokens</span>
        <span v-if="contextTokens">
          上下文 {{ full(contextTokens) }}
          <template v-if="summary.maxContext && summary.maxContext !== contextTokens">
            · 峰值 {{ full(summary.maxContext) }}
          </template>
        </span>
      </div>
    </template>
    <div v-else class="tok-empty">还没有模型调用，暂无用量</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { compactNumber } from '@/lib/format'

const props = withDefaults(defineProps<{
  totalTokens?: number
  modelName?: string
  modelTokens?: number
  redacted?: number
  enabled?: boolean
}>(), {
  totalTokens: 0,
  modelName: '',
  modelTokens: 0,
  redacted: 0,
  enabled: true,
})

const offsets = [-60, -50, -41, -33, -25, -18, -11, -4, 4, 11, 18, 25, 33, 41, 50, 60]
const leftPaths = offsets.map((off) => (
  `M 128 ${150 + off} C 300 ${150 + off * 0.34}, 450 150, 566 150`
))
const rightPaths = offsets.map((off) => (
  `M 634 150 C 750 150, 900 ${150 + off * 0.34}, 1072 ${150 + off}`
))
const modelLabel = computed(() => {
  const name = String(props.modelName || '').trim()
  if (!name) return '未调用模型'
  return name.length > 18 ? `${name.slice(0, 18)}…` : name
})
const caption = computed(() => (
  props.enabled
    ? 'magpie 在这台电脑上脱敏'
    : 'magpie 已关闭 · 请求按原文发送'
))
const statsLine = computed(() => (
  props.enabled
    ? `本次运行已脱敏 ${compactNumber(props.redacted)} 处`
    : '开启后，密钥 / 个人信息 / 本机路径不会离开这台电脑'
))
</script>

<template>
  <section class="flow-panel" :class="{ idle: !enabled || !totalTokens }" role="img"
    :aria-label="`模型调用链路：${caption}，共 ${totalTokens} tokens`">
    <svg viewBox="0 0 1200 300" preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="flow-core" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="var(--accent)" />
          <stop offset="100%" stop-color="var(--accent-2)" />
        </linearGradient>
        <radialGradient id="flow-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="var(--accent)" stop-opacity=".35" />
          <stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />
        </radialGradient>
      </defs>

      <line x1="600" y1="16" x2="600" y2="284" class="flow-axis" />

      <path v-for="(path, index) in leftPaths" :key="`l-${index}`" :d="path"
        class="flow-line" :class="{ hot: index === 5 || index === 6 }"
        :style="{ animationDelay: `${index * -0.28}s` }" />
      <path v-for="(path, index) in rightPaths" :key="`r-${index}`" :d="path"
        class="flow-line" :class="{ hot: index === 5 || index === 6 }"
        :style="{ animationDelay: `${index * -0.28}s` }" />

      <circle cx="600" cy="150" r="72" fill="url(#flow-glow)" />
      <circle cx="600" cy="150" r="33" class="flow-core" />
      <path class="flow-shield"
        d="M600 133c7 4 12 5 17 5v12c0 11-7 18-17 22-10-4-17-11-17-22v-12c5 0 10-1 17-5Z" />
      <circle cx="600" cy="149" r="3.4" class="flow-keyhole" />
      <rect x="598.7" y="151" width="2.6" height="7.2" rx="1.3" class="flow-keyhole" />
      <circle cx="874" cy="150" r="2.6" class="flow-spark" />

      <g class="flow-node">
        <circle cx="84" cy="150" r="26" />
        <path class="flow-icon" d="M84 139 95 145v11l-11 6-11-6v-11Z M73 145l11 6 11-6 M84 151v11" />
      </g>
      <text x="124" y="145" class="flow-name">AtlasX</text>
      <text x="124" y="167" class="flow-tokens">{{ compactNumber(totalTokens) }}</text>

      <g class="flow-node" :class="{ dim: !modelName }">
        <circle cx="1116" cy="150" r="26" />
        <path class="flow-icon" d="M1116 138l12 6v12l-12 6-12-6v-12Z M1110 144l6 3 6-3 M1116 147v9" />
      </g>
      <text x="1076" y="145" text-anchor="end" class="flow-name">{{ modelLabel }}</text>
      <text x="1076" y="167" text-anchor="end" class="flow-tokens">{{ compactNumber(modelTokens) }}</text>

      <text x="600" y="235" text-anchor="middle" class="flow-caption">
        <tspan class="flow-brand">magpie</tspan><tspan dx="5">{{ enabled ? '在这台电脑上脱敏' : '已关闭 · 请求按原文发送' }}</tspan>
      </text>
      <text x="600" y="258" text-anchor="middle" class="flow-stats">{{ statsLine }}</text>
    </svg>
  </section>
</template>

<script setup lang="ts">
import { ElCard, ElStatistic } from 'element-plus'
import { computed } from 'vue'

const props = defineProps<{ value: string | number; label: string }>()

// ElStatistic 只接受 number，字符串（带千分位 / 单位）先归一成数字。
const numeric = computed(() => {
  const n = typeof props.value === 'number'
    ? props.value
    : Number(String(props.value).replace(/,/g, ''))
  return Number.isFinite(n) ? n : 0
})
</script>

<template>
  <el-card shadow="never" class="atlas-stat">
    <el-statistic :value="numeric">
      <template #title>
        <span>{{ label }}</span>
      </template>
    </el-statistic>
  </el-card>
</template>

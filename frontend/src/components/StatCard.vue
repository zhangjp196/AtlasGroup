<script setup lang="ts">
defineProps<{ value: string | number; label: string }>()
</script>

<template>
  <el-card shadow="never" class="atlas-stat">
    <el-statistic :value="value">
      <template #title>
        <span>{{ label }}</span>
      </template>
    </el-statistic>
  </el-card>
</template>

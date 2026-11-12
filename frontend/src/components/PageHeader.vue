<script setup lang="ts">
import { ElTag } from 'element-plus'

defineProps<{
  section: string
  title: string
  chip?: string
}>()
</script>

<template>
  <header class="canvas-head" :aria-label="section">
    <div class="page-head-main">
      <div class="page-head-title-row">
        <h1 class="page-head-title">{{ title }}</h1>
        <el-tag v-if="chip" size="small" type="info">{{ chip }}</el-tag>
      </div>
    </div>
    <div class="head-actions">
      <slot />
    </div>
  </header>
</template>

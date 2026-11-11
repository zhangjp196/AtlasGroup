<script setup lang="ts">
import { ElBreadcrumb, ElBreadcrumbItem, ElTag } from 'element-plus'
import { computed } from 'vue'
import { hueOf } from '@/lib/format'

const props = defineProps<{
  section: string
  title: string
  chip?: string
}>()

const MARKS: Record<string, string> = {
  工单: '工',
  会话: '会',
  定时: '时',
  工作区: '区',
  组织与角色: '组',
  设置: '设',
  关于: '关',
}
const mark = computed(() => MARKS[props.section] || String(props.section || '·').slice(0, 1))
const hue = computed(() => hueOf(props.section || '·'))
</script>

<template>
  <header class="canvas-head" :style="{ '--ph': hue }">
    <div class="page-head-main">
      <span class="page-head-mark">{{ mark }}</span>
      <div class="page-head-copy">
        <el-breadcrumb v-if="section !== title" separator="/">
          <el-breadcrumb-item>{{ section }}</el-breadcrumb-item>
        </el-breadcrumb>
        <div class="page-head-title-row">
          <b class="page-head-title">{{ title }}</b>
          <el-tag v-if="chip" size="small" type="info">{{ chip }}</el-tag>
        </div>
      </div>
    </div>
    <div class="head-actions">
      <slot />
    </div>
  </header>
</template>

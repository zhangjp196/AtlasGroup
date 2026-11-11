<script setup lang="ts">
import { computed } from 'vue'
import { DEFAULT_PAGE_SIZE, pageCount } from '@/lib/paging'

// 列表页统一的分页器：只在超过一页时出现，单页列表不显示任何「共 N 条」尾巴。
// 页码状态留在各页面，这样筛选 / 部门切换可以自己决定什么时候回到第一页。
const props = withDefaults(defineProps<{
  page: number
  total: number
  pageSize?: number
  pages?: number
}>(), {
  pageSize: DEFAULT_PAGE_SIZE,
  pages: 0,
})

const emit = defineEmits<{
  (e: 'update:page', value: number): void
}>()

const pageCountValue = computed(() => props.pages || pageCount(props.total, props.pageSize))
const current = computed(() => Math.min(Math.max(1, props.page), pageCountValue.value))
const multi = computed(() => pageCountValue.value > 1)

// 页码窗口：首尾页固定，当前页前后各一页，其余用省略号折叠。
const tokens = computed<(number | 'gap')[]>(() => {
  const last = pageCountValue.value
  if (last <= 7) return Array.from({ length: last }, (_, index) => index + 1)
  const start = Math.max(2, current.value - 1)
  const end = Math.min(last - 1, current.value + 1)
  const list: (number | 'gap')[] = [1]
  if (start > 2) list.push('gap')
  for (let target = start; target <= end; target += 1) list.push(target)
  if (end < last - 1) list.push('gap')
  list.push(last)
  return list
})

function go(next: number): void {
  const target = Math.min(Math.max(1, next), pageCountValue.value)
  if (target !== current.value) emit('update:page', target)
}
</script>

<template>
  <nav v-if="multi" class="list-pager" aria-label="分页">
    <button
      class="list-pager-btn list-pager-arrow"
      type="button"
      aria-label="上一页"
      :disabled="current <= 1"
      @click="go(current - 1)"
    >‹</button>
    <template v-for="(token, index) in tokens" :key="`${token}-${index}`">
      <span v-if="token === 'gap'" class="list-pager-gap" aria-hidden="true">…</span>
      <button
        v-else
        class="list-pager-btn"
        :class="{ active: token === current }"
        type="button"
        :aria-current="token === current ? 'page' : undefined"
        @click="go(token)"
      >{{ token }}</button>
    </template>
    <button
      class="list-pager-btn list-pager-arrow"
      type="button"
      aria-label="下一页"
      :disabled="current >= pageCountValue"
      @click="go(current + 1)"
    >›</button>
  </nav>
</template>

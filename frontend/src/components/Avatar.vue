<script setup lang="ts">
import { computed } from 'vue'
import { hueOf } from '@/lib/format'

const props = withDefaults(defineProps<{
  name?: string
  avatar?: string
  round?: boolean
  xs?: boolean
  size?: number
}>(), {
  name: '·',
  avatar: '',
  round: false,
  xs: false,
  size: 0,
})

const initial = computed(() => String(props.name || '·').slice(0, 1).toUpperCase() || '·')
const hue = computed(() => hueOf(props.name || '·'))
const style = computed(() => ({
  '--h': hue.value,
  ...(props.size ? { width: `${props.size}px`, height: `${props.size}px`, fontSize: `${Math.max(9, props.size * 0.38)}px` } : {}),
}))
</script>

<template>
  <span class="avatar" :class="{ round, xs }" :style="style">
    <img v-if="avatar" class="avatar-img" :src="avatar" alt="" loading="lazy" />
    <template v-else>{{ initial }}</template>
  </span>
</template>

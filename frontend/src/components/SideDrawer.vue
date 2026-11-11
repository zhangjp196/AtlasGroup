<script lang="ts">
let globalLayerSeq = 0
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  title: string
  subtitle?: string
  width?: string
}>()
const emit = defineEmits<{ (e: 'close'): void }>()

const layer = ref(0)
watch(() => props.open, (open) => {
  if (open) layer.value = ++globalLayerSeq
}, { immediate: true })

const width = ref<number | null>(null)
const dragging = ref(false)
let start: { x: number; y: number; w: number } | null = null

function startResize(event: PointerEvent): void {
  const el = event.currentTarget as HTMLElement | null
  const drawer = el?.closest<HTMLElement>('.side-drawer')
  if (!drawer) return
  start = { x: event.clientX, y: event.clientY, w: drawer.getBoundingClientRect().width }
  dragging.value = true
  const onMove = (move: PointerEvent): void => {
    if (!start) return
    const max = Math.min(window.innerWidth - 40, window.innerWidth * 0.94)
    width.value = Math.min(max, Math.max(340, start.w + (start.x - move.clientX)))
  }
  const onUp = (): void => {
    start = null
    dragging.value = false
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}

const drawerStyle = computed(() => ({
  width: width.value ? `${width.value}px` : (props.width || undefined),
  maxWidth: '94vw',
  zIndex: 106 + layer.value * 2,
}))
const scrimStyle = computed(() => ({ zIndex: 105 + layer.value * 2 }))
</script>

<template>
  <Teleport to="body">
    <div v-show="open" class="drawer-scrim" :style="scrimStyle" @click="emit('close')" />
    <aside v-show="open" class="side-drawer" :class="{ dragging }" :style="drawerStyle" :aria-label="title">
      <span class="rs rs-left" title="拖拽调整宽度" @pointerdown.prevent="startResize" />
      <header class="side-drawer-head">
        <div>
          <h2>{{ title }}</h2>
          <p v-if="subtitle">{{ subtitle }}</p>
        </div>
        <button class="iconbtn" type="button" title="关闭" aria-label="关闭" @click="emit('close')">
          <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>

      <div class="side-drawer-body">
        <slot />
      </div>

      <footer v-if="$slots.footer" class="side-drawer-foot">
        <slot name="footer" />
      </footer>
    </aside>
  </Teleport>
</template>

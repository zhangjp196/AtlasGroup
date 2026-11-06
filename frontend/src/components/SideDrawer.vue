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

const drawerStyle = computed(() => ({
  width: props.width || undefined,
  maxWidth: '94vw',
  zIndex: 106 + layer.value * 2,
}))
const scrimStyle = computed(() => ({ zIndex: 105 + layer.value * 2 }))
</script>

<template>
  <Teleport to="body">
    <div v-show="open" class="drawer-scrim" :style="scrimStyle" @click="emit('close')" />
    <aside v-show="open" class="side-drawer" :style="drawerStyle" :aria-label="title">
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

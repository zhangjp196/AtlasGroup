<script setup lang="ts">
import { computed } from 'vue'

export interface ContextMenuItem {
  label: string
  action: string
  danger?: boolean
  disabled?: boolean
  hint?: string
  separator?: boolean
}

const props = defineProps<{
  open: boolean
  x: number
  y: number
  items: ContextMenuItem[]
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'select', action: string): void
}>()

const style = computed(() => {
  const width = 210
  const height = Math.max(48, props.items.length * 32 + 12)
  const maxX = typeof window === 'undefined' ? props.x : window.innerWidth - width - 10
  const maxY = typeof window === 'undefined' ? props.y : window.innerHeight - height - 10
  return {
    left: `${Math.max(8, Math.min(props.x, maxX))}px`,
    top: `${Math.max(8, Math.min(props.y, maxY))}px`,
  }
})

function pick(item: ContextMenuItem): void {
  if (item.disabled || item.separator) return
  emit('select', item.action)
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="ctx-scrim" @click="emit('close')" @contextmenu.prevent="emit('close')" />
    <div v-if="open" class="ctx-menu" :style="style" role="menu" @click.stop>
      <template v-for="(item, index) in items" :key="`${item.action}-${index}`">
        <div v-if="item.separator" class="ctx-sep" />
        <button
          v-else
          class="ctx-item"
          :class="{ danger: item.danger, disabled: item.disabled }"
          type="button"
          role="menuitem"
          :disabled="item.disabled"
          @click="pick(item)"
        >
          <span>{{ item.label }}</span>
          <small v-if="item.hint">{{ item.hint }}</small>
        </button>
      </template>
    </div>
  </Teleport>
</template>

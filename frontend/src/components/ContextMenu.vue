<script setup lang="ts">
import { computed, ref, watch } from 'vue'

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

const menu = ref<HTMLElement>()
let previousFocus: HTMLElement | null = null

watch(() => props.open, (open) => {
  if (open) {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    menu.value?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true })
  } else if (previousFocus?.isConnected) {
    previousFocus.focus({ preventScroll: true })
  }
}, { flush: 'post' })

function onMenuKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (!menu.value || !['ArrowDown', 'ArrowUp'].includes(event.key)) return
  const buttons = Array.from(menu.value.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
  if (!buttons.length) return
  event.preventDefault()
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  const offset = event.key === 'ArrowDown' ? 1 : -1
  buttons[(index + offset + buttons.length) % buttons.length]?.focus({ preventScroll: true })
}

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
    <div v-if="open" ref="menu" class="ctx-menu" :style="style" role="menu" @click.stop @keydown="onMenuKey">
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

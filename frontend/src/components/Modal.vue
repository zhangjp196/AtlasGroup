<script setup lang="ts">
import { ElDialog } from 'element-plus'
import { useSlots } from 'vue'

defineProps<{ open: boolean; title: string; width?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const slots = useSlots()
</script>

<template>
  <el-dialog
    :model-value="open"
    :title="title"
    :width="width || '560px'"
    append-to-body
    destroy-on-close
    class="atlas-dialog"
    @update:model-value="emit('close')"
    @close="emit('close')"
  >
    <slot />
    <template v-if="slots.footer" #footer>
      <slot name="footer" />
    </template>
  </el-dialog>
</template>

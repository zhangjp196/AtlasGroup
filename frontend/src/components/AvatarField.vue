<script setup lang="ts">
import { ElButton } from 'element-plus'
import { ref } from 'vue'
import Avatar from '@/components/Avatar.vue'
import { useAppStore } from '@/stores/app'

const props = defineProps<{
  modelValue?: string
  scope: 'role' | 'department' | 'team'
  name?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()
const app = useAppStore()
const input = ref<HTMLInputElement | null>(null)
const uploading = ref(false)

function pick(): void {
  input.value?.click()
}

async function onChange(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  const form = new FormData()
  form.append('scope', props.scope)
  form.append('file', file)
  uploading.value = true
  try {
    const res = await fetch('/api/agent-zero/avatars', { method: 'POST', body: form })
    const payload = await res.json().catch(() => null)
    if (!res.ok) throw new Error(payload?.detail || `HTTP ${res.status}`)
    emit('update:modelValue', String(payload?.data?.url || ''))
    app.toast('头像已上传')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    uploading.value = false
    if (input.value) input.value.value = ''
  }
}

function remove(): void {
  emit('update:modelValue', '')
}
</script>

<template>
  <div class="avatar-field">
    <Avatar class="avatar-preview" :name="name || '群'" :avatar="modelValue" />
    <div class="avatar-field-main">
      <div class="avatar-actions">
        <el-button size="small" :loading="uploading" @click="pick">上传头像</el-button>
        <el-button size="small" text :disabled="!modelValue" @click="remove">移除</el-button>
      </div>
      <small class="avatar-hint">支持 PNG / JPG / GIF / WebP / SVG / BMP / TIFF 等，最大 8 MB。</small>
    </div>
    <input ref="input" type="file" accept="image/*" hidden @change="onChange" />
  </div>
</template>

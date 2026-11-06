<script setup lang="ts">
import { computed } from 'vue'
import Avatar from '@/components/Avatar.vue'
import type { LiveEntry } from '@/composables/useChat'
import { splitEntries } from '@/lib/trace'
import Markdown from './Markdown.vue'
import ProcessList from './ProcessList.vue'

const props = defineProps<{
  entries: LiveEntry[]
  roleName: string
  roleAvatar?: string
  roleSub?: string
  error?: string
}>()

const parts = computed(() => splitEntries(props.entries))
</script>

<template>
  <div class="msg assistant">
    <Avatar :name="roleName || '助手'" :avatar="roleAvatar" round />
    <div class="msg-body">
      <div class="msg-who">
        <b>{{ roleName || '助手' }}</b>
        <span v-if="roleSub" class="msg-role">{{ roleSub }}</span>
      </div>
      <ProcessList v-if="parts.process.length" :entries="parts.process" live />
      <div v-if="parts.final" class="bubble assistant-text">
        <Markdown :text="parts.final.text" />
      </div>
      <div v-if="error" class="run-error">{{ error }}</div>
    </div>
  </div>
</template>

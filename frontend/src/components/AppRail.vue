<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const app = useAppStore()
const data = useDataStore()
const route = useRoute()
const router = useRouter()

const sections = [
  { key: 'work', label: '工单', path: '/work' },
  { key: 'conv', label: '会话', path: '/conv' },
  { key: 'ws', label: '工作区', path: '/ws' },
  { key: 'role', label: '组织与角色', path: '/role' },
  { key: 'group', label: '群组', path: '/group' },
]

const attention = computed(() => Number(data.workStats?.attention || 0))
const current = computed(() => String(route.meta.section || 'work'))

function go(path: string): void {
  router.push(path)
}

function toggle(): void {
  app.toggleTheme()
}
</script>

<template>
  <aside class="rail">
    <div class="logo" title="AtlasX">A</div>

    <button
      v-for="section in sections"
      :key="section.key"
      class="rail-btn"
      :class="{ active: current === section.key }"
      :title="section.label"
      :aria-label="section.label"
      @click="go(section.path)"
    >
      <svg v-if="section.key === 'work'" viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="12" rx="4" /><path d="M12 3v4M9 12h.01M15 12h.01M9 16h6" /></svg>
      <svg v-else-if="section.key === 'conv'" viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z" /></svg>
      <svg v-else-if="section.key === 'ws'" viewBox="0 0 24 24"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z" /></svg>
      <svg v-else-if="section.key === 'role'" viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.2" /><path d="M5 20c0-3.4 3.1-5.6 7-5.6s7 2.2 7 5.6" /></svg>
      <svg v-else viewBox="0 0 24 24"><circle cx="9" cy="8" r="2.6" /><path d="M3.6 19c0-2.6 2.4-4.4 5.4-4.4s5.4 1.8 5.4 4.4" /><circle cx="17" cy="9" r="2.2" /><path d="M15.8 14.8c2.4.3 4.2 2 4.2 4.2" /></svg>
      <span v-if="section.key === 'work' && attention > 0" class="rail-badge">{{ attention > 99 ? '99+' : attention }}</span>
    </button>

    <div class="spacer" />

    <button class="rail-btn theme-toggle" :title="`主题：${app.theme}`" aria-label="切换主题" @click="toggle">
      <svg class="theme-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
      <svg class="theme-moon" viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" /></svg>
      <span v-if="app.schedule.mode !== 'manual'" class="theme-auto" title="按系统时间自动切换" />
    </button>
    <button class="rail-btn" :class="{ active: current === 'settings' }" title="设置" aria-label="设置" @click="go('/settings')">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.6 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></svg>
    </button>
    <button class="rail-btn" :class="{ active: current === 'about' }" title="关于" aria-label="关于" @click="go('/about')">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>
    </button>
    <div class="me" title="本地用户">Z</div>
  </aside>
</template>

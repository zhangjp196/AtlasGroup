<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const props = defineProps<{ collapsed?: boolean }>()
const section = computed(() => String(route.meta.section || 'work'))
const organizationActive = computed(() => section.value === 'organization')
</script>

<template>
  <nav class="workbench-nav" :class="{ collapsed: props.collapsed }" aria-label="协作中心模块">
    <div class="workbench-nav-main">
      <router-link to="/conv" :class="{ active: section === 'conv' }" title="会话" aria-label="会话">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z"/></svg><span>会话</span>
      </router-link>
      <router-link to="/schedule" :class="{ active: section === 'schedule' }" title="定时任务" aria-label="定时任务">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 2M9 2h6"/></svg><span>定时任务</span>
      </router-link>
      <router-link to="/ws" :class="{ active: section === 'ws' }" title="工作区" aria-label="工作区">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z"/></svg><span>工作区</span>
      </router-link>
      <router-link to="/organization/roles" :class="{ active: organizationActive }" title="组织" aria-label="组织">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.2"/><path d="M5 20c0-3.4 3.1-5.6 7-5.6s7 2.2 7 5.6"/></svg><span>组织</span>
      </router-link>
    </div>
    <div v-if="organizationActive" class="workbench-nav-sub" aria-label="组织管理">
      <router-link to="/organization/roles" :class="{ active: route.path.includes('/roles') }" title="角色">角色</router-link>
      <router-link to="/organization/groups" :class="{ active: route.path.includes('/groups') }" title="群组">群组</router-link>
    </div>
  </nav>
</template>

<style scoped>
.workbench-nav{display:flex;flex:none;flex-direction:column;gap:4px;width:100%;min-width:0;padding:0 0 8px;border-bottom:1px solid var(--line-soft);overflow:auto;scrollbar-width:none}
.workbench-nav::-webkit-scrollbar{display:none}
.workbench-nav-main,.workbench-nav-sub{display:flex;flex:none;flex-direction:column;gap:3px;width:100%;min-width:0}
.workbench-nav-main a,.workbench-nav-sub a{position:relative;display:flex;align-items:center;gap:11px;min-width:0;min-height:37px;padding:0 10px;border:1px solid transparent;border-radius:8px;color:var(--dim);font-size:12px;text-decoration:none;white-space:nowrap}
.workbench-nav-main a svg{width:19px;height:19px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.workbench-nav-main a:hover,.workbench-nav-sub a:hover{background:var(--panel);color:var(--text)}
.workbench-nav-main a.active{border-color:color-mix(in srgb,var(--accent) 23%,var(--line-soft));background:color-mix(in srgb,var(--accent) 10%,var(--panel));color:var(--accent);font-weight:650}
.workbench-nav-sub{gap:2px;padding-left:39px}
.workbench-nav-sub a{min-height:29px;padding:0 8px;font-size:11px}
.workbench-nav-sub a.active{background:color-mix(in srgb,var(--accent) 14%,var(--panel));color:var(--accent-ink)}
.workbench-nav.collapsed .workbench-nav-main a{justify-content:center;padding-inline:0}
.workbench-nav.collapsed .workbench-nav-main a span,.workbench-nav.collapsed .workbench-nav-sub{display:none}
</style>

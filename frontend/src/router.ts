import { createRouter, createWebHistory } from 'vue-router'
import AboutView from '@/views/AboutView.vue'
import ConversationsView from '@/views/ConversationsView.vue'
import RolesView from '@/views/RolesView.vue'
import SettingsView from '@/views/SettingsView.vue'
import TeamsView from '@/views/TeamsView.vue'
import WorkspacesView from '@/views/WorkspacesView.vue'
import WorkView from '@/views/WorkView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/work' },
    { path: '/work', component: WorkView, meta: { section: 'work', title: '工单' } },
    { path: '/work/:id', component: WorkView, meta: { section: 'work', title: '工单' } },
    { path: '/conv', component: ConversationsView, meta: { section: 'conv', title: '会话' } },
    { path: '/conv/:id', component: ConversationsView, meta: { section: 'conv', title: '会话' } },
    { path: '/ws', component: WorkspacesView, meta: { section: 'ws', title: '工作区' } },
    { path: '/ws/:id', component: WorkspacesView, meta: { section: 'ws', title: '工作区' } },
    { path: '/role', component: RolesView, meta: { section: 'role', title: '组织与角色' } },
    { path: '/role/:id', component: RolesView, meta: { section: 'role', title: '角色' } },
    { path: '/group', component: TeamsView, meta: { section: 'group', title: '群组' } },
    { path: '/group/:id', component: TeamsView, meta: { section: 'group', title: '群组' } },
    { path: '/settings', component: SettingsView, meta: { section: 'settings', title: '设置', flat: true } },
    { path: '/about', component: AboutView, meta: { section: 'about', title: '关于', flat: true } },
    { path: '/:pathMatch(.*)*', redirect: '/work' },
  ],
})

router.afterEach((to) => {
  const title = String(to.meta.title || 'AtlasX')
  document.title = title === 'AtlasX' ? 'AtlasX' : `${title} · AtlasX`
})

export default router

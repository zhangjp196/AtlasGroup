import { createRouter, createWebHistory } from 'vue-router'

const WorkView = () => import('@/views/WorkView.vue')
const ConversationsView = () => import('@/views/ConversationsView.vue')
const SchedulesView = () => import('@/views/SchedulesView.vue')
const WorkspacesView = () => import('@/views/WorkspacesView.vue')
const RolesView = () => import('@/views/RolesView.vue')
const SettingsView = () => import('@/views/SettingsView.vue')
const AboutView = () => import('@/views/AboutView.vue')

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/work' },
    { path: '/work', component: WorkView, meta: { section: 'work', title: '工单' } },
    { path: '/work/:id', component: WorkView, meta: { section: 'work', title: '工单' } },
    { path: '/conv', component: ConversationsView, meta: { section: 'conv', title: '会话' } },
    { path: '/conv/:id', component: ConversationsView, meta: { section: 'conv', title: '会话' } },
    { path: '/schedule', component: SchedulesView, meta: { section: 'schedule', title: '定时任务' } },
    { path: '/ws', component: WorkspacesView, meta: { section: 'ws', title: '工作区' } },
    { path: '/ws/:id', component: WorkspacesView, meta: { section: 'ws', title: '工作区' } },
    { path: '/role', component: RolesView, meta: { section: 'role', title: '组织与角色' } },
    { path: '/role/:id', component: RolesView, meta: { section: 'role', title: '角色' } },
    // 群组功能已移除：旧书签落到会话页，由会话内的参与角色承担多角色协作。
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

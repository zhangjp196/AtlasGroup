import { createRouter, createWebHistory } from 'vue-router'

const WorkView = () => import('@/views/WorkView.vue')
const ConversationsView = () => import('@/views/ConversationsView.vue')
const WorkspacesView = () => import('@/views/WorkspacesView.vue')
const RolesView = () => import('@/views/RolesView.vue')
const TeamsView = () => import('@/views/TeamsView.vue')
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

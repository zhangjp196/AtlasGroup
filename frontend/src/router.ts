import { createRouter, createWebHistory } from 'vue-router'

const WorkView = () => import('@/views/WorkView.vue')
const ConversationsView = () => import('@/views/ConversationsView.vue')
const SchedulesView = () => import('@/views/SchedulesView.vue')
const WorkspacesView = () => import('@/views/WorkspacesView.vue')
const RolesView = () => import('@/views/RolesView.vue')
const GroupsView = () => import('@/views/GroupsView.vue')
const OrganizationView = () => import('@/views/OrganizationView.vue')
const SettingsView = () => import('@/views/SettingsView.vue')
const AboutView = () => import('@/views/AboutView.vue')

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/work' },
    { path: '/work', component: WorkView, meta: { section: 'work', title: '协作中心' } },
    { path: '/work/:id', component: WorkView, meta: { section: 'work', title: '协作中心' } },
    { path: '/conv', component: ConversationsView, meta: { section: 'conv', title: '会话' } },
    { path: '/conv/:id', component: ConversationsView, meta: { section: 'conv', title: '会话' } },
    { path: '/schedule', component: SchedulesView, meta: { section: 'schedule', title: '定时任务' } },
    { path: '/ws', component: WorkspacesView, meta: { section: 'ws', title: '工作区' } },
    { path: '/ws/:id', component: WorkspacesView, meta: { section: 'ws', title: '工作区' } },
    {
      path: '/organization', component: OrganizationView,
      meta: { section: 'organization', title: '组织' },
      children: [
        { path: '', redirect: '/organization/roles' },
        { path: 'roles', component: RolesView, meta: { section: 'organization', title: '组织' } },
        { path: 'roles/:id', component: RolesView, meta: { section: 'organization', title: '组织' } },
        { path: 'groups', component: GroupsView, meta: { section: 'organization', title: '组织' } },
        { path: 'groups/:id', component: GroupsView, meta: { section: 'organization', title: '组织' } },
      ],
    },
    { path: '/role', redirect: (to) => ({ path: '/organization/roles', query: to.query, hash: to.hash }) },
    { path: '/role/:id', redirect: (to) => ({ path: `/organization/roles/${encodeURIComponent(String(to.params.id))}`, query: to.query, hash: to.hash }) },
    { path: '/group', redirect: (to) => ({ path: '/organization/groups', query: to.query, hash: to.hash }) },
    { path: '/group/:id', redirect: (to) => ({ path: `/organization/groups/${encodeURIComponent(String(to.params.id))}`, query: to.query, hash: to.hash }) },
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

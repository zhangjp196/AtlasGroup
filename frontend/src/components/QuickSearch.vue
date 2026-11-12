<script setup lang="ts">
import { Search } from '@element-plus/icons-vue'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useDataStore } from '@/stores/data'

const router = useRouter()
const data = useDataStore()
const dialog = ref<HTMLDialogElement>()
const query = ref('')
const selected = ref(0)
const sections = [
  ['工单', '/work'], ['会话', '/conv'], ['定时任务', '/schedule'],
  ['工作区', '/ws'], ['组织与角色', '/role'], ['群组', '/group'],
  ['设置', '/settings'], ['关于', '/about'],
]

const results = computed(() => {
  const pages = sections.map(([title, path]) => ({ title, path, type: '页面', keywords: '' }))
  const terms = query.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!terms.length) return pages
  const entries = [
    ...pages,
    ...data.workItems.map((item) => ({ title: item.title || '未命名工单', path: '/work/' + encodeURIComponent(item.id), type: '工单', keywords: item.body || '' })),
    ...data.conversations.map((item) => ({ title: item.title || '未命名会话', path: '/conv/' + encodeURIComponent(item.id), type: '会话', keywords: '' })),
    ...data.workspaces.map((item) => ({ title: item.name || item.id, path: '/ws/' + encodeURIComponent(item.id), type: '工作区', keywords: item.root_path || '' })),
    ...data.persistentRoles.map((item) => ({ title: item.name || item.id, path: '/role/' + encodeURIComponent(item.id), type: '角色', keywords: item.title || '' })),
    ...data.groups.map((item) => ({ title: item.name || item.id, path: '/group/' + encodeURIComponent(item.id), type: '群组', keywords: '' })),
  ]
  return entries.filter(({ title, type, keywords }) => {
    const content = `${title} ${type} ${keywords}`.toLowerCase()
    return terms.every((term) => content.includes(term))
  }).slice(0, 20)
})

function open(): void {
  if (dialog.value?.open) return
  query.value = ''
  selected.value = 0
  dialog.value?.showModal()
}

function onShortcut(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    open()
  }
}

function move(direction: number): void {
  const count = results.value.length
  if (!count) return
  selected.value = (Math.min(selected.value, count - 1) + direction + count) % count
  document.getElementById(`quick-result-${selected.value}`)?.scrollIntoView({ block: 'nearest' })
}

function go(path?: string): void {
  if (!path) return
  dialog.value?.close()
  void router.push(path)
}

onMounted(() => window.addEventListener('keydown', onShortcut))
onUnmounted(() => window.removeEventListener('keydown', onShortcut))
</script>

<template>
  <button class="quick-trigger" type="button" aria-haspopup="dialog" aria-label="快速搜索" @click="open">
    <Search aria-hidden="true" />
    <span>快速搜索</span>
    <kbd>⌘ / Ctrl K</kbd>
  </button>
  <Teleport to="body">
    <dialog ref="dialog" class="quick-dialog" aria-labelledby="quick-title" @click.self="dialog?.close()">
      <div class="quick-surface">
        <header class="quick-head">
          <h2 id="quick-title">快速搜索</h2>
          <button class="btn small" type="button" aria-label="关闭快速搜索" @click="dialog?.close()">Esc</button>
        </header>
        <div class="quick-field">
          <Search aria-hidden="true" />
          <input v-model="query" autofocus role="combobox" aria-label="搜索页面和已加载的内容"
            aria-controls="quick-results" :aria-expanded="Boolean(results.length)"
            :aria-activedescendant="results[selected] ? `quick-result-${selected}` : undefined"
            placeholder="搜索页面、工单、会话或角色…"
            @input="selected = 0" @keydown.down.prevent="move(1)" @keydown.up.prevent="move(-1)"
            @keydown.enter.prevent="!$event.isComposing && go(results[selected]?.path)" />
        </div>
        <ul id="quick-results" class="quick-results" role="listbox" aria-label="搜索结果">
          <li v-for="(item, index) in results" :id="`quick-result-${index}`" :key="item.path" role="option"
            :aria-selected="selected === index" @mouseenter="selected = index" @click="go(item.path)">
            <span>{{ item.title }}</span><small>{{ item.type }}</small>
          </li>
        </ul>
        <p v-if="!results.length" class="quick-empty" role="status">已加载的内容中没有匹配结果。</p>
        <footer class="quick-foot">↑ ↓ 选择 · Enter 打开</footer>
      </div>
    </dialog>
  </Teleport>
</template>

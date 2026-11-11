<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Markdown from '@/components/chat/Markdown.vue'
import TokenMeter from '@/components/TokenMeter.vue'
import WorkEditorDialog from '@/components/WorkEditorDialog.vue'
import { api } from '@/api/client'
import type { TokenUsage, WorkItem } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

const props = defineProps<{ open: boolean; item: WorkItem | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved'): void
  (e: 'deleted', id: string): void
}>()

const app = useAppStore()
const data = useDataStore()
const router = useRouter()
const editing = ref(false)
const busy = ref(false)
// 工单详情用轻量刷新拿到最新用量：浮窗里的旧列表对象可能是几分钟前的快照。
const usage = ref<TokenUsage | null>(null)
const usageLoading = ref(false)

watch(() => [props.open, props.item], () => {
  usage.value = null
  if (props.open && props.item?.id) void loadUsage()
}, { immediate: true })

async function loadUsage(): Promise<void> {
  const id = String(props.item?.id || '')
  if (!id) return
  usageLoading.value = true
  try {
    const fresh = await api<WorkItem>(`/work-items/${encodeURIComponent(id)}`)
    if (fresh?.usage) usage.value = fresh.usage
  } catch {
    /* 保留列表里的旧快照 */
  } finally {
    usageLoading.value = false
  }
}

const statusLabels: Record<string, string> = {
  inbox: '待分派',
  working: '进行中',
  needs_attention: '需要你',
  done: '已完成',
  closed: '已关闭',
}

const badge = computed(() => statusLabels[String(props.item?.status || '')] || String(props.item?.status || ''))
const waiting = computed(() => props.item?.status === 'needs_attention' && /等待/.test(String(props.item?.attention_reason || '')))
const isDone = computed(() => ['done', 'closed'].includes(String(props.item?.status || '')))
const canDispatch = computed(() => ['inbox', 'needs_attention'].includes(String(props.item?.status || '')) && !waiting.value)
const sourceRef = computed(() => String(props.item?.source_ref || ''))

function close(): void {
  emit('close')
}

function openConversation(): void {
  const id = String(props.item?.conversation_id || '')
  if (!id) return
  emit('close')
  void router.push(`/conv/${encodeURIComponent(id)}`)
}

async function dispatch(): Promise<void> {
  if (!props.item || busy.value) return
  busy.value = true
  try {
    const out = await api<WorkItem>(`/work-items/${encodeURIComponent(props.item.id)}/dispatch`, { method: 'POST' })
    await data.loadWork()
    app.toast(out.conversation_id ? '已派活；需要你时会出现在「需要你」' : '已派活')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = false
  }
}

async function setStatus(status: string): Promise<void> {
  if (!props.item || busy.value) return
  busy.value = true
  try {
    await api(`/work-items/${encodeURIComponent(props.item.id)}`, { method: 'PATCH', body: { status } })
    await data.loadWork()
    app.toast(status === 'done' ? '工单已完成' : status === 'closed' ? '工单已关闭' : '工单已重新打开')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = false
  }
}

async function remove(): Promise<void> {
  if (!props.item || busy.value) return
  if (!window.confirm(`删除工单「${props.item.title || ''}」？已创建的会话不会被删除。`)) return
  busy.value = true
  try {
    await api(`/work-items/${encodeURIComponent(props.item.id)}`, { method: 'DELETE' })
    await data.loadWork()
    app.toast('工单已删除')
    emit('deleted', props.item.id)
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = false
  }
}

function onSaved(): void {
  editing.value = false
  emit('saved')
}
</script>

<template>
  <Teleport to="body">
    <div v-show="open" class="drawer-scrim" @click="close" />
    <aside v-show="open" class="side-drawer" aria-label="工单详情">
      <header class="side-drawer-head">
        <div>
          <h2>工单</h2>
          <p>{{ item?.title || '' }} · {{ badge }}</p>
        </div>
        <button class="iconbtn" type="button" title="关闭" aria-label="关闭" @click="close">
          <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>

      <div v-if="item" class="side-drawer-body">
        <article class="work-detail" :class="`work-detail-${item.status || 'inbox'}`" style="border:0;padding:0;background:transparent;box-shadow:none">
          <div class="sa-detail-head">
            <span class="sa-detail-orb" :class="item.status">
              <svg viewBox="0 0 24 24">
                <template v-if="item.status === 'needs_attention'"><path d="M12 4 3 20h18Z" /><path d="M12 10v4M12 17h.01" /></template>
                <template v-else-if="item.status === 'working'"><circle cx="12" cy="12" r="7" /><path d="M12 8v4l3 2" /></template>
                <template v-else-if="item.status === 'done'"><path d="m5 12 4 4L19 6" /></template>
                <template v-else><path d="M5 4h14v17H5z" /><path d="M8 8h8M8 12h8M8 16h5" /></template>
              </svg>
            </span>
            <div class="sa-detail-title">
              <div class="sa-eyebrow">工单 · {{ item.source || 'manual' }}</div>
              <h1>{{ item.title || '未命名工单' }}</h1>
            </div>
            <span class="work-badge" :class="item.status">{{ badge }}</span>
          </div>

          <div class="work-meta">
            <span><i>来源</i><b>{{ item.source || 'manual' }}</b></span>
            <span v-if="sourceRef">
              <i>链接</i>
              <a v-if="/^https?:\/\//.test(sourceRef)" :href="sourceRef" target="_blank" rel="noopener">{{ sourceRef }}</a>
              <b v-else>{{ sourceRef }}</b>
            </span>
            <span v-if="item.source_actor"><i>发起人</i><b>{{ item.source_actor }}</b></span>
            <span><i>工作区</i><b>{{ item.workspace_name || item.workspace_id || '默认' }}</b></span>
            <span><i>角色</i><b>{{ [item.role_name || item.role_id || '默认', item.role_title, item.role_department].filter(Boolean).join(' · ') }}</b></span>
            <span><i>创建</i><b>{{ item.created_at || '' }}</b></span>
          </div>

          <section class="work-tokens">
            <div class="sa-detail-label" style="margin:0">
              Token 用量 · {{ usageLoading ? '统计中…' : '输入 / 输出 / 速度 / 缓存' }}
            </div>
            <TokenMeter :usage="usage || item.usage" variant="panel" headless />
          </section>

          <div v-if="item.status === 'needs_attention'" class="work-attention">
            <span class="sa-attention-ico danger">
              <svg viewBox="0 0 24 24"><path d="M12 4 3 20h18Z" /><path d="M12 10v4M12 17h.01" /></svg>
            </span>
            <span>需要你：{{ item.attention_reason || '处理这个工单' }}</span>
            <button v-if="item.conversation_id" class="btn primary" type="button" @click="openConversation">打开会话处理</button>
            <button v-if="item.conversation_id && !waiting" class="btn" type="button" @click="dispatch">重新派活</button>
          </div>

          <div class="sa-detail-label">工单说明</div>
          <div class="work-body">
            <Markdown v-if="item.body" :text="String(item.body)" />
            <span v-else>（没有补充说明）</span>
          </div>

          <template v-if="item.result_summary">
            <div class="sa-detail-label">结果</div>
            <div class="work-result"><Markdown :text="String(item.result_summary)" /></div>
          </template>

          <div class="sa-detail-label">运行</div>
          <div class="work-run">
            <span v-if="item.run_id"><i>run</i><code>{{ item.run_id }}</code></span>
            <span v-if="item.conversation_id"><i>会话</i><code>{{ item.conversation_id }}</code></span>
            <span v-if="item.finished_at"><i>结束</i>{{ item.finished_at }}</span>
            <span>只展示需要人的工单；其余在后台推进。</span>
          </div>
        </article>
      </div>

      <footer class="side-drawer-foot">
        <button v-if="item?.conversation_id" class="btn" :class="{ primary: item?.status === 'needs_attention' }" type="button" @click="openConversation">打开会话</button>
        <button v-if="canDispatch" class="btn" :class="{ primary: !item?.conversation_id }" type="button" :disabled="busy" @click="dispatch">
          {{ item?.conversation_id ? '重新派活' : '派活' }}
        </button>
        <template v-if="!isDone">
          <button class="btn" type="button" :disabled="busy" @click="setStatus('done')">标记完成</button>
          <button class="btn danger" type="button" :disabled="busy" @click="setStatus('closed')">关闭</button>
        </template>
        <button v-else class="btn" type="button" :disabled="busy" @click="setStatus('inbox')">重新打开</button>
        <button class="btn" type="button" @click="editing = true">编辑</button>
        <button class="btn danger" type="button" :disabled="busy" @click="remove">删除</button>
        <span class="spacer" />
        <button class="btn" type="button" @click="close">关闭</button>
      </footer>
    </aside>

    <WorkEditorDialog :open="editing" :item="item" @close="editing = false" @saved="onSaved" />
  </Teleport>
</template>

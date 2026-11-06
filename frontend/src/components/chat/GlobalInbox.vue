<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'
import { useDataStore } from '@/stores/data'

const props = defineProps<{ full?: boolean }>()
const emit = defineEmits<{ (e: 'open', conversationId: string): void }>()
const attention = useAttentionStore()
const data = useDataStore()
const app = useAppStore()
const open = ref(true)
const busy = ref('')
const marking = ref(false)

const approvals = computed(() => attention.data.approvals || [])
const questions = computed(() => attention.data.questions || [])
const pendingTotal = computed(() => approvals.value.length + questions.value.length)

onMounted(() => {
  void attention.load()
})

function go(id?: string): void {
  if (id) emit('open', id)
}

async function markAllRead(): Promise<void> {
  if (marking.value) return
  marking.value = true
  try {
    const out = await api<{ marked?: number }>('/app/read-all', { method: 'POST' })
    await Promise.all([attention.load(), data.loadAll()])
    app.toast(out?.marked ? '已全部标记为已读' : '当前没有未读消息')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    marking.value = false
  }
}

async function decide(id: string, action: string): Promise<void> {
  busy.value = `approval-${id}`
  try {
    await api(`/approvals/${encodeURIComponent(id)}`, { method: 'POST', body: { action } })
    await attention.load()
    app.toast('审批已处理')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <section class="global-inbox" :class="{ full: props.full }">
    <header v-if="!props.full" class="gi-head" @click="open = !open">
      <b>待处理事项</b>
      <el-tag v-if="pendingTotal" size="small" type="danger">待处理 {{ pendingTotal }}</el-tag>
      <span class="gi-caret">{{ open ? '▾' : '▸' }}</span>
    </header>

    <div v-if="open || props.full" class="gi-body">
      <div class="gi-toolbar">
        <el-button size="small" :loading="marking" :disabled="!Number(attention.counts.unread || 0)" @click="markAllRead">一键已读</el-button>
      </div>
      <div v-if="!pendingTotal" class="gi-empty">当前没有需要处理的事项</div>

      <template v-if="approvals.length">
        <div class="gi-sec"><span>待授权</span><b>{{ approvals.length }}</b></div>
        <div v-for="item in approvals" :key="item.id" class="gi-card">
          <div class="gi-card-head">
            <el-tag size="small" type="danger" effect="plain">{{ item.risk || 'medium' }}</el-tag>
            <b>{{ item.tool || item.name || '工具' }}</b>
            <el-button text size="small" @click="go(item.conversation_id)">定位</el-button>
          </div>
          <small v-if="item.note">{{ item.note }}</small>
          <div class="gi-actions">
            <el-button size="small" type="primary" :loading="busy === `approval-${item.id}`" @click="decide(item.id, 'approve_once')">允许一次</el-button>
            <el-button size="small" @click="decide(item.id, 'approve_session')">本会话</el-button>
            <el-button size="small" @click="decide(item.id, 'approve_always')">始终</el-button>
            <el-button size="small" type="danger" plain @click="decide(item.id, 'deny')">拒绝</el-button>
          </div>
        </div>
      </template>

      <template v-if="questions.length">
        <div class="gi-sec"><span>待回答</span><b>{{ questions.length }}</b></div>
        <button v-for="item in questions" :key="`q-${item.id}`" class="gi-row" @click="go(item.conversation_id)">
          <div class="gi-main">
            <b>{{ item.question || '有问题等待回答' }}</b>
            <small>打开会话回答</small>
          </div>
          <span class="gi-caret">›</span>
        </button>
      </template>
    </div>
  </section>
</template>

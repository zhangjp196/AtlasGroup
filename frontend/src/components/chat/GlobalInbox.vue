<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useAttentionStore } from '@/stores/attention'

const props = defineProps<{ full?: boolean }>()
const emit = defineEmits<{ (e: 'open', conversationId: string): void }>()
const attention = useAttentionStore()
const app = useAppStore()
const open = ref(true)
const busy = ref('')

const approvals = computed(() => attention.data.approvals || [])
const questions = computed(() => attention.data.questions || [])
const unread = computed(() => attention.data.unread || [])
const running = computed(() => attention.data.running || [])
const pendingTotal = computed(() => (
  approvals.value.length + questions.value.length + unread.value.length + running.value.length
))

onMounted(() => {
  void attention.load()
})

function go(id?: string): void {
  if (id) emit('open', id)
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

      <template v-if="unread.length">
        <div class="gi-sec"><span>未读</span><b>{{ unread.length }}</b></div>
        <button v-for="item in unread" :key="`u-${item.id}`" class="gi-row" @click="go(item.id)">
          <div class="gi-main">
            <b>{{ item.title || '未命名会话' }}</b>
            <small>{{ item.last_message || '有新的助手回复' }}</small>
          </div>
          <span class="gi-caret">›</span>
        </button>
      </template>

      <template v-if="running.length">
        <div class="gi-sec"><span>运行中</span><b>{{ running.length }}</b></div>
        <button v-for="item in running" :key="`r-${item.id || item.conversation_id}`" class="gi-row" @click="go(item.conversation_id)">
          <div class="gi-main">
            <b>{{ item.title || '未命名会话' }}</b>
            <small>{{ [item.status, item.workspace_name].filter(Boolean).join(' · ') || '正在运行' }}</small>
          </div>
          <span class="gi-caret">›</span>
        </button>
      </template>
    </div>
  </section>
</template>

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

const counts = computed(() => attention.counts)
const approvals = computed(() => attention.data.approvals || [])
const questions = computed(() => attention.data.questions || [])
const unread = computed(() => attention.data.unread || [])
const running = computed(() => attention.data.running || [])
const sessions = computed(() => attention.data.sessions || [])
const defaultMode = computed(() => String(attention.data.policy?.default || 'once'))
const total = computed(() => approvals.value.length + questions.value.length + unread.value.length + running.value.length)

const modeLabels: Record<string, string> = {
  auto: '自动判断',
  once: '每次询问',
  session: '本会话允许',
  always: '完全允许',
}

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

function answerOther(id: string, value: string): void {
  const text = String(value || '').trim()
  if (text) void answer(id, { other: text })
}

async function answer(id: string, payload: Record<string, unknown>): Promise<void> {
  busy.value = `question-${id}`
  try {
    await api(`/questions/${encodeURIComponent(id)}`, { method: 'POST', body: payload })
    await attention.load()
    app.toast('回答已提交')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = ''
  }
}

async function markRead(id: string): Promise<void> {
  busy.value = `read-${id}`
  try {
    await api(`/conversations/${encodeURIComponent(id)}/read`, { method: 'POST' })
    await attention.load()
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = ''
  }
}

async function markAllRead(): Promise<void> {
  busy.value = 'read-all'
  try {
    await Promise.all(unread.value.map((item) => api(`/conversations/${encodeURIComponent(item.id)}/read`, { method: 'POST' }).catch(() => null)))
    await attention.load()
    app.toast('已全部标记已读')
  } finally {
    busy.value = ''
  }
}

async function stopRun(id?: string): Promise<void> {
  if (!id) return
  busy.value = `stop-${id}`
  try {
    await api(`/runs/${encodeURIComponent(id)}/stop`, { method: 'POST' })
    await attention.load()
    app.toast('已请求停止')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  } finally {
    busy.value = ''
  }
}

async function setDefaultMode(mode: string): Promise<void> {
  try {
    await api('/app/permissions', { method: 'PUT', body: { default: mode } })
    await attention.load()
    app.toast('默认权限已更新')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}

async function setSessionMode(id: string, mode: string): Promise<void> {
  try {
    await api(`/conversations/${encodeURIComponent(id)}/permission`, { method: 'PUT', body: { mode } })
    await attention.load()
    app.toast('会话权限已更新')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}
</script>

<template>
  <section class="global-inbox" :class="{ full: props.full }">
    <header v-if="!props.full" class="gi-head" @click="open = !open">
      <b>全局消息</b>
      <el-tag v-if="Number(counts.authorizations || 0)" size="small" type="danger">待授权 {{ counts.authorizations }}</el-tag>
      <el-tag v-if="Number(counts.unread || 0)" size="small" type="primary">未读 {{ counts.unread }}</el-tag>
      <el-tag v-if="Number(counts.running || 0)" size="small" type="success">运行 {{ counts.running }}</el-tag>
      <span class="gi-count">{{ total }}</span>
      <span class="gi-caret">{{ open ? '▾' : '▸' }}</span>
    </header>

    <div v-if="open || props.full" class="gi-body">
      <div v-if="!total && !sessions.length" class="gi-empty">当前没有需要处理的全局消息</div>

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
        <div v-for="item in questions" :key="item.id" class="gi-card">
          <div class="gi-card-head">
            <el-tag size="small" type="warning" effect="plain">提问</el-tag>
            <b>{{ item.question || '需要你决定' }}</b>
            <el-button text size="small" @click="go(item.conversation_id)">定位</el-button>
          </div>
          <div v-if="item.options?.length" class="gi-actions">
            <el-button
              v-for="(option, index) in item.options"
              :key="index"
              size="small"
              @click="answer(item.id, { selected: [String(typeof option === 'string' ? option : (option.value || option.label || ''))] })"
            >
              {{ typeof option === 'string' ? option : (option.label || option.value) }}
            </el-button>
          </div>
          <div class="gi-actions">
            <el-input
              :model-value="''"
              size="small"
              placeholder="输入其他回答…"
              style="flex:1;min-width:140px"
              @change="answerOther(item.id, String($event))"
            />
            <el-button size="small" @click="answer(item.id, { ignored: true })">跳过</el-button>
          </div>
        </div>
      </template>

      <template v-if="unread.length">
        <div class="gi-sec">
          <span>未读会话</span><b>{{ unread.length }}</b>
          <el-button text size="small" style="margin-left:auto" :loading="busy === 'read-all'" @click="markAllRead">全部已读</el-button>
        </div>
        <div v-for="item in unread" :key="item.id" class="gi-card compact">
          <span class="dot" />
          <div class="gi-main">
            <b>{{ item.title || item.id }}</b>
            <small>{{ item.last_message || '有新内容' }}</small>
          </div>
          <el-button text size="small" @click="go(item.id)">打开</el-button>
          <el-button text size="small" :loading="busy === `read-${item.id}`" @click="markRead(item.id)">已读</el-button>
        </div>
      </template>

      <template v-if="running.length">
        <div class="gi-sec"><span>运行中</span><b>{{ running.length }}</b></div>
        <div v-for="item in running" :key="item.id || item.conversation_id" class="gi-card compact">
          <span class="dot run" />
          <div class="gi-main">
            <b>{{ item.title || item.conversation_id }}</b>
            <small>{{ item.status || '运行中' }} · {{ item.workspace_name || '' }}</small>
          </div>
          <el-button text size="small" @click="go(item.conversation_id)">打开</el-button>
          <el-button text size="small" type="danger" :loading="busy === `stop-${item.id}`" @click="stopRun(item.id)">停止</el-button>
        </div>
      </template>

      <template v-if="sessions.length">
        <div class="gi-sec"><span>会话授权</span><b>{{ sessions.length }}</b></div>
        <div class="gi-perm">
          <span>新会话默认权限</span>
          <el-select :model-value="defaultMode" size="small" style="width:130px" @update:model-value="setDefaultMode(String($event))">
            <el-option v-for="(label, value) in modeLabels" :key="value" :label="label" :value="value" />
          </el-select>
        </div>
        <div v-for="session in sessions" :key="session.id" class="gi-perm">
          <span class="gi-perm-name" :title="session.title || session.id">{{ session.title || session.id }}</span>
          <el-select :model-value="session.permission_mode || defaultMode" size="small" style="width:130px" @update:model-value="setSessionMode(session.id, String($event))">
            <el-option v-for="(label, value) in modeLabels" :key="value" :label="label" :value="value" />
          </el-select>
        </div>
      </template>
    </div>
  </section>
</template>

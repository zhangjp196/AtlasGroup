<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import { useAppStore } from '@/stores/app'

interface Bot {
  id: string
  provider: 'feishu' | 'wecom'
  bot_id: string
  status: string
  error?: string
  target_type?: 'role' | 'group' | ''
  target_id?: string
  bound: boolean
}

const props = defineProps<{ targetType: 'role' | 'group'; targetId: string }>()
const app = useAppStore()
const bots = ref<Bot[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const selectedId = ref('')

const candidates = computed(() => bots.value.filter((bot) =>
  !(bot.bound && bot.target_type === props.targetType && bot.target_id === props.targetId),
))
const boundBots = computed(() => bots.value.filter((bot) =>
  bot.bound && bot.target_type === props.targetType && bot.target_id === props.targetId,
))

function providerName(provider: Bot['provider']): string {
  return provider === 'feishu' ? '飞书' : '企业微信'
}
function statusName(status: string): string {
  return ({ connected: '已连接', connecting: '连接中', reconnecting: '正在重连', error: '连接失败' } as Record<string, string>)[status] || status
}
async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const result = await api<{ items: Bot[] }>('/bots')
    bots.value = result.items || []
    if (!candidates.value.some((bot) => bot.id === selectedId.value)) selectedId.value = candidates.value[0]?.id || ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}
async function bind(): Promise<void> {
  const bot = candidates.value.find((item) => item.id === selectedId.value)
  if (!bot || busy.value) return
  if (bot.bound && !window.confirm('这个机器人目前绑定了其他角色或群组。切换后会解除旧目标绑定，继续吗？')) return
  busy.value = bot.id
  error.value = ''
  try {
    await api(`/bots/${encodeURIComponent(bot.id)}/bind`, {
      method: 'POST',
      body: { target_type: props.targetType, target_id: props.targetId },
    })
    app.toast(`机器人已绑定到${props.targetType === 'role' ? '角色' : '群组'}`)
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = ''
  }
}
async function unbind(bot: Bot): Promise<void> {
  if (busy.value) return
  busy.value = bot.id
  error.value = ''
  try {
    await api(`/bots/${encodeURIComponent(bot.id)}/route`, { method: 'DELETE' })
    app.toast('机器人已解绑')
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = ''
  }
}

watch(() => [props.targetType, props.targetId], () => { void load() })
onMounted(() => { void load() })
</script>

<template>
  <div class="bot-target-bindings">
    <section class="group-section">
      <div class="group-section-head">
        <h3>已绑定机器人</h3>
        <span>{{ boundBots.length }} 个</span>
      </div>
      <div v-if="boundBots.length" class="bot-target-list">
        <div v-for="bot in boundBots" :key="bot.id" class="bot-target-row">
          <span class="bot-target-icon">{{ bot.provider === 'feishu' ? '飞' : '企' }}</span>
          <span class="bot-target-main"><b>{{ providerName(bot.provider) }}</b><small>{{ bot.bot_id }} · {{ statusName(bot.status) }}</small></span>
          <button class="btn small" type="button" :disabled="Boolean(busy)" @click="unbind(bot)">{{ busy === bot.id ? '处理中…' : '解绑' }}</button>
        </div>
      </div>
      <p v-else class="group-mode-hint" style="margin:0">这个{{ targetType === 'role' ? '角色' : '群组' }}还没有绑定机器人。</p>
    </section>

    <section class="group-section">
      <div class="group-section-head"><h3>添加机器人</h3><span>机器人只能绑定一个角色或群组</span></div>
      <div v-if="loading" class="group-empty">正在读取机器人…</div>
      <template v-else-if="candidates.length">
        <div class="field">
          <label>选择机器人</label>
          <select v-model="selectedId" class="input">
            <option v-for="bot in candidates" :key="bot.id" :value="bot.id">
              {{ providerName(bot.provider) }} · {{ bot.bot_id }}{{ bot.bound ? '（切换当前绑定）' : '' }}
            </option>
          </select>
        </div>
        <button class="btn primary small" type="button" :disabled="!selectedId || Boolean(busy)" @click="bind">{{ busy ? '绑定中…' : '绑定到此目标' }}</button>
      </template>
      <p v-else-if="!loading && !bots.length" class="group-mode-hint" style="margin:0">还没有机器人连接，请先到「设置 → 机器人」添加。</p>
      <p v-else class="group-mode-hint" style="margin:0">没有可绑定的机器人。</p>
    </section>
    <p v-if="error" class="group-mode-hint bot-target-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.bot-target-bindings{display:flex;flex-direction:column;gap:12px;min-height:100%}
.bot-target-list{display:flex;flex-direction:column;gap:6px}
.bot-target-row{display:flex;align-items:center;gap:9px;min-width:0;padding:8px;border:1px solid var(--line-soft);border-radius:9px;background:var(--bg-2)}
.bot-target-icon{display:grid;place-items:center;width:30px;height:30px;flex:none;border-radius:9px;background:color-mix(in srgb,var(--accent) 12%,var(--panel));color:var(--accent);font-size:11px;font-weight:700}
.bot-target-main{display:flex;flex:1;flex-direction:column;min-width:0;gap:2px}.bot-target-main b{font-size:11px}.bot-target-main small{overflow:hidden;color:var(--faint);font-size:10px;text-overflow:ellipsis;white-space:nowrap}
.bot-target-error{color:var(--danger)}
</style>

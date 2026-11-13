<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect } from 'element-plus'
import { api } from '@/api/client'
import type { Conversation } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useDataStore } from '@/stores/data'

interface Bot {
  id: string
  provider: 'feishu' | 'wecom'
  bot_id: string
  status: string
  error?: string
  conversation_id: string
  lane_id?: string
  peer_id: string
  bound: boolean
}

const PROVIDER_META: Record<Bot['provider'], {
  label: string
  short: string
  desc: string
  accountLabel: string
  accountPlaceholder: string
  secretLabel: string
  secretPlaceholder: string
}> = {
  feishu: {
    label: '飞书',
    short: '飞',
    desc: '应用机器人 · 长连接',
    accountLabel: 'App ID',
    accountPlaceholder: 'cli_a1b2c3d4e5f6g7h8',
    secretLabel: 'App Secret',
    secretPlaceholder: '应用凭证中的 App Secret',
  },
  wecom: {
    label: '企业微信',
    short: '企',
    desc: '智能机器人 · 长连接',
    accountLabel: 'Bot ID',
    accountPlaceholder: '机器人 Bot ID',
    secretLabel: 'Secret',
    secretPlaceholder: '机器人 Secret',
  },
}

const statuses: Record<string, string> = {
  connecting: '连接中', connected: '已连接', reconnecting: '正在重连', error: '连接失败',
}
const statusHints: Record<string, string> = {
  connecting: '正在建立长连接，稍等片刻…',
  connected: '长连接已就绪，消息可实时收发',
  reconnecting: '连接中断，正在自动重连…',
  error: '连接失败，请检查凭据或网络',
}

const app = useAppStore()
const data = useDataStore()
const items = ref<Bot[]>([])
const selected = reactive<Record<string, string>>({})
const provider = ref<Bot['provider']>('feishu')
const account = ref('')
const secret = ref('')
const loading = ref(false)
const refreshing = ref(false)
const busy = ref('')
const error = ref('')
const addDialogVisible = ref(false)
const addError = ref('')
const accountInput = ref<InstanceType<typeof ElInput> | null>(null)
let timer = 0

const conversations = computed(() => data.conversations)
const connectedCount = computed(() => items.value.filter((bot) => bot.status === 'connected').length)
const boundCount = computed(() => items.value.filter((bot) => bot.bound).length)
const providerMeta = computed(() => PROVIDER_META[provider.value])
const providerList = computed(() => (
  Object.keys(PROVIDER_META) as Array<Bot['provider']>
).map((id) => ({ id, ...PROVIDER_META[id] })))

function providerName(value: Bot['provider']): string {
  return PROVIDER_META[value]?.label || value
}

function providerShort(value: Bot['provider']): string {
  return PROVIDER_META[value]?.short || 'B'
}

function statusClass(status: string): string {
  return ['connected', 'connecting', 'reconnecting', 'error'].includes(status) ? status : 'unknown'
}

async function refresh(): Promise<void> {
  try {
    const result = await api<{ items: Bot[] }>('/bots')
    items.value = result.items || []
    for (const bot of items.value) {
      if (!selected[bot.id]) selected[bot.id] = bot.conversation_id || ''
    }
    error.value = ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function manualRefresh(): Promise<void> {
  if (refreshing.value) return
  refreshing.value = true
  await refresh()
  refreshing.value = false
}

function focusAdd(): void {
  addError.value = ''
  addDialogVisible.value = true
}

function focusAccount(): void {
  accountInput.value?.focus()
}

function chooseProvider(next: Bot['provider']): void {
  if (provider.value === next) return
  provider.value = next
  account.value = ''
  secret.value = ''
}

function titleOf(id: string): string {
  return data.conversations.find((conv) => conv.id === id)?.title || id
}

function optionsFor(bot: Bot): Conversation[] {
  const list = [...conversations.value]
  if (bot.conversation_id && !list.some((conv) => conv.id === bot.conversation_id)) {
    list.unshift({ id: bot.conversation_id, title: bot.conversation_id })
  }
  return list
}

async function add(): Promise<void> {
  if (busy.value) return
  busy.value = 'add'
  addError.value = ''
  try {
    await api('/bots', {
      method: 'POST',
      body: { provider: provider.value, bot_id: account.value.trim(), secret: secret.value.trim() },
    })
    account.value = ''
    secret.value = ''
    addDialogVisible.value = false
    app.toast('机器人已添加')
    await refresh()
  } catch (cause) {
    addError.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = ''
  }
}

async function bind(bot: Bot): Promise<void> {
  const conversationId = selected[bot.id]
  if (!conversationId || busy.value) return
  busy.value = bot.id
  error.value = ''
  try {
    await api('/bots/' + encodeURIComponent(bot.id) + '/bind', {
      method: 'POST',
      body: { conversation_id: conversationId },
    })
    app.toast('机器人已绑定到该会话')
    await refresh()
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
    await api('/bots/' + encodeURIComponent(bot.id) + '/route', { method: 'DELETE' })
    selected[bot.id] = ''
    app.toast('机器人已解绑')
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = ''
  }
}

async function remove(bot: Bot): Promise<void> {
  if (busy.value) return
  if (!window.confirm('删除机器人连接后，当前绑定也会清除。确定删除？')) return
  busy.value = bot.id
  error.value = ''
  try {
    await api('/bots/' + encodeURIComponent(bot.id), { method: 'DELETE' })
    delete selected[bot.id]
    app.toast('机器人已删除')
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = ''
  }
}

onMounted(() => {
  loading.value = true
  void data.loadConversations()
  void refresh()
  timer = window.setInterval(() => void refresh(), 2500)
})
onBeforeUnmount(() => { window.clearInterval(timer) })
</script>

<template>
  <section class="bots-settings">
    <section class="bots-hero">
      <div class="hero-main">
        <div class="hero-eyebrow">消息连接 · IM BRIDGE</div>
        <h2>机器人</h2>
        <p>
          把飞书 / 企业微信聊天接到会话里。一个机器人同一时间只绑定一个会话，
          收到的消息默认写入该会话下自动新建的栏目，回复发回原聊天。
        </p>
        <div class="hero-stats">
          <span class="hero-stat"><b>{{ items.length }}</b>个机器人</span>
          <span class="hero-stat"><i class="dot on" />{{ connectedCount }} 个已连接</span>
          <span class="hero-stat"><i class="dot bound" />{{ boundCount }} 个已绑定会话</span>
        </div>
      </div>
      <div class="hero-side">
        <ElButton :loading="refreshing" @click="manualRefresh">刷新状态</ElButton>
        <button class="hero-cta" type="button" @click="focusAdd">＋ 添加机器人</button>
        <small>状态每 2.5 秒自动同步</small>
      </div>
    </section>

    <p v-if="error" class="bots-alert" role="alert">
      <span class="alert-mark">!</span>
      <span>{{ error }}</span>
    </p>

    <div v-if="loading && !items.length" class="bots-loading" aria-hidden="true">
      <div v-for="n in 2" :key="n" class="bot-skeleton" />
    </div>

    <div v-if="items.length" class="bot-list">
      <article
        v-for="bot in items"
        :key="bot.id"
        class="bot-card"
        :class="['is-' + statusClass(bot.status), { 'is-bound': bot.bound }]"
      >
        <header class="bot-head">
          <span class="bot-logo" :class="bot.provider">{{ providerShort(bot.provider) }}</span>
          <div class="bot-id">
            <div class="bot-title-line">
              <b>{{ providerName(bot.provider) }}机器人</b>
              <code class="bot-account" :title="bot.bot_id">{{ bot.bot_id }}</code>
              <span v-if="bot.bound" class="bound-chip"><i />已绑定</span>
            </div>
            <small>{{ statusHints[bot.status] || '状态未知' }}</small>
          </div>
          <span class="status-pill" :class="statusClass(bot.status)">
            <i />{{ statuses[bot.status] || bot.status }}
          </span>
          <ElButton class="bot-delete" size="small" text type="danger"
            :disabled="busy === bot.id" @click="remove(bot)">删除</ElButton>
        </header>

        <p v-if="bot.error" class="bot-error" role="alert">{{ bot.error }}</p>

        <div class="bot-meta">
          <div>
            <span>当前绑定会话</span>
            <b :class="{ muted: !bot.bound }">{{ bot.bound ? titleOf(bot.conversation_id) : '尚未绑定' }}</b>
          </div>
          <div>
            <span>最近聊天</span>
            <b :class="{ muted: !bot.peer_id }">{{ bot.peer_id || '—' }}</b>
          </div>
        </div>

        <div class="bot-route">
          <label class="route-field">
            <span>目标会话 <small>保存后立即生效，换绑会解除旧会话</small></span>
            <ElSelect v-model="selected[bot.id]" filterable placeholder="选择接收机器人消息的会话" class="conv-select">
              <ElOption v-for="conv in optionsFor(bot)" :key="conv.id"
                :label="conv.title || conv.id" :value="conv.id" />
            </ElSelect>
          </label>
          <div class="route-actions">
            <ElButton type="primary" :disabled="busy === bot.id || !selected[bot.id]" @click="bind(bot)">保存绑定</ElButton>
            <ElButton v-if="bot.bound" :disabled="busy === bot.id" @click="unbind(bot)">解绑</ElButton>
          </div>
        </div>

        <p class="bot-foot">
          <template v-if="bot.bound">消息默认写入「{{ titleOf(bot.conversation_id) }}」下自动新建的栏目；回复发回原聊天。支持 /new、/clear、/compact、/stop、/help、/status，也可以直接发「新建栏目」「清空上下文」「停止」等短句。</template>
          <template v-else>尚未绑定会话；未绑定时收到的消息会被忽略。</template>
        </p>
      </article>
    </div>

    <div v-else-if="!loading" class="bots-empty">
      <span class="empty-icon">✳</span>
      <b>还没有机器人</b>
      <p>按下方向导添加飞书或企业微信机器人，就能把 IM 聊天接入会话。</p>
      <button class="empty-cta" type="button" @click="focusAdd">＋ 添加第一个机器人</button>
    </div>

    <ElDialog
      v-model="addDialogVisible"
      class="bot-add-dialog"
      width="min(640px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      @opened="focusAccount"
    >
      <template #header>
        <header class="bot-add-head">
          <div>
            <div class="hero-eyebrow">连接向导</div>
            <h3>添加机器人</h3>
            <p>选择平台并填入应用凭据，保存后自动建立长连接。</p>
          </div>
          <span class="add-chip">凭证仅保存在本机</span>
        </header>
      </template>

      <div class="bot-add">
        <div class="provider-cards" role="group" aria-label="机器人平台">
          <button
            v-for="item in providerList"
            :key="item.id"
            type="button"
            class="provider-card"
            :class="{ on: provider === item.id }"
            :aria-pressed="provider === item.id"
            @click="chooseProvider(item.id)"
          >
            <span class="bot-logo small" :class="item.id">{{ item.short }}</span>
            <span class="provider-copy">
              <b>{{ item.label }}机器人</b>
              <small>{{ item.desc }}</small>
            </span>
            <span class="provider-check">✓</span>
          </button>
        </div>

        <form class="bot-form" @submit.prevent="add">
          <label class="field">
            <span>{{ providerMeta.accountLabel }}</span>
            <ElInput
              ref="accountInput"
              v-model="account"
              class="field-input"
              spellcheck="false"
              :placeholder="providerMeta.accountPlaceholder"
            />
          </label>
          <label class="field">
            <span>{{ providerMeta.secretLabel }}</span>
            <ElInput
              v-model="secret"
              class="field-input"
              type="password"
              show-password
              spellcheck="false"
              :placeholder="providerMeta.secretPlaceholder"
            />
          </label>
          <ElButton
            class="connect-btn"
            type="primary"
            native-type="submit"
            :loading="busy === 'add'"
            :disabled="!account.trim() || !secret.trim()"
          >
            添加机器人
          </ElButton>
        </form>

        <p v-if="addError" class="bots-alert add-alert" role="alert">
          <span class="alert-mark">!</span>
          <span>{{ addError }}</span>
        </p>

        <div class="help-grid">
          <div class="help-card">
            <b><span class="help-badge feishu">飞书</span>开放平台</b>
            <p>启用机器人能力，长连接订阅 <code>im.message.receive_v1</code> 并开通发送消息权限，然后复制 App ID / App Secret。</p>
          </div>
          <div class="help-card">
            <b><span class="help-badge wecom">企业微信</span>管理后台</b>
            <p>创建 API 模式智能机器人，选择长连接方式，获取 Bot ID 与 Secret 后填到上方。</p>
          </div>
        </div>
      </div>
    </ElDialog>
  </section>
</template>

<style scoped>
.bots-settings{display:flex;flex-direction:column;gap:14px;min-width:0;padding-bottom:8px}

/* 顶部概览 */
.bots-hero{position:relative;display:flex;align-items:flex-start;gap:20px;padding:22px 24px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 8%,var(--panel)),var(--panel) 55%);box-shadow:0 16px 38px color-mix(in srgb,var(--accent) 6%,transparent);overflow:hidden}
.bots-hero::before{content:"";position:absolute;right:-70px;top:-110px;width:280px;height:280px;border-radius:50%;background:radial-gradient(circle,color-mix(in srgb,var(--accent) 17%,transparent),transparent 68%);pointer-events:none}
.bots-hero::after{content:"";position:absolute;left:24px;right:24px;top:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--accent-2),transparent)}
.hero-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:5px;position:relative;z-index:1}
.hero-eyebrow{font:9.5px var(--mono);letter-spacing:.15em;text-transform:uppercase;color:var(--accent-ink)}
.hero-main h2{margin:0;font-size:24px;line-height:1.15;font-weight:700;letter-spacing:-.02em}
.hero-main p{margin:2px 0 0;max-width:720px;font-size:11.5px;line-height:1.7;color:var(--dim)}
.hero-stats{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}
.hero-stat{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--line-soft);border-radius:999px;background:color-mix(in srgb,var(--panel) 72%,transparent);font-size:10.5px;color:var(--dim)}
.hero-stat b{font:600 11px var(--mono);color:var(--text)}
.hero-stat .dot{width:6px;height:6px;border-radius:50%;background:var(--faint);flex:none}
.hero-stat .dot.on{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 15%,transparent)}
.hero-stat .dot.bound{background:var(--accent-2)}
.hero-side{position:relative;z-index:1;display:flex;flex-direction:column;align-items:flex-end;gap:8px;flex:none}
.hero-side small{font-size:9.5px;color:var(--faint);white-space:nowrap}
.hero-cta{padding:7px 14px;border:1px solid var(--accent);border-radius:9px;background:var(--accent);color:var(--on-accent);font:600 11.5px var(--font);cursor:pointer;box-shadow:0 8px 20px color-mix(in srgb,var(--accent) 22%,transparent);transition:transform .15s ease,background .15s ease}
.hero-cta:hover{background:color-mix(in srgb,var(--accent) 86%,var(--text));transform:translateY(-1px)}
.hero-cta:active{transform:none}

/* 错误提示 */
.bots-alert{display:flex;align-items:flex-start;gap:9px;margin:0;padding:10px 12px;border:1px solid color-mix(in srgb,var(--danger) 34%,var(--line));border-radius:12px;background:color-mix(in srgb,var(--danger) 7%,var(--panel));color:var(--danger);font-size:11px;line-height:1.6;overflow-wrap:anywhere}
.alert-mark{display:grid;place-items:center;width:17px;height:17px;border-radius:50%;background:color-mix(in srgb,var(--danger) 18%,transparent);font:700 10px var(--mono);flex:none;margin-top:1px}

/* 加载骨架 */
.bots-loading{display:flex;flex-direction:column;gap:10px}
.bot-skeleton{height:158px;border:1px solid var(--line-soft);border-radius:14px;background:linear-gradient(90deg,var(--panel) 25%,var(--panel-2) 37%,var(--panel) 63%);background-size:400% 100%;animation:botsShimmer 1.4s ease infinite}
@keyframes botsShimmer{0%{background-position:100% 0}100%{background-position:0 0}}

/* 机器人卡片 */
.bot-list{display:flex;flex-direction:column;gap:10px}
.bot-card{position:relative;display:flex;flex-direction:column;gap:12px;padding:14px 16px;border:1px solid var(--line);border-radius:14px;background:var(--panel);box-shadow:0 10px 26px color-mix(in srgb,#000 4%,transparent);transition:border-color .15s ease,box-shadow .15s ease}
.bot-card:hover{border-color:color-mix(in srgb,var(--accent) 28%,var(--line));box-shadow:0 14px 34px color-mix(in srgb,#000 7%,transparent)}
.bot-card::before{content:"";position:absolute;left:0;top:15px;bottom:15px;width:2px;border-radius:0 2px 2px 0;background:var(--line);opacity:.8}
.bot-card.is-connected::before{background:var(--ok)}
.bot-card.is-connecting::before{background:var(--accent-2)}
.bot-card.is-reconnecting::before{background:var(--warn)}
.bot-card.is-error::before{background:var(--danger)}
.bot-card.is-bound{background:linear-gradient(180deg,color-mix(in srgb,var(--accent) 3%,var(--panel)),var(--panel) 90px)}
.bot-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0}
.bot-logo{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;flex:none;font-size:15px;font-weight:750;line-height:1;background:linear-gradient(140deg,color-mix(in srgb,#3370ff 24%,var(--panel-2)),var(--panel));border:1px solid color-mix(in srgb,#3370ff 30%,var(--line));color:color-mix(in srgb,#3370ff 45%,var(--text));box-shadow:inset 0 1px 0 color-mix(in srgb,#fff 10%,transparent)}
.bot-logo.wecom{background:linear-gradient(140deg,color-mix(in srgb,#07c160 22%,var(--panel-2)),var(--panel));border-color:color-mix(in srgb,#07c160 30%,var(--line));color:color-mix(in srgb,#07c160 45%,var(--text))}
.bot-logo.small{width:32px;height:32px;border-radius:9px;font-size:13px}
.bot-id{flex:1;min-width:180px;display:flex;flex-direction:column;gap:2px}
.bot-title-line{display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0}
.bot-title-line b{font-size:12.5px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bot-account{max-width:260px;padding:1px 6px;border:1px solid var(--line-soft);border-radius:6px;background:var(--bg-2);color:var(--faint);font:10px var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bot-id small{font-size:10px;color:var(--faint);line-height:1.4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bound-chip{display:inline-flex;align-items:center;gap:4px;padding:2px 7px;border:1px solid color-mix(in srgb,var(--ok) 32%,var(--line));border-radius:999px;background:color-mix(in srgb,var(--ok) 8%,var(--panel-2));color:color-mix(in srgb,var(--ok) 76%,var(--text));font-size:9.5px;white-space:nowrap}
.bound-chip i{width:5px;height:5px;border-radius:50%;background:var(--ok)}
.status-pill{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border:1px solid var(--line-soft);border-radius:999px;background:var(--bg-2);color:var(--faint);font-size:10px;white-space:nowrap;flex:none}
.status-pill i{width:6px;height:6px;border-radius:50%;background:var(--faint);flex:none}
.status-pill.connected{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 36%,var(--line));background:color-mix(in srgb,var(--ok) 8%,var(--bg-2))}
.status-pill.connected i{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 15%,transparent);animation:statusPulse 2s ease-in-out infinite}
.status-pill.connecting{color:var(--accent-2);border-color:color-mix(in srgb,var(--accent-2) 36%,var(--line));background:color-mix(in srgb,var(--accent-2) 8%,var(--bg-2))}
.status-pill.connecting i{background:var(--accent-2)}
.status-pill.reconnecting{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 36%,var(--line));background:color-mix(in srgb,var(--warn) 8%,var(--bg-2))}
.status-pill.reconnecting i{background:var(--warn)}
.status-pill.error{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 36%,var(--line));background:color-mix(in srgb,var(--danger) 8%,var(--bg-2))}
.status-pill.error i{background:var(--danger)}
@keyframes statusPulse{0%,100%{box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 15%,transparent)}50%{box-shadow:0 0 0 5px color-mix(in srgb,var(--ok) 7%,transparent)}}
.bot-delete{flex:none}
.bot-error{margin:0;padding:7px 9px;border:1px solid color-mix(in srgb,var(--danger) 26%,var(--line));border-radius:9px;background:color-mix(in srgb,var(--danger) 6%,var(--bg-2));color:var(--danger);font-size:10.5px;line-height:1.55;overflow-wrap:anywhere}
.bot-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:10px 12px;border:1px solid var(--line-soft);border-radius:11px;background:var(--bg-2)}
.bot-meta > div{min-width:0;display:flex;flex-direction:column;gap:2px}
.bot-meta span{font-size:9.5px;color:var(--faint);letter-spacing:.03em}
.bot-meta b{font-size:11.5px;font-weight:550;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bot-meta b.muted{color:var(--faint);font-weight:500}
.bot-route{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap}
.route-field{flex:1;min-width:220px;display:flex;flex-direction:column;gap:5px;min-width:0}
.route-field > span{display:flex;align-items:baseline;gap:6px;font-size:10px;color:var(--dim);flex-wrap:wrap}
.route-field > span small{font-size:9px;color:var(--faint)}
.conv-select{width:100%}
.route-actions{display:flex;gap:6px;align-items:center;flex:none}
.bot-foot{display:flex;align-items:flex-start;gap:6px;margin:0;font-size:10px;line-height:1.55;color:var(--faint)}
.bot-foot::before{content:"↳";color:var(--accent);flex:none;line-height:1.55}

/* 空状态 */
.bots-empty{display:flex;flex-direction:column;align-items:center;gap:7px;padding:34px 20px 30px;border:1px dashed color-mix(in srgb,var(--line) 80%,var(--accent));border-radius:16px;background:color-mix(in srgb,var(--panel) 62%,transparent);text-align:center}
.empty-icon{display:grid;place-items:center;width:46px;height:46px;margin-bottom:2px;border:1px solid color-mix(in srgb,var(--accent) 26%,var(--line));border-radius:15px;background:color-mix(in srgb,var(--accent) 9%,var(--panel));color:var(--accent);font-size:21px;line-height:1}
.bots-empty b{font-size:13px}
.bots-empty p{margin:0;max-width:420px;font-size:11px;line-height:1.65;color:var(--faint)}
.empty-cta{margin-top:8px;padding:6px 13px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);font-size:11px;cursor:pointer;transition:border-color .15s ease,color .15s ease}
.empty-cta:hover{border-color:color-mix(in srgb,var(--accent) 45%,var(--line));color:var(--accent-ink)}

/* 添加机器人 */
.bot-add{display:flex;flex-direction:column;gap:14px}
.bot-add-head{display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap}
.bot-add-head > div{display:flex;flex-direction:column;gap:3px;min-width:0}
.bot-add-head h3{margin:0;font-size:15px;font-weight:650}
.bot-add-head p{margin:0;font-size:10.5px;line-height:1.55;color:var(--faint)}
.add-chip{margin-left:auto;align-self:center;padding:3px 9px;border:1px solid var(--line-soft);border-radius:999px;background:var(--bg-2);color:var(--faint);font-size:9.5px;white-space:nowrap}
.provider-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.provider-card{display:flex;align-items:center;gap:10px;width:100%;padding:10px 12px;border:1px solid var(--line-soft);border-radius:12px;background:var(--bg-2);color:inherit;text-align:left;cursor:pointer;transition:border-color .15s ease,background .15s ease,box-shadow .15s ease}
.provider-card:hover{border-color:color-mix(in srgb,var(--accent) 30%,var(--line));background:var(--panel-2)}
.provider-card.on{border-color:color-mix(in srgb,var(--accent) 45%,var(--line));background:color-mix(in srgb,var(--accent) 8%,var(--panel-2));box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 14%,transparent)}
.provider-copy{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.provider-copy b{font-size:11.5px;font-weight:600}
.provider-copy small{font-size:9.5px;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.provider-check{display:grid;place-items:center;width:18px;height:18px;border:1px solid var(--line);border-radius:50%;color:transparent;font-size:10px;flex:none;transition:background .15s ease,border-color .15s ease,color .15s ease}
.provider-card.on .provider-check{border-color:var(--accent);background:var(--accent);color:var(--on-accent)}
.bot-form{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:10px;align-items:end}
.field{display:flex;flex-direction:column;gap:5px;min-width:0}
.field > span{font-size:10px;color:var(--dim)}
.field-input{width:100%}
.connect-btn{height:32px;padding:0 18px;white-space:nowrap}
.help-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding-top:13px;border-top:1px solid var(--line-soft)}
.help-card{display:flex;flex-direction:column;gap:4px;padding:10px 12px;border:1px solid var(--line-soft);border-radius:11px;background:var(--bg-2)}
.help-card > b{display:flex;align-items:center;gap:6px;font-size:10.5px;font-weight:600;color:var(--dim)}
.help-card p{margin:0;font-size:10px;line-height:1.65;color:var(--faint)}
.help-badge{padding:1px 6px;border:1px solid color-mix(in srgb,#3370ff 32%,var(--line));border-radius:5px;background:color-mix(in srgb,#3370ff 8%,var(--panel-2));color:color-mix(in srgb,#3370ff 42%,var(--text));font-size:9px;font-weight:600}
.help-badge.wecom{border-color:color-mix(in srgb,#07c160 32%,var(--line));background:color-mix(in srgb,#07c160 8%,var(--panel-2));color:color-mix(in srgb,#07c160 42%,var(--text))}
.help-card code{padding:0 4px;border:1px solid var(--line-soft);border-radius:4px;background:var(--bg);color:var(--dim);font:9.5px var(--mono);overflow-wrap:anywhere}

:deep(.bot-add-dialog){max-width:calc(100vw - 32px);border:1px solid var(--line);border-radius:16px;background:var(--panel)}
:deep(.bot-add-dialog .el-dialog__header){margin:0;padding:18px 20px 14px;border-bottom:1px solid var(--line-soft)}
:deep(.bot-add-dialog .el-dialog__body){padding:16px 20px 20px}
.add-alert{margin:0}

@media (max-width:760px){
  .bots-hero{flex-direction:column;gap:14px;padding:18px}
  .hero-main h2{font-size:21px}
  .hero-side{flex-direction:row;align-items:center;flex-wrap:wrap}
  .hero-side small{width:100%}
  .bot-card{padding:12px 13px}
  .bot-id{min-width:140px}
  .bot-meta{grid-template-columns:1fr}
  .bot-route{flex-direction:column;align-items:stretch}
  .route-field{min-width:0;width:100%}
  .route-actions{justify-content:flex-end}
  .provider-cards,.help-grid,.bot-form{grid-template-columns:1fr}
  .connect-btn{width:100%}
  .add-chip{margin-left:0}
}
</style>

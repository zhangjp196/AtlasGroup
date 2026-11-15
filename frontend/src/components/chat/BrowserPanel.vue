<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { automateEmbeddedBrowser, navigateEmbeddedBrowser } from '@/composables/useNative'
import { startBrowserCommandBridge } from '@/lib/browserBridge'
import { useAppStore } from '@/stores/app'

defineProps<{ open: boolean }>()
const app = useAppStore()
const address = ref('https://example.com')
const pageUrl = ref('')
const pageKey = ref(0)
const selector = ref('')
const value = ref('')
const result = ref('')
const busy = ref(false)
const pageStatus = ref('输入网址开始浏览')
const history = ref<string[]>([])
const historyIndex = ref(-1)
const automationOpen = ref(false)
function normalizeUrl(input: string): string {
  const raw = input.trim()
  if (!raw) throw new Error('请输入网页地址')
  const url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`)
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('只支持 HTTP 或 HTTPS 网页')
  return url.href
}

function showPage(url: string, remember = true): void {
  pageUrl.value = url
  address.value = url
  pageKey.value += 1
  pageStatus.value = '正在加载网页…'
  if (remember) {
    history.value = [...history.value.slice(0, historyIndex.value + 1), url]
    historyIndex.value = history.value.length - 1
  }
}

onMounted(() => {
  startBrowserCommandBridge()
})

function navigate(): void {
  try {
    showPage(normalizeUrl(address.value))
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}
function goBack(): void {
  if (historyIndex.value <= 0) return
  historyIndex.value -= 1
  showPage(history.value[historyIndex.value], false)
}
function goForward(): void {
  if (historyIndex.value >= history.value.length - 1) return
  historyIndex.value += 1
  showPage(history.value[historyIndex.value], false)
}
function reload(): void {
  if (!pageUrl.value) return
  pageStatus.value = '正在刷新网页…'
  pageKey.value += 1
}
function onPageLoad(): void {
  pageStatus.value = '网页已加载'
}
async function openStandalone(): Promise<void> {
  if (!pageUrl.value) {
    try { showPage(normalizeUrl(address.value)) } catch (error) {
      app.toast(error instanceof Error ? error.message : String(error), 'bad')
      return
    }
  }
  try {
    if (!await navigateEmbeddedBrowser(pageUrl.value)) window.open(pageUrl.value, '_blank', 'noopener')
  } catch (error) {
    app.toast(error instanceof Error ? error.message : String(error), 'bad')
  }
}
async function automate(action: 'click' | 'fill' | 'text' | 'title' | 'url'): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    const opened = await navigateEmbeddedBrowser(pageUrl.value || normalizeUrl(address.value))
    if (!opened) throw new Error('当前环境不支持桌面浏览器自动化')
    const response = await automateEmbeddedBrowser(action, selector.value.trim(), value.value)
    result.value = typeof response.result === 'string' ? response.result : JSON.stringify(response.result, null, 2)
  } catch (error) {
    result.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section v-if="open" class="browser-panel" aria-label="浏览器">
    <form class="browser-toolbar" @submit.prevent="navigate">
      <button class="btn small" type="button" title="后退" aria-label="后退" :disabled="historyIndex <= 0" @click="goBack">‹</button>
      <button class="btn small" type="button" title="前进" aria-label="前进" :disabled="historyIndex >= history.length - 1" @click="goForward">›</button>
      <button class="btn small" type="button" title="刷新" aria-label="刷新" :disabled="!pageUrl" @click="reload">↻</button>
      <input v-model="address" class="input browser-address" aria-label="网页地址" placeholder="输入网址" autocomplete="url" spellcheck="false" />
      <button class="btn primary browser-go" type="submit">前往</button>
      <button class="btn small browser-standalone" type="button" title="网站禁止内嵌时，在独立窗口打开" aria-label="在独立窗口打开" :disabled="!pageUrl && !address" @click="openStandalone">↗</button>
    </form>

    <div class="browser-page-wrap">
      <iframe
        v-if="pageUrl"
        :key="pageKey"
        class="browser-page"
        :src="pageUrl"
        title="网页内容"
        referrerpolicy="no-referrer"
        sandbox="allow-forms allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals"
        allow="clipboard-read; clipboard-write; fullscreen"
        @load="onPageLoad"
      />
      <div v-else class="browser-start">
        <span class="browser-globe">🌐</span>
        <b>输入网址开始浏览</b>
        <small>网页会直接显示在此面板中</small>
      </div>
      <div class="browser-page-status" role="status">{{ pageStatus }}</div>
    </div>

    <details :open="automationOpen" class="browser-automation" @toggle="automationOpen = ($event.currentTarget as HTMLDetailsElement).open">
      <summary>页面自动化 <span>桌面浏览器控制与 DOM 操作</span></summary>
      <div class="browser-automation-body">
        <label class="field"><span>CSS 选择器</span><input v-model="selector" class="input" placeholder="input[name=q]" /></label>
        <label class="field"><span>填入内容</span><input v-model="value" class="input" placeholder="要输入的文本" /></label>
        <div class="browser-actions">
          <button class="btn small" :disabled="busy" @click.prevent="automate('click')">点击</button>
          <button class="btn small" :disabled="busy" @click.prevent="automate('fill')">填入</button>
          <button class="btn small" :disabled="busy" @click.prevent="automate('text')">读文本</button>
          <button class="btn small" :disabled="busy" @click.prevent="automate('title')">读标题</button>
          <button class="btn small" :disabled="busy" @click.prevent="automate('url')">读地址</button>
        </div>
        <pre class="browser-result" role="status">{{ result || '操作结果会显示在这里。' }}</pre>
      </div>
    </details>
  </section>
</template>

<style scoped>
.browser-panel{height:100%;min-height:0;display:flex;flex-direction:column;gap:8px;padding:8px;overflow:hidden;background:var(--bg)}
.browser-toolbar{flex:none;display:flex;align-items:center;gap:5px;min-width:0;padding:4px;border:1px solid var(--line);border-radius:10px;background:var(--bg-2)}
.browser-toolbar .btn{flex:none}.browser-toolbar .btn.small{min-width:32px;padding-inline:7px;font-size:15px;line-height:1.2}
.browser-address{flex:1;min-width:0;height:32px;padding:5px 10px;font-size:11px}
.browser-go{height:32px;padding:5px 10px;font-size:10px}.browser-standalone{font-size:13px!important}
.browser-page-wrap{position:relative;display:flex;flex:1;min-width:0;min-height:0;overflow:hidden;border:1px solid var(--line);border-radius:9px;background:#fff}
.browser-page{display:block;flex:1;width:100%;height:100%;min-width:0;min-height:0;border:0;background:#fff}
.browser-start{display:flex;flex:1;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#526174}.browser-globe{font-size:32px}.browser-start b{font-size:14px}.browser-start small{font-size:11px}
.browser-page-status{position:absolute;right:8px;bottom:8px;padding:4px 7px;border:1px solid #dce4ec;border-radius:6px;background:color-mix(in srgb,#fff 90%,transparent);color:#536274;font-size:9px;pointer-events:none}
.browser-automation{flex:none;max-height:40%;overflow:auto;border:1px solid var(--line-soft);border-radius:8px;background:var(--bg-2)}
.browser-automation summary{display:flex;align-items:center;gap:8px;padding:8px 10px;color:var(--text);font-size:10px;cursor:pointer;list-style:none}.browser-automation summary::-webkit-details-marker{display:none}.browser-automation summary::before{content:'›';color:var(--faint);font-size:14px}.browser-automation[open] summary::before{content:'⌄'}.browser-automation summary span{color:var(--faint);font-size:9px}
.browser-automation-body{display:flex;flex-direction:column;gap:7px;padding:0 9px 9px}.browser-automation .field{margin:0;gap:4px;font-size:9px}.browser-automation .input{min-height:30px;padding:5px 8px;font-size:10px}.browser-actions{display:flex;gap:5px;flex-wrap:wrap}.browser-result{max-height:100px;overflow:auto;margin:0;padding:7px;border:1px solid var(--line-soft);border-radius:7px;background:var(--code-bg);font:10px/1.45 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere}
</style>

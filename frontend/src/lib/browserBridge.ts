import { api } from '@/api/client'
import { automateEmbeddedBrowser, hasNativeBridge, navigateEmbeddedBrowser } from '@/composables/useNative'

interface QueuedBrowserCommand {
  id: string
  op: string
  url?: string
  selector?: string
  value?: string
}

let started = false

async function commandPump(): Promise<void> {
  while (true) {
    if (!hasNativeBridge()) {
      await new Promise((resolve) => window.setTimeout(resolve, 700))
      continue
    }
    try {
      const command = await api<QueuedBrowserCommand | null>('/local/browser/next')
      if (!command) continue
      let result: unknown = null
      let error = ''
      try {
        if (command.op === 'navigate') {
          const opened = await navigateEmbeddedBrowser(command.url || '')
          if (!opened) throw new Error('无法打开桌面内置浏览器')
          result = { navigated: true, url: command.url || '' }
        } else {
          const action = command.op === 'read_text' ? 'text' : command.op
          result = (await automateEmbeddedBrowser(action, command.selector || '', command.value || '')).result
        }
      } catch (cause) {
        error = cause instanceof Error ? cause.message : String(cause)
      }
      await api(`/local/browser/${encodeURIComponent(command.id)}/result`, {
        method: 'POST', body: { result, error },
      })
    } catch {
      await new Promise((resolve) => window.setTimeout(resolve, 900))
    }
  }
}

/** Start the renderer-wide native browser command consumer once. */
export function startBrowserCommandBridge(): void {
  if (started) return
  started = true
  const ping = () => {
    if (hasNativeBridge()) void api('/local/browser/heartbeat', { method: 'POST' }).catch(() => undefined)
  }
  ping()
  window.setInterval(ping, 4000)
  void commandPump()
}

declare global {
  interface Window {
    pywebview?: {
      api?: Record<string, (...args: unknown[]) => Promise<unknown>>
    }
  }
}

export function hasNativeBridge(): boolean {
  return Boolean(window.pywebview?.api)
}

export async function pickFolder(): Promise<string> {
  const api = window.pywebview?.api
  if (!api?.pick_folder) return ''
  try {
    const result = await api.pick_folder()
    return typeof result === 'string' ? result : ''
  } catch {
    return ''
  }
}

export async function revealData(): Promise<boolean> {
  const api = window.pywebview?.api
  if (!api?.reveal_data) return false
  try {
    return Boolean(await api.reveal_data())
  } catch {
    return false
  }
}

export async function openExternal(url: string): Promise<boolean> {
  const api = window.pywebview?.api
  if (!api?.open_external) {
    window.open(url, '_blank', 'noopener')
    return true
  }
  try {
    return Boolean(await api.open_external(url))
  } catch {
    return false
  }
}

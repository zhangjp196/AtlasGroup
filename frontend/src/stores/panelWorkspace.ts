import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { WorkspaceToolPanelState } from '@/components/WorkspaceToolPanel.vue'

export interface BoardDesktop {
  id: string
  name: string
}

const DESKTOPS_KEY = 'atlasx.panel-board.desktops.v1'
const DESKTOP_ASSIGNMENTS_KEY = 'atlasx.panel-board.assignments.v1'
const BOARD_HIDDEN_KEY = 'atlasx.panel-board.hidden.v1'
const TOOL_PANELS_KEY = 'atlasx.panel-board.tools.v1'
const PANEL_LAYOUTS_KEY = 'atlasx.panel-board.layouts.v1'
const PANEL_ORDERS_KEY = 'atlasx.panel-board.orders.v1'

function readJson<T>(key: string, fallback: T): T {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null')
    return value === null ? fallback : value as T
  } catch {
    return fallback
  }
}

function readDesktops(): BoardDesktop[] {
  const saved = readJson<unknown>(DESKTOPS_KEY, [])
  if (!Array.isArray(saved)) return [{ id: 'desktop-1', name: '桌面 1' }]
  const desktops = saved.filter((item) => item && typeof item.id === 'string' && item.id)
    .map((item, index) => ({ id: item.id, name: String(item.name || `桌面 ${index + 1}`) }))
  return desktops.length ? desktops : [{ id: 'desktop-1', name: '桌面 1' }]
}

export const usePanelWorkspaceStore = defineStore('panelWorkspace', () => {
  const desktops = ref<BoardDesktop[]>(readDesktops())
  const desktopAssignments = ref<Record<string, string>>(readJson(DESKTOP_ASSIGNMENTS_KEY, {}))
  const hiddenPanels = ref<Record<string, boolean>>(readJson(BOARD_HIDDEN_KEY, {}))
  const toolPanels = ref<WorkspaceToolPanelState[]>(readJson(TOOL_PANELS_KEY, [])
    .filter((item: WorkspaceToolPanelState) => item && item.key && item.desktopId
      && ['browser', 'terminal', 'tasks', 'conversations', 'schedules', 'workspaces', 'organization'].includes(item.kind)))
  const layouts = ref<Record<string, { minimized: boolean; maximized: boolean }>>(readJson(PANEL_LAYOUTS_KEY, {}))
  const orders = ref<Record<string, number>>(readJson(PANEL_ORDERS_KEY, {}))
  const page = ref(0)
  const pageCount = computed(() => Math.max(1, Math.ceil(desktops.value.length / 4)))
  const visibleDesktops = computed(() => desktops.value.slice(page.value * 4, page.value * 4 + 4))

  function persist(key: string, value: unknown): void {
    try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage may be unavailable */ }
  }
  function saveDesktops(): void { persist(DESKTOPS_KEY, desktops.value) }
  function saveAssignments(): void { persist(DESKTOP_ASSIGNMENTS_KEY, desktopAssignments.value) }
  function saveHidden(): void { persist(BOARD_HIDDEN_KEY, hiddenPanels.value) }
  function saveTools(): void { persist(TOOL_PANELS_KEY, toolPanels.value) }
  function saveLayouts(): void { persist(PANEL_LAYOUTS_KEY, layouts.value) }
  function saveOrders(): void { persist(PANEL_ORDERS_KEY, orders.value) }

  function desktopFor(panelKey: string): string {
    const stored = desktopAssignments.value[panelKey]
    return desktops.value.some((desktop) => desktop.id === stored) ? stored : desktops.value[0]?.id || 'desktop-1'
  }
  function normalizeMaximizedPanels(): void {
    const occupied = new Set<string>()
    let layoutsChanged = false
    let toolsChanged = false
    for (const [key, layout] of Object.entries(layouts.value)) {
      if (layout.minimized && layout.maximized) {
        layouts.value[key] = { ...layout, maximized: false }
        layoutsChanged = true
        continue
      }
      if (!layout.maximized) continue
      const desktopId = desktopFor(key)
      if (occupied.has(desktopId)) {
        layouts.value[key] = { ...layout, maximized: false }
        layoutsChanged = true
      } else occupied.add(desktopId)
    }
    for (const panel of toolPanels.value) {
      if (panel.minimized && panel.maximized) {
        panel.maximized = false
        toolsChanged = true
        continue
      }
      if (!panel.maximized) continue
      if (occupied.has(panel.desktopId)) {
        panel.maximized = false
        toolsChanged = true
      } else occupied.add(panel.desktopId)
    }
    if (layoutsChanged) saveLayouts()
    if (toolsChanged) saveTools()
  }
  normalizeMaximizedPanels()
  function releaseMaximized(desktopId: string, exceptKey: string): void {
    let toolsChanged = false
    let layoutsChanged = false
    for (const panel of toolPanels.value) {
      if (panel.key !== exceptKey && panel.desktopId === desktopId && panel.maximized) {
        panel.maximized = false
        toolsChanged = true
      }
    }
    for (const [key, layout] of Object.entries(layouts.value)) {
      if (key !== exceptKey && layout.maximized && desktopFor(key) === desktopId) {
        layouts.value[key] = { ...layout, maximized: false }
        layoutsChanged = true
      }
    }
    if (toolsChanged) saveTools()
    if (layoutsChanged) saveLayouts()
  }
  function assign(panelKey: string, desktopId: string): void {
    const tool = toolPanels.value.find((item) => item.key === panelKey)
    if (tool) {
      if (tool.desktopId !== desktopId && tool.maximized) releaseMaximized(desktopId, panelKey)
      tool.desktopId = desktopId
      saveTools()
      return
    }
    if (desktopFor(panelKey) !== desktopId && layouts.value[panelKey]?.maximized) releaseMaximized(desktopId, panelKey)
    desktopAssignments.value[panelKey] = desktopId
    saveAssignments()
  }
  function orderOf(panelKey: string, fallbackIndex: number): number {
    return Number.isFinite(orders.value[panelKey]) ? orders.value[panelKey] : 1000 + Math.max(0, fallbackIndex)
  }
  function placePanel(panelKey: string, desktopId: string, beforeKey: string, targetKeys: string[]): void {
    assign(panelKey, desktopId)
    const keys = targetKeys.filter((key) => key !== panelKey)
    const targetIndex = beforeKey ? keys.indexOf(beforeKey) : -1
    keys.splice(targetIndex >= 0 ? targetIndex : keys.length, 0, panelKey)
    keys.forEach((key, index) => { orders.value[key] = index })
    saveOrders()
  }
  function addDesktop(): void {
    const index = desktops.value.length + 1
    desktops.value.push({ id: `desktop-${Date.now()}-${index}`, name: `桌面 ${index}` })
    page.value = Math.floor((desktops.value.length - 1) / 4)
    saveDesktops()
  }
  function renameDesktop(id: string, name: string): void {
    const desktop = desktops.value.find((item) => item.id === id)
    if (!desktop) return
    desktop.name = name.trim() || `桌面 ${desktops.value.indexOf(desktop) + 1}`
    saveDesktops()
  }
  function removeDesktop(id: string): void {
    if (desktops.value.length <= 1) return
    const replacement = desktops.value.find((item) => item.id !== id)?.id || desktops.value[0].id
    for (const [key, desktopId] of Object.entries(desktopAssignments.value)) {
      if (desktopId === id) desktopAssignments.value[key] = replacement
    }
    for (const panel of toolPanels.value) if (panel.desktopId === id) panel.desktopId = replacement
    desktops.value = desktops.value.filter((item) => item.id !== id)
    page.value = Math.min(page.value, pageCount.value - 1)
    saveDesktops()
    saveAssignments()
    saveTools()
  }
  function addToolPanel(kind: WorkspaceToolPanelState['kind'], desktopId: string, workspaceId = '', conversationId = ''): void {
    const key = `tool:${kind}:${Date.now()}:${toolPanels.value.length + 1}`
    toolPanels.value.push({ key, kind, desktopId, workspaceId, conversationId })
    saveTools()
  }
  function patchToolPanel(key: string, patch: Partial<WorkspaceToolPanelState>): void {
    const panel = toolPanels.value.find((item) => item.key === key)
    if (!panel) return
    const destination = patch.desktopId || panel.desktopId
    if (patch.maximized || (patch.desktopId && patch.desktopId !== panel.desktopId && panel.maximized)) {
      releaseMaximized(destination, key)
    }
    const next = { ...patch }
    if (patch.maximized === true) next.minimized = false
    if (patch.minimized === true) next.maximized = false
    Object.assign(panel, next)
    saveTools()
  }
  function closeToolPanel(key: string): void {
    removePanel(key)
  }
  function removePanel(key: string): void {
    toolPanels.value = toolPanels.value.filter((item) => item.key !== key)
    delete desktopAssignments.value[key]
    delete hiddenPanels.value[key]
    delete layouts.value[key]
    delete orders.value[key]
    saveTools()
    saveAssignments()
    saveHidden()
    saveLayouts()
    saveOrders()
  }
  function toggleHidden(key: string): void {
    const tool = toolPanels.value.find((item) => item.key === key)
    if (tool) {
      tool.hidden = !tool.hidden
      saveTools()
    } else {
      hiddenPanels.value[key] = !hiddenPanels.value[key]
      saveHidden()
    }
  }
  function hide(key: string): void {
    hiddenPanels.value[key] = true
    saveHidden()
  }
  function updateLayout(key: string, layout: { minimized: boolean; maximized: boolean }): void {
    const next = { ...layout, maximized: layout.minimized ? false : layout.maximized }
    if (next.maximized) releaseMaximized(desktopFor(key), key)
    layouts.value[key] = next
    saveLayouts()
  }

  return {
    desktops, desktopAssignments, hiddenPanels, toolPanels, layouts,
    orders, page, pageCount, visibleDesktops,
    desktopFor, assign, orderOf, placePanel, addDesktop, renameDesktop, removeDesktop,
    addToolPanel, patchToolPanel, closeToolPanel, removePanel, toggleHidden, hide, updateLayout,
  }
})

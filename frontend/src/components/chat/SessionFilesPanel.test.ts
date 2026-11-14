import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import SessionFilesPanel from './SessionFilesPanel.vue'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  apiUrl: (path: string) => `/api/agent-zero${path}`,
}))

const apiMock = vi.mocked(api)

function mountPanel(workspaceId = 'ws-a') {
  return mount(SessionFilesPanel, {
    props: {
      open: true,
      workspaceId,
      workspaceName: `Workspace ${workspaceId}`,
    },
    global: {
      plugins: [createPinia()],
      stubs: { teleport: true },
    },
  })
}

describe('SessionFilesPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    apiMock.mockReset()
    apiMock.mockImplementation(async (path) => {
      if (path.startsWith('/workspace/list')) {
        if (path.includes('path=src')) {
          return [{ name: 'main.py', path: 'src/main.py', type: 'file', size: 12 }] as never
        }
        return [
          { name: 'src', path: 'src', type: 'dir', size: 0 },
          { name: 'README.md', path: 'README.md', type: 'file', size: 5 },
          { name: '.env', path: '.env', type: 'file', size: 16 },
        ] as never
      }
      if (path.startsWith('/workspace/file/preview')) {
        return { path: 'README.md', kind: 'markdown', text: 'hello', size: 5 } as never
      }
      return null as never
    })
  })

  it('browses folders, previews files, and hides dotfiles by default', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('README.md')
    expect(wrapper.text()).not.toContain('.env')
    await wrapper.get('.session-files-entry.directory').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith(expect.stringContaining('path=src'), expect.any(Object))
    expect(wrapper.text()).toContain('main.py')

    await wrapper.get('.mini-link').trigger('click') // return to root from src
    await flushPromises()
    await wrapper.get('.session-files-entry:not(.directory)').trigger('click')
    await flushPromises()
    expect(wrapper.find('.workspace-file-text').text()).toBe('hello')
    await wrapper.get('button[title="将文件路径插入消息输入框"]').trigger('click')
    expect(wrapper.emitted('reference')?.[0]).toEqual(['README.md'])
  })

  it('reloads against the new workspace when the session workspace changes', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.setProps({ workspaceId: 'ws-b' })
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith(expect.stringContaining('workspace_id=ws-b'), expect.any(Object))
  })
})

import { createApp, defineComponent, h, ref } from 'vue'
import { createPinia } from 'pinia'
import SessionFilesPanel from '@/components/chat/SessionFilesPanel.vue'
import WorkspaceChangesPanel from '@/components/chat/WorkspaceChangesPanel.vue'
import MessageItem from '@/components/chat/MessageItem.vue'
import { continuationPrompt } from '@/lib/continuationPrompt'

const prompt = ref('')

const Harness = defineComponent({
  name: 'WorkspaceFlowHarness',
  setup() {
    return () => h('main', { style: 'padding:16px;min-height:100vh' }, [
      h('label', { for: 'e2e-prompt' }, '消息输入'),
      h('input', {
        id: 'e2e-prompt',
        'data-testid': 'prompt',
        value: prompt.value,
        onInput: (event: Event) => { prompt.value = (event.target as HTMLInputElement).value },
      }),
      h(MessageItem, {
        message: {
          id: 'm-interrupted-e2e',
          by: 'assistant',
          interrupted: true,
          content: '已完成部分工作',
        },
        onContinueMessage: () => {
          prompt.value = prompt.value.trim()
            ? `${prompt.value.trim()}\n\n${continuationPrompt({ interrupted: true })}`
            : continuationPrompt({ interrupted: true })
          document.querySelector<HTMLInputElement>('[data-testid="prompt"]')?.focus()
        },
      }),
      h(SessionFilesPanel, {
        open: true,
        workspaceId: 'ws-e2e',
        workspaceName: 'E2E Workspace',
        onReference: (path: string) => { prompt.value = `${prompt.value} @${path}`.trim() },
      }),
      h(WorkspaceChangesPanel, {
        open: true,
        conversationId: 'conv-e2e',
        onReference: (path: string) => { prompt.value = `${prompt.value} @${path}`.trim() },
      }),
    ])
  },
})

createApp(Harness).use(createPinia()).mount('#app')
window.__e2eReady = true

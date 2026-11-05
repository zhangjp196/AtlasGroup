import type { SlashCommand } from '@/api/types'

/** 前端直接执行的斜杠命令；与后端 /app/commands 返回的内置命令合并展示。 */
export const ACTION_COMMANDS: SlashCommand[] = [
  { name: 'new', description: '新建一个会话', builtin: true, source: 'action' },
  { name: 'compact', description: '压缩当前会话上下文', builtin: true, source: 'action' },
  { name: 'clear', description: '清空模型上下文（历史消息保留）', argument_hint: '[all|current]', builtin: true, source: 'action' },
  { name: 'work', description: '交给工单', argument_hint: '[标题]', builtin: true, source: 'action' },
  { name: 'stop', description: '停止当前运行', builtin: true, source: 'action' },
  { name: 'attach', description: '添加附件', builtin: true, source: 'action' },
  { name: 'model', description: '聚焦模型选择器', builtin: true, source: 'action' },
  { name: 'rename', description: '重命名当前会话', argument_hint: '[新标题]', builtin: true, source: 'action' },
  { name: 'delete', description: '删除当前会话', builtin: true, source: 'action' },
  { name: 'conv', description: '返回会话列表', builtin: true, source: 'action' },
  { name: 'settings', description: '打开设置', builtin: true, source: 'action' },
  { name: 'about', description: '打开关于', builtin: true, source: 'action' },
  { name: 'theme', description: '切换明暗主题', builtin: true, source: 'action' },
]

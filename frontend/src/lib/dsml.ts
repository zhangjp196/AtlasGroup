// DeepSeek 兼容端点偶尔把 DSML 工具调用当正文存进历史。
// 新回复已由后端还原成工具调用；这里只负责让旧消息不再显示标记。
const BAR = '[|｜]'
const TOKEN = `(?:${BAR}\\s*){1,2}DSML(?:\\s*${BAR}){1,2}`
const BLOCK = new RegExp(
  `<\\s*${TOKEN}\\s*[^>]*>[\\s\\S]*?<\\s*/\\s*${TOKEN}\\s*[^>]*>`,
  'gi',
)
const STRAY = new RegExp(`<\\s*/?\\s*${TOKEN}\\s*[^>]*>`, 'gi')

export function stripDsml(text: string): string {
  if (!text || !/DSML/i.test(text)) return text
  return text
    .replace(BLOCK, '')
    .replace(STRAY, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

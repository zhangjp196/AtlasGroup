export function continuationPrompt(status: { interrupted?: boolean; error?: string }): string {
  const reason = status.interrupted ? '上一条回复被中断' : '上一条回复执行失败'
  return `${reason}了。请先检查当前工作区和已有改动，确认已经完成的部分，再从未完成处继续；避免重复执行有副作用的操作。完成后简要说明结果。`
}

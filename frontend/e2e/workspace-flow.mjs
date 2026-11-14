import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

const frontendRoot = resolve(import.meta.dirname, '..')
const port = Number(process.env.E2E_VITE_PORT || 4179)
const origin = `http://127.0.0.1:${port}`
const chromePath = process.env.CHROME_PATH || findChrome()

function findChrome() {
  const candidates = process.platform === 'darwin'
    ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    : process.platform === 'win32'
      ? [join(process.env.PROGRAMFILES || 'C:\\Program Files', 'Google/Chrome/Application/chrome.exe')]
      : ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser']
  return candidates.find((candidate) => {
    try { return requireAccess(candidate) } catch { return false }
  }) || ''
}

function requireAccess(path) {
  return Boolean(process.getBuiltinModule('node:fs').existsSync(path))
}

async function waitFor(check, label, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      const value = await check()
      if (value) return value
    } catch (error) {
      lastError = error
    }
    await delay(100)
  }
  throw new Error(`Timed out waiting for ${label}${lastError ? `: ${lastError.message}` : ''}`)
}

async function main() {
  assert.ok(chromePath, 'Chrome not found; set CHROME_PATH to a Chrome/Chromium executable')
  console.log('Starting temporary Vite server and headless Chrome…')
  const profile = await mkdtemp(join(tmpdir(), 'agentzero-e2e-chrome-'))
  const vite = spawn(process.execPath, [join(frontendRoot, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: frontendRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const chrome = spawn(chromePath, [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
  ], { stdio: 'ignore' })

  let viteOutput = ''
  const viteStopped = new Promise((resolve) => vite.once('exit', resolve))
  const chromeStopped = new Promise((resolve) => chrome.once('exit', resolve))
  vite.stdout.on('data', (chunk) => { viteOutput += chunk.toString() })
  vite.stderr.on('data', (chunk) => { viteOutput += chunk.toString() })

  try {
    await waitFor(async () => {
      if (vite.exitCode !== null) throw new Error(`Vite exited (${vite.exitCode}): ${viteOutput}`)
      const response = await fetch(`${origin}/e2e/workspace-flow.html`, { signal: AbortSignal.timeout(1000) })
      return response.ok
    }, 'Vite test page')
    console.log('Vite test page is ready')

    const activePortFile = join(profile, 'DevToolsActivePort')
    const activePort = await waitFor(async () => {
      if (chrome.exitCode !== null) throw new Error(`Chrome exited (${chrome.exitCode})`)
      try { return Number((await readFile(activePortFile, 'utf8')).split('\n')[0]) } catch { return 0 }
    }, 'Chrome DevTools endpoint')
    console.log('Chrome DevTools endpoint is ready')

    const targets = await fetch(`http://127.0.0.1:${activePort}/json/list`, {
      signal: AbortSignal.timeout(3000),
    }).then((response) => response.json())
    const target = targets.find((item) => item.type === 'page')
    assert.ok(target?.webSocketDebuggerUrl, 'Chrome page target not found')
    console.log('Chrome page target is ready')
    const cdp = new CdpClient(target.webSocketDebuggerUrl)
    await cdp.connect()
    console.log('Connected to Chrome DevTools Protocol')
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Page.navigate', { url: `${origin}/e2e/workspace-flow.html` })
    console.log('Navigated to the test harness')

    await waitFor(() => cdp.evaluate('window.__e2eReady === true'), 'Vue harness mount')
    console.log('Vue test harness is mounted')
    await waitFor(() => cdp.evaluate("document.body.innerText.includes('README.md')"), 'workspace file list')

    await cdp.evaluate("[...document.querySelectorAll('.session-files-entry')].find((el) => el.textContent.includes('src'))?.click()")
    await waitFor(() => cdp.evaluate("[...document.querySelectorAll('.session-files-entry')].some((el) => el.textContent.includes('main.py'))"), 'nested workspace folder')
    await cdp.evaluate("[...document.querySelectorAll('.session-files-entry')].find((el) => el.textContent.includes('main.py'))?.click()")
    await waitFor(() => cdp.evaluate("document.querySelector('.workspace-file-text')?.textContent.includes('nested workspace')"), 'nested file preview')
    await cdp.evaluate("document.querySelector('.session-files-preview-head button[title=\"将文件路径插入消息输入框\"]')?.click()")
    assert.match(await cdp.evaluate("document.querySelector('[data-testid=prompt]')?.value"), /@src\/main\.py/, 'nested file reference should preserve its relative path')
    await cdp.evaluate("const input = document.querySelector('[data-testid=prompt]'); input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true }))")

    await cdp.evaluate("[...document.querySelectorAll('button')].find((el) => el.textContent.trim() === '根目录')?.click()")
    await waitFor(() => cdp.evaluate("[...document.querySelectorAll('.session-files-entry')].some((el) => el.textContent.includes('README.md'))"), 'workspace root navigation')

    await cdp.evaluate("[...document.querySelectorAll('.session-files-entry')].find((el) => el.textContent.includes('README.md'))?.click()")
    await waitFor(() => cdp.evaluate("document.querySelector('.workspace-file-text')?.textContent.includes('hello workspace')"), 'file preview')
    await cdp.evaluate("document.querySelector('.session-files-preview-head button[title=\"将文件路径插入消息输入框\"]')?.click()")
    const promptValue = await cdp.evaluate("document.querySelector('[data-testid=prompt]')?.value")
    assert.match(promptValue, /@README\.md/, 'file reference should enter the prompt')
    console.log('Workspace navigation, preview, and reference passed')

    await cdp.evaluate("[...document.querySelectorAll('.msg-op')].find((el) => el.textContent.trim() === '放入续办提示')?.click()")
    await waitFor(() => cdp.evaluate("document.querySelector('[data-testid=prompt]')?.value.includes('上一条回复被中断')"), 'interrupted reply continuation prompt')
    const continuedPrompt = await cdp.evaluate("document.querySelector('[data-testid=prompt]')?.value")
    assert.match(continuedPrompt, /@README\.md/, 'continuation should preserve the existing draft')
    assert.match(continuedPrompt, /避免重复执行有副作用的操作/, 'continuation should request safe resumption')
    assert.equal(await cdp.evaluate("document.activeElement?.dataset.testid"), 'prompt', 'continuation should focus the composer')
    console.log('Interrupted reply continuation passed')

    await waitFor(() => cdp.evaluate("document.body.innerText.includes('src/main.py')"), 'workspace change list')
    await cdp.evaluate("window.confirm = () => true; [...document.querySelectorAll('.wc-file')].find((el) => el.textContent.includes('src/main.py'))?.querySelector('button[title=\"恢复此文件到检查点状态\"]')?.click()")
    await waitFor(() => cdp.evaluate("window.__e2e.restoreRequest?.paths?.[0] === 'src/main.py'"), 'selective file restore request')
    const restored = await cdp.evaluate('window.__e2e.restoreRequest')
    assert.deepEqual(restored, {
      files: true,
      conversation: false,
      delete_added: true,
      paths: ['src/main.py'],
    })
    await waitFor(() => cdp.evaluate("document.body.innerText.includes('最近一次运行后没有文件变化')"), 'updated changes panel')

    console.log('PASS workspace navigation, preview, reference, interrupted reply continuation, and selective restore')
    await cdp.close()
  } finally {
    vite.kill('SIGTERM')
    chrome.kill('SIGTERM')
    await Promise.race([Promise.all([viteStopped, chromeStopped]), delay(3000)])
    await rm(profile, { recursive: true, force: true })
  }
}

class CdpClient {
  constructor(url) {
    this.url = url
    this.socket = null
    this.nextId = 0
    this.pending = new Map()
  }

  async connect() {
    this.socket = new WebSocket(this.url)
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(String(event.data))
      if (!message.id) return
      const pending = this.pending.get(message.id)
      if (!pending) return
      this.pending.delete(message.id)
      if (message.error) pending.reject(new Error(message.error.message))
      else pending.resolve(message.result)
    })
    await Promise.race([new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true })
      this.socket.addEventListener('error', reject, { once: true })
    }), delay(5000).then(() => { throw new Error('Timed out connecting to Chrome DevTools Protocol') })])
  }

  send(method, params = {}) {
    const id = ++this.nextId
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.socket.send(JSON.stringify({ id, method, params }))
      delay(5000).then(() => {
        if (this.pending.delete(id)) reject(new Error(`Timed out waiting for Chrome DevTools method ${method}`))
      })
    })
  }

  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    })
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || 'Browser JavaScript evaluation failed')
    }
    return result.result?.value
  }

  async close() {
    this.socket?.close()
  }
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})

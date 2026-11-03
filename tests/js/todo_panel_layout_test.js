/**
 * 任务清单面板 / 进程面板真实布局回归（headless Chrome，零依赖）。
 *
 * 用户要求（2026-11-02）：「任务面板 调整到进程下方，目前也没有入口」。
 *
 * 这里把 web/index.html 里的顶栏动作区和对话区浮动面板列原样抠出来、套真实
 * app.css 渲染后量尺寸，验证：
 *
 *   1. 任务面板在进程面板**正下方**（上边界 ≥ 进程面板下边界），左右边界对齐；
 *   2. 进程面板隐藏时，任务面板仍贴着左上角（不因空槽位下沉）；
 *   3. 两块面板都不横向超出对话区、不压到底部输入区；
 *   4. 收起后是小浮标（宽度 / 高度都明显变小，不撑满整列）；
 *   5. 顶栏 #topTodoBtn 可见、带未完成数徽标 #topTodoCount，徽标不出界；
 *   6. 浮动列空白处不拦鼠标（pointer-events: none）。
 *
 * 没有 Chrome / Chromium 时跳过（exit 0），不因环境差异让 CI 挂掉。
 *
 * 运行：node tests/js/todo_panel_layout_test.js
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '../..');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');

const CHROME_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
];
const CHROME = CHROME_CANDIDATES.find((p) => {
  try { return fs.statSync(p).isFile(); } catch { return false; }
});
if (!CHROME) {
  console.log('跳过：未找到 Chrome / Chromium，布局实测需要真实渲染引擎');
  process.exit(0);
}

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  OK   ${name}`);
  else { failures += 1; console.log(`  FAIL ${name}${extra !== undefined ? ` — ${extra}` : ''}`); }
}

// ── 从 index.html 抠出真实结构 ─────────────────────────────────────────
const floatsFrom = HTML.indexOf('<div class="chat-floats" id="chatFloats">');
const columnAt = HTML.indexOf('<div class="chat-column">', floatsFrom);
const floatsTo = HTML.lastIndexOf('</div>', columnAt);   // 浮动列容器的收尾
if (floatsFrom < 0 || columnAt < 0 || floatsTo <= floatsFrom) {
  throw new Error('未找到对话区浮动面板列结构');
}
const floats = HTML.slice(floatsFrom, floatsTo + '</div>'.length);

const actionsFrom = HTML.indexOf('<div class="topbar-actions">');
const actionsTo = HTML.indexOf('</div>', HTML.indexOf('id="deleteChatBtn"', actionsFrom));
if (actionsFrom < 0 || actionsTo <= actionsFrom) throw new Error('未找到顶栏动作区');
const actions = HTML.slice(actionsFrom, actionsTo + '</div>'.length);

const PROCESS_ROWS = 2;
const page = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<link rel="stylesheet" href="app.css">
<style>
 body { margin: 0; height: 100vh; }
 /* 只保留布局相关的骨架：顶栏 + 对话区 + 底部输入区高度占位。
    .app 在生产里是「侧栏 | 对话区 | dock」三列 grid，这里省掉侧栏与 dock，
    直接让对话区铺满视口，量出来的面板坐标才是相对对话区的。 */
 .app { display: block; height: 100vh; }
 .main { display: flex; flex-direction: column; height: 100vh; }
 .chat-shell { flex: 1; }
 .composer-area { flex: none; height: 120px; }
</style></head><body>
<div class="app" data-dock="closed" data-sidebar-collapsed="1" style="--sidebar-w: 0px;">
  <main class="main">
    <header class="topbar">${actions}</header>
    <div class="chat-shell" id="chatShell">
      ${floats}
      <div class="chat-column">
        <div class="chat-scroll" id="chatScroll"></div>
      </div>
    </div>
    <div class="composer-area" id="composerArea"></div>
  </main>
</div>
<script>
(function () {
  // 进程面板：塞两条正在跑的进程（模拟真实渲染高度）
  var process = document.getElementById('processPanel');
  process.hidden = false;
  document.getElementById('processList').innerHTML = Array.from({ length: ${PROCESS_ROWS} },
    function (_, i) {
      return '<div class="process-row"><span class="process-dot"></span>' +
        '<span class="process-icon"></span>' +
        '<button type="button" class="process-main">' +
        '<span class="process-name">shell · npm test -- ' + i + '</span>' +
        '<span class="process-meta">工具 · 12s</span></button></div>';
    }).join('');
  document.getElementById('processCount').textContent = '${PROCESS_ROWS}';

  // 任务面板：默认打开，放三条任务
  var todo = document.getElementById('todoPanel');
  todo.hidden = false;
  document.getElementById('todoSummary').textContent = '1/3 已完成';
  document.getElementById('todoList').innerHTML = ['a', 'b', 'c'].map(function (k, i) {
    return '<div class="todo-item" data-status="' + (i ? 'open' : 'done') + '">' +
      '<button type="button" class="todo-check"></button>' +
      '<div class="todo-main"><span class="todo-title">任务 ' + k + '</span></div>' +
      '<button type="button" class="todo-status">待办</button>' +
      '<button type="button" class="icon-btn todo-del"></button></div>';
  }).join('');
  document.getElementById('todoFoot').textContent = '2 待办 · 1 完成';
  var badge = document.getElementById('topTodoCount');
  badge.hidden = false;
  badge.textContent = '2';

  var r = function (el) { return el.getBoundingClientRect(); };
  var round = function (o) { return { left: Math.round(o.left), top: Math.round(o.top),
    right: Math.round(o.right), bottom: Math.round(o.bottom),
    width: Math.round(o.width), height: Math.round(o.height) }; };
  var btn = document.getElementById('topTodoBtn');
  var floatsEl = document.getElementById('chatFloats');

  var out = {
    floatsPointerEvents: getComputedStyle(floatsEl).pointerEvents,
    shell: round(r(document.getElementById('chatShell'))),
    floats: round(r(floatsEl)),
    process: round(r(process)),
    todo: round(r(todo)),
    composer: round(r(document.getElementById('composerArea'))),
    btn: { hidden: btn.hidden, width: Math.round(r(btn).width),
           right: Math.round(r(btn).right), top: Math.round(r(btn).top) },
    badge: { hidden: badge.hidden, ...round(r(badge)) },
    pageOverflowX: document.documentElement.scrollWidth >
      document.documentElement.clientWidth + 1,
  };

  // 收起后的尺寸：模拟点 chevron 的效果（app.js 里是加 is-collapsed + 重渲染）
  todo.classList.add('is-collapsed');
  out.todoCollapsed = round(r(todo));
  todo.classList.remove('is-collapsed');

  // 进程面板隐藏时，任务面板不应下沉
  process.hidden = true;
  out.todoWithoutProcess = round(r(todo));
  process.hidden = false;

  var pre = document.createElement('pre');
  pre.id = 'metrics';
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
</script>
</body></html>`;

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-todolayout-'));
fs.writeFileSync(path.join(dir, 'harness.html'), page);
fs.writeFileSync(path.join(dir, 'app.css'), CSS);

function measure(size) {
  const dom = execFileSync(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--force-device-scale-factor=1', `--window-size=${size}`,
    '--virtual-time-budget=4000', '--dump-dom',
    `file://${path.join(dir, 'harness.html')}`,
  ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  const m = dom.match(/<pre id="metrics">([\s\S]*?)<\/pre>/);
  if (!m) throw new Error(`未拿到 metrics，DOM 前 400 字：\n${dom.slice(0, 400)}`);
  return JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>'));
}

try {
  console.log('浮动面板列真实布局（宽屏 1400）');
  const wide = measure('1400,900');

  check('任务面板在进程面板下方',
    wide.todo.top >= wide.process.bottom,
    `process.bottom=${wide.process.bottom} todo.top=${wide.todo.top}`);
  check('两块面板上下留出间距（不粘连）',
    wide.todo.top - wide.process.bottom >= 4,
    wide.todo.top - wide.process.bottom);
  check('两块面板左边界对齐',
    Math.abs(wide.todo.left - wide.process.left) <= 1,
    `${wide.process.left} vs ${wide.todo.left}`);
  check('两块面板宽度一致',
    Math.abs(wide.todo.width - wide.process.width) <= 1,
    `${wide.process.width} vs ${wide.todo.width}`);
  check('面板列贴在对话区左上角',
    wide.process.left - wide.shell.left <= 16 && wide.process.top - wide.shell.top <= 16,
    JSON.stringify({ shell: wide.shell, process: wide.process }));
  check('面板不横向超出对话区',
    wide.todo.right <= wide.shell.right && wide.process.right <= wide.shell.right,
    JSON.stringify({ shellRight: wide.shell.right, todoRight: wide.todo.right }));
  check('面板不压到底部输入区',
    wide.todo.bottom <= wide.composer.top,
    `todo.bottom=${wide.todo.bottom} composer.top=${wide.composer.top}`);
  check('浮动列空白处点击穿透（pointer-events: none）',
    wide.floatsPointerEvents === 'none', wide.floatsPointerEvents);

  check('进程面板隐藏时任务面板不下沉（仍在左上角）',
    wide.todoWithoutProcess.top - wide.shell.top <= 16,
    JSON.stringify(wide.todoWithoutProcess));

  check('收起后宽度明显变小（小浮标而不是空列）',
    wide.todoCollapsed.width < wide.todo.width * 0.75,
    `${wide.todoCollapsed.width} vs ${wide.todo.width}`);
  check('收起后高度也变小',
    wide.todoCollapsed.height < wide.todo.height,
    `${wide.todoCollapsed.height} vs ${wide.todo.height}`);

  check('顶栏任务清单入口可见', wide.btn.hidden === false);
  check('顶栏入口尺寸正常（30px 图标按钮）',
    wide.btn.width >= 24 && wide.btn.width <= 36, wide.btn.width);
  check('顶栏入口徽标可见', wide.badge.hidden === false);
  check('徽标没有超出按钮（扣在图标右上角）',
    wide.badge.right <= wide.btn.right + 1 && wide.badge.top >= wide.btn.top - 1,
    JSON.stringify({ badge: wide.badge, btn: wide.btn }));
  check('无页面级横向溢出', wide.pageOverflowX === false);

  console.log('浮动面板列真实布局（窄屏 420）');
  const narrow = measure('420,760');
  check('窄屏任务面板仍在进程面板下方', narrow.todo.top >= narrow.process.bottom,
    `${narrow.process.bottom} vs ${narrow.todo.top}`);
  check('窄屏面板不超出对话区宽度', narrow.todo.right <= narrow.shell.right,
    `${narrow.todo.right} vs ${narrow.shell.right}`);
  check('窄屏无页面级横向溢出', narrow.pageOverflowX === false);
} catch (err) {
  failures += 1;
  console.log(`  FAIL 渲染实测异常：${err.message}`);
}

console.log(failures === 0
  ? '\n[todo-panel-layout] 全部通过'
  : `\n[todo-panel-layout] ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);

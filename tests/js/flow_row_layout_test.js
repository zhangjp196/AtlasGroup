/**
 * 「一行式输出」真实浏览器布局回归（headless Chrome，零依赖）。
 *
 * 静态断言只能证明 HTML 结构对了，证明不了「真的只占一行」。这里用真实渲染引擎量：
 *   - 折叠后的每条工具 / 思考行高度 ≈ 一行（不能换行成两行、更不能再摊开）
 *   - 长路径在固定宽度下被省略（scrollWidth > clientWidth），并保留文件名
 *   - 各行的图标左边缘对齐（去掉思考块竖线后整列齐平）
 *   - 展开的思考块显示全文，但标题行仍是单行
 *
 * 没有 Chrome / Chromium 时跳过（exit 0）。
 * 运行：node tests/js/flow_row_layout_test.js
 */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');

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
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra !== undefined ? `\n       ${extra}` : ''}`);
  }
}

function slice(startMark, endMark) {
  const i = SRC.indexOf(startMark);
  const j = SRC.indexOf(endMark);
  if (i < 0 || j < 0 || j <= i) throw new Error(`未能定位 ${startMark} → ${endMark}`);
  return SRC.slice(i, j);
}

// ── 1. 用真实 renderStep 产出几行（与 flow_row_test 同一套桩） ───────────
const esc = (s) => String(s == null ? '' : s)
  .replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const ctx = {
  t: (k) => k,
  escapeHtml: esc,
  escAttr: (v) => esc(v).replace(/'/g, '&#39;'),
  renderStepCaret: () => '<span class="step-caret" aria-hidden="true"><svg><use href="#i-chevron-right"/></svg></span>',
  renderPlan: () => '',
  renderProposal: () => '',
  renderApproval: () => '',
  renderCompression: () => '',
  diffLineHtml: (l) => esc(l),
  diffHeadHtml: () => '',
  pvBlock: () => '',
  detailOpen: () => false,
  duration: (ms) => `${ms}ms`,
  PREVIEW_EXT_RE: /\.(html)$/,
};
const names = Object.keys(ctx);
const code = slice('  const TOOL_LABELS = {', '  function toolLabel(')
  + slice('  function toolLabel(', '  function stepTime(')
  + slice('  function renderStep(', '  // 时间线逐块输出');
const api = new Function(
  ...names,
  `${code}; return { renderStep, toolLabel, toolIcon };`,
)(...names.map((k) => ctx[k]));

const LONG = 'macos/Sources/SplashApp/Server/Settings/Advanced/ServerSettings.swift';
const steps = [
  { kind: 'tool', name: 'edit_file', status: 'done', duration_ms: 180,
    args: { path: LONG, old_string: 'a', new_string: 'b' },
    display: { type: 'diff', path: LONG, added: 6, removed: 4, diff: '-a\n+b' } },
  { kind: 'tool', name: 'shell', status: 'done', duration_ms: 120,
    args: { cmd: 'cd /Users/zhangjp && grep -rn "stale|14 day|fourteen|nobody opened this app" .' } },
  { kind: 'thinking', text: 'Now disk eviction strengthening. Let us think of a concrete change: in cleanup, drop caches untouched for 14 days.\nSecond line of the same thought.' },
  { kind: 'tool', name: 'read_file', status: 'done', duration_ms: 12,
    display: { type: 'file', path: 'services/agent_zero/runtime/zero/file_ops.py', lineStart: 420, lineEnd: 462, totalLines: 462, text: 'x' } },
  // 超长无空格串（真实场景里是一条几百字符的 find / grep）：CSS 必须兜住，不能顶宽
  { kind: 'tool', name: 'shell', status: 'done', duration_ms: 30,
    args: { cmd: `/usr/bin/find ${'/very-long-unbroken-segment'.repeat(12)}` } },
];

const rowsHtml = steps.map((s) => `<div class="flow-block ${s.kind} foldable is-collapsed" data-fold-key="k">
  ${api.renderStep(s, { id: 'm1' })}</div>`).join('');

const THINK_OPEN = `<div class="flow-block thinking foldable">
  ${api.renderStep(steps[2], { id: 'm1' })}</div>`;

const page = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<link rel="stylesheet" href="app.css">
<style>
  body { margin: 0; background: var(--bg, #101014); }
  .msg.assistant { margin: 14px 0 30px; }
  .chat-scroll { width: 760px; padding: 0 18px; }
</style></head><body>
<svg class="sprite" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" style="display:none">
  <symbol id="i-chevron-right" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></symbol>
  <symbol id="i-brain" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7"/></symbol>
  <symbol id="i-terminal" viewBox="0 0 24 24"><path d="m5 7 4 5-4 5"/></symbol>
  <symbol id="i-file" viewBox="0 0 24 24"><path d="M14 3H7v18h10V7Z"/></symbol>
  <symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 4 2.6 20h18.8Z"/></symbol>
</svg>
<div class="chat-scroll app" id="app">
  <article class="msg assistant"><div class="assistant-body">
    <div class="turn-flow" data-flow id="flow">${rowsHtml}</div>
    <div class="turn-flow" id="flowOpen">${THINK_OPEN}</div>
  </div></article>
</div>
<script>
(function () {
  requestAnimationFrame(function () {
    var rows = Array.prototype.map.call(document.querySelectorAll('#flow > .flow-block'), function (b) {
      var title = b.querySelector('.step-title');
      var subj = b.querySelector('.step-subject');
      return {
        h: Math.round(b.getBoundingClientRect().height * 10) / 10,
        titleH: title ? Math.round(title.getBoundingClientRect().height * 10) / 10 : 0,
        titleWrapped: title ? title.scrollHeight > title.clientHeight + 1 : false,
        iconLeft: Math.round(b.querySelector('.step-icon').getBoundingClientRect().left),
        subjectClipped: subj ? subj.scrollWidth > subj.clientWidth + 1 : false,
        subjectText: subj ? subj.textContent : '',
        subjectEllipsis: subj ? getComputedStyle(subj).textOverflow : '',
        subjectTitle: subj ? subj.getAttribute('title') : '',
        caretTransform: b.querySelector('.step-caret')
          ? getComputedStyle(b.querySelector('.step-caret')).transform : ''
      };
    });
    var open = document.querySelector('#flowOpen .flow-block.foldable');
    var openTitle = open ? open.querySelector('.step-title') : null;
    var out = {
      rows: rows,
      pageOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      openHeight: open ? Math.round(open.getBoundingClientRect().height) : 0,
      openTitleH: openTitle ? Math.round(openTitle.getBoundingClientRect().height) : 0
    };
    var pre = document.createElement('pre');
    pre.id = 'metrics';
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  });
})();
</script>
</body></html>`;

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-flow-row-'));
fs.writeFileSync(path.join(dir, 'harness.html'), page);
fs.writeFileSync(path.join(dir, 'app.css'), CSS);

// headless Chrome 偶尔还没跑完 rAF 就被 --dump-dom 抓走（实测 3 次里挂 1 次），
// 重试几次；连续失败才算环境问题。
function measureOnce() {
  const dom = execFileSync(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--force-device-scale-factor=1', '--window-size=900,1200',
    '--virtual-time-budget=6000', '--dump-dom',
    `file://${path.join(dir, 'harness.html')}`,
  ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  const m = dom.match(/<pre id="metrics">([\s\S]*?)<\/pre>/);
  if (!m) throw new Error(`未拿到 metrics，DOM 前 400 字：\n${dom.slice(0, 400)}`);
  return JSON.parse(m[1]);
}

function measure() {
  let last;
  for (let i = 0; i < 4; i += 1) {
    try { return measureOnce(); } catch (err) { last = err; }
  }
  throw last;
}

try {
  const r = measure();
  console.log('一行式输出的真实布局');
  check('渲染出 5 行', r.rows.length === 5, r.rows.length);
  check('每行高度都 ≈ 一行（≤ 28px）',
    r.rows.every((x) => x.h <= 28), JSON.stringify(r.rows.map((x) => x.h)));
  check('标题行自身不换行',
    r.rows.every((x) => !x.titleWrapped), JSON.stringify(r.rows.map((x) => x.titleWrapped)));
  check('各行图标左边缘对齐',
    new Set(r.rows.map((x) => x.iconLeft)).size === 1, JSON.stringify(r.rows.map((x) => x.iconLeft)));
  check('长路径被中段省略（保留首段 + 文件名）',
    r.rows[0].subjectText.includes('…') && r.rows[0].subjectText.endsWith('ServerSettings.swift'),
    r.rows[0].subjectText);
  check('省略后不再包含完整路径', !r.rows[0].subjectText.includes('/Settings/Advanced/'),
    r.rows[0].subjectText);
  check('省略用 ellipsis（更长的路径由 CSS 兜底）', r.rows[0].subjectEllipsis === 'ellipsis', r.rows[0].subjectEllipsis);
  check('悬停标题是完整路径',
    r.rows[0].subjectTitle.includes('/Settings/Advanced/'), r.rows[0].subjectTitle);
  check('shell 行显示命令开头',
    r.rows[1].subjectText.startsWith('cd /Users/zhangjp'), r.rows[1].subjectText);
  check('思考行折叠时只有一行', r.rows[2].h <= 28, r.rows[2].h);
  check('折叠态 caret 未旋转（▶）',
    r.rows[0].caretTransform === 'none', r.rows[0].caretTransform);
  check('超长路径由 CSS 兜底裁切（scrollWidth > clientWidth）',
    r.rows[4].subjectClipped, JSON.stringify(r.rows[4]));
  check('无页面级横向溢出', r.pageOverflowX === false);
  check('展开的思考块显示全文（明显高于一行）', r.openHeight > 40, r.openHeight);
  check('展开的思考标题仍是单行（摘要不换行）',
    r.openTitleH > 0 && r.openTitleH <= 26, r.openTitleH);
} finally {
  fs.rmSync(dir, { recursive: true, force: true });
}

if (failures) {
  console.error(`\n[flow-row-layout] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[flow-row-layout] 全部通过');

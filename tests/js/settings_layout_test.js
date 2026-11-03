/**
 * 设置弹框 / 用量面板的「真实浏览器」布局回归测试（headless Chrome，零依赖）。
 *
 * 做法：把 web/index.html 里的设置弹框结构原样抠出来，套真实 app.css 渲染，
 * 再把真实 renderUsageStats 的产物灌进 #usageStatsBody，然后量尺寸断言：
 *
 *   - 左栏 .settings-tabs 与右栏 .settings-panels 各自滚动，互不带动，外框不滚
 *   - 用量面板收窄：卡片固定 4 列、内容区 ≤600px、表格与页面都不横向溢出
 *   - 卡片数值是 k / 万 / 亿 的紧凑写法，且文字不被裁切
 *   - 窄屏（<720px）退化为上下叠放 + 2 列卡片，照样不溢出
 *
 * 没有 Chrome / Chromium 时跳过（exit 0），不因环境差异让 CI 挂掉。
 *
 * 运行：node tests/js/settings_layout_test.js
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const FIXTURE = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/usage_stats.json'), 'utf8'));

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

function slice(startMark, endMark) {
  const i = SRC.indexOf(startMark);
  const j = SRC.indexOf(endMark);
  if (i < 0 || j < 0 || j <= i) throw new Error(`未能定位 ${startMark} → ${endMark}`);
  return SRC.slice(i, j);
}

// ── 1. 离线跑真实 renderUsageStats，拿到用量面板 HTML ────────────────────
const code = slice('  function compact(', '  function toDate(')
  + slice('  function usageCardHtml(', '  async function loadUsageStats(');
const esc = (s) => String(s == null ? '' : s)
  .replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ctx = {
  el: { innerHTML: '' },
  state: { usagePeriod: '7d', usageStats: FIXTURE },
  escapeHtml: esc,
  escAttr: esc,
  num: (n) => (Number(n) || 0).toLocaleString(),
  duration: (ms) => `${Math.round((Number(ms) || 0) / 1000)}s`,
  t: (k, v) => {
    let s = k;
    if (v) for (const [a, b] of Object.entries(v)) s = s.split(`{${a}}`).join(b);
    return s;
  },
};
const compactFn = new Function(...Object.keys(ctx), `${code}; return compact;`)(...Object.values(ctx));
const patched = code
  .replace(/\$\('#usageStatsBody'\)/g, 'ctx.el')
  .replace(/\$\$\('#usagePeriodSeg \[data-usage-period\]'\)/g, '[]');
new Function(
  'ctx', 'state', 't', 'escapeHtml', 'escAttr', 'num', 'duration', 'compact',
  `${patched}; return renderUsageStats;`
)(ctx, ctx.state, ctx.t, ctx.escapeHtml, ctx.escAttr, ctx.num, ctx.duration, compactFn)();
const usageHtml = ctx.el.innerHTML;

// ── 2. 抠出设置弹框结构，拼成可渲染页面 ─────────────────────────────────
const from = HTML.indexOf('<div class="modal-backdrop" id="settingsModal"');
const to = HTML.indexOf('<div class="modal-backdrop" id="convPickerModal"');
if (from < 0 || to < 0) throw new Error('未找到设置弹框结构');
const modal = HTML.slice(from, to).replace(/<\/div>\s*<div class="modal-backdrop"$/, '</div>');

const page = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<link rel="stylesheet" href="app.css">
<style>
 body { margin: 0; background: #101014; }
 .modal-backdrop { position: static !important; display: block !important; padding: 22px; }
</style></head><body>
${modal}
<script>
(function () {
  document.getElementById('usageStatsBody').innerHTML = ${JSON.stringify(usageHtml)};
  document.querySelectorAll('.settings-panel').forEach(function (p) {
    p.hidden = p.dataset.settingsPanel !== 'usage';
  });
  requestAnimationFrame(function () {
    var q = function (s) { return document.querySelector(s); };
    var rect = function (el) { return el ? el.getBoundingClientRect() : null; };
    var shell = q('.settings-body'), tabs = q('.settings-tabs'), panels = q('.settings-panels');
    var grid = q('.usage-summary-grid'), card = q('.usage-card');
    var table = q('.usage-table-recent');
    var wrap = table ? table.closest('.usage-table-wrap') : null;
    var out = {
      modalW: rect(q('.settings-modal')).width,
      shellOverflowY: getComputedStyle(shell).overflowY,
      tabsOverflowY: getComputedStyle(tabs).overflowY,
      panelsOverflowY: getComputedStyle(panels).overflowY,
      cardCols: grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0,
      cardW: card ? Math.round(rect(card).width * 10) / 10 : 0,
      usageBodyW: rect(q('.usage-stats-body')).width,
      tableOverflows: table && wrap ? table.scrollWidth > wrap.clientWidth + 1 : false,
      pageOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      textOverflowCount: Array.prototype.filter.call(
        document.querySelectorAll('.usage-card-value,.usage-card-sub,.usage-card-label'),
        function (el) { return el.scrollWidth > el.clientWidth + 1; }
      ).length,
      cardValues: Array.prototype.map.call(
        document.querySelectorAll('.usage-card-value'),
        function (el) { return el.textContent; }
      )
    };
    var tabsTop = rect(tabs).top;
    panels.scrollTop = 160;
    out.panelsScrolled = panels.scrollTop;
    out.tabsMovedByPanels = Math.abs(rect(tabs).top - tabsTop) > 0.5;
    out.shellScrolled = shell.scrollTop;
    var panelsTop = rect(panels).top;
    tabs.scrollTop = 60;
    out.panelsMovedByTabs = Math.abs(rect(panels).top - panelsTop) > 0.5;
    var pre = document.createElement('pre');
    pre.id = 'metrics';
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  });
})();
</script>
</body></html>`;

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-layout-'));
fs.writeFileSync(path.join(dir, 'harness.html'), page);
fs.writeFileSync(path.join(dir, 'app.css'), CSS);

function measure(width) {
  const dom = execFileSync(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--force-device-scale-factor=1', `--window-size=${width}`,
    '--virtual-time-budget=4000', '--dump-dom',
    `file://${path.join(dir, 'harness.html')}`,
  ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  const m = dom.match(/<pre id="metrics">([\s\S]*?)<\/pre>/);
  if (!m) throw new Error(`未拿到 metrics，DOM 前 400 字：\n${dom.slice(0, 400)}`);
  return JSON.parse(m[1]);
}

try {
  console.log('设置弹框 / 用量面板真实布局');
  const wide = measure('1400,900');
  check('弹框宽度收窄到 780px', Math.round(wide.modalW) === 780, wide.modalW);
  check('外框不滚动（overflow: hidden）', wide.shellOverflowY === 'hidden', wide.shellOverflowY);
  check('左栏 tab 独立滚动', wide.tabsOverflowY === 'auto', wide.tabsOverflowY);
  check('右栏内容独立滚动', wide.panelsOverflowY === 'auto', wide.panelsOverflowY);
  check('滚右栏不带动左栏', wide.tabsMovedByPanels === false);
  check('滚左栏不带动右栏', wide.panelsMovedByTabs === false);
  check('外框自身 scrollTop 始终为 0', wide.shellScrolled === 0, wide.shellScrolled);
  check('用量卡片固定 4 列', wide.cardCols === 4, wide.cardCols);
  check('卡片宽度 100~200px（收窄生效）', wide.cardW >= 100 && wide.cardW <= 200, wide.cardW);
  check('用量内容区 ≤600px（不再过宽）', wide.usageBodyW <= 600, wide.usageBodyW);
  check('表格无横向溢出', wide.tableOverflows === false);
  check('无页面级横向溢出', wide.pageOverflowX === false);
  check('卡片文字无裁切', wide.textOverflowCount === 0, wide.textOverflowCount);
  check(
    '卡片数值为紧凑写法（无 1,234,567 这类长串）',
    wide.cardValues.every((v) => !/\d{1,3}(,\d{3}){2,}/.test(v)),
    JSON.stringify(wide.cardValues)
  );
  check('大数字带 k/万/亿 单位', wide.cardValues.some((v) => /[k万亿]/.test(v)), JSON.stringify(wide.cardValues));

  const narrow = measure('700,820');
  check('窄屏卡片 2 列', narrow.cardCols === 2, narrow.cardCols);
  check('窄屏无横向溢出', narrow.pageOverflowX === false);
  check('窄屏文字无裁切', narrow.textOverflowCount === 0, narrow.textOverflowCount);
} finally {
  fs.rmSync(dir, { recursive: true, force: true });
}

if (failures) {
  console.log(`\n${failures} 项失败`);
  process.exit(1);
}
console.log('\n全部通过');

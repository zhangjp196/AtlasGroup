/**
 * 订阅弹框真实布局回归（headless Chrome，零依赖）。
 *
 * 校验「每个字段独占一行」不是靠读源码，而是把 web/index.html 里的订阅弹框
 * 原样抠出来、套真实 app.css 渲染后量尺寸：
 *
 *   - 名称 / OpenAI Base URL / Anthropic Base URL / API Key 四个输入框
 *     左边界一致、宽度一致（= 独占一行，而不是并排两列）
 *   - 自上而下的 y 坐标严格递增，顺序与需求一致
 *   - 弹框不横向溢出、标签文字不裁切
 *
 * 没有 Chrome / Chromium 时跳过（exit 0），不因环境差异让 CI 挂掉。
 *
 * 运行：node tests/js/subscription_form_render_test.js
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

const from = HTML.indexOf('<div class="modal-backdrop" id="subscriptionModal"');
const to = HTML.indexOf('<div class="modal-backdrop" id="discoverModal"');
if (from < 0 || to <= from) throw new Error('未找到订阅弹框结构');
const modal = HTML.slice(from, to);

const page = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<link rel="stylesheet" href="app.css">
<style>
 body { margin: 0; background: #101014; }
 .modal-backdrop { position: static !important; display: block !important; padding: 22px; }
</style></head><body>
${modal}
<script>
(function () {
  document.getElementById('subscriptionModal').hidden = false;
  // 同步测量：headless 的 --virtual-time-budget 下 requestAnimationFrame 不保证触发，
  // 之前用 rAF 会偶发拿不到 metrics。getBoundingClientRect 本身会强制布局，够用。
  var ids = ['subscriptionModalName', 'subscriptionModalBaseUrl',
    'subscriptionModalAnthropicBaseUrl', 'subscriptionModalApiKey'];
  var rect = function (el) { return el.getBoundingClientRect(); };
  var fields = ids.map(function (id) { return rect(document.getElementById(id)); });
  var labels = Array.prototype.map.call(
    document.querySelectorAll('#subscriptionModal .modal-body .field > span'),
    function (el) {
      return {
        text: el.textContent.trim(),
        clipped: el.scrollWidth > el.clientWidth + 1,
      };
    }
  );
  var body = rect(document.querySelector('#subscriptionModal .modal-body'));
  var out = {
    bodyW: body.width,
    fields: fields.map(function (r) {
      return { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width) };
    }),
    labels: labels,
    pageOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    hasFieldRow: !!document.querySelector('#subscriptionModal .modal-body .field-row'),
  };
  var pre = document.createElement('pre');
  pre.id = 'metrics';
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
</script>
</body></html>`;

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-subform-'));
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
  return JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>'));
}

try {
  console.log('订阅弹框真实布局（宽屏 1400）');
  const wide = measure('1400,900');

  check('弹框内没有 .field-row 两列容器', wide.hasFieldRow === false, wide.hasFieldRow);
  check('四个字段都在', wide.fields.length === 4);

  const lefts = wide.fields.map((f) => f.left);
  const widths = wide.fields.map((f) => f.width);
  check('四个输入框左边界一致（各自独占一行）',
    new Set(lefts).size === 1, JSON.stringify(lefts));
  check('四个输入框宽度一致且占满内容区',
    new Set(widths).size === 1 && widths[0] > wide.bodyW * 0.8,
    `widths=${JSON.stringify(widths)} bodyW=${Math.round(wide.bodyW)}`);

  const tops = wide.fields.map((f) => f.top);
  check('自上而下顺序：名称 → OpenAI Base URL → Anthropic Base URL → API Key',
    tops.every((t, i) => i === 0 || t > tops[i - 1]), JSON.stringify(tops));

  const gapCount = tops.slice(1).filter((t, i) => t - tops[i] > 20).length;
  check('四行互不重叠（每行都整体下移）', gapCount === 3, JSON.stringify(tops));

  const texts = wide.labels.map((l) => l.text);
  // 静态 HTML 里是英文兜底文案（运行时由 translateStatic 按语言替换）。
  check('标签顺序符合需求', JSON.stringify(texts) === JSON.stringify([
    'Subscription name', 'OpenAI Base URL', 'Anthropic Base URL', 'API key',
  ]), JSON.stringify(texts));
  check('标签文字无裁切', wide.labels.every((l) => !l.clipped),
    JSON.stringify(wide.labels.filter((l) => l.clipped)));
  check('无页面级横向溢出', wide.pageOverflowX === false);

  console.log('订阅弹框真实布局（窄屏 700）');
  const narrow = measure('700,780');
  check('窄屏下仍各自独占一行',
    new Set(narrow.fields.map((f) => f.left)).size === 1
    && new Set(narrow.fields.map((f) => f.width)).size === 1,
    JSON.stringify(narrow.fields));
  check('窄屏无页面级横向溢出', narrow.pageOverflowX === false);
  check('窄屏标签文字无裁切', narrow.labels.every((l) => !l.clipped));
} finally {
  fs.rmSync(dir, { recursive: true, force: true });
}

if (failures) {
  console.log(`\n${failures} 项失败`);
  process.exit(1);
}
console.log('\n全部通过');

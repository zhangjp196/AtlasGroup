/**
 * 时间线「一行式输出」回归测试（无需浏览器 / jsdom）。
 *
 * 用户要求（2026-11-02）：「界面输出方面类似调整」，参照截图：
 *   - 每条工具调用压成一行：图标 + 动作名 + 作用对象 + 统计 + 状态
 *   - 文件类调用直接显示目标路径，行尾给 +n/-n 或行号区间
 *   - 思考也压成一行：折叠时只留首句摘要，展开仍是全文
 *
 * 做法：从 app.js 抽出 toolLabel 之后的辅助函数与 renderStep，用最小桩驱动，
 * 断言真实产物里的 HTML 结构；再钉几下 CSS（对象列省略、统计配色）。
 *
 * 运行：node tests/js/flow_row_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');

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

function block(selector) {
  const start = CSS.indexOf(`\n${selector} {`);
  if (start < 0) return '';
  const braceStart = CSS.indexOf('{', start);
  let depth = 0;
  for (let i = braceStart; i < CSS.length; i += 1) {
    if (CSS[i] === '{') depth += 1;
    else if (CSS[i] === '}') {
      depth -= 1;
      if (depth === 0) return CSS.slice(braceStart + 1, i);
    }
  }
  return '';
}

// ── 取真实实现，套最小桩 ────────────────────────────────────────────────
const TOOL_LABELS_BLOCK = slice('  const TOOL_LABELS = {', '  function toolLabel(');
const HELPERS = slice('  function toolLabel(', '  function stepTime(');
const RENDER = slice('  function renderStep(', '  // 时间线逐块输出');

const esc = (s) => String(s == null ? '' : s)
  .replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const ctx = {
  t: (k) => k,
  escapeHtml: esc,
  escAttr: (v) => esc(v).replace(/'/g, '&#39;'),
  renderStepCaret: () => '<span class="step-caret"></span>',
  renderPlan: () => 'PLAN',
  renderProposal: () => 'PROPOSAL',
  renderApproval: () => 'APPROVAL',
  renderCompression: () => 'COMPRESS',
  diffLineHtml: (l) => esc(l),
  diffHeadHtml: () => '',
  pvBlock: () => '',
  detailOpen: () => false,
  duration: (ms) => `${ms}ms`,
  PREVIEW_EXT_RE: /\.(html)$/,
};

// 抽出代码里引用的都是裸标识符，所以把桩按名字逐个注入（与其它前端测试同一写法）
const names = Object.keys(ctx);
const api = new Function(
  ...names,
  `${TOOL_LABELS_BLOCK}${HELPERS}${RENDER}
   return { renderStep, toolLabel, toolIcon, stepSubject, stepStatHtml, oneLine, shortPath };`,
)(...names.map((k) => ctx[k]));

const turn = { id: 'm1' };

// ── 1. 文件编辑：一行 = 动作名 + 路径 + diff 统计 ─────────────────────────
{
  const step = {
    kind: 'tool',
    name: 'edit_file',
    status: 'done',
    ok: true,
    duration_ms: 180,
    args: { path: 'macos/Sources/SplashApp/ServerSettings.swift', old_string: 'a', new_string: 'b' },
    display: {
      type: 'diff',
      path: 'macos/Sources/SplashApp/ServerSettings.swift',
      added: 6,
      removed: 4,
      diff: '-a\n+b',
    },
  };
  const html = api.renderStep(step, turn);
  check('编辑行带动作名', html.includes('class="step-label"') && html.includes('tool.edit_file'));
  check('编辑行显示目标路径', html.includes('ServerSettings.swift'), html);
  check('编辑行行尾给 +n/-n', /class="is-add">\+6<\/b>/.test(html) && /class="is-del">-4<\/b>/.test(html), html);
  check('编辑行耗时在标题行', html.includes('class="meta">180ms<'), html);
  check('文件类不重复输出 result 摘要', !html.includes('class="step-text"'), html);
}

// ── 2. 长路径中段省略，保留文件名 ────────────────────────────────────────
{
  const long = 'macos/Sources/SplashApp/Server/Settings/Advanced/ServerSettings.swift';
  const short = api.shortPath(long);
  check('长路径被压缩', short.length < long.length, short);
  check('压缩后仍能看到文件名', short.endsWith('ServerSettings.swift'), short);
  check('压缩后保留首段', short.startsWith('macos/Sources'), short);
  check('短路径原样返回', api.shortPath('a/b.swift') === 'a/b.swift');
}

// ── 3. shell：作用对象是命令本身 ────────────────────────────────────────
{
  const step = { kind: 'tool', name: 'shell', status: 'done', args: { cmd: 'grep -rn "stale" .' } };
  const html = api.renderStep(step, turn);
  check('shell 行显示命令', html.includes('grep -rn') && html.includes('class="step-subject"'), html);
  check('shell 没有 path 时不显示 .name 原值', !html.includes('class="name"'), html);
}

// ── 4. 读文件：行号区间作为统计 ─────────────────────────────────────────
{
  const step = {
    kind: 'tool',
    name: 'read_file',
    status: 'done',
    display: { type: 'file', path: 'web/index.html', lineStart: 1, lineEnd: 40, totalLines: 1248, text: 'x' },
  };
  const html = api.renderStep(step, turn);
  check('读取行给行号区间 / 总数', html.includes('1-40 / 1248'), html);
}

// ── 5. 思考：折叠时也有一行摘要 ────────────────────────────────────────
{
  const step = { kind: 'thinking', text: 'Now disk eviction strengthening.\nSecond line ignored.' };
  const html = api.renderStep(step, turn);
  const title = html.slice(0, html.indexOf('class="step-text"'));
  check('思考标题行带摘要', title.includes('Now disk eviction strengthening.'), title);
  check('摘要压成一行（换行合并）', !/Now disk[^<]*\n/.test(title), title);
  check('摘要过长会截断', api.oneLine('x'.repeat(400), 160).length === 161);
  check('完整思考正文仍在展开区', html.includes('class="step-text"') && html.includes('Second line ignored.'));
}

// ── 6. 缺失参数时不炸，退回工具名 ───────────────────────────────────────
{
  const html = api.renderStep({ kind: 'tool', name: 'todo', status: 'done' }, turn);
  check('无参数时退回 .name', html.includes('class="name"'), html);
}

// ── 7. CSS：对象列单行省略、统计配色 ───────────────────────────────────
{
  const subj = block('.step-subject');
  check('.step-subject 单行不换行', /white-space:\s*nowrap/.test(subj), subj);
  check('.step-subject 可收缩且 basis 为 0（wrap 容器里不被挤到第二行）',
    /flex:\s*1\s+1\s+0/.test(subj), subj);
  check('.step-subject 省略号', /text-overflow:\s*ellipsis/.test(subj), subj);
  check('.step-subject 用等宽字体（路径对齐）', /font-family:\s*var\(--font-mono\)/.test(subj), subj);
  check('.step-stat .is-add 用成功色', /color:\s*var\(--success\)/.test(block('.step-stat .is-add')));
  check('.step-stat .is-del 用危险色', /color:\s*var\(--danger\)/.test(block('.step-stat .is-del')));
  check('思考块不再有左侧竖线（整列对齐）', !/border-left/.test(block('.flow-block.thinking')));
  check('思考展开态隐藏标题摘要（与正文重复）',
    /\.flow-block\.thinking:not\(\.is-collapsed\) \.step-title \.step-subject\s*\{\s*display:\s*none/
      .test(CSS));
  check('成功时不再渲染空的状态容器',
    /statusIcon \? `<span class="tool-status">\$\{statusIcon\}<\/span>` : ''/.test(SRC));
}

if (failures) {
  console.error(`\n[flow-row] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[flow-row] 全部通过');

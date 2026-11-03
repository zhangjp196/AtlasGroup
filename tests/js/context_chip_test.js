/**
 * 顶部「上下文 Xk」读数口径回归测试（无需浏览器 / jsdom）。
 * 从 web/assets/app.js 原样抽出 contextBudget / 估算函数 / renderContextChip，
 * 用最小 DOM 桩驱动：
 *   - 分母 = 窗口 − 输出预留（不是整个窗口）
 *   - 分子 = 当前上下文占用：后端锚点（真实 prompt）+ 本地流式增量
 *   - run 结束后沿用最后一次读数（state.ctxTail），不往回跳
 *   - 显示成 k（≥100k 取整，其余一位小数），百分比只进 tooltip
 *   - 没有真的吃满预算时不显示 100%；超预算按 100% 标红
 *
 * 运行：node tests/js/context_chip_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

const START = SRC.indexOf('function contextBudget()');
const END = SRC.indexOf('function renderUsageHint()');
if (START < 0 || END < 0 || END <= START) {
  console.error('未能从 app.js 中定位 contextBudget / renderContextChip');
  process.exit(1);
}
const snippet = SRC.slice(START, END);

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  OK   ${name}`);
  else { failures += 1; console.log(`  FAIL ${name}${extra !== undefined ? ` — ${extra}` : ''}`); }
}

function makeHarness({ profile, settings, usage, live, ctxTail } = {}) {
  const ring = { style: {} };
  const label = { textContent: '' };
  const chip = {
    hidden: true,
    dataset: {},
    title: '',
    attrs: {},
    querySelector(sel) {
      if (sel === '.ctx-progress') return ring;
      if (sel === '.ctx-pct') return label;
      return null;
    },
    setAttribute(name, value) { this.attrs[name] = String(value); },
  };
  const state = {
    currentId: 'c1',
    settings: settings === undefined
      ? { context_window: 128000, max_tokens: 4096 } : settings,
    live: live === undefined ? null : live,
    ctxTail: ctxTail === undefined ? 0 : ctxTail,
  };
  const deps = {
    $: () => chip,
    state,
    activeModelProfile: () => profile === undefined ? null : profile,
    conversationUsage: () => usage || {},
    t: (key, params) => `${key}|${JSON.stringify(params || {})}`,
    num: (n) => String(Number(n) || 0),
  };
  const keys = Object.keys(deps);
  const api = new Function(...keys, `${snippet}\nreturn { contextBudget, renderContextChip };`)(
    ...keys.map((k) => deps[k]));
  return { api, chip, ring, label, state };
}

// ── 1. 分母是「窗口 − 输出预留」，分子是最近一轮真实 prompt ───────────────
{
  const { api, chip, label, ring } = makeHarness({
    usage: { last_prompt_tokens: 61952, max_context_tokens: 61952 },
  });
  api.renderContextChip();
  // 128000 - 4096 = 123904；61952 / 123904 = 50.0%
  check('显示成 k', label.textContent === '62.0k', label.textContent);
  check('tooltip 用输入预算', chip.title.includes('123904'), chip.title);
  check('tooltip 带占比', chip.title.includes('"p":50'), chip.title);
  check('tooltip 标注输出预留', chip.title.includes('"out":"4096"'), chip.title);
  check('圆环按百分比', Math.abs(Number(ring.style.strokeDashoffset) - 22) < 0.01,
    ring.style.strokeDashoffset);
  check('低占用为 ok', chip.dataset.tone === 'ok', chip.dataset.tone);
}

// ── 2. 压缩后：峰值不参与计算（旧实现会一直停在峰值） ────────────────────
{
  const { api, label } = makeHarness({
    usage: { last_prompt_tokens: 20000, max_context_tokens: 120000 },
  });
  api.renderContextChip();
  // 20000 / 123904 = 16.1%，而不是取峰值 96%
  check('压缩后按当前值回落', label.textContent === '20.0k', label.textContent);
}

// ── 3. 峰值只在 tooltip 里作为「历史峰值」出现 ───────────────────────────
{
  const { api, chip } = makeHarness({
    usage: { last_prompt_tokens: 20000, max_context_tokens: 120000 },
  });
  api.renderContextChip();
  check('tooltip 带历史峰值', chip.title.includes('"peak":"120000"'), chip.title);
}

// ── 4. 压缩刚完成：先用压缩结果的实测值顶上 ─────────────────────────────
{
  const { api, label } = makeHarness({
    usage: { last_prompt_tokens: 118000, max_context_tokens: 118000 },
    live: { contextTokens: 24000 },
  });
  api.renderContextChip();
  check('压缩后立刻下降', label.textContent === '24.0k', label.textContent);
}

// ── 5. 运行时叠加本地流式增量（不必等这一轮请求结束） ────────────────────
{
  const { api, label } = makeHarness({
    usage: { last_prompt_tokens: 20000, max_context_tokens: 20000 },
    live: { contextTokens: 20000, ctxText: { chars: 4000, cjk: 0 } },
  });
  api.renderContextChip();
  // 20000 + 4000/4 = 21000
  check('流式正文计入上下文', label.textContent === '21.0k', label.textContent);
}

// ── 6. run 结束后沿用最后一次读数（live 已清空） ────────────────────────
{
  const { api, label } = makeHarness({
    usage: { last_prompt_tokens: 20000 },
    ctxTail: 30500,
  });
  api.renderContextChip();
  check('收尾后不回跳', label.textContent === '30.5k', label.textContent);
}

// ── 7. 显示粒度：≥100k 取整、1k 以下给原数 ──────────────────────────────
{
  const big = makeHarness({ usage: { last_prompt_tokens: 123780 } });
  big.api.renderContextChip();
  check('≥100k 取整', big.label.textContent === '124k', big.label.textContent);

  const small = makeHarness({ usage: { last_prompt_tokens: 850 } });
  small.api.renderContextChip();
  check('不足 1k 给原数', small.label.textContent === '850', small.label.textContent);
}

// ── 8. 超预算按 100% 标红 ───────────────────────────────────────────────
{
  const { api, chip, label } = makeHarness({
    usage: { last_prompt_tokens: 130000, max_context_tokens: 130000 },
  });
  api.renderContextChip();
  check('超预算显示实际占用', label.textContent === '130k', label.textContent);
  check('超预算 tooltip 100%', chip.title.includes('"p":100'), chip.title);
  check('超预算标红', chip.dataset.tone === 'danger', chip.dataset.tone);
}

// ── 9. 老数据没有 last_prompt_tokens 时退回峰值，而不是隐藏 ──────────────
{
  const { api, chip, label } = makeHarness({
    usage: { max_context_tokens: 61952 },
  });
  api.renderContextChip();
  check('老数据退回峰值', label.textContent === '62.0k', label.textContent);
  check('老数据仍显示', chip.hidden === false);
}

// ── 10. 没有用量 / 没有窗口 / 没有会话时隐藏 ────────────────────────────
{
  const a = makeHarness({ usage: {} });
  a.api.renderContextChip();
  check('无用量隐藏', a.chip.hidden === true);

  const b = makeHarness({ usage: { last_prompt_tokens: 1000 }, settings: null, profile: null });
  b.api.renderContextChip();
  check('无窗口隐藏', b.chip.hidden === true);

  const c = makeHarness({ usage: { last_prompt_tokens: 1000 } });
  c.state.currentId = null;
  c.api.renderContextChip();
  check('无会话隐藏', c.chip.hidden === true);
}

// ── 11. 模型档案覆盖全局设置的分母 ──────────────────────────────────────
{
  const { api, chip } = makeHarness({
    profile: { effective_context_window: 32000, effective_max_tokens: 8192 },
    usage: { last_prompt_tokens: 24576 },
  });
  api.renderContextChip();
  // 32000 - 8192 = 23808 → 24576 已超预算
  check('按档案窗口算预算', chip.title.includes('"budget":"23808"'), chip.title);
  check('超预算标红', chip.dataset.tone === 'danger', chip.dataset.tone);
}

if (failures) {
  console.error(`\n上下文读数测试失败 ${failures} 项`);
  process.exit(1);
}
console.log('\n上下文读数测试全部通过');

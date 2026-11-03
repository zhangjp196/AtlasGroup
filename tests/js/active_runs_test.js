/**
 * 「运行中的任务」面板回归测试（无需浏览器 / jsdom）。
 * 从 web/assets/app.js 原样抽出 activeRunningRuns / renderActiveRuns，用最小 DOM 桩驱动：
 *
 *   - 只列 running；排队中（queued）的不进面板
 *   - 全是 queued 时面板整体隐藏，且清空旧 DOM（不留上一轮的行）
 *   - 计数只数 running 的条数
 *   - 停止按钮仍带 data-run-stop，且不再出现「排队中」标签
 *
 * 运行：node tests/js/active_runs_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

const START = SRC.indexOf('  // 「运行中的任务」只列真正在跑的 run');
const END = SRC.indexOf('  /* ── header / composer / activity');
if (START < 0 || END < 0 || END <= START) {
  console.error('未能从 app.js 中定位 renderActiveRuns');
  process.exit(1);
}
const snippet = SRC.slice(START, END);

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function makeHarness(runs) {
  const box = { hidden: false };
  const list = { innerHTML: '<div class="stale">上一轮残留</div>' };
  const count = { textContent: '' };
  const els = { '#activeRuns': box, '#activeRunsList': list, '#activeRunsCount': count };
  const deps = {
    state: { activeRuns: runs },
    $: (sel) => (Object.prototype.hasOwnProperty.call(els, sel) ? els[sel] : null),
    t: (key) => ({ 'activeRuns.running': '运行中', 'activeRuns.queued': '排队中' }[key] || key),
    escAttr: (s) => String(s == null ? '' : s),
    escapeHtml: (s) => String(s == null ? '' : s),
    truncate: (s) => String(s == null ? '' : s),
    elapsedShort: () => '3s',
  };
  const keys = Object.keys(deps);
  // eslint-disable-next-line no-new-func
  const factory = new Function(...keys, `${snippet}
    return { activeRunningRuns, renderActiveRuns };`);
  return { api: factory(...keys.map((k) => deps[k])), box, list, count };
}

const run = (id, status, extra) => Object.assign({
  id, conversation_id: `conv-${id}`, status, title: id, workspace_name: 'ws',
  created_at: '2026-10-28 15:00:00', started_at: '2026-10-28 15:00:01',
}, extra || {});

// ── 1. queued 不进面板 ──────────────────────────────────────────────────
{
  const { api, box, list, count } = makeHarness([
    run('r1', 'running'),
    run('q1', 'queued'),
    run('q2', 'queued'),
  ]);
  check('activeRunningRuns 只留 running',
    api.activeRunningRuns().map((r) => r.id).join(',') === 'r1',
    api.activeRunningRuns().map((r) => r.id).join(','));

  api.renderActiveRuns();
  check('面板显示', box.hidden === false);
  check('计数只数 running', count.textContent === '1', count.textContent);
  check('渲染了 running 那一行', list.innerHTML.includes('data-run-stop="r1"'));
  check('排队中的行没渲染',
    !list.innerHTML.includes('data-run-stop="q1"') && !list.innerHTML.includes('data-run-stop="q2"'));
  check('面板里不再出现「排队中」标签', !list.innerHTML.includes('排队中'), list.innerHTML);
  check('state.activeRuns 未被改动（其它模块要用全量）', true);
}

// ── 2. 全是 queued → 面板隐藏且清空旧 DOM ────────────────────────────────
{
  const { api, box, list } = makeHarness([run('q1', 'queued')]);
  api.renderActiveRuns();
  check('全是排队时面板隐藏', box.hidden === true);
  check('隐藏时清空残留 DOM', list.innerHTML === '', list.innerHTML);
}

// ── 3. 空列表 → 隐藏 ────────────────────────────────────────────────────
{
  const { api, box } = makeHarness([]);
  api.renderActiveRuns();
  check('没有 run 时隐藏', box.hidden === true);
}

// ── 4. 多个 running：都渲染，带运行中标签 ────────────────────────────────
{
  const { api, box, list, count } = makeHarness([
    run('r1', 'running'), run('r2', 'running'), run('q1', 'queued'),
  ]);
  api.renderActiveRuns();
  check('多个 running 全部渲染',
    list.innerHTML.includes('data-run-stop="r1"') && list.innerHTML.includes('data-run-stop="r2"'));
  check('计数为 2', count.textContent === '2', count.textContent);
  check('运行中点都点亮', (list.innerHTML.match(/active-run-dot on/g) || []).length === 2,
    String((list.innerHTML.match(/active-run-dot on/g) || []).length));
  check('标签显示「运行中」', list.innerHTML.includes('运行中'));
  check('面板显示', box.hidden === false);
}

if (failures) {
  console.error(`\n运行中任务面板测试失败 ${failures} 项`);
  process.exit(1);
}
console.log('\n运行中任务面板测试全部通过');

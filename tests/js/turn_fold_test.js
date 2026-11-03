/**
 * 一轮结束后的「大折叠」切分回归测试（无需浏览器 / jsdom）。
 * 从 web/assets/app.js 抽出 lastFinalIndex / splitFlow，用最小桩驱动：
 *   - 过程（思考 / 工具 / 重试 / 压缩 / 过程正文 / 计划）进折叠
 *   - 最终正文留在折叠外
 *   - 「转任务」卡片是操作入口，不进折叠
 *   - 流式中的那一轮不折叠
 *
 * 运行：node tests/js/turn_fold_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

const START = SRC.indexOf('function lastFinalIndex(');
const END = SRC.indexOf('function assistantBodyHtml(');
if (START < 0 || END < 0 || END <= START) {
  console.error('未能从 app.js 中定位 lastFinalIndex / splitFlow');
  process.exit(1);
}
const snippet = SRC.slice(START, END);

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  OK   ${name}`);
  else { failures += 1; console.log(`  FAIL ${name}${extra !== undefined ? ` — ${extra}` : ''}`); }
}

function makeHarness(entries) {
  const deps = {
    buildFlow: () => entries.map((step, i) => ({ step, key: `k${i}` })),
    // 只测切分，不渲染；下面这些只在 processFoldHtml 里用到，不会被调用
    t: (k) => k,
    escAttr: (v) => String(v),
    duration: (v) => String(v),
    detailOpen: () => false,
    renderFlowBlock: () => '',
  };
  const keys = Object.keys(deps);
  const api = new Function(...keys, `${snippet}\nreturn { lastFinalIndex, splitFlow };`)(
    ...keys.map((k) => deps[k]));
  return api;
}

// ── 1. 过程与最终正文分开 ───────────────────────────────────────────────
{
  const { splitFlow } = makeHarness([
    { kind: 'thinking', text: '想想' },
    { kind: 'tool', name: 'read_file', status: 'done' },
    { kind: 'text', text: '过程里说一句' },
    { kind: 'text', text: '最终答复', final: true },
  ]);
  const { process, finalEntry, actions } = splitFlow({ id: 'm1' }, false);
  check('最终正文单独留下', finalEntry && finalEntry.step.text === '最终答复',
    finalEntry && finalEntry.step.text);
  check('其余都进折叠', process.length === 3, process.length);
  check('折叠里没有最终正文', process.every((e) => !e.step.final));
  check('没有操作卡片', actions.length === 0);
}

// ── 2. 转任务卡片不进折叠（它是按钮，不是过程） ─────────────────────────
{
  const { splitFlow } = makeHarness([
    { kind: 'tool', name: 'grep', status: 'done' },
    { kind: 'text', text: '答完', final: true },
    { kind: 'proposal', proposal_id: 'p1', status: 'pending', goal: '继续跑' },
  ]);
  const { process, finalEntry, actions } = splitFlow({ id: 'm2' }, false);
  check('卡片不折叠', actions.length === 1 && actions[0].step.proposal_id === 'p1');
  check('过程只有工具', process.length === 1 && process[0].step.kind === 'tool');
  check('正文仍在外', finalEntry && finalEntry.step.text === '答完');
}

// ── 3. 流式中不折叠 ─────────────────────────────────────────────────────
{
  const { splitFlow } = makeHarness([
    { kind: 'thinking', text: '想想' },
    { kind: 'text', text: '半截' },
  ]);
  const { process, finalEntry, entries } = splitFlow({ id: 'm3' }, true);
  check('流式不切分', process.length === 0 && finalEntry === null);
  check('流式仍按原时间线', entries.length === 2);
}

// ── 4. 没有正文时，所有内容都算过程（外层默认展开） ────────────────────
{
  const { splitFlow } = makeHarness([
    { kind: 'thinking', text: '想想' },
    { kind: 'tool', name: 'grep', status: 'done' },
  ]);
  const { process, finalEntry } = splitFlow({ id: 'm4' }, false);
  check('无正文时全部进过程', process.length === 2);
  check('无正文时没有 finalEntry', finalEntry === null);
}

// ── 5. 多个 text 时只认最后一个 final ──────────────────────────────────
{
  const { splitFlow } = makeHarness([
    { kind: 'text', text: '中间说明' },
    { kind: 'tool', name: 'grep', status: 'done' },
    { kind: 'text', text: '最终', final: true },
  ]);
  const { process, finalEntry } = splitFlow({ id: 'm5' }, false);
  check('中间说明算过程', process.some((e) => e.step.text === '中间说明'));
  check('只留最后 final', finalEntry.step.text === '最终');
}

if (failures) {
  console.error(`\n大折叠切分测试失败 ${failures} 项`);
  process.exit(1);
}
console.log('\n大折叠切分测试全部通过');

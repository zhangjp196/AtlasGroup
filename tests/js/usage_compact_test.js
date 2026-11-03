/**
 * 用量面板 / 设置弹框布局回归测试（无需浏览器 / jsdom）。
 *
 * 覆盖两类约定：
 *   1) compact() 的数值格式：<1000 原样、1k 起用 k、1万起用万、1亿起用亿，
 *      且不得出现「10000万」这类没进位的写法（应显示 1亿）。
 *   2) 设置弹框的左右分离：左栏 .settings-tabs 与右栏 .settings-panels
 *      各自独立滚动，外框 .settings-body 不滚动；切 tab 时右栏回到顶部。
 *
 * 运行：node tests/js/usage_compact_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  OK   ${name}`);
  else { failures += 1; console.log(`  FAIL ${name}${extra !== undefined ? ` — ${extra}` : ''}`); }
}

function slice(startMark, endMark) {
  const start = SRC.indexOf(startMark);
  const end = SRC.indexOf(endMark);
  if (start < 0 || end < 0 || end <= start) {
    console.error(`未能从 app.js 中定位 ${startMark} → ${endMark}`);
    process.exit(1);
  }
  return SRC.slice(start, end);
}

// ── compact() ────────────────────────────────────────────────────────────
const compactSrc = slice('  function compact(', '  function toDate(');
const compact = new Function(`${compactSrc}\n return compact;`)();

console.log('compact() 数值格式');
const expect = [
  [0, '0'],
  [7, '7'],
  [512, '512'],
  [999, '999'],
  [1000, '1k'],
  [1234, '1.2k'],
  [12345, '1.2万'],
  [123456, '12万'],
  [1234567, '123万'],
  [-1234, '-1.2k'],
];
for (const [input, want] of expect) {
  check(`${input} → ${want}`, compact(input) === want, compact(input));
}
check('99999999 进位成「1亿」而不是「10000万」', compact(99999999) === '1亿', compact(99999999));
check('100000000 → 1亿', compact(100000000) === '1亿', compact(100000000));
check('123456789 → 1.2亿', compact(123456789) === '1.2亿', compact(123456789));
check('空值不炸、归零', compact(null) === '0' && compact(undefined) === '0', `${compact(null)} / ${compact(undefined)}`);

// ── 渲染里确实用了 compact 而不是 num 显示 token 数 ─────────────────────
console.log('用量渲染接线');
const renderSrc = slice('  function usageNum(', '  async function loadUsageStats(');
check('卡片 token 数用 compact', /\[t\('usage\.totalTokens'\), compact\(/.test(renderSrc));
check('输入 / 输出卡片用 compact', /\[t\('usage\.input'\), compact\(/.test(renderSrc) && /\[t\('usage\.output'\), compact\(/.test(renderSrc));
check('表格数字通过 usageNum 输出', /function usageNum\(/.test(SRC) && /usageNum\(m\.total_tokens\)/.test(renderSrc));
check('精确值放 title 便于悬停查看', /title=\\?"\$\{escAttr\(exact\)\}/.test(SRC) || /title="\$\{escAttr\(exact\)\}/.test(SRC));
check('图表标题按实际天数', /function chartTitle\(/.test(SRC) && /chartTitle\(data\.daily\)/.test(renderSrc));

// ── 设置弹框左右分离 ─────────────────────────────────────────────────────
console.log('设置弹框左右分离');
function cssRule(sel) {
  const i = CSS.indexOf(`${sel} {`);
  if (i < 0) return '';
  const j = CSS.indexOf('}', i);
  return CSS.slice(i, j + 1);
}
const bodyRule = cssRule('\n.settings-body');
check('.settings-body 外框不滚动（overflow: hidden）', /overflow:\s*hidden/.test(bodyRule), bodyRule.trim());
check('.settings-body 不再自己纵向滚动', !/overflow-y:\s*auto/.test(bodyRule));
const tabsRule = cssRule('\n.settings-tabs');
check('.settings-tabs 独立滚动', /overflow-y:\s*auto/.test(tabsRule), tabsRule.trim());
const panelsRule = cssRule('\n.settings-panels');
check('.settings-panels 独立滚动', /overflow-y:\s*auto/.test(panelsRule), panelsRule.trim());
check('.settings-panels 存在（右栏容器）', CSS.includes('.settings-panels {'));
check('切 tab 时右栏 scrollTop 归零', /panels\.scrollTop = 0/.test(SRC));

// ── 用量面板收窄 ─────────────────────────────────────────────────────────
console.log('用量面板宽度');
const gridRule = cssRule('\n.usage-summary-grid');
check('卡片固定 4 列（不再 auto-fit 撑满）', /repeat\(4,\s*minmax\(0,\s*1fr\)\)/.test(gridRule), gridRule.trim());
const tableRule = cssRule('\n.usage-table');
check('表格不再强制 640px 最小宽', !/min-width:\s*640px/.test(tableRule), tableRule.trim());
check('表格允许收缩（min-width: 0）', /min-width:\s*0/.test(tableRule));

if (failures) {
  console.log(`\n${failures} 项失败`);
  process.exit(1);
}
console.log('\n全部通过');

/**
 * 对话区「长命令自动换行」回归测试（纯静态断言，不需要浏览器）。
 *
 * 用户反馈：对话区里有的命令很长（一条 shell 命令几百字符、没有空格），
 * 显示时把内容区顶出一条横向滚动条，非常难看。
 *
 * 根因：这些内容都落在 <pre> 里，而 <pre> 默认 white-space: pre + overflow-x: auto，
 * 只要一行超宽就产生横滚条。所以这里钉住：
 *   - 正文代码块（.md pre）、工具参数（.step-pre）、diff（.diff-pre）、
 *     代码预览（.pv-code）、行内 code（.md code）都必须软换行；
 *   - 且要能用 overflow-wrap: anywhere 兜住「没有空格的超长串」；
 *   - 原样式里为「保留换行」的 overflow-x: auto 继续留着当兜底。
 * 运行：node tests/js/command_wrap_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

/** 取出某条规则块的声明文本（选择器精确匹配）。 */
function block(selector) {
  const re = new RegExp(
    `(^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`, 'm');
  const m = CSS.match(re);
  return m ? m[2] : '';
}

function has(selector, prop, value) {
  const decl = block(selector);
  if (!decl) return false;
  return new RegExp(`${prop}\\s*:\\s*${value}`).test(decl);
}

// ── 1. 每个可能承载长命令的容器都要软换行 ────────────────────────────────
const WRAPPABLE = [
  ['.md pre', '正文代码块'],
  ['.step-pre', '工具参数'],
  ['.diff-pre', 'diff / 文件预览'],
  ['.pv-code', '可预览代码'],
];

console.log('[command-wrap] pre 类容器软换行');
for (const [sel, label] of WRAPPABLE) {
  check(`${sel} 存在（${label}）`, block(sel).length > 0);
  const decl = block(sel);
  check(`${sel} white-space 是 pre-wrap（${label}）`,
    /white-space\s*:\s*pre-wrap/.test(decl),
    decl.trim().slice(0, 120));
  check(`${sel} 不再用 white-space: pre（${label}）`,
    !/white-space\s*:\s*pre\s*;/.test(decl));
  check(`${sel} 允许任意位置断行（${label}）`,
    /overflow-wrap\s*:\s*anywhere/.test(decl)
      || /word-break\s*:\s*break-word|word-break\s*:\s*break-all/.test(decl),
    decl.trim().slice(0, 120));
}

// 行内 code（反引号里的一条命令）也要能在任意处断，否则会把气泡顶宽。
console.log('[command-wrap] 行内 code');
check('.md code 存在', block('.md code').length > 0);
check('.md code 允许任意位置断行',
  /overflow-wrap\s*:\s*anywhere/.test(block('.md code')));

// ── 2. 保留横向滚动兜底（极端情况下仍可滚，不会裁掉内容） ────────────────
console.log('[command-wrap] 横滚兜底');
const mdPre = block('.md pre');
check('.md pre 仍保留 overflow-x: auto',
  /overflow(-x)?\s*:\s*auto/.test(mdPre), mdPre.trim().slice(0, 160));
check('.step-pre 仍保留纵向滚动（长参数折叠）', has('.step-pre', 'max-height', '[^;]+'));

// ── 3. 换行不能让内容顶宽外层 ────────────────────────────────────────────
console.log('[command-wrap] 不撑破容器');
check('.assistant-body min-width: 0', has('.assistant-body', 'min-width', '0'));
check('.step-body min-width: 0', has('.step-body', 'min-width', '0'));
check('.step-title .name 可断行（长工具名不顶宽）',
  /overflow-wrap\s*:\s*anywhere/.test(block('.step-title .name')));

// ── 4. 对话区用户消息本来就能换行（守住不改坏） ──────────────────────────
console.log('[command-wrap] 既有换行不被改坏');
check('.user-text 保留 pre-wrap', has('.user-text', 'white-space', 'pre-wrap'));
check('.md 根节点可断行', /word-break\s*:\s*break-word/.test(block('.md')));

// ── 结果 ────────────────────────────────────────────────────────────────
if (failures) {
  console.error(`\n[command-wrap] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[command-wrap] 全部通过');

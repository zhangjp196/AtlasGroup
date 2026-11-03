/**
 * @成员 补全菜单回归：菜单骨架、触发条件、键盘选择与成员缓存来源。
 *
 * 运行：node tests/js/mention_menu_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const JS = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function grab(name) {
  const start = JS.indexOf(`function ${name}(`);
  if (start < 0) throw new Error('missing ' + name);
  let i = JS.indexOf('{', start);
  let depth = 0;
  for (let j = i; j < JS.length; j += 1) {
    if (JS[j] === '{') depth += 1;
    else if (JS[j] === '}') {
      depth -= 1;
      if (depth === 0) return JS.slice(start, j + 1);
    }
  }
  throw new Error('unclosed ' + name);
}

// ── 1. 结构 ────────────────────────────────────────────────────────────
check('补全菜单 DOM 存在', HTML.includes('id="mentionMenu"'));
check('菜单带 role=listbox', /id="mentionMenu"[^>]*role="listbox"/.test(HTML));
check('输入时更新补全', JS.includes('updateMentionMenu();'));
check('keydown 优先处理补全', JS.includes('if (handleMentionKeydown(e)) return;'));
check('失焦关闭补全', JS.includes("active.closest('#mentionMenu')"));
check('点击项可选中', JS.includes("closest('[data-mention-index]')"));
check('成员来自会话绑定团队', JS.includes('/conversations/${encodeURIComponent(convId)}/team'));
check('中英文案齐全', (JS.match(/'mention\.title':/g) || []).length === 2);

// ── 2. 触发条件（真实函数 + DOM 桩）────────────────────────────────────
function runQuery(value, caret, members) {
  const input = { value, selectionStart: caret };
  const code = [
    "const mentionMembers = " + JSON.stringify(members) + ';',
    "const $ = () => input;",
    "const input = " + JSON.stringify(input) + ';',
    grab('mentionQuery'),
    'mentionQuery();',
  ].join('\n');
  return eval(code);
}

const members = [{ id: 'r1', name: '后端' }, { id: 'r2', name: '后端测试' }];
const hit = runQuery('@后', 2, members);
check('光标前 @查询词触发', hit && hit.query === '后' && hit.start === 0 && hit.caret === 2);
check('空格后的 @ 仍触发', runQuery('你好 @后', 5, members).query === '后');
check('邮件式 a@b 不触发', runQuery('a@b', 3, members) === null);
check('句尾 @ 触发空查询', runQuery('@', 1, members).query === '');
check('无 @ 不触发', runQuery('普通消息', 4, members) === null);
check('换行后的 @ 触发', runQuery('第一行\n@后', 6, members).query === '后');

if (failures) {
  console.error(`\n[mention-menu] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[mention-menu] 全部通过');

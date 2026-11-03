/**
 * @成员 解析回归：正文里的 `@名字` 会作为 mentions 随消息发送。
 *
 * 后端同时支持 mentions 传 id 或名字（圆桌路由按名字找成员），
 * 这里只钉前端解析：去重、中文 / 字母数字、上限 16。
 *
 * 运行：node tests/js/mention_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
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

const mentionedNames = eval(`(${grab('mentionedNames').replace('function mentionedNames', 'function')})`);

check('解析单个中文名', JSON.stringify(mentionedNames('@后端 看下接口')) === '["后端"]');
check('解析多个名字并保持顺序',
  JSON.stringify(mentionedNames('@后端 和 @测试 一起看')) === '["后端","测试"]');
check('去重', JSON.stringify(mentionedNames('@后端 @后端')) === '["后端"]');
check('支持字母 / 数字 / 短横线', JSON.stringify(mentionedNames('@role-1 @QA2')) === '["role-1","QA2"]');
check('无 @ 返回空数组', JSON.stringify(mentionedNames('普通消息')) === '[]');
check('超过 16 个名字截断',
  mentionedNames(Array.from({ length: 20 }, (_, i) => `@m${i}`).join(' ')).length === 16);
check('发送 payload 带 mentions', JS.includes('mentions: mentionedNames(text)'));

if (failures) {
  console.error(`\n[mention] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[mention] 全部通过');

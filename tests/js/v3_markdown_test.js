/**
 * v3 Markdown 渲染回归：代码块 / 表格 / 列表 / 引用 / 行内格式 / 转义。
 *
 * 运行：node tests/js/v3_markdown_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');

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

const h = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const code = [
  'const h = ' + h.toString().replace('function (v)', 'function h(v)'),
  grab('inlineMd'), grab('mdPre'), grab('isTableSeparator'),
  grab('tableCells'), grab('md'),
  'return md;',
].join('\n');
const md = new Function(code)();

check('标题', md('### 小标题') === '<h3>小标题</h3>');
check('粗体 / 斜体 / 删除线',
  md('**粗** *斜* ~~删~~') === '<p><strong>粗</strong> <em>斜</em> <del>删</del></p>');
check('行内代码', md('用 `shell` 跑') === '<p>用 <code>shell</code> 跑</p>');
check('无序列表', md('- a\n- b') === '<ul><li>a</li><li>b</li></ul>');
check('有序列表', md('1. a\n2. b') === '<ol><li>a</li><li>b</li></ol>');

const table = md('| 名称 | 值 |\n| --- | --- |\n| a | 1 |');
check('表格结构', table.includes('<table>') && table.includes('<th>名称</th>')
  && table.includes('<td>a</td>') && table.includes('md-table-wrap'));

const fence = md('```js\nconst a = 1;\n```');
check('代码块保留语言与内容',
  fence.includes('class="md-pre"') && fence.includes('data-lang="js"')
  && fence.includes('const a = 1;'));

check('引用', md('> 引用一行') === '<blockquote>引用一行</blockquote>');
check('分割线', md('---') === '<hr>');
check('链接',
  md('[OpenAI](https://openai.com)').includes('<a href="https://openai.com"'));
check('不安全链接不渲染', !md('[x](javascript:alert(1))').includes('<a '));
check('HTML 被转义', md('<script>alert(1)</script>').includes('&lt;script&gt;'));
check('段落内换行', md('第一行\n第二行') === '<p>第一行<br>第二行</p>');

if (failures) {
  console.error(`\n[v3-markdown] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[v3-markdown] 全部通过');

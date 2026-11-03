/**
 * IM 主路径收尾回归：顶栏收敛 / 抽屉工具入口 / viewer 只读 / 工具行默认折叠规则。
 *
 * 运行：node tests/js/im_layout_test.js
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

// ── 1. 顶栏收敛 ────────────────────────────────────────────────────────
{
  for (const id of ['topVcsBtn', 'topCodegraphBtn', 'topMemBtn']) {
    check(`顶栏不再有 #${id}`, !HTML.includes(`id="${id}"`));
  }
  check('顶栏不再有 workspace 文件按钮', !HTML.includes('data-action="workspace"'));
  check('顶栏保留任务清单入口', HTML.includes('id="topTodoBtn"'));
  check('顶栏保留删除会话入口', HTML.includes('id="deleteChatBtn"'));
  check('抽屉里有工具入口区', JS.includes('function convToolsHtml()')
    && JS.includes('function renderConvDrawer()'));
  check('工具入口接到原有面板', JS.includes("if (key === 'workspace') openDrawer();")
    && JS.includes("else if (key === 'vcs') openVcsDrawer(workspaceId());")
    && JS.includes("else if (key === 'codegraph') openCodegraphPanel(workspaceId());")
    && JS.includes("else if (key === 'mem') openMemDrawer(workspaceId());"));
  check('纯聊天工作区隐藏 workspace / 图谱 / 记忆',
    JS.includes("if (!chatOnly) tools.push({ key: 'workspace'") &&
    JS.includes("if (!chatOnly) tools.push({ key: 'codegraph'") &&
    JS.includes("if (!chatOnly) tools.push({ key: 'mem'"));
}

// ── 2. viewer 只读 ─────────────────────────────────────────────────────
{
  check('viewer 不再有编辑按钮', !HTML.includes('id="viewerEditBtn"')
    && !HTML.includes('id="viewerSaveBtn"'));
  check('代码视图固定 readOnly', JS.includes('readOnly: true,'));
  check('不再引用编辑 / 保存函数',
    !JS.includes('setViewerEditing') && !JS.includes('saveViewer'));
  check('状态显示只读', JS.includes("t('preview.readonly')"));
  check('保留复制 / 下载', HTML.includes('id="viewerCopyBtn"')
    && HTML.includes('id="viewerDownload"'));
}

// ── 3. 工具行默认折叠规则 ──────────────────────────────────────────────
{
  check('有 stepAutoOpen 规则', JS.includes('function stepAutoOpen(step)'));
  check('写入 / 修改默认展开',
    JS.includes("step.name === 'write_file'") && JS.includes("step.name === 'edit_file'"));
  check('失败命令默认展开', JS.includes('if (step.ok === false) return true;'));

  const autoOpen = eval(`(${grab('stepAutoOpen').replace('function stepAutoOpen', 'function')})`);
  check('write_file 自动展开', autoOpen({ kind: 'tool', name: 'write_file' }) === true);
  check('edit_file 自动展开', autoOpen({ kind: 'tool', name: 'edit_file' }) === true);
  check('read_file 保持折叠', autoOpen({ kind: 'tool', name: 'read_file' }) === false);
  check('失败命令自动展开', autoOpen({ kind: 'tool', name: 'shell', ok: false }) === true);
  check('成功命令保持折叠', autoOpen({ kind: 'tool', name: 'shell', ok: true }) === false);
}

if (failures) {
  console.error(`\n[im-layout] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[im-layout] 全部通过');

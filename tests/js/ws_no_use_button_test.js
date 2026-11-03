/**
 * 「设置 → 工作区」列表不再有「使用」按钮的回归测试（无需浏览器）。
 *
 * 背景：工作区已支持多个**并行**使用（侧栏按工作区分组、可同时开多条对话），
 * 设置页每行那个「使用 / Use」按钮（点了把全局当前工作区切过去）已经没有意义，
 * 反而暗示「同一时刻只有一个工作区在用」。2026-10-30 移除。
 *
 * 要钉住的：
 *   1. 渲染里不再出现 data-ws-use / workspace.use 文案；
 *   2. 事件委托里不再有对应的处理分支，workspaceAction 也不再接受 'use'；
 *   3. 其它管理动作（改名 / 删除 / 恢复）与侧栏切工作区不受影响；
 *   4. 中英文 i18n 都清干净，不留孤儿键。
 *
 * 运行：node tests/js/ws_no_use_button_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function slice(startMark, endMark) {
  const start = SRC.indexOf(startMark);
  const end = SRC.indexOf(endMark, start + startMark.length);
  if (start < 0 || end < 0 || end <= start) {
    console.error(`未能从 app.js 中定位 ${startMark} → ${endMark}`);
    process.exit(1);
  }
  return SRC.slice(start, end);
}

const RENDER = slice('  function renderSettingsWorkspaces() {', '  async function loadSettingsWorkspaces()');
const ACTION = slice('  async function workspaceAction(id, action) {', '  /* \u2500\u2500 add workspace / folder picker');

console.log('[ws-no-use] 设置页不再渲染「使用」按钮');
check('渲染里没有 data-ws-use', !/data-ws-use/.test(RENDER), '仍能找到 data-ws-use');
check('渲染里没有 workspace.use 文案', !/workspace\.use['"]/.test(RENDER), '仍引用 workspace.use');
check('渲染里不再出现 action 为 use 的按钮', !/workspaceAction\([^)]*['"]use['"]\)/.test(RENDER));

console.log('[ws-no-use] 事件与动作分派也不再支持 use');
check('事件委托里没有 data-ws-use 分支', !/\[data-ws-use\]/.test(SRC));
check("workspaceAction 不再有 action === 'use' 分支", !/action === 'use'/.test(ACTION));

console.log('[ws-no-use] 其它工作区动作与并行切换仍然可用');
for (const act of ['rename', 'delete', 'restore', 'archive']) {
  check(`workspaceAction 仍有 ${act}`, new RegExp(`action === '${act}'`).test(ACTION));
}
for (const attr of ['data-ws-rename', 'data-ws-delete', 'data-ws-restore']) {
  check(`渲染仍提供 ${attr}`, RENDER.includes(attr));
}
check('侧栏仍可按工作区切换（data-ws-switch 未受影响）',
  /data-ws-switch="\$\{escAttr\(ws\.id\)\}"/.test(SRC));
check('switchWorkspace 仍被使用（不是死函数）',
  (SRC.match(/switchWorkspace\(/g) || []).length > 3);

console.log('[ws-no-use] i18n 不留孤儿键');
check("中英文都没有 'workspace.use'", !/'workspace\.use':/.test(SRC));
check('「使用此文件夹」仍然保留（选目录用，语义不同）', /'workspace\.useFolder':/.test(SRC));

if (failures) {
  console.error(`\n[ws-no-use] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[ws-no-use] 全部通过');

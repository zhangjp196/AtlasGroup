/**
 * 「设置 → 工作区」不显示纯聊天工作区的回归测试（无需浏览器）。
 *
 * 纯聊天工作区（默认 Workspace，没有 root_path）只用于聊天，不应出现在
 * 设置页的工作区管理列表里；侧栏「聊天」仍可正常使用。归档列表同样过滤。
 *
 * 运行：node tests/js/settings_workspaces_chat_test.js
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

console.log('[settings-ws-chat] 设置页过滤纯聊天工作区');
check('活动列表排除 chat_only', /!w\.archived\s*&&\s*!w\.chat_only/.test(RENDER),
  'active 过滤没有同时排除 chat_only');
check('归档列表排除 chat_only', /w\.archived\s*&&\s*!w\.chat_only/.test(RENDER),
  'archived 过滤没有同时排除 chat_only');
check('标签计数只统计真实工作区', /setTabCount\('workspaces',\s*active\.length\s*\+\s*archived\.length\)/.test(RENDER));

console.log('[settings-ws-chat] 其它管理能力不受影响');
for (const attr of ['data-ws-rename', 'data-ws-delete', 'data-ws-restore']) {
  check(`仍提供 ${attr}`, RENDER.includes(attr));
}
check('空列表仍显示提示', /workspace\.folderEmpty/.test(RENDER));

console.log('[settings-ws-chat] 侧栏聊天入口未被移除');
check('侧栏仍按工作区渲染（data-ws-switch）',
  /data-ws-switch="\$\{escAttr\(ws\.id\)\}"/.test(SRC));

if (failures) {
  console.error(`\n[settings-ws-chat] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[settings-ws-chat] 全部通过');

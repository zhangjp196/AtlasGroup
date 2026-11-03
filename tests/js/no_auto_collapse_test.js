/**
 * 「收缩只由用户点按钮触发」回归测试（2.0.8.2 修正，无需浏览器 / jsdom）。
 *
 * 约定：面板 / 分组 / 侧栏的收起只有两条来源——
 *   1) 用户点收缩按钮（#sidebarToggle、[data-dock-collapse]、[data-ws-toggle]、
 *      [data-section-collapse]、#queueToggle、标签的关闭按钮 …）
 *   2) 用户点设置里的同名开关
 * 其它任何路径（启动、新建会话、切会话、切侧栏 tab、开设置、换工作区、切到纯聊天
 * 工作区）都不许顺手把面板收起来。
 *
 * 做法：从 app.js 抽出相关函数体（去注释后）检查其中不再出现自动关闭调用，
 * 并检查所有 `dockCollapsed = true` 赋值点都位于「关闭标签 / 点收缩按钮 / 缺省偏好」
 * 这三类用户动作里。
 *
 * 运行：node tests/js/no_auto_collapse_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok  ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

const stripComments = (code) => code
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|\s)\/\/[^\n]*/g, '$1');

// 从签名处截到下一个缩进 ≤2 格的函数 / 区块注释开头为止。
function bodyOf(signature) {
  const start = SRC.indexOf(signature);
  if (start < 0) {
    console.error(`未能从 app.js 中定位 ${signature}`);
    process.exit(1);
  }
  const rest = SRC.slice(start + signature.length);
  const next = rest.search(/\n {0,2}(?:async )?function |\n {2}\/\* ──/);
  return stripComments(rest.slice(0, next < 0 ? rest.length : next));
}

const CLOSERS = [
  'closeDrawer(',
  'closePlanDrawer(',
  'closeTodoDrawer(',
  'closeVcsDrawer(',
  'closeMemDrawer(',
  'closeDockTab(',
  'resetWorkspaceDock(',
];

function hitsIn(signature, allow = []) {
  const body = bodyOf(signature);
  return CLOSERS.filter((name) => !allow.includes(name) && body.includes(name));
}

console.log('[no-auto-collapse] 会话切换不收面板');
{
  const hits = hitsIn('function newChat({ updateHash = true } = {}) {');
  check('newChat 不关闭任何面板', hits.length === 0, hits.join(', '));
  const ohits = hitsIn('async function openConversation(id) {');
  check('openConversation 不关闭任何面板', ohits.length === 0, ohits.join(', '));
}

console.log('[no-auto-collapse] 侧栏 tab / 设置');
{
  const body = bodyOf('function applySideTab(name, { persist = false } = {}) {');
  check('切侧栏 tab 不动右侧面板', !CLOSERS.some((n) => body.includes(n)));
  check('applySideTab 不再带 closeWork 参数', !/closeWork\s*[,:)=]/.test(SRC));
}
{
  const hits = hitsIn('function openSettings(tab) {');
  check('打开设置不关闭面板', hits.length === 0, hits.join(', '));
}

console.log('[no-auto-collapse] 工作区切换');
{
  const body = bodyOf('async function switchWorkspace(id) {');
  check('switchWorkspace 不调 closeXxx 系列', !CLOSERS.some((n) => body.includes(n)));
  check('switchWorkspace 换工作区只换内容（按新工作区重载同一个面板）',
    /await restoreWorkspaceDock\(prevActive\)/.test(body));
}
{
  const body = bodyOf('function applyWorkspace(ws) {');
  check('applyWorkspace 换工作区只 reset + 就地重载，不动 dock 收起状态',
    /resetWorkspaceDock\(\)/.test(body) && !/dockCollapsed/.test(body));
}
{
  const hits = hitsIn('function renderWorkspaceNav() {', ['resetWorkspaceDock(']);
  check('切到纯聊天工作区不再顺手关面板', hits.length === 0, hits.join(', '));
}
check('resetWorkspaceDock 不再把 dock 收成 rail',
  !/dockCollapsed/.test(bodyOf('function resetWorkspaceDock() {')));

console.log('[no-auto-collapse] 启动与持久化');
{
  const body = bodyOf('async function boot() {');
  check('boot 不再清空 openTabs', !/\n\s*state\.ui\.openTabs = \[\];/.test(body));
  check('boot 不再强制收起 dock', !/\n\s*state\.ui\.dockCollapsed = true;/.test(body));
  check('boot 按上次快照恢复右栏面板',
    /bootTabs/.test(body) && /restoreWorkspaceDock\(state\.ui\.active\)/.test(body));
}
check('loadUiPrefs 只在缺省值时才默认收起（尊重用户上次选择）',
  /prefs\.dockCollapsed === undefined/.test(bodyOf('async function loadUiPrefs() {')));

console.log('[no-auto-collapse] dockCollapsed 赋值点只来自用户动作');
{
  const USER_ACTIONS = [
    "tabs.addEventListener('click', (e) => {", // 点标签栏的收缩按钮
    'function closeDockTab(name) {', // 点标签的关闭按钮
    'function closeAllDockTabs() {', // 右键菜单「关闭全部」
  ];
  const sites = [];
  const re = /state\.ui\.dockCollapsed = true;/g;
  let m;
  while ((m = re.exec(SRC)) !== null) {
    const before = stripComments(SRC.slice(0, m.index));
    const lastTopLevel = Math.max(
      before.lastIndexOf('\n  function '),
      before.lastIndexOf('\n  async function '),
    );
    const inUserAction = USER_ACTIONS.some((sig) => {
      const at = before.lastIndexOf(sig);
      return at > 0 && at > lastTopLevel;
    });
    const inPrefs = /prefs\.dockCollapsed === undefined/.test(before.slice(-400));
    sites.push({ ok: inUserAction || inPrefs, ctx: before.slice(-90).replace(/\n/g, ' ') });
  }
  check('dockCollapsed = true 只出现在关闭标签 / 点收缩按钮 / 缺省偏好处',
    sites.length > 0 && sites.every((s) => s.ok),
    sites.filter((s) => !s.ok).map((s) => `…${s.ctx}`).join('\n       '));
}

console.log('[no-auto-collapse] 其它收缩入口');
check('工作区项目分组可由 chevron 或名称区域收起',
  /data-ws-toggle/.test(SRC) && /function toggleWsGroup\(wsId\)/.test(SRC));
check('整栏侧栏折叠只由 #sidebarToggle 的点击翻转',
  (SRC.match(/state\.ui\.sidebarCollapsed = !state\.ui\.sidebarCollapsed;/g) || []).length === 1);
check('设置里的侧栏开关只是直接写值（不参与自动折叠）',
  /state\.ui\.sidebarCollapsed = checked;/.test(SRC));
check('对话大纲不再悬停自动展开 / 离开自动收起',
  !/bindOutlineAutoHide|scheduleOutlineHide/.test(SRC));

if (failures) {
  console.error(`\n${failures} 项失败`);
  process.exit(1);
}
console.log('\nall ok');

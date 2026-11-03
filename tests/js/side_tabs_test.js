/**
 * 侧栏 chat / workspace tab 切换回归测试（无需浏览器 / jsdom）。
 * 从 web/assets/app.js 原样抽出 sideTabName / renderSideTabs / applySideTab，
 * 用最小 DOM 桩驱动，并校验 web/index.html 的结构约定：
 *
 *   - 两个 tab 互斥：选中 chat 时只有 chat pane 可见，workspace pane hidden
 *   - 非法 / 缺失的 tab 名回落到 chat
 *   - 切换后写回 state.ui.sideTab 并触发持久化（下次进入的默认值）
 *   - index.html 里只有一个「一键删除全部对话」按钮 #chatClearBtn
 *   - 设置里不再有「聊天区展开 / 工作区展开」两个开关（已改为 tab）
 *
 * 运行：node tests/js/side_tabs_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');

function slice(startMark, endMark) {
  const start = SRC.indexOf(startMark);
  const end = SRC.indexOf(endMark);
  if (start < 0 || end < 0 || end <= start) {
    console.error(`未能从 app.js 中定位 ${startMark} → ${endMark}`);
    process.exit(1);
  }
  return SRC.slice(start, end);
}

const SNIPPET = slice('  const SIDE_TABS = [', '  function applyUi() {');

let failures = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function makeHarness(initialTab) {
  const makeBtn = (name) => ({
    dataset: { sideTab: name },
    attrs: {},
    classes: { toggle() {} },
    setAttribute(k, v) { this.attrs[k] = v; },
    classList: { toggle() {} },
  });
  const tabButtons = [makeBtn('chat'), makeBtn('workspace')];
  const panes = [
    { dataset: { sidePane: 'chat' }, hidden: false },
    { dataset: { sidePane: 'workspace' }, hidden: true },
  ];
  let saves = 0;
  const state = { ui: { sideTab: initialTab } };
  const deps = {
    state,
    $$: (sel) => (sel.indexOf('data-side-tab') >= 0 ? tabButtons : panes),
    saveUi: () => { saves += 1; },
  };
  const factory = new Function(...Object.keys(deps), `
    ${SNIPPET}
    return { sideTabName, renderSideTabs, applySideTab, SIDE_TABS };
  `);
  return { ...factory(...Object.values(deps)), tabButtons, panes, state, saves: () => saves };
}

function visiblePanes(panes) {
  return panes.filter((p) => !p.hidden).map((p) => p.dataset.sidePane);
}

// ── 逻辑 ────────────────────────────────────────────────────────────────
console.log('side tabs');

{
  const h = makeHarness('chat');
  check('sideTabName 接受 chat / workspace', h.sideTabName('chat') === 'chat'
    && h.sideTabName('workspace') === 'workspace');
  check('sideTabName 非法值回落到 chat', h.sideTabName('nope') === 'chat'
    && h.sideTabName(undefined) === 'chat');
}

{
  const h = makeHarness('chat');
  h.renderSideTabs('workspace');
  check('选中 workspace 时只有 workspace pane 可见', visiblePanes(h.panes).join(',') === 'workspace',
    JSON.stringify(visiblePanes(h.panes)));
  const sel = h.tabButtons.map((b) => `${b.dataset.sideTab}:${b.attrs['aria-selected']}`);
  check('tab 的 aria-selected 跟着切换', sel.join(',') === 'chat:false,workspace:true', sel.join(','));

  h.renderSideTabs('chat');
  check('切回 chat 时 pane 也切回', visiblePanes(h.panes).join(',') === 'chat');
}

{
  const h = makeHarness('chat');
  h.applySideTab('workspace', { persist: true });
  check('applySideTab 写回 state.ui.sideTab', h.state.ui.sideTab === 'workspace', String(h.state.ui.sideTab));
  check('applySideTab(persist) 调到 saveUi', h.saves() === 1, String(h.saves()));
  h.applySideTab('chat');
  check('不传 persist 时不落盘', h.saves() === 1, String(h.saves()));
}

// ── index.html 结构 ─────────────────────────────────────────────────────
console.log('index.html');

check('存在 tab 容器 #sideTabs', /id="sideTabs"/.test(HTML));
check('两个 tab 都在', /data-side-tab="chat"/.test(HTML) && /data-side-tab="workspace"/.test(HTML));
check('两个 pane 都在', /data-side-pane="chat"/.test(HTML) && /data-side-pane="workspace"/.test(HTML));
check('聊天 pane 里有会话列表 #chatList', /id="sideChatPane"[\s\S]*id="chatList"/.test(HTML));
check('工作区 pane 里有工作区分组 #wsGroups', /id="sideWsPane"[\s\S]*id="wsGroups"/.test(HTML));
check('聊天区有一键删除按钮 #chatClearBtn', (HTML.match(/id="chatClearBtn"/g) || []).length === 1);
check('旧的「聊天区展开」开关已移除', !/id="uiChatSection"/.test(HTML));
check('旧的「工作区展开」开关已移除', !/id="uiWorkspaceSection"/.test(HTML));
check('旧的 section 折叠按钮已移除', !/id="chatSectionToggle"/.test(HTML) && !/id="wsSectionToggle"/.test(HTML));

// 聊天列表：标题 + 列表同属一个滚动容器，靠滚动自动续页，不再显示提示文字。
check('聊天区标题与列表在同一个滚动容器 #chatBox 内',
  /id="chatBox"[\s\S]*class="ws-list-head chat-head"[\s\S]*id="chatList"/.test(HTML));
check('不再显示「继续滚动加载更多」按钮',
  !/id="chatSentinel"/.test(HTML) && !/chat\.loadMore/.test(SRC) && !/继续滚动加载更多/.test(SRC));
check('聊天面板滚动到底部附近自动加载下一页',
  /chatBox\.addEventListener\('scroll'/.test(SRC) && /function fillChatListIfNeeded/.test(SRC));
check('标题可点击重命名，并提供 AI 重新生成',
  /id="renameModal"/.test(HTML) && /id="renameInput"/.test(HTML)
  && /id="renameRegenerate"/.test(HTML) && /function openRenameModal/.test(SRC)
  && /function regenerateConversationTitle/.test(SRC));
check('问答按会话保存，切换会话会恢复对应的待回答问题',
  /pendingQuestions: \{\}/.test(SRC) && /function loadPendingQuestions/.test(SRC)
  && /\/questions`/.test(SRC));
check('askUser 使用会话内底部抽屉而不是模态框',
  /id="askDrawer"/.test(HTML) && /ask-drawer/.test(SRC)
  && !/askUserModal/.test(SRC));
check('左侧运行中在有 askUser 时显示回答按钮',
  /data-run-ask/.test(SRC) && /pending_question/.test(SRC)
  && /active-run-ask/.test(SRC));
check('进程耗时按秒数格式化（不再把 elapsed_s 当时间戳）',
  /function durationShort/.test(SRC)
  && /durationShort\(it\.elapsed_s\)/.test(SRC)
  && /durationShort\(item\.elapsed_s\)/.test(SRC)
  && !/elapsedShort\(it\.elapsed_s \* 1000\)/.test(SRC));
check('权限授权也支持左侧运行中闪烁按钮与直达',
  /pending_approval/.test(SRC) && /data-run-approval/.test(SRC)
  && /function loadPendingApprovals/.test(SRC)
  && /\/approvals\?status=pending/.test(SRC));

// app.js 里不该再残留旧的折叠状态写法
check('app.js 不再用 chatCollapsed', !/chatCollapsed/.test(SRC));
check('app.js 不再用 wsCollapsed', !/wsCollapsed/.test(SRC));

// 收缩只由用户点按钮触发（2.0.8.2 修正）：没有自动收缩。
check('默认不整栏收缩侧栏', /sidebarCollapsed: false/.test(SRC));
check('不再有自动收起工作区项目的 collapseWorkspaceGroups',
  !/collapseWorkspaceGroups/.test(SRC));
check('不再有被丢弃的 collapseSidebar 自动折叠函数',
  !/function collapseSidebar\(\)/.test(SRC));
check('工作区项目默认收起（wsGroupOpen 只看「被展开过」的记录）',
  /return map\[wsId\] === true;/.test(SRC));
check('工作区展开状态不跨启动持久化',
  /const \{ wsOpen, \.\.\.rest \} = state\.ui;/.test(SRC) && /state\.ui\.wsOpen = \{\};/.test(SRC));
check('工作区展开 / 收起可由 chevron 或名称区域触发',
  /data-ws-toggle/.test(SRC) && /function toggleWsGroup\(wsId\)/.test(SRC));
check('点工作区名称区域也能展开 / 收起',
  /if \(wsId === workspaceId\(\)\) \{\s*toggleWsGroup\(wsId\);/.test(SRC));
check('对话大纲不再悬停自动展开 / 离开自动收起',
  !/bindOutlineAutoHide|scheduleOutlineHide/.test(SRC));

if (failures) {
  console.error(`\n${failures} 项失败`);
  process.exit(1);
}
console.log('\nall ok');

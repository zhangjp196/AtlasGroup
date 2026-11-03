/**
 * 记忆入口回归测试（无需浏览器 / jsdom）。
 * 从 web/assets/app.js 原样抽出 syncMemoryEntry / openMemDrawer，用最小 DOM 桩驱动：
 *   - 纯聊天工作区：侧栏 rail 的 Memory 按钮隐藏，dock 里残留的 mem 标签被摘掉
 *     （V3 顶栏收敛后记忆入口只在侧栏 rail / 会话信息抽屉）
 *   - 普通工作区：入口可见、面板照常打开，既有的 mem 标签不会被误关
 *   - openMemDrawer 在纯聊天工作区直接返回，不会把空面板拉出来
 *
 * 运行：node tests/js/memory_entry_test.js
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
    console.error(`未能从 app.js 中定位 ${startMark}`);
    process.exit(1);
  }
  return SRC.slice(start, end);
}

const ENTRY_SNIPPET = slice('function syncMemoryEntry(chatOnly) {', 'function renderWorkspaceNav() {');
const DRAWER_SNIPPET = slice('async function openMemDrawer(wsId) {', '/* ── CodeGraph panel');

function makeHarness(opts) {
  const options = opts || {};
  const railBtn = { hidden: false };
  const closedTabs = [];
  const openedPanels = [];
  const deps = {
    $: () => null,
    document: {
      querySelectorAll: (sel) => (sel === '[data-sidebar-action="memory"]' ? [railBtn] : []),
    },
    isChatOnlyWorkspace: () => !!options.chatOnly,
    dockOpenTabs: () => (options.openTabs || []).slice(),
    closeDockTab: (name) => { closedTabs.push(name); },
    // openMemDrawer 用到的依赖
    panelEl: (name) => (name === 'mem' ? { id: 'panel-mem' } : null),
    isMemDrawerOpen: () => !!options.memOpen,
    closeMemDrawer: () => { closedTabs.push('mem-close'); },
    openPanel: (name) => { openedPanels.push(name); return true; },
    state: { ui: { memView: 'list' } },
    refreshMem: async () => {},
    renderMemView: () => {},
    renderMemSummary: () => {},
    startRebuildPoll: () => {},
    api: async () => ({ status: 'idle' }),
  };
  const keys = Object.keys(deps);
  const factory = new Function(...keys, `
    ${ENTRY_SNIPPET}
    ${DRAWER_SNIPPET}
    return { syncMemoryEntry, openMemDrawer };
  `);
  const api = factory(...keys.map((k) => deps[k]));
  return { api, railBtn, closedTabs, openedPanels, deps };
}

let failed = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  OK   ${name}`);
  else { failed += 1; console.log(`  FAIL ${name}${extra ? ` — ${extra}` : ''}`); }
}

async function main() {
  // 0) V3：顶栏不再有记忆按钮（已收进会话信息抽屉）
  check('顶栏没有记忆按钮', !HTML.includes('id="topMemBtn"'));
  check('JS 不再引用顶栏记忆按钮', !SRC.includes("$('#topMemBtn')"));

  // 1) 纯聊天工作区：入口全隐藏，残留的 mem 标签被摘掉
  {
    const h = makeHarness({ chatOnly: true, openTabs: ['workspace', 'mem'], memOpen: true });
    h.api.syncMemoryEntry();
    check('纯聊天：侧栏 rail 记忆按钮隐藏', h.railBtn.hidden === true);
    check('纯聊天：dock 里残留的 mem 标签被关闭',
      h.closedTabs.length === 1 && h.closedTabs[0] === 'mem', JSON.stringify(h.closedTabs));
  }

  // 2) 普通工作区：入口可见，已打开的 mem 标签保持不动
  {
    const h = makeHarness({ chatOnly: false, openTabs: ['mem'] });
    h.api.syncMemoryEntry();
    check('普通：侧栏按钮可见', h.railBtn.hidden === false);
    check('普通：不会关闭 mem 标签', h.closedTabs.length === 0, JSON.stringify(h.closedTabs));
  }

  // 3) 显式传参优先于全局工作区状态
  {
    const h = makeHarness({ chatOnly: false, openTabs: [] });
    h.api.syncMemoryEntry(true);
    check('显式 chatOnly=true 时同样隐藏', h.railBtn.hidden === true);
  }

  // 4) openMemDrawer：纯聊天工作区直接返回，不创建 state.mem、不开面板
  {
    const h = makeHarness({ chatOnly: true });
    await h.api.openMemDrawer('ws-chat');
    check('纯聊天：openMemDrawer 不开面板', h.openedPanels.length === 0, JSON.stringify(h.openedPanels));
    check('纯聊天：openMemDrawer 不初始化记忆状态', h.deps.state.mem === undefined);
  }

  // 5) openMemDrawer：普通工作区照常打开
  {
    const h = makeHarness({ chatOnly: false });
    await h.api.openMemDrawer('ws-code');
    check('普通：openMemDrawer 打开 mem 面板',
      h.openedPanels.length === 1 && h.openedPanels[0] === 'mem', JSON.stringify(h.openedPanels));
    check('普通：记忆状态按工作区初始化', !!h.deps.state.mem && h.deps.state.mem.ws === 'ws-code');
  }

  console.log(failed ? `\n${failed} 项失败` : '\n全部通过');
  process.exit(failed ? 1 : 0);
}

main();

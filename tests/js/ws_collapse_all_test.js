/**
 * 「一键收缩全部工作区」回归测试（无需浏览器 / jsdom）。
 *
 * 约定：
 *   - 侧栏「工作区」面板表头有一个常显按钮 #wsCollapseAllBtn（i-collapse-all 图标）；
 *   - 点击后把所有 .ws-group 的会话预览收起、chevron 的 aria-expanded 置 false，
 *     并清空 state.ui.wsOpen（不落盘）；
 *   - 只收起分组，不动整栏侧栏（sidebarCollapsed）也不动右侧 dock。
 *
 * 运行：node tests/js/ws_collapse_all_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');

function slice(startMark, endMark) {
  const start = SRC.indexOf(startMark);
  const end = SRC.indexOf(endMark);
  if (start < 0 || end < 0 || end <= start) {
    console.error(`未能从 app.js 中定位 ${startMark} → ${endMark}`);
    process.exit(1);
  }
  return SRC.slice(start, end);
}

const SNIPPET = slice('  // 一键收缩：把侧栏', '  function renderWorkspaceGroups() {');

let failures = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function makeHarness({ wsOpen = { 'ws-a': true }, groups = 2 } = {}) {
  const makeGroup = (id, open) => {
    const preview = { hidden: !open };
    const chevron = {
      attrs: {},
      setAttribute(k, v) { this.attrs[k] = v; },
    };
    return {
      id,
      preview,
      chevron,
      querySelector: (sel) => (sel === '.ws-group-recent' ? preview : chevron),
    };
  };
  const nodes = Array.from({ length: groups }, (_, i) => makeGroup(`ws-${i}`, true));
  const state = { ui: { wsOpen: { ...wsOpen } } };
  const deps = {
    state,
    $$: (sel) => (sel === '#wsGroups .ws-group' ? nodes : []),
    t: (key) => key,
  };
  const factory = new Function(...Object.keys(deps), `
    ${SNIPPET}
    return { collapseAllWsGroups };
  `);
  return { ...factory(...Object.values(deps)), nodes, state };
}

// ── 逻辑 ────────────────────────────────────────────────────────────────
console.log('[ws-collapse-all] collapseAllWsGroups');

{
  const h = makeHarness({ groups: 3 });
  const n = h.collapseAllWsGroups();
  check('返回收起的分组数量', n === 3, String(n));
  check('所有分组预览都 hidden', h.nodes.every((g) => g.preview.hidden === true));
  check('所有 chevron aria-expanded=false',
    h.nodes.every((g) => g.chevron.attrs['aria-expanded'] === 'false'));
  check('chevron 标题切到「展开」文案',
    h.nodes.every((g) => g.chevron.attrs.title === 'conv.expand'));
  check('清空 state.ui.wsOpen', Object.keys(h.state.ui.wsOpen).length === 0,
    JSON.stringify(h.state.ui.wsOpen));
}

{
  const h = makeHarness({ groups: 0 });
  check('没有分组时安全返回 0', h.collapseAllWsGroups() === 0);
}

check('collapseAllWsGroups 不碰侧栏折叠 / 右侧面板',
  !/sidebarCollapsed/.test(SNIPPET) && !/dockCollapsed/.test(SNIPPET)
  && !/closeDockTab|resetWorkspaceDock/.test(SNIPPET));
check('collapseAllWsGroups 不落盘（wsOpen 只在本次运行有效）',
  !/saveUi\(/.test(SNIPPET));

// ── index.html / app.js / app.css 结构 ─────────────────────────────────
console.log('[ws-collapse-all] 结构');

check('工作区表头有一键收缩按钮', /id="wsCollapseAllBtn"/.test(HTML)
  && (HTML.match(/id="wsCollapseAllBtn"/g) || []).length === 1);
check('按钮位于工作区 pane 的 side-head-tools 里',
  /id="sideWsPane"[\s\S]*class="side-head-tools"[\s\S]*id="wsCollapseAllBtn"[\s\S]*id="wsListRefresh"/.test(HTML));
check('按钮有中文可翻译的 title / aria-label',
  /id="wsCollapseAllBtn"[^>]*data-i18n-title="workspace.collapseAll"[^>]*data-i18n-aria-label="workspace.collapseAll"/.test(HTML));
check('使用了专用图标 i-collapse-all',
  /<symbol id="i-collapse-all"/.test(HTML) && /href="#i-collapse-all"/.test(HTML));
check('i18n 里有中英文案',
  /'workspace\.collapseAll': 'Collapse all workspaces'/.test(SRC)
  && /'workspace\.collapseAll': '收缩全部工作区'/.test(SRC));
check('点击绑定到 collapseAllWsGroups',
  /wsCollapseAllBtn[\s\S]{0,80}?addEventListener\('click', \(\) => collapseAllWsGroups\(\)\)/.test(SRC));
check('按钮常显（不依赖 .ws-list-actions 的 hover 才出现）',
  /\.ws-list-head \.ws-collapse-all \{[^}]*color/.test(CSS)
  && !/\.ws-list-head:hover \.ws-collapse-all/.test(CSS));
check('聊天 pane 没有这个按钮（只在工作区 pane）',
  (HTML.match(/wsCollapseAllBtn/g) || []).length === 1);
check('没有工作区分组时按钮置灰',
  /collapseBtn\.disabled = disabled;/.test(SRC)
  && /const disabled = !items\.length;/.test(SRC));

if (failures) {
  console.error(`\n${failures} 项失败`);
  process.exit(1);
}
console.log('\nall ok');

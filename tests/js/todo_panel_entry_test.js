/**
 * 任务清单面板：位置（进程面板下方）+ 常驻入口回归测试（无需浏览器 / jsdom）。
 *
 * 用户反馈（2026-11-02）：「任务面板 调整到进程下方，目前也没有入口」。
 *
 * 原状：任务清单是右侧 dock 里的一个标签面板（#panel-todo）。dock 默认收起，
 * 标签没被打开过时，唯一入口是 AI 输出头像旁的 chip——而那个 chip 只在模型
 * 调用过 todo 工具后才出现，于是用户「找不到入口」。
 *
 * 现在锁定：
 *   1. 任务面板在对话区左上角的浮动列 .chat-floats 里，排在进程面板**之后**；
 *   2. 顶栏有常驻入口 #topTodoBtn（带未完成数徽标 #topTodoCount）；
 *   3. 任务面板已从 dock 体系移除（DOCK_PANELS / #panel-todo 不再有它）；
 *   4. 开关 / 收起都是纯 DOM 操作，不再依赖 openPanel('todo')；
 *   5. AI 输出头像旁的 chip 仍然能开同一个面板。
 *
 * 运行：node tests/js/todo_panel_entry_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');
const JS = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function cssBlock(src, selector) {
  const start = src.indexOf(`\n${selector} {`);
  if (start < 0) return '';
  const braceStart = src.indexOf('{', start);
  let depth = 0;
  for (let i = braceStart; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(braceStart + 1, i);
    }
  }
  return '';
}

// ── 1. DOM 位置：进程在上，任务清单紧挨着它下面 ─────────────────────────
{
  const floats = HTML.indexOf('id="chatFloats"');
  const process = HTML.indexOf('id="processPanel"');
  const todo = HTML.indexOf('id="todoPanel"');
  const column = HTML.indexOf('class="chat-column"');
  check('浮动列容器存在', floats > 0);
  check('进程面板在浮动列里', process > floats);
  check('任务面板在浮动列里（同一列，不是右侧 dock）', todo > floats && todo < column);
  check('任务面板排在进程面板之后（下方）', todo > process);
  check('旧的右侧 dock 面板已移除', !HTML.includes('id="panel-todo"'));
}

// ── 2. 常驻入口 ────────────────────────────────────────────────────────
{
  const btn = HTML.indexOf('id="topTodoBtn"');
  check('顶栏有任务清单入口 #topTodoBtn', btn > 0);
  check('入口在顶栏（topbar-actions 内、chat-shell 之前）',
    btn > 0 && btn < HTML.indexOf('id="chatShell"'));
  check('入口带未完成数徽标 #topTodoCount', HTML.includes('id="topTodoCount"'));
  check('入口有 aria-pressed（表示面板开合）', /id="topTodoBtn"[^>]*aria-pressed=/.test(HTML));
  check('入口绑定点击：开着就关、关着就开',
    JS.includes("$('#topTodoBtn').addEventListener('click'")
    && JS.includes('if (isTodoDrawerOpen()) closeTodoDrawer();')
    && JS.includes('else openTodoDrawer();'));
  check('徽标由 updateTodoBadge 维护（进行中高亮）',
    JS.includes("const topCount = $('#topTodoCount');")
    && JS.includes("topCount.classList.toggle('is-live', live);"));
}

// ── 3. 已退出 dock 体系 ────────────────────────────────────────────────
{
  const dockLine = (JS.match(/const DOCK_PANELS = \[[^\]]*\];/) || [''])[0];
  check('DOCK_PANELS 不再包含 todo', dockLine && !/'todo'/.test(dockLine), dockLine);
  check('不再调用 openPanel(\'todo\')', !JS.includes("openPanel('todo')"));
  check('不再调用 closePanel(\'todo\')', !JS.includes("closePanel('todo')"));
  check('dock 标签徽标不再管 todo', JS.includes("['vcs'].forEach((name) => {"));
  check('开关直接控制面板元素显隐',
    JS.includes('function todoPanelEl() { return $(\'#todoPanel\'); }')
    && JS.includes('el.hidden = false;') && JS.includes('if (el) el.hidden = true;'));
}

// ── 4. 收起 / 关闭 / 记忆打开状态 ───────────────────────────────────────
{
  check('有独立收起按钮 #todoSectionToggle', HTML.includes('id="todoSectionToggle"'));
  check('收起状态只由点击切换',
    JS.includes('state.ui.todoCollapsed = !state.ui.todoCollapsed;'));
  check('打开状态写进 UI 偏好（重启后保持）',
    JS.includes('state.ui.todoOpen = true;') && JS.includes('if (state.ui.todoOpen) openTodoDrawer();'));
  check('Esc / closeTopLayer 仍能关掉面板', JS.includes('if (isTodoDrawerOpen()) { closeTodoDrawer(); return true; }'));
  check('对话内任务卡片仍能开面板', JS.includes("const todoOpen = e.target.closest('[data-open-todo]');"));
  check('对话内任务卡片与 DSH 同构（标题 / 摘要 / 折叠）',
    JS.includes('function todoCardHtml()')
    && JS.includes('data-todo-card-meta')
    && JS.includes('data-todo-card-toggle'));
}

// ── 5. 样式：卡片外观 + 收起小浮标 ──────────────────────────────────────
{
  const floats = cssBlock(CSS, '.chat-floats');
  check('浮动列竖直排布', /flex-direction:\s*column/.test(floats), floats);
  check('浮动列有间距（两块卡片不粘连）', /gap:\s*\d+px/.test(floats), floats);

  const card = cssBlock(CSS, '.process-panel,\n.todo-panel');
  check('任务面板与进程面板共用卡片外观',
    /border:/.test(card) && /border-radius:/.test(card) && /background:/.test(card), card);

  check('没有任务面板内容时整块 hidden',
    CSS.includes('.process-panel[hidden],\n.todo-panel[hidden] { display: none; }'));
  check('收起后是左对齐小浮标（不撑满整列）',
    /\.todo-panel\.is-collapsed[^{]*\{[^}]*align-self:\s*flex-start/.test(CSS));
  check('收起隐藏标题文字与内容区',
    /\.todo-panel\.is-collapsed \.todo-panel-title > span/.test(CSS)
    && /\.todo-panel\.is-collapsed \.todo-panel-body/.test(CSS));
  check('顶栏徽标定位在图标右上角',
    /\.icon-btn\.has-count \{ position:\s*relative/.test(CSS)
    && /\.icon-count \{[^}]*position:\s*absolute/.test(CSS));
}

console.log(failures === 0
  ? '\n[todo-panel-entry] 全部通过'
  : `\n[todo-panel-entry] ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);

/**
 * 任务清单卡片 / 计划流程移除的静态回归（无需浏览器）。
 *
 * 用户要求（2026-11-03）：
 *   - 「计划流程面板和相关功能彻底移除，整合到任务清单即可」；
 *   - 「任务清单也要调整参考 dsh」——整份覆盖式 todo_write，状态三态，
 *     对话内渲染成卡片（标题 + 进度摘要 + 可折叠条目）。
 *
 * 这里钉住结构层面，避免以后又把计划流程加回来或把卡片退回成小 chip。
 *
 * 运行：node tests/js/todo_card_test.js
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

// ── 1. 计划流程彻底移除 ────────────────────────────────────────────────
{
  check('顶栏不再有计划流程按钮', !HTML.includes('id="topPlanBtn"'));
  check('dock 不再有 #panel-plan', !HTML.includes('id="panel-plan"'));
  check('DOCK_PANELS 不含 plan', !/const DOCK_PANELS = \[[^\]]*'plan'/.test(JS));
  for (const fn of ['renderPlanDrawer', 'renderPlanFlow', 'planFlowSvg', 'collectPlans',
    'updatePlanBadge', 'openPlanDrawer', 'applyPlanZoom', 'renderPlan']) {
    check(`不再有 ${fn}`, !JS.includes(`function ${fn}(`));
  }
  check('不再处理 plan 事件', !/if \(evt\.plan\)/.test(JS));
  check('不再有 plan 相关 i18n 文案', !/'plan\.[a-zA-Z]+':/.test(JS));
  check('不再有 .plan- 样式', !/\.plan-(body|list|card|flow|step|zoom|summary)/.test(CSS));
}

// ── 2. 任务卡片（DSH 同构）─────────────────────────────────────────────
{
  check('有 todoCardHtml 渲染函数', JS.includes('function todoCardHtml()'));
  check('卡片带标题与进度摘要', JS.includes('todo.cardTitle') && JS.includes('data-todo-card-meta'));
  check('卡片可折叠', JS.includes('data-todo-card-toggle') && JS.includes('state.todoCardOpen'));
  check('卡片挂在助手输出下（不是头像旁的 chip）',
    JS.includes('showTodo ? todoCardHtml() : \'\''));
  check('流式过程中卡片能补齐', JS.includes('function syncLiveTodoCard('));
  check('识别 todo_write 工具名', JS.includes("new Set(['todo_write', 'todo', 'todo_list', 'task_list'])"));
  check('状态只认 DSH 三态',
    JS.includes('in_progress: 0, pending: 1, completed: 2'));
  check('进行中 / 已完成有独立样式',
    CSS.includes('.turn-todo-item.is-in_progress') && CSS.includes('.turn-todo-item.is-completed'));
  check('完成条目划掉', /is-completed .turn-todo-text[^{]*\{[^}]*line-through/.test(CSS));
}

// ── 3. REST / 事件用 content + DSH 状态 ────────────────────────────────
{
  check('新增任务提交 content', JS.includes("JSON.stringify({ content })"));
  check('面板状态文案用 DSH 键',
    JS.includes("t('todo.status.' + status)") && !JS.includes("todo.status.doing"));
  check('事件里的清单直接进 state', JS.includes('state.todos = upd.items'));
}

if (failures) {
  console.error(`\n[todo-card] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[todo-card] 全部通过');

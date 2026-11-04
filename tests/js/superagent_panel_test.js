/**
 * Superagent 事项面板回归：新面板、来源准入、派活与注意力。
 * 运行：node tests/js/superagent_panel_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/v3/index.html'), 'utf8');
const JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/v3/app.css'), 'utf8');
const SERVICE = fs.readFileSync(path.join(ROOT, 'services/agent_zero/services/work_service.py'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

check('Superagent 是独立主面板', HTML.includes('data-section="work"') && HTML.includes('id="workBadge"'));
check('Superagent 是默认首页', /class="rail-btn active" data-section="work"/.test(HTML)
  && /section:\s*'work'/.test(JS) && JS.includes("work: '工单'"));
check('Superagent 汇总会话里的待处理', JS.includes('function attentionInboxHtml(')
  && JS.includes('data-attn-conv') && JS.includes('需要你处理'));
check('角色 / 群组 / 工作区 / 会话都能交给 Superagent',
  JS.includes("label: '新建工单…'") && JS.includes('data-role-superagent')
  && JS.includes('data-group-superagent') && JS.includes('data-ws-superagent'));
check('列表有 待办 / 需要你 / 进行中 / 已完成 过滤', JS.includes("['open', '待办']")
  && JS.includes("['attention', '需要你']") && JS.includes("['working', '进行中']"));
check('需要你时 rail 显示角标', JS.includes('function paintWorkBadge(')
  && JS.includes("badge.hidden = n <= 0"));
check('事项详情有派活 / 打开会话 / 完成 / 关闭', JS.includes('data-work-dispatch')
  && JS.includes('data-work-open') && JS.includes('data-work-done') && JS.includes('data-work-close'));
check('新建 / 编辑 / 删除 / 策略面板', JS.includes('async function newWorkItem(')
  && JS.includes('async function editWorkItem(') && JS.includes('async function deleteWorkItem(')
  && JS.includes('async function openWorkSettings('));
check('派活接口与后台 worker 触发', JS.includes('/dispatch')
  && SERVICE.includes('async def dispatch_item(') && SERVICE.includes('conv_worker.request_run'));
check('事项状态自动跟随 run', SERVICE.includes('def _sync_item(')
  && SERVICE.includes('needs_attention') && SERVICE.includes('pending_approvals'));
check('来源准入策略', SERVICE.includes('def admission(') && SERVICE.includes('allowed_sources')
  && SERVICE.includes('allowed_actors') && SERVICE.includes('def save_policy('));
check('事项按工作区 / 角色 / 群组隔离', SERVICE.includes('workspace_id') && SERVICE.includes('role_id')
  && SERVICE.includes('team_id') && SERVICE.includes("source=\"superagent\""));
check('面板每 10 秒同步一次状态', JS.includes('loadWorkItems({ quiet: true })')
  && JS.includes('}, 10000);'));
check('事项样式', CSS.includes('.work-filters') && CSS.includes('.work-attention') && CSS.includes('.work-stats'));

if (failures) {
  console.error(`\n[superagent] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[superagent] 全部通过');

/**
 * 执行画布：DAG、子代理节点、进度与活动时间线。
 * 运行：node tests/js/agent_canvas_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/v3/index.html'), 'utf8');
const JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/v3/app.css'), 'utf8');
const GRAPH = fs.readFileSync(path.join(ROOT, 'services/agent_zero/services/graph_service.py'), 'utf8');
const ROUTES = fs.readFileSync(path.join(ROOT, 'services/agent_zero/routes/task_routes.py'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

check('会话顶栏有执行画布入口', JS.includes('data-conv-graph') && JS.includes('function openAgentCanvas('));
check('画布覆盖层与活动侧栏', HTML.includes('id="agentCanvas"')
  && HTML.includes('id="agentCanvasStage"') && HTML.includes('id="agentActivity"'));
check('DAG 节点 + 连线', JS.includes('function agentNodeHtml(') && JS.includes('agent-edge')
  && CSS.includes('.agent-node') && CSS.includes('.agent-edge'));
check('子代理状态 / 进度 / 指标', JS.includes('AGENT_STATUS') && JS.includes('agent-progress')
  && JS.includes('agent-metrics'));
check('执行画布接口', ROUTES.includes('/conversations/{conv_id}/graph')
  && GRAPH.includes('def conversation_graph('));
check('从 run 的 subagent_event 聚合', GRAPH.includes('subagent_event')
  && JS.includes('evt.subagent_event'));
check('历史 trace 兜底', GRAPH.includes('display.get("type") != "subagent"')
  && GRAPH.includes('_trace_nodes'));
check('活动 / 即将 / 刚刚发生', JS.includes('agent-feed') && HTML.includes('id="agentUpcoming"')
  && HTML.includes('id="agentJustHappened"'));
check('过滤 / 缩放 / 回放', JS.includes('data-graph-filter') && JS.includes('function replayAgentCanvas(')
  && JS.includes('agentCanvasZoom'));
check('自动刷新与关闭清理', JS.includes('setInterval(() => {')
  && JS.includes('refreshAgentCanvas') && JS.includes('function closeAgentCanvas('));

if (failures) {
  console.error(`\n[agent-canvas] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[agent-canvas] 全部通过');

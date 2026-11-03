/**
 * 会话信息抽屉（IM 群资料）静态 + 渲染回归（无需浏览器）。
 *
 * 覆盖：
 *   1. 顶栏入口 / 抽屉骨架 / 样式存在；
 *   2. 绑定团队时渲染群名、模式、上限、成员与解绑入口；
 *   3. 未绑定团队时渲染「单聊」、绑定已有群、新建群聊入口；
 *   4. 数据来源是 /teams 与 /conversations/{id}/team，改动走 PATCH / bind。
 *
 * 运行：node tests/js/conv_drawer_test.js
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

// ── 1. 骨架 ────────────────────────────────────────────────────────────
{
  check('顶栏有会话信息入口', HTML.includes('id="convInfoBtn"'));
  check('抽屉与遮罩存在', HTML.includes('id="convDrawer"')
    && HTML.includes('id="convDrawerBackdrop"')
    && HTML.includes('id="convDrawerBody"'));
  check('抽屉带 aria 语义', /id="convDrawer"[^>]*role="dialog"[^>]*aria-modal="true"/.test(HTML));
  check('抽屉样式存在', CSS.includes('.side-drawer {') && CSS.includes('.conv-member {'));

  check('入口 / 关闭 / 遮罩都绑定', JS.includes("$('#convInfoBtn').addEventListener('click'")
    && JS.includes("close.addEventListener('click', closeConvDrawer)")
    && JS.includes("backdrop.addEventListener('click', closeConvDrawer)"));
  check('Esc 能关抽屉', JS.includes('if (isConvDrawerOpen()) { closeConvDrawer(); return true; }'));
  check('切换 / 新建会话收起抽屉', JS.includes('closeConvDrawer();\n    closeStream();'));
}

// ── 2. 数据与写操作 ────────────────────────────────────────────────────
{
  check('读取会话绑定团队', JS.includes('/conversations/${encodeURIComponent(convId)}/team'));
  check('读取工作区团队列表', JS.includes('/teams?workspace_id='));
  check('PATCH 团队（名称 / 模式 / 上限 / 成员）', JS.includes('payload') === false
    ? JS.includes("method: 'PATCH'")
    : JS.includes('async function patchConvTeam(payload)'));
  check('绑定 / 解绑走 REST', JS.includes('/bind`, {') && JS.includes('/unbind`, {'));
  check('新建群聊走 POST /teams', JS.includes("api('/teams', {"));
  check('默认主持名走 i18n', JS.includes("leader: { name: t('conv.leaderName') }"));
}

// ── 3. 真实渲染（抽函数 + 最小 DOM 桩）────────────────────────────────
function grab(name) {
  const start = JS.indexOf(`function ${name}(`);
  if (start < 0) throw new Error('missing ' + name);
  let i = JS.indexOf('{', start);
  let depth = 0;
  for (let j = i; j < JS.length; j += 1) {
    if (JS[j] === '{') depth += 1;
    else if (JS[j] === '}') {
      depth -= 1;
      if (depth === 0) return JS.slice(start, j + 1);
    }
  }
  throw new Error('unclosed ' + name);
}

function render(data) {
  const code = [
    "const holders = {};",
    "const CONV_DRAWER_MODES = ['orchestrate', 'roundtable', 'single'];",
    'const convDrawerData = ' + JSON.stringify(data) + ';',
    "const state = { conv: { model_code: 'fast' }, activeModel: 'fast' };",
    "const t = (k, p) => k + (p ? JSON.stringify(p) : '');",
    "const escapeHtml = (v) => String(v).replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));",
    'const escAttr = escapeHtml;',
    "const currentWorkspace = () => ({ name: '默认空间', vcs: true });",
    "const isChatOnlyWorkspace = () => false;",
    "const chatTitle = () => '研发群会话';",
    "const document = { getElementById: () => null };",
    "const $ = (sel) => { holders[sel] = holders[sel] || { innerHTML: '' }; return holders[sel]; };",
    grab('convMemberBrief'), grab('convMemberHtml'), grab('convToolsHtml'), grab('renderConvDrawer'),
    'renderConvDrawer();',
    "globalThis.__convDrawerHtml = holders['#convDrawerBody'].innerHTML;",
  ].join('\n');
  eval(code);
  const html = globalThis.__convDrawerHtml;
  delete globalThis.__convDrawerHtml;
  return html;
}

const teamHtml = render({
  team: {
    id: 'team-1', name: '研发群', mode: 'roundtable', max_speakers: 6,
    members: [
      { id: 'r1', name: '后端', expertise: 'API', tools: ['shell', 'read_file'] },
      { id: 'r2', name: '测试' },
    ],
  },
  teams: [],
});
check('绑定时渲染群名', teamHtml.includes('value="研发群"'));
check('绑定时渲染模式选项', teamHtml.includes('value="roundtable"')
  && teamHtml.includes('conv.mode.orchestrate'));
check('绑定时渲染上限', teamHtml.includes('value="6"'));
check('绑定时渲染成员与工具数', teamHtml.includes('后端')
  && teamHtml.includes('conv.memberTools{"n":2}'));
check('抽屉里有工具入口（工作区 / 版本控制 / 图谱 / 记忆）',
  teamHtml.includes('data-conv-tool="workspace"')
  && teamHtml.includes('data-conv-tool="vcs"')
  && teamHtml.includes('data-conv-tool="codegraph"')
  && teamHtml.includes('data-conv-tool="mem"'));
check('绑定时有移除 / 添加 / 解绑入口',
  teamHtml.includes('data-team-member-remove="r1"')
  && teamHtml.includes('id="convMemberAdd"')
  && teamHtml.includes('id="convTeamUnbind"'));

const singleHtml = render({
  team: null,
  teams: [{ id: 'team-default-ws', name: '默认群聊' }],
});
check('未绑定时显示单聊', singleHtml.includes('conv.single'));
check('未绑定时可绑定已有群', singleHtml.includes('id="convBindTeam"')
  && singleHtml.includes('team-default-ws'));
check('未绑定时可新建群聊', singleHtml.includes('id="convNewTeamName"')
  && singleHtml.includes('id="convNewTeamBtn"'));
check('未绑定时同样有工具入口', singleHtml.includes('data-conv-tool="workspace"'));

if (failures) {
  console.error(`\n[conv-drawer] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[conv-drawer] 全部通过');

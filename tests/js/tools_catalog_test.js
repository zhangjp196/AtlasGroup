/**
 * 设置 → 工具页「工具目录」回归测试（无需浏览器 / jsdom）。
 *
 * 用户反馈（2026-10-30）：「设置-工具 需要先更新下」。
 *
 * 现象：应用默认工作区是纯聊天（没有工作目录），从它切到有目录的工作区后，
 * 「独立工具」（shell / tool_job / vcs / codegraph / workspace）整块是空的。
 * 原因：目录来自 /app/info，它的 has_working_dir 取默认工作区，后端按它把独立
 * 工具整个过滤掉了；页面拿到的是启动时的快照，切工作区不会重新拉。
 *
 * 约定（本测试锁定）：
 *   1. 目录始终列出全部独立工具，纯聊天工作区只置灰（后端改，见
 *      tests/unit/test_tool_packs.py::test_catalog_lists_every_standalone_tool...）；
 *   2. 前端按**当前工作区**（isChatOnlyWorkspace）决定是否置灰，不信目录里的
 *      available（那是默认工作区算出来的，切工作区后就过期了）；
 *   3. 置灰的独立工具带「需要工作目录」提示，不是只剩一个灰块。
 *
 * 运行：node tests/js/tools_catalog_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function extractFn(src, name) {
  let start = src.indexOf(`  async function ${name}(`);
  if (start < 0) start = src.indexOf(`  function ${name}(`);
  if (start < 0) return '';
  const braceStart = src.indexOf('{', start);
  if (braceStart < 0) return '';
  let depth = 0;
  for (let i = braceStart; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return '';
}

const PACK_ICONS = {
  fs: 'folder', net: 'globe', memory: 'brain',
  plan: 'list', skill: 'book', mcp: 'plug',
};

// 后端目录：独立工具全部列出；available 是**默认工作区**（纯聊天）算出来的 false
// （只有 workspace 这种必须落在用户文件夹里的工具才会 workspace_required=true）
const CATALOG = {
  packs: [
    {
      code: 'fs', available: true,
      ops: [{ op: 'read', tool: 'read_file', risk: 'low', workspace_required: false, available: true }],
    },
    {
      code: 'plan', available: true,
      ops: [{ op: 'delegate', tool: 'delegate', risk: 'low',
              workspace_required: false, available: true, conditional: 'team' }],
    },
  ],
  standalone: [
    { code: 'shell', risk: 'medium', workspace_required: false, available: true },
    { code: 'tool_job', risk: 'low', workspace_required: false, available: true },
    { code: 'vcs', risk: 'low', workspace_required: false, available: true },
    { code: 'codegraph', risk: 'low', workspace_required: false, available: true },
    { code: 'workspace', risk: 'medium', workspace_required: true, available: false },
    { code: 'ask_user', risk: 'low', workspace_required: false, available: true },
  ],
};

function render({ chatOnly }) {
  const els = {
    '#toolPackGrid': { innerHTML: '' },
    '#toolStandaloneGrid': { innerHTML: '' },
    '#tabCount-tools': { textContent: '' },
    '#toolChatOnlyNote': { hidden: true },
  };
  const state = { app: { tool_catalog: CATALOG } };
  const $ = (sel) => els[sel] || null;
  const t = (key) => ({
    'tools.needWorkspace': '需要本地文件夹',
    'tools.needTeam': 'target=team 需要先绑定团队',
    'tools.condTeam': '团队',
    'tools.pack.fs': '文件',
    'tools.pack.plan': '计划与协作',
    'tools.chatOnly': '纯聊天',
    'tools.standalone': '独立工具',
  }[key] || key);
  const deps = {
    state, $, t, PACK_ICONS,
    escAttr: (s) => String(s == null ? '' : s),
    escapeHtml: (s) => String(s == null ? '' : s),
    toolLabel: (code) => code,
    toolIcon: () => 'terminal',
    isChatOnlyWorkspace: () => chatOnly,
  };
  const keys = Object.keys(deps);
  const fn = extractFn(SRC, 'renderToolsCatalog');
  const factory = new Function(...keys, `${fn}
    return renderToolsCatalog;`);
  factory(...keys.map((k) => deps[k]))();
  return els;
}

// ── 1. 有工作目录：全部独立工具正常显示 ────────────────────────────────
{
  const els = render({ chatOnly: false });
  const html = els['#toolStandaloneGrid'].innerHTML;
  check('独立工具全部渲染', CATALOG.standalone.every((s) => html.includes(`>${s.code}<`)), html);
  check('不按目录里的 available（默认工作区快照）置灰', !html.includes('is-off'), html);
  check('纯聊天提示隐藏', els['#toolChatOnlyNote'].hidden === true);
}

// ── 2. 纯聊天：只有 workspace 置灰 + 说明原因，但仍在列表里 ────────────
{
  const els = render({ chatOnly: true });
  const html = els['#toolStandaloneGrid'].innerHTML;
  check('纯聊天仍列出全部独立工具', CATALOG.standalone.every((s) => html.includes(`>${s.code}<`)), html);
  const off = CATALOG.standalone.filter((s) => s.workspace_required).map((s) => s.code);
  check('只置灰 workspace（shell / vcs / 命令类照常可用）',
    (html.match(/tool-alone is-off/g) || []).length === off.length, html);
  check('置灰项带「需要本地文件夹」提示', html.includes('需要本地文件夹'), html);
  check('页面级提示显示', els['#toolChatOnlyNote'].hidden === false);
}

// ── 3. 条件可用：delegate 的 team 目标要有标记 ─────────────────────────
{
  const els = render({ chatOnly: false });
  const html = els['#toolPackGrid'].innerHTML;
  check('条件可用动作带「团队」标记', html.includes('tool-cond') && html.includes('团队'), html);
  check('标记上带原因提示', html.includes('target=team 需要先绑定团队'), html);
}

// ── 4. 不再按目录快照里的 chat_only 决定（否则切工作区就错）───────────
{
  const body = extractFn(SRC, 'renderToolsCatalog');
  check('渲染时用当前工作区判断可用性', body.includes('isChatOnlyWorkspace()'), body);
  check('不读目录里的 chat_only 标记', !body.includes('cat.chat_only'), body);
}

console.log(failures === 0
  ? '\n[tools-catalog] 全部通过'
  : `\n[tools-catalog] ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);

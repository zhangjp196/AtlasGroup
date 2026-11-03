/**
 * 会话列表分组回归测试（无需浏览器 / jsdom）。
 * 从 web/assets/app.js 原样抽出 convIsActive / sortConversations /
 * splitConversations / convSectionsHtml，用最小 DOM 桩驱动：
 *
 *   - 进行中在前、完结在后；两组各自按时间倒序（最近活动最上）
 *   - 后端没给 active 时，本地 activeRuns（刚发出的 run）也算进行中
 *   - 空组不渲染标题（不会出现「进行中 0」这种孤立标题）
 *   - 分组标题带 data-i18n，切语言时能被重新翻译
 *
 * 运行：node tests/js/conversation_sections_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

function slice(startMark, endMark) {
  const start = SRC.indexOf(startMark);
  const end = SRC.indexOf(endMark);
  if (start < 0 || end < 0 || end <= start) {
    console.error(`未能从 app.js 中定位 ${startMark} → ${endMark}`);
    process.exit(1);
  }
  return SRC.slice(start, end);
}

// convIsActive → loadWorkspaceMenuPage 之前就是分组那一块（含 convSectionsHtml / convMiniRow）。
const SECTION_SNIPPET = slice('  // 会话是否「进行中」', '  async function loadWorkspaceMenuPage(page) {');

let failures = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function makeHarness() {
  const state = { activeRuns: [] };
  const LABELS = { 'conv.section.active': '进行中', 'conv.section.done': '完结' };
  const deps = {
    state,
    t: (key) => LABELS[key] || key,
    escapeHtml: (s) => String(s == null ? '' : s),
    escAttr: (s) => String(s == null ? '' : s),
    truncate: (s) => String(s == null ? '' : s),
    relativeTime: () => '刚刚',
    conversationTitle: (c) => c.title || '未命名',
  };
  const names = Object.keys(deps);
  // eslint-disable-next-line no-new-func
  const factory = new Function(...names, `${SECTION_SNIPPET}
    return { convIsActive, sortConversations, splitConversations, convSectionsHtml, convMiniRow };`);
  return { api: factory(...names.map((n) => deps[n])), state };
}

const conv = (id, extra) => Object.assign({
  id, title: id, created_at: '2026-10-28 10:00:00', updated_at: '2026-10-28 10:00:00',
}, extra || {});

// ── 1. 进行中优先 + 组内时间倒序 ────────────────────────────────────────
{
  const { api } = makeHarness();
  const items = [
    conv('done-newest', { updated_at: '2026-10-28 15:00:00' }),
    conv('live-oldest', { updated_at: '2026-10-28 09:00:00', active: true }),
    conv('done-oldest', { updated_at: '2026-10-28 12:00:00' }),
    conv('live-newest', { updated_at: '2026-10-28 13:00:00', active: true }),
  ];
  const sorted = api.sortConversations(items).map((c) => c.id);
  check('进行中在前，且组内时间倒序',
    JSON.stringify(sorted) === JSON.stringify(['live-newest', 'live-oldest', 'done-newest', 'done-oldest']),
    JSON.stringify(sorted));

  const { active, done } = api.splitConversations(items);
  check('splitConversations 分成 2 / 2', active.length === 2 && done.length === 2,
    `active=${active.length} done=${done.length}`);
  check('不修改入参数组顺序', items[0].id === 'done-newest', items.map((c) => c.id).join(','));
}

// ── 2. 后端没给 active 时，本地 activeRuns 兜底 ─────────────────────────
{
  const { api, state } = makeHarness();
  state.activeRuns = [{ conversation_id: 'just-sent' }];
  const items = [
    conv('other', { updated_at: '2026-10-28 15:00:00' }),
    conv('just-sent', { updated_at: '2026-10-28 09:00:00' }),
  ];
  check('本地 activeRuns 也算进行中', api.convIsActive(items[1]) === true);
  const sorted = api.sortConversations(items).map((c) => c.id);
  check('刚发出的会话排到最前',
    JSON.stringify(sorted) === JSON.stringify(['just-sent', 'other']), JSON.stringify(sorted));
}

// ── 3. 空组不渲染标题 ──────────────────────────────────────────────────
{
  const { api } = makeHarness();
  const onlyDone = [conv('a'), conv('b')];
  const html = api.convSectionsHtml(onlyDone);
  check('只有完结时不出「进行中」标题', !html.includes('data-conv-group="active"'));
  check('只有完结时出「完结」标题', html.includes('data-conv-group="done"'));

  const onlyActive = [conv('c', { active: true })];
  const html2 = api.convSectionsHtml(onlyActive);
  check('只有进行中时不出「完结」标题', !html2.includes('data-conv-group="done"'));
  check('只有进行中时出「进行中」标题', html2.includes('data-conv-group="active"'));

  check('空列表渲染为空串', api.convSectionsHtml([]) === '');
}

// ── 4. 标题可翻译 + 有计数 + 条目可点 ──────────────────────────────────
{
  const { api } = makeHarness();
  const html = api.convSectionsHtml([conv('a', { active: true }), conv('b')]);
  check('分组标题带 data-i18n',
    html.includes('data-i18n="conv.section.active"') && html.includes('data-i18n="conv.section.done"'));
  check('分组标题带组内计数', html.includes('conv-group-count'));
  check('条目带 data-conv 可点', html.includes('data-conv="a"') && html.includes('data-conv="b"'));
  check('进行中的条目有呼吸点', html.includes('conv-running-dot'));
  check('完结的条目没有呼吸点',
    (html.match(/conv-running-dot/g) || []).length === 1,
    String((html.match(/conv-running-dot/g) || []).length));
}

// ── 5. 工作区预览不渲染「完结」标题（减少左栏 DOM）────────────────────
{
  const { api } = makeHarness();
  const html = api.convSectionsHtml([conv('a', { active: true }), conv('b')], undefined, { doneLabel: false, counts: false });
  check('doneLabel=false 时不出「完结」标题', !html.includes('data-i18n="conv.section.done"'));
  check('doneLabel=false 时仍保留进行中标题与完结条目',
    html.includes('data-i18n="conv.section.active"') && html.includes('data-conv="b"'));
  check('counts=false 时不渲染组内计数', !html.includes('conv-group-count'));
}

if (failures) {
  console.error(`\n会话分组测试失败 ${failures} 项`);
  process.exit(1);
}
console.log('\n会话分组测试全部通过');

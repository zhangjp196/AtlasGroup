/**
 * 消息队列条回归测试（无需浏览器 / jsdom）。
 * 从 web/assets/app.js 原样抽出 renderQueue / queueAction，用最小 DOM 桩驱动：
 *
 *   - 插队按钮与「插队」标签都没了（用户要求：去除插队按钮）
 *   - 每行有上移 / 下移两个按钮，带 data-queue-action=up|down
 *   - 队首的上移按钮、队尾的下移按钮 disabled（点了也没意义）
 *   - 上移 / 下移分别 POST .../move {direction}
 *   - 边界返回 moved=false 时只提示、不算失败
 *
 * 运行：node tests/js/queue_reorder_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

/**
 * 从 app.js 里整段抽出某个具名函数（含函数体）。
 *
 * 不用「起点 → 下一个注释」的切片：注释标记会随重构消失/改名，切片长度也难以
 * 目测，一旦截在函数中间就是 SyntaxError（本测试曾因此整体跑不起来）。改为从
 * 签名后的第一个 `{` 起做括号配平，取到深度归零的那个 `}`。模板字符串里的
 * `${...}` 自带配对，不影响计数。
 */
function extractFn(src, name) {
  // 有的函数带 async 前缀，签名有两种写法
  let start = src.indexOf(`  async function ${name}(`);
  if (start < 0) start = src.indexOf(`  function ${name}(`);
  if (start < 0) return '';
  const braceStart = src.indexOf('{', start);
  if (braceStart < 0) return '';
  let depth = 0;
  for (let i = braceStart; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return '';
}

const snippet = ['queuePreview', 'renderQueue', 'queueAction']
  .map((n) => extractFn(SRC, n))
  .join('\n\n');
const missing = ['queuePreview', 'renderQueue', 'queueAction']
  .filter((n) => !snippet.includes(`function ${n}(`));
if (missing.length) {
  console.error(`未能从 app.js 中完整抽取: ${missing.join(', ')}`);
  process.exit(1);
}

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

const ZH = {
  'queue.count': '{n} 条等待',
  'queue.moveUp': '上移',
  'queue.moveUpTitle': '提前一位执行',
  'queue.moveDown': '下移',
  'queue.moveDownTitle': '推后一位执行',
  'queue.inject': '停止并继续',
  'queue.injectTitle': '停止当前回复，紧接着执行这条消息',
  'queue.edit': '编辑',
  'queue.editTitle': '编辑这条排队消息',
  'queue.cancel': '取消',
  'queue.movedUp': '已上移一位。',
  'queue.movedDown': '已下移一位。',
  'queue.atTop': '已经在队首。',
  'queue.atBottom': '已经在队尾。',
  'queue.injected': '已插入。',
  'queue.stopContinue': '已停止当前回复，这条消息紧接着执行。',
  'queue.cancelled': '已取消。',
  'queue.failed': '队列操作失败：{msg}',
};

function makeHarness(items, opts) {
  const o = opts || {};
  const els = {
    '#queueStrip': { hidden: false },
    '#queueItems': { innerHTML: '' },
    '#queueCount': { textContent: '' },
  };
  const calls = [];
  const toasts = [];
  const editStarts = [];
  const deps = {
    state: { queue: items, currentId: 'conv-1', running: !!o.running, queueRunning: null },
    $: (sel) => (Object.prototype.hasOwnProperty.call(els, sel) ? els[sel] : null),
    t: (key, vars) => {
      let s = ZH[key] || key;
      if (vars && vars.n != null) s = s.replace('{n}', vars.n);
      if (vars && vars.msg != null) s = s.replace('{msg}', vars.msg);
      return s;
    },
    escAttr: (s) => String(s == null ? '' : s),
    escapeHtml: (s) => String(s == null ? '' : s),
    toast: (msg, kind) => toasts.push({ msg, kind }),
    localizeServerMessage: (m) => m,
    renderQueue: () => {},
    loadConversation: async () => {},
    findMessage: (id) => ((o.messages || []).find((m) => m && m.id === id) || null),
    startEditMessage: (id) => editStarts.push(id),
    api: async (url, options) => {
      calls.push({ url, method: (options && options.method) || 'GET',
        body: options && options.body ? JSON.parse(options.body) : null });
      if (o.apiResponse) return o.apiResponse(url, options);
      return { moved: true, items };
    },
  };
  const keys = Object.keys(deps);
  // eslint-disable-next-line no-new-func
  const factory = new Function(...keys, `${snippet}
    return { queuePreview, renderQueue, queueAction };`);
  const api = factory(...keys.map((k) => deps[k]));
  api.renderQueue = api.renderQueue.bind(null);
  // renderQueue 内部读的是 deps.renderQueue？不，它直接调用自身作用域里的函数，
  // 故这里用工厂返回的实现即可。
  return { api, els, calls, toasts, deps, editStarts };
}

const item = (id, extra) => Object.assign({ id, content: `消息 ${id}`, status: 'queued' }, extra || {});

// ── 1. 插队按钮已移除 ──────────────────────────────────────────────────
{
  const h = makeHarness([item('a'), item('b'), item('c')]);
  h.api.renderQueue();
  const html = h.els['#queueItems'].innerHTML;
  check('不再有 prioritize 按钮', !html.includes('data-queue-action="prioritize"'), html);
  check('不再有「插队」文字', !html.includes('插队'), html);
  check('不再有「插队」标签样式', !html.includes('queue-tag'), html);
}

// ── 2. 每行都有上移 / 下移 ─────────────────────────────────────────────
{
  const h = makeHarness([item('a'), item('b')]);
  h.api.renderQueue();
  const html = h.els['#queueItems'].innerHTML;
  check('出现 up 按钮', html.includes('data-queue-action="up"'), html);
  check('出现 down 按钮', html.includes('data-queue-action="down"'), html);
  check('按钮数量 = 行数 × 2', (html.match(/data-queue-action="(up|down)"/g) || []).length === 4,
    String((html.match(/data-queue-action="(up|down)"/g) || []).length));
  check('用 chevron 图标', html.includes('#i-chevron-up') && html.includes('#i-chevron-down'));
}

// ── 3. 边界按钮 disabled ───────────────────────────────────────────────
{
  const h = makeHarness([item('a'), item('b'), item('c')]);
  h.api.renderQueue();
  const html = h.els['#queueItems'].innerHTML;
  const upDisabled = (html.match(/data-queue-action="up"[^>]*disabled/g) || []).length;
  const downDisabled = (html.match(/data-queue-action="down"[^>]*disabled/g) || []).length;
  check('只有队首的上移置灰', upDisabled === 1, String(upDisabled));
  check('只有队尾的下移置灰', downDisabled === 1, String(downDisabled));
  check('单行时两个都置灰', (() => {
    const one = makeHarness([item('x')]);
    one.api.renderQueue();
    const h1 = one.els['#queueItems'].innerHTML;
    return (h1.match(/disabled/g) || []).length >= 2;
  })());
}

// ── 4. 上移 / 下移打到正确的接口 ───────────────────────────────────────
(async () => {
  {
    const h = makeHarness([item('a'), item('b')]);
    await h.api.queueAction('up', 'b');
    const call = h.calls[0];
    check('上移请求路径', call && call.url === '/conversations/conv-1/queue/b/move', call && call.url);
    check('上移方法 POST', call && call.method === 'POST');
    check('上移方向 up', call && call.body && call.body.direction === 'up',
      JSON.stringify(call && call.body));
    check('上移提示', h.toasts.some((x) => x.msg === '已上移一位。'),
      JSON.stringify(h.toasts));
  }
  {
    const h = makeHarness([item('a'), item('b')]);
    await h.api.queueAction('down', 'a');
    const call = h.calls[0];
    check('下移方向 down', call && call.body && call.body.direction === 'down',
      JSON.stringify(call && call.body));
    check('下移提示', h.toasts.some((x) => x.msg === '已下移一位。'));
  }

  // ── 5. 边界 moved=false：只提示，不弹错误 ───────────────────────────
  {
    const h = makeHarness([item('a'), item('b')], {
      apiResponse: () => ({ moved: false, items: [item('a'), item('b')] }),
    });
    await h.api.queueAction('up', 'a');
    check('到顶提示「已经在队首」', h.toasts.some((x) => x.msg === '已经在队首。'),
      JSON.stringify(h.toasts));
    check('到顶不报错', !h.toasts.some((x) => x.kind === 'bad'), JSON.stringify(h.toasts));
  }
  {
    const h = makeHarness([item('a'), item('b')], {
      apiResponse: () => ({ moved: false, items: [item('a'), item('b')] }),
    });
    await h.api.queueAction('down', 'b');
    check('到底提示「已经在队尾」', h.toasts.some((x) => x.msg === '已经在队尾。'));
  }

  // ── 6. 服务端返回的新顺序被采纳 ─────────────────────────────────────
  {
    const reordered = [item('b'), item('a')];
    const h = makeHarness([item('a'), item('b')], {
      apiResponse: () => ({ moved: true, items: reordered }),
    });
    await h.api.queueAction('up', 'b');
    check('本地队列顺序被服务端结果覆盖',
      h.deps.state.queue.map((r) => r.id).join(',') === 'b,a',
      h.deps.state.queue.map((r) => r.id).join(','));
  }

  // ── 7. 立即插入 / 取消仍然可用 ──────────────────────────────────────
  {
    const h = makeHarness([item('a'), item('b')], { running: true });
    h.api.renderQueue();
    const html = h.els['#queueItems'].innerHTML;
    check('有 running 时显示「停止并继续」', html.includes('data-queue-action="inject"'), html);
    check('取消按钮始终在', html.includes('data-queue-action="cancel"'), html);
  }
  {
    const h = makeHarness([item('a')], { running: false });
    h.api.renderQueue();
    const html = h.els['#queueItems'].innerHTML;
    check('没有 running 时不显示「停止并继续」', !html.includes('data-queue-action="inject"'), html);
  }

  // ── 8. 排队消息不进转录，编辑入口在队列条上 ─────────────────────────
  {
    const h = makeHarness([item('a', { queued_msg_id: 'msg-a' })], {
      messages: [{ id: 'msg-a', content: '消息 a', by: 'local' }],
    });
    h.api.renderQueue();
    const html = h.els['#queueItems'].innerHTML;
    check('队列条有「编辑」按钮', html.includes('data-queue-action="edit"'), html);
    await h.api.queueAction('edit', 'a');
    check('编辑按钮打开对应消息的编辑器', h.editStarts.includes('msg-a'),
      JSON.stringify(h.editStarts));
    check('编辑不走队列接口', h.calls.length === 0, JSON.stringify(h.calls));
  }

  if (failures) {
    console.error(`\n消息队列排序测试失败 ${failures} 项`);
    process.exit(1);
  }
  console.log('\n消息队列排序测试全部通过');
})();

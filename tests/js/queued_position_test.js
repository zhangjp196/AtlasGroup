/**
 * 排队消息渲染位置回归测试（无需浏览器 / jsdom）。
 *
 * 用户反馈：「排队发送的位置也不对，应该是在上个回答之后，而不是用户消息下面」。
 *
 * 队列模式下，用户在回复还在生成时继续发消息，那条消息会立刻落库（可见），但它
 * 的 run 要等当前 run 收尾才启动。原来的 renderMessages 无条件把 state.live
 * （正在生成的那一条）append 到最后，于是排队消息显示在当前回答**上方**——看起
 * 来像是「挂在上一条用户消息下面」。
 *
 * 后续用户要求（2026-10-30）：「排队的不要发出去，等到这一条才发」——排队中的
 * 消息不再进转录，只显示在「消息队列」条上；等它的 run 开始执行才出现。
 *
 * 这里锁定：
 *   1. liveBeforeMessage 只对「仍在队列里」的消息返回 true；
 *   2. renderMessages 用 liveBeforeMessage 把 live 穿插进去，而不是无条件放最后；
 *   3. 顺序模拟：u1 → a1 → LIVE → u2(排队中)；
 *   4. 排队消息不渲染（isQueuedUserMessage），轮到它才出现。
 *
 * 运行：node tests/js/queued_position_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

/** 从 app.js 抽出具名函数（括号配平，模板字符串里的 ${} 自带配对）。 */
function extractFn(src, name) {
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

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

// ── 1. liveBeforeMessage 的判定 ────────────────────────────────────────
{
  const fn = extractFn(SRC, 'liveBeforeMessage');
  check('能抽出 liveBeforeMessage', !!fn, fn ? '' : '函数不存在（可能被重命名/删除）');
  const make = new Function('state', `${fn}
    return liveBeforeMessage;`);
  const state = {
    queue: [
      { id: 'run-2', queued_msg_id: 'msg-q2' },
      { id: 'run-3', queued_msg_id: 'msg-q3' },
    ],
  };
  const liveBeforeMessage = make(state);
  check('仍在队列里的消息 → true（live 排它前面）',
    liveBeforeMessage({ id: 'msg-q2', by: 'local' }) === true);
  check('不在队列里的消息 → false',
    liveBeforeMessage({ id: 'msg-old', by: 'local' }) === false);
  check('助手历史回复 → false',
    liveBeforeMessage({ id: 'msg-a1', by: 'assistant' }) === false);
  check('空消息 → false', liveBeforeMessage(null) === false);

  const fn2 = new Function('state', `${fn}
    return liveBeforeMessage;`)({ queue: [] });
  check('队列为空时一律 false（普通流式放最后）',
    fn2({ id: 'msg-any', by: 'local' }) === false);
}

// ── 2. renderMessages 用 liveBeforeMessage 穿插 ────────────────────────
{
  const body = extractFn(SRC, 'renderMessages');
  check('能抽出 renderMessages', !!body);
  check('renderMessages 调用 liveBeforeMessage', body.includes('liveBeforeMessage(m)'),
    '未调用 → live 又被无条件放到最后了');
  check('没有待插入消息时仍会补进 live',
    body.includes('if (!livePlaced) parts.push(liveHtml)'));
}

// ── 3. 顺序模拟：排队消息应落在 live 之后 ──────────────────────────────
{
  const fn = extractFn(SRC, 'liveBeforeMessage');
  const liveBeforeMessage = new Function('state', `${fn}
    return liveBeforeMessage;`)({
    queue: [{ id: 'run-2', queued_msg_id: 'msg-q2' }],
  });

  // 模拟 renderMessages 的循环：messages = [u1, a1, u2(排队中)]
  const messages = [
    { id: 'msg-u1', by: 'local', label: 'u1' },
    { id: 'msg-a1', by: 'assistant', label: 'a1' },
    { id: 'msg-q2', by: 'local', label: 'u2(排队)' },
  ];
  const liveHtml = 'LIVE';
  const parts = [];
  let livePlaced = false;
  for (const m of messages) {
    if (!livePlaced && liveBeforeMessage(m)) {
      parts.push(liveHtml);
      livePlaced = true;
    }
    parts.push(m.label);
  }
  if (!livePlaced) parts.push(liveHtml);

  check('顺序应为 u1 → a1 → LIVE → u2(排队)',
    parts.join(' → ') === 'u1 → a1 → LIVE → u2(排队)', parts.join(' → '));
}

// ── 4. 运行中连发的消息：乐观 id 要换成服务端 id ────────────────────────
// 用户第二次反馈「队列的还有 bug」：截图里两条排队消息仍挂在当前回答上面。
// 原因是运行中发送时本地先插一条 id=local-xxx 的乐观消息，服务端落库后它的
// id 是 msg-xxx，排队 run 的 queued_msg_id 指向 msg-xxx，两边对不上号，
// liveBeforeMessage 一律 false → live 被排到最后。
{
  const fn = extractFn(SRC, 'adoptSentMessageId');
  check('能抽出 adoptSentMessageId', !!fn, fn ? '' : '函数不存在（可能被重命名/删除）');
  const adopt = new Function('state', `${fn}
    return adoptSentMessageId;`);
  const optimistic = { id: 'local-1', by: 'local', content: '你好' };
  const state = { messages: [optimistic] };
  const id = adopt(state)(optimistic, { id: 'run-9', queued_msg_id: 'msg-9' });
  check('换成服务端 id', id === 'msg-9' && optimistic.id === 'msg-9', `${id} / ${optimistic.id}`);

  const liveBeforeMessage = new Function('state', `${
    extractFn(SRC, 'liveBeforeMessage')}
    return liveBeforeMessage;`)({ queue: [{ id: 'run-9', queued_msg_id: 'msg-9' }] });
  check('换 id 后 live 排到排队消息前面', liveBeforeMessage(optimistic) === true);

  const stale = { id: 'local-2', by: 'local', content: '你好' };
  const id2 = adopt({ messages: [] })(stale, { id: 'run-10', queued_msg_id: 'msg-10' });
  check('重拉过转录（乐观行已不在）时不动它',
    id2 === '' && stale.id === 'local-2', `${id2} / ${stale.id}`);

  check('sendMessage 收到响应后调用 adoptSentMessageId',
    SRC.includes('adoptSentMessageId(optimistic, run)'),
    '未调用 → 运行中连发的排队消息又会显示在当前回答上面');
}


// ── 5. 排队中的消息不进转录 ────────────────────────────────────────────
{
  const fn = extractFn(SRC, 'isQueuedUserMessage');
  check('能抽出 isQueuedUserMessage', !!fn, fn ? '' : '函数不存在（可能被重命名/删除）');
  const make = new Function('state', `${fn}
    return isQueuedUserMessage;`);

  // 服务端 queued 标记（刷新后的历史消息）
  let q = make({ queue: [], editingMsg: null });
  check('queued 标记 → 隐藏', q({ id: 'msg-1', by: 'local', queued: true }) === true);
  // 队列条里的 queued_msg_id（刚发出、还没重拉转录）
  q = make({ queue: [{ id: 'run-2', queued_msg_id: 'msg-2' }], editingMsg: null });
  check('在 state.queue 里 → 隐藏', q({ id: 'msg-2', by: 'local' }) === true);
  // 乐观发送途中
  q = make({ queue: [], editingMsg: null });
  check('pendingQueue（请求还没返回）→ 隐藏',
    q({ id: 'local-1', by: 'local', pendingQueue: true }) === true);
  // 正常消息 / 助手 / 已轮到
  check('普通用户消息 → 显示', q({ id: 'msg-3', by: 'local' }) === false);
  check('助手回复 → 不受影响', q({ id: 'msg-4', by: 'assistant', queued: true }) === false);
  check('队列里没有它（已轮到）→ 显示',
    q({ id: 'msg-5', by: 'local', queued: false }) === false);
  // 从队列条点编辑：临时露出来渲染编辑器
  q = make({ queue: [{ id: 'run-6', queued_msg_id: 'msg-6' }],
    editingMsg: { id: 'msg-6', text: '' } });
  check('正在编辑的排队消息 → 临时显示',
    q({ id: 'msg-6', by: 'local' }) === false);

  const body = extractFn(SRC, 'renderMessages');
  check('renderMessages 跳过排队消息', body.includes('if (isQueuedUserMessage(m)) continue'),
    '没跳过 → 排队消息又会出现在转录里');
}

// ── 6. 大纲不列排队消息（DOM 序号必须与转录一致）─────────────────────
{
  const body = extractFn(SRC, 'outlineSource');
  check('outlineSource 跳过排队消息', body.includes('if (isQueuedUserMessage(m)) continue'), body);
}

console.log(failures === 0
  ? '\n[queued-position] 全部通过'
  : `\n[queued-position] ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);

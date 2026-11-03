/**
 * 「立即插入」的渲染位置（历史数据兼容）+ V2「停止并继续」语义锁定。
 *
 * V2（2026-11-02）用户要求：「立即插入 需要调整为 停止然后继续，目前跟队列一样，
 * 但输出又在原来的位置，导致界面异常」。旧实现把消息注入正在生成的这一轮——正文
 * 继续在原位输出、消息却被摘出队列并跳位，界面就错乱。现在改成「停下当前回复，
 * 这条当下一轮跑」，前端不再本地写注入归属。
 *
 * 但线上已有会话里存着旧的注入数据（injected_into_run），所以 liveBeforeMessage /
 * insertMessageInOrder 的兼容逻辑必须留着，这里继续锁定（第 1~4 节）。
 *
 * 历史说明（用户反馈：「立即插入也是一样」，紧接「排队发送的位置也不对」之后）。
 *
 * 队列模式下用户点「立即插入」：后端把消息交给当前 run 当干预取走，并删掉它的排队
 * run。于是这条消息**不再出现在 state.queue 里**——只看队列的 liveBeforeMessage 就
 * 认不出它属于「本轮之后」，live（当前回答）被放到最后，插入消息显示在回答上面 /
 * 挂在用户消息下面。这里锁定：
 *
 *   1. liveBeforeMessage 对「被本轮取走的插入消息」返回 true（按 injected_into_run）；
 *   2. 只认**本轮**（别的 run 取走的插入消息不影响当前排版）；
 *   3. insertMessageInOrder 把刚完成的那条按回合顺序插回去，而不是 push 到最后；
 *   4. 顺序模拟：u1 → a0(上一轮) → LIVE → u2(插入)。
 *
 * 运行：node tests/js/injected_position_test.js
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

function makeLiveBefore(state) {
  const fn = extractFn(SRC, 'liveBeforeMessage');
  check('能抽出 liveBeforeMessage', !!fn,
    fn ? '' : '函数不存在（可能被重命名/删除）');
  return new Function('state', `${fn}\n    return liveBeforeMessage;`)(state);
}

// ── 1. 被本轮取走的插入消息 → true ─────────────────────────────────────
{
  const liveBeforeMessage = makeLiveBefore({ queue: [], run: { id: 'run-1' } });
  check('injected_into_run === 当前 run → true（live 排它前面）',
    liveBeforeMessage({ id: 'msg-inj', by: 'local', injected_into_run: 'run-1' }) === true);
  check('injected_into_run 是别的 run → false',
    liveBeforeMessage({ id: 'msg-inj2', by: 'local', injected_into_run: 'run-999' }) === false);
  check('没有归属的普通消息 → false',
    liveBeforeMessage({ id: 'msg-old', by: 'local' }) === false);
  check('空消息 → false', liveBeforeMessage(null) === false);
}

// ── 2. 没有 run / 队列为空时行为不变（回归保护） ───────────────────────
{
  const noRun = makeLiveBefore({ queue: [], run: null });
  check('没有当前 run 时，插入消息不影响排版',
    noRun({ id: 'msg-inj', by: 'local', injected_into_run: 'run-1' }) === false);

  const queuedOnly = makeLiveBefore({
    queue: [{ id: 'run-2', queued_msg_id: 'msg-q2' }],
    run: { id: 'run-1' },
  });
  check('仍在队列里的消息仍返回 true',
    queuedOnly({ id: 'msg-q2', by: 'local' }) === true);
}

// ── 3. insertMessageInOrder：收尾那条按顺序插回，不无条件追加 ──────────
{
  const fn = extractFn(SRC, 'insertMessageInOrder');
  check('能抽出 insertMessageInOrder', !!fn,
    fn ? '' : '函数不存在：finishRun 里可能又改回 push 到最后了');

  const make = (messages) => {
    const state = { messages, queue: [], run: { id: 'run-1' } };
    const liveBeforeMessage = new Function('state',
      `${extractFn(SRC, 'liveBeforeMessage')}\n      return liveBeforeMessage;`)(state);
    const insert = new Function('state', 'liveBeforeMessage', `${fn}
      return insertMessageInOrder;`)(state, liveBeforeMessage);
    return { state, insert };
  };

  // 本轮回答后面已经有「立即插入」的消息：答完要插在它前面
  {
    const { state, insert } = make([
      { id: 'msg-u1', by: 'local' },
      { id: 'msg-inj', by: 'local', injected_into_run: 'run-1' },
    ]);
    insert({ id: 'msg-a1', by: 'assistant' });
    check('本轮回答插到插入消息之前',
      state.messages.map((m) => m.id).join(',') === 'msg-u1,msg-a1,msg-inj',
      state.messages.map((m) => m.id).join(','));
  }

  // 后面没有需要让它靠前的消息：仍然追加到最后
  {
    const { state, insert } = make([{ id: 'msg-u1', by: 'local' }]);
    insert({ id: 'msg-a1', by: 'assistant' });
    check('没有待插消息时追加到最后',
      state.messages.map((m) => m.id).join(',') === 'msg-u1,msg-a1',
      state.messages.map((m) => m.id).join(','));
  }
}

// ── 4. 顺序模拟：插入消息应落在 LIVE 之后 ──────────────────────────────
{
  const liveBeforeMessage = makeLiveBefore({ queue: [], run: { id: 'run-1' } });
  const messages = [
    { id: 'msg-u1', by: 'local', label: 'u1' },
    { id: 'msg-a0', by: 'assistant', label: 'a0(上一轮)' },
    { id: 'msg-inj', by: 'local', injected_into_run: 'run-1', label: 'u2(插入)' },
  ];
  const parts = [];
  let livePlaced = false;
  for (const m of messages) {
    if (!livePlaced && liveBeforeMessage(m)) { parts.push('LIVE'); livePlaced = true; }
    parts.push(m.label);
  }
  if (!livePlaced) parts.push('LIVE');
  check('顺序应为 u1 → a0(上一轮) → LIVE → u2(插入)',
    parts.join(' → ') === 'u1 → a0(上一轮) → LIVE → u2(插入)', parts.join(' → '));
}

// ── 5. V2 语义：inject 动作 = 停止当前回复，不再本地注入 ────────────────
{
  const injectBlock = (SRC.match(/action === 'inject'[\s\S]{0,1200}/) || [''])[0];
  check("inject 动作不再本地写 injected_into_run",
    !/injected_into_run\s*:/ .test(injectBlock),
    '本地改写归属 → 界面又出现「输出在原位、消息跳位」的错乱');
  check('inject 动作不再把这条从 state.queue 里摘掉',
    !/items\.filter\(\(it\) => it\.id !== runId\)/.test(injectBlock),
    '摘掉排队行 → 消息既不在队列也不在转录正确位置');
  check('inject 动作会挂停止兜底（run_end 迟迟不来时收尾）',
    /action === 'inject'[\s\S]{0,1200}stopRunFallback\(\)/.test(SRC),
    '不挂兜底 → 界面可能一直停在「处理中」');
}

// ── 5b. 后端语义：inject 只提队首、不再写 inject=1 ─────────────────────
{
  const py = fs.readFileSync(
    path.join(ROOT, 'services/agent_zero/task_layer/run_service.py'), 'utf8');
  const fnStart = py.indexOf('def inject_run(');
  const fnEnd = py.indexOf('def mark_intervention(');
  const body = fnStart >= 0 && fnEnd > fnStart ? py.slice(fnStart, fnEnd) : '';
  check('能读到 inject_run', !!body, '函数不存在（可能被重命名/删除）');
  // SQL 已从 PG 风格的 jsonb_set 迁到 sqlite 的 json_set，两种写法都认。
  check('inject_run 不含写 inject=1',
    !/('\{inject\}',\s*'1'|'\$\.inject',\s*1)/.test(body),
    '还在把消息当干预 → 会被正在执行的 run 取走');
  check('inject_run 会清掉残留的 inject=1',
    /('\{inject\}',\s*'0'|'\$\.inject',\s*0)/.test(body),
    '历史标记残留会让消息被当干预重复取走');
}

// ── 6. 后端锚点约定：list_messages 输出 injected_into_run ──────────────
{
  const py = fs.readFileSync(
    path.join(ROOT, 'services/agent_zero/task_layer/conversation.py'), 'utf8');
  check('list_messages 输出 injected_into_run',
    /"injected_into_run":\s*\(?props\.get\("injected_into_run"\)/.test(py),
    '前端拿不到归属就判不出位置');
  check('排序用 injected_into_run 回锚',
    /anchor = run_anchor_by_id\.get\(str\(m\.get\("injected_into_run"\)/.test(py),
    '排序没回锚 → 退回 created_at，插入消息会夹到回答上面');
}

console.log(failures === 0
  ? '\n[injected-position] 全部通过'
  : `\n[injected-position] ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);

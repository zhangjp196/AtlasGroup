/**
 * 发送按钮行为回归测试（无需浏览器 / jsdom）。
 * 从 web/assets/app.js 原样抽出 bindSendButton，用最小 DOM 桩驱动：
 *   - 普通长度单击 → 立即发送，双击也不会发出两条
 *   - 长文本单击 → 普通发送（内部会弹转 txt 确认）
 *   - 长文本双击 → 强制发送（跳过确认）
 *   - 长按 1.2s → 强制发送，随后补发的 click 被吃掉
 *   - 未达阈值的短按 → 退化为普通发送
 *
 * 运行：node tests/js/send_button_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

const START = SRC.indexOf('function bindSendButton()');
const END = SRC.indexOf('async function decideProposal(');
if (START < 0 || END < 0 || END <= START) {
  console.error('未能从 app.js 中定位 bindSendButton');
  process.exit(1);
}
const snippet = SRC.slice(START, END);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeHarness(inputValue, attachments) {
  const handlers = new Map();
  const style = { setProperty() {}, removeProperty() {} };
  const btn = {
    disabled: false,
    style,
    classList: { add() {}, remove() {} },
    addEventListener(type, fn) { handlers.set(type, fn); },
  };
  const input = { value: inputValue || '' };
  const calls = [];
  const deps = {
    $: (sel) => (sel === '#sendBtn' ? btn : input),
    state: { attachments: attachments || [] },
    LONG_TEXT_CONFIRM_CHARS: 6000,
    sendMessage: (opts) => { calls.push(opts && opts.force ? 'force' : 'normal'); },
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    navigator: {},
    Date,
    setTimeout,
    clearTimeout,
  };
  const keys = Object.keys(deps);
  new Function(...keys, `${snippet}\nbindSendButton();`)(...keys.map((k) => deps[k]));
  return { btn, handlers, calls };
}

let failed = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  OK   ${name}`);
  else { failed += 1; console.log(`  FAIL ${name}${extra ? ` — ${extra}` : ''}`); }
}

async function main() {
  console.log('send button 行为测试');

  // 1) 普通长度单击 → 立即发送一次
  {
    const { handlers, calls } = makeHarness('短消息');
    handlers.get('click')();
    check('普通长度单击立即发送', calls.length === 1 && calls[0] === 'normal', JSON.stringify(calls));
  }

  // 2) 普通长度双击 → 只发一条（第二下被吞掉）
  {
    const { handlers, calls } = makeHarness('短消息');
    handlers.get('click')();
    handlers.get('click')();
    check('普通长度双击不发两条', calls.length === 1, JSON.stringify(calls));
  }

  // 3) 长文本单击 → 普通发送（内部会弹转 txt 确认）
  {
    const { handlers, calls } = makeHarness('x'.repeat(6001));
    handlers.get('click')();
    await sleep(400);
    check('长文本单击走普通发送', calls.length === 1 && calls[0] === 'normal', JSON.stringify(calls));
  }

  // 4) 长文本双击 → 强制发送
  {
    const { handlers, calls } = makeHarness('x'.repeat(6001));
    handlers.get('click')();
    handlers.get('click')();
    await sleep(400);
    check('长文本双击强制发送', calls.length === 1 && calls[0] === 'force', JSON.stringify(calls));
  }

  // 5) 长按 → 强制发送，且补发的 click 不再发送
  {
    const { handlers, calls } = makeHarness('x'.repeat(6001));
    handlers.get('pointerdown')({ button: 0 });
    await sleep(1300);
    handlers.get('pointerup')();
    handlers.get('click')();
    await sleep(50);
    check('长按强制发送且不重复', calls.length === 1 && calls[0] === 'force', JSON.stringify(calls));
  }

  // 6) 未达阈值的短按 → 退化为普通发送
  {
    const { handlers, calls } = makeHarness('短消息');
    handlers.get('pointerdown')({ button: 0 });
    await sleep(200);
    handlers.get('pointerup')();
    handlers.get('click')();
    await sleep(20);
    check('短按退化为普通发送', calls.length === 1 && calls[0] === 'normal', JSON.stringify(calls));
  }

  console.log(failed ? `\n${failed} 项失败` : '\n全部通过');
  process.exit(failed ? 1 : 0);
}

main();

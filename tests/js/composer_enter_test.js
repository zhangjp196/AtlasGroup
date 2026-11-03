/**
 * 输入框 Enter 行为回归测试（无需浏览器 / jsdom）。
 * 做法：从 web/assets/app.js 里原样抽出 IME / Enter 状态机那段代码，
 * 用一个最小 DOM 桩执行它，检查发送时机是否符合预期。
 *
 * 运行：node tests/js/composer_enter_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

const START_MARK = 'let imeComposing = false;';
const END_MARK = "input.addEventListener('paste'";
const start = SRC.indexOf(START_MARK);
const end = SRC.indexOf(END_MARK);
if (start < 0 || end < 0 || end <= start) {
  console.error('未能从 app.js 中定位 Enter 状态机代码段');
  process.exit(1);
}
const snippet = SRC.slice(start, end);

function makeHarness() {
  const handlers = new Map();
  const input = {
    value: '你好',
    addEventListener(type, fn) { handlers.set(type, fn); },
    focus() {},
  };
  const calls = { send: 0, force: 0, toast: 0 };
  const sandbox = {
    input,
    autoGrow() {},
    updateLongTextHint() {},
    // 斜杠命令相关：本测试只关心 Enter / IME 状态机，命令面板不参与。
    handleSlashKeydown() { return false; },
    // @ 成员补全：本测试只关心 Enter / IME 状态机，补全菜单不参与。
    handleMentionKeydown() { return false; },
    updateMentionMenu() {},
    closeMentionMenu() {},
    updateSlashMenu() {},
    closeSlashMenu() {},
    document: { activeElement: null },
    t: (k) => k,
    toast: () => { calls.toast += 1; },
    sendMessage: (opts) => {
      calls.send += 1;
      if (opts && opts.force) calls.force += 1;
    },
    Date,
    setTimeout,
  };
  const keys = Object.keys(sandbox);
  // eslint-disable-next-line no-new-func
  new Function(...keys, snippet)(...keys.map((k) => sandbox[k]));
  return { input, handlers, calls };
}

function keyEvent(over) {
  return Object.assign({
    key: 'Enter',
    shiftKey: false,
    metaKey: false,
    ctrlKey: false,
    isComposing: false,
    keyCode: 13,
    which: 13,
    prevented: false,
    preventDefault() { this.prevented = true; },
  }, over || {});
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  OK   ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL ${name}${extra ? ` — ${extra}` : ''}`);
  }
}

async function main() {
  console.log('composer Enter 行为测试');

  // 1) 普通 Enter：一次直达
  {
    const { handlers, calls } = makeHarness();
    const e = keyEvent();
    handlers.get('keydown')(e);
    check('普通 Enter 直接发送', calls.send === 1 && e.prevented, `send=${calls.send}`);
  }

  // 2) Shift+Enter：不发送也不拦
  {
    const { handlers, calls } = makeHarness();
    const e = keyEvent({ shiftKey: true });
    handlers.get('keydown')(e);
    check('Shift+Enter 只换行', calls.send === 0 && !e.prevented);
  }

  // 3) 组词中的 Enter（isComposing / keyCode 229）：完全不干预
  {
    const { handlers, calls } = makeHarness();
    handlers.get('compositionstart')();
    const a = keyEvent({ isComposing: true });
    handlers.get('keydown')(a);
    const b = keyEvent({ keyCode: 229, which: 229 });
    handlers.get('keydown')(b);
    check('组词中的 Enter 不发也不拦', calls.send === 0 && !a.prevented && !b.prevented);
    handlers.get('compositionend')();
    handlers.get('input')();
  }

  // 4) 候选词上屏后立刻 Enter：不发送，提示再按一次
  {
    const { handlers, calls } = makeHarness();
    handlers.get('compositionstart')();
    handlers.get('compositionend')();
    handlers.get('input')();          // 候选词上屏触发的 input
    const e = keyEvent();
    handlers.get('keydown')(e);
    check('IME 刚结束的首次 Enter 不发送', calls.send === 0 && e.prevented,
      `send=${calls.send}`);
    check('IME 首次 Enter 有提示', calls.toast === 1, `toast=${calls.toast}`);
    const e2 = keyEvent();
    handlers.get('keydown')(e2);
    check('第二次 Enter 才发送', calls.send === 1 && e2.prevented, `send=${calls.send}`);
  }

  // 5) 两次 Enter 之间继续输入 → 确认态失效
  {
    const { handlers, calls } = makeHarness();
    handlers.get('compositionstart')();
    handlers.get('compositionend')();
    handlers.get('input')();
    handlers.get('keydown')(keyEvent());   // 预告
    handlers.get('input')();               // 用户继续打字
    handlers.get('keydown')(keyEvent());   // 这次不算确认
    check('继续输入取消预告', calls.send === 0, `send=${calls.send}`);
  }

  // 6) IME 结束超过冷却窗口后，一次 Enter 直达
  {
    const { handlers, calls } = makeHarness();
    handlers.get('compositionstart')();
    handlers.get('compositionend')();
    handlers.get('input')();
    await sleep(700);
    const e = keyEvent();
    handlers.get('keydown')(e);
    check('冷却期外一次 Enter 直达', calls.send === 1 && e.prevented, `send=${calls.send}`);
  }

  // 7) Cmd/Ctrl+Enter：强制发送
  {
    const { handlers, calls } = makeHarness();
    const e = keyEvent({ metaKey: true });
    handlers.get('keydown')(e);
    check('Cmd+Enter 强制发送', calls.send === 1 && calls.force === 1 && e.prevented);
  }

  console.log(failed ? `\n${failed} 项失败` : '\n全部通过');
  process.exit(failed ? 1 : 0);
}

main();

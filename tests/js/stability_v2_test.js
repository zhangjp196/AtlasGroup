/**
 * V2 稳定性收尾的前端行为回归（2026-11-02）。
 *
 * 用户反馈的三件事，各自锁一条前端不变量：
 *
 *   1. 「正在输出中窗口，订阅右键会频闪」——流式输出每帧自动贴底都会触发
 *      scroll，全局 capture 的 scroll 监听若一律关菜单，右键菜单在输出期间
 *      根本站不住。锁定：程序化贴底（lastAutoScrollAt 之内）不关菜单。
 *   2. 「立即插入的停止然后继续，是静默的」——停止并继续不提示「运行已停止」，
 *      也不落「本轮输出已中断」红条。
 *   3. 「模型很长时间运行就会卡住」——后端长时间没有新事件时，界面必须如实
 *      提示而不是永远转圈。
 *   4. run_end 只收尾自己那条 run：旧 run 的迟到帧不能打掉刚开始的新一轮。
 *
 * 运行：node tests/js/stability_v2_test.js
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
    const ch = src[i];
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return '';
}

// ── 1. 自动贴底引起的 scroll 不关右键菜单 ─────────────────────────────
{
  const pin = extractFn(SRC, 'pinChatBottom');
  check('pinChatBottom 存在', !!pin);
  check('自动贴底记录时间戳（供 scroll 监听区分）',
    /lastAutoScrollAt\s*=\s*Date\.now\(\)/.test(pin),
    '自动贴底没有打标记：scroll 监听无法区分用户滚动');
  check('已在底部时不重复写 scrollTop（不产生多余 scroll 事件）',
    /scroller\.scrollTop\s*-\s*top\)\s*<\s*1/.test(pin));

  const scrollBlock = (SRC.match(/document\.addEventListener\('scroll',\s*\(e\)\s*=>\s*\{[\s\S]{0,900}?\},\s*true\);/) || [''])[0];
  check('全局 scroll 监听拿到了事件参数', !!scrollBlock, '没有 (e) 参数就拿不到滚动源');
  check('自动贴底引起的滚动直接放行（不关菜单）',
    /Date\.now\(\)\s*-\s*lastAutoScrollAt\s*<\s*300/.test(scrollBlock),
    scrollBlock.slice(0, 200));
  check('放行分支在 closeUiMenu 之前 return',
    scrollBlock.indexOf('if (auto) return') > 0 &&
    scrollBlock.indexOf('if (auto) return') < scrollBlock.indexOf('closeUiMenu'));
}

// ── 2. 停止并继续是静默的 ─────────────────────────────────────────────
{
  const injectBlock = (SRC.match(/action === 'inject'[\s\S]{0,900}/) || [''])[0];
  check('inject 动作存在', !!injectBlock);
  check('inject 置静默停止标记', /state\.silentStop\s*=\s*true/.test(injectBlock));
  check('inject 不再弹「已停止当前回复」toast',
    !/toast\(t\('queue\.stopContinue'\)/.test(injectBlock),
    '静默停止却仍在弹提示');
  check('inject 仍挂停止兜底（run_end 迟到也不卡界面）',
    /stopRunFallback\(\)/.test(injectBlock));

  const finish = extractFn(SRC, 'finishRun');
  check('finishRun 存在', !!finish);
  check('静默停止不弹「运行已停止」',
    /status === 'stopped' && !silentStop/.test(finish),
    '静默停止仍会提示运行已停止');
  check('静默停止的回复不标 interrupted（无红条）',
    /interrupted:\s*silentStop \? false/.test(finish));
  check('silentStop 用过即清（不影响下一轮）',
    /state\.silentStop = false/.test(finish));
}

// ── 3. 停滞提示 ───────────────────────────────────────────────────────
{
  const reconcile = extractFn(SRC, 'reconcileRunState');
  check('reconcileRunState 存在', !!reconcile);
  check('有停滞阈值常量', /const STALL_NOTICE_S = \d+/.test(SRC));
  check('停滞判定按 last_seq 是否推进', /lastSeq !== state\.stallSeq/.test(reconcile));
  check('停滞到阈值给提示', /t\('run\.stalled'/.test(reconcile));
  check('提示有节流（不反复弹）', /STALL_NOTICE_GAP_S/.test(reconcile));
  check('中文文案存在', /'run\.stalled':\s*'已经 \{s\}s/.test(SRC));
  check('英文文案存在', /'run\.stalled':\s*'No progress for \{s\}s/.test(SRC));
}

// ── 4. run_end 只收尾自己那条 run ────────────────────────────────────
{
  const endBlock = (SRC.match(/addEventListener\('run_end'[\s\S]{0,900}?\}\);\n/) || [''])[0];
  check('run_end 处理器存在', !!endBlock);
  check('run_end 比对 run id 后再收尾',
    /ended !== state\.run\.id/.test(endBlock),
    '旧 run 的迟到 run_end 会把刚开始的新一轮打掉');
  check('比对通过才 finishRun', /finishRun\(payload\.run \|\| state\.run\)/.test(endBlock));

  const fallback = extractFn(SRC, 'stopRunFallback');
  check('停止兜底锁定发起时的 run',
    /const targetRunId = \(state\.run && state\.run\.id\)/.test(fallback),
    '不锁定就会去轮询「停止并继续」刚起的新一轮');
}

console.log(failures ? `\n[stability-v2] ${failures} 项失败` : '\n[stability-v2] 全部通过');
process.exit(failures ? 1 : 0);

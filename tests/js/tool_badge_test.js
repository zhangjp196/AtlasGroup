/**
 * 对话区工具徽标（pack.op）不撑宽时间线的回归测试。
 *
 * 用户反馈：截图里出现一条巨宽的 `code.cd /Users/... && python3 - <<'PY' ...`
 * 徽标，把对话区顶出容器；要求「要么隐藏要么换行」。
 *
 * 两条防线：
 *   1. 后端 packs.pack_op_label 只下发「该包已知的动作名」（见
 *      tests/unit/test_tool_packs.py）；
 *   2. 前端渲染时再兜一层：op 不是短标识就不拼进徽标，且 CSS 上徽标可收缩 /
 *      标题行可折行，任何历史坏数据都不会顶宽容器。
 * 运行：node tests/js/tool_badge_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const JS = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok  ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function block(selector) {
  const re = new RegExp(
    `(^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`, 'm');
  const m = CSS.match(re);
  return m ? m[2] : '';
}

console.log('[tool-badge] 前端只拼短 op');
check('renderStep 对 op 做了白名单 + 长度校验',
  /packOp\s*=\s*step\.op[\s\S]{0,160}\^\[A-Za-z0-9_-\]\{1,32\}\$/.test(JS),
  '找不到 packOp 的正则校验');
check('pack 名也做了长度截断', /\$\{String\(step\.pack\)\.slice\(0,\s*24\)\}/.test(JS));

console.log('[tool-badge] CSS 不再让徽标顶宽');
const badge = block('.pack-badge');
check('.pack-badge 存在', badge.length > 0);
check('.pack-badge 允许收缩（flex 不是 none）',
  /flex\s*:\s*0\s+1\s+auto/.test(badge), badge.trim().split('\n')[0]);
check('.pack-badge 有 max-width', /max-width\s*:/.test(badge));
check('.pack-badge 超长省略', /text-overflow\s*:\s*ellipsis/.test(badge));
check('.pack-badge 仍保留省略号需要的 nowrap', /white-space\s*:\s*nowrap/.test(badge));
check('.step-title 允许折行（flex-wrap）',
  /flex-wrap\s*:\s*wrap/.test(block('.step-title')));

if (failures) {
  console.error(`\n[tool-badge] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[tool-badge] 全部通过');

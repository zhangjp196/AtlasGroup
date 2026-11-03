/**
 * 上下文大纲「鼠标悬浮打开」回归测试（无需浏览器 / jsdom）。
 *
 * 用户要求（2026-10-30）：「鼠标悬浮打开面板显示」「当前为左键点击」。
 *
 * 现在的行为：
 *   - 鼠标进入右侧大纲区域（收起时的刻度条 / 展开后的面板）→ 展开；
 *   - 鼠标离开 → 自动收起（延迟 140ms，且指针仍在大纲上时不收）；
 *   - 点「收起」后先压住悬停展开，等指针离开再恢复，否则刚点完就被重新打开；
 *   - 触摸 / 触控笔不参与悬停（点按仍走原来的展开按钮）。
 *
 * 运行：node tests/js/outline_hover_test.js
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

function fnBody(name) {
  const start = SRC.indexOf(`  function ${name}(`);
  if (start < 0) return '';
  const brace = SRC.indexOf('{', start);
  if (brace < 0) return '';
  let depth = 0;
  for (let i = brace; i < SRC.length; i += 1) {
    if (SRC[i] === '{') depth += 1;
    else if (SRC[i] === '}') {
      depth -= 1;
      if (depth === 0) return SRC.slice(brace, i + 1);
    }
  }
  return '';
}

// ── 1. 绑定 ────────────────────────────────────────────────────────────
{
  check('存在 bindOutlineHover', !!fnBody('bindOutlineHover'));
  check('boot 里调用了 bindOutlineHover', /bindOutlineHover\(\);/.test(SRC));
}

// ── 2. 进入 / 离开 ─────────────────────────────────────────────────────
{
  const body = fnBody('bindOutlineHover');
  check('进入用 pointerover（外层 height:0，mouseenter 不可靠）', body.includes("'pointerover'"), body);
  check('离开用 pointerout', body.includes("'pointerout'"), body);
  check('只对鼠标生效（触摸 / 触控笔不悬浮展开）',
    (body.match(/pointerType !== 'mouse'/g) || []).length === 2, body);
  check('子元素之间移动不算离开（relatedTarget 在大纲内则跳过）',
    (body.match(/relatedTarget/g) || []).length >= 2, body);

  const enter = fnBody('outlineHoverEnter');
  check('进入时若已压制 / 已展开则不动', enter.includes('outlineHoverSuppressed') && enter.includes('outlineOpen'));
  check('进入调用 setOutlineOpen(true)', enter.includes('setOutlineOpen(true)'), enter);

  const leave = fnBody('outlineHoverLeave');
  check('离开是延迟收起', /setTimeout\(/.test(leave), leave);
  check('收起前清掉压制标记（离开后恢复悬停）', leave.includes('outlineHoverSuppressed = false'));
  check('指针仍在大纲上时不收（坐标反查命中元素）',
    leave.includes('elementFromPoint') && leave.includes('wrap.contains(el)'), leave);
}

// ── 3. 点「收起」后不被悬停立刻重新打开 ────────────────────────────────
{
  const close = SRC.slice(SRC.indexOf("e.target.closest('[data-outline-close]')"));
  check('点收起设置 outlineHoverSuppressed',
    /outlineHoverSuppressed\s*=\s*true/.test(close.slice(0, 240)), close.slice(0, 240));

  const open = SRC.slice(SRC.indexOf("e.target.closest('[data-outline-open]')"));
  check('点展开清除压制', /outlineHoverSuppressed\s*=\s*false/.test(open.slice(0, 240)), open.slice(0, 240));
}

console.log(failures === 0
  ? '\n[outline-hover] 全部通过'
  : `\n[outline-hover] ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);

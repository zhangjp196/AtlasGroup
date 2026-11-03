/**
 * 进程面板悬浮布局回归测试（无需浏览器 / jsdom）。
 *
 * 用户要求（2026-10-30）：「调整为悬浮面板」。
 *
 * 以前进程面板是对话区左侧的分栏（252px，占满整列高度）：跑起进程时挤出一整列
 * 空白，正文宽度被压窄；进程跑完又整块消失，正文随之跳动。现在改为悬浮卡片：
 * 绝对定位在对话区左上角，不参与布局，正文宽度恒定。
 *
 * 这里锁定：
 *   1. .chat-shell 是定位上下文，.process-panel 绝对定位且有最大高度；
 *   2. 不再有分栏的 border-right / flex: none；
 *   3. 没有进程时仍然 hidden（跑完自动消失）；
 *   4. 收起状态是小浮标：保留图标与数量，只隐藏标题文字和列表；
 *   5. index.html 里面板仍在 chat-shell 内、chatScroll 之前。
 *
 * 运行：node tests/js/process_panel_float_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

/** 取出某个选择器的第一条规则体（{ ... } 配平）。 */
function cssBlock(src, selector) {
  const start = src.indexOf(`\n${selector} {`);
  if (start < 0) return '';
  const braceStart = src.indexOf('{', start);
  let depth = 0;
  for (let i = braceStart; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(braceStart + 1, i);
    }
  }
  return '';
}

// ── 1. 悬浮定位 ────────────────────────────────────────────────────────
{
  const shell = cssBlock(CSS, '.chat-shell');
  check('.chat-shell 是定位上下文', /position:\s*relative/.test(shell), shell);

  // 定位与尺寸挪到了浮动列容器上（进程 / 任务清单共用一列，见 2026-11-02）
  const floats = cssBlock(CSS, '.chat-floats');
  check('.chat-floats 绝对定位（不再参与布局）', /position:\s*absolute/.test(floats), floats);
  check('悬浮在左上角', /left:\s*\d+px/.test(floats) && /top:\s*\d+px/.test(floats), floats);
  check('有最大高度（不超过对话区 60%）', /max-height:\s*calc\(60%/.test(floats), floats);
  check('浮动列自身不拦鼠标（点击穿透正文）',
    /pointer-events:\s*none/.test(floats), floats);
  check('竖直排列（进程在上、任务清单在下）', /flex-direction:\s*column/.test(floats), floats);

  const panel = cssBlock(CSS, '.process-panel,\n.todo-panel');
  check('面板卡片不再是分栏（没有右边框）', !/border-right/.test(panel), panel);
  check('不再有 flex: none 分栏痕迹', !/flex:\s*none/.test(panel), panel);
  check('有阴影与圆角（浮层观感）',
    /box-shadow:/.test(panel) && /border-radius:/.test(panel), panel);
  check('面板恢复接收鼠标事件', /pointer-events:\s*auto/.test(panel), panel);
}

// ── 2. 消失 / 收起 ─────────────────────────────────────────────────────
{
  check('没有进程时仍整块 hidden',
    CSS.includes('.process-panel[hidden],\n.todo-panel[hidden] { display: none; }'));

  const collapsed = CSS.slice(CSS.indexOf('.process-panel.is-collapsed'));
  check('收起后不再是一条 38px 空列', !/is-collapsed\s*\{\s*width:\s*38px/.test(collapsed),
    '收起的宽度应随内容（小浮标）');
  check('收起只隐藏标题文字（保留图标与数量）',
    /\.process-panel\.is-collapsed \.process-title > span/.test(collapsed));
  check('收起不隐藏数量徽标',
    !/\.process-panel\.is-collapsed \.active-runs-count/.test(collapsed),
    '数量要留在小浮标上');
  check('收起隐藏列表',
    /\.process-panel\.is-collapsed[^{]*\.process-list/.test(collapsed));
}

// ── 3. DOM 位置 ────────────────────────────────────────────────────────
{
  const shell = HTML.indexOf('id="chatShell"');
  const floats = HTML.indexOf('id="chatFloats"');
  const panel = HTML.indexOf('id="processPanel"');
  const scroll = HTML.indexOf('id="chatScroll"');
  check('面板在 chat-shell 内', shell > 0 && panel > shell);
  check('面板在浮动列容器内', floats > 0 && panel > floats);
  check('面板仍在对话滚动区之前', panel > 0 && scroll > panel);
}

console.log(failures === 0
  ? '\n[process-panel-float] 全部通过'
  : `\n[process-panel-float] ${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);

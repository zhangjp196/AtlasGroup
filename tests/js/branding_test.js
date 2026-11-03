/**
 * 品牌一致性回归（无需浏览器）：显示层 AtlasX，标识层保持 Atlas。
 *
 * 用户要求（2026-11-03）：「V3 版本调整，名称调整为 AtlasX，相关文档调整」。
 * 这里钉住前端最容易漏的几处：硬编码 title / 启动动画 / 侧栏 / 占位符、
 * i18n 里的品牌名（应为 {app} 占位，由 /app/info 注入）、缓存戳与版本。
 *
 * 运行：node tests/js/branding_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const JS = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');
const VERSION = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8').trim();

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

// ── 1. 静态品牌点 ───────────────────────────────────────────────────────
{
  check('页面标题是 AtlasX', HTML.includes('<title>AtlasX</title>'));
  check('启动动画用 AtlasX', HTML.includes('boot-splash-name">AtlasX<'));
  check('侧栏品牌是 AtlasX', HTML.includes('<strong>AtlasX</strong>'));
  check('顶栏副标题兜底是 AtlasX', HTML.includes('id="chatSubtitle">AtlasX<'));
  check('输入框占位是 AtlasX', HTML.includes('placeholder="Message AtlasX…"'));
  check('不再有裸品牌 >Atlas<', !/>Atlas</.test(HTML));
}

// ── 2. i18n 用 {app} 占位，由 /app/info 注入 ─────────────────────────────
{
  const keys = ['prompt.placeholder', 'workspace.empty', 'ask.title',
    'empty.body', 'empty.bodyChatOnly', 'error.unreachable'];
  for (const key of keys) {
    const count = (JS.match(new RegExp(`'${key.replace(/\./g, '\\.')}':`, 'g')) || []).length;
    check(`${key} 中英文各一条`, count === 2, `count=${count}`);
  }
  check('i18n 品牌名使用占位符', JS.includes("'prompt.placeholder': 'Message {app}…'")
    && JS.includes("'prompt.placeholder': '给 {app} 发消息…'"));
  check('t() 自动注入 app', JS.includes('app: appBrand()'));
  check('品牌兜底是 AtlasX', JS.includes("|| 'AtlasX'"));
  check('页面标题用 appBrand', JS.includes('${title} — ${appBrand()}'));
  check('默认角色名用 appBrand', JS.includes('role_name: appBrand()'));
  check('头像名兜底用 appBrand', JS.includes(': appBrand();'));
}

// ── 3. 版本与缓存戳 ─────────────────────────────────────────────────────
{
  check('VERSION 属于 V3', VERSION.split('.')[0] === '3', VERSION);
  check('CSS 缓存戳同步', HTML.includes(`app.css?v=${VERSION}-1`));
  check('JS 缓存戳同步', HTML.includes(`app.js?v=${VERSION}-1`));
  check('旧缓存戳不再残留', !HTML.includes('?v=2.4.'));
}

// ── 4. 标识层不变（改了会丢数据 / 影响签名）──────────────────────────────
{
  check('API 路径保持 /api/agent-zero', JS.includes("const API = '/api/agent-zero'"));
  check('CSS 里没有遗留品牌注释', !/Atlas — static UI/.test(CSS));
}

if (failures) {
  console.error(`\n[branding] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[branding] 全部通过');

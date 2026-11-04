/**
 * 共用主题核心回归（无需浏览器）：明暗模式、按系统时间昼夜切换、跨午夜区间。
 * 运行：node tests/js/theme_test.js
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/theme.js'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function makeSandbox(initial = {}) {
  const store = new Map(Object.entries(initial));
  const cookieStore = new Map();
  const meta = { content: '' };
  const root = { dataset: {}, style: {} };
  const document = {
    documentElement: root,
    querySelector(sel) { return sel === 'meta[name="theme-color"]' ? meta : null; },
    get cookie() {
      return [...cookieStore.entries()].map(([k, v]) => `${k}=${v}`).join('; ');
    },
    set cookie(value) {
      const first = String(value).split(';')[0];
      const idx = first.indexOf('=');
      if (idx > 0) cookieStore.set(first.slice(0, idx), first.slice(idx + 1));
    },
  };
  const sandbox = {
    document,
    console,
    localStorage: {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)),
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return { T: sandbox.AtlasTheme, store, meta, root };
}

const at = (hours, minutes = 0) => new Date(2026, 0, 1, hours, minutes);

// ── 1. 默认值与非法输入 ────────────────────────────────────────────────
{
  const { T } = makeSandbox();
  const defaults = T.normalizeSchedule(null);
  check('默认白天从 06:00 开始', defaults.dayStart === '06:00', defaults.dayStart);
  check('默认夜间从 19:00 开始', defaults.nightStart === '19:00', defaults.nightStart);
  check('默认手动模式', defaults.mode === 'manual');
  const bad = T.normalizeSchedule({
    mode: 'nope', day: 'nope', night: 'nope', interval: 0, dayStart: '99:99', nightStart: 'abc',
  });
  check('非法模式回落到手动', bad.mode === 'manual');
  check('非法主题回落到默认值', bad.day === 'light' && bad.night === 'dark');
  check('非法时间回落到默认值', bad.dayStart === '06:00' && bad.nightStart === '19:00');
  check('轮换间隔夹到 5–720 分钟', bad.interval === 15);
}

// ── 2. 昼夜切换（普通区间）─────────────────────────────────────────────
{
  const { T } = makeSandbox();
  const sc = { mode: 'daynight', day: 'light', night: 'dark', dayStart: '08:00', nightStart: '20:30' };
  check('08:00 刚进入白天', T.scheduledTheme(sc, 'dark', at(8, 0)) === 'light');
  check('12:00 是白天', T.scheduledTheme(sc, 'dark', at(12, 0)) === 'light');
  check('20:29 还是白天', T.scheduledTheme(sc, 'dark', at(20, 29)) === 'light');
  check('20:30 切换到夜间', T.scheduledTheme(sc, 'light', at(20, 30)) === 'dark');
  check('凌晨 03:00 是夜间', T.scheduledTheme(sc, 'light', at(3, 0)) === 'dark');
}

// ── 3. 跨午夜区间（例如 22:00–06:00 算白天）────────────────────────────
{
  const { T } = makeSandbox();
  const sc = { mode: 'daynight', day: 'ocean', night: 'violet', dayStart: '22:00', nightStart: '06:00' };
  check('23:00 在跨午夜白天区间内', T.scheduledTheme(sc, 'violet', at(23, 0)) === 'ocean');
  check('05:59 仍在白天区间内', T.scheduledTheme(sc, 'violet', at(5, 59)) === 'ocean');
  check('06:00 进入夜间', T.scheduledTheme(sc, 'ocean', at(6, 0)) === 'violet');
  check('12:00 是夜间', T.scheduledTheme(sc, 'ocean', at(12, 0)) === 'violet');
}

// ── 4. 手动 / 轮换 ────────────────────────────────────────────────────
{
  const { T } = makeSandbox();
  check('手动模式保持当前主题', T.scheduledTheme({ mode: 'manual' }, 'sepia', at(23, 0)) === 'sepia');
  const sc = { mode: 'rotate', interval: 15 };
  const first = T.scheduledTheme(sc, 'dark', new Date(2026, 0, 1, 10, 0));
  const sameSlot = T.scheduledTheme(sc, 'dark', new Date(2026, 0, 1, 10, 14, 59));
  const nextSlot = T.scheduledTheme(sc, 'dark', new Date(2026, 0, 1, 10, 15));
  check('同一 15 分钟窗口主题一致', first === sameSlot, `${first} / ${sameSlot}`);
  check('跨窗口后轮换到下一主题', first !== nextSlot, `${first} / ${nextSlot}`);
}

// ── 5. 存储与 <html> 应用 ─────────────────────────────────────────────
{
  const { T, store, meta, root } = makeSandbox();
  T.writePrefs('light', { mode: 'daynight', day: 'light', night: 'dark', dayStart: '07:30', nightStart: '21:45' });
  check('写入当前主题', store.get('az-theme') === 'light');
  check('写入定时设置', /07:30/.test(store.get('az-theme-schedule') || ''));
  check('读取主题', T.readTheme() === 'light');
  check('读取定时设置', T.readSchedule().dayStart === '07:30' && T.readSchedule().nightStart === '21:45');
  T.apply('light');
  check('应用 data-theme', root.dataset.theme === 'light');
  check('应用 data-theme-mode', root.dataset.themeMode === 'light');
  check('更新 theme-color', meta.content === '#fbfbfa', meta.content);
  T.apply('dark');
  check('切回深色', root.dataset.theme === 'dark' && root.dataset.themeMode === 'dark');
  check('非法主题回落到深色', T.apply('nope') === 'dark');
}

// ── 5b. cookie 兜底：桌面端换端口后 localStorage 按 origin 丢失 ─────────
{
  const { T, store } = makeSandbox();
  check('初始没有已保存主题', T.hasSaved() === false);
  T.writePrefs('ocean', { mode: 'daynight', day: 'light', night: 'dark', dayStart: '08:00', nightStart: '20:00' });
  store.delete('az-theme');
  store.delete('az-theme-schedule');
  check('换端口后从 cookie 读回主题', T.readTheme() === 'ocean');
  check('换端口后从 cookie 读回定时设置', T.readSchedule().dayStart === '08:00');
  check('有 cookie 也算已保存', T.hasSaved() === true);
}

// ── 6. 旧版设置缺少起止时间时自动补默认值 ─────────────────────────────
{
  const { T } = makeSandbox({ 'az-theme-schedule': JSON.stringify({ mode: 'daynight', day: 'light', night: 'dark' }) });
  const sc = T.readSchedule();
  check('旧设置补上白天起点', sc.dayStart === '06:00', sc.dayStart);
  check('旧设置补上夜间起点', sc.nightStart === '19:00', sc.nightStart);
}

// ── 7. 静态接线：V3 与设置页共用主题核心 ──────────────────────────────
{
  const V3_HTML = fs.readFileSync(path.join(ROOT, 'web/v3/index.html'), 'utf8');
  const V3_JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');
  const V3_CSS = fs.readFileSync(path.join(ROOT, 'web/v3/app.css'), 'utf8');
  const V2_HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
  check('V3 加载共用主题核心', V3_HTML.includes('/assets/theme.js'));
  check('V3 有快捷主题按钮', V3_HTML.includes('id="themeToggle"'));
  check('V3 支持浅色变量', V3_CSS.includes('html[data-theme="light"]'));
  check('V3 启动定时主题', V3_JS.includes('function startThemeSchedule(') && V3_JS.includes('startThemeSchedule();'));
  check('V3 接收设置页主题变化', V3_JS.includes("data.type !== 'atlasx:theme'"));
  check('V3 主题变化同步服务器', V3_JS.includes('/app/theme') && V3_JS.includes('function syncThemeToServer(')
    && V3_JS.includes('hasSaved'));
  check('设置页写主题也走共用核心（含 cookie）', fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8')
    .includes('AtlasTheme.writePrefs'));
  check('设置页加载共用主题核心', V2_HTML.includes('/assets/theme.js'));
  check('设置页可配置昼夜起止', V2_HTML.includes('id="themeDayStart"') && V2_HTML.includes('id="themeNightStart"'));
}

if (failures) {
  console.error(`\n[theme] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[theme] 全部通过');

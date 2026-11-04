/* AtlasX 共用主题核心：主题清单、明暗模式、按系统时间自动切换。
 *
 * V3 主壳与内嵌的 V2 设置页共用同一份 localStorage：
 *   az-theme           当前主题 id
 *   az-theme-schedule  定时切换设置（手动 / 昼夜 / 轮换）
 * 这里只做纯计算与 <html> 属性应用，不绑定任何界面。
 */
(() => {
  'use strict';

  const THEMES = [
    { id: 'dark', label: 'Dark' },
    { id: 'light', label: 'Light' },
    { id: 'midnight', label: 'Midnight' },
    { id: 'slate', label: 'Slate' },
    { id: 'ocean', label: 'Ocean' },
    { id: 'forest', label: 'Forest' },
    { id: 'sepia', label: 'Sepia' },
    { id: 'violet', label: 'Violet' },
  ];
  const THEME_BG = {
    dark: '#0d0d0e',
    light: '#fbfbfa',
    midnight: '#0e1216',
    slate: '#121416',
    ocean: '#f4f8fb',
    forest: '#0e1310',
    sepia: '#f6f1e7',
    violet: '#121016',
  };
  const THEME_MODE = {
    dark: 'dark', light: 'light', midnight: 'dark', slate: 'dark',
    ocean: 'light', forest: 'dark', sepia: 'light', violet: 'dark',
  };
  const DEFAULT_SCHEDULE = Object.freeze({
    mode: 'manual',       // manual | daynight | rotate
    day: 'light',
    night: 'dark',
    interval: 15,         // rotate：分钟
    dayStart: '06:00',    // 白天开始
    nightStart: '19:00',  // 夜间开始（也就是白天结束）
  });
  const THEME_KEY = 'az-theme';
  const SCHEDULE_KEY = 'az-theme-schedule';
  const THEME_IDS = new Set(THEMES.map((th) => th.id));
  const SCHEDULE_MODES = new Set(['manual', 'daynight', 'rotate']);

  /** "HH:MM" -> 当天分钟数；非法输入返回 null。 */
  function timeToMinutes(value) {
    const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim());
    if (!match) return null;
    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;
    if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
    return hours * 60 + minutes;
  }

  function clockText(minutes) {
    const hh = String(Math.floor(minutes / 60)).padStart(2, '0');
    const mm = String(minutes % 60).padStart(2, '0');
    return `${hh}:${mm}`;
  }

  function normalizeSchedule(raw) {
    const src = raw && typeof raw === 'object' ? raw : {};
    const interval = Math.max(5, Math.min(720, parseInt(src.interval, 10) || DEFAULT_SCHEDULE.interval));
    const dayStart = timeToMinutes(src.dayStart);
    const nightStart = timeToMinutes(src.nightStart);
    return {
      mode: SCHEDULE_MODES.has(src.mode) ? src.mode : DEFAULT_SCHEDULE.mode,
      day: THEME_IDS.has(src.day) ? src.day : DEFAULT_SCHEDULE.day,
      night: THEME_IDS.has(src.night) ? src.night : DEFAULT_SCHEDULE.night,
      interval,
      dayStart: dayStart == null ? DEFAULT_SCHEDULE.dayStart : clockText(dayStart),
      nightStart: nightStart == null ? DEFAULT_SCHEDULE.nightStart : clockText(nightStart),
    };
  }

  /** 当前是否处于「白天」区间；允许 dayStart > nightStart（跨午夜）。 */
  function isDaytime(schedule, now = new Date()) {
    const sc = normalizeSchedule(schedule);
    const start = timeToMinutes(sc.dayStart);
    const end = timeToMinutes(sc.nightStart);
    if (start == null || end == null || start === end) return false;
    const current = now.getHours() * 60 + now.getMinutes();
    return start < end
      ? current >= start && current < end
      : current >= start || current < end;
  }

  /** 按设置算出此刻应该使用的主题。 */
  function scheduledTheme(schedule, currentTheme = 'dark', now = new Date()) {
    const sc = normalizeSchedule(schedule);
    if (sc.mode === 'manual') return THEME_IDS.has(currentTheme) ? currentTheme : sc.day;
    if (sc.mode === 'daynight') return isDaytime(sc, now) ? sc.day : sc.night;
    const step = Math.max(5, sc.interval || DEFAULT_SCHEDULE.interval);
    const index = Math.floor(now.getTime() / (step * 60000)) % THEMES.length;
    return THEMES[index].id;
  }

  function readTheme() {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (THEME_IDS.has(saved)) return saved;
    } catch { /* localStorage 不可用时用默认值 */ }
    return 'dark';
  }

  function readSchedule() {
    try {
      const raw = localStorage.getItem(SCHEDULE_KEY);
      return normalizeSchedule(raw ? JSON.parse(raw) : null);
    } catch {
      return normalizeSchedule(null);
    }
  }

  function writePrefs(theme, schedule) {
    try {
      if (THEME_IDS.has(theme)) localStorage.setItem(THEME_KEY, theme);
      if (schedule) localStorage.setItem(SCHEDULE_KEY, JSON.stringify(normalizeSchedule(schedule)));
    } catch { /* 无痕模式等场景：仅本次会话生效 */ }
  }

  /** 把主题写到 <html>，并同步浏览器/WebView 的配色与状态栏颜色。 */
  function apply(theme) {
    const id = THEME_IDS.has(theme) ? theme : 'dark';
    const mode = THEME_MODE[id] || 'dark';
    const root = document.documentElement;
    if (root) {
      root.dataset.theme = id;
      root.dataset.themeMode = mode;
      try { root.style.colorScheme = mode; } catch { /* ignore */ }
    }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = THEME_BG[id] || THEME_BG.dark;
    return id;
  }

  function modeOf(theme) {
    return THEME_MODE[theme] || 'dark';
  }

  function applySaved() {
    const schedule = readSchedule();
    return apply(scheduledTheme(schedule, readTheme()));
  }

  window.AtlasTheme = {
    THEMES,
    THEME_BG,
    THEME_MODE,
    DEFAULT_SCHEDULE,
    KEYS: { theme: THEME_KEY, schedule: SCHEDULE_KEY },
    timeToMinutes,
    normalizeSchedule,
    isDaytime,
    scheduledTheme,
    readTheme,
    readSchedule,
    writePrefs,
    apply,
    modeOf,
    applySaved,
  };

  // 尽早应用，避免先亮一下默认主题再切换。
  if (document.documentElement) {
    try { applySaved(); } catch { /* 读取失败时保持页面上的默认主题 */ }
  }
})();

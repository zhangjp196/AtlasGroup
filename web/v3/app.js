/* AtlasX V3 shell: 工作区 / 角色 / 群组独立，聊天嵌入画布并支持多标签切换。 */
(() => {
  'use strict';
  const API = '/api/agent-zero';
  const GLOBAL_AGENT_ZONE = 'global-agent';
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const h = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const hue = (s) => {
    let n = 0;
    for (let i = 0; i < String(s).length; i += 1) n = ((n * 31) + String(s).charCodeAt(i)) >>> 0;
    return n % 360;
  };
  const av = (name, cls = '') => `<span class="avatar ${cls}" style="--h:${hue(name)}">${h((String(name || '·')[0] || '·').toUpperCase())}</span>`;
  const gaAvatar = (cls = '') => `<span class="avatar ga-avatar ${cls}"><svg viewBox="0 0 24 24"><rect x="4" y="8" width="16" height="11" rx="4"/><path d="M12 4v4M9 13h.01M15 13h.01M9 16h6"/></svg></span>`;
  const toast = (msg, tone = '') => {
    const el = document.createElement('div');
    el.className = `toast ${tone}`;
    el.textContent = msg;
    $('#toastWrap').appendChild(el);
    setTimeout(() => el.remove(), tone === 'bad' ? 6000 : 3000);
  };

  async function api(path, opts = {}) {
    const res = await fetch(API + path, {
      headers: { 'Content-Type': 'application/json' }, ...opts,
    });
    let body = null;
    try { body = await res.json(); } catch { /* ignore */ }
    if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
    return body && body.data !== undefined ? body.data : body;
  }

  window.addEventListener('error', (e) => {
    const wrap = document.getElementById('toastWrap');
    if (!wrap) return;
    const el = document.createElement('div');
    el.className = 'toast bad';
    el.textContent = 'JS: ' + (e.message || e.error);
    wrap.appendChild(el);
  });

  const S = {
    section: 'conv',
    workspaces: [], convs: [], roles: [], teams: [],
    windows: new Map(), activeWin: null, z: 100, models: [], activeModel: '', commands: null, 
    globalWin: null,
    selectedWs: null, wsPath: '.', wsFiles: null, wsPreview: null, wsLoading: false,
    settingsTab: 'models', settings: null, draft: null, toolCatalog: null,
    attention: null, gaTab: 'auth', gaOpen: false, gaTimer: null,
    gaAuto: false, gaAutoHandled: new Set(), gaNotifyCount: -1,
  };
  try { S.gaAuto = localStorage.getItem('atlasx.ga.auto') === '1'; } catch { /* ignore */ }

  /* ── 主题：暗 / 亮 + 按系统时间自动切换 ─────────────────── */
  const THEME_LABELS = {
    dark: '深色', light: '浅色', midnight: '午夜', slate: '石板',
    ocean: '海洋', forest: '森林', sepia: '复古', violet: '紫罗兰',
  };
  const themeCore = window.AtlasTheme || null;
  S.theme = themeCore ? themeCore.readTheme() : 'dark';
  S.themeSchedule = themeCore ? themeCore.readSchedule() : {
    mode: 'manual', day: 'light', night: 'dark', interval: 15, dayStart: '06:00', nightStart: '19:00',
  };
  S.themeTimer = null;

  function applyTheme(theme, opts = {}) {
    S.theme = themeCore ? themeCore.apply(theme) : theme;
    const auto = S.themeSchedule.mode !== 'manual';
    const btn = $('#themeToggle');
    if (btn) {
      const label = THEME_LABELS[S.theme] || S.theme;
      btn.title = auto
        ? `主题：${label} · 按系统时间自动切换（设置 → 外观可调整）`
        : `主题：${label}（点击切换明暗）`;
      btn.setAttribute('aria-label', btn.title);
      btn.classList.toggle('auto', auto);
    }
    const badge = $('#themeAuto');
    if (badge) badge.hidden = !auto;
    if (opts.persist !== false) persistThemePrefs();
    return S.theme;
  }

  function persistThemePrefs() {
    if (themeCore) themeCore.writePrefs(S.theme, S.themeSchedule);
  }

  function scheduledTheme() {
    if (!themeCore) return S.theme;
    return themeCore.scheduledTheme(S.themeSchedule, S.theme);
  }

  function tickTheme() {
    if (S.themeSchedule.mode === 'manual') return;
    const next = scheduledTheme();
    if (next !== S.theme) {
      applyTheme(next);
      broadcastThemeToSettings();
    }
  }

  function startThemeSchedule() {
    if (S.themeTimer) { clearInterval(S.themeTimer); S.themeTimer = null; }
    tickTheme();
    if (S.themeSchedule.mode !== 'manual') {
      S.themeTimer = setInterval(tickTheme, 20000);
    }
    applyTheme(S.theme, { persist: false });
  }

  function toggleTheme() {
    // 快捷按钮只在明 / 暗之间切换；要自动切换请到「设置 → 外观」。
    const wasAuto = S.themeSchedule.mode !== 'manual';
    const next = (themeCore ? themeCore.modeOf(S.theme) : 'dark') === 'light' ? 'dark' : 'light';
    S.themeSchedule.mode = 'manual';
    applyTheme(next);
    startThemeSchedule();
    broadcastThemeToSettings();
    toast(wasAuto
      ? `已切换为${THEME_LABELS[next] || next}主题，按时间自动切换已关闭`
      : `已切换到${THEME_LABELS[next] || next}主题`, 'ok', wasAuto ? 2200 : 1400);
  }

  function broadcastThemeToSettings() {
    const frame = $('.settings-frame');
    if (!frame || !frame.contentWindow) return;
    try {
      frame.contentWindow.postMessage({
        type: 'atlasx:theme-apply', theme: S.theme, schedule: S.themeSchedule,
      }, location.origin);
    } catch { /* ignore */ }
  }

  // 内嵌设置页改了主题 / 定时设置时同步外壳。
  window.addEventListener('message', (event) => {
    if (event.origin !== location.origin) return;
    const data = event.data || {};
    if (data.type !== 'atlasx:theme') return;
    if (data.schedule && themeCore) S.themeSchedule = themeCore.normalizeSchedule(data.schedule);
    if (data.theme) applyTheme(data.theme);
    startThemeSchedule();
  });

  // 另一个标签页 / 窗口改了设置时也跟随。
  window.addEventListener('storage', (event) => {
    if (!themeCore || (event.key !== themeCore.KEYS.theme && event.key !== themeCore.KEYS.schedule)) return;
    S.theme = themeCore.readTheme();
    S.themeSchedule = themeCore.readSchedule();
    startThemeSchedule();
    broadcastThemeToSettings();
  });

  // 一个会话是群聊的唯一判据：绑定的团队真的有成员。
  // 旧版本会把「只有主持」的默认团队绑到单聊上；那种不算群聊，
  // 否则单聊会被显示成「默认群聊」并出现 @成员 / 主持口吻。
  function convParts(conv) {
    const team = S.teams.find((t) => t.id === conv.team_id) || null;
    const members = (team && Array.isArray(team.members)) ? team.members : [];
    const role = S.roles.find((r) => r.id === conv.role_id) || null;
    return { team, members, role, isGroup: !!(team && members.length) };
  }

  // 会话标题：群聊显示群名、单聊显示角色名，避免用首条消息当标题
  function convTitle(conv) {
    if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) return '全局 Agent';
    const { team, role, isGroup } = convParts(conv);
    if (isGroup && team) return team.name;
    if (role) return role.name;
    return conv.title || '未命名会话';
  }

  function convMeta(conv) {
    if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) {
      return { kind: '全局', sub: '可代替你管理 App' };
    }
    const { team, role, members, isGroup } = convParts(conv);
    if (isGroup && team) {
      return { kind: '群聊', sub: `${members.length} 个角色 · ${modeLabel(team.mode)}` };
    }
    return { kind: '单聊', sub: role && role.name ? `角色：${role.name}` : '默认角色' };
  }

  function convIsGroup(conv) {
    return convParts(conv).isGroup;
  }

  /* ── 列表 ─────────────────────────────────────────────── */
  const TITLES = { conv: '会话', ws: '工作区', role: '角色', group: '群组', settings: '设置', about: '关于' };
  const ADD_LABEL = { conv: '新建会话', ws: '添加工作区', role: '新建角色', group: '新建群组' };

  async function loadAll() {
    const [ws, conv, roles, teams, models] = await Promise.all([
      api('/workspaces?include_archived=true').catch(() => ({ items: [] })),
      api('/conversations?limit=80').catch(() => ({ items: [] })),
      api('/roles').catch(() => ({ items: [] })),
      api('/teams').catch(() => ({ items: [] })),
      api('/app/models').catch(() => ({ items: [], active: '' })),
    ]);
    S.models = models.items || [];
    S.activeModel = models.active || '';
    S.workspaces = (ws.items || []).filter((x) => !x.archived);
    S.convs = conv.items || [];
    S.roles = roles.items || [];
    S.teams = teams.items || [];
    // 会话改名 / 群名变更后，已打开的标签也跟着更新。
    S.windows.forEach((w) => {
      const fresh = S.convs.find((c) => c.id === w.convId);
      if (!fresh) return;
      w.conv = { ...w.conv, ...fresh };
      const title = convTitle(w.conv);
      if (title && title !== w.title) {
        w.title = title;
        const b = $('.win-title b', w.el);
        if (b) b.textContent = title;
      }
    });
    renderList();
    renderCanvas();
  }

  function setSection(name) {
    if (name === 'about') { openAboutModal(); return; }
    S.section = name;
    $$('.rail-btn').forEach((b) => b.classList.toggle('active', b.dataset.section === name));
    $('#listTitle').textContent = TITLES[name] || '';
    renderList();
    renderCanvas();
  }

  function renderList() {
    const body = $('#listBody');
    const q = ($('#searchInput').value || '').trim().toLowerCase();
    const match = (s) => !q || String(s || '').toLowerCase().includes(q);
    let html = '';
    if (S.section === 'conv') {
      const items = S.convs.filter((c) => {
        if (String(c.zone || '') === GLOBAL_AGENT_ZONE) return false;
        const title = convTitle(c);
        return match(title) || match(c.last_message);
      });
      html = items.length ? items.map((c) => {
        const open = [...S.windows.values()].some((w) => w.convId === c.id);
        const title = convTitle(c);
        const group = convIsGroup(c);
        const running = !!c.active;
        const unread = !!c.unread;
        return `<button class="row${open ? ' active' : ''}" data-conv="${h(c.id)}">
          ${av(title || '会', group ? '' : 'round')}
          <span class="row-main">
            <span class="row-title">${h(title)}
              <span class="tag${group ? ' grp' : ''}">${group ? '群' : '单'}</span>
              ${running ? '<span class="dot run" title="运行中"></span>' : (unread ? '<span class="dot" title="未读"></span>' : '')}</span>
            <span class="row-sub">${h(c.last_message || c.updated_at || '还没有消息')}</span>
          </span>
          <span class="row-time">${h(shortTime(c.updated_at))}</span>
        </button>`;
      }).join('') : `<div class="empty">还没有会话<br>点右上角 + 新建，或从角色 / 群组开始</div>`;
    } else if (S.section === 'ws') {
      const items = S.workspaces.filter((w) => match(w.name));
      html = items.map((w) => `<button class="row${S.selectedWs === w.id ? ' active' : ''}" data-ws="${h(w.id)}">
        ${av(w.name || 'W')}
        <span class="row-main"><span class="row-title">${h(w.name)}</span>
        <span class="row-sub">${h(w.path || '')}</span></span>
      </button>`).join('') || `<div class="empty">还没有工作区<br>工作区只提供文件与终端上下文</div>`;
    } else if (S.section === 'role') {
      const items = S.roles.filter((r) => match(r.name));
      html = items.map((r) => `<button class="row" data-role="${h(r.id)}">
        ${av(r.name || '角', 'round')}
        <span class="row-main"><span class="row-title">${h(r.name)}
          ${r.scope === 'temporary' ? '<span class="tag tmp">临时</span>' : '<span class="tag">持久</span>'}</span>
          <span class="row-sub">${h(r.expertise || r.persona || '')}</span></span>
      </button>`).join('') || `<div class="empty">角色库是空的<br>新建一个，或在对话里让 LLM 创建</div>`;
    } else if (S.section === 'group') {
      const items = S.teams.filter((t) => match(t.name));
      html = items.map((t) => `<button class="row" data-group="${h(t.id)}">
        ${av(t.name || '群')}
        <span class="row-main"><span class="row-title">${h(t.name)}</span>
        <span class="row-sub">${(t.members || []).length} 个角色 · ${modeLabel(t.mode)}</span></span>
      </button>`).join('') || `<div class="empty">还没有群组<br>群组 = 一组角色 + 协作方式，不绑定工作区</div>`;
    } else if (S.section === 'settings') {
      // 直接铺满 V2 原版设置页：不再套一层「模型与订阅 / 关于」导航。
      body.innerHTML = `<iframe class="settings-frame" title="设置"
        src="/classic?settings=1&embed=1&tab=models"></iframe>`;
    } else {
      html = `<div class="empty">AtlasX V3 · 界面重构预览<br>工作区 / 角色 / 群组相互独立</div>`;
    }
    body.innerHTML = html;
    const foot = {
      conv: `${S.convs.length} 个会话 · ${S.windows.size} 个会话打开`,
      ws: `${S.workspaces.length} 个工作区`,
      role: `${S.roles.filter((r) => r.scope !== 'temporary').length} 个持久角色`,
      group: `${S.teams.length} 个群组 · 不绑定工作区`,
      about: '',
    }[S.section];
    $('#listFoot').innerHTML = `<span>${h(foot || '')}</span>`;
    $$('[data-conv]', body).forEach((el) => {
      el.addEventListener('click', () => { closeMobileList(); openConversation(el.dataset.conv); });
      el.addEventListener('contextmenu', (e) => {
        const conv = S.convs.find((c) => c.id === el.dataset.conv);
        if (conv) convMenu(e, conv);
      });
    });
    $$('[data-ws]', body).forEach((el) => {
      el.addEventListener('click', () => {
        S.selectedWs = el.dataset.ws; S.wsPath = '.'; S.wsFiles = null; S.wsPreview = null;
        renderList(); renderCanvas();
      });
      el.addEventListener('contextmenu', (e) => {
        const ws = S.workspaces.find((w) => w.id === el.dataset.ws);
        if (ws) workspaceMenu(e, ws);
      });
    });
    $$('[data-role]', body).forEach((el) => {
      el.addEventListener('click', () => { closeMobileList(); openRoleChat(el.dataset.role); });
      el.addEventListener('contextmenu', (e) => {
        const role = S.roles.find((r) => r.id === el.dataset.role);
        if (role) roleMenu(e, role);
      });
    });
    $$('[data-group]', body).forEach((el) => {
      el.addEventListener('click', () => { closeMobileList(); openGroupChat(el.dataset.group); });
      el.addEventListener('contextmenu', (e) => {
        const team = S.teams.find((t) => t.id === el.dataset.group);
        if (team) groupMenu(e, team);
      });
    });
    $$('[data-stab]', body).forEach((el) => el.addEventListener('click', () => {
      S.settingsTab = el.dataset.stab; renderList(); renderCanvas();
    }));
  }

  const modeLabel = (m) => ({ orchestrate: '主持编排', roundtable: '圆桌', single: '单角色' }[m] || '主持编排');
  const bytes = (n) => (n > 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' MB'
    : n > 1024 ? (n / 1024).toFixed(1) + ' KB' : (n || 0) + ' B');

  // 会话列表时间：今天只给 HH:MM，昨天给「昨天」，更早给 MM-DD。
  function shortTime(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const s = raw.replace('T', ' ');
    const day = s.slice(0, 10);
    const clock = s.slice(11, 16);
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    if (day === today) return clock || '';
    const yesterday = new Date(now.getTime() - 86400000);
    const ymd = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, '0')}-${String(yesterday.getDate()).padStart(2, '0')}`;
    if (day === ymd) return '昨天';
    return day.slice(5) || '';
  }

  function durationText(ms) {
    const n = Number(ms) || 0;
    if (!n) return '';
    return n < 1000 ? `${n}ms` : `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}s`;
  }

  function firstLine(text, max = 110) {
    const one = String(text || '').replace(/\s+/g, ' ').trim();
    return one.length > max ? `${one.slice(0, max)}…` : one;
  }

  async function loadWsDir(wsId, path) {
    S.wsLoading = true; renderCanvas();
    try {
      S.wsFiles = await api(`/workspace/list?workspace_id=${encodeURIComponent(wsId)}&path=${encodeURIComponent(path || '.')}`);
    } catch (err) {
      S.wsFiles = []; toast(err.message, 'bad');
    } finally {
      S.wsLoading = false; renderCanvas();
    }
  }

  async function openWsFile(wsId, path) {
    try {
      const data = await api(`/workspace/file?workspace_id=${encodeURIComponent(wsId)}&path=${encodeURIComponent(path)}`);
      S.wsPreview = { path, text: data.text || '', size: data.size || 0, truncated: !!data.truncated };
      renderCanvas();
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function bindWorkspaceTo(win, wsId) {
    try {
      const out = await api(`/conversations/${encodeURIComponent(win.convId)}/workspace`, {
        method: 'PATCH', body: JSON.stringify({ workspace_id: wsId }),
      });
      win.conv.bound_workspace_id = out.bound_workspace_id || '';
      const chip = $('[data-bind-ws]', win.el);
      if (chip) chip.textContent = boundLabel(win.conv);
      toast('已绑定到当前会话', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── 画布 ─────────────────────────────────────────────── */
  function renderCanvas() {
    const crumb = $('#crumb');
    const actions = $('#canvasActions');
    const body = $('#canvasBody');
    body.classList.remove('settings-full');
    const shell = $('#app');
    if (shell) shell.classList.toggle('settings-mode', S.section === 'settings');
    const chatVisible = S.section === 'conv' && S.windows.size > 0 && !!S.activeWin;
    showChat(chatVisible);
    if (chatVisible) return;
    if (S.section === 'conv') {
      crumb.innerHTML = `<span>桌面</span><b>打开会话后在标签间切换</b><span class="chip">${S.windows.size} 个会话</span>`;
      actions.innerHTML = `<button class="btn primary" data-new="conv">新建会话</button>`;
      body.innerHTML = `<div class="hero"><div><h1>工作区桌面</h1>
        <p>打开会话后，聊天会嵌入在这里，用顶部标签在多个会话之间切换。工作区只负责文件、终端与项目上下文。</p></div></div>
        <div class="cards">
          <article class="card"><h3>打开的会话</h3>
            <p>${S.windows.size ? [...S.windows.values()].map((w) => h(w.title)).join(' · ') : '还没有打开会话'}</p>
            <div class="chips">${[...S.windows.values()].map((w) => `<span class="chip">${h(w.title)}</span>`).join('') || '<span class="chip">从左侧会话开始</span>'}</div></article>
          <article class="card"><h3>角色</h3><p>${S.roles.length} 个角色可直接单聊；临时角色只属于一个会话。</p>
            <div class="chips">${S.roles.slice(0, 5).map((r) => `<span class="chip">${h(r.name)}</span>`).join('') || '<span class="chip">角色库为空</span>'}</div></article>
          <article class="card"><h3>群组</h3><p>${S.teams.length} 个群组；群组不绑定工作区，会话可单独绑。</p>
            <div class="chips">${S.teams.slice(0, 4).map((t) => `<span class="chip">${h(t.name)}</span>`).join('') || '<span class="chip">还没有群组</span>'}</div></article>
        </div>`;
    } else if (S.section === 'ws') {
      const ws = S.workspaces.find((w) => w.id === S.selectedWs) || S.workspaces[0];
      S.selectedWs = ws ? ws.id : null;
      crumb.innerHTML = `<span>工作区</span><b>${h(ws ? ws.name : '未选择')}</b>${ws ? `<span class="chip">${h(S.wsPath === '.' ? '/' : '/' + S.wsPath)}</span>` : ''}`;
      actions.innerHTML = `<button class="btn" data-add-workspace>＋ 添加</button>`
        + (ws ? `<button class="btn" data-ws-rename>重命名</button><button class="btn" data-ws-up>上一级</button><button class="btn" data-ws-reveal>打开文件夹</button>` : '');
      if (!ws) {
        body.innerHTML = `<div class="empty">选择或添加一个工作区</div>`;
      } else {
        if (!S.wsFiles && !S.wsLoading) loadWsDir(ws.id, S.wsPath);
        const files = S.wsFiles || [];
        const preview = S.wsPreview;
        body.innerHTML = `<div class="hero"><div><h1>${h(ws.name)}</h1>
          <p>${h(ws.path || '')} · 工作区只提供文件与终端上下文，不承载聊天或角色。</p></div>
          <div class="head-actions" style="margin-left:auto"><button class="btn" data-ws-bind>绑定到当前窗口</button></div></div>
          <div style="display:grid;grid-template-columns:minmax(220px,320px) minmax(0,1fr);gap:14px;min-height:320px">
            <aside class="editor" style="padding:10px;gap:2px;max-width:none">
              <div class="role-line" style="padding:4px 8px;color:var(--dim)">${h(S.wsPath === '.' ? '/' : '/' + S.wsPath)}</div>
              <div class="file-list">${S.wsLoading ? '<div>加载中…</div>' : (files.length ? files.map((f) => `
                <div data-ws-item="${h(f.path)}" data-ws-type="${h(f.type)}">${f.type === 'dir' ? '▸ ' : '· '}${h(f.name)}</div>`).join('') : '<div style="color:var(--faint)">空目录</div>')}</div>
            </aside>
            <article class="editor" style="max-width:none;padding:0;overflow:hidden;min-width:0">
              ${preview ? `<div style="display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);font-size:11px;color:var(--faint);min-width:0">
                <b style="color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${h(preview.path)}</b>
                <span>·</span><span>${bytes(preview.size)}</span><span>·</span><span>只读预览</span>
                <span style="margin-left:auto;cursor:pointer" data-ws-preview-close>关闭</span></div>
                <pre style="margin:0;padding:14px;font:11.5px/1.7 var(--mono);color:#aab6c8;overflow:auto;max-height:520px;white-space:pre-wrap">${h(preview.text || '')}</pre>`
                : `<div class="empty" style="padding:60px 20px">选择左侧文件查看只读预览<br><span style="font-size:10.5px">聊天窗口需要文件上下文时，在标题栏绑定工作区</span></div>`}
            </article>
          </div>`;
        $$('[data-ws-item]', body).forEach((el) => el.addEventListener('click', () => {
          if (el.dataset.wsType === 'dir') { S.wsPath = el.dataset.wsItem; S.wsPreview = null; S.wsFiles = null; loadWsDir(ws.id, S.wsPath); }
          else openWsFile(ws.id, el.dataset.wsItem);
        }));
        const up = $('[data-ws-up]', actions);
        if (up) up.addEventListener('click', () => {
          const parts = (S.wsPath === '.' ? [] : S.wsPath.split('/'));
          parts.pop();
          S.wsPath = parts.length ? parts.join('/') : '.';
          S.wsPreview = null; S.wsFiles = null; loadWsDir(ws.id, S.wsPath);
        });
        const add = $('[data-add-workspace]', actions);
        if (add) add.addEventListener('click', () => addWorkspace());
        const rename = $('[data-ws-rename]', actions);
        if (rename) rename.addEventListener('click', () => renameWorkspace(ws));
        const reveal = $('[data-ws-reveal]', actions);
        if (reveal) reveal.addEventListener('click', async () => {
          try {
            await api('/workspace/reveal', {
              method: 'POST', body: JSON.stringify({ workspace_id: ws.id, path: '.' }),
            });
          } catch (err) { toast(err.message, 'bad'); }
        });
        const close = $('[data-ws-preview-close]', body);
        if (close) close.addEventListener('click', () => { S.wsPreview = null; renderCanvas(); });
        const bind = $('[data-ws-bind]', body);
        if (bind) bind.addEventListener('click', () => {
          const win = S.activeWin;
          if (!win) { toast('先打开一个会话窗口', 'bad'); return; }
          bindWorkspaceTo(win, ws.id);
        });
      }
    } else if (S.section === 'role') {
      crumb.innerHTML = `<span>角色库</span><b>独立于工作区</b>`;
      actions.innerHTML = `<button class="btn primary" data-new="role">＋ 新建角色</button>`;
      body.innerHTML = `<div class="hero"><div><h1>角色库</h1>
        <p>角色是独立人格，不属于工作区也不属于群组。持久角色跨会话复用；临时角色只活在一个会话里；LLM 可以在对话中直接创建。</p></div></div>
        <div class="role-grid">${S.roles.map((r) => `<article class="role-card" data-role-card="${h(r.id)}">
          <div class="role-top">${av(r.name || '角', 'round')}<span style="min-width:0"><b>${h(r.name)}</b><br><span class="role-line">${h(r.expertise || '')}</span></span>
            <span class="tag ${r.scope === 'temporary' ? 'tmp' : ''}" style="margin-left:auto">${r.scope === 'temporary' ? '临时' : (r.source === 'llm' ? 'LLM' : '持久')}</span></div>
          <div class="role-line">${r.model_code ? `模型：${h(r.model_code)}` : '模型：跟随全局'}</div>
          <div class="chips">${(r.tools || []).slice(0, 6).map((x) => `<span class="chip">${h(x)}</span>`).join('') || '<span class="chip">无工具</span>'}</div>
        </article>`).join('') || '<div class="empty">还没有角色</div>'}</div>`;
      $$('[data-role-card]', body).forEach((el) => {
        el.addEventListener('click', () => openRoleChat(el.dataset.roleCard));
        el.addEventListener('contextmenu', (e) => {
          const role = S.roles.find((r) => r.id === el.dataset.roleCard);
          if (role) roleMenu(e, role);
        });
      });
    } else if (S.section === 'group') {
      crumb.innerHTML = `<span>群组</span><b>不绑定工作区</b>`;
      actions.innerHTML = `<button class="btn primary" data-new="group">＋ 新建群组</button>`;
      const cards = S.teams.map((t) => `<article class="card"><h3>${h(t.name)} <span class="tag grp">${modeLabel(t.mode)}</span></h3>
        <p>${(t.members || []).length} 个角色</p>
        <div class="chips">${(t.members || []).slice(0, 8).map((m) => `<span class="chip">${h(m.name)}</span>`).join('') || '<span class="chip">暂无成员</span>'}</div>
        <div class="chips" style="margin-top:auto"><button class="btn" data-group-open="${h(t.id)}">打开群聊</button><button class="btn" data-group-edit="${h(t.id)}">编辑</button></div>
      </article>`).join('');
      body.innerHTML = `<div class="hero"><div><h1>群组</h1>
        <p>群组 = 一组角色 + 协作方式，没有 workspace_id。需要文件上下文时，给某个会话单独绑定。</p></div></div>
        <div class="cards">${cards || '<div class="empty">还没有群组</div>'}</div>`;
      $$('.card', body).forEach((card) => {
        const btn = card.querySelector('[data-group-open]');
        if (!btn) return;
        const team = S.teams.find((t) => t.id === btn.dataset.groupOpen);
        if (team) card.addEventListener('contextmenu', (e) => groupMenu(e, team));
      });
      $$('[data-group-open]', body).forEach((el) => el.addEventListener('click', () => openGroupChat(el.dataset.groupOpen)));
      $$('[data-group-edit]', body).forEach((el) => el.addEventListener('click', () => openGroupDetail(el.dataset.groupEdit)));
    } else if (S.section === 'settings') {
      crumb.innerHTML = `<span>设置</span><b>${h(({ models: '模型与订阅', about: '关于' })[S.settingsTab] || '')}</b>`;
      actions.innerHTML = '';
      body.innerHTML = '<div class="empty">加载设置…</div>';
      renderSettings();
    } else {
      crumb.innerHTML = `<span>关于</span><b>AtlasX</b>`;
      actions.innerHTML = '';
      body.innerHTML = `<div class="hero"><div><h1>AtlasX</h1>
        <p>本地单用户 Agent 工作台 · V3 界面重构预览。左侧四个空间相互独立：会话、工作区、角色、群组；聊天嵌入画布，用顶部标签在多个会话之间切换。</p>
        <p>旧界面与旧数据已移除；所有入口都在本界面内。MCP / 技能 / 诊断等扩展设置正在迁移。</p></div></div>`;
    }
    $$('[data-new]').forEach((el) => el.addEventListener('click', () => onNew(el.dataset.new)));
    $$('[data-folder]', body).forEach((el) => el.addEventListener('click', () => toast('文件面板将在下一阶段接入')));
  }

  function onNew(kind) {
    if (kind === 'conv') return newConversation();
    if (kind === 'ws') return addWorkspace();
    if (kind === 'role') return openRoleEditor(null);
    if (kind === 'group') return newGroup();
    return null;
  }

  async function openAboutModal() {
    let info = {};
    try { info = await api('/app/info'); } catch { /* ignore */ }
    const llm = info.llm || {};
    const active = (llm.models || []).find((m) => m.code === llm.active_model) || {};
    await modal({
      title: '关于 AtlasX',
      fields: `<div class="about-modal">
        <div class="about-brand">${av('A')}<div><b>AtlasX</b><div class="role-line">本地单用户 Agent 工作台 · ${h(info.version || '')}</div></div></div>
        <div class="settings-fields">
          <div class="field"><span>运行模式</span><div class="role-line">${h(info.mode || 'local')} · ${h(info.name || 'AtlasX')}</div></div>
          <div class="field"><span>当前模型</span><div class="role-line">${h(active.effective_model || active.model || '未配置')}${llm.active_model ? ` （${h(llm.active_model)}）` : ''}</div></div>
          <div class="field"><span>数据库</span><div class="role-line mono">${h(info.db_path || '')}</div></div>
          <div class="field"><span>配置</span><div class="role-line mono">${h(info.config_path || '')}</div></div>
        </div>
        <div class="hintline">模型 / 订阅 / MCP / 技能 / 用量等设置：点左下角齿轮。</div>
      </div>`,
      buttons: [{ label: '关闭', primary: true, value: 'close' }],
      read: () => true,
    });
  }

  /* ── 右键菜单 ─────────────────────────────────────────── */
  function closeCtx() {
    const el = $('#ctxMenu');
    if (el) { el.hidden = true; el.innerHTML = ''; }
  }

  function openCtx(event, items) {
    event.preventDefault();
    event.stopPropagation();
    const menu = $('#ctxMenu');
    if (!menu) return;
    menu.innerHTML = items.map((it, i) => {
      if (it.sep) return '<div class="ctx-sep"></div>';
      return `<button class="ctx-item${it.danger ? ' danger' : ''}" data-ctx="${i}" ${it.disabled ? 'disabled' : ''}>${it.icon ? `<span>${it.icon}</span>` : ''}<span>${h(it.label)}</span>${it.hint ? `<span class="hint">${h(it.hint)}</span>` : ''}</button>`;
    }).join('');
    menu.hidden = false;
    const rect = menu.getBoundingClientRect();
    const x = Math.min(event.clientX, window.innerWidth - rect.width - 8);
    const y = Math.min(event.clientY, window.innerHeight - rect.height - 8);
    menu.style.left = Math.max(6, x) + 'px';
    menu.style.top = Math.max(6, y) + 'px';
    $$('[data-ctx]', menu).forEach((btn) => btn.addEventListener('click', () => {
      const item = items[Number(btn.dataset.ctx)];
      closeCtx();
      if (item && typeof item.action === 'function') Promise.resolve(item.action()).catch((err) => toast(err.message, 'bad'));
    }));
  }

  document.addEventListener('click', () => closeCtx());
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCtx(); });
  window.addEventListener('blur', () => closeCtx());

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(String(text || ''));
      toast('已复制', 'ok', 1200);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = String(text || '');
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); toast('已复制', 'ok', 1200); }
      catch { toast('复制失败', 'bad'); }
      ta.remove();
    }
  }

  async function openRoleChat(roleId, forceNew = false) {
    const role = S.roles.find((r) => r.id === roleId);
    if (!role) return;
    try {
      if (!forceNew) {
        // 单聊复用：同一个角色已有单聊就切过去，不重复堆会话。
        const existing = S.convs.find((c) => c.role_id === roleId && !convIsGroup(c));
        if (existing) return openConversation(existing.id);
      }
      const conv = await api('/conversations', {
        method: 'POST', body: JSON.stringify({ role_id: roleId }),
      });
      await loadAll();
      openConversation(conv.id);
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function duplicateRole(role) {
    try {
      const copy = await api('/roles', {
        method: 'POST',
        body: JSON.stringify({
          name: `${role.name} 副本`, expertise: role.expertise, persona: role.persona,
          tools: role.tools, model_code: role.model_code || null, source: 'user',
          scope: 'persistent',
        }),
      });
      await loadAll();
      toast(`已复制角色「${copy.name}」`, 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function addRoleToGroup(role) {
    if (!S.teams.length) { toast('还没有群组', 'bad'); return; }
    const saved = await modal({
      title: `把「${role.name}」加入群组`,
      fields: `<div class="field"><label>目标群组</label><select class="input" id="addRoleGroup">
        ${S.teams.map((t) => `<option value="${h(t.id)}">${h(t.name)}</option>`).join('')}</select></div>`,
      read: () => ({ group: $('#addRoleGroup').value }),
    });
    if (!saved) return;
    const team = S.teams.find((t) => t.id === saved.group);
    if (!team) return;
    try {
      const members = (team.members || []).map((m) => ({ id: m.id, name: m.name, expertise: m.expertise }))
        .concat([{ id: role.id, name: role.name, expertise: role.expertise }]);
      await api(`/teams/${encodeURIComponent(team.id)}`, {
        method: 'PATCH', body: JSON.stringify({ members }),
      });
      await loadAll(); toast(`已加入「${team.name}」`, 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function renameConversation(conv) {
    const saved = await modal({
      title: '重命名会话',
      fields: `<div class="field"><label>标题</label><input class="input" id="convNewTitle" value="${h(conv.title || '')}" maxlength="200" /></div>`,
      read: () => ({ title: $('#convNewTitle').value.trim() }),
    });
    if (!saved || !saved.title) return;
    try {
      await api(`/conversations/${encodeURIComponent(conv.id)}`, {
        method: 'PATCH', body: JSON.stringify({ title: saved.title }),
      });
      const win = S.windows.get(conv.id);
      if (win) {
        win.title = saved.title;
        win.conv.title = saved.title;
        const b = $('.win-title b', win.el); if (b) b.textContent = saved.title;
      }
      if (S.globalWin && S.globalWin.convId === conv.id) {
        S.globalWin.conv.title = saved.title;
        const b = $('.win-title b', S.globalWin.el); if (b) b.textContent = saved.title;
      }
      await loadAll(); toast('已重命名', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deleteConversation(conv) {
    if (!confirm(`删除会话「${convTitle(conv)}」？消息会一并删除。`)) return;
    try {
      await api(`/conversations/${encodeURIComponent(conv.id)}`, { method: 'DELETE' });
      const win = S.windows.get(conv.id);
      if (win) closeWin(win);
      if (S.globalWin && S.globalWin.convId === conv.id) closeWin(S.globalWin);
      await loadAll(); toast('会话已删除', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function duplicateTeam(team) {
    try {
      const copy = await api('/teams', {
        method: 'POST',
        body: JSON.stringify({
          name: `${team.name} 副本`, mode: team.mode, max_speakers: team.max_speakers,
          leader: team.leader || { name: '主持' },
          members: (team.members || []).map((m) => ({ id: m.id, name: m.name, expertise: m.expertise })),
          bind: false,
        }),
      });
      await loadAll();
      toast(`已复制群组「${copy.name}」`, 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deleteTeam(team) {
    if (!confirm(`删除群组「${team.name}」？绑定会话会退回单聊。`)) return;
    try {
      await api(`/teams/${encodeURIComponent(team.id)}`, { method: 'DELETE' });
      await loadAll(); toast('群组已删除', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── 添加工作区：目录选择器（对齐 V2 的文件夹选择）─────────── */
  function pickFolderModal() {
    return new Promise((resolve) => {
      const back = $('#modal');
      const bodyEl = $('#modalBody');
      const foot = $('#modal .modal-foot');
      $('#modalTitle').textContent = '添加工作区';
      bodyEl.innerHTML = `
        <div class="field"><label>工作区名称</label>
          <input class="input" id="pickName" maxlength="120" placeholder="默认使用文件夹名" spellcheck="false" /></div>
        <div class="field"><label>选择文件夹</label>
          <div class="folder-path-row">
            <button type="button" class="iconbtn" data-pick-up title="上一级" aria-label="上一级">
              <svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg></button>
            <input class="input mono folder-path-input" id="pickPath" spellcheck="false" autocomplete="off" placeholder="输入绝对路径，或点文件夹进入" aria-label="文件夹路径" />
            <button type="button" class="btn small" id="pickGo" title="前往这个路径">前往</button>
            <button type="button" class="iconbtn" data-pick-home title="主目录" aria-label="主目录">
              <svg viewBox="0 0 24 24"><path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1Z"/></svg></button>
            <button type="button" class="iconbtn" data-pick-native hidden title="在系统对话框中选择" aria-label="在系统对话框中选择">
              <svg viewBox="0 0 24 24"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z"/></svg></button>
          </div>
          <div class="folder-filter">
            <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>
            <input class="input" id="pickFilter" placeholder="筛选当前目录的文件夹" autocomplete="off" spellcheck="false" />
          </div>
          <div class="recent-row" id="pickRecent" hidden></div>
          <div class="folder-list" id="pickList" role="listbox" aria-label="文件夹"></div>
          <div class="folder-note" id="pickNote"></div>
        </div>
        <div class="hintline">点文件夹进入，到达目标目录后「使用此文件夹」。路径可以直接修改。工作区只提供文件与终端上下文，不承载聊天或角色；会话可单独绑定。</div>`;
      foot.innerHTML = `<span class="save-note" id="pickSaveNote"></span>
        <button class="btn" id="pickCancel">取消</button>
        <button class="btn primary" id="pickUse">使用此文件夹</button>`;

      const listEl = $('#pickList');
      const pathEl = $('#pickPath');
      const goBtn = $('#pickGo');
      const noteEl = $('#pickNote');
      const nameEl = $('#pickName');
      const filterEl = $('#pickFilter');
      const recentEl = $('#pickRecent');
      const upBtn = $('[data-pick-up]', bodyEl);
      const nativeBtn = $('[data-pick-native]', bodyEl);
      const saveNote = $('#pickSaveNote');
      const nativeApi = window.pywebview && window.pywebview.api;
      if (nativeApi && typeof nativeApi.pick_folder === 'function') nativeBtn.hidden = false;

      const cur = { path: '', parent: null, items: [], files: 0 };
      let nameTouched = false;
      let selIndex = -1;
      nameEl.addEventListener('input', () => { nameTouched = true; });

      const readRecent = () => {
        try { return JSON.parse(localStorage.getItem('atlasx.addws.recent') || '[]'); }
        catch { return []; }
      };
      const rememberRecent = (path) => {
        if (!path) return;
        const list = [path, ...readRecent().filter((p) => p !== path)].slice(0, 6);
        try { localStorage.setItem('atlasx.addws.recent', JSON.stringify(list)); } catch { /* ignore */ }
      };
      const paintRecent = () => {
        const recents = readRecent().filter((p) => p && p !== cur.path);
        if (!recents.length) { recentEl.hidden = true; recentEl.innerHTML = ''; return; }
        recentEl.hidden = false;
        recentEl.innerHTML = recents.map((p) => `<button type="button" class="chip recent" data-recent="${h(p)}" title="${h(p)}">${h(String(p).split('/').pop() || p)}</button>`).join('');
        $$('[data-recent]', recentEl).forEach((el) => el.addEventListener('click', () => load(el.dataset.recent)));
      };

      const visibleItems = () => {
        const q = (filterEl.value || '').trim().toLowerCase();
        return q ? cur.items.filter((it) => String(it.name).toLowerCase().includes(q)) : cur.items;
      };

      const paintRows = () => {
        const items = visibleItems();
        listEl.innerHTML = items.length
          ? items.map((it, i) => `<button type="button" class="folder-row${it.hidden ? ' hidden-folder' : ''}" data-folder="${h(it.path)}" data-idx="${i}" role="option">
              <svg viewBox="0 0 24 24"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z"/></svg>
              <span>${h(it.name)}</span>
              <svg class="folder-open" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg></button>`).join('')
          : `<div class="folder-empty">${cur.items.length ? `没有匹配「${h(filterEl.value)}」的文件夹` : '这里没有子文件夹——当前目录就是工作区'}</div>`;
        $$('[data-folder]', listEl).forEach((el) => el.addEventListener('click', () => load(el.dataset.folder)));
        selIndex = -1;
        noteEl.textContent = `${cur.items.length} 个文件夹 · ${cur.files} 个文件 · 当前目录将作为工作区`;
      };

      async function load(path, opts = {}) {
        const { fallbackHome = false } = opts;
        listEl.innerHTML = '<div class="folder-empty">正在读取…</div>';
        goBtn.disabled = true;
        cur.path = '';
        try {
          const data = await api(`/workspaces/browse?path=${encodeURIComponent(path || '')}`);
          cur.path = data.path || '';
          cur.parent = data.parent || null;
          cur.items = data.items || [];
          cur.files = Number(data.file_count || 0);
          upBtn.disabled = !cur.parent;
          pathEl.value = cur.path;
          try { localStorage.setItem('atlasx.addws.path', cur.path); } catch { /* ignore */ }
          if (!nameTouched) nameEl.value = data.name || '';
          paintRows();
          paintRecent();
          saveNote.textContent = '';
        } catch (err) {
          if (fallbackHome && path) {
            // 记住的旧路径已不可用：回到主目录，别让弹框一打开就是错误态。
            try { localStorage.removeItem('atlasx.addws.path'); } catch { /* ignore */ }
            pathEl.value = '';
            await load('');
            return;
          }
          noteEl.textContent = err.message;
          listEl.innerHTML = '<div class="folder-empty">无法读取这个文件夹</div>';
        } finally {
          goBtn.disabled = false;
        }
      }

      async function useFolder() {
        if (!cur.path) return;
        const btn = $('#pickUse');
        btn.disabled = true;
        saveNote.className = 'save-note';
        saveNote.textContent = '正在添加…';
        try {
          const ws = await api('/workspaces', {
            method: 'POST',
            body: JSON.stringify({ name: nameEl.value.trim(), path: cur.path }),
          });
          try {
            localStorage.setItem('atlasx.addws.path', cur.path);
            rememberRecent(cur.path);
          } catch { /* ignore */ }
          close(ws);
        } catch (err) {
          saveNote.className = 'save-note bad';
          saveNote.textContent = err.message;
          btn.disabled = false;
        }
      }

      const onKey = (e) => {
        if (e.key === 'Escape') { close(null); return; }
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); useFolder(); return; }
        const active = document.activeElement || {};
        if (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA') return;
        const items = visibleItems();
        if (!items.length) return;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          selIndex = (selIndex + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
          $$('[data-folder]', listEl).forEach((el, i) => el.classList.toggle('sel', i === selIndex));
          const row = $$('[data-folder]', listEl)[selIndex];
          if (row) row.focus();
        } else if (e.key === 'Enter') {
          e.preventDefault();
          load(items[Math.max(0, selIndex)].path);
        }
      };
      const close = (val) => { back.hidden = true; document.removeEventListener('keydown', onKey); resolve(val); };
      document.addEventListener('keydown', onKey);
      $('#modalClose').onclick = () => close(null);
      $('#pickCancel').onclick = () => close(null);

      upBtn.addEventListener('click', () => { if (cur.parent) load(cur.parent); });
      goBtn.addEventListener('click', () => load(pathEl.value.trim()));
      pathEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); load(pathEl.value.trim()); }
      });
      $('[data-pick-home]', bodyEl).addEventListener('click', () => load(''));
      filterEl.addEventListener('input', paintRows);
      nativeBtn.addEventListener('click', async () => {
        try {
          const path = await nativeApi.pick_folder();
          if (path) load(path);
        } catch (err) { toast(String((err && err.message) || err), 'bad'); }
      });
      $('#pickUse').addEventListener('click', useFolder);

      let start = '';
      try { start = localStorage.getItem('atlasx.addws.path') || ''; } catch { /* ignore */ }
      back.hidden = false;
      paintRecent();
      load(start, { fallbackHome: true });
      setTimeout(() => nameEl.focus(), 40);
    });
  }

  async function addWorkspace() {
    const ws = await pickFolderModal();
    if (!ws) return;
    await loadAll();
    S.selectedWs = ws.id; S.wsPath = '.'; S.wsFiles = null; S.wsPreview = null;
    setSection('ws');
    toast(ws.restored ? '工作区已恢复' : '工作区已添加', 'ok');
  }

  async function renameWorkspace(ws) {
    if (!ws) return;
    const saved = await modal({
      title: '重命名工作区',
      fields: `<div class="field"><label>名称</label><input class="input" id="wsRename" value="${h(ws.name || '')}" maxlength="120" /></div>
        <div class="hintline">只改显示名；目录路径保持不变。</div>`,
      read: () => ({ name: $('#wsRename').value.trim() }),
    });
    if (!saved) return;
    if (!saved.name) { toast('名称不能为空', 'bad'); return; }
    try {
      await api(`/workspaces/${encodeURIComponent(ws.id)}`, {
        method: 'PATCH', body: JSON.stringify({ name: saved.name }),
      });
      await loadAll();
      toast('已重命名', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deleteWorkspace(ws) {
    if (!ws) return;
    if (!confirm(`删除工作区「${ws.name}」？目录、会话、文件都会保留，只是从列表隐藏。`)) return;
    try {
      await api(`/workspaces/${encodeURIComponent(ws.id)}`, { method: 'DELETE' });
      if (S.selectedWs === ws.id) { S.selectedWs = null; S.wsFiles = null; S.wsPreview = null; }
      await loadAll();
      toast('工作区已归档', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function revealWorkspace(ws) {
    try {
      await api('/workspace/reveal', {
        method: 'POST', body: JSON.stringify({ workspace_id: ws.id, path: '.' }),
      });
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function archiveWorkspace(ws) {
    try {
      await api(`/workspaces/${encodeURIComponent(ws.id)}`, {
        method: 'PATCH', body: JSON.stringify({ archived: !ws.archived }),
      });
      await loadAll(); S.settings = null; toast('已更新', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  function convMenu(e, conv) {
    openCtx(e, [
      { label: '打开会话', action: () => openConversation(conv.id) },
      { label: '标记已读', action: async () => {
        await api(`/conversations/${encodeURIComponent(conv.id)}/read`, { method: 'POST' });
        await loadAll();
      } },
      { label: '重命名…', action: () => renameConversation(conv) },
      { label: '绑定工作区…', action: async () => {
        const win = S.windows.get(conv.id) || await (async () => { await openConversation(conv.id); return S.windows.get(conv.id); })();
        if (win) bindWorkspace(win);
      } },
      { sep: true },
      { label: '复制会话 ID', action: () => copyText(conv.id) },
      { label: '删除会话', danger: true, action: () => deleteConversation(conv) },
    ]);
  }

  function roleMenu(e, role) {
    openCtx(e, [
      { label: '单聊', hint: '默认', action: () => openRoleChat(role.id) },
      { label: '新建单聊会话', action: () => openRoleChat(role.id, true) },
      { label: '编辑角色…', action: () => openRoleEditor(role) },
      { label: '查看详情', action: () => openRoleDetail(role.id) },
      { sep: true },
      { label: '复制角色', action: () => duplicateRole(role) },
      ...(role.scope === 'temporary' ? [{ label: '存为持久角色', action: async () => {
        await api(`/roles/${encodeURIComponent(role.id)}/promote`, { method: 'POST' });
        await loadAll(); toast('已存为持久角色', 'ok');
      } }] : []),
      { label: '加入群组…', action: () => addRoleToGroup(role) },
      { sep: true },
      { label: '复制角色 ID', action: () => copyText(role.id) },
      { label: '删除角色', danger: true, action: async () => {
        if (!confirm(`删除角色「${role.name}」？`)) return;
        try { await api(`/roles/${encodeURIComponent(role.id)}`, { method: 'DELETE' }); await loadAll(); toast('角色已删除', 'ok'); }
        catch (err) { toast(err.message, 'bad'); }
      } },
    ]);
  }

  function groupMenu(e, team) {
    openCtx(e, [
      { label: '打开群聊', action: () => openGroupChat(team.id) },
      { label: '编辑群组…', action: () => openGroupDetail(team.id) },
      { sep: true },
      { label: '复制群组', action: () => duplicateTeam(team) },
      { label: '复制群组 ID', action: () => copyText(team.id) },
      { label: '删除群组', danger: true, action: () => deleteTeam(team) },
    ]);
  }

  function workspaceMenu(e, ws) {
    openCtx(e, [
      { label: '在画布里打开', action: () => {
        S.selectedWs = ws.id; S.wsPath = '.'; S.wsFiles = null; S.wsPreview = null; setSection('ws');
      } },
      { label: '绑定到当前窗口', action: async () => {
        const win = S.activeWin;
        if (!win) { toast('先打开一个会话窗口', 'bad'); return; }
        await bindWorkspaceTo(win, ws.id);
      } },
      { label: '在 Finder 中打开', action: () => revealWorkspace(ws) },
      { label: '复制路径', action: () => copyText(ws.path || '') },
      { sep: true },
      { label: '重命名…', action: () => renameWorkspace(ws) },
      { label: ws.archived ? '恢复工作区' : '归档工作区', danger: !ws.archived, action: () => archiveWorkspace(ws) },
      { label: '删除工作区', danger: true, action: () => deleteWorkspace(ws) },
    ]);
  }

  function conversationMenuItems(w) {
    const isGlobal = String(w.conv.zone || '') === GLOBAL_AGENT_ZONE;
    return [
      { label: '会话信息', action: () => openConversationInfo(w) },
      { label: '重命名会话…', action: () => renameConversation(w.conv) },
      { label: '绑定工作区…', action: () => bindWorkspace(w) },
      { label: '标记为已读', action: async () => {
        await api(`/conversations/${encodeURIComponent(w.convId)}/read`, { method: 'POST' });
        await loadAll();
      } },
      ...(isGlobal ? [
        { label: '开启系统通知…', action: () => enableGaNotify() },
        { label: S.gaAuto ? '关闭低风险代处理' : '开启低风险代处理', action: () => {
          S.gaAuto = !S.gaAuto;
          try { localStorage.setItem('atlasx.ga.auto', S.gaAuto ? '1' : '0'); } catch { /* ignore */ }
          toast(S.gaAuto ? '全局 Agent 将代处理低风险授权' : '已关闭低风险代处理', 'ok', 1600);
          if (S.gaAuto) loadAttention();
        } },
      ] : []),
      { sep: true },
      { label: '复制会话 ID', action: () => copyText(w.convId) },
      ...(isGlobal ? [] : [{ label: '在会话列表中定位', action: () => openConversation(w.convId) }]),
      { sep: true },
      { label: '删除会话', danger: true, action: () => deleteConversation(w.conv) },
      { label: '关闭会话', danger: true, action: () => closeWin(w) },
    ];
  }

  function windowMenu(e, w) {
    openCtx(e, conversationMenuItems(w));
  }

  async function openConversationInfo(w) {
    if (!w) return;
    const conv = w.conv;
    const parts = convParts(conv);
    const title = convTitle(conv);
    const ws = S.workspaces.find((x) => x.id === conv.bound_workspace_id);
    const model = conv.model_code || S.activeModel || '跟随全局';
    const msgCount = Number(conv.message_count || 0);
    const role = parts.role;
    const subtitle = parts.isGroup
      ? `群聊 · ${parts.members.length} 个角色 · ${modeLabel(parts.team && parts.team.mode)}`
      : `单聊 · ${role && role.name ? `角色：${role.name}` : '默认角色'}`;
    const memberHtml = parts.isGroup
      ? `<div class="field"><span>群成员</span><div class="chips">${parts.members.map((m) => `<span class="chip">${h(m.name)}</span>`).join('') || '<span class="chip">暂无成员</span>'}</div></div>`
      : `<div class="field"><span>角色</span><div class="role-line">${h((role && (role.expertise || role.persona)) || '默认角色')}</div></div>`;
    const action = await modal({
      title: '会话信息',
      fields: `<div class="about-modal">
        <div class="about-brand">${av(title || '会', parts.isGroup ? '' : 'round')}
          <div><b>${h(title)}</b><div class="role-line">${h(subtitle)}</div></div></div>
        <div class="settings-fields">
          <div class="field"><span>工作区</span><div class="role-line">${h(ws ? ws.name : '未绑定（纯聊天）')}</div></div>
          <div class="field"><span>模型</span><div class="role-line">${h(model)}</div></div>
          <div class="field"><span>消息</span><div class="role-line">${msgCount} 条</div></div>
          <div class="field"><span>运行状态</span><div class="role-line">${w.streamingNow || w.es ? '运行中' : '空闲'}</div></div>
          <div class="field"><span>创建时间</span><div class="role-line">${h(conv.created_at || '')}</div></div>
          <div class="field"><span>最近活动</span><div class="role-line">${h(conv.updated_at || '')}</div></div>
        </div>
        ${memberHtml}
        <div class="field"><span>会话 ID</span><div class="role-line mono">${h(w.convId)}</div></div>
      </div>`,
      buttons: [
        { label: parts.isGroup ? '群组资料' : '角色详情', value: 'detail' },
        { label: '重命名', value: 'rename' },
        { label: '关闭', primary: true, value: 'close' },
      ],
      read: (v) => ({ action: v }),
    });
    if (!action) return;
    if (action.action === 'detail') {
      if (parts.isGroup && parts.team) return openGroupDetail(parts.team.id);
      if (role) return openRoleDetail(role.id);
      return null;
    }
    if (action.action === 'rename') return renameConversation(conv);
    if (action.action === 'close') return closeWin(w);
    return null;
  }

  /* ── 设置 ─────────────────────────────────────────────── */
  async function ensureSettings(force = false) {
    if (S.settings && !force) return S.settings;
    const [llm, info, presets] = await Promise.all([
      api('/app/settings'),
      api('/app/info').catch(() => ({})),
      api('/app/models/presets').catch(() => ({ items: [] })),
    ]);
    S.settings = { llm, info, presets: (presets.items || []) };
    const copy = (x) => JSON.parse(JSON.stringify(x || {}));
    S.draft = {
      models: (llm.models || []).map(copy),
      subs: (llm.subscriptions || []).map(copy),
      active: llm.active_model || '',
    };
    return S.settings;
  }

  function apiKeyField(id, hasKey) {
    return `<input class="input" id="${id}" type="password" autocomplete="new-password"
      placeholder="${hasKey ? '已保存（留空不改）' : '未设置'}" />`;
  }

  function subCardHtml(s0) {
    return `<article class="settings-card model-card" data-sub-card="${h(s0.id)}">
      <div class="role-top"><b>${h(s0.name || s0.id)}</b><span class="tag">${h(s0.id)}</span>
        ${s0.has_api_key ? '<span class="tag ok">已配置 Key</span>' : '<span class="tag tmp">未配置</span>'}
        <button class="btn small ghost" data-discover-sub="${h(s0.id)}" title="用这个订阅拉取模型">拉取模型</button>
        <button class="wbtn" data-del-sub="${h(s0.id)}" title="删除订阅" style="margin-left:auto">×</button></div>
      <div class="settings-fields">
        <label class="field"><span>订阅 ID</span><input class="input" data-sf="id" data-id="${h(s0.id)}" value="${h(s0.id)}" /></label>
        <label class="field"><span>名称</span><input class="input" data-sf="name" data-id="${h(s0.id)}" value="${h(s0.name || '')}" /></label>
        <label class="field"><span>Base URL</span><input class="input" data-sf="base_url" data-id="${h(s0.id)}" value="${h(s0.base_url || '')}" placeholder="${h(s0.effective_base_url || '')}" /></label>
        <label class="field"><span>API Key</span><input class="input" type="password" data-sf="api_key" data-id="${h(s0.id)}" placeholder="${s0.has_api_key ? '已保存（留空不改）' : '未设置'}" /></label>
        <label class="field"><span>协议</span><input class="input" data-sf="protocol" data-id="${h(s0.id)}" value="${h(s0.protocol || '')}" placeholder="openai-chat" /></label>
        <label class="field"><span>Models URL（可选）</span><input class="input" data-sf="models_url" data-id="${h(s0.id)}" value="${h(s0.models_url || '')}" /></label>
      </div>
    </article>`;
  }

  function modelCardHtml(m, active) {
    const subs = (S.draft && S.draft.subs) || [];
    return `<article class="settings-card model-card" data-model-card="${h(m.code)}">
      <div class="role-top">
        <label class="model-active"><input type="radio" name="activeModel" value="${h(m.code)}"${m.code === active ? ' checked' : ''} /> 当前</label>
        <b>${h(m.name || m.code)}</b><span class="tag">${h(m.code)}</span>
        ${m.effective_has_api_key || m.has_api_key ? '<span class="tag ok">Key</span>' : '<span class="tag tmp">无 Key</span>'}
        ${m.code !== 'default' ? `<button class="wbtn" data-del-model="${h(m.code)}" title="删除模型" style="margin-left:auto">×</button>` : ''}
      </div>
      <div class="role-line">实际使用：${h(m.effective_model || '未配置')}${m.effective_base_url ? ` · ${h(m.effective_base_url)}` : ''}${m.subscription_name ? ` · 订阅 ${h(m.subscription_name)}` : ''}</div>
      <div class="settings-fields">
        <label class="field"><span>名称</span><input class="input" data-mf="name" data-code="${h(m.code)}" value="${h(m.name || '')}" /></label>
        <label class="field"><span>模型 ID</span><input class="input" data-mf="model" data-code="${h(m.code)}" value="${h(m.model || '')}" placeholder="${h(m.effective_model || '')}" /></label>
        <label class="field"><span>Base URL</span><input class="input" data-mf="base_url" data-code="${h(m.code)}" value="${h(m.base_url || '')}" placeholder="${h(m.effective_base_url || '')}" /></label>
        <label class="field"><span>API Key</span><input class="input" type="password" data-mf="api_key" data-code="${h(m.code)}" placeholder="${(m.effective_has_api_key || m.has_api_key) ? '已保存（留空不改）' : '未设置'}" /></label>
        <label class="field"><span>上下文窗口</span><input class="input" type="number" data-mf="context_window" data-code="${h(m.code)}" value="${Number(m.context_window || 0) || ''}" /></label>
        <label class="field"><span>最大输出</span><input class="input" type="number" data-mf="max_tokens" data-code="${h(m.code)}" value="${Number(m.max_tokens || 0) || ''}" /></label>
        <label class="field"><span>协议</span><input class="input" data-mf="protocol" data-code="${h(m.code)}" value="${h(m.protocol || '')}" placeholder="openai-chat" /></label>
        <label class="field"><span>订阅</span><select class="input" data-mf="subscription_id" data-code="${h(m.code)}">
          <option value="">不挂订阅</option>${subs.map((s0) => `<option value="${h(s0.id)}"${s0.id === m.subscription_id ? ' selected' : ''}>${h(s0.name || s0.id)}</option>`).join('')}</select></label>
        <label class="field"><span>思考档位（逗号分隔）</span><input class="input" data-mf="thinking_levels" data-code="${h(m.code)}" value="${h((m.thinking_levels || []).join(', '))}" /></label>
        <label class="field checkbox-field"><span>视觉</span><input type="checkbox" data-mf="vision" data-code="${h(m.code)}"${m.vision ? ' checked' : ''} /></label>
      </div>
    </article>`;
  }

  function readModelDraft(body) {
    const draft = S.draft;
    draft.models = draft.models.map((m) => {
      const val = (f) => { const el = body.querySelector(`[data-mf="${f}"][data-code="${m.code}"]`); return el ? el.value.trim() : ''; };
      const chk = body.querySelector(`[data-mf="vision"][data-code="${m.code}"]`);
      return {
        ...m,
        name: val('name'), model: val('model'), base_url: val('base_url'),
        context_window: Number(val('context_window')) || 0,
        max_tokens: Number(val('max_tokens')) || 0,
        protocol: val('protocol') || undefined,
        subscription_id: val('subscription_id') || '',
        thinking_levels: val('thinking_levels').split(',').map((x) => x.trim()).filter(Boolean),
        vision: !!(chk && chk.checked),
        api_key: body.querySelector(`[data-mf="api_key"][data-code="${m.code}"]`)?.value || '',
      };
    });
    draft.subs = draft.subs.map((s0) => {
      const val = (f) => { const el = body.querySelector(`[data-sf="${f}"][data-id="${s0.id}"]`); return el ? el.value.trim() : ''; };
      return { ...s0, name: val('name'), base_url: val('base_url'), protocol: val('protocol') || undefined,
        models_url: val('models_url') || undefined,
        api_key: body.querySelector(`[data-sf="api_key"][data-id="${s0.id}"]`)?.value || '' };
    });
    const active = body.querySelector('input[name="activeModel"]:checked');
    draft.active = active ? active.value : draft.active;
    return draft;
  }

  async function saveModelDraft(body) {
    const draft = readModelDraft(body);
    const def = draft.models.find((m) => m.code === 'default');
    const payload = { active_model: draft.active || '' };
    if (def) {
      if (def.model) payload.model = def.model;
      if (def.base_url) payload.base_url = def.base_url;
      if (def.api_key) payload.api_key = def.api_key;
      if (def.context_window) payload.context_window = def.context_window;
      if (def.max_tokens) payload.max_tokens = def.max_tokens;
      payload.protocol = def.protocol || 'openai-chat';
      payload.vision = !!def.vision;
      payload.thinking_levels = def.thinking_levels || [];
    }
    payload.models = draft.models.filter((m) => m.code !== 'default').map((m) => ({
      code: m.code, name: m.name, model: m.model, base_url: m.base_url,
      api_key: m.api_key || '••••••••',
      context_window: m.context_window || undefined,
      max_tokens: m.max_tokens || undefined,
      protocol: m.protocol || undefined,
      vision: !!m.vision,
      thinking_levels: m.thinking_levels || [],
      subscription_id: m.subscription_id || undefined,
    }));
    payload.subscriptions = draft.subs.map((s0) => ({
      id: s0.id, name: s0.name, base_url: s0.base_url,
      api_key: s0.api_key || '••••••••',
      protocol: s0.protocol || undefined,
      models_url: s0.models_url || undefined,
    }));
    try {
      await api('/app/settings', { method: 'PUT', body: JSON.stringify(payload) });
      S.settings = null; S.draft = null;
      await loadAll(); renderCanvas(); toast('模型与订阅已保存', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function discoverInto(body, sub) {
    try {
      const out = await api('/app/models/discover', {
        method: 'POST',
        body: JSON.stringify({ base_url: sub.base_url || '', api_key: sub.api_key || '••••••••', code: sub.id || 'default' }),
      });
      const draft = S.draft;
      const exists = new Set(draft.models.map((m) => m.model));
      (out.models || []).forEach((m) => {
        const id = String(m.id || '');
        if (!id || exists.has(id)) return;
        draft.models.push({
          code: 'model-' + id.replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 48),
          name: id, model: id, base_url: '', subscription_id: sub.id || '',
        });
      });
      renderCanvas();
      toast(`发现 ${out.count || 0} 个模型`, 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  function bindSettingsDraft(body, draft) {
    const save = $('[data-save-models]', body);
    if (save) save.addEventListener('click', () => saveModelDraft(body));
    const addSub = $('[data-add-sub]', body);
    if (addSub) addSub.addEventListener('click', () => {
      draft.subs.push({ id: 'sub-' + Date.now().toString(36), name: '新订阅', base_url: '', api_key: '' });
      renderCanvas();
    });
    const addPlatform = $('[data-add-platform]', body);
    if (addPlatform) addPlatform.addEventListener('click', async () => {
      const presets = (S.settings && S.settings.presets) || [];
      const saved = await modal({
        title: '添加平台',
        fields: `<div class="field"><label>平台</label><select class="input" id="presetSel">
          ${presets.map((p) => `<option value="${h(p.code)}">${h(p.name)}${p.group === 'opencode' ? '（OpenCode）' : ''}</option>`).join('')}
          </select></div><div class="hintline">选中后会自动填 Base URL / Models URL，接着填 API Key 即可拉取模型。OpenCode Zen / Go 也在这里。</div>`,
        read: () => ({ code: $('#presetSel').value }),
      });
      if (!saved) return;
      const preset = presets.find((p) => p.code === saved.code);
      if (!preset) return;
      const stamp = Date.now().toString(36);
      draft.subs.push({
        id: 'sub-' + (preset.code || stamp) + '-' + stamp,
        name: preset.name || preset.code,
        base_url: preset.base_url || '',
        protocol: preset.protocol || 'openai-chat',
        provider: preset.provider || '',
        models_url: preset.models_url || '',
        anthropic_base_url: preset.anthropic_base_url || '',
        api_key: '',
      });
      renderCanvas();
      toast(`已添加平台「${preset.name}」，填 API Key 后拉取模型`, 'ok', 2600);
    });
    $$('[data-discover-sub]', body).forEach((btn) => btn.addEventListener('click', () => {
      readModelDraft(body);
      const sub = draft.subs.find((x) => x.id === btn.dataset.discoverSub);
      if (sub) discoverInto(body, sub);
    }));
    const addModel = $('[data-add-model]', body);
    if (addModel) addModel.addEventListener('click', () => {
      draft.models.push({ code: 'model-' + Date.now().toString(36), name: '新模型', model: '', base_url: '' });
      renderCanvas();
    });
    const discover = $('[data-discover]', body);
    if (discover) discover.addEventListener('click', () => {
      readModelDraft(body);
      const sub = draft.subs[0];
      if (sub) discoverInto(body, sub);
      else toast('先添加一个订阅或平台', 'bad');
    });
    $$('[data-del-sub]', body).forEach((b) => b.addEventListener('click', () => {
      readModelDraft(body);
      draft.subs = draft.subs.filter((x) => x.id !== b.dataset.delSub);
      renderCanvas();
    }));
    $$('[data-del-model]', body).forEach((b) => b.addEventListener('click', () => {
      readModelDraft(body);
      draft.models = draft.models.filter((x) => x.code !== b.dataset.delModel);
      renderCanvas();
    }));
  }

  async function renderSettings() {
    let data;
    try { data = await ensureSettings(); } catch (err) {
      const body = $('#canvasBody');
      if (body) body.innerHTML = `<div class="empty">${h(err.message)}</div>`;
      return;
    }
    if (S.section !== 'settings') return;
    const body = $('#canvasBody');
    if (!body) return;
    const llm = data.llm || {};
    const models = llm.models || [];
    const subs = llm.subscriptions || [];
    if (S.settingsTab === 'models') {
      // 模型 / 订阅完全沿用 V2 原版设置：内嵌 /classic?settings=1&embed=1，
      // 不在这里重做一套「看起来差不多」的 UI。
      body.innerHTML = `<iframe class="settings-frame" title="模型与订阅设置"
        src="/classic?settings=1&embed=1&tab=models"></iframe>`;
      return;
    } else {
      const info = data.info || {};
      body.innerHTML = `<div class="hero"><div><h1>关于 AtlasX</h1>
        <p>版本 ${h(info.version || '')} · 本地单用户 · ${h(info.mode || 'local')}</p>
        <p>模型 / 订阅 / MCP / 技能 / 用量 / 外观等设置由 V2 原版设置页提供，已内嵌在「模型与订阅」。</p></div></div>
        <div class="cards"><article class="card"><h3>数据</h3><p>数据库：${h(info.db_path || '')}</p><p>配置：${h(info.config_path || '')}</p></article>
          <article class="card"><h3>界面</h3><p>旧界面已移除；设置页是唯一保留的 V2 页面，用于模型与扩展配置。</p></article></div>`;
    }
  }

  /* ── 会话 / 嵌入标签 ────────────────────────────────────── */
  async function newConversation() {
    const roles = S.roles.filter((r) => r.scope !== 'temporary');
    const groups = S.teams.filter((t) => (t.members || []).length);
    const choice = await modal({
      title: '新建会话',
      fields: `
        <div class="field"><label>单聊 · 选一个角色</label>
          <select class="input" id="ncRole"><option value="">默认角色（AtlasX）</option>
          ${roles.map((r) => `<option value="${h(r.id)}">${h(r.name)}${r.expertise ? ` · ${h(r.expertise)}` : ''}</option>`).join('')}</select></div>
        <div class="field"><label>或群聊（选择后优先于单聊）</label>
          <select class="input" id="ncGroup"><option value="">不开群聊</option>
          ${groups.map((t) => `<option value="${h(t.id)}">${h(t.name)} · ${(t.members || []).length} 个角色</option>`).join('')}</select></div>
        <div class="field"><label>绑定工作区（可选）</label>
          <select class="input" id="ncWs"><option value="">纯聊天（不绑定）</option>
          ${S.workspaces.map((w) => `<option value="${h(w.id)}">${h(w.name)}</option>`).join('')}</select></div>
        <div class="hintline">单聊就是一个角色和你聊天；需要多角色协作时再开群聊。工作区只提供文件上下文，随时可解绑。</div>`,
      read: () => ({
        role_id: $('#ncRole').value || undefined,
        team_id: $('#ncGroup').value || undefined,
        bound_workspace_id: $('#ncWs').value || undefined,
      }),
    });
    if (!choice) return;
    try {
      const conv = await api('/conversations', { method: 'POST', body: JSON.stringify(choice) });
      await loadAll();
      openConversation(conv.id);
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function openConversation(convId) {
    const existing = S.windows.get(convId);
    if (existing) {
      S.section = 'conv';
      $$('.rail-btn').forEach((b) => b.classList.toggle('active', b.dataset.section === 'conv'));
      activateView(existing);
      showChat(true);
      renderCanvas();
      return;
    }
    try {
      const conv = await api(`/conversations/${encodeURIComponent(convId)}`);
      createWindow(conv);
      setSection('conv');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function openGlobalAgent() {
    closeGaPanel();
    if (S.globalWin) {
      const ta = $('.composer textarea', S.globalWin.el);
      if (ta) ta.focus();
      return;
    }
    try {
      let conv = S.convs.find((c) => String(c.zone || '') === GLOBAL_AGENT_ZONE);
      if (!conv) {
        conv = await api('/conversations', {
          method: 'POST', body: JSON.stringify({ zone: GLOBAL_AGENT_ZONE }),
        });
        await loadAll();
      }
      const full = await api(`/conversations/${encodeURIComponent(conv.id)}`);
      const embedded = S.windows.get(full.id);
      if (embedded) closeWin(embedded);
      const win = createWindow(full, { floating: true });
      setTimeout(() => {
        const ta = $('.composer textarea', win.el);
        if (ta) ta.focus();
      }, 80);
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function openGroupChat(teamId) {
    try {
      const team = S.teams.find((t) => t.id === teamId);
      if (!team || !(team.members || []).length) {
        toast('这个群组还没有成员，先在「群组」里加角色', 'bad');
        return;
      }
      const existing = S.convs.find((c) => c.team_id === teamId && convIsGroup(c));
      if (existing) return openConversation(existing.id);
      const conv = await api('/conversations', { method: 'POST', body: JSON.stringify({ team_id: teamId }) });
      await loadAll();
      openConversation(conv.id);
    } catch (err) { toast(err.message, 'bad'); }
  }

  function createWindow(conv, opts = {}) {
    const floating = !!opts.floating;
    const el = document.createElement('section');
    el.className = floating ? 'chat-view ga-win' : 'chat-view';
    el.dataset.conv = conv.id;
    const title = convTitle(conv);
    const meta = convMeta(conv);
    const parts = convParts(conv);
    const isGroup = parts.isGroup;
    const isGlobal = String(conv.zone || '') === GLOBAL_AGENT_ZONE;
    let perm = 'ask';
    try { perm = localStorage.getItem('atlasx.perm.' + conv.id) || 'ask'; } catch { /* ignore */ }
    el.innerHTML = `
      <header class="win-head chat-head">
        ${isGlobal ? gaAvatar(isGroup ? '' : 'round') : av(title || '会', isGroup ? '' : 'round')}
        <span class="win-title"><b>${h(title)}</b><span>${h(meta.kind)} · ${h(meta.sub)}</span></span>
        <button class="chip" data-bind-ws title="绑定工作区">${h(boundLabel(conv))}</button>
        <div class="head-menus">
          <span class="run-state" data-run-state hidden><span class="pulse"></span><span>运行中</span></span>
          ${isGlobal ? `<button class="head-btn" data-ga-notify title="系统通知" aria-label="系统通知">
            <svg viewBox="0 0 24 24"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>
          </button>` : ''}
          <button class="head-btn" data-conv-info title="会话信息" aria-label="会话信息">
            <svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="2.6"/><path d="M3.6 19c0-2.6 2.4-4.4 5.4-4.4s5.4 1.8 5.4 4.4"/><circle cx="17" cy="9" r="2.2"/><path d="M15.8 14.8c2.4.3 4.2 2 4.2 4.2"/></svg>
          </button>
          <button class="head-btn" data-conv-more title="更多操作" aria-label="更多操作">
            <svg viewBox="0 0 24 24" class="dots"><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg>
          </button>
          <button class="head-btn danger" data-conv-delete title="删除会话" aria-label="删除会话">
            <svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13M10 11v6M14 11v6"/></svg>
          </button>
          <button class="head-btn" data-close title="关闭会话" aria-label="关闭会话">×</button>
        </div>
      </header>
      <div class="win-body"><div class="msgs"></div></div>
      <button class="scroll-down" data-scroll-bottom title="回到底部" aria-label="回到底部">
        <svg viewBox="0 0 24 24"><path d="M12 5v14M6 13l6 6 6-6"/></svg>
      </button>
      <footer class="composer">
        <div class="queue-bar" hidden></div>
        <div class="attach-bar" hidden></div>
        <div class="slash-pop mention-pop" hidden></div>
        <div class="mention-pop" hidden></div>
        <div class="box"><button class="wbtn attach" title="添加附件"><svg viewBox="0 0 24 24"><path d="m21 11-8.5 8.5a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L16 6"/></svg></button>
          <input type="file" class="file-input" hidden multiple />
          <textarea rows="1" placeholder="${isGroup ? '发消息…  @ 成员 · / 命令 · Enter 发送' : '发消息…  / 命令 · Enter 发送'}" aria-label="消息"></textarea>
          <button class="send" title="发送 (Enter)"><svg viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></svg></button>
          <button class="stop" hidden title="停止"><svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button></div>
        <div class="tools">
          <select class="mini" data-model title="模型">${modelOptions(conv.model_code)}</select>
          <select class="mini" data-think title="思考强度"${thinkingOptions(conv.model_code, conv.thinking_level).hidden ? ' hidden' : ''}>${thinkingOptions(conv.model_code, conv.thinking_level).html}</select>
          <span class="seg" role="group" aria-label="权限">
            <button class="seg-btn${perm === 'auto' ? '' : ' on'}" data-perm="ask" title="危险工具先询问">询问</button>
            <button class="seg-btn${perm === 'auto' ? ' on' : ''}" data-perm="auto" title="危险工具自动执行">自动</button>
          </span>
          ${isGroup ? '<button class="mini-link" data-temp-role title="临时角色（只属于本会话)">＋ 临时角色</button>' : ''}
        </div>
      </footer>`;
    (floating ? document.body : $('#chatHostV3')).appendChild(el);
    const w = {
      convId: conv.id, conv, el, es: null, runId: null, cursor: 0,
      title, kind: meta.kind, isGlobal, floating, live: null,
      members: parts.members, teamId: isGroup ? (conv.team_id || '') : '', perm,
      queue: [], pollTimer: null, streamingNow: false, attachments: [],
      think: conv.thinking_level || '',
    };
    if (floating) {
      if (S.globalWin) { S.globalWin.el.remove(); }
      S.globalWin = w;
      document.body.classList.add('ga-open');
      restoreGlobalWindow(w);
    } else {
      S.windows.set(conv.id, w);
      S.activeWin = w;
    }
    bindWindow(w);
    renderHistory(w, conv.messages || [], true);
    loadPending(w);
    pollQueue(w);
    if (!floating) {
      activateView(w);
      showChat(true);
      renderList();
    }
    return w;
  }

  async function loadPending(w) {
    try {
      const [apps, qs] = await Promise.all([
        api(`/conversations/${encodeURIComponent(w.convId)}/approvals?status=pending`).catch(() => ({ items: [] })),
        api(`/conversations/${encodeURIComponent(w.convId)}/questions`).catch(() => ({ items: [] })),
      ]);
      (apps.items || []).forEach((a) => approvalCard(w, a));
      (qs.items || []).forEach((q) => askCard(w, q));
      if ((apps.items || []).length || (qs.items || []).length) scrollBottom($('.msgs', w.el));
    } catch { /* ignore */ }
  }

  function attachmentsHtml(list) {
    if (!Array.isArray(list) || !list.length) return '';
    return `<div class="msg-attachments">${list.map((a) => {
      const name = h(a.name || '附件');
      const url = h(a.url || '');
      const img = String(a.mime || '').startsWith('image/') && url;
      return img ? `<a class="att-img" href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="${name}" /></a>`
        : `<a class="att-file" href="${url}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24"><path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7Z"/><path d="M14 3v4h4"/></svg><span>${name}</span></a>`;
    }).join('')}</div>`;
  }

  function renderAttachments(w) {
    const bar = $('.attach-bar', w.el);
    if (!bar) return;
    if (!w.attachments.length) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.hidden = false;
    bar.innerHTML = w.attachments.map((a, i) =>
      `<span class="chip att-chip">${h(a.name)} · ${bytes(a.size)}<button class="wbtn" data-attach-remove="${i}" title="移除">×</button></span>`).join('');
  }

  function addAttachments(w, files) {
    const allowed = files.slice(0, 8);
    allowed.forEach((f) => {
      if (f.size > 25 * 1024 * 1024) { toast(`${f.name} 超过 25MB`, 'bad'); return; }
      const reader = new FileReader();
      reader.onload = () => {
        w.attachments.push({
          name: f.name, mime: f.type || '', size: f.size, data: String(reader.result || ''),
        });
        renderAttachments(w);
      };
      reader.readAsDataURL(f);
    });
  }

  function thinkingOptions(modelCode, current) {
    const profile = (S.models || []).find((m) => (m.code || m.id) === (modelCode || S.activeModel));
    const levels = (profile && (profile.thinking_levels || profile.effective_thinking_levels)) || [];
    if (!levels.length) return { hidden: true, html: '<option value="">默认</option>' };
    const code = current || '';
    return {
      hidden: false,
      html: `<option value="">默认思考</option>` + levels.map((l) =>
        `<option value="${h(l)}"${l === code ? ' selected' : ''}>${h(l)}</option>`).join(''),
    };
  }

  function syncThinking(w) {
    const sel = $('[data-think]', w.el);
    if (!sel) return;
    const opts = thinkingOptions(w.conv.model_code, w.think);
    sel.innerHTML = opts.html;
    sel.hidden = opts.hidden;
  }

  function modelOptions(current) {
    const code = current || S.activeModel || '';
    const items = S.models || [];
    if (!items.length) return `<option value="">默认模型</option>`;
    const has = items.some((m) => (m.code || m.id) === code);
    return (has ? '' : `<option value="${h(code || '')}">${h(code || '默认模型')}</option>`)
      + items.map((m) => {
        const c = m.code || m.id || '';
        return `<option value="${h(c)}"${c === code ? ' selected' : ''}>${h(m.name || c)}</option>`;
      }).join('');
  }

  function boundLabel(conv) {
    const ws = S.workspaces.find((x) => x.id === conv.bound_workspace_id);
    return ws ? `工作区：${ws.name}` : '未绑定工作区';
  }

  /* 会话 = 列表行；顶部不再放标签条。后台会话有新内容时刷新列表标记。 */
  let listDirty = false;
  function markListDirty() {
    if (listDirty) return;
    listDirty = true;
    setTimeout(() => { listDirty = false; renderList(); }, 400);
  }

  function switchTo(w) {
    if (!w) return;
    S.section = 'conv';
    $$('.rail-btn').forEach((b) => b.classList.toggle('active', b.dataset.section === 'conv'));
    $('#listTitle').textContent = '会话';
    activateView(w);
    renderCanvas();
    setTimeout(() => {
      const ta = $('.composer textarea', w.el);
      if (ta && !narrow()) ta.focus();
    }, 40);
  }

  function activateView(w) {
    if (!w) return;
    S.activeWin = w;
    w.unread = false;
    S.windows.forEach((x) => { x.el.hidden = x !== w; });
    renderList();
    requestAnimationFrame(() => {
      const body = $('.win-body', w.el);
      if (body && body.dataset.stick !== '0') scrollBottom(body, true);
      const btn = $('[data-scroll-bottom]', w.el);
      if (btn && body) btn.hidden = nearBottom(body);
    });
    api(`/conversations/${encodeURIComponent(w.convId)}/read`, { method: 'POST' }).catch(() => { /* 忽略 */ });
  }

  function showChat(show) {
    const shell = $('#chatShellV3');
    const canvas = document.querySelector('.canvas');
    if (!shell) return;
    shell.hidden = !show;
    if (canvas) canvas.classList.toggle('chat-open', !!show);
    document.body.classList.toggle('chat-open', !!show);
    if (show && S.activeWin) activateView(S.activeWin);
  }

  function menuAt(anchor, items) {
    const r = anchor.getBoundingClientRect();
    openCtx({ preventDefault() {}, stopPropagation() {}, clientX: r.left + 8, clientY: r.bottom + 4 }, items);
  }

  function closeWin(w) {
    if (!w) return;
    if (w.es) w.es.close();
    if (w.floating) {
      w.el.remove();
      if (S.globalWin === w) S.globalWin = null;
      document.body.classList.remove('ga-open');
      return;
    }
    const order = [...S.windows.keys()];
    const idx = order.indexOf(w.convId);
    w.el.remove();
    S.windows.delete(w.convId);
    const rest = [...S.windows.values()];
    S.activeWin = rest.length ? rest[Math.max(0, Math.min(idx, rest.length - 1))] : null;
    if (S.activeWin) { activateView(S.activeWin); showChat(true); }
    else showChat(false);
    renderList();
    renderCanvas();
  }

  function restoreGlobalWindow(w) {
    try {
      const pos = JSON.parse(localStorage.getItem('atlasx.ga.pos.v2') || 'null');
      if (pos && Number.isFinite(pos.left) && Number.isFinite(pos.top)) {
        const left = Math.max(4, Math.min(window.innerWidth - 120, pos.left));
        const top = Math.max(4, Math.min(window.innerHeight - 60, pos.top));
        w.el.style.left = `${left}px`;
        w.el.style.top = `${top}px`;
        w.el.style.right = 'auto';
        w.el.style.bottom = 'auto';
      }
    } catch { /* ignore */ }
  }

  function saveGlobalWindowPos(w) {
    try {
      const r = w.el.getBoundingClientRect();
      localStorage.setItem('atlasx.ga.pos.v2', JSON.stringify({ left: r.left, top: r.top }));
    } catch { /* ignore */ }
  }

  function setupGlobalDrag(w) {
    const head = $('.chat-head', w.el);
    if (!head) return;
    let drag = null;
    head.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button,input,select,textarea,a')) return;
      const r = w.el.getBoundingClientRect();
      drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      w.el.style.right = 'auto';
      w.el.style.bottom = 'auto';
      w.el.style.left = `${r.left}px`;
      w.el.style.top = `${r.top}px`;
      w.el.classList.add('dragging');
      try { head.setPointerCapture(e.pointerId); } catch { /* ignore */ }
    });
    head.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const left = Math.max(4, Math.min(window.innerWidth - 140, e.clientX - drag.dx));
      const top = Math.max(4, Math.min(window.innerHeight - 64, e.clientY - drag.dy));
      w.el.style.left = `${left}px`;
      w.el.style.top = `${top}px`;
    });
    const stop = () => {
      if (!drag) return;
      drag = null;
      w.el.classList.remove('dragging');
      saveGlobalWindowPos(w);
    };
    head.addEventListener('pointerup', stop);
    head.addEventListener('pointercancel', stop);
  }

  function bindWindow(w) {
    const el = w.el;
    const head = $('.win-head', el);
    el.addEventListener('pointerdown', () => { if (!w.floating) activateView(w); }, true);
    head.addEventListener('contextmenu', (e) => windowMenu(e, w));
    if (w.floating) setupGlobalDrag(w);
    const body = $('.win-body', el);
    const syncScrollButton = () => {
      if (body.dataset.freeze === '1') return;
      const btn = $('[data-scroll-bottom]', el);
      if (!btn) return;
      btn.hidden = nearBottom(body);
      body.dataset.stick = nearBottom(body) ? '1' : '0';
    };
    body.addEventListener('scroll', syncScrollButton, { passive: true });
    const jump = $('[data-scroll-bottom]', el);
    if (jump) jump.addEventListener('click', () => scrollBottom(body, true));
    syncScrollButton();
    $('.win-body', el).addEventListener('contextmenu', (e) => {
      const msgEl = e.target.closest('.msg');
      if (!msgEl) return;
      const mid = msgEl.dataset.msgId || '';
      const msg = mid ? (w.msgMap && w.msgMap.get(mid)) : null;
      const text = msg ? (msg.content || '') : ((msgEl.querySelector('.bubble') || {}).textContent || '');
      openCtx(e, [
        { label: '复制文本', action: () => copyText(text) },
        { label: '复制为引用', action: () => copyText(`> ${String(text).split('\n').join('\n> ')}`) },
        { sep: true },
        { label: '复制消息 ID', disabled: !mid, action: () => copyText(mid) },
      ]);
    });
    $('[data-close]', el).addEventListener('click', (e) => { e.stopPropagation(); closeWin(w); });
    $('[data-bind-ws]', el).addEventListener('click', () => bindWorkspace(w));
    const infoBtn = $('[data-conv-info]', el);
    if (infoBtn) infoBtn.addEventListener('click', (e) => { e.stopPropagation(); openConversationInfo(w); });
    const moreBtn = $('[data-conv-more]', el);
    if (moreBtn) moreBtn.addEventListener('click', (e) => { e.stopPropagation(); menuAt(moreBtn, conversationMenuItems(w)); });
    const delBtn = $('[data-conv-delete]', el);
    if (delBtn) delBtn.addEventListener('click', (e) => { e.stopPropagation(); deleteConversation(w.conv); });
    const gaNotifyBtn = $('[data-ga-notify]', el);
    if (gaNotifyBtn) gaNotifyBtn.addEventListener('click', (e) => { e.stopPropagation(); enableGaNotify(); });
    const ta = $('.composer textarea', el);
    ta.addEventListener('keydown', (e) => {
      if (slashKey(w, e)) return;
      if (mentionKey(w, e)) return;
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(w); }
    });
    ta.addEventListener('input', () => { autoGrow(ta); updateMention(w); updateSlash(w); });
    ta.addEventListener('blur', () => setTimeout(() => hideMention(w), 150));
    autoGrow(ta);
    const temp = $('[data-temp-role]', el);
    if (temp) temp.addEventListener('click', () => createTempRole(w));
    const fileInput = $('.file-input', el);
    $('.attach', el).addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => addAttachments(w, [...fileInput.files]));
    $('.attach-bar', el).addEventListener('click', (e) => {
      const rm = e.target.closest('[data-attach-remove]');
      if (!rm) return;
      w.attachments.splice(Number(rm.dataset.attachRemove), 1);
      renderAttachments(w);
    });
    const modelSel = $('[data-model]', el);
    if (modelSel) modelSel.addEventListener('change', async () => {
      try {
        await api(`/conversations/${encodeURIComponent(w.convId)}/preferences`, {
          method: 'PUT', body: JSON.stringify({ model_code: modelSel.value || '' }),
        });
        w.conv.model_code = modelSel.value || '';
        w.think = '';
        syncThinking(w);
        toast('模型已切换', 'ok', 1500);
      } catch (err) { toast(err.message, 'bad'); }
    });
    const thinkSel = $('[data-think]', el);
    if (thinkSel) thinkSel.addEventListener('change', async () => {
      w.think = thinkSel.value || '';
      try {
        await api(`/conversations/${encodeURIComponent(w.convId)}/preferences`, {
          method: 'PUT', body: JSON.stringify({ thinking_level: w.think }),
        });
        toast('思考强度已更新', 'ok', 1500);
      } catch (err) { toast(err.message, 'bad'); }
    });
    $$('.seg [data-perm]', el).forEach((btn) => btn.addEventListener('click', () => {
      w.perm = btn.dataset.perm === 'auto' ? 'auto' : 'ask';
      $$('.seg [data-perm]', el).forEach((b) => b.classList.toggle('on', b === btn));
      try { localStorage.setItem('atlasx.perm.' + w.convId, w.perm); } catch { /* ignore */ }
    }));
    $('.send', el).addEventListener('click', () => send(w));
    $('.stop', el).addEventListener('click', () => stopRun(w));
  }

  async function bindWorkspace(w) {
    const choice = await modal({
      title: '绑定工作区',
      fields: `<div class="field"><label>工作区只提供文件上下文，可随时解绑</label>
        <select class="input" id="bwSel"><option value="">不绑定（纯聊天）</option>
        ${S.workspaces.map((x) => `<option value="${h(x.id)}"${x.id === w.conv.bound_workspace_id ? ' selected' : ''}>${h(x.name)}</option>`).join('')}</select></div>`,
      read: () => ({ workspace_id: $('#bwSel').value || null }),
    });
    if (!choice) return;
    try {
      const out = await api(`/conversations/${encodeURIComponent(w.convId)}/workspace`, {
        method: 'PATCH', body: JSON.stringify(choice),
      });
      w.conv.bound_workspace_id = out.bound_workspace_id || '';
      $('[data-bind-ws]', w.el).textContent = boundLabel(w.conv);
      const wsChip = $('[data-bind-ws]', w.el);
      if (wsChip) wsChip.textContent = boundLabel(w.conv);
      toast('工作区绑定已更新', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── 消息渲染 ─────────────────────────────────────────── */
  function renderHistory(w, messages, force = false) {
    const box = $('.msgs', w.el);
    const scroller = $('.win-body', w.el) || box;
    const stick = force || scroller.dataset.stick !== '0';
    scroller.dataset.freeze = '1';
    box.innerHTML = '';
    if (!messages || !messages.length) {
      const hint = w.kind === '群聊'
        ? '发一条消息开始，或者 @ 成员 / 输入 / 命令'
        : '发一条消息开始，或者输入 / 命令';
      box.innerHTML = `<div class="chat-empty">${w.isGlobal ? gaAvatar('round') : av(w.title || 'A', w.kind === '群聊' ? '' : 'round')}<b>${h(w.title)}</b><span>${h(hint)}</span></div>`;
      scroller.dataset.stick = stick ? '1' : '0';
      scroller.dataset.freeze = '0';
      return;
    }
    w.msgMap = new Map();
    (messages || []).forEach((m) => {
      if (m && m.id) w.msgMap.set(m.id, m);
      box.appendChild(messageNode(m));
    });
    scroller.dataset.stick = stick ? '1' : '0';
    scroller.dataset.freeze = '0';
    if (stick) scrollBottom(scroller, true);
  }

  function messageNode(m) {
    if (m.by === 'system') {
      const el = document.createElement('div');
      el.className = 'sys';
      el.dataset.msgId = m.id || '';
      el.textContent = m.content || '';
      return el;
    }
    if (m.by === 'assistant') return assistantNode(m);
    const el = document.createElement('div');
    el.className = 'msg user';
    el.dataset.msgId = m.id || '';
    el.innerHTML = `${av('我', 'round')}<div class="msg-body"><div class="msg-who">我</div><div class="bubble">${h(m.content || '')}</div>${attachmentsHtml(m.attachments)}</div>`;
    return el;
  }

  /* 助手一轮 = 按真实发生顺序交错的时间线：思考 / 工具 / 中间正文 / 最终答复。
     后端把过程按发生顺序落在 trace；这里不再把所有过程折到最上面，
     每一步都留在它发生的位置，思考块各自可折叠。 */
  function normalizeTrace(trace) {
    const items = [];
    const tools = new Map();
    (trace || []).forEach((raw) => {
      if (!raw || typeof raw !== 'object') return;
      const s = { ...raw };
      if (s.kind === 'tool') {
        // 后端把工具拆成 start / end 两条：按 id 合并成一条，位置留在开始处。
        if (s.id && tools.has(s.id)) {
          Object.assign(tools.get(s.id), s);
          return;
        }
        if (s.status === undefined) {
          s.status = (s.phase === 'end' || s.ok !== undefined || s.duration_ms !== undefined)
            ? 'done' : 'running';
        }
        items.push(s);
        if (s.id) tools.set(s.id, s);
      } else {
        items.push(s);
      }
    });
    const timed = items.every((s) => Number.isFinite(Number(s.ts)));
    if (timed) items.sort((a, b) => Number(a.ts) - Number(b.ts));
    return items;
  }

  function traceEntries(m) {
    const entries = [];
    normalizeTrace(Array.isArray(m.trace) ? m.trace : []).forEach((s) => {
      if (!s || !s.kind) return;
      if (s.kind === 'thinking') {
        const text = String(s.text || '').trim();
        if (text) {
          const last = entries[entries.length - 1];
          if (last && last.kind === 'thinking') last.text += `\n${text}`;
          else entries.push({ kind: 'thinking', text });
        }
      } else if (s.kind === 'tool') {
        entries.push({ kind: 'tool', ...s });
      } else if (s.kind === 'text') {
        const text = String(s.text || '').trim();
        if (text) entries.push({ kind: 'text', text });
      } else if (s.kind === 'retry' || s.kind === 'compress') {
        entries.push({ kind: s.kind, text: String(s.text || '') });
      }
    });
    // 旧数据只有 reasoning、没有 trace 里的 thinking：它发生在答复之前，
    // 留在时间线开头；已有 trace 的顺序不受影响。
    if (m.reasoning && !entries.some((e) => e.kind === 'thinking')) {
      entries.unshift({ kind: 'thinking', text: String(m.reasoning) });
    }
    if (Array.isArray(m.tool_trace) && !entries.some((e) => e.kind === 'tool')) {
      m.tool_trace.forEach((t) => entries.push({ kind: 'tool', ...t }));
    }
    return entries;
  }

  // 把最终答复接回时间线：已经在 trace 末尾的 text 块只打 final 标记，
  // 没有的话追加到末尾。最终答复因此总在它该出现的位置，而不是另起一块。
  function markAssistantFinal(m, entries) {
    const body = String(m.content || '').trim();
    if (body) {
      for (let i = entries.length - 1; i >= 0; i -= 1) {
        const e = entries[i];
        if (e.kind === 'text' && String(e.text || '').trim() === body) {
          e.final = true;
          return;
        }
      }
      entries.push({ kind: 'text', text: body, final: true });
      return;
    }
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      if (entries[i].kind === 'text' && String(entries[i].text || '').trim()) {
        entries[i].final = true;
        return;
      }
    }
  }

  function toolHtml(t) {
    const running = t.status === 'running' || t.status === 'preparing' || t.status === 'background';
    const ok = t.ok !== false;
    const status = running ? '<span class="sp"></span>' : `<span class="${ok ? 'ok' : 'fail'}">${ok ? '' : '!'}</span>`;
    const ms = durationText(t.duration_ms);
    return `<span class="tool-ico">${running ? '' : '<svg viewBox="0 0 24 24"><path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7Z"/><path d="M14 3v4h4"/></svg>'}</span>
      <span class="tool-name">${h(t.name || 'tool')}</span>
      ${ms ? `<span class="tool-ms">${h(ms)}</span>` : ''}
      ${status}`;
  }

  function toolDetailHtml(t) {
    let text = '';
    if (t.display && typeof t.display === 'object' && t.display.diff) {
      text = String(t.display.diff || '');
    } else if (t.args !== undefined && t.args !== null && t.args !== '') {
      try {
        text = typeof t.args === 'string' ? t.args : JSON.stringify(t.args, null, 2);
      } catch { text = String(t.args || ''); }
      if (text === '{}') text = '';
    }
    if (!text) return '';
    if (text.length > 4000) text = `${text.slice(0, 4000)}\n…`;
    return `<details class="tool-args"><summary>${t.display && t.display.diff ? '改动' : '参数'}</summary><pre>${h(text)}</pre></details>`;
  }

  function procStepHtml(step, opts = {}) {
    if (step.kind === 'thinking') {
      return `<details class="think-step"${opts.open ? ' open' : ''}><summary><span class="proc-caret">›</span><b>深度思考</b><span class="think-brief">${h(firstLine(step.text, 120))}</span>${opts.live ? '<span class="live-dots"><i></i><i></i><i></i></span>' : ''}</summary>
        <div class="think-body">${h(step.text || '')}</div></details>`;
    }
    if (step.kind === 'tool') {
      return `<div class="proc-tool"><div class="tool-line">${toolHtml(step)}</div>${toolDetailHtml(step)}</div>`;
    }
    if (step.kind === 'text') {
      if (step.final) {
        return `<div class="bubble assistant-text">${md(step.text || '')}${opts.streaming ? '<span class="streaming-caret"></span>' : ''}</div>`;
      }
      return `<div class="proc-text">${md(step.text || '')}${opts.streaming ? '<span class="streaming-caret"></span>' : ''}</div>`;
    }
    const label = step.kind === 'retry' ? '自动重试' : '上下文压缩';
    return `<div class="proc-note"><b>${label}</b>${step.text ? ` · ${h(firstLine(step.text, 160))}` : ''}</div>`;
  }

  // 历史消息：保持 trace 顺序，不再把所有思考 / 工具折到顶部。
  function assistantFlowHtml(entries) {
    if (!entries.length) return '';
    return `<div class="turn-flow">${entries.map((step) => procStepHtml(step)).join('')}</div>`;
  }

  function assistantNode(m) {
    const el = document.createElement('div');
    el.className = 'msg assistant';
    el.dataset.msgId = m.id || '';
    const entries = traceEntries(m);
    markAssistantFinal(m, entries);
    const name = m.role_name || 'AtlasX';
    const meta = [];
    const ms = durationText(m.duration_ms);
    if (ms) meta.push(ms);
    if (m.interrupted) meta.push('已停止');
    if (m.error) meta.push('失败');
    el.innerHTML = `${av(name, 'round')}
      <div class="msg-body"><div class="msg-who"><b>${h(name)}</b>${meta.length ? `<span class="msg-meta">${h(meta.join(' · '))}</span>` : ''}</div>
      ${assistantFlowHtml(entries)}
      ${m.error ? `<div class="run-error">${h(m.error)}</div>` : ''}</div>`;
    return el;
  }

  function toolLines(m) {
    const out = [];
    const seen = new Map();
    (m.trace || []).forEach((s) => {
      if (s.kind !== 'tool') return;
      if (s.phase === 'start' || (!s.phase && s.args !== undefined)) {
        const item = { id: s.id || s.name, name: s.name || 'tool', status: 'running', ok: true, duration_ms: 0 };
        seen.set(item.id, item); out.push(item);
      }
      const it = seen.get(s.id || s.name);
      if (it && (s.phase === 'end' || s.count !== undefined || s.duration_ms !== undefined)) {
        it.status = s.status || 'done'; it.ok = s.ok !== false;
        it.duration_ms = s.duration_ms || it.duration_ms;
      }
    });
    if (!out.length && Array.isArray(m.tool_trace)) {
      m.tool_trace.forEach((t) => out.push({ id: t.id || t.name, name: t.name, ok: t.ok !== false, status: 'done', duration_ms: t.duration_ms || 0 }));
    }
    return out;
  }

  function toolNode(t) {
    const el = document.createElement('div');
    el.className = 'tool-line';
    el.dataset.toolId = t.id || '';
    paintTool(el, t);
    return el;
  }

  function paintTool(el, t) {
    el.innerHTML = toolHtml(t);
  }

  // 轻量 Markdown：先转义再渲染；支持代码块 / 行内代码 / 标题 / 粗体 / 列表 / 引用。
  function md(text) {
    return String(text || '').split(/```/).map((seg, i) => {
      if (i % 2 === 1) {
        const nl = seg.indexOf('\n');
        return `<pre class="md-pre">${h(nl >= 0 ? seg.slice(nl + 1) : seg)}</pre>`;
      }
      let t = h(seg);
      t = t.replace(/`([^`\n]+)`/g, '<code>$1</code>');
      t = t.replace(/^### (.+)$/gm, '<h4>$1</h4>')
        .replace(/^## (.+)$/gm, '<h3>$1</h3>')
        .replace(/^# (.+)$/gm, '<h2>$1</h2>');
      t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      t = t.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
      t = t.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
      t = t.replace(/(?:<li>.*<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`);
      t = t.replace(/^\d+\. (.+)$/gm, '<li class="ord">$1</li>');
      t = t.replace(/(?:<li class="ord">.*<\/li>\n?)+/g, (m) => `<ol>${m.replace(/ class="ord"/g, '')}</ol>`);
      t = t.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
      return t.replace(/\n/g, '<br>');
    }).join('');
  }

  function nearBottom(box, threshold = 96) {
    if (!box) return true;
    return (box.scrollHeight - box.scrollTop - box.clientHeight) <= threshold;
  }

  function scrollBottom(box, force = false) {
    if (!box) return;
    // 调用方常传 .msgs（内容容器）；真正滚动的是外面的 .win-body。
    const scroller = box.classList && box.classList.contains('msgs')
      ? (box.closest('.win-body') || box)
      : box;
    if (!force && scroller.dataset.stick === '0') return;
    requestAnimationFrame(() => {
      scroller.scrollTop = scroller.scrollHeight;
      scroller.dataset.stick = '1';
    });
  }

  function scrollInner(el) {
    if (!el) return;
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
  }

  function autoGrow(ta) {
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(180, Math.max(24, ta.scrollHeight))}px`;
  }

  /* ── 发送 / 流 ─────────────────────────────────────────── */
  async function send(w) {
    const ta = $('.composer textarea', w.el);
    const text = String(ta.value || '').trim();
    if (!text) return;
    const sendBtn = $('.send', w.el);
    const attachments = w.attachments.map((a) => ({ ...a }));
    sendBtn.disabled = true;
    ta.value = '';
    autoGrow(ta);
    const box = $('.msgs', w.el);
    const userEl = messageNode({ by: 'user', content: text });
    box.appendChild(userEl);
    const live = document.createElement('div');
    live.className = 'msg assistant';
    live.innerHTML = `${av('A', 'round')}<div class="msg-body"><div class="msg-who"><b>AtlasX</b></div>
      <div class="turn-flow live-flow"></div></div>`;
    box.appendChild(live);
    const liveFlow = $('.live-flow', live);
    w.live = {
      el: live, flow: liveFlow, toolHost: liveFlow, tools: new Map(),
      text: '', textEl: null, textRound: null,
      thinkEl: null, thinkText: '', thinkRound: null, lastKind: '',
    };
    scrollBottom(box, true);
    ta.focus();
    try {
      const run = await api(`/conversations/${encodeURIComponent(w.convId)}/messages/run`, {
        method: 'POST',
        body: JSON.stringify({
          content: text, mode: 'autonomous', auto_execute: w.perm === 'auto',
          thinking_level: w.think || '',
          attachments: attachments.length ? attachments.map((a) => ({
            name: a.name, mime: a.mime, size: a.size, data: a.data,
          })) : undefined,
        }),
      });
      // 本窗口没有在跑的 run → 直接跟这条；否则它已进队列，轮询等它开始。
      if (w.streamingNow || w.es) {
        w.queue = (run.queue || []).length ? (run.queue || []) : w.queue;
        renderQueue(w);
        pollQueue(w);
      } else {
        startStream(w, run.id);
      }
    } catch (err) {
      paintError(w, err.message);
      sendBtn.disabled = false;
    }
  }

  async function stopRun(w) {
    if (!w.runId) return;
    try {
      await api(`/runs/${encodeURIComponent(w.runId)}/stop`, { method: 'POST' });
      toast('已请求停止', 'ok', 1500);
      setTimeout(() => pollQueue(w), 600);
    } catch (err) { toast(err.message, 'bad'); }
  }

  function paintError(w, msg) {
    if (!w.live) return;
    const host = w.live.flow || $('.live-flow', w.live.el);
    const el = document.createElement('div');
    el.className = 'tool-line';
    el.innerHTML = `<span class="fail">!</span><span>${h(msg)}</span>`;
    if (host) host.appendChild(el);
    $('.send', w.el).disabled = false;
  }

  function startStream(w, runId) {
    if (!runId) return;
    w.runId = runId;
    w.cursor = 0;
    w.streamingNow = true;
    const row = S.convs.find((c) => c.id === w.convId);
    if (row) row.active = true;
    setRunState(w, true);
    $('.stop', w.el).hidden = false;
    renderList();
    openStream(w);
  }

  function setRunState(w, on) {
    const el = w && w.el ? $('[data-run-state]', w.el) : null;
    if (el) el.hidden = !on;
  }

  function renderQueue(w) {
    const bar = $('.queue-bar', w.el);
    if (!bar) return;
    const items = (w.queue || []).filter((r) => r.id !== w.runId);
    if (!items.length) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.hidden = false;
    bar.innerHTML = `<div class="queue-head">排队中 · ${items.length}</div>` + items.map((r, i) =>
      `<div class="queue-item" data-run="${h(r.id)}">
        <span class="queue-text">${h(r.content || r.title || '')}</span>
        <button class="wbtn" data-qmove="up" data-id="${h(r.id)}" ${i === 0 ? 'disabled' : ''} title="上移">↑</button>
        <button class="wbtn" data-qmove="down" data-id="${h(r.id)}" ${i === items.length - 1 ? 'disabled' : ''} title="下移">↓</button>
        <button class="wbtn" data-qcancel="${h(r.id)}" title="取消">×</button>
      </div>`).join('');
    $$('[data-qmove]', bar).forEach((b) => b.addEventListener('click', async () => {
      try {
        await api(`/conversations/${encodeURIComponent(w.convId)}/queue/${encodeURIComponent(b.dataset.id)}/move`, {
          method: 'POST', body: JSON.stringify({ direction: b.dataset.qmove }),
        });
        pollQueue(w);
      } catch (err) { toast(err.message, 'bad'); }
    }));
    $$('[data-qcancel]', bar).forEach((b) => b.addEventListener('click', async () => {
      try {
        await api(`/conversations/${encodeURIComponent(w.convId)}/queue/${encodeURIComponent(b.dataset.qcancel)}`, { method: 'DELETE' });
        pollQueue(w);
      } catch (err) { toast(err.message, 'bad'); }
    }));
  }

  async function pollQueue(w) {
    if (w.pollTimer) { clearTimeout(w.pollTimer); w.pollTimer = null; }
    try {
      const data = await api(`/conversations/${encodeURIComponent(w.convId)}/queue`);
      w.queue = data.items || [];
      renderQueue(w);
      const running = data.running;
      if (!w.streamingNow && running && running.id) {
        startStream(w, running.id);
      } else if (!running && !w.streamingNow) {
        $('.stop', w.el).hidden = true;
        setRunState(w, false);
      }
      if (w.queue.length || running) {
        w.pollTimer = setTimeout(() => pollQueue(w), 2000);
      }
    } catch { /* ignore */ }
  }

  function openStream(w) {
    if (!window.EventSource) { toast('当前浏览器不支持 EventSource', 'bad'); return; }
    if (w.es) w.es.close();
    const es = new EventSource(`${API}/runs/${encodeURIComponent(w.runId)}/stream?since=${w.cursor}`);
    w.es = es;
    es.addEventListener('run', (event) => {
      let payload = {};
      try { payload = JSON.parse(event.data); } catch { return; }
      (payload.events || []).forEach((evt) => {
        const seq = Number(evt && evt._seq);
        if (seq && seq <= w.cursor) return;
        applyEvent(w, evt);
        if (seq) w.cursor = seq;
      });
      if (payload.last_seq && payload.last_seq > w.cursor) w.cursor = payload.last_seq;
    });
    es.addEventListener('run_end', (event) => {
      let payload = {};
      try { payload = JSON.parse(event.data); } catch { /* ignore */ }
      es.close();
      if (w.es === es) w.es = null;
      $('.send', w.el).disabled = false;
      $('.stop', w.el).hidden = true;
      setRunState(w, false);
      collapseLiveThinking(w);
      w.live = null;
      w.streamingNow = false;
      loadAll();
      loadAttention();
      setTimeout(() => pollQueue(w), 400);
      setTimeout(async () => {
        try {
          const conv = await api(`/conversations/${encodeURIComponent(w.convId)}`);
          w.conv = conv;
          renderHistory(w, conv.messages || [], false);
          loadPending(w);
          const box = $('.msgs', w.el);
          const btn = $('[data-scroll-bottom]', w.el);
          if (btn) btn.hidden = nearBottom(box);
        } catch { /* ignore */ }
      }, 400);
    });
    es.addEventListener('error', () => {
      if (es.readyState === EventSource.CLOSED) { $('.send', w.el).disabled = false; }
    });
  }

  function applyEvent(w, evt) {
    // 后台会话收到新内容：列表行点亮，不切走用户当前正在看的会话。
    if (w !== S.activeWin && (evt.delta !== undefined || evt.thinking !== undefined || evt.tool_call || evt.tool_result || evt.done)) {
      w.unread = true;
      markListDirty();
    }
    if (!w.live) return;
    const host = w.live.flow || $('.live-flow', w.live.el);
    const box = $('.msgs', w.el);
    if (evt.start) {
      const who = $('.msg-who', w.live.el);
      if (evt.role_name && who) who.innerHTML = `<b>${h(evt.role_name)}</b>`;
      $('.avatar', w.live.el).outerHTML = av(evt.role_name || 'A', 'round');
      return;
    }
    if (evt.thinking !== undefined) {
      const piece = String(evt.thinking || '');
      if (piece) liveAppendThinking(w, piece, evt.round);
      return;
    }
    if (evt.delta !== undefined) {
      const piece = String(evt.delta || '');
      if (piece) liveAppendText(w, piece, evt.round);
      return;
    }
    const tc = evt.tool_call;
    if (tc) {
      const id = tc.id || tc.name;
      let line = w.live.tools.get(id);
      if (!line) {
        liveCloseThinking(w);
        liveCloseText(w);
        line = toolNode({ id, name: tc.name, status: tc.status || 'running', ok: true });
        w.live.tools.set(id, line);
        host.appendChild(line);
        w.live.lastKind = 'tool';
      } else {
        paintTool(line, { ...tc, status: tc.status || 'running', ok: true });
      }
      scrollBottom(box);
      return;
    }
    const tr = evt.tool_result;
    if (tr) {
      const id = tr.id || tr.name;
      let line = w.live.tools.get(id);
      if (!line && id) {
        line = toolNode({ id, name: tr.name, status: 'done', ok: tr.ok !== false, duration_ms: tr.duration_ms });
        w.live.tools.set(id, line);
        host.appendChild(line);
      }
      if (line) paintTool(line, { ...tr, status: 'done', ok: tr.ok !== false, duration_ms: tr.duration_ms });
      return;
    }
    if (evt.todo_update) {
      renderTasks(host, evt.todo_update.items || []);
      scrollBottom(box);
      return;
    }
    if (evt.tool_approval) {
      approvalCard(w, evt.tool_approval);
      loadAttention();
      scrollBottom(box, true);
      return;
    }
    if (evt.approval_decided) {
      const el = $(`[data-approval="${evt.approval_decided.id}"]`, w.el);
      if (el) paintApproval(el, evt.approval_decided.status || 'denied');
      loadAttention();
      return;
    }
    if (evt.role_update) {
      loadAll();
      toast('角色库已更新', 'ok', 1800);
      return;
    }
    if (evt.app_update) {
      loadAttention();
      loadAll();
      return;
    }
    if (evt.ask_user) {
      askCard(w, evt.ask_user);
      loadAttention();
      scrollBottom(box, true);
      return;
    }
    if (evt.ask_user_done) {
      const el = $(`[data-question="${evt.ask_user_done.id}"]`, w.el);
      if (el) el.remove();
      loadAttention();
      return;
    }
    if (evt.error) {
      paintError(w, typeof evt.error === 'string' ? evt.error : (evt.error.message || '运行失败'));
      return;
    }
    if (evt.done) {
      const content = evt.content || evt.text;
      const live = w.live;
      if (content) {
        if (!live.textEl || live.lastKind !== 'text') {
          liveCloseThinking(w);
          liveCloseText(w);
          const el = document.createElement('div');
          el.className = 'bubble assistant-text';
          live.flow.appendChild(el);
          live.textEl = el;
          live.textRound = evt.round;
          live.lastKind = 'text';
        }
        live.text = content;
        live.textEl.innerHTML = md(content);
        live.textEl.classList.remove('streaming');
      }
      liveCloseThinking(w);
      if (live.textEl) live.textEl.classList.remove('streaming');
      $('.send', w.el).disabled = false;
      scrollBottom(box);
    }
  }

  // 新的思考片段：接在当前时间线后面；上一段思考 / 正文先收起来。
  function liveAppendThinking(w, piece, round) {
    const live = w.live;
    if (!live || !live.flow) return;
    if (!live.thinkEl || live.lastKind !== 'thinking' || live.thinkRound !== round) {
      liveCloseText(w);
      liveCloseThinking(w);
      const el = document.createElement('details');
      el.className = 'think-step';
      el.open = true;
      el.innerHTML = `<summary><span class="proc-caret">›</span><b>深度思考中</b><span class="think-brief"></span><span class="live-dots"><i></i><i></i><i></i></span></summary>
        <div class="think-body"></div>`;
      live.flow.appendChild(el);
      live.thinkEl = el;
      live.thinkText = '';
      live.thinkRound = round;
      live.lastKind = 'thinking';
    }
    live.thinkText += piece;
    const body = $('.think-body', live.thinkEl);
    if (body) body.textContent = live.thinkText;
    const brief = $('.think-brief', live.thinkEl);
    if (brief) brief.textContent = firstLine(live.thinkText, 120);
    scrollInner(body);
    scrollBottom($('.msgs', w.el));
  }

  // 正文增量：同一轮接到当前 text 块，换轮 / 被工具或思考打断后另起一块。
  function liveAppendText(w, piece, round) {
    const live = w.live;
    if (!live || !live.flow) return;
    if (!live.textEl || live.lastKind !== 'text' || live.textRound !== round) {
      liveCloseThinking(w);
      liveCloseText(w);
      const el = document.createElement('div');
      el.className = 'bubble assistant-text streaming';
      live.flow.appendChild(el);
      live.textEl = el;
      live.text = '';
      live.textRound = round;
      live.lastKind = 'text';
    }
    live.text += piece;
    live.textEl.textContent = live.text;
    if (!live.textEl.classList.contains('streaming')) live.textEl.classList.add('streaming');
    scrollBottom($('.msgs', w.el));
  }

  function liveCloseThinking(w) {
    const live = w && w.live;
    if (!live || !live.thinkEl) return;
    live.thinkEl.open = false;
    const dots = $('.live-dots', live.thinkEl);
    if (dots) dots.remove();
    const title = $('b', live.thinkEl);
    if (title) title.textContent = '深度思考';
    if (live.lastKind === 'thinking') live.lastKind = '';
    live.thinkEl = null;
    live.thinkText = '';
    live.thinkRound = null;
  }

  function liveCloseText(w) {
    const live = w && w.live;
    if (!live || !live.textEl) return;
    live.textEl.classList.remove('streaming');
    if (live.lastKind === 'text') live.lastKind = '';
    live.textEl = null;
    live.text = '';
    live.textRound = null;
  }

  function collapseLiveThinking(w) {
    liveCloseThinking(w);
    const live = w && w.live;
    if (live && live.textEl) live.textEl.classList.remove('streaming');
  }

  function approvalCard(w, a) {
    const el = document.createElement('div');
    el.className = `approval-card risk-${['low', 'medium', 'high'].includes(a.risk) ? a.risk : 'medium'}`;
    el.dataset.approval = a.id;
    el.innerHTML = `<div class="approval-head"><b>需要确认</b><span>${h(a.tool || a.name || '工具')}</span>
      <span class="tag">${h({ low: '低风险', medium: '中风险', high: '高风险' }[a.risk] || '中风险')}</span></div>
      ${a.note ? `<div class="approval-note">${h(a.note)}</div>` : ''}
      <div class="approval-actions">
        <button class="btn primary" data-appr="approve_once">允许一次</button>
        <button class="btn" data-appr="approve_session">本会话允许</button>
        <button class="btn" data-appr="approve_always">始终允许</button>
        <button class="btn danger" data-appr="deny">拒绝</button>
      </div>`;
    $('.msgs', w.el).appendChild(el);
    $$('[data-appr]', el).forEach((btn) => btn.addEventListener('click', async () => {
      const action = btn.dataset.appr;
      $$('button', el).forEach((b) => { b.disabled = true; });
      try {
        const out = await api(`/approvals/${encodeURIComponent(a.id)}`, {
          method: 'POST', body: JSON.stringify({ action }),
        });
        paintApproval(el, (out && out.status) || (action === 'deny' ? 'denied' : 'approved'));
        loadAttention();
      } catch (err) {
        toast(err.message, 'bad');
        $$('button', el).forEach((b) => { b.disabled = false; });
      }
    }));
  }

  function paintApproval(el, status) {
    el.dataset.status = status;
    const actions = $('.approval-actions', el);
    if (actions) actions.innerHTML = `<span class="approval-done">${h(status === 'approved' ? '已允许' : status === 'denied' ? '已拒绝' : status)}</span>`;
  }

  function askCard(w, q) {
    const el = document.createElement('div');
    el.className = 'ask-card';
    el.dataset.question = q.id;
    const options = Array.isArray(q.options) ? q.options : [];
    el.innerHTML = `<div class="approval-head"><b>需要你决定</b></div>
      <div class="ask-question">${h(q.question || '')}</div>
      ${options.length ? `<div class="ask-options">${options.map((o, i) =>
        `<button class="btn" data-ask="${i}">${h(o.label || o.value || o)}</button>`).join('')}</div>`
        : `<div class="ask-other"><input class="input" placeholder="输入回答…" /><button class="btn primary" data-ask-send>回答</button></div>`}
      <div class="approval-actions"><button class="btn ghost" data-ask-ignore>跳过</button></div>`;
    $('.msgs', w.el).appendChild(el);
    const answer = async (payload) => {
      try {
        await api(`/questions/${encodeURIComponent(q.id)}`, { method: 'POST', body: JSON.stringify(payload) });
        el.remove();
        loadAttention();
      } catch (err) { toast(err.message, 'bad'); }
    };
    $$('[data-ask]', el).forEach((btn) => btn.addEventListener('click', () => {
      const o = options[Number(btn.dataset.ask)];
      answer({ selected: [String(o.value || o.label || o)] });
    }));
    const send = $('[data-ask-send]', el);
    if (send) send.addEventListener('click', () => {
      const text = $('.ask-other input', el).value.trim();
      if (!text) return;
      answer({ other: text });
    });
    $('[data-ask-ignore]', el).addEventListener('click', () => answer({ ignored: true }));
  }

  function renderTasks(host, items) {
    if (!items.length) return;
    let card = $('.task-card', host.parentElement);
    if (!card) {
      card = document.createElement('div');
      card.className = 'task-card';
      host.appendChild(card);
    }
    const done = items.filter((x) => x.status === 'completed').length;
    const doing = items.filter((x) => x.status === 'in_progress').length;
    card.innerHTML = `<div class="task-head"><b>任务</b><span>${doing} 进行中 · ${items.length - done - doing} 待处理</span></div>` +
      items.map((x) => `<div class="task-item ${x.status === 'completed' ? 'done' : (x.status === 'in_progress' ? 'doing' : '')}">
        <span class="mark">${x.status === 'completed' ? '✓' : ''}</span><span>${h(x.content)}</span></div>`).join('');
  }

  /* ── 角色 / 群组 ──────────────────────────────────────── */
  async function openRoleDetail(roleId) {
    const role = S.roles.find((r) => r.id === roleId);
    if (!role) return;
    const action = await modal({
      title: role.name,
      fields: `<div class="role-line">${h(role.expertise || '')}</div>
        <div class="field"><label>人格 / 说明</label><div class="bubble" style="background:var(--bg-2)">${h(role.persona || '（未填写）')}</div></div>
        <div class="field"><label>工具</label><div class="chips">${(role.tools || []).map((t) => `<span class="chip">${h(t)}</span>`).join('') || '<span class="chip">无</span>'}</div></div>
        <div class="field"><label>模型</label><div class="role-line">${h(role.model_code || '跟随全局')}</div></div>`,
      buttons: [
        { label: '单聊', primary: true, value: 'chat' },
        { label: '编辑', value: 'edit' },
        ...(role.scope === 'temporary' ? [{ label: '存为持久角色', value: 'promote' }] : []),
        { label: '删除', danger: true, value: 'delete' },
      ],
      read: (v) => ({ action: v }),
    });
    if (!action) return;
    if (action.action === 'chat') return openRoleChat(role.id);
    if (action.action === 'edit') return openRoleEditor(role);
    if (action.action === 'promote') {
      try {
        await api(`/roles/${encodeURIComponent(role.id)}/promote`, { method: 'POST' });
        await loadAll(); toast('已存为持久角色', 'ok');
      } catch (err) { toast(err.message, 'bad'); }
      return;
    }
    if (action.action === 'delete') {
      if (!confirm(`删除角色「${role.name}」？`)) return;
      try {
        await api(`/roles/${encodeURIComponent(role.id)}`, { method: 'DELETE' });
        await loadAll(); toast('角色已删除', 'ok');
      } catch (err) { toast(err.message, 'bad'); }
    }
    return null;
  }

  async function ensureToolCatalog() {
    if (Array.isArray(S.toolCatalog)) return S.toolCatalog;
    try {
      const data = await api('/app/tools');
      S.toolCatalog = (data && data.items) || [];
    } catch { S.toolCatalog = []; }
    return S.toolCatalog;
  }

  function roleToolPickerHtml(role, catalog) {
    const picked = new Set((role && role.tools) || []);
    if (!catalog.length) {
      return `<input class="input mono" id="rTools" value="${h(role ? (role.tools || []).join(', ') : '')}" placeholder="read_file, shell（留空 = 全部）" />`;
    }
    return `<input class="input" id="rToolSearch" placeholder="搜索工具…" autocomplete="off" />
      <div class="tool-picker" id="rToolPicker">
        ${catalog.map((tool) => `<label class="tool-choice" title="${h(tool.description || '')}">
          <input type="checkbox" data-role-tool="${h(tool.code)}"${picked.has(tool.code) ? ' checked' : ''} />
          <span>${h(tool.name || tool.code)}</span>
          <span class="tool-choice-code">${h(tool.code)}</span>
        </label>`).join('')}
      </div>
      <div class="tool-picker-actions">
        <button class="mini-link" type="button" data-tools-all>全选</button>
        <button class="mini-link" type="button" data-tools-none>清空</button>
        <span id="rToolCount">${picked.size ? `已选 ${picked.size} 个` : '未选 = 全部工具'}</span>
      </div>`;
  }

  async function openRoleEditor(role) {
    const isNew = !role;
    const catalog = await ensureToolCatalog();
    const modelCode = (role && role.model_code) || '';
    const modelOptions = [`<option value="">跟随全局</option>`]
      .concat(S.models.map((m) => `<option value="${h(m.code)}"${m.code === modelCode ? ' selected' : ''}>${h(m.name || m.code)} · ${h(m.code)}</option>`));
    if (modelCode && !S.models.some((m) => m.code === modelCode)) {
      modelOptions.push(`<option value="${h(modelCode)}" selected>${h(modelCode)}</option>`);
    }
    const saved = await modal({
      title: isNew ? '新建角色' : `编辑「${role.name}」`,
      fields: `<div class="field"><label>名字</label><input class="input" id="rName" value="${h(role ? role.name : '')}" /></div>
        <div class="field"><label>专长（一句话）</label><input class="input" id="rExpertise" value="${h(role ? role.expertise : '')}" /></div>
        <div class="field"><label>人格 / 工作方式</label><textarea class="input" id="rPersona">${h(role ? role.persona : '')}</textarea></div>
        <div class="field"><label>工具权限 <span class="role-line">不选 = 全部工具</span></label>
          ${roleToolPickerHtml(role, catalog)}
        </div>
        <div class="field"><label>模型档案</label>
          <select class="input" id="rModel">${modelOptions.join('')}</select>
          <small class="role-line">留空则跟随全局模型。</small>
        </div>`,
      onMount: () => {
        const picker = $('#rToolPicker');
        if (!picker) return;
        const count = $('#rToolCount');
        const paint = () => {
          const n = $$('[data-role-tool]:checked', picker).length;
          if (count) count.textContent = n ? `已选 ${n} 个` : '未选 = 全部工具';
        };
        picker.addEventListener('change', paint);
        const search = $('#rToolSearch');
        if (search) search.addEventListener('input', () => {
          const q = search.value.trim().toLowerCase();
          $$('.tool-choice', picker).forEach((el) => {
            el.hidden = !!q && !el.textContent.toLowerCase().includes(q);
          });
        });
        const all = $('[data-tools-all]');
        if (all) all.addEventListener('click', () => {
          $$('[data-role-tool]', picker).forEach((el) => { el.checked = true; });
          paint();
        });
        const none = $('[data-tools-none]');
        if (none) none.addEventListener('click', () => {
          $$('[data-role-tool]', picker).forEach((el) => { el.checked = false; });
          paint();
        });
      },
      read: () => {
        const fallback = $('#rTools');
        return {
          name: $('#rName').value.trim(),
          expertise: $('#rExpertise').value.trim(),
          persona: $('#rPersona').value.trim(),
          tools: fallback
            ? fallback.value.split(',').map((x) => x.trim()).filter(Boolean)
            : $$('[data-role-tool]:checked').map((el) => el.dataset.roleTool),
          model_code: $('#rModel').value || null,
        };
      },
    });
    if (!saved) return;
    if (!saved.name) { toast('角色名不能为空', 'bad'); return; }
    try {
      if (isNew) await api('/roles', { method: 'POST', body: JSON.stringify(saved) });
      else await api(`/roles/${encodeURIComponent(role.id)}`, { method: 'PATCH', body: JSON.stringify(saved) });
      await loadAll(); toast('角色已保存', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function openGroupDetail(teamId) {
    const team = S.teams.find((t) => t.id === teamId);
    if (!team) return;
    const members = new Set((team.members || []).map((m) => m.id));
    const saved = await modal({
      title: `群组「${team.name}」`,
      fields: `<div class="field"><label>群名</label><input class="input" id="gName" value="${h(team.name)}" /></div>
        <div class="field"><label>协作方式</label>
          <select class="input" id="gMode">
            <option value="orchestrate"${team.mode === 'orchestrate' ? ' selected' : ''}>主持编排</option>
            <option value="roundtable"${team.mode === 'roundtable' ? ' selected' : ''}>圆桌</option>
            <option value="single"${team.mode === 'single' ? ' selected' : ''}>单角色</option>
          </select></div>
        <div class="field"><label>成员（从角色库选；可含临时角色）</label>
          <div style="display:flex;flex-direction:column;gap:6px">
          ${S.roles.map((r) => `<label class="member"><input type="checkbox" data-member="${h(r.id)}"${members.has(r.id) ? ' checked' : ''} />
            ${av(r.name, 'round')}<span class="row-main"><span class="row-title">${h(r.name)}</span>
            <span class="row-sub">${h(r.expertise || '')}</span></span>
            <span class="tag ${r.scope === 'temporary' ? 'tmp' : ''}">${r.scope === 'temporary' ? '临时' : ''}</span></label>`).join('')}
          </div></div>
        <div class="hintline">群组不绑定工作区；每个会话在标题栏单独选择文件上下文。</div>`,
      buttons: [
        { label: '打开群聊', value: 'open' },
        { label: '删除群组', danger: true, value: 'delete' },
        { label: '保存', primary: true, value: 'save' },
      ],
      read: (v) => ({
        action: v,
        patch: {
          name: $('#gName').value.trim(),
          mode: $('#gMode').value,
          members: $$('[data-member]').filter((x) => x.checked).map((x) => {
            const r = S.roles.find((y) => y.id === x.dataset.member);
            return { id: r.id, name: r.name, expertise: r.expertise };
          }),
        },
      }),
    });
    if (!saved) return;
    if (saved.action === 'open') return openGroupChat(team.id);
    if (saved.action === 'delete') {
      if (!confirm(`删除群组「${team.name}」？绑定的会话会退回单聊。`)) return;
      try {
        await api(`/teams/${encodeURIComponent(team.id)}`, { method: 'DELETE' });
        await loadAll(); toast('群组已删除', 'ok');
      } catch (err) { toast(err.message, 'bad'); }
      return;
    }
    if (!saved.patch.name) { toast('群名不能为空', 'bad'); return; }
    try {
      await api(`/teams/${encodeURIComponent(team.id)}`, { method: 'PATCH', body: JSON.stringify(saved.patch) });
      await loadAll(); toast('群组已保存', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── / 斜杠命令 ──────────────────────────────────────── */
  async function ensureCommands() {
    if (S.commands) return;
    try {
      const data = await api(`/app/commands?workspace_id=${encodeURIComponent(S.selectedWs || '')}`);
      S.commands = data.commands || [];
    } catch { S.commands = []; }
  }

  function slashHit(w) {
    const ta = $('.composer textarea', w.el);
    if (!ta) return null;
    const m = /^\/([A-Za-z0-9_-]*)$/.exec(ta.value.slice(0, ta.selectionStart));
    return m ? m[1].toLowerCase() : null;
  }

  async function updateSlash(w) {
    const pop = $('.slash-pop', w.el);
    if (!pop) return;
    if (slashHit(w) === null) { hideSlash(w); return; }
    await ensureCommands();
    const q = slashHit(w);
    if (q === null) { hideSlash(w); return; }
    const items = (S.commands || []).filter((c) => !q || String(c.name).toLowerCase().includes(q)).slice(0, 10);
    if (!items.length) { hideSlash(w); return; }
    w.slash = { items, index: 0 };
    pop.innerHTML = `<div class="mention-head">/ 命令</div>` + items.map((c, i) =>
      `<button class="mention-item${i === 0 ? ' active' : ''}" data-slash="${i}"><b>/${h(c.name)}</b><span>${h(c.description || '')}</span></button>`).join('');
    pop.hidden = false;
    $$('[data-slash]', pop).forEach((el) => el.addEventListener('mousedown', (e) => {
      e.preventDefault(); chooseSlash(w, Number(el.dataset.slash));
    }));
  }

  function hideSlash(w) {
    const pop = $('.slash-pop', w.el);
    if (pop) { pop.hidden = true; pop.innerHTML = ''; }
    w.slash = null;
  }

  function chooseSlash(w, index) {
    const item = w.slash && w.slash.items[index];
    if (!item) return hideSlash(w);
    const ta = $('.composer textarea', w.el);
    ta.value = `/${item.name} `;
    try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch { /* ignore */ }
    hideSlash(w);
    ta.focus();
  }

  function slashKey(w, e) {
    if (!w.slash) return false;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const n = w.slash.items.length;
      w.slash.index = (w.slash.index + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      $$('.slash-pop .mention-item', w.el).forEach((el, i) => el.classList.toggle('active', i === w.slash.index));
      return true;
    }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); chooseSlash(w, w.slash.index); return true; }
    if (e.key === 'Escape') { e.preventDefault(); hideSlash(w); return true; }
    return false;
  }

  /* ── @ 成员补全 ───────────────────────────────────────── */
  function mentionHit(w) {
    const ta = $('.composer textarea', w.el);
    if (!ta) return null;
    const caret = ta.selectionStart == null ? ta.value.length : ta.selectionStart;
    const before = ta.value.slice(0, caret);
    const m = /(^|\s)@([A-Za-z0-9_\-\u4e00-\u9fff]{0,24})$/.exec(before);
    if (!m) return null;
    return { start: caret - m[2].length - 1, end: caret, query: m[2] };
  }

  function updateMention(w) {
    const pop = $('.mention-pop', w.el);
    if (!pop) return;
    const hit = mentionHit(w);
    if (!hit || !w.members.length) { hideMention(w); return; }
    const q = hit.query.toLowerCase();
    const items = w.members.filter((m) => !q || String(m.name || '').toLowerCase().includes(q)).slice(0, 8);
    if (!items.length) { hideMention(w); return; }
    w.mention = { ...hit, items, index: 0 };
    pop.innerHTML = `<div class="mention-head">@ 成员</div>` + items.map((m, i) =>
      `<button class="mention-item${i === 0 ? ' active' : ''}" data-mention="${i}"><b>${h(m.name)}</b><span>${h(m.expertise || '')}</span></button>`).join('');
    pop.hidden = false;
    $$('[data-mention]', pop).forEach((el) => el.addEventListener('mousedown', (e) => {
      e.preventDefault(); chooseMention(w, Number(el.dataset.mention));
    }));
  }

  function hideMention(w) {
    const pop = $('.mention-pop', w.el);
    if (pop) { pop.hidden = true; pop.innerHTML = ''; }
    w.mention = null;
  }

  function chooseMention(w, index) {
    const m = w.mention;
    if (!m) return;
    const item = m.items[index];
    if (!item) return hideMention(w);
    const ta = $('.composer textarea', w.el);
    const before = ta.value.slice(0, m.start);
    const after = ta.value.slice(m.end);
    ta.value = `${before}@${item.name} ${after}`;
    const pos = before.length + String(item.name).length + 2;
    try { ta.setSelectionRange(pos, pos); } catch { /* ignore */ }
    hideMention(w);
    ta.focus();
  }

  function mentionKey(w, e) {
    if (!w.mention) return false;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const n = w.mention.items.length;
      w.mention.index = (w.mention.index + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      $$('.mention-item', w.el).forEach((el, i) => el.classList.toggle('active', i === w.mention.index));
      return true;
    }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); chooseMention(w, w.mention.index); return true; }
    if (e.key === 'Escape') { e.preventDefault(); hideMention(w); return true; }
    return false;
  }

  /* ── 会话内创建临时角色 ───────────────────────────────── */
  async function createTempRole(w) {
    if (!w.teamId) { toast('单聊没有群成员，直接创建持久角色即可', 'bad'); return; }
    const saved = await modal({
      title: '临时角色（仅本会话）',
      fields: `<div class="field"><label>名字</label><input class="input" id="tName" placeholder="例如：调研员" /></div>
        <div class="field"><label>专长（一句话）</label><input class="input" id="tExpertise" placeholder="只查资料，不改代码" /></div>
        <div class="field"><label>人格 / 工作方式</label><textarea class="input" id="tPersona"></textarea></div>
        <div class="field"><label>工具（逗号分隔，留空 = 全部）</label><input class="input" id="tTools" /></div>
        <div class="hintline">临时角色只属于当前会话；关闭会话后自动销毁。也可以之后「存为持久角色」。</div>`,
      read: () => ({
        name: $('#tName').value.trim(),
        expertise: $('#tExpertise').value.trim(),
        persona: $('#tPersona').value.trim(),
        tools: $('#tTools').value.split(',').map((x) => x.trim()).filter(Boolean),
      }),
    });
    if (!saved) return;
    if (!saved.name) { toast('角色名不能为空', 'bad'); return; }
    try {
      const role = await api('/roles', {
        method: 'POST',
        body: JSON.stringify({ ...saved, scope: 'temporary', session_id: w.convId, source: 'user' }),
      });
      const team = S.teams.find((t) => t.id === w.teamId);
      const members = ((team && team.members) || []).map((m) => ({ id: m.id, name: m.name, expertise: m.expertise }))
        .concat([{ id: role.id, name: role.name, expertise: role.expertise }]);
      await api(`/teams/${encodeURIComponent(w.teamId)}`, {
        method: 'PATCH', body: JSON.stringify({ members }),
      });
      await loadAll();
      const fresh = S.teams.find((t) => t.id === w.teamId);
      w.members = fresh ? (fresh.members || []) : members;
      toast(`临时角色「${role.name}」已加入本会话`, 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── 通用模态框 ───────────────────────────────────────── */
  function modal({ title, fields, buttons, read, onMount }) {
    return new Promise((resolve) => {
      const back = $('#modal');
      $('#modalTitle').textContent = title;
      $('#modalBody').innerHTML = fields || '';
      const foot = $('#modal .modal-foot');
      const btns = buttons || [{ label: '保存', primary: true, value: 'save' }];
      foot.innerHTML = '';
      const cancel = document.createElement('button');
      cancel.className = 'btn'; cancel.id = 'modalCancel'; cancel.textContent = '取消';
      foot.appendChild(cancel);
      btns.forEach((b) => {
        const el = document.createElement('button');
        el.className = `btn${b.primary ? ' primary' : ''}${b.danger ? ' danger' : ''}`;
        el.textContent = b.label;
        el.addEventListener('click', () => {
          if (b.value === 'save' && !$('#modalBody').querySelector('input,select,textarea')) return;
          const out = read ? read(b.value) : true;
          close();
          resolve(out);
        });
        foot.appendChild(el);
      });
      const close = () => { back.hidden = true; document.removeEventListener('keydown', onKey); };
      const onKey = (e) => { if (e.key === 'Escape') { close(); resolve(null); } };
      document.addEventListener('keydown', onKey);
      cancel.onclick = () => { close(); resolve(null); };
      const closeBtn = $('#modalClose');
      if (closeBtn) closeBtn.onclick = () => { close(); resolve(null); };
      back.hidden = false;
      if (onMount) {
        try { onMount(); } catch (err) { console.warn('modal mount failed', err); }
      }
      const first = $('#modalBody input, #modalBody textarea');
      if (first) setTimeout(() => first.focus(), 30);
    });
  }

  /* ── 全局 Agent：待授权 / 通知 / 授权管理 ─────────────── */
  const GA_MODE_LABEL = { auto: '自动判断', once: '每次询问', session: '本会话允许', always: '完全允许' };
  const GA_RISK_LABEL = { low: '低风险', medium: '中风险', high: '高风险' };

  function gaModeOptions(current) {
    return Object.entries(GA_MODE_LABEL).map(([v, label]) =>
      `<option value="${v}"${v === current ? ' selected' : ''}>${label}</option>`).join('');
  }

  function paintGaBadges() {
    const c = (S.attention && S.attention.counts) || {};
    const auth = Number(c.authorizations || 0);
    const note = Number(c.unread || 0);
    const a = $('#gaAuthBadge');
    const n = $('#gaNoteBadge');
    if (a) { a.hidden = auth <= 0; a.textContent = auth > 99 ? '99+' : String(auth); }
    if (n) { n.hidden = note <= 0; n.textContent = note > 99 ? '99+' : String(note); }
    const fab = $('#gaFab');
    if (fab) fab.title = `全局 Agent · 待授权 ${auth} · 未读 ${note} · 运行 ${Number(c.running || 0)}`;
  }

  function maybeNotifyAttention(counts) {
    const count = Number((counts || {}).authorizations || 0);
    if (S.gaNotifyCount < 0) { S.gaNotifyCount = count; return; }
    if (count > S.gaNotifyCount && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      try {
        const note = new Notification('AtlasX · 全局 Agent', {
          body: `${count} 个待授权操作等待处理`, tag: 'atlasx-attention',
        });
        note.onclick = () => { window.focus(); openGlobalAgent(); note.close(); };
      } catch { /* ignore */ }
    }
    S.gaNotifyCount = count;
  }

  let gaSig = '';
  function attentionSignature(data) {
    const d = data || {};
    const ids = (arr) => (arr || []).map((x) => String(x.id || '')).join(',');
    return [
      JSON.stringify(d.counts || {}),
      ids(d.approvals), ids(d.questions), ids(d.unread), ids(d.running),
      (d.sessions || []).map((s) => `${s.id}:${s.permission_mode || ''}`).join(','),
      JSON.stringify(d.policy || {}),
    ].join('|');
  }

  async function enableGaNotify() {
    if (typeof Notification === 'undefined') {
      toast('当前环境不支持系统通知', 'bad');
      return;
    }
    const perm = await Notification.requestPermission();
    if (perm === 'granted') toast('已开启系统通知', 'ok', 1800);
    else toast('未获得通知授权', 'bad', 1800);
    if (S.gaOpen) renderGaPanel();
  }

  async function loadAttention() {
    try {
      const data = await api('/app/attention?limit=100');
      const sig = attentionSignature(data);
      const changed = sig !== gaSig;
      gaSig = sig;
      S.attention = data;
      paintGaBadges();
      maybeNotifyAttention(data.counts);
      if (S.gaOpen && changed) renderGaPanel();
      autoHandleLowRisk(data);
    } catch { /* 全局通知失败不影响主界面 */ }
  }

  let gaAutoBusy = false;
  async function autoHandleLowRisk(data) {
    if (!S.gaAuto || gaAutoBusy || !data) return;
    const low = (data.approvals || []).filter((a) => String(a.risk || '') === 'low'
      && !S.gaAutoHandled.has(String(a.id || '')));
    if (!low.length) return;
    gaAutoBusy = true;
    try {
      for (const a of low) {
        const id = String(a.id || '');
        if (!id) continue;
        S.gaAutoHandled.add(id);
        try {
          await api(`/approvals/${encodeURIComponent(id)}`, {
            method: 'POST', body: JSON.stringify({ action: 'approve_once' }),
          });
          toast(`全局 Agent 已代批：${a.tool || '低风险工具'}`, 'ok', 1800);
        } catch { /* ignore */ }
      }
    } finally {
      gaAutoBusy = false;
      setTimeout(() => { if (S.gaOpen) loadAttention(); }, 300);
    }
  }

  function openGaPanel(tab) {
    S.gaOpen = true;
    if (tab && ['auth', 'note', 'perm'].includes(tab)) S.gaTab = tab;
    const panel = $('#gaPanel');
    if (panel) panel.hidden = false;
    renderGaPanel();
    loadAttention();
  }

  function closeGaPanel() {
    S.gaOpen = false;
    const panel = $('#gaPanel');
    if (panel) panel.hidden = true;
  }

  function toggleGaPanel() {
    if (S.gaOpen) closeGaPanel(); else openGaPanel();
  }

  function gaAuthHtml(data) {
    const approvals = data.approvals || [];
    const questions = data.questions || [];
    const sessionTitle = (id) => {
      const c = S.convs.find((x) => x.id === id);
      return c ? convTitle(c) : (id ? String(id) : '会话');
    };
    if (!approvals.length && !questions.length) {
      return `<div class="ga-empty">没有待授权操作<br>危险工具、提问会集中到这里</div>`;
    }
    let html = '';
    if (approvals.length) {
      html += `<div class="ga-sec-title">工具授权 · ${approvals.length}</div>`;
      html += approvals.map((a) => {
        const risk = ['low', 'medium', 'high'].includes(a.risk) ? a.risk : 'medium';
        const title = sessionTitle(a.conversation_id);
        return `<div class="ga-card risk-${risk}">
          <div class="ga-card-head"><b>需要确认</b><span class="tag">${h(GA_RISK_LABEL[risk])}</span>
            <span class="ga-meta" title="${h(title)}">${h(title)}</span></div>
          <div class="ga-card-title">${h(a.tool || a.name || '工具')}</div>
          ${a.note ? `<div class="ga-card-note">${h(a.note)}</div>` : ''}
          <div class="ga-card-actions">
            <button class="btn primary" data-ga-appr="approve_once" data-id="${h(a.id)}">允许一次</button>
            <button class="btn" data-ga-appr="approve_session" data-id="${h(a.id)}">本会话允许</button>
            <button class="btn" data-ga-appr="approve_always" data-id="${h(a.id)}">始终允许</button>
            <button class="btn danger" data-ga-appr="deny" data-id="${h(a.id)}">拒绝</button>
            <button class="btn" data-ga-jump="${h(a.conversation_id)}">定位</button>
          </div></div>`;
      }).join('');
    }
    if (questions.length) {
      html += `<div class="ga-sec-title">待回答 · ${questions.length}</div>`;
      html += questions.map((q) => {
        const options = Array.isArray(q.options) ? q.options : [];
        const title = sessionTitle(q.conversation_id);
        return `<div class="ga-card" data-ga-qid="${h(q.id)}">
          <div class="ga-card-head"><b>需要你决定</b>
            <span class="ga-meta" title="${h(title)}">${h(title)}</span></div>
          <div class="ga-card-title">${h(q.question || '请选择')}</div>
          ${options.length ? `<div class="ga-card-actions">${options.map((o, i) =>
            `<button class="btn" data-ga-opt="${i}" data-qid="${h(q.id)}">${h(o.label || o.value || o)}</button>`).join('')}</div>`
            : `<div class="ga-answer"><input class="input" data-ga-answer-input="${h(q.id)}" placeholder="输入回答…" /><button class="btn primary" data-ga-answer="${h(q.id)}">回答</button></div>`}
          <div class="ga-card-actions">
            ${options.length ? `<div class="ga-answer"><input class="input" data-ga-answer-input="${h(q.id)}" placeholder="或者输入其他回答…" /><button class="btn" data-ga-answer="${h(q.id)}">回答</button></div>` : ''}
            <button class="btn" data-ga-skip="${h(q.id)}">跳过</button>
            <button class="btn" data-ga-jump="${h(q.conversation_id)}">定位</button>
          </div></div>`;
      }).join('');
    }
    return html;
  }

  function gaNoteHtml(data) {
    const unread = data.unread || [];
    const running = data.running || [];
    if (!unread.length && !running.length) {
      return `<div class="ga-empty">没有未读通知<br>运行中的会话也会显示在这里</div>`;
    }
    let html = '';
    if (running.length) {
      html += `<div class="ga-sec-title">运行中 · ${running.length}</div>`;
      html += running.map((r) => `<div class="ga-row">
        <span class="dot run"></span>
        <span class="row-main"><span class="row-title">${h(r.title || r.conversation_id || '运行')}</span>
          <span class="row-sub">${h(r.status || '运行中')} · ${h(r.workspace_name || '')}</span></span>
        <button class="btn" data-ga-open="${h(r.conversation_id)}">打开</button>
        <button class="btn danger" data-ga-stop="${h(r.id)}">停止</button>
      </div>`).join('');
    }
    if (unread.length) {
      html += `<div class="ga-sec-title">未读会话 · ${unread.length}
        <button class="btn" data-ga-all-read style="margin-left:auto">全部已读</button></div>`;
      html += unread.map((c) => `<div class="ga-row">
        <span class="dot"></span>
        <span class="row-main"><span class="row-title">${h(convTitle(c))}</span>
          <span class="row-sub">${h(c.last_message || '有新内容')}</span></span>
        <button class="btn" data-ga-open="${h(c.id)}">打开</button>
        <button class="btn" data-ga-read="${h(c.id)}">已读</button>
      </div>`).join('');
    }
    return html;
  }

  function gaPermHtml(data) {
    const policy = data.policy || {};
    const sessions = data.sessions || [];
    const mode = policy.default || 'once';
    const notifySupported = typeof Notification !== 'undefined';
    const notifyState = notifySupported ? Notification.permission : 'unsupported';
    let html = `<div class="ga-global">
      <label><span>新会话默认权限</span><select class="input" id="gaDefaultMode">${gaModeOptions(mode)}</select></label>
      <label><span>系统通知</span><button class="btn" id="gaNotifyBtn">${notifyState === 'granted' ? '已开启' : '授权通知'}</button></label>
      <label class="ga-toggle"><input type="checkbox" id="gaAutoApprove"${S.gaAuto ? ' checked' : ''} /> 全局 Agent 代处理低风险授权</label>
      <div class="ga-note-line">开启后，低风险工具审批自动放行；中 / 高风险仍等你确认。系统通知需要浏览器授权。</div>
    </div>
    <div class="ga-sec-title">会话授权 · ${sessions.length}</div>`;
    html += sessions.map((s) => {
      const current = s.permission_mode || mode;
      return `<div class="ga-row">
        <span class="row-main"><span class="row-title">${h(convTitle(s))}</span>
          <span class="row-sub">${h(s.updated_at || '')}</span></span>
        <select class="mini input" data-ga-perm="${h(s.id)}">${gaModeOptions(current)}</select>
      </div>`;
    }).join('') || '<div class="ga-empty">还没有会话</div>';
    return html;
  }

  function renderGaPanel() {
    const panel = $('#gaPanel');
    if (!panel) return;
    const data = S.attention || { counts: {}, approvals: [], questions: [], unread: [], running: [], sessions: [], policy: {} };
    const counts = data.counts || {};
    const scroll = ($('.ga-body', panel) || {}).scrollTop || 0;
    const auth = Number(counts.authorizations || 0);
    const note = Number(counts.unread || 0);
    const tab = S.gaTab || 'auth';
    let bodyHtml = '';
    if (tab === 'note') bodyHtml = gaNoteHtml(data);
    else if (tab === 'perm') bodyHtml = gaPermHtml(data);
    else bodyHtml = gaAuthHtml(data);
    panel.innerHTML = `<header class="ga-head">
        <span class="ga-logo"><svg viewBox="0 0 24 24"><rect x="4" y="8" width="16" height="11" rx="4"/><path d="M12 4v4M9 13h.01M15 13h.01M9 16h6"/></svg></span>
        <span class="ga-title"><b>全局 Agent</b><span>待授权 ${auth} · 未读 ${note} · 运行 ${Number(counts.running || 0)}</span></span>
        <button class="ga-close" data-ga-close title="关闭">×</button>
      </header>
      <div class="ga-tabs">
        <button class="ga-tab${tab === 'auth' ? ' on' : ''}" data-ga-tab="auth" aria-pressed="${tab === 'auth'}">待授权${auth ? `<span class="n">${auth}</span>` : ''}</button>
        <button class="ga-tab${tab === 'note' ? ' on' : ''}" data-ga-tab="note" aria-pressed="${tab === 'note'}">通知${note ? `<span class="n note">${note}</span>` : ''}</button>
        <button class="ga-tab${tab === 'perm' ? ' on' : ''}" data-ga-tab="perm" aria-pressed="${tab === 'perm'}">授权管理</button>
      </div>
      <div class="ga-body">${bodyHtml}</div>`;
    const newBody = $('.ga-body', panel);
    if (newBody) newBody.scrollTop = scroll;
    $$('[data-ga-tab]', panel).forEach((btn) => btn.addEventListener('click', () => {
      S.gaTab = btn.dataset.gaTab; renderGaPanel();
    }));
    $('[data-ga-close]', panel).addEventListener('click', closeGaPanel);
    // 工具授权
    $$('[data-ga-appr]', panel).forEach((btn) => btn.addEventListener('click', async () => {
      const id = btn.dataset.id;
      const action = btn.dataset.gaAppr;
      $$('button', btn.closest('.ga-card')).forEach((b) => { b.disabled = true; });
      try {
        await api(`/approvals/${encodeURIComponent(id)}`, {
          method: 'POST', body: JSON.stringify({ action }),
        });
        toast(action === 'deny' ? '已拒绝' : '已授权', 'ok', 1200);
        await loadAttention();
      } catch (err) { toast(err.message, 'bad'); }
    }));
    // 待回答问题
    $$('[data-ga-opt]', panel).forEach((btn) => btn.addEventListener('click', async () => {
      const q = (data.questions || []).find((x) => String(x.id) === String(btn.dataset.qid));
      if (!q) return;
      const opt = (q.options || [])[Number(btn.dataset.gaOpt)];
      await gaAnswer(btn.dataset.qid, { selected: [String((opt || {}).label || (opt || {}).value || opt || '')] });
    }));
    $$('[data-ga-answer]', panel).forEach((btn) => btn.addEventListener('click', async () => {
      const input = $(`[data-ga-answer-input="${btn.dataset.gaAnswer}"]`, panel);
      const text = input ? input.value.trim() : '';
      if (!text) return;
      await gaAnswer(btn.dataset.gaAnswer, { other: text });
    }));
    $$('[data-ga-skip]', panel).forEach((btn) => btn.addEventListener('click', () => gaAnswer(btn.dataset.gaSkip, { ignored: true })));
    // 跳转 / 已读 / 停止
    $$('[data-ga-jump], [data-ga-open]', panel).forEach((btn) => btn.addEventListener('click', () => {
      const id = btn.dataset.gaJump || btn.dataset.gaOpen;
      closeGaPanel();
      if (id) openConversation(id);
    }));
    $$('[data-ga-read]', panel).forEach((btn) => btn.addEventListener('click', async () => {
      await api(`/conversations/${encodeURIComponent(btn.dataset.gaRead)}/read`, { method: 'POST' });
      await loadAll(); await loadAttention();
    }));
    const allRead = $('[data-ga-all-read]', panel);
    if (allRead) allRead.addEventListener('click', async () => {
      const list = (data.unread || []).map((c) => c.id);
      await Promise.all(list.map((id) => api(`/conversations/${encodeURIComponent(id)}/read`, { method: 'POST' }).catch(() => null)));
      await loadAll(); await loadAttention(); toast('已全部标记已读', 'ok', 1500);
    });
    $$('[data-ga-stop]', panel).forEach((btn) => btn.addEventListener('click', async () => {
      try { await api(`/runs/${encodeURIComponent(btn.dataset.gaStop)}/stop`, { method: 'POST' }); toast('已请求停止', 'ok', 1200); await loadAttention(); }
      catch (err) { toast(err.message, 'bad'); }
    }));
    // 授权管理
    const def = $('#gaDefaultMode', panel);
    if (def) def.addEventListener('change', async () => {
      try {
        await api('/app/permissions', { method: 'PUT', body: JSON.stringify({ default: def.value }) });
        toast('默认权限已更新', 'ok', 1200); await loadAttention();
      } catch (err) { toast(err.message, 'bad'); }
    });
    $$('[data-ga-perm]', panel).forEach((sel) => sel.addEventListener('change', async () => {
      try {
        await api(`/conversations/${encodeURIComponent(sel.dataset.gaPerm)}/permission`, {
          method: 'PUT', body: JSON.stringify({ mode: sel.value }),
        });
        toast('会话权限已更新', 'ok', 1200); await loadAttention();
      } catch (err) { toast(err.message, 'bad'); }
    }));
    const auto = $('#gaAutoApprove', panel);
    if (auto) auto.addEventListener('change', () => {
      S.gaAuto = !!auto.checked;
      try { localStorage.setItem('atlasx.ga.auto', S.gaAuto ? '1' : '0'); } catch { /* ignore */ }
      toast(S.gaAuto ? '全局 Agent 将代处理低风险授权' : '已关闭代处理', 'ok', 1600);
      if (S.gaAuto) loadAttention();
    });
    const notifyBtn = $('#gaNotifyBtn', panel);
    if (notifyBtn) notifyBtn.addEventListener('click', () => enableGaNotify());
  }

  async function gaAnswer(questionId, payload) {
    try {
      await api(`/questions/${encodeURIComponent(questionId)}`, {
        method: 'POST', body: JSON.stringify(payload),
      });
      await loadAttention();
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── 事件 ─────────────────────────────────────────────── */
  /* 窄屏：会话列表变成抽屉，点轨道切换栏目时滑出，点内容区收起。 */
  const narrow = () => window.matchMedia('(max-width:760px)').matches;
  function closeMobileList() { const app = $('#app'); if (app) app.classList.remove('list-open'); }
  $$('.rail-btn[data-section]').forEach((b) => b.addEventListener('click', () => {
    setSection(b.dataset.section);
    if (narrow()) $('#app').classList.add('list-open');
  }));
  document.addEventListener('click', (e) => {
    const app = $('#app');
    if (!app || !app.classList.contains('list-open')) return;
    if (e.target.closest('.listpane') || e.target.closest('.rail')) return;
    closeMobileList();
  });
  $('#searchInput').addEventListener('input', () => renderList());

  // ⌘/Ctrl + 1–9 直接切会话；⌘/Ctrl + ←/→ 前后切，多会话切换不用找鼠标。
  document.addEventListener('keydown', (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
    const list = [...S.windows.values()];
    if (!list.length) return;
    if (/^[1-9]$/.test(e.key)) {
      const w = list[Number(e.key) - 1];
      if (w) { e.preventDefault(); switchTo(w); }
      return;
    }
    if (list.length > 1 && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      const i = list.indexOf(S.activeWin);
      const w = list[((i < 0 ? 0 : i) + (e.key === 'ArrowLeft' ? -1 : 1) + list.length) % list.length];
      if (w) { e.preventDefault(); switchTo(w); }
    }
  });
  const themeToggle = $('#themeToggle');
  if (themeToggle) themeToggle.addEventListener('click', toggleTheme);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) tickTheme();
  });
  $('#listAdd').addEventListener('click', () => {
    if (S.section === 'role') openRoleEditor(null);
    else if (S.section === 'group') newGroup();
    else if (S.section === 'conv') newConversation();
    else if (S.section === 'ws') addWorkspace();
    else toast('该入口将在下一阶段迁移');
  });
  $('#listRefresh').addEventListener('click', async () => {
    await loadAll();
    toast('已刷新', 'ok', 1200);
  });
  const gaFab = $('#gaFab');
  if (gaFab) gaFab.addEventListener('click', (e) => { e.stopPropagation(); openGlobalAgent(); });
  document.addEventListener('click', (e) => {
    if (!S.gaOpen) return;
    if (e.target.closest('#gaPanel') || e.target.closest('#gaFab')) return;
    closeGaPanel();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && S.gaOpen) closeGaPanel();
  });
  setInterval(() => { if (!document.hidden) loadAttention(); }, 5000);

  async function newGroup() {
    const saved = await modal({
      title: '新建群组',
      fields: `<div class="field"><label>群名</label><input class="input" id="ngName" placeholder="例如：研发群" /></div>
        <div class="field"><label>协作方式</label><select class="input" id="ngMode">
          <option value="orchestrate">主持编排</option><option value="roundtable">圆桌</option><option value="single">单角色</option>
        </select></div>
        <div class="field"><label>成员</label><div style="display:flex;flex-direction:column;gap:6px">
        ${S.roles.map((r) => `<label class="member"><input type="checkbox" data-ng-member="${h(r.id)}" />
          ${av(r.name, 'round')}<span class="row-main"><span class="row-title">${h(r.name)}</span><span class="row-sub">${h(r.expertise || '')}</span></span></label>`).join('')}
        </div></div>`,
      read: () => ({
        name: $('#ngName').value.trim(),
        mode: $('#ngMode').value,
        members: $$('[data-ng-member]').filter((x) => x.checked).map((x) => {
          const r = S.roles.find((y) => y.id === x.dataset.ngMember);
          return { id: r.id, name: r.name, expertise: r.expertise };
        }),
      }),
    });
    if (!saved) return;
    if (!saved.name) { toast('群名不能为空', 'bad'); return; }
    try {
      await api('/teams', { method: 'POST', body: JSON.stringify(saved) });
      await loadAll(); toast('群组已创建', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  // 初始化（?section=role&conv=conv-xxx 便于深链 / 截图；?pick=ws 直接打开目录选择器）
  startThemeSchedule();
  loadAttention();
  loadAll().then(() => {
    const p = new URLSearchParams(location.search);
    if (p.get('section')) setSection(p.get('section'));
    const conv = p.get('conv');
    if (conv) openConversation(conv);
    if (p.get('pick') === 'ws') setTimeout(() => addWorkspace(), 200);
  }).catch((err) => toast(err.message, 'bad'));
})();

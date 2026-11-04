/* AtlasX V3 shell: 工作区 / 角色 / 群组独立，聊天嵌入画布并支持多标签切换。 */
(() => {
  'use strict';
  const API = '/api/agent-zero';
  const GLOBAL_AGENT_ZONE = 'global-agent';
  // 附件：与后端 attachment_service 的限制保持一致。
  const MAX_ATTACHMENTS = 8;
  const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;
  const MAX_ATTACHMENTS_TOTAL = 50 * 1024 * 1024;
  const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|bmp|avif)$/i;
  const DOC_EXTENSIONS = [
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.pdf',
    '.txt', '.text', '.md', '.markdown', '.mdx', '.rst', '.log', '.csv', '.tsv',
    '.json', '.jsonl', '.xml', '.yaml', '.yml', '.toml', '.ini', '.conf', '.cfg',
    '.properties', '.html', '.htm', '.css', '.scss', '.less', '.js', '.mjs', '.cjs',
    '.jsx', '.ts', '.tsx', '.vue', '.svelte', '.py', '.java', '.kt', '.scala',
    '.cs', '.c', '.h', '.cc', '.cpp', '.hpp', '.go', '.rs', '.rb', '.php', '.swift',
    '.dart', '.lua', '.r', '.pl', '.sh', '.bash', '.zsh', '.fish', '.bat', '.cmd',
    '.ps1', '.sql', '.gradle', '.tex', '.proto',
  ];
  const DOC_ACCEPT = DOC_EXTENSIONS.join(',');
  const IMAGE_ACCEPT = 'image/png,image/jpeg,image/gif,image/webp,image/bmp,image/avif';
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
  const avatarInner = (name, avatar = '') => (avatar
    ? `<img class="avatar-img" src="${h(avatar)}" alt="" loading="lazy" />`
    : h((String(name || '·')[0] || '·').toUpperCase()));
  const av = (name, cls = '', avatar = '') => `<span class="avatar ${cls}" style="--h:${hue(name)}">${avatarInner(name, avatar)}</span>`;
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

  async function uploadAvatar(file, scope) {
    if (!file) return null;
    const form = new FormData();
    form.append('scope', scope);
    form.append('file', file);
    const res = await fetch(`${API}/avatars`, { method: 'POST', body: form });
    let body = null;
    try { body = await res.json(); } catch { /* ignore */ }
    if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
    return body && body.data !== undefined ? body.data : body;
  }

  function avatarFieldHtml(id, scope, current, name) {
    return `<div class="avatar-field" data-avatar-field="${h(id)}">
      <span class="avatar round avatar-preview" data-avatar-preview="${h(id)}" style="--h:${hue(name)}">${avatarInner(name, current || '')}</span>
      <div class="avatar-field-main">
        <input type="hidden" id="${h(id)}" value="${h(current || '')}" />
        <input type="file" id="${h(id)}File" accept="image/*" hidden />
        <div class="avatar-actions">
          <button class="btn small" type="button" data-avatar-pick="${h(id)}">上传头像</button>
          <button class="btn small" type="button" data-avatar-clear="${h(id)}">移除</button>
        </div>
        <small class="role-line">支持 PNG / JPG / GIF / WebP / SVG / BMP / TIFF 等，最大 8 MB。</small>
      </div>
    </div>`;
  }

  function bindAvatarField(id, scope, name) {
    const hidden = document.getElementById(id);
    const input = document.getElementById(`${id}File`);
    const preview = document.querySelector(`[data-avatar-preview="${id}"]`);
    if (!hidden || !input) return;
    const pick = document.querySelector(`[data-avatar-pick="${id}"]`);
    const clear = document.querySelector(`[data-avatar-clear="${id}"]`);
    if (pick) pick.addEventListener('click', () => input.click());
    if (clear) clear.addEventListener('click', () => {
      hidden.value = '';
      if (preview) preview.innerHTML = avatarInner(name, '');
    });
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      input.value = '';
      if (!file) return;
      try {
        const out = await uploadAvatar(file, scope);
        if (!out || !out.url) return;
        hidden.value = out.url;
        if (preview) preview.innerHTML = avatarInner(name, out.url);
        toast('头像已上传', 'ok', 1600);
      } catch (err) { toast(err.message, 'bad'); }
    });
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
    section: 'work',
    workspaces: [], convs: [], roles: [], teams: [], departments: [],
    roleDept: 'all',
    windows: new Map(), activeWin: null, z: 100, models: [], activeModel: '', commands: null, 
    globalWin: null,
    selectedWs: null, wsDrawerId: null, wsPath: '.', wsFiles: null, wsPreview: null, wsLoading: false,
    settingsTab: 'models', settings: null, draft: null, toolCatalog: null,
    workItems: [], workStats: {}, workPolicy: null, workFilter: 'open',
    workSelected: null, workDrawerId: null, workTimer: null,
    roleSkills: [], roleSkillsRoleId: null,
    agentCanvasConv: null, agentCanvasTimer: null, agentCanvasData: null,
    agentCanvasFilter: 'all', agentCanvasZoom: 1, agentCanvasSelected: null,
    agentCanvasReplayTimer: null, agentCanvasReplay: false, agentCanvasRefreshTimer: null,
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
  S.themeSyncTimer = null;

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
    if (opts.persist !== false) {
      persistThemePrefs();
      syncThemeToServer();
    }
    return S.theme;
  }

  function persistThemePrefs() {
    if (themeCore) themeCore.writePrefs(S.theme, S.themeSchedule);
  }

  function syncThemeToServer() {
    if (!themeCore) return;
    clearTimeout(S.themeSyncTimer);
    S.themeSyncTimer = setTimeout(() => {
      try {
        fetch(`${API}/app/theme`, {
          method: 'PUT',
          keepalive: true,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ theme: S.theme, themeSchedule: S.themeSchedule }),
        }).catch(() => {});
      } catch { /* ignore */ }
    }, 120);
  }

  async function initTheme() {
    // 本地 / cookie 有值时优先用本机选择；完全没有时才从服务器恢复。
    if (themeCore && !themeCore.hasSaved()) {
      try {
        const data = await api('/app/theme');
        if (data && data.theme) {
          S.themeSchedule = themeCore.normalizeSchedule(data.themeSchedule || S.themeSchedule);
          S.theme = data.theme;
          themeCore.writePrefs(S.theme, S.themeSchedule);
          applyTheme(S.theme, { persist: false });
        }
      } catch { /* ignore */ }
    }
    startThemeSchedule();
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

  function convAvatar(conv) {
    const { team, role, isGroup } = convParts(conv);
    if (isGroup && team) return team.avatar || '';
    return (role && role.avatar) || '';
  }

  // ── 组织：部门树 + 角色身份 ─────────────────────────────
  function roleLabel(role) {
    if (!role) return '';
    const bits = [];
    if (role.title) bits.push(role.title);
    if (role.department) bits.push(role.department);
    return bits.length ? `${role.name || role.id} · ${bits.join(' · ')}` : (role.name || role.id || '');
  }

  function deptById(id) {
    return S.departments.find((d) => d.id === id) || null;
  }

  function deptPath(id) {
    const seen = new Set();
    const names = [];
    let cur = deptById(id);
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id);
      names.push(cur.name);
      cur = deptById(cur.parent_id);
    }
    return names.reverse().join(' / ');
  }

  function deptChildren(parentId) {
    return S.departments.filter((d) => (d.parent_id || '') === (parentId || ''));
  }

  function deptOrdered() {
    const out = [];
    const walk = (parentId, depth) => {
      deptChildren(parentId).forEach((d) => {
        out.push({ ...d, depth });
        walk(d.id, depth + 1);
      });
    };
    walk('', 0);
    return out;
  }

  function deptSubtree(id) {
    const out = new Set([String(id)]);
    let changed = true;
    while (changed) {
      changed = false;
      S.departments.forEach((d) => {
        if (!out.has(d.id) && out.has(d.parent_id || '')) { out.add(d.id); changed = true; }
      });
    }
    return out;
  }

  function deptRoleCount(id, subtree = true) {
    const ids = subtree ? deptSubtree(id) : new Set([String(id)]);
    return S.roles.filter((r) => ids.has(r.department_id || '')).length;
  }

  function deptOptions(current, opts = {}) {
    const rows = [];
    const walk = (parentId, depth) => {
      deptChildren(parentId).forEach((d) => {
        if (opts.exclude && opts.exclude.has(d.id)) return;
        rows.push(`<option value="${h(d.id)}"${d.id === current ? ' selected' : ''}>${'　'.repeat(depth)}${h(d.name)}</option>`);
        walk(d.id, depth + 1);
      });
    };
    walk('', 0);
    return `<option value=""${!current ? ' selected' : ''}>${h(opts.emptyLabel || '未安排')}</option>` + rows.join('');
  }

  function roleDeptMatches(role, key) {
    if (key === 'all') return true;
    if (!key) return !role.department_id;
    return deptSubtree(key).has(role.department_id || '');
  }

  // 会话标题：群聊显示群名、单聊显示角色名，避免用首条消息当标题
  function convTitle(conv) {
    if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) return '工单';
    const { team, role, isGroup } = convParts(conv);
    if (isGroup && team) return team.name;
    if (role) return role.name;
    return conv.title || '未命名会话';
  }

  function convMeta(conv) {
    if (String(conv.zone || '') === GLOBAL_AGENT_ZONE) {
      return { kind: '工单', sub: '主动推进 · 代处理授权' };
    }
    const { team, role, members, isGroup } = convParts(conv);
    if (isGroup && team) {
      return { kind: '群聊', sub: `${members.length} 个角色 · ${modeLabel(team.mode)}` };
    }
    return { kind: '单聊', sub: role && role.name ? `角色：${roleLabel(role)}` : '默认角色' };
  }

  function convIsGroup(conv) {
    return convParts(conv).isGroup;
  }

  /* ── 列表 ─────────────────────────────────────────────── */
  const TITLES = { conv: '会话', work: '工单', ws: '工作区', role: '组织与角色', group: '群组', settings: '设置', about: '关于' };
  const ADD_LABEL = { conv: '新建会话', work: '新建工单', ws: '添加工作区', role: '新建角色', group: '新建群组' };
  const WORK_FILTERS = [
    ['open', '待办'], ['attention', '需要你'], ['inbox', '待分派'],
    ['working', '进行中'], ['done', '已完成'], ['all', '全部'],
  ];
  const WORK_STATUS = {
    inbox: '待分派', working: '进行中', needs_attention: '需要你',
    done: '已完成', closed: '已关闭',
  };

  async function loadAll() {
    const [ws, conv, roles, teams, depts, models] = await Promise.all([
      api('/workspaces?include_archived=true').catch(() => ({ items: [] })),
      api('/conversations?limit=80').catch(() => ({ items: [] })),
      api('/roles').catch(() => ({ items: [] })),
      api('/teams').catch(() => ({ items: [] })),
      api('/departments').catch(() => ({ items: [] })),
      api('/app/models').catch(() => ({ items: [], active: '' })),
    ]);
    S.models = models.items || [];
    S.activeModel = models.active || '';
    S.workspaces = (ws.items || []).filter((x) => !x.archived);
    S.convs = conv.items || [];
    S.roles = roles.items || [];
    S.teams = teams.items || [];
    S.departments = depts.items || [];
    if (S.roleDept !== 'all' && S.roleDept !== '' && !deptById(S.roleDept)) S.roleDept = 'all';
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

  /* ── 工单 ─────────────────────────────────── */
  async function loadWorkItems(opts = {}) {
    try {
      const data = await api('/work-items?limit=120');
      S.workItems = data.items || [];
      S.workStats = data.stats || {};
      S.workPolicy = data.policy || S.workPolicy;
      if (S.workSelected && !S.workItems.some((w) => w.id === S.workSelected)) {
        S.workSelected = null;
      }
      if (S.workDrawerId && !S.workItems.some((w) => w.id === S.workDrawerId)) {
        S.workDrawerId = null;
      }
      paintWorkBadge();
      const modalOpen = $('#modal') && !$('#modal').hidden;
      if (S.workDrawerId && !modalOpen) {
        renderWorkDrawer();
      } else if (S.section === 'work' && !modalOpen) {
        renderCanvas();
      }
    } catch (err) {
      if (!opts.quiet) toast(`工单加载失败：${err.message}`, 'bad');
    }
  }

  function paintWorkBadge() {
    const n = Number((S.workStats && S.workStats.attention) || 0);
    const badge = $('#workBadge');
    if (badge) {
      badge.hidden = n <= 0;
      badge.textContent = n > 99 ? '99+' : String(n);
    }
    const btn = $('.rail-btn[data-section="work"]');
    if (btn) btn.title = n ? `工单 · ${n} 个需要你` : '工单';
  }

  function workItemsForFilter() {
    const f = S.workFilter;
    return S.workItems.filter((w) => {
      if (f === 'all') return true;
      if (f === 'open') return ['inbox', 'working', 'needs_attention'].includes(w.status);
      return w.status === f;
    });
  }

  function workSelectedItem() {
    return S.workItems.find((w) => w.id === S.workSelected) || null;
  }

  function attentionInboxHtml() {
    const attention = S.attention || {};
    const items = [];
    (attention.approvals || []).forEach((a) => {
      items.push({
        kind: 'approval', id: a.id, conversation: a.conversation_id,
        title: a.tool || a.name || '工具授权',
        sub: a.conversation_title || a.conversation_id || '',
        note: a.note || (a.reasons || []).join(' · ') || '需要你批准后才能继续',
      });
    });
    (attention.questions || []).forEach((q) => {
      items.push({
        kind: 'question', id: q.id, conversation: q.conversation_id,
        title: q.question || '代理在等你回答',
        sub: q.conversation_title || q.conversation_id || '',
        note: '回答后代理继续推进',
      });
    });
    if (!items.length) {
      return `<section class="sa-attention sa-attention-empty">
        <span class="sa-attention-ico"><svg viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg></span>
        <div><b>现在没有需要你处理的事</b><span>工单会在需要授权或回答时回来。</span></div>
      </section>`;
    }
    return `<section class="sa-attention">
      <div class="sa-section-head">
        <div><h2>需要你处理</h2><p>这些事停下来等你决定，其余工作继续在后台推进。</p></div>
        <span class="sa-count danger">${items.length}</span>
      </div>
      <div class="sa-attention-list">${items.slice(0, 8).map((it) => `<div class="sa-attention-row">
        <span class="sa-attention-type ${it.kind}">${it.kind === 'approval' ? '授权' : '回答'}</span>
        <div class="sa-attention-main"><b>${h(it.title)}</b><span>${h(it.sub)} · ${h(it.note)}</span></div>
        ${it.conversation ? `<button class="btn small primary" type="button" data-attn-conv="${h(it.conversation)}">处理</button>` : ''}
      </div>`).join('')}</div>
    </section>`;
  }

  const FLAT_SECTIONS = new Set(['conv', 'work', 'role', 'group', 'ws']);

  function applySectionLayout(name) {
    const app = $('#app');
    if (app) app.classList.toggle('flat', FLAT_SECTIONS.has(name));
  }

  function setSection(name) {
    if (name === 'about') { openAboutModal(); return; }
    S.section = name;
    applySectionLayout(name);
    $$('.rail-btn').forEach((b) => b.classList.toggle('active', b.dataset.section === name));
    $('#listTitle').textContent = TITLES[name] || '';
    if (name === 'work') loadWorkItems({ quiet: true });
    renderList();
    renderCanvas();
  }

  function renderList() {
    const body = $('#listBody');
    const q = ($('#searchInput').value || '').trim().toLowerCase();
    const match = (s) => !q || String(s || '').toLowerCase().includes(q);
    let html = '';
    if (FLAT_SECTIONS.has(S.section)) { body.innerHTML = ''; return; }
    if (S.section === 'role') {
      const visible = S.roles.filter((r) => match(r.name) || match(r.title)
        || match(r.department) || match(r.expertise));
      const rowHtml = (r) => `<button class="row" data-role="${h(r.id)}">
        ${av(r.name || '角', 'round', r.avatar)}
        <span class="row-main"><span class="row-title">${h(r.name)}
          ${r.scope === 'temporary' ? '<span class="tag tmp">临时</span>' : '<span class="tag">持久</span>'}</span>
          <span class="row-sub">${h([r.title, r.department || '未安排部门', r.expertise || r.persona || ''].filter(Boolean).join(' · '))}</span></span>
      </button>`;
      const groups = deptOrdered().map((d) => ({
        id: d.id, label: d.name, path: deptPath(d.id), depth: d.depth,
        avatar: d.avatar || '',
        rows: visible.filter((r) => r.department_id === d.id),
      }));
      groups.push({
        id: '', label: '未安排', path: '未安排部门', depth: 0, avatar: '',
        rows: visible.filter((r) => !r.department_id || !deptById(r.department_id)),
      });
      const grouped = groups.filter((g) => g.rows.length).map((g) =>
        `<div class="list-dept" style="--depth:${g.depth}" title="${h(g.path)}">
          ${g.avatar ? av(g.label, 'xs round', g.avatar) : ''}<span>${h(g.label)}</span><b>${g.rows.length}</b>
        </div>${g.rows.map(rowHtml).join('')}`).join('');
      html = grouped || `<div class="empty">角色库是空的<br>新建一个，或在对话里让 LLM 创建</div>`;
    } else if (S.section === 'group') {
      const items = S.teams.filter((t) => match(t.name));
      html = items.map((t) => `<button class="row" data-group="${h(t.id)}">
        ${av(t.name || '群', '', t.avatar)}
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
      work: `${S.workStats.open || 0} 个待办 · ${S.workStats.attention || 0} 个需要你`,
      ws: `${S.workspaces.length} 个工作区`,
      role: `${S.roles.filter((r) => r.scope !== 'temporary').length} 个持久角色 · ${S.departments.length} 个部门`,
      group: `${S.teams.length} 个群组 · 不绑定工作区`,
      about: '',
    }[S.section];
    $('#listFoot').innerHTML = `<span>${h(foot || '')}</span>`;
    $$('[data-work-filter]', body).forEach((el) => el.addEventListener('click', () => {
      S.workFilter = el.dataset.workFilter;
      renderList();
    }));
    $$('[data-work]', body).forEach((el) => el.addEventListener('click', () => {
      S.workSelected = el.dataset.work;
      closeMobileList();
      renderList();
      renderCanvas();
    }));
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
      el.addEventListener('click', () => { closeMobileList(); openRoleDetail(el.dataset.role); });
      el.addEventListener('contextmenu', (e) => {
        const role = S.roles.find((r) => r.id === el.dataset.role);
        if (role) roleMenu(e, role);
      });
    });
    $$('[data-group]', body).forEach((el) => {
      el.addEventListener('click', () => { closeMobileList(); openGroupDetail(el.dataset.group); });
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

  function refreshWorkspaceView() {
    if (S.wsDrawerId) renderWorkspaceDrawer();
    else renderCanvas();
  }

  async function loadWsDir(wsId, path) {
    S.wsLoading = true; refreshWorkspaceView();
    try {
      S.wsFiles = await api(`/workspace/list?workspace_id=${encodeURIComponent(wsId)}&path=${encodeURIComponent(path || '.')}`);
    } catch (err) {
      S.wsFiles = []; toast(err.message, 'bad');
    } finally {
      S.wsLoading = false; refreshWorkspaceView();
    }
  }

  async function openWsFile(wsId, path) {
    try {
      const data = await api(`/workspace/file?workspace_id=${encodeURIComponent(wsId)}&path=${encodeURIComponent(path)}`);
      S.wsPreview = { path, text: data.text || '', size: data.size || 0, truncated: !!data.truncated };
      refreshWorkspaceView();
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
  function bindWorkActions(scope) {
    $$('[data-attn-conv]', scope).forEach((el) => el.addEventListener('click', () => {
      const id = el.dataset.attnConv;
      if (id) openConversation(id);
    }));
    $$('[data-work-select]', scope).forEach((el) => el.addEventListener('click', () => {
      S.workSelected = el.dataset.workSelect;
      renderList();
      renderCanvas();
    }));
    $$('[data-work-new]', scope).forEach((el) => el.addEventListener('click', newWorkItem));
    $$('[data-work-settings]', scope).forEach((el) => el.addEventListener('click', openWorkSettings));
    $$('[data-work-refresh]', scope).forEach((el) => el.addEventListener('click', () => loadWorkItems()));
    $$('[data-work-open]', scope).forEach((el) => el.addEventListener('click', () => {
      const item = workSelectedItem();
      if (item && item.conversation_id) openWorkConversation(item);
    }));
    $$('[data-work-dispatch]', scope).forEach((el) => el.addEventListener('click', () => {
      const item = workSelectedItem();
      if (item) dispatchWork(item);
    }));
    $$('[data-work-done]', scope).forEach((el) => el.addEventListener('click', () => {
      const item = workSelectedItem();
      if (item) patchWorkStatus(item, 'done');
    }));
    $$('[data-work-close]', scope).forEach((el) => el.addEventListener('click', () => {
      const item = workSelectedItem();
      if (item) patchWorkStatus(item, 'closed');
    }));
    $$('[data-work-reopen]', scope).forEach((el) => el.addEventListener('click', () => {
      const item = workSelectedItem();
      if (item) patchWorkStatus(item, 'inbox');
    }));
    $$('[data-work-edit]', scope).forEach((el) => el.addEventListener('click', () => {
      const item = workSelectedItem();
      if (item) editWorkItem(item);
    }));
    $$('[data-work-delete]', scope).forEach((el) => el.addEventListener('click', () => {
      const item = workSelectedItem();
      if (item) deleteWorkItem(item);
    }));
  }

  function workDetailHtml(item) {
    const badge = WORK_STATUS[item.status] || item.status;
    const waiting = item.status === 'needs_attention' && /等待/.test(item.attention_reason || '');
    const sourceRef = item.source_ref
      ? (/^https?:\/\//.test(item.source_ref)
        ? `<a href="${h(item.source_ref)}" target="_blank" rel="noopener">${h(item.source_ref)}</a>`
        : h(item.source_ref))
      : '';
    const statusIcon = item.status === 'needs_attention'
      ? '<path d="M12 4 3 20h18Z"/><path d="M12 10v4M12 17h.01"/>'
      : item.status === 'working'
        ? '<circle cx="12" cy="12" r="7"/><path d="M12 8v4l3 2"/>'
        : item.status === 'done'
          ? '<path d="m5 12 4 4L19 6"/>'
          : '<path d="M5 4h14v17H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>';
    return `<article class="work-detail work-detail-${h(item.status)}">
      <div class="sa-detail-head">
        <span class="sa-detail-orb ${h(item.status)}"><svg viewBox="0 0 24 24">${statusIcon}</svg></span>
        <div class="sa-detail-title">
          <div class="sa-eyebrow">工单 · ${h(item.source || 'manual')}</div>
          <h1>${h(item.title)}</h1>
        </div>
        <span class="work-badge ${h(item.status)}">${h(badge)}</span>
      </div>
      <div class="work-meta">
        <span><i>来源</i><b>${h(item.source || 'manual')}</b></span>
        ${sourceRef ? `<span><i>链接</i>${sourceRef}</span>` : ''}
        ${item.source_actor ? `<span><i>发起人</i><b>${h(item.source_actor)}</b></span>` : ''}
        <span><i>工作区</i><b>${h(item.workspace_name || item.workspace_id || '默认')}</b></span>
        <span><i>角色</i><b>${h([item.role_name || item.role_id || '默认', item.role_title, item.role_department].filter(Boolean).join(' · '))}</b></span>
        ${item.team_name ? `<span><i>群组</i><b>${h(item.team_name)}</b></span>` : ''}
        <span><i>创建</i><b>${h(item.created_at || '')}</b></span>
      </div>
      ${item.status === 'needs_attention' ? `<div class="work-attention">
        <span class="sa-attention-ico danger"><svg viewBox="0 0 24 24"><path d="M12 4 3 20h18Z"/><path d="M12 10v4M12 17h.01"/></svg></span>
        <span>需要你：${h(item.attention_reason || '处理这个工单')}</span>
        ${item.conversation_id ? '<button class="btn primary" data-work-open>打开会话处理</button>' : ''}
        ${(item.conversation_id && !waiting) ? '<button class="btn" data-work-dispatch>重新派活</button>' : ''}
      </div>` : ''}
      <div class="sa-detail-label">工单说明</div>
      <div class="work-body">${md(item.body || '（没有补充说明）')}</div>
      ${item.result_summary ? `<div class="sa-detail-label">结果</div><div class="work-result">${md(item.result_summary)}</div>` : ''}
      <div class="sa-detail-label">运行</div>
      <div class="work-run">
        ${item.run_id ? `<span><i>run</i><code>${h(item.run_id)}</code></span>` : ''}
        ${item.conversation_id ? `<span><i>会话</i><code>${h(item.conversation_id)}</code></span>` : ''}
        ${item.finished_at ? `<span><i>结束</i>${h(item.finished_at)}</span>` : ''}
        <span>只展示需要人的工单；其余在后台推进。</span>
      </div>
    </article>`;
  }

  function workActionButtonsHtml(item) {
    const waiting = item.status === 'needs_attention' && /等待/.test(item.attention_reason || '');
    const out = [];
    if (item.conversation_id) out.push(`<button class="btn${item.status === 'needs_attention' ? ' primary' : ''}" data-work-open>打开会话</button>`);
    if (['inbox', 'needs_attention'].includes(item.status) && !waiting) {
      out.push(`<button class="btn${item.conversation_id ? '' : ' primary'}" data-work-dispatch>${item.conversation_id ? '重新派活' : '派活'}</button>`);
    }
    if (!['done', 'closed'].includes(item.status)) {
      out.push('<button class="btn" data-work-done>标记完成</button>');
      out.push('<button class="btn danger" data-work-close>关闭</button>');
    } else {
      out.push('<button class="btn" data-work-reopen>重新打开</button>');
    }
    out.push('<button class="btn" data-work-edit>编辑</button>');
    out.push('<button class="btn danger" data-work-delete>删除</button>');
    return out.join('');
  }

  function renderWorkDrawer() {
    const item = S.workItems.find((w) => w.id === S.workDrawerId);
    const drawer = $('#sideDrawer');
    const body = $('#sideDrawerBody');
    const foot = $('#sideDrawerFoot');
    if (!drawer || !body || !foot) return;
    if (!item) { closeSideDrawer(); return; }
    const badge = WORK_STATUS[item.status] || item.status;
    $('#sideDrawerTitle').textContent = '工单';
    $('#sideDrawerSub').textContent = `${item.title} · ${badge}`;
    body.innerHTML = workDetailHtml(item);
    foot.innerHTML = `${workActionButtonsHtml(item)}<span class="spacer"></span><button class="btn" type="button" data-work-drawer-close>关闭</button>`;
    drawer.hidden = false;
    const scrim = $('#drawerScrim');
    if (scrim) scrim.hidden = false;
    bindWorkActions(body);
    bindWorkActions(foot);
    const close = $('[data-work-drawer-close]', foot);
    if (close) close.addEventListener('click', closeSideDrawer);
  }

  function openWorkDetail(itemId) {
    S.workSelected = itemId;
    S.workDrawerId = itemId;
    renderWorkDrawer();
  }

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
      const items = S.convs.filter((c) => String(c.zone || '') !== GLOBAL_AGENT_ZONE);
      crumb.innerHTML = `<span>会话</span><b>全部会话</b><span class="chip">${items.length} 个</span>`;
      actions.innerHTML = `<button class="btn primary" data-new="conv">新建会话</button>`;
      body.innerHTML = `<div class="hero"><div><h1>会话</h1>
        <p>点开一个会话直接在画布里聊天；右键可以重命名、绑定工作区或删除。</p></div></div>
        <div class="tile-grid">${items.map((c) => {
          const title = convTitle(c);
          const group = convIsGroup(c);
          const role = convParts(c).role;
          const running = !!c.active;
          const unread = !!c.unread;
          const meta = convMeta(c);
          return `<article class="tile-card conv-tile${unread ? ' unread' : ''}" data-conv-card="${h(c.id)}">
            <div class="tile-head">
              ${av(title || '会', group ? '' : 'round', convAvatar(c))}
              <div class="tile-title"><b>${h(title)}</b><span>${h(meta.kind)} · ${h(meta.sub)}</span></div>
              ${running ? '<span class="dot run" title="运行中"></span>' : (unread ? '<span class="dot" title="未读"></span>' : '')}
            </div>
            <p class="tile-preview">${h(c.last_message || '还没有消息')}</p>
            <div class="tile-chips">
              <span class="chip">${group ? '群聊' : '单聊'}</span>
              ${role && role.title ? `<span class="chip">${h(role.title)}</span>` : ''}
              ${role && role.department ? `<span class="chip">${h(role.department)}</span>` : ''}
            </div>
            <div class="tile-foot"><span>${h(shortTime(c.updated_at))}</span><span>${unread ? '未读' : '打开会话'}</span></div>
          </article>`;
        }).join('') || '<div class="empty">还没有会话<br>点右上角「新建会话」开始</div>'}</div>`;
      $$('[data-conv-card]', body).forEach((el) => {
        el.addEventListener('click', () => openConversation(el.dataset.convCard));
        el.addEventListener('contextmenu', (e) => {
          const conv = S.convs.find((c) => c.id === el.dataset.convCard);
          if (conv) convMenu(e, conv);
        });
      });
    } else if (S.section === 'work') {
      const policy = S.workPolicy || {};
      const mode = policy.mode || 'observe';
      const modeText = { off: '手动模式', observe: '观察模式', auto: '自动模式' }[mode] || '观察模式';
      const defaultWs = S.workspaces.find((w) => w.id === policy.default_workspace_id);
      const items = workItemsForFilter();
      const filters = WORK_FILTERS.map(([id, label]) => {
        const n = id === 'all' ? S.workItems.length
          : id === 'open' ? Number(S.workStats.open || 0)
          : Number(S.workStats[id] || 0);
        return `<button class="work-filter${S.workFilter === id ? ' on' : ''}" data-work-filter="${id}">${label}${n ? ` ${n}` : ''}</button>`;
      }).join('');
      crumb.innerHTML = `<span>工单</span><b>注意力与工单</b><span class="chip">${S.workStats.open || 0} 个待办</span>`;
      actions.innerHTML = `<button class="btn primary" data-work-new>新建工单</button><button class="btn" data-work-settings>策略</button>`;
      body.innerHTML = `<div class="sa-dashboard">
        <section class="sa-hero">
          <div class="sa-orb"><svg viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="12" rx="4"/><path d="M12 3v4M9 12h.01M15 12h.01M9 16h6"/></svg></div>
          <div class="sa-hero-main">
            <div class="sa-eyebrow">工单 · 主动执行层</div>
            <h1>工单</h1>
            <p>把工作拆成工单，在隔离的会话 / 工作区里推进；只在必须由你决定时才回来。你看到的是结果和需要你处理的事，不是一堆过程。</p>
            <div class="sa-pills">
              <span class="sa-pill"><i class="sa-dot ${mode === 'auto' ? 'on' : ''}"></i>${modeText}</span>
              <span class="sa-pill"><i class="sa-dot ${(S.workStats.working || 0) ? 'on' : ''}"></i>${S.workStats.working || 0} 个运行中</span>
              <span class="sa-pill${(S.workStats.attention || 0) ? ' danger' : ''}"><i class="sa-dot ${(S.workStats.attention || 0) ? 'danger' : ''}"></i>${S.workStats.attention || 0} 个需要你</span>
            </div>
          </div>
        </section>
        ${attentionInboxHtml()}
        <div class="work-stats sa-stats">
          <div class="work-stat sa-stat">
            <span class="sa-stat-ico"><svg viewBox="0 0 24 24"><path d="M5 4h14v17H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg></span>
            <div><b>${S.workStats.open || 0}</b><span>待办（含进行中）</span></div>
          </div>
          <div class="work-stat sa-stat">
            <span class="sa-stat-ico accent"><svg viewBox="0 0 24 24"><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="5"/></svg></span>
            <div><b>${S.workStats.working || 0}</b><span>正在执行</span></div>
          </div>
          <div class="work-stat attention sa-stat">
            <span class="sa-stat-ico danger"><svg viewBox="0 0 24 24"><path d="M12 4 3 20h18Z"/><path d="M12 10v4M12 17h.01"/></svg></span>
            <div><b>${S.workStats.attention || 0}</b><span>需要你</span></div>
          </div>
          <div class="work-stat sa-stat">
            <span class="sa-stat-ico ok"><svg viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg></span>
            <div><b>${S.workStats.done || 0}</b><span>已完成</span></div>
          </div>
        </div>
        <div class="sa-columns">
          <section class="sa-section">
            <div class="sa-section-head"><div><h2>全部工单</h2><p>点开工单查看详情或派活；按状态筛选。</p></div><span class="sa-count">${S.workStats.total || 0}</span></div>
            <div class="work-filters">${filters}</div>
            ${items.length ? `<div class="tile-grid work-grid">${items.map((w) => {
              const attention = w.status === 'needs_attention';
              return `<article class="tile-card work-tile${attention ? ' attention' : ''}" data-work-card="${h(w.id)}">
                <div class="tile-head">
                  <span class="work-status ${h(w.status)}"></span>
                  <div class="tile-title"><b>${h(w.title)}</b><span>${h(WORK_STATUS[w.status] || w.status)} · ${h(w.source || 'manual')}${w.source_ref ? ` · ${h(w.source_ref)}` : ''}</span></div>
                  ${w.priority ? '<span class="tag">高</span>' : ''}
                </div>
                <p class="tile-preview">${h(firstLine(w.body || w.attention_reason || '没有补充说明', 110))}</p>
                <div class="tile-foot"><span>${h(shortTime(w.updated_at))}</span><span>${h(WORK_STATUS[w.status] || w.status)}</span></div>
              </article>`;
            }).join('')}</div>` : `<div class="sa-onboard">
              <div class="sa-onboard-step"><b>1</b><span>新建一个工单，写清要做什么</span></div>
              <div class="sa-onboard-step"><b>2</b><span>派活后工单在隔离会话里执行</span></div>
              <div class="sa-onboard-step"><b>3</b><span>只有需要授权 / 回答 / 失败时才回来</span></div>
              <button class="btn primary" type="button" data-work-new>创建第一个工单</button>
            </div>`}
          </section>
          <section class="sa-section sa-policy-card">
            <div class="sa-section-head"><div><h2>工作方式</h2><p>决定工单何时自动推进。</p></div></div>
            <div class="sa-policy-row"><span>模式</span><b>${modeText}</b></div>
            <div class="sa-policy-row"><span>新会话权限</span><b>${h({ auto: '自动判断', once: '每次询问', session: '会话允许', always: '完全允许' }[policy.permission_mode] || '自动判断')}</b></div>
            <div class="sa-policy-row"><span>默认工作区</span><b>${h((defaultWs && defaultWs.name) || policy.default_workspace_id || '默认')}</b></div>
            <p class="sa-policy-note">会话、角色、群组、工作区都可以「新建工单」；工单完成后结果会回写到这里。</p>
            <button class="btn" type="button" data-work-settings>调整策略</button>
          </section>
        </div>
      </div>`;
      $$('[data-work-filter]', body).forEach((el) => el.addEventListener('click', () => {
        S.workFilter = el.dataset.workFilter;
        renderCanvas();
      }));
      $$('[data-work-card]', body).forEach((el) => el.addEventListener('click', () => openWorkDetail(el.dataset.workCard)));
      bindWorkActions(actions);
      bindWorkActions(body);
    } else if (S.section === 'ws') {
      const items = S.workspaces.filter((w) => !w.archived);
      crumb.innerHTML = `<span>工作区</span><b>全部工作区</b><span class="chip">${items.length} 个</span>`;
      actions.innerHTML = `<button class="btn primary" data-add-workspace>＋ 添加工作区</button>`;
      body.innerHTML = `<div class="hero"><div><h1>工作区</h1>
        <p>工作区只提供文件与终端上下文。点开一个块查看目录、文件预览和绑定操作。</p></div></div>
        <div class="tile-grid">${items.map((w) => `<article class="tile-card ws-tile" data-ws-card="${h(w.id)}">
          <div class="tile-head">
            <span class="ws-tile-icon"><svg viewBox="0 0 24 24"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z"/></svg></span>
            <div class="tile-title"><b>${h(w.name)}</b><span>${h(w.path || w.root_path || '托管目录')}</span></div>
          </div>
          <div class="tile-chips">
            <span class="chip">${Number(w.conversation_count || 0)} 个会话</span>
            ${w.last_conv_updated ? `<span class="chip">最近 ${h(shortTime(w.last_conv_updated))}</span>` : '<span class="chip">还没有会话</span>'}
          </div>
          <div class="tile-foot"><span>点击查看目录与文件</span><span>${w.archived ? '已归档' : '打开详情'}</span></div>
        </article>`).join('') || `<div class="empty">还没有工作区<br>点右上角「＋ 添加工作区」选择一个文件夹</div>`}</div>`;
      $$('[data-ws-card]', body).forEach((el) => {
        el.addEventListener('click', () => openWorkspaceDetail(el.dataset.wsCard));
        el.addEventListener('contextmenu', (e) => {
          const ws = S.workspaces.find((w) => w.id === el.dataset.wsCard);
          if (ws) workspaceMenu(e, ws);
        });
      });
      const add = $('[data-add-workspace]', body);
      if (add) add.addEventListener('click', () => addWorkspace());
      const add2 = $('[data-add-workspace]', actions);
      if (add2) add2.addEventListener('click', () => addWorkspace());
    } else if (S.section === 'role') {
      const selected = S.roleDept === undefined ? 'all' : S.roleDept;
      const selectedDept = selected && selected !== 'all' ? deptById(selected) : null;
      const visible = S.roles.filter((r) => roleDeptMatches(r, selected));
      crumb.innerHTML = `<span>组织与角色</span><b>${h(selected === 'all' ? '全部角色' : (selectedDept ? deptPath(selectedDept.id) : '未安排'))}</b>`;
      actions.innerHTML = `<button class="btn" data-new="dept">＋ 部门</button><button class="btn primary" data-new="role">＋ 新建角色</button>`;
      const deptNode = (d, depth) => {
        const kids = deptChildren(d.id);
        const row = `<div class="dept-row${selected === d.id ? ' active' : ''}" style="--depth:${depth}">
          <button class="dept-main" type="button" data-dept-select="${h(d.id)}" title="${h(deptPath(d.id))}">
            ${av(d.name, 'xs round', d.avatar)}<span class="dept-name">${h(d.name)}</span><span class="dept-count">${deptRoleCount(d.id)}</span>
          </button>
          <button class="dept-more" type="button" data-dept-menu="${h(d.id)}" title="部门操作" aria-label="部门操作">⋯</button>
        </div>`;
        return row + kids.map((c) => deptNode(c, depth + 1)).join('');
      };
      const rootNodes = deptChildren('').map((d) => deptNode(d, 0)).join('');
      const headTitle = selected === 'all' ? '全部角色'
        : (selectedDept ? deptPath(selectedDept.id) : '未安排部门');
      const headHint = selected === 'all'
        ? '按部门管理角色；角色也可以不安排部门，归入「未安排」。'
        : (selectedDept
          ? `包含「${selectedDept.name}」及其子部门的角色；可在此新建角色或子部门。`
          : '这些角色还没有安排部门，编辑角色即可归档到部门。');
      const cards = visible.map((r) => `<article class="role-card" data-role-card="${h(r.id)}">
        <div class="role-top">${av(r.name || '角', 'round', r.avatar)}<span style="min-width:0"><b>${h(r.name)}</b><br><span class="role-line">${h(r.title || r.expertise || '未填写岗位')}</span></span>
          <span class="tag ${r.scope === 'temporary' ? 'tmp' : ''}" style="margin-left:auto">${r.scope === 'temporary' ? '临时' : (r.source === 'llm' ? 'LLM' : '持久')}</span></div>
        <div class="role-line">${r.department ? `部门：${h(r.department)}` : '<span class="role-warn">未安排部门</span>'}</div>
        <div class="role-line">${r.model_code ? `模型：${h(r.model_code)}` : '模型：未指定'}</div>
        <div class="chips">${(r.tools || []).slice(0, 6).map((x) => `<span class="chip">${h(x)}</span>`).join('') || '<span class="chip">无工具</span>'}</div>
      </article>`).join('') || `<div class="empty">${selected === 'all' ? '还没有角色' : '这个范围还没有角色'}<br>${selectedDept ? '点右上角「＋ 新建角色」归档到这里' : '编辑角色即可安排部门'}</div>`;
      body.innerHTML = `<div class="hero"><div><h1>组织与角色</h1>
        <p>部门支持多级；角色可以安排在部门里，也可以保持「未安排」。角色名称是「谁」，岗位是「做什么」，两者分开维护。</p></div></div>
        <div class="org-layout">
          <aside class="org-tree">
            <div class="org-tree-head"><b>组织</b><button class="mini-link" type="button" data-new="dept">＋ 根部门</button></div>
            <button class="dept-row dept-flat${selected === 'all' ? ' active' : ''}" type="button" data-dept-select="all">
              <span class="dept-name">全部角色</span><span class="dept-count">${S.roles.length}</span>
            </button>
            <button class="dept-row dept-flat${selected === '' ? ' active' : ''}" type="button" data-dept-select="">
              <span class="dept-name">未安排</span><span class="dept-count">${S.roles.filter((r) => !r.department_id).length}</span>
            </button>
            <div class="org-tree-sep"></div>
            ${rootNodes || '<div class="org-tree-empty">还没有部门<br>用「＋ 根部门」开始</div>'}
          </aside>
          <section class="org-main">
            <div class="org-main-head">
              <div><h2>${h(headTitle)}</h2><p>${h(headHint)}</p></div>
              <span class="org-count">${visible.length} 个角色</span>
            </div>
            ${selectedDept ? `<div class="org-main-actions">
              <button class="btn small" type="button" data-dept-child="${h(selectedDept.id)}">＋ 子部门</button>
              <button class="btn small" type="button" data-dept-add-role="${h(selectedDept.id)}">＋ 在此部门新建角色</button>
            </div>` : ''}
            <div class="role-grid">${cards}</div>
          </section>
        </div>`;
      $$('[data-dept-select]', body).forEach((el) => el.addEventListener('click', () => {
        S.roleDept = el.dataset.deptSelect;
        renderList();
        renderCanvas();
      }));
      $$('[data-dept-menu]', body).forEach((el) => el.addEventListener('click', (e) => {
        e.stopPropagation();
        const dept = deptById(el.dataset.deptMenu);
        if (dept) departmentMenu(e, dept);
      }));
      $$('[data-dept-child]', body).forEach((el) => el.addEventListener('click', () => {
        openDeptEditor(null, el.dataset.deptChild);
      }));
      $$('[data-dept-add-role]', body).forEach((el) => el.addEventListener('click', () => {
        openRoleEditor(null, { department_id: el.dataset.deptAddRole });
      }));
      $$('[data-role-card]', body).forEach((el) => {
        el.addEventListener('click', () => openRoleDetail(el.dataset.roleCard));
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
        if (team) {
          card.addEventListener('contextmenu', (e) => groupMenu(e, team));
          card.addEventListener('click', (e) => {
            if (e.target.closest('button, a')) return;
            openGroupDetail(team.id);
          });
        }
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
    if (kind === 'dept') return openDeptEditor(null);
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
        if (existing) {
          // 角色固定了模型、而旧单聊还没有自己的模型时，补成角色模型；
          // 会话里显式选过别的模型则保持不变。
          if (role.model_code && !existing.model_code) {
            try {
              await api(`/conversations/${encodeURIComponent(existing.id)}/preferences`, {
                method: 'PUT', body: JSON.stringify({ model_code: role.model_code }),
              });
              existing.model_code = role.model_code;
            } catch { /* ignore */ }
          }
          return openConversation(existing.id);
        }
      }
      const conv = await api('/conversations', {
        method: 'POST', body: JSON.stringify({
          role_id: roleId, model_code: role.model_code || undefined,
        }),
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
    setSection('ws');
    openWorkspaceDetail(ws.id);
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
      if (S.wsDrawerId === ws.id) renderWorkspaceDrawer();
      toast('已重命名', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deleteWorkspace(ws) {
    if (!ws) return;
    if (!confirm(`删除工作区「${ws.name}」？目录、会话、文件都会保留，只是从列表隐藏。`)) return;
    try {
      await api(`/workspaces/${encodeURIComponent(ws.id)}`, { method: 'DELETE' });
      if (S.selectedWs === ws.id) { S.selectedWs = null; S.wsFiles = null; S.wsPreview = null; }
      if (S.wsDrawerId === ws.id) closeSideDrawer();
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
      await loadAll(); S.settings = null;
      if (S.wsDrawerId === ws.id) renderWorkspaceDrawer();
      toast('已更新', 'ok');
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
      { label: '新建工单…', action: () => newWorkItem({
        role_id: role.id, body: `来自角色「${role.name}」\n\n`,
      }) },
      { sep: true },
      { label: '复制角色', action: () => duplicateRole(role) },
      ...(role.scope === 'temporary' ? [{ label: '存为持久角色', action: async () => {
        await api(`/roles/${encodeURIComponent(role.id)}/promote`, { method: 'POST' });
        await loadAll(); toast('已存为持久角色', 'ok');
      } }] : []),
      { label: '加入群组…', action: () => addRoleToGroup(role) },
      { sep: true },
      { label: '复制角色 ID', action: () => copyText(role.id) },
      ...(role.system ? [] : [{ label: '删除角色', danger: true, action: async () => {
        if (!confirm(`删除角色「${role.name}」？`)) return;
        try { await api(`/roles/${encodeURIComponent(role.id)}`, { method: 'DELETE' }); await loadAll(); toast('角色已删除', 'ok'); }
        catch (err) { toast(err.message, 'bad'); }
      } }]),
    ]);
  }

  function departmentMenu(e, dept) {
    openCtx(e, [
      { label: '在此部门新建角色…', action: () => openRoleEditor(null, { department_id: dept.id }) },
      { label: '新建子部门…', action: () => openDeptEditor(null, dept.id) },
      { label: '重命名 / 移动…', action: () => openDeptEditor(dept) },
      { sep: true },
      { label: '复制部门 ID', action: () => copyText(dept.id) },
      { label: '删除部门', danger: true, action: () => deleteDepartment(dept) },
    ]);
  }

  function groupMenu(e, team) {
    openCtx(e, [
      { label: '打开群聊', action: () => openGroupChat(team.id) },
      { label: '编辑群组…', action: () => openGroupDetail(team.id) },
      { label: '新建工单…', action: () => newWorkItem({
        team_id: team.id, body: `来自群组「${team.name}」\n\n`,
      }) },
      { sep: true },
      { label: '复制群组', action: () => duplicateTeam(team) },
      { label: '复制群组 ID', action: () => copyText(team.id) },
      { label: '删除群组', danger: true, action: () => deleteTeam(team) },
    ]);
  }

  function workspaceMenu(e, ws) {
    openCtx(e, [
      { label: '查看详情', action: () => {
        setSection('ws');
        openWorkspaceDetail(ws.id);
      } },
      { label: '绑定到当前窗口', action: async () => {
        const win = S.activeWin;
        if (!win) { toast('先打开一个会话窗口', 'bad'); return; }
        await bindWorkspaceTo(win, ws.id);
      } },
      { label: '新建工单…', action: () => newWorkItem({
        workspace_id: ws.id, body: `来自工作区「${ws.name}」\n\n`,
      }) },
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
    // 工单不提供「角色资料」入口：它绑定的是默认角色，打开后会看到不可删除的默认角色抽屉。
    const infoItem = { label: convParts(w.conv).isGroup ? '群聊设置' : '角色资料', action: () => openConversationInfo(w) };
    return [
      ...(isGlobal ? [] : [infoItem]),
      { label: '执行画布…', action: () => openAgentCanvas(w) },
      { label: '新建工单…', action: () => newWorkItem({
        workspace_id: w.conv.bound_workspace_id || w.conv.workspace_id || null,
        role_id: w.conv.role_id || null,
        team_id: w.conv.team_id || null,
        body: `来自会话「${convTitle(w.conv)}」\n\n`,
      }) },
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
          toast(S.gaAuto ? '工单将代处理低风险授权' : '已关闭低风险代处理', 'ok', 1600);
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

  // 会话信息不再单独弹框：群聊直接打开群设置抽屉，单聊直接打开角色资料。
  function openConversationInfo(w) {
    if (!w) return;
    // 工单不打开角色资料（按钮已隐藏，这里兜底）。
    if (String(w.conv.zone || '') === GLOBAL_AGENT_ZONE) return;
    const parts = convParts(w.conv);
    if (parts.isGroup && parts.team) return openGroupDetail(parts.team.id);
    if (parts.role) return openRoleDetail(parts.role.id);
    toast('这个会话没有可编辑的资料', 'ok');
  }

  /* ── 执行画布：主代理 / 团队 / 子代理 DAG ─────────────── */
  const AGENT_STATUS = {
    running: '运行中', queued: '排队中', done: '完成',
    failed: '失败', idle: '待命',
  };

  function agentTokens(n) {
    const value = Number(n) || 0;
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
    return String(value);
  }

  function agentNodeHtml(node, x, y) {
    const status = String(node.status || 'idle');
    const tools = node.tools || [];
    const root = node.kind === 'root';
    const progress = status === 'done' || status === 'failed' ? 100
      : status === 'running' ? 64 : status === 'queued' ? 12 : 0;
    const metrics = [];
    if (root) {
      metrics.push(`${agentTokens((S.agentCanvasData.stats || {}).tokens)} token`);
      metrics.push(`${tools.length} 工具`);
    } else {
      metrics.push(`${tools.length} 工具`);
      if (node.kind === 'team' && status === 'idle') metrics.push('待委派');
      else if (node.elapsed_ms) metrics.push(durationText(node.elapsed_ms));
    }
    const current = node.current || firstLine(node.text || node.goal || '', 64);
    const selected = S.agentCanvasSelected === node.id ? ' selected' : '';
    return `<article class="agent-node ${status}${selected}" data-agent-node="${h(node.id)}" style="left:${x}px;top:${y}px">
      <div class="agent-node-head">
        ${av(node.name || 'A', 'round')}
        <div class="agent-node-name"><b>${h(node.name)}</b><span>${root ? '主代理' : node.kind === 'team' ? '团队成员' : '子代理'}</span></div>
        <span class="agent-status ${status}">${h(AGENT_STATUS[status] || status)}</span>
      </div>
      <div class="agent-goal">${h(firstLine(node.goal || '', 74))}</div>
      <div class="agent-progress ${status === 'running' ? 'indeterminate' : ''}"><i style="width:${progress}%"></i></div>
      <div class="agent-metrics">${metrics.map((m) => `<span>${h(m)}</span>`).join('')}</div>
      <div class="agent-current" title="${h(current)}">${h(current || '—')}</div>
    </article>`;
  }

  function agentFeedRow(item) {
    const status = String(item.status || '');
    const kind = String(item.kind || '');
    const label = kind === 'tool_call' ? '工具' : kind === 'tool_result' ? '完成'
      : kind === 'error' ? '失败' : kind === 'done' ? '完成' : '进展';
    const when = item.ts ? new Date(Number(item.ts)).toLocaleTimeString('zh-CN', { hour12: false }) : '';
    return `<div class="agent-feed-row ${h(status)}" data-agent-feed="${h(item.agent_id || '')}">
      <span class="agent-dot ${h(status)}"></span>
      <div class="agent-feed-main"><b>${h(item.agent || '代理')}</b><span>${h(firstLine(item.text || label, 90))}</span></div>
      <span class="agent-feed-meta">${h(label)}${when ? ` · ${h(when)}` : ''}</span>
    </div>`;
  }

  function renderAgentCanvas() {
    const data = S.agentCanvasData;
    if (!data || !data.root) return;
    const filter = S.agentCanvasFilter;
    const allNodes = data.nodes || [];
    const visible = allNodes.filter((n) => {
      if (filter === 'all') return true;
      if (filter === 'running') return n.status === 'running' || n.status === 'queued';
      if (filter === 'failed') return n.status === 'failed';
      return true;
    });
    const stage = $('#agentCanvasStage');
    if (!stage) return;
    const cols = Math.min(4, Math.max(1, visible.length));
    const rows = Math.max(1, Math.ceil(visible.length / cols));
    const nodeW = 260;
    const gapX = 24;
    const gapY = 30;
    const width = Math.max(920, cols * nodeW + (cols - 1) * gapX + 80);
    const height = 250 + rows * 190 + 40;
    stage.style.width = `${width}px`;
    stage.style.height = `${height}px`;
    stage.style.transform = `scale(${S.agentCanvasZoom})`;
    const rootW = 260;
    const rootX = (width - rootW) / 2;
    const rootY = 26;
    let svg = `<svg class="agent-edges" width="${width}" height="${height}">`;
    let cards = '';
    visible.forEach((node, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const x = (width - (cols * nodeW + (cols - 1) * gapX)) / 2 + col * (nodeW + gapX);
      const y = 236 + row * (190 + gapY);
      const x1 = rootX + rootW / 2;
      const y1 = rootY + 176;
      const x2 = x + nodeW / 2;
      const y2 = y;
      const mid = (y1 + y2) / 2;
      const cls = node.status === 'running' || node.status === 'queued' ? 'active'
        : node.status === 'failed' ? 'failed' : '';
      svg += `<path class="agent-edge ${cls}" d="M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}"/>`;
      cards += agentNodeHtml(node, x, y);
    });
    svg += '</svg>';
    stage.innerHTML = `${svg}${agentNodeHtml(data.root, rootX, rootY)}${cards}`;
    $$('[data-agent-node]', stage).forEach((el) => el.addEventListener('click', () => {
      const id = el.dataset.agentNode;
      S.agentCanvasSelected = S.agentCanvasSelected === id ? null : id;
      renderAgentCanvas();
    }));

    const stats = data.stats || {};
    const run = data.run || {};
    $('#agentCanvasSub').textContent = `${(data.conversation || {}).title || '当前会话'} · ${AGENT_STATUS[run.status] || run.status || '空闲'} · ${stats.agents || 1} 个代理 · ${agentTokens(stats.tokens)} token`;
    $$('#agentCanvasFilter [data-graph-filter]').forEach((btn) => {
      btn.classList.toggle('on', btn.dataset.graphFilter === filter);
    });

    const selected = S.agentCanvasSelected;
    let activity = data.activity || [];
    if (!S.agentCanvasReplay && selected) activity = activity.filter((x) => x.agent_id === selected);
    const recent = activity.slice().sort((a, b) => (Number(b.ts) || 0) - (Number(a.ts) || 0)).slice(0, 40);
    const activityEl = $('#agentActivity');
    if (activityEl) activityEl.innerHTML = recent.length ? recent.map(agentFeedRow).join('') : '<div class="agent-feed-empty">还没有活动</div>';
    const just = (data.activity || []).slice(-5).reverse();
    const justEl = $('#agentJustHappened');
    if (justEl) justEl.innerHTML = just.length ? just.map(agentFeedRow).join('') : '<div class="agent-feed-empty">暂无</div>';
    const up = data.upcoming || [];
    const upEl = $('#agentUpcoming');
    if (upEl) upEl.innerHTML = up.length ? up.map((r) => `<div class="agent-feed-row queued">
      <span class="agent-dot queued"></span><div class="agent-feed-main"><b>排队中的运行</b><span>${h(firstLine(r.content || '等待执行', 90))}</span></div>
      <span class="agent-feed-meta">${h((r.created_at || '').slice(11, 16))}</span></div>`).join('') : '<div class="agent-feed-empty">没有待执行的运行</div>';
  }

  async function refreshAgentCanvas() {
    if (!S.agentCanvasConv) return;
    try {
      const data = await api(`/conversations/${encodeURIComponent(S.agentCanvasConv)}/graph`);
      S.agentCanvasData = data;
      renderAgentCanvas();
    } catch (err) {
      const sub = $('#agentCanvasSub');
      if (sub) sub.textContent = `画布加载失败：${err.message}`;
    }
  }

  function openAgentCanvas(w) {
    if (!w) return;
    S.agentCanvasConv = w.convId;
    S.agentCanvasData = null;
    S.agentCanvasFilter = 'all';
    S.agentCanvasSelected = null;
    S.agentCanvasZoom = 1;
    S.agentCanvasReplay = false;
    const overlay = $('#agentCanvas');
    if (overlay) overlay.hidden = false;
    $('#agentCanvasSub').textContent = `${convTitle(w.conv)} · 加载中…`;
    refreshAgentCanvas();
    if (S.agentCanvasTimer) clearInterval(S.agentCanvasTimer);
    S.agentCanvasTimer = setInterval(() => {
      if (!$('#agentCanvas') || $('#agentCanvas').hidden) return;
      refreshAgentCanvas();
    }, 2000);
  }

  function closeAgentCanvas() {
    const overlay = $('#agentCanvas');
    if (overlay) overlay.hidden = true;
    if (S.agentCanvasTimer) { clearInterval(S.agentCanvasTimer); S.agentCanvasTimer = null; }
    if (S.agentCanvasReplayTimer) { clearInterval(S.agentCanvasReplayTimer); S.agentCanvasReplayTimer = null; }
    S.agentCanvasReplay = false;
    S.agentCanvasConv = null;
    S.agentCanvasData = null;
  }

  function replayAgentCanvas() {
    const data = S.agentCanvasData;
    if (!data) return;
    if (S.agentCanvasReplayTimer) { clearInterval(S.agentCanvasReplayTimer); S.agentCanvasReplayTimer = null; }
    const items = (data.activity || []).slice().sort((a, b) => (Number(a.ts) || 0) - (Number(b.ts) || 0));
    if (!items.length) return;
    S.agentCanvasReplay = true;
    let idx = 0;
    S.agentCanvasReplayTimer = setInterval(() => {
      if (idx >= items.length) {
        clearInterval(S.agentCanvasReplayTimer);
        S.agentCanvasReplayTimer = null;
        S.agentCanvasReplay = false;
        S.agentCanvasSelected = null;
        renderAgentCanvas();
        return;
      }
      S.agentCanvasSelected = items[idx].agent_id || null;
      renderAgentCanvas();
      idx += 1;
    }, 650);
  }

  function bindAgentCanvas() {
    const overlay = $('#agentCanvas');
    if (!overlay) return;
    const close = $('#agentCanvasClose');
    if (close) close.addEventListener('click', closeAgentCanvas);
    const refresh = $('#agentCanvasRefresh');
    if (refresh) refresh.addEventListener('click', refreshAgentCanvas);
    const replay = $('#agentCanvasReplay');
    if (replay) replay.addEventListener('click', replayAgentCanvas);
    const zin = $('#agentCanvasZoomIn');
    if (zin) zin.addEventListener('click', () => {
      S.agentCanvasZoom = Math.min(1.6, S.agentCanvasZoom + 0.1);
      renderAgentCanvas();
    });
    const zout = $('#agentCanvasZoomOut');
    if (zout) zout.addEventListener('click', () => {
      S.agentCanvasZoom = Math.max(0.6, S.agentCanvasZoom - 0.1);
      renderAgentCanvas();
    });
    const fit = $('#agentCanvasFit');
    if (fit) fit.addEventListener('click', () => {
      S.agentCanvasZoom = 1;
      renderAgentCanvas();
    });
    $$('#agentCanvasFilter [data-graph-filter]').forEach((btn) => btn.addEventListener('click', () => {
      S.agentCanvasFilter = btn.dataset.graphFilter;
      renderAgentCanvas();
    }));
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
          ${roles.map((r) => `<option value="${h(r.id)}">${h(roleLabel(r))}${r.expertise ? ` · ${h(r.expertise)}` : ''}</option>`).join('')}</select></div>
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
      applySectionLayout('conv');
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
      const leaderModel = team.leader && team.leader.model_code ? team.leader.model_code : '';
      const existing = S.convs.find((c) => c.team_id === teamId && convIsGroup(c));
      if (existing) {
        if (leaderModel && !existing.model_code) {
          try {
            await api(`/conversations/${encodeURIComponent(existing.id)}/preferences`, {
              method: 'PUT', body: JSON.stringify({ model_code: leaderModel }),
            });
            existing.model_code = leaderModel;
          } catch { /* ignore */ }
        }
        return openConversation(existing.id);
      }
      const conv = await api('/conversations', {
        method: 'POST', body: JSON.stringify({ team_id: teamId, model_code: leaderModel || undefined }),
      });
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
        ${isGlobal ? gaAvatar(isGroup ? '' : 'round') : av(title || '会', isGroup ? '' : 'round', convAvatar(conv))}
        <span class="win-title"><b>${h(title)}</b><span>${h(meta.kind)} · ${h(meta.sub)}</span></span>
        <button class="chip" data-bind-ws title="绑定工作区">${h(boundLabel(conv))}</button>
        <div class="head-menus">
          <span class="run-state" data-run-state hidden><span class="pulse"></span><span>运行中</span></span>
          ${isGlobal ? `<button class="head-btn" data-ga-notify title="系统通知" aria-label="系统通知">
            <svg viewBox="0 0 24 24"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>
          </button>` : ''}
          <button class="head-btn" data-conv-graph title="执行画布" aria-label="执行画布">
            <svg viewBox="0 0 24 24"><circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="7" r="2.2"/><circle cx="12" cy="18" r="2.2"/><path d="M8.2 6.2 15.8 7M7.2 8l3.6 8M16.8 9l-3.6 7"/></svg>
          </button>
          ${isGlobal ? '' : `<button class="head-btn" data-conv-info title="${isGroup ? '群聊设置' : '角色资料'}" aria-label="${isGroup ? '群聊设置' : '角色资料'}">
            <svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="2.6"/><path d="M3.6 19c0-2.6 2.4-4.4 5.4-4.4s5.4 1.8 5.4 4.4"/><circle cx="17" cy="9" r="2.2"/><path d="M15.8 14.8c2.4.3 4.2 2 4.2 4.2"/></svg>
          </button>`}
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
        <div class="mention-pop member-pop" hidden></div>
        <div class="box"><button class="wbtn attach" title="添加附件"><svg viewBox="0 0 24 24"><path d="m21 11-8.5 8.5a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L16 6"/></svg></button>
          <input type="file" class="file-input" hidden multiple />
          <textarea rows="1" placeholder="${isGroup ? '输入 @ 选择群成员 · / 命令 · Enter 发送' : '发消息…  / 命令 · Enter 发送'}" aria-label="消息"></textarea>
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
      title, kind: meta.kind, isGlobal, floating, isGroup, live: null,
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

  function attExt(name) {
    const match = /\.([A-Za-z0-9]+)$/.exec(String(name || ''));
    return match ? match[1].toLowerCase() : '';
  }

  function attLabel(att) {
    const ext = attExt(att.name);
    const kind = String(att.kind || '');
    if (kind === 'image' || String(att.mime || '').startsWith('image/')) return '图片';
    if (ext === 'pdf' || kind === 'pdf') return 'PDF';
    if (['doc', 'docx', 'odt', 'rtf'].includes(ext)) return 'Word';
    if (['ppt', 'pptx', 'odp'].includes(ext)) return 'PPT';
    if (['xls', 'xlsx', 'xlsm', 'ods', 'csv', 'tsv'].includes(ext)) return '表格';
    if (['md', 'markdown', 'mdx'].includes(ext)) return 'Markdown';
    if (['html', 'htm', 'xhtml'].includes(ext)) return 'HTML';
    if (['json', 'jsonl'].includes(ext)) return 'JSON';
    return kind === 'office' ? '文档' : '文本';
  }

  function attachmentsHtml(list) {
    if (!Array.isArray(list) || !list.length) return '';
    return `<div class="msg-attachments">${list.map((a) => {
      const name = h(a.name || '附件');
      const url = h(a.url || '');
      const img = String(a.mime || '').startsWith('image/') && url;
      if (img) return `<a class="att-img" href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="${name}" /></a>`;
      const ext = attExt(a.name);
      if (a.local || !url) {
        return `<span class="att-file" title="${name}">
          <svg viewBox="0 0 24 24"><path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7Z"/><path d="M14 3v4h4"/></svg>
          <b>${name}</b><span>${bytes(a.size)}</span></span>`;
      }
      return `<div class="att-card" data-att-id="${h(a.id || '')}" data-att-url="${url}"
        data-att-name="${name}" data-att-ext="${h(ext)}" data-att-kind="${h(a.kind || '')}">
        <button class="att-card-head" type="button" data-att-toggle>
          <span class="att-card-ico"><svg viewBox="0 0 24 24"><path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7Z"/><path d="M14 3v4h4"/></svg></span>
          <span class="att-card-title"><b>${name}</b><span>${h(attLabel(a))} · ${bytes(a.size)}</span></span>
          <span class="att-card-action">预览</span>
          <span class="att-card-caret">›</span>
        </button>
        <div class="att-card-body" hidden></div>
      </div>`;
    }).join('')}</div>`;
  }

  function attFooter(url, truncated) {
    return `<div class="att-foot"><a href="${h(url)}" target="_blank" rel="noopener">打开原文件</a>${truncated ? '<span>· 预览已截断</span>' : ''}</div>`;
  }

  function attCsvTable(text, delimiter) {
    const lines = String(text || '').split(/\r?\n/).filter((line) => line.length).slice(0, 200);
    if (!lines.length) return '<div class="att-loading">没有可预览的表格内容</div>';
    const rows = lines.map((line) => line.split(delimiter || ',').slice(0, 20));
    return `<div class="att-table-wrap"><table class="att-table"><tbody>${rows.map((row, i) =>
      `<tr>${row.map((cell) => (i === 0 ? `<th>${h(cell)}</th>` : `<td>${h(cell)}</td>`)).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  function renderAttPreview(card, host, data) {
    const ext = card.dataset.attExt || '';
    const text = String(data.text || '');
    const url = data.url || card.dataset.attUrl || '';
    const footer = attFooter(url, data.truncated);
    if (ext === 'pdf' || data.kind === 'pdf') {
      host.innerHTML = `<iframe class="att-pdf" src="${h(url)}" title="${h(data.name || 'PDF')}"></iframe>
        ${text ? `<details class="att-pdf-text"><summary>文本内容</summary><pre class="att-pre">${h(text)}</pre></details>` : ''}${footer}`;
      return;
    }
    if (['html', 'htm', 'xhtml'].includes(ext)) {
      host.innerHTML = `<iframe class="att-html" sandbox="" title="${h(data.name || 'HTML')}"></iframe>${footer}`;
      const frame = host.querySelector('iframe');
      if (frame) frame.srcdoc = text;
      return;
    }
    if (['md', 'markdown', 'mdx'].includes(ext)) {
      host.innerHTML = `<div class="att-md">${md(text)}</div>${footer}`;
      return;
    }
    if (['csv', 'tsv'].includes(ext)) {
      host.innerHTML = `${attCsvTable(text, ext === 'tsv' ? '\t' : ',')}${footer}`;
      return;
    }
    if (['xls', 'xlsx', 'xlsm', 'ods'].includes(ext)) {
      // document_service 的表格抽取格式是「单元格 | 单元格」。
      host.innerHTML = `${attCsvTable(text, ' | ')}${footer}`;
      return;
    }
    if (['json', 'jsonl'].includes(ext)) {
      let pretty = text;
      try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep raw */ }
      host.innerHTML = `<pre class="att-pre">${h(pretty)}</pre>${footer}`;
      return;
    }
    host.innerHTML = `<pre class="att-pre att-doc">${h(text || '没有可预览的文本，请打开原文件。')}</pre>${footer}`;
  }

  async function loadAttPreview(card) {
    const host = $('.att-card-body', card);
    if (!host) return;
    host.innerHTML = '<div class="att-loading"><span class="sp"></span>加载预览…</div>';
    try {
      const res = await fetch(`${card.dataset.attUrl}/preview`, { headers: { Accept: 'application/json' } });
      let payload = null;
      try { payload = await res.json(); } catch { /* ignore */ }
      if (!res.ok) throw new Error((payload && payload.detail) || `HTTP ${res.status}`);
      renderAttPreview(card, host, (payload && payload.data) || {});
    } catch (err) {
      host.innerHTML = `<div class="att-loading">预览失败：${h(err.message)}</div>${attFooter(card.dataset.attUrl || '', false)}`;
    }
  }

  function toggleAttPreview(card) {
    const host = $('.att-card-body', card);
    if (!host) return;
    const open = !host.hidden;
    host.hidden = open;
    card.classList.toggle('open', !open);
    const action = $('.att-card-action', card);
    if (action) action.textContent = open ? '预览' : '收起';
    if (!open && card.dataset.loaded !== '1') {
      card.dataset.loaded = '1';
      loadAttPreview(card);
    }
  }

  function isImageFile(file) {
    return String(file.type || '').startsWith('image/') || IMAGE_EXT_RE.test(file.name || '');
  }

  function isDocumentFile(file) {
    const name = String(file.name || '').toLowerCase();
    const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
    if (DOC_EXTENSIONS.includes(ext)) return true;
    const base = name.split('/').pop();
    if (['dockerfile', 'makefile', 'rakefile', 'gemfile', 'procfile'].includes(base)) return true;
    return String(file.type || '').startsWith('text/');
  }

  function attachmentKind(name, mime) {
    if (String(mime || '').startsWith('image/') || IMAGE_EXT_RE.test(name || '')) return 'image';
    const ext = `.${attExt(name)}`;
    if (ext === '.pdf') return 'pdf';
    if (['.doc', '.docx', '.xls', '.xlsx', '.xlsm', '.ppt', '.pptx', '.odt', '.ods'].includes(ext)) return 'office';
    return 'file';
  }

  function modelProfile(code) {
    const key = code || S.activeModel || '';
    return (S.models || []).find((m) => (m.code || m.id) === key) || null;
  }

  // 图片是否随消息发送，由当前会话实际使用的模型决定（后端会再校验一次）。
  function supportsVision(w) {
    if (w && w.conv && typeof w.conv.vision === 'boolean') return w.conv.vision;
    const role = (S.roles || []).find((r) => r.id === ((w && w.conv && w.conv.role_id) || ''));
    const code = (w && w.conv && w.conv.model_code)
      || (role && role.model_code) || S.activeModel || '';
    const profile = modelProfile(code);
    if (!profile) return false;
    if (typeof profile.effective_vision === 'boolean') return profile.effective_vision;
    return !!profile.vision;
  }

  function syncAttachAvailability(w) {
    const btn = $('.attach', w.el);
    const input = $('.file-input', w.el);
    if (!btn) return;
    const vision = supportsVision(w);
    btn.dataset.vision = vision ? '1' : '0';
    btn.title = vision ? '添加图片或文档' : '添加文档（当前模型不支持图片）';
    btn.setAttribute('aria-label', btn.title);
    if (input) input.accept = vision ? `${DOC_ACCEPT},${IMAGE_ACCEPT}` : DOC_ACCEPT;
  }

  function renderAttachments(w) {
    const bar = $('.attach-bar', w.el);
    if (!bar) return;
    const list = w.attachments || [];
    if (!list.length) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.hidden = false;
    bar.innerHTML = list.map((a, i) => {
      const image = a.kind === 'image';
      const thumb = image && a.preview
        ? `<img src="${h(a.preview)}" alt="" />`
        : `<svg viewBox="0 0 24 24"><path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7Z"/><path d="M14 3v4h4"/></svg>`;
      return `<div class="att-pending${image ? ' is-image' : ''}">
        <span class="att-pending-thumb">${thumb}</span>
        <span class="att-pending-main">
          <b title="${h(a.name)}">${h(a.name)}</b>
          <span>${bytes(a.size)}${image ? ' · 图片' : ''}</span>
        </span>
        <button type="button" class="wbtn att-pending-remove" data-attach-remove="${i}" title="移除附件" aria-label="移除附件">×</button>
      </div>`;
    }).join('');
  }

  function addAttachments(w, files) {
    const incoming = Array.from(files || []);
    if (!incoming.length) return;
    const vision = supportsVision(w);
    let total = (w.attachments || []).reduce((n, a) => n + Number(a.size || 0), 0);
    let changed = false;
    for (const file of incoming) {
      if ((w.attachments || []).length >= MAX_ATTACHMENTS) {
        toast(`每条消息最多 ${MAX_ATTACHMENTS} 个附件`, 'bad', 4000);
        break;
      }
      const image = isImageFile(file);
      if (image && !vision) {
        toast('当前模型未开启图片支持，请在「设置 → 模型」中开启或切换模型', 'bad', 5200);
        continue;
      }
      if (!image && !isDocumentFile(file)) {
        toast(`不支持的文件：${file.name}`, 'bad', 4000);
        continue;
      }
      if (file.size > MAX_ATTACHMENT_BYTES) {
        toast(`${file.name} 超过 25 MB`, 'bad', 4200);
        continue;
      }
      if (total + file.size > MAX_ATTACHMENTS_TOTAL) {
        toast('附件总大小超过 50 MB', 'bad', 4200);
        break;
      }
      total += file.size;
      w.attachments.push({
        localId: `att-local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: file.name,
        mime: file.type || '',
        size: file.size,
        kind: image ? 'image' : 'file',
        file,
        preview: image ? URL.createObjectURL(file) : '',
      });
      changed = true;
    }
    if (changed) renderAttachments(w);
  }

  function removeAttachment(w, index) {
    const att = (w.attachments || [])[index];
    if (att && att.preview) URL.revokeObjectURL(att.preview);
    w.attachments.splice(index, 1);
    renderAttachments(w);
  }

  function clearAttachments(w) {
    // 发送成功后清空待发送区；预览 URL 留给乐观消息继续显示，页面刷新后自然释放。
    w.attachments = [];
    renderAttachments(w);
  }

  async function attachmentPayloads(list) {
    const out = [];
    for (const a of list || []) {
      let data = '';
      if (a.file) {
        data = bytesToBase64(await a.file.arrayBuffer());
      }
      if (!data) continue;
      out.push({ name: a.name, mime: a.mime || '', size: a.size || 0, data });
    }
    return out;
  }

  function bytesToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
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
    refreshTeamTasks();
  }

  function showChat(show) {
    const shell = $('#chatShellV3');
    const canvas = document.querySelector('.canvas');
    if (!shell) return;
    shell.hidden = !show;
    if (canvas) canvas.classList.toggle('chat-open', !!show);
    document.body.classList.toggle('chat-open', !!show);
    if (show && S.activeWin) activateView(S.activeWin);
    refreshTeamTasks();
  }

  async function refreshTeamTasks() {
    const el = $('#teamTaskFloat');
    if (!el) return;
    const w = S.activeWin;
    const shell = $('#chatShellV3');
    if (!w || !w.isGroup || w.floating || !shell || shell.hidden) {
      el.hidden = true;
      return;
    }
    try {
      const data = await api(`/conversations/${encodeURIComponent(w.convId)}/runs?limit=30`);
      const runs = (Array.isArray(data) ? data : (data && data.items) || [])
        .filter((r) => r && r.target_role_id
          && ['queued', 'running'].includes(String(r.status || '')));
      if (!runs.length) { el.hidden = true; return; }
      el.innerHTML = `<div class="ttf-head"><span class="dot run"></span><b>后台成员</b><span class="n">${runs.length}</span></div>
        <div class="ttf-list">${runs.map((r) => {
          const role = S.roles.find((x) => x.id === r.target_role_id) || null;
          const name = role ? role.name : (r.role_name || r.target_role_id);
          const status = r.status === 'running' ? '执行中' : '排队中';
          return `<div class="ttf-item">
            ${av(name || '成', 'xs round', role ? role.avatar : '')}
            <div class="ttf-main"><b>${h(name || '成员')}</b><span>${h(status)} · ${h((r.content || '').slice(0, 60))}</span><small>${h(shortTime(r.started_at || r.created_at || ''))}</small></div>
            <span class="ttf-dot${r.status === 'running' ? ' running' : ''}"></span>
          </div>`;
        }).join('')}</div>`;
      el.hidden = false;
    } catch {
      el.hidden = true;
    }
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
    refreshTeamTasks();
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
    body.addEventListener('click', (e) => {
      const toggle = e.target.closest('[data-att-toggle]');
      if (!toggle) return;
      const card = toggle.closest('.att-card');
      if (card) toggleAttPreview(card);
    });
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
    const graphBtn = $('[data-conv-graph]', el);
    if (graphBtn) graphBtn.addEventListener('click', (e) => { e.stopPropagation(); openAgentCanvas(w); });
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
    ta.addEventListener('paste', (e) => {
      const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
      if (!files.length) return;
      e.preventDefault();
      addAttachments(w, files);
    });
    autoGrow(ta);
    const temp = $('[data-temp-role]', el);
    if (temp) temp.addEventListener('click', () => createTempRole(w));
    const fileInput = $('.file-input', el);
    $('.attach', el).addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      addAttachments(w, [...fileInput.files]);
      fileInput.value = '';
    });
    const composerBox = $('.composer .box', el);
    if (composerBox) {
      composerBox.addEventListener('dragover', (e) => {
        if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return;
        e.preventDefault();
        composerBox.classList.add('drag-over');
      });
      composerBox.addEventListener('dragleave', (e) => {
        if (!composerBox.contains(e.relatedTarget)) composerBox.classList.remove('drag-over');
      });
      composerBox.addEventListener('drop', (e) => {
        if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
        e.preventDefault();
        composerBox.classList.remove('drag-over');
        addAttachments(w, e.dataTransfer.files);
      });
    }
    $('.attach-bar', el).addEventListener('click', (e) => {
      const rm = e.target.closest('[data-attach-remove]');
      if (!rm) return;
      removeAttachment(w, Number(rm.dataset.attachRemove));
    });
    syncAttachAvailability(w);
    const modelSel = $('[data-model]', el);
    if (modelSel) modelSel.addEventListener('change', async () => {
      try {
        await api(`/conversations/${encodeURIComponent(w.convId)}/preferences`, {
          method: 'PUT', body: JSON.stringify({ model_code: modelSel.value || '' }),
        });
        w.conv.model_code = modelSel.value || '';
        if (modelSel.value) {
          const profile = modelProfile(w.conv.model_code);
          w.conv.vision = !!(profile && (profile.effective_vision ?? profile.vision));
        } else {
          delete w.conv.vision;
        }
        w.think = '';
        syncThinking(w);
        syncAttachAvailability(w);
        if (!supportsVision(w) && (w.attachments || []).some((a) => a.kind === 'image')) {
          w.attachments = w.attachments.filter((a) => a.kind !== 'image');
          renderAttachments(w);
          toast('新模型不支持图片，已移除待发送的图片', 'bad', 4200);
        }
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
      box.innerHTML = `<div class="chat-empty">${w.isGlobal ? gaAvatar('round') : av(w.title || 'A', w.kind === '群聊' ? '' : 'round', convAvatar(w.conv))}<b>${h(w.title)}</b><span>${h(hint)}</span></div>`;
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
    if (m.kind === 'clear_context') {
      const el = document.createElement('div');
      el.className = 'ctx-clear';
      el.dataset.msgId = m.id || '';
      el.innerHTML = `<span class="ctx-clear-line"></span>
        <span class="ctx-clear-copy"><b>上下文已清空</b><span>此前的消息仍保留在历史中，模型不会再读取它们</span></span>
        <span class="ctx-clear-line"></span>`;
      return el;
    }
    if (m.by === 'system') {
      const el = document.createElement('div');
      el.className = 'sys';
      el.dataset.msgId = m.id || '';
      el.textContent = m.content || '';
      return el;
    }
    if (m.by === 'assistant') return assistantNode(m);
    if (m.kind === 'team_mention') {
      const el = document.createElement('div');
      el.className = 'msg assistant';
      el.dataset.msgId = m.id || '';
      const name = m.role_name || '主持';
      el.innerHTML = `${av(name, 'round', m.role_avatar || '')}
        <div class="msg-body"><div class="msg-who"><b>${h(name)}</b><span class="msg-meta">@ 指派</span></div>
        <div class="bubble handoff-bubble">${h(m.content || '')}</div></div>`;
      return el;
    }
    const el = document.createElement('div');
    el.className = 'msg user';
    el.dataset.msgId = m.id || '';
    el.innerHTML = `${av('我', 'round')}<div class="msg-body"><div class="msg-who">我</div>${m.content ? `<div class="bubble">${h(m.content)}</div>` : ''}${attachmentsHtml(m.attachments)}</div>`;
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

  // 完成 / 失败的一轮：最终答复留在外面，其余步骤按发生顺序折进「过程」。
  function splitAssistantEntries(entries) {
    let final = null;
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      if (entries[i].kind === 'text' && entries[i].final) { final = entries[i]; break; }
    }
    if (!final) {
      for (let i = entries.length - 1; i >= 0; i -= 1) {
        if (entries[i].kind === 'text' && String(entries[i].text || '').trim()) {
          entries[i].final = true;
          final = entries[i];
          break;
        }
      }
    }
    return { process: entries.filter((entry) => entry !== final), final };
  }

  function processFoldHtml(m, entries, open = false) {
    if (!entries.length) return '';
    const thoughts = entries.filter((x) => x.kind === 'thinking').length;
    const tools = entries.filter((x) => x.kind === 'tool').length;
    const meta = [];
    if (thoughts) meta.push(`思考 ${thoughts}`);
    if (tools) meta.push(`工具 ${tools}`);
    const ms = durationText(m.duration_ms);
    if (ms) meta.push(ms);
    if (m.interrupted) meta.push('已停止');
    if (m.error) meta.push('失败');
    return `<details class="turn-fold history-fold"${open ? ' open' : ''}>
      <summary class="turn-fold-bar">
        <span class="fold-caret">›</span>
        <span class="turn-fold-title">过程</span>
        <span class="turn-fold-meta">${h(meta.join(' · '))}</span>
      </summary>
      <div class="turn-fold-body">${entries.map((step) => procStepHtml(step)).join('')}</div>
    </details>`;
  }

  function assistantNode(m) {
    const el = document.createElement('div');
    el.className = 'msg assistant';
    el.dataset.msgId = m.id || '';
    const entries = traceEntries(m);
    markAssistantFinal(m, entries);
    const { process, final } = splitAssistantEntries(entries);
    const name = m.role_name || 'AtlasX';
    const roleSub = [m.role_title, m.role_department].filter(Boolean).join(' · ');
    const meta = [];
    const ms = durationText(m.duration_ms);
    if (ms) meta.push(ms);
    if (m.interrupted) meta.push('已停止');
    if (m.error) meta.push('失败');
    el.innerHTML = `${av(name, 'round', m.role_avatar || '')}
      <div class="msg-body"><div class="msg-who"><b>${h(name)}</b>${roleSub ? `<span class="msg-role">${h(roleSub)}</span>` : ''}${meta.length ? `<span class="msg-meta">${h(meta.join(' · '))}</span>` : ''}</div>
      ${processFoldHtml(m, process, !final)}
      ${final ? procStepHtml(final) : ''}
      ${attachmentsHtml(m.attachments)}
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

  // Markdown → HTML：先转义再按块解析，支持代码块 / 行内代码 / 标题 / 粗斜体 /
  // 删除线 / 列表 / 引用 / 分割线 / 链接 / 图片 / 表格。会话与附件预览共用。
  function inlineMd(raw) {
    const codes = [];
    let s = h(raw).replace(/`([^`\n]+)`/g, (_, code) => {
      codes.push(code);
      return `\u0001${codes.length - 1}\u0001`;
    });
    s = s
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(（])__([^_\n]+)__/g, '$1<strong>$2</strong>')
      .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
      .replace(/(^|[\s(（])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/(^|[\s(（])_([^_\n]+)_/g, '$1<em>$2</em>');
    s = s.replace(/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g,
      (_, alt, src) => `<img src="${src}" alt="${alt}" loading="lazy" />`);
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      if (!/^(https?:|mailto:|#|\/)/i.test(url)) return m;
      return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
    s = s.replace(/\u0001(\d+)\u0001/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
    return s;
  }

  function mdPre(code, lang) {
    const attr = lang ? ` data-lang="${h(lang)}"` : '';
    return `<pre class="md-pre"${attr}><code>${h(code)}</code></pre>`;
  }

  function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(line);
  }

  function tableCells(line) {
    let s = line.trim();
    if (s.startsWith('|')) s = s.slice(1);
    if (s.endsWith('|')) s = s.slice(0, -1);
    return s.split('|').map((c) => c.trim());
  }

  function md(text) {
    const src = String(text || '').replace(/\r\n?/g, '\n');
    const lines = src.split('\n');
    const out = [];
    let para = [];
    let i = 0;

    const flush = () => {
      if (!para.length) return;
      out.push(`<p>${para.map(inlineMd).join('<br>')}</p>`);
      para = [];
    };

    while (i < lines.length) {
      const line = lines[i];

      const fence = line.match(/^\s*(```+|~~~+)\s*([\w+.#-]*)\s*$/);
      if (fence) {
        flush();
        const marker = fence[1][0];
        const lang = fence[2] || '';
        const buf = [];
        i += 1;
        while (i < lines.length && !new RegExp(`^\\s*${marker}{3,}\\s*$`).test(lines[i])) {
          buf.push(lines[i]);
          i += 1;
        }
        i += 1;
        out.push(mdPre(buf.join('\n'), lang));
        continue;
      }

      if (!line.trim()) { flush(); i += 1; continue; }

      const heading = line.match(/^\s*(#{1,4})\s+(.+)$/);
      if (heading) {
        flush();
        const level = heading[1].length;
        out.push(`<h${level}>${inlineMd(heading[2].replace(/\s+#+\s*$/, ''))}</h${level}>`);
        i += 1;
        continue;
      }

      if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
        flush();
        out.push('<hr>');
        i += 1;
        continue;
      }

      if (/^\s*>/.test(line)) {
        flush();
        const buf = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) {
          buf.push(lines[i].replace(/^\s*>\s?/, ''));
          i += 1;
        }
        out.push(`<blockquote>${buf.map(inlineMd).join('<br>')}</blockquote>`);
        continue;
      }

      if (/^\s*[-*+]\s+/.test(line)) {
        flush();
        const items = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*+]\s+/, ''));
          i += 1;
        }
        out.push(`<ul>${items.map((x) => `<li>${inlineMd(x)}</li>`).join('')}</ul>`);
        continue;
      }

      if (/^\s*\d+[.)]\s+/.test(line)) {
        flush();
        const items = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''));
          i += 1;
        }
        out.push(`<ol>${items.map((x) => `<li>${inlineMd(x)}</li>`).join('')}</ol>`);
        continue;
      }

      if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
        flush();
        const head = tableCells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
          rows.push(tableCells(lines[i]));
          i += 1;
        }
        out.push('<div class="md-table-wrap"><table><thead><tr>'
          + head.map((c) => `<th>${inlineMd(c)}</th>`).join('')
          + '</tr></thead><tbody>'
          + rows.map((r) => '<tr>' + r.map((c) => `<td>${inlineMd(c)}</td>`).join('') + '</tr>').join('')
          + '</tbody></table></div>');
        continue;
      }

      para.push(line);
      i += 1;
    }
    flush();
    return out.join('');
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
  async function compactConversation(w) {
    if (!w) return;
    try {
      const out = await api(`/conversations/${encodeURIComponent(w.convId)}/compact`, { method: 'POST' });
      if (out && out.compressed) {
        toast(`上下文已压缩：${out.before_tokens} → ${out.after_tokens} tokens`, 'ok', 4500);
      } else {
        const reason = (out && out.reason) || '';
        const hint = reason === 'under' ? '上下文还很短，暂时不用压缩'
          : reason === 'no_window' ? '当前模型没有配置上下文窗口'
          : '没有可折叠的历史';
        toast(hint, 'ok', 3000);
      }
    } catch (err) { toast(err.message, 'bad'); }
  }

  // 从正文解析 @成员名：随 run 落库，队列重放 / 非圆桌模式也能直接点名到人。
  function mentionedNames(text) {
    const out = [];
    const re = /@([A-Za-z0-9_\-\u4e00-\u9fff]{1,32})/g;
    let m;
    while ((m = re.exec(String(text || ''))) !== null) {
      const name = m[1].trim();
      if (name && !out.includes(name)) out.push(name);
      if (out.length >= 16) break;
    }
    return out;
  }

  async function send(w) {
    const ta = $('.composer textarea', w.el);
    const text = String(ta.value || '').trim();
    if (!text && !(w.attachments || []).length) return;
    const action = /^\/(new|compact|clear|work)(?:\s+([\s\S]*))?$/.exec(text);
    if (action) {
      ta.value = '';
      autoGrow(ta);
      if (action[1] === 'new') { newConversation(); return; }
      if (action[1] === 'compact') { compactConversation(w); return; }
      if (action[1] === 'clear') {
        const scope = String(action[2] || '').trim().toLowerCase() === 'current'
          ? 'current' : 'all';
        try {
          const out = await api(`/conversations/${encodeURIComponent(w.convId)}/clear`, {
            method: 'POST', body: JSON.stringify({ scope }),
          });
          const conv = await api(`/conversations/${encodeURIComponent(w.convId)}`);
          w.conv = conv;
          renderHistory(w, conv.messages || [], true);
          toast(scope === 'all'
            ? `已清空 ${Number(out && out.cleared) || 0} 个会话的上下文（历史消息保留）`
            : '当前会话上下文已清空（历史消息保留）', 'ok', 3000);
        } catch (err) { toast(err.message, 'bad'); }
        return;
      }
      if (action[1] === 'work') {
        newWorkItem({
          title: String(action[2] || '').trim(),
          workspace_id: w.conv.bound_workspace_id || w.conv.workspace_id || null,
          role_id: w.conv.role_id || null,
          team_id: w.conv.team_id || null,
          body: `来自会话「${w.title}」\n\n`,
        });
        return;
      }
    }
    const sendBtn = $('.send', w.el);
    const attachments = (w.attachments || []).map((a) => ({ ...a }));
    const optimisticAtts = attachments.map((a, i) => ({
      id: a.localId || `local-${i}`,
      name: a.name, mime: a.mime, size: a.size, kind: a.kind,
      url: a.preview || '', local: !a.preview,
    }));
    sendBtn.disabled = true;
    ta.value = '';
    autoGrow(ta);
    const box = $('.msgs', w.el);
    const userEl = messageNode({ by: 'user', content: text, attachments: optimisticAtts });
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
      const payloadAttachments = await attachmentPayloads(attachments);
      const run = await api(`/conversations/${encodeURIComponent(w.convId)}/messages/run`, {
        method: 'POST',
        body: JSON.stringify({
          content: text, mode: 'autonomous', auto_execute: w.perm === 'auto',
          thinking_level: w.think || '',
          mentions: mentionedNames(text),
          attachments: payloadAttachments.length ? payloadAttachments : undefined,
        }),
      });
      // 发送成功后待发送区立即清空；会话区的乐观消息已经带上图片 / 文件卡片。
      clearAttachments(w);
      // 本窗口没有在跑的 run → 直接跟这条；否则它已进队列，轮询等它开始。
      if (w.streamingNow || w.es) {
        w.queue = (run.queue || []).length ? (run.queue || []) : w.queue;
        renderQueue(w);
        pollQueue(w);
      } else {
        startStream(w, run.id);
      }
    } catch (err) {
      // 失败时撤回乐观消息与占位，把正文留在输入框，附件仍在待发送区。
      userEl.remove();
      live.remove();
      w.live = null;
      ta.value = text;
      autoGrow(ta);
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
      if (S.agentCanvasConv === w.convId && $('#agentCanvas') && !$('#agentCanvas').hidden) {
        refreshAgentCanvas();
      }
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
    if (evt.subagent_event) {
      if (S.agentCanvasConv === w.convId && $('#agentCanvas') && !$('#agentCanvas').hidden) {
        clearTimeout(S.agentCanvasRefreshTimer);
        S.agentCanvasRefreshTimer = setTimeout(refreshAgentCanvas, 250);
      }
      return;
    }
    if (!w.live) return;
    const host = w.live.flow || $('.live-flow', w.live.el);
    const box = $('.msgs', w.el);
    if (evt.start) {
      const who = $('.msg-who', w.live.el);
      const role = S.roles.find((r) => r.id === evt.role_id) || null;
      const roleSub = role ? [role.title, role.department].filter(Boolean).join(' · ') : '';
      if (evt.role_name && who) {
        who.innerHTML = `<b>${h(evt.role_name)}</b>${roleSub ? `<span class="msg-role">${h(roleSub)}</span>` : ''}`;
      }
      $('.avatar', w.live.el).outerHTML = av(evt.role_name || 'A', 'round', role ? role.avatar : '');
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
    if (evt.org_update) {
      loadAll();
      toast('组织与部门已更新', 'ok', 1800);
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
  function roleToolName(code) {
    const tool = (S.toolCatalog || []).find((x) => x.code === code);
    return tool ? (tool.name || tool.code) : code;
  }

  function renderRoleDrawer() {
    const role = roleDrawerRole;
    if (!role) return;
    const drawer = $('#sideDrawer');
    const body = $('#sideDrawerBody');
    const foot = $('#sideDrawerFoot');
    if (!drawer || !body || !foot) return;
    const tools = Array.isArray(role.tools) ? role.tools : [];
    const skills = S.roleSkillsRoleId === role.id ? S.roleSkills : null;
    const temporary = role.scope === 'temporary';
    $('#sideDrawerTitle').textContent = '角色资料';
    $('#sideDrawerSub').textContent = `${roleLabel(role) || '角色'} · ${temporary ? '临时角色' : '持久角色'}`;
    body.innerHTML = `<div class="group-identity">
      ${av(role.name || '角', 'round', role.avatar)}
      <div class="group-identity-main">
        <div class="role-detail-name">${h(role.name || '角色')} <span class="tag ${temporary ? 'tmp' : ''}">${temporary ? '临时' : '持久'}</span></div>
        <div class="role-line"><b>${h(role.title || '未填写岗位')}</b></div>
        <div class="role-line">${role.department ? `部门：${h(role.department)}` : '<span class="role-warn">未安排部门</span>'}</div>
        <div class="role-line">${h(role.expertise || '没有填写专长')}</div>
        <div class="role-line mono">模型：${h(role.model_code || '未指定')}</div>
      </div>
    </div>
    <section class="group-section">
      <div class="group-section-head"><h3>人格 / 工作方式</h3></div>
      <div class="work-body">${md(role.persona || '（未填写）')}</div>
    </section>
    <section class="group-section">
      <div class="group-section-head"><h3>工具权限</h3><span>${tools.length ? `${tools.length} 个工具` : '全部工具'}</span></div>
      <div class="chips">${tools.map((t) => `<span class="chip" title="${h(t)}">${h(roleToolName(t))}</span>`).join('') || '<span class="chip">全部工具</span>'}</div>
    </section>
    <section class="group-section">
      <div class="group-section-head"><h3>专属技能</h3><span>${skills ? `${skills.length} / 20` : '加载中…'}</span></div>
      <p class="group-mode-hint">只属于「${h(role.name || '当前角色')}」；其他角色看不到，也不会出现在它们的技能清单里。可通过文件、图片、网页搜索等材料整理后创建。</p>
      ${skills ? (skills.length ? `<div class="role-skill-list">${skills.map((s) => `<div class="role-skill-row">
        <div class="role-skill-main"><b>${h(s.name)}</b><span>${h(s.description || '无描述')}</span></div>
        <button class="iconbtn" type="button" data-role-skill-del="${h(s.id)}" title="删除技能" aria-label="删除技能">×</button>
      </div>`).join('')}</div>` : '<div class="group-empty">还没有专属技能</div>') : ''}
      <div class="avatar-actions">
        <button class="btn small" type="button" data-role-skill-new>新建专属技能</button>
        <button class="btn small" type="button" data-role-skill-merge${skills && skills.length >= 2 ? '' : ' disabled'}>合并所选…</button>
      </div>
    </section>
    <section class="group-section">
      <div class="group-section-head"><h3>角色信息</h3></div>
      <div class="work-meta" style="border:0;padding:0">
        <span>ID <b>${h(role.id)}</b></span>
        <span>来源 <b>${h(role.source || 'user')}</b></span>
        <span>创建 <b>${h(role.created_at || '')}</b></span>
      </div>
    </section>
    ${role.system ? '' : `<section class="group-section group-danger">
      <div class="group-section-head"><h3>删除角色</h3></div>
      <p class="group-mode-hint">只删除角色本身，已有会话保留；临时角色删除后不可恢复。</p>
      <button class="btn danger" type="button" data-role-delete>删除角色</button>
    </section>`}`;
    foot.innerHTML = `<button class="btn primary" type="button" data-role-chat>单聊</button>
      <button class="btn" type="button" data-role-edit>编辑</button>
      <button class="btn" type="button" data-role-superagent>新建工单</button>
      ${temporary ? '<button class="btn" type="button" data-role-promote>存为持久角色</button>' : ''}
      <span class="spacer"></span><button class="btn" type="button" data-role-close>关闭</button>`;
    const newSkill = $('[data-role-skill-new]', body);
    if (newSkill) newSkill.addEventListener('click', () => openRoleSkillEditor(role));
    const mergeSkill = $('[data-role-skill-merge]', body);
    if (mergeSkill) mergeSkill.addEventListener('click', () => mergeRoleSkills(role));
    $$('[data-role-skill-del]', body).forEach((el) => {
      el.addEventListener('click', () => deleteRoleSkill(role, el.dataset.roleSkillDel));
    });
    drawer.hidden = false;
    const scrim = $('#drawerScrim');
    if (scrim) scrim.hidden = false;
  }

  function openRoleDetail(roleId) {
    const role = S.roles.find((r) => r.id === roleId);
    if (!role) { toast('角色不存在', 'bad'); return; }
    roleDrawerRole = role;
    S.roleSkills = [];
    S.roleSkillsRoleId = null;
    // 工具显示名按需加载，不阻塞抽屉打开。
    ensureToolCatalog().then(() => renderRoleDrawer()).catch(() => {});
    renderRoleDrawer();
    loadRoleSkills(role.id);
  }

  async function loadRoleSkills(roleId) {
    try {
      const data = await api(`/app/skills?role_id=${encodeURIComponent(roleId)}&include_global=false`);
      S.roleSkills = data.items || [];
      S.roleSkillsRoleId = roleId;
      if (roleDrawerRole && roleDrawerRole.id === roleId) renderRoleDrawer();
    } catch (err) {
      S.roleSkills = [];
      S.roleSkillsRoleId = roleId;
      if (roleDrawerRole && roleDrawerRole.id === roleId) renderRoleDrawer();
      toast(err.message, 'bad');
    }
  }

  async function openRoleSkillEditor(role) {
    const saved = await modal({
      title: `为「${role.name || '角色'}」新建专属技能`,
      fields: `<div class="field"><label>技能名称</label><input class="input" id="rsName" placeholder="例如：合同条款审阅" /></div>
        <div class="field"><label>一句话描述</label><input class="input" id="rsDesc" placeholder="什么时候用、能做什么" /></div>
        <div class="field"><label>SKILL.md 正文</label><textarea class="input mono" id="rsContent" rows="12" placeholder="适用场景、步骤、检查项、注意事项……">${h('')}</textarea></div>
        <div class="hintline">只能由该角色使用；可先用文件、图片或网页搜索整理材料，再把结论写进正文。</div>`,
      read: () => ({
        name: $('#rsName').value.trim(),
        description: $('#rsDesc').value.trim(),
        content: $('#rsContent').value.trim(),
      }),
    });
    if (!saved) return;
    if (!saved.name || !saved.content) { toast('技能名称和正文不能为空', 'bad'); return; }
    try {
      await api('/app/skills', {
        method: 'POST', body: JSON.stringify({ ...saved, role_id: role.id }),
      });
      await loadRoleSkills(role.id);
      toast('专属技能已创建', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function mergeRoleSkills(role) {
    const skills = S.roleSkillsRoleId === role.id ? S.roleSkills : [];
    if (skills.length < 2) { toast('至少需要 2 个专属技能才能合并', 'bad'); return; }
    const saved = await modal({
      title: `合并「${role.name || '角色'}」的专属技能`,
      fields: `<div class="field"><label>选择要合并的技能（至少 2 个）</label>
          <div class="role-skill-picker">${skills.map((s) => `<label class="role-skill-choice"><input type="checkbox" data-rs-pick="${h(s.id)}" /> <span>${h(s.name)}</span><small>${h(s.description || '')}</small></label>`).join('')}</div>
        </div>
        <div class="field"><label>合并后的名称</label><input class="input" id="rsMergeName" placeholder="例如：综合审阅技能" /></div>
        <div class="field"><label>合并后的描述</label><input class="input" id="rsMergeDesc" placeholder="合并后的适用场景" /></div>
        <div class="hintline">来源技能会被删除，合并后的技能仍只属于该角色。</div>`,
      read: () => ({
        skill_ids: $$('[data-rs-pick]:checked').map((el) => el.dataset.rsPick),
        name: $('#rsMergeName').value.trim(),
        description: $('#rsMergeDesc').value.trim(),
      }),
    });
    if (!saved) return;
    if (!saved.name) { toast('合并后的名称不能为空', 'bad'); return; }
    if (saved.skill_ids.length < 2) { toast('至少选择 2 个技能', 'bad'); return; }
    try {
      await api('/app/skills/merge', {
        method: 'POST', body: JSON.stringify({ ...saved, role_id: role.id }),
      });
      await loadRoleSkills(role.id);
      toast('技能已合并', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deleteRoleSkill(role, skillId) {
    const skill = (S.roleSkills || []).find((s) => s.id === skillId);
    if (!confirm(`删除专属技能「${skill ? skill.name : skillId}」？`)) return;
    try {
      await api(`/app/skills/${encodeURIComponent(skillId)}`, { method: 'DELETE' });
      await loadRoleSkills(role.id);
      toast('技能已删除', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function promoteRoleDrawer() {
    if (!roleDrawerRole) return;
    try {
      await api(`/roles/${encodeURIComponent(roleDrawerRole.id)}/promote`, { method: 'POST' });
      await loadAll();
      const fresh = S.roles.find((r) => r.id === roleDrawerRole.id);
      if (fresh) { roleDrawerRole = fresh; renderRoleDrawer(); }
      toast('已存为持久角色', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deleteRoleDrawer() {
    if (!roleDrawerRole) return;
    if (roleDrawerRole.system) { toast('默认角色不能删除', 'bad'); return; }
    if (!confirm(`删除角色「${roleDrawerRole.name}」？`)) return;
    try {
      await api(`/roles/${encodeURIComponent(roleDrawerRole.id)}`, { method: 'DELETE' });
      closeSideDrawer();
      await loadAll();
      toast('角色已删除', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
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

  async function openDeptEditor(dept, parentId) {
    const isNew = !dept;
    const exclude = dept ? deptSubtree(dept.id) : new Set();
    const parent = isNew ? (parentId || '') : (dept.parent_id || '');
    const saved = await modal({
      title: isNew ? '新建部门' : `编辑部门「${dept.name}」`,
      fields: `<div class="field"><label>部门头像</label>${avatarFieldHtml('dAvatar', 'department', dept ? dept.avatar : '', dept ? dept.name : '')}</div>
        <div class="field"><label>部门名称</label><input class="input" id="dName" value="${h(dept ? dept.name : '')}" placeholder="例如：产品部" /></div>
        <div class="field"><label>上级部门</label><select class="input" id="dParent">${deptOptions(parent, { exclude, emptyLabel: '无（根部门）' })}</select>
          <small class="role-line">选择某个部门即成为它的子部门；部门支持多层。</small></div>`,
      onMount: () => bindAvatarField('dAvatar', 'department', dept ? dept.name : ''),
      read: () => ({
        name: $('#dName').value.trim(),
        avatar: $('#dAvatar').value || '',
        parent_id: $('#dParent').value || null,
      }),
    });
    if (!saved) return;
    if (!saved.name) { toast('部门名称不能为空', 'bad'); return; }
    try {
      if (isNew) await api('/departments', { method: 'POST', body: JSON.stringify(saved) });
      else await api(`/departments/${encodeURIComponent(dept.id)}`, { method: 'PATCH', body: JSON.stringify(saved) });
      await loadAll();
      toast(isNew ? '部门已创建' : '部门已保存', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deleteDepartment(dept) {
    if (!confirm(`删除部门「${dept.name}」？\n子部门会移到上一级，部门内角色会变成「未安排」。`)) return;
    try {
      const out = await api(`/departments/${encodeURIComponent(dept.id)}`, { method: 'DELETE' });
      if (S.roleDept === dept.id) S.roleDept = 'all';
      await loadAll();
      const moved = Number(out && out.children_moved) || 0;
      const unassigned = Number(out && out.roles_unassigned) || 0;
      toast(`部门已删除${moved ? ` · ${moved} 个子部门已上移` : ''}${unassigned ? ` · ${unassigned} 个角色转为未安排` : ''}`, 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function openRoleEditor(role, preset = {}) {
    const isNew = !role;
    const catalog = await ensureToolCatalog();
    const modelCode = (role && role.model_code) || '';
    const currentDept = role ? (role.department_id || '') : (preset.department_id || '');
    const modelOptions = [`<option value="">未指定（使用默认）</option>`]
      .concat(S.models.map((m) => `<option value="${h(m.code)}"${m.code === modelCode ? ' selected' : ''}>${h(m.name || m.code)} · ${h(m.code)}</option>`));
    if (modelCode && !S.models.some((m) => m.code === modelCode)) {
      modelOptions.push(`<option value="${h(modelCode)}" selected>${h(modelCode)}</option>`);
    }
    const saved = await modal({
      title: isNew ? '新建角色' : `编辑「${role.name}」`,
      fields: `<div class="field"><label>角色头像</label>${avatarFieldHtml('rAvatar', 'role', role ? role.avatar : '', role ? role.name : '')}</div>
        <div class="field"><label>名称（谁）</label><input class="input" id="rName" value="${h(role ? role.name : '')}" placeholder="例如：张三 / 审阅者" /></div>
        <div class="field"><label>岗位（做什么）</label><input class="input" id="rTitle" value="${h(role ? role.title : '')}" placeholder="例如：后端工程师 / 合同审阅" /></div>
        <div class="field"><label>所属部门 <span class="role-line">可留空 = 未安排</span></label>
          <select class="input" id="rDept">${deptOptions(currentDept)}</select>
          <small class="role-line">部门支持多级；在「组织与角色」里可以新建子部门。</small>
        </div>
        <div class="field"><label>专长（一句话）</label><input class="input" id="rExpertise" value="${h(role ? role.expertise : '')}" /></div>
        <div class="field"><label>人格 / 工作方式</label><textarea class="input" id="rPersona">${h(role ? role.persona : '')}</textarea></div>
        <div class="field"><label>工具权限 <span class="role-line">不选 = 全部工具</span></label>
          ${roleToolPickerHtml(role, catalog)}
        </div>
        <div class="field"><label>模型档案</label>
          <select class="input" id="rModel">${modelOptions.join('')}</select>
          <small class="role-line">不指定时使用默认模型；指定后优先使用该角色的模型。</small>
        </div>`,
      onMount: () => {
        bindAvatarField('rAvatar', 'role', role ? role.name : '');
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
          avatar: $('#rAvatar').value || '',
          title: $('#rTitle').value.trim(),
          department_id: $('#rDept').value || null,
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

  /* ── 工单：工单弹框与动作 ───────────────────────── */
  function workAssignFields(item = {}, opts = {}) {
    const ws = item.workspace_id || (S.workPolicy && S.workPolicy.default_workspace_id) || '';
    const role = item.role_id || (S.workPolicy && S.workPolicy.default_role_id) || '';
    const team = item.team_id || (S.workPolicy && S.workPolicy.default_team_id) || '';
    const options = (list, current, emptyLabel) => `<option value="">${emptyLabel}</option>` + list.map((x) =>
      `<option value="${h(x.id)}"${x.id === current ? ' selected' : ''}>${h(x.name || x.id)}</option>`).join('');
    const priority = opts.priority === false ? '' : `<div class="field"><label>优先级</label><label class="switch"><input type="checkbox" id="wPriority"${Number(item.priority || 0) > 0 ? ' checked' : ''} /><span class="track"></span><span class="switch-label">高优先级</span></label></div>`;
    return `<div class="work-settings-grid">
      <label class="field"><label>工作区</label><select class="input" id="wWorkspace">${options(S.workspaces, ws, '默认工作区')}</select></label>
      <label class="field"><label>角色</label><select class="input" id="wRole">${options(S.roles, role, '默认角色')}</select></label>
      <label class="field"><label>群组（可选）</label><select class="input" id="wTeam">${options(S.teams, team, '不绑定群组')}</select></label>
      ${priority}
    </div>`;
  }

  function readWorkAssign() {
    return {
      workspace_id: $('#wWorkspace').value || null,
      role_id: $('#wRole').value || null,
      team_id: $('#wTeam').value || null,
      priority: $('#wPriority').checked ? 1 : 0,
    };
  }

  async function newWorkItem(seed = {}) {
    const fields = `<div class="field"><label>标题</label><input class="input" id="wTitle" value="${h(seed.title || '')}" placeholder="例如：修复登录超时" /></div>
      <div class="field"><label>说明</label><textarea class="input" id="wBody" rows="5" placeholder="背景、验收标准、相关链接……">${h(seed.body || '')}</textarea></div>
      ${workAssignFields(seed)}`;
    const saved = await modal({
      title: '新建工单',
      fields,
      read: () => ({
        title: $('#wTitle').value.trim(),
        body: $('#wBody').value.trim(),
        ...readWorkAssign(),
      }),
    });
    if (!saved) return;
    if (!saved.title) { toast('工单标题不能为空', 'bad'); return; }
    try {
      const item = await api('/work-items', { method: 'POST', body: JSON.stringify(saved) });
      S.workSelected = item.id;
      setSection('work');
      await loadWorkItems();
      openWorkDetail(item.id);
      toast('工单已创建', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function editWorkItem(item) {
    const saved = await modal({
      title: '编辑工单',
      fields: `<label class="field"><label>标题</label><input class="input" id="wTitle" value="${h(item.title || '')}" /></label>
        <label class="field"><label>说明</label><textarea class="input" id="wBody" rows="6">${h(item.body || '')}</textarea></label>
        <div class="work-settings-grid">
          <label class="field"><label>状态</label><select class="input" id="wStatus">${Object.entries(WORK_STATUS).map(([id, label]) => `<option value="${id}"${item.status === id ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
        </div>
        ${workAssignFields(item)}`,
      read: () => ({
        title: $('#wTitle').value.trim(),
        body: $('#wBody').value.trim(),
        status: $('#wStatus').value,
        ...readWorkAssign(),
      }),
    });
    if (!saved) return;
    if (!saved.title) { toast('工单标题不能为空', 'bad'); return; }
    try {
      await api(`/work-items/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: JSON.stringify(saved) });
      await loadWorkItems();
      toast('工单已更新', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function openWorkSettings() {
    const policy = S.workPolicy || {};
    const saved = await modal({
      title: '工单策略',
      fields: `<div class="work-settings-grid">
        <label class="field"><label>运行模式</label><select class="input" id="wMode">
          <option value="off"${policy.mode === 'off' ? ' selected' : ''}>关闭：只手动创建工单</option>
          <option value="observe"${(!policy.mode || policy.mode === 'observe') ? ' selected' : ''}>观察：只记录，不自动派活</option>
          <option value="auto"${policy.mode === 'auto' ? ' selected' : ''}>自动：新建工单后自动派活</option>
        </select></label>
        <label class="field"><label>新会话权限</label><select class="input" id="wPerm">
          <option value="auto"${policy.permission_mode === 'auto' ? ' selected' : ''}>自动判断（高危才问）</option>
          <option value="once"${policy.permission_mode === 'once' ? ' selected' : ''}>每次询问</option>
          <option value="session"${policy.permission_mode === 'session' ? ' selected' : ''}>会话允许</option>
          <option value="always"${policy.permission_mode === 'always' ? ' selected' : ''}>完全允许</option>
        </select></label>
      </div>
      <div class="field"><label>默认执行上下文</label>${workAssignFields({ workspace_id: policy.default_workspace_id, role_id: policy.default_role_id, team_id: policy.default_team_id }, { priority: false })}</div>
      <div class="field"><label>执行策略</label><label class="switch"><input type="checkbox" id="wAutoExecute"${policy.auto_execute ? ' checked' : ''} /><span class="track"></span><span class="switch-label">跳过常规确认直接执行（高风险仍受权限档位约束）</span></label></div>`,
      read: () => ({
        mode: $('#wMode').value,
        permission_mode: $('#wPerm').value,
        default_workspace_id: $('#wWorkspace').value || null,
        default_role_id: $('#wRole').value || null,
        default_team_id: $('#wTeam').value || null,
        auto_execute: $('#wAutoExecute').checked,
      }),
    });
    if (!saved) return;
    try {
      S.workPolicy = await api('/work-items/settings', { method: 'PUT', body: JSON.stringify(saved) });
      await loadWorkItems();
      toast('工单策略已保存', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function dispatchWork(item) {
    try {
      const out = await api(`/work-items/${encodeURIComponent(item.id)}/dispatch`, { method: 'POST' });
      S.workSelected = out.id;
      await loadWorkItems();
      if (out.status === 'needs_attention') {
        toast(out.attention_reason || '需要你处理', 'bad', 4000);
      } else {
        toast(out.conversation_id ? '已派活；需要你时会出现在「需要你」' : '已派活', 'ok', 2600);
      }
    } catch (err) { toast(err.message, 'bad'); }
  }

  function openWorkConversation(item) {
    if (!item || !item.conversation_id) return;
    closeSideDrawer();
    setSection('conv');
    openConversation(item.conversation_id);
  }

  async function patchWorkStatus(item, status) {
    try {
      await api(`/work-items/${encodeURIComponent(item.id)}`, {
        method: 'PATCH', body: JSON.stringify({ status }),
      });
      await loadWorkItems();
      toast(status === 'done' ? '工单已完成' : status === 'closed' ? '工单已关闭' : '工单已重新打开', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function deleteWorkItem(item) {
    if (!confirm(`删除工单「${item.title}」？已创建的会话不会被删除。`)) return;
    try {
      await api(`/work-items/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
      S.workSelected = null;
      if (S.workDrawerId === item.id) closeSideDrawer();
      await loadWorkItems();
      toast('工单已删除', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── QQ 式群聊设置抽屉 ───────────────────────────────── */
  let groupDrawerTeam = null;
  let groupDraft = null;
  let roleDrawerRole = null;

  function groupRoleBrief(role) {
    return {
      id: role.id,
      name: role.name || role.id,
      title: role.title || '',
      department: role.department || '',
      avatar: role.avatar || '',
      expertise: role.expertise || '',
      model_code: role.model_code || null,
    };
  }

  function roleNameKey(name) {
    return String(name || '').trim().toLowerCase();
  }

  function openWorkspaceDetail(wsId) {
    const ws = S.workspaces.find((w) => w.id === wsId);
    if (!ws) { toast('工作区不存在', 'bad'); return; }
    if (S.wsDrawerId !== wsId) {
      S.wsPath = '.';
      S.wsFiles = null;
      S.wsPreview = null;
    }
    S.wsDrawerId = wsId;
    S.selectedWs = wsId;
    renderWorkspaceDrawer();
    if (!S.wsFiles && !S.wsLoading) loadWsDir(wsId, S.wsPath);
  }

  function renderWorkspaceDrawer() {
    const ws = S.workspaces.find((w) => w.id === S.wsDrawerId);
    const drawer = $('#sideDrawer');
    const body = $('#sideDrawerBody');
    const foot = $('#sideDrawerFoot');
    if (!drawer || !body || !foot) return;
    if (!ws) { closeSideDrawer(); return; }
    const files = S.wsFiles || [];
    const preview = S.wsPreview;
    const pathLabel = S.wsPath === '.' ? '/' : '/' + S.wsPath;
    const count = Number(ws.conversation_count || 0);
    $('#sideDrawerTitle').textContent = '工作区';
    $('#sideDrawerSub').textContent = `${ws.name} · ${pathLabel}`;
    body.innerHTML = `<div class="group-identity">
      <span class="ws-tile-icon lg"><svg viewBox="0 0 24 24"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5Z"/></svg></span>
      <div class="group-identity-main">
        <div class="role-detail-name">${h(ws.name)}</div>
        <div class="role-line mono">${h(ws.path || ws.root_path || '托管目录')}</div>
        <div class="chips"><span class="chip">${count} 个会话</span>${ws.last_conv_updated ? `<span class="chip">最近 ${h(shortTime(ws.last_conv_updated))}</span>` : ''}</div>
      </div>
    </div>
    <section class="group-section">
      <div class="group-section-head"><h3>目录</h3><span>${h(pathLabel)}</span></div>
      <div class="ws-browser">
        <aside class="ws-file-list">
          ${S.wsPath === '.' ? '' : '<button class="btn small" type="button" data-ws-up>↑ 上一级</button>'}
          ${S.wsLoading ? '<div class="ws-empty">加载中…</div>' : (files.length ? files.map((f) => `
            <button class="ws-file-row" type="button" data-ws-item="${h(f.path)}" data-ws-type="${h(f.type)}">
              <span class="ws-file-ico">${f.type === 'dir' ? '▸' : '·'}</span><span>${h(f.name)}</span>
            </button>`).join('') : '<div class="ws-empty">空目录</div>')}
        </aside>
        <article class="ws-file-preview">
          ${preview ? `<div class="ws-preview-head"><b>${h(preview.path)}</b><span>${bytes(preview.size)}</span><button class="mini-link" type="button" data-ws-preview-close>关闭</button></div>
            <pre>${h(preview.text || '')}</pre>`
            : '<div class="ws-empty">选择左侧文件查看只读预览</div>'}
        </article>
      </div>
    </section>
    <section class="group-section">
      <div class="group-section-head"><h3>管理</h3></div>
      <div class="avatar-actions">
        <button class="btn small" type="button" data-ws-rename>重命名</button>
        <button class="btn small" type="button" data-ws-archive>${ws.archived ? '恢复工作区' : '归档工作区'}</button>
        <button class="btn small danger" type="button" data-ws-delete>删除工作区</button>
      </div>
    </section>`;
    foot.innerHTML = `<button class="btn" type="button" data-ws-bind>绑定到当前窗口</button>
      <button class="btn" type="button" data-ws-reveal>打开文件夹</button>
      <button class="btn" type="button" data-ws-superagent>新建工单</button>
      <span class="spacer"></span><button class="btn" type="button" data-ws-drawer-close>关闭</button>`;
    drawer.hidden = false;
    const scrim = $('#drawerScrim');
    if (scrim) scrim.hidden = false;
    $$('[data-ws-item]', body).forEach((el) => el.addEventListener('click', () => {
      if (el.dataset.wsType === 'dir') {
        S.wsPath = el.dataset.wsItem; S.wsPreview = null; S.wsFiles = null;
        loadWsDir(ws.id, S.wsPath);
      } else {
        openWsFile(ws.id, el.dataset.wsItem);
      }
    }));
    const up = $('[data-ws-up]', body);
    if (up) up.addEventListener('click', () => {
      const parts = (S.wsPath === '.' ? [] : S.wsPath.split('/'));
      parts.pop();
      S.wsPath = parts.length ? parts.join('/') : '.';
      S.wsPreview = null; S.wsFiles = null;
      loadWsDir(ws.id, S.wsPath);
    });
    const closePreview = $('[data-ws-preview-close]', body);
    if (closePreview) closePreview.addEventListener('click', () => { S.wsPreview = null; renderWorkspaceDrawer(); });
    const rename = $('[data-ws-rename]', body);
    if (rename) rename.addEventListener('click', () => renameWorkspace(ws));
    const archive = $('[data-ws-archive]', body);
    if (archive) archive.addEventListener('click', () => archiveWorkspace(ws));
    const del = $('[data-ws-delete]', body);
    if (del) del.addEventListener('click', () => deleteWorkspace(ws));
    const bind = $('[data-ws-bind]', foot);
    if (bind) bind.addEventListener('click', () => {
      const win = S.activeWin;
      if (!win) { toast('先打开一个会话窗口', 'bad'); return; }
      bindWorkspaceTo(win, ws.id);
    });
    const reveal = $('[data-ws-reveal]', foot);
    if (reveal) reveal.addEventListener('click', () => revealWorkspace(ws));
    const superBtn = $('[data-ws-superagent]', foot);
    if (superBtn) superBtn.addEventListener('click', () => newWorkItem({
      workspace_id: ws.id, body: `来自工作区「${ws.name}」\n\n`,
    }));
    const close = $('[data-ws-drawer-close]', foot);
    if (close) close.addEventListener('click', closeSideDrawer);
  }

  function closeSideDrawer() {
    const drawer = $('#sideDrawer');
    const scrim = $('#drawerScrim');
    if (drawer) drawer.hidden = true;
    if (scrim) scrim.hidden = true;
    groupDrawerTeam = null;
    groupDraft = null;
    roleDrawerRole = null;
    S.workDrawerId = null;
    S.wsDrawerId = null;
  }

  function syncGroupDraft() {
    if (!groupDraft) return;
    const name = $('#groupNameInput');
    const desc = $('#groupDescInput');
    const mode = $('#groupModeSelect');
    const speakers = $('#groupSpeakers');
    const search = $('#groupInviteSearch');
    const avatar = $('#groupAvatar');
    if (name) groupDraft.name = name.value;
    if (avatar) groupDraft.avatar = avatar.value;
    if (desc) groupDraft.description = desc.value;
    if (mode) groupDraft.mode = mode.value;
    if (speakers) groupDraft.max_speakers = Number(speakers.value) || 4;
    if (search) groupDraft.search = search.value;
  }

  function groupModeHint(mode, speakers) {
    if (mode === 'roundtable') return `成员可各自应答，@成员 点名；每条消息最多 ${speakers || 4} 个角色发言。`;
    if (mode === 'single') return '群只作为角色预设，消息由主持角色单独处理。';
    return '主持接收消息，按需把子任务委派给成员；只有被委派时成员才行动。';
  }

  function renderGroupDrawer() {
    if (!groupDrawerTeam || !groupDraft) return;
    const drawer = $('#sideDrawer');
    const body = $('#sideDrawerBody');
    const foot = $('#sideDrawerFoot');
    if (!drawer || !body || !foot) return;
    const leader = groupDraft.leader || null;
    const members = groupDraft.members || [];
    const all = [];
    if (leader && leader.id) all.push({ ...leader, isLeader: true });
    members.forEach((m) => { if (m && m.id && m.id !== (leader && leader.id)) all.push({ ...m, isLeader: false }); });
    const used = new Set(all.map((m) => m.id));
    const usedNames = new Set(all.map((m) => roleNameKey(m.name)).filter(Boolean));
    const q = String(groupDraft.search || '').trim().toLowerCase();
    // 邀请列表排除已在群里的成员：id 相同或名字相同都算已加入，避免同名角色重复出现。
    const available = S.roles.filter((r) => r && r.id && !used.has(r.id)
      && !usedNames.has(roleNameKey(r.name))
      && (!q || `${r.name || ''} ${r.expertise || ''} ${r.id}`.toLowerCase().includes(q)));
    const memberRows = all.length ? all.map((m) => `<div class="group-member" data-gm-row="${h(m.id)}">
      ${av(m.name, 'round', m.avatar)}
      <div class="group-member-main">
        <div class="group-member-name"><span>${h(m.name)}</span>${m.isLeader ? '<span class="tag grp">群主</span>' : ''}</div>
        <div class="group-member-sub">${h([m.title, m.department, m.expertise].filter(Boolean).join(' · ') || (m.model_code ? `模型：${m.model_code}` : '群成员'))}</div>
      </div>
      ${m.isLeader ? '' : `<div class="group-member-actions">
        <button class="btn small ghost" type="button" data-gm-leader="${h(m.id)}">设为主持</button>
        <button class="iconbtn" type="button" data-gm-remove="${h(m.id)}" title="移出群聊" aria-label="移出群聊"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13"/></svg></button>
      </div>`}
    </div>`).join('') : '<div class="group-empty">还没有群成员。从下面邀请一个角色。</div>';
    const inviteRows = available.length ? available.map((r) => `<div class="group-role-row" data-group-role="${h(r.id)}">
      ${av(r.name, 'round', r.avatar)}
      <div class="row-main"><div class="row-title">${h(r.name)}${r.scope === 'temporary' ? ' <span class="tag tmp">临时</span>' : ''}</div>
        <div class="row-sub">${h([r.title, r.department, r.expertise || r.persona].filter(Boolean).join(' · ') || '没有补充说明')}</div></div>
      <button class="btn small" type="button" data-gm-invite="${h(r.id)}">邀请</button>
    </div>`).join('') : `<div class="group-empty">${q ? '没有匹配的角色' : '角色库里的角色都已加入'}</div>`;
    const canInvite = members.length < 12;
    $('#sideDrawerSub').textContent = `${groupDrawerTeam.name} · ${all.length} 人`;
    body.innerHTML = `<div class="group-identity">
      ${avatarFieldHtml('groupAvatar', 'team', groupDraft.avatar || '', groupDraft.name || groupDrawerTeam.name)}
      <div class="group-identity-main">
        <label class="field"><label>群名称</label><input class="input" id="groupNameInput" value="${h(groupDraft.name)}" maxlength="120" /></label>
        <label class="field"><label>群介绍</label><textarea class="input" id="groupDescInput" rows="2" maxlength="1000" placeholder="这个群一起做什么">${h(groupDraft.description || '')}</textarea></label>
      </div>
    </div>
    <section class="group-section">
      <div class="group-section-head"><h3>群成员</h3><span>${all.length} / 13 人 · 群主 1 人</span></div>
      <div class="group-member-list">${memberRows}</div>
    </section>
    <section class="group-section">
      <div class="group-section-head"><h3>邀请成员</h3><span>从角色库添加</span></div>
      <label class="search group-invite-search">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>
        <input id="groupInviteSearch" placeholder="搜索角色…" autocomplete="off" value="${h(groupDraft.search || '')}" />
      </label>
      <div class="group-invite-list">${canInvite ? inviteRows : '<div class="group-empty">群成员已达上限（12 个成员 + 1 个主持）</div>'}</div>
    </section>
    <section class="group-section">
      <div class="group-section-head"><h3>群设置</h3><span>协作方式与发言上限</span></div>
      <label class="field"><label>协作方式</label>
        <select class="input" id="groupModeSelect">
          <option value="orchestrate"${groupDraft.mode === 'orchestrate' ? ' selected' : ''}>主持编排（默认）</option>
          <option value="roundtable"${groupDraft.mode === 'roundtable' ? ' selected' : ''}>圆桌（成员各自应答）</option>
          <option value="single"${groupDraft.mode === 'single' ? ' selected' : ''}>单角色（只作预设）</option>
        </select>
      </label>
      <label class="field range-field"><span><span>圆桌发言上限</span><output id="groupSpeakersVal">${groupDraft.max_speakers || 4}</output></span>
        <input class="range-input" type="range" id="groupSpeakers" min="1" max="12" step="1" value="${groupDraft.max_speakers || 4}" />
      </label>
      <div class="group-mode-hint" id="groupModeHint">${h(groupModeHint(groupDraft.mode, groupDraft.max_speakers))}</div>
    </section>
    <section class="group-section group-danger">
      <div class="group-section-head"><h3>解散群聊</h3></div>
      <p class="group-mode-hint">删除群组后，绑定会话会退回单聊；群成员角色本身不会被删除。</p>
      <button class="btn danger" type="button" data-group-delete>解散群聊</button>
    </section>`;
    bindAvatarField('groupAvatar', 'team', groupDraft.name || groupDrawerTeam.name);
    foot.innerHTML = `<button class="btn" type="button" data-group-open>打开群聊</button><button class="btn" type="button" data-group-superagent>新建工单</button><span class="spacer"></span><button class="btn" type="button" data-group-cancel>取消</button><button class="btn primary" type="button" id="groupSaveBtn">保存修改</button>`;
    drawer.hidden = false;
    const scrim = $('#drawerScrim');
    if (scrim) scrim.hidden = false;
  }

  function openGroupDetail(teamId) {
    const team = S.teams.find((t) => t.id === teamId);
    if (!team) { toast('群组不存在', 'bad'); return; }
    groupDrawerTeam = team;
    const leader = team.leader ? groupRoleBrief(team.leader) : null;
    // 成员按 id / 名字去重，并排除主持（旧数据可能把主持也写进 members）。
    const leaderId = leader && leader.id;
    const leaderName = roleNameKey(leader && leader.name);
    const seenIds = new Set();
    const seenNames = new Set();
    const members = (team.members || [])
      .map(groupRoleBrief)
      .filter((m) => {
        const key = roleNameKey(m.name);
        if (!m.id || m.id === leaderId || (key && key === leaderName)) return false;
        if (seenIds.has(m.id) || (key && seenNames.has(key))) return false;
        seenIds.add(m.id);
        if (key) seenNames.add(key);
        return true;
      });
    groupDraft = {
      name: team.name || '',
      avatar: team.avatar || '',
      description: team.description || '',
      mode: team.mode || 'orchestrate',
      max_speakers: Number(team.max_speakers) || 4,
      leader,
      members,
      search: '',
    };
    renderGroupDrawer();
  }

  async function saveGroupDrawer() {
    if (!groupDrawerTeam || !groupDraft) return;
    syncGroupDraft();
    const name = String(groupDraft.name || '').trim();
    if (!name) { toast('群名不能为空', 'bad'); return; }
    const leader = groupDraft.leader ? groupRoleBrief(groupDraft.leader) : null;
    const leaderId = leader && leader.id;
    const leaderName = roleNameKey(leader && leader.name);
    const seenIds = new Set();
    const seenNames = new Set();
    const members = (groupDraft.members || [])
      .map(groupRoleBrief)
      .filter((m) => {
        const key = roleNameKey(m.name);
        if (!m.id || m.id === leaderId || (key && key === leaderName)) return false;
        if (seenIds.has(m.id) || (key && seenNames.has(key))) return false;
        seenIds.add(m.id);
        if (key) seenNames.add(key);
        return true;
      });
    const payload = {
      name,
      avatar: String(groupDraft.avatar || ''),
      description: String(groupDraft.description || '').trim(),
      mode: groupDraft.mode,
      max_speakers: groupDraft.max_speakers,
      leader,
      members,
    };
    const btn = $('#groupSaveBtn');
    if (btn) btn.disabled = true;
    try {
      await api(`/teams/${encodeURIComponent(groupDrawerTeam.id)}`, {
        method: 'PATCH', body: JSON.stringify(payload),
      });
      const id = groupDrawerTeam.id;
      closeSideDrawer();
      await loadAll();
      toast('群聊设置已保存', 'ok');
      if (S.section === 'group') S.selectedGroup = id;
    } catch (err) {
      toast(err.message, 'bad');
      if (btn) btn.disabled = false;
    }
  }

  async function deleteGroupDrawer() {
    if (!groupDrawerTeam) return;
    if (!confirm(`解散群组「${groupDrawerTeam.name}」？绑定会话会退回单聊。`)) return;
    try {
      await api(`/teams/${encodeURIComponent(groupDrawerTeam.id)}`, { method: 'DELETE' });
      closeSideDrawer();
      await loadAll();
      toast('群组已解散', 'ok');
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── / 斜杠命令 ──────────────────────────────────────── */
  const ACTION_COMMANDS = [
    { name: 'new', description: '新建一个会话', action: () => newConversation() },
    { name: 'compact', description: '压缩当前会话上下文', action: (w) => compactConversation(w) },
    { name: 'clear', description: '清空模型上下文（历史消息保留，可回看）', argument_hint: '[all|current]' },
    { name: 'work', description: '交给 工单', argument_hint: '[标题]' },
  ];

  async function ensureCommands() {
    if (S.commands) return;
    try {
      const data = await api(`/app/commands?workspace_id=${encodeURIComponent(S.selectedWs || '')}`);
      S.commands = [
        ...ACTION_COMMANDS.map((c) => ({
          ...c, source: 'action', builtin: true,
        })),
        ...(data.commands || []),
      ];
    } catch {
      S.commands = ACTION_COMMANDS.map((c) => ({ ...c, source: 'action', builtin: true }));
    }
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
    hideSlash(w);
    if (typeof item.action === 'function') {
      Promise.resolve(item.action(w)).catch((err) => toast(err.message, 'bad'));
      return;
    }
    const ta = $('.composer textarea', w.el);
    ta.value = `/${item.name} `;
    try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch { /* ignore */ }
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
    const pop = $('.member-pop', w.el);
    if (!pop) return;
    const hit = mentionHit(w);
    if (!hit || !w.members.length) { hideMention(w); return; }
    const q = hit.query.toLowerCase();
    const items = w.members.filter((m) => !q || String(m.name || '').toLowerCase().includes(q)).slice(0, 8);
    if (!items.length) { hideMention(w); return; }
    w.mention = { ...hit, items, index: 0 };
    pop.innerHTML = `<div class="mention-head">@ 成员</div>` + items.map((m, i) =>
      `<button class="mention-item${i === 0 ? ' active' : ''}" data-mention="${i}">${av(m.name, 'xs round', m.avatar)}<b>${h(m.name)}</b><span>${h([m.title, m.department, m.expertise].filter(Boolean).join(' · '))}</span></button>`).join('');
    pop.hidden = false;
    $$('[data-mention]', pop).forEach((el) => el.addEventListener('mousedown', (e) => {
      e.preventDefault(); chooseMention(w, Number(el.dataset.mention));
    }));
  }

  function hideMention(w) {
    const pop = $('.member-pop', w.el);
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

  /* ── 工单：待授权 / 通知 / 授权管理 ─────────────── */
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
    if (fab) fab.title = `工单 · 待授权 ${auth} · 未读 ${note} · 运行 ${Number(c.running || 0)}`;
  }

  function maybeNotifyAttention(counts) {
    const count = Number((counts || {}).authorizations || 0);
    if (S.gaNotifyCount < 0) { S.gaNotifyCount = count; return; }
    if (count > S.gaNotifyCount && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      try {
        const note = new Notification('AtlasX · 工单', {
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
      if (changed && S.section === 'work' && $('#modal').hidden) renderCanvas();
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
          toast(`工单已代批：${a.tool || '低风险工具'}`, 'ok', 1800);
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
      <label class="ga-toggle"><input type="checkbox" id="gaAutoApprove"${S.gaAuto ? ' checked' : ''} /> 工单代处理低风险授权</label>
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
        <span class="ga-title"><b>工单</b><span>待授权 ${auth} · 未读 ${note} · 运行 ${Number(counts.running || 0)}</span></span>
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
      toast(S.gaAuto ? '工单将代处理低风险授权' : '已关闭代处理', 'ok', 1600);
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
  bindAgentCanvas();
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('#agentCanvas') && !$('#agentCanvas').hidden) closeAgentCanvas();
  });
  const themeToggle = $('#themeToggle');
  if (themeToggle) themeToggle.addEventListener('click', toggleTheme);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) tickTheme();
  });

  // 群聊设置抽屉：成员邀请 / 移出 / 转让主持 / 群资料 / 协作方式。
  const sideDrawer = $('#sideDrawer');
  if (sideDrawer) {
    sideDrawer.addEventListener('click', (e) => {
      if (e.target.closest('[data-role-close]')) { closeSideDrawer(); return; }
      if (e.target.closest('[data-role-chat]') && roleDrawerRole) {
        const id = roleDrawerRole.id;
        closeSideDrawer();
        openRoleChat(id);
        return;
      }
      if (e.target.closest('[data-role-edit]') && roleDrawerRole) {
        const role = roleDrawerRole;
        closeSideDrawer();
        openRoleEditor(role);
        return;
      }
      if (e.target.closest('[data-role-superagent]') && roleDrawerRole) {
        const role = roleDrawerRole;
        closeSideDrawer();
        newWorkItem({ role_id: role.id, body: `来自角色「${role.name}」\n\n` });
        return;
      }
      if (e.target.closest('[data-role-promote]')) { promoteRoleDrawer(); return; }
      if (e.target.closest('[data-role-delete]')) { deleteRoleDrawer(); return; }
      const remove = e.target.closest('[data-gm-remove]');
      if (remove && groupDraft) {
        syncGroupDraft();
        groupDraft.members = groupDraft.members.filter((m) => m.id !== remove.dataset.gmRemove);
        renderGroupDrawer();
        return;
      }
      const leader = e.target.closest('[data-gm-leader]');
      if (leader && groupDraft) {
        syncGroupDraft();
        const target = groupDraft.members.find((m) => m.id === leader.dataset.gmLeader);
        if (!target) return;
        const old = groupDraft.leader;
        groupDraft.members = groupDraft.members.filter((m) => m.id !== target.id);
        if (old && old.id && old.id !== target.id && !groupDraft.members.some((m) => m.id === old.id)) {
          groupDraft.members.push(old);
        }
        groupDraft.leader = target;
        renderGroupDrawer();
        return;
      }
      const invite = e.target.closest('[data-gm-invite]');
      if (invite && groupDraft) {
        syncGroupDraft();
        if (groupDraft.members.length >= 12) { toast('群成员已达上限', 'bad'); return; }
        const role = S.roles.find((r) => r.id === invite.dataset.gmInvite);
        if (!role) return;
        const key = roleNameKey(role.name);
        const leader = groupDraft.leader;
        if ((groupDraft.members || []).some((m) => m.id === role.id
              || (key && roleNameKey(m.name) === key))
            || (leader && (leader.id === role.id
              || (key && roleNameKey(leader.name) === key)))) return;
        groupDraft.members.push(groupRoleBrief(role));
        renderGroupDrawer();
        return;
      }
      if (e.target.closest('[data-group-delete]')) { deleteGroupDrawer(); return; }
      if (e.target.closest('[data-group-superagent]') && groupDrawerTeam) {
        const team = groupDrawerTeam;
        closeSideDrawer();
        newWorkItem({ team_id: team.id, body: `来自群组「${team.name}」\n\n` });
        return;
      }
      if (e.target.closest('[data-group-cancel]')) { closeSideDrawer(); return; }
      if (e.target.closest('[data-group-open]') && groupDrawerTeam) {
        const id = groupDrawerTeam.id;
        closeSideDrawer();
        setSection('group');
        openGroupChat(id);
        return;
      }
      if (e.target.closest('#groupSaveBtn')) { saveGroupDrawer(); return; }
    });
    sideDrawer.addEventListener('input', (e) => {
      if (!groupDraft) return;
      if (e.target.id === 'groupInviteSearch') {
        groupDraft.search = e.target.value;
        const q = groupDraft.search.trim().toLowerCase();
        $$('.group-role-row', $('#sideDrawerBody')).forEach((row) => {
          row.hidden = !!q && !row.textContent.toLowerCase().includes(q);
        });
      } else if (e.target.id === 'groupSpeakers') {
        groupDraft.max_speakers = Number(e.target.value) || 4;
        const out = $('#groupSpeakersVal');
        if (out) out.textContent = String(groupDraft.max_speakers);
        const hint = $('#groupModeHint');
        if (hint) hint.textContent = groupModeHint(groupDraft.mode, groupDraft.max_speakers);
      } else if (e.target.id === 'groupNameInput') {
        groupDraft.name = e.target.value;
        const sub = $('#sideDrawerSub');
        if (sub && groupDrawerTeam) sub.textContent = `${e.target.value || groupDrawerTeam.name} · ${(groupDraft.members || []).length + (groupDraft.leader && groupDraft.leader.id ? 1 : 0)} 人`;
      } else if (e.target.id === 'groupDescInput') {
        groupDraft.description = e.target.value;
      }
    });
    sideDrawer.addEventListener('change', (e) => {
      if (!groupDraft) return;
      if (e.target.id === 'groupModeSelect') {
        groupDraft.mode = e.target.value;
        const hint = $('#groupModeHint');
        if (hint) hint.textContent = groupModeHint(groupDraft.mode, groupDraft.max_speakers);
      }
    });
    const closeBtn = $('#sideDrawerClose');
    if (closeBtn) closeBtn.addEventListener('click', closeSideDrawer);
    const scrim = $('#drawerScrim');
    if (scrim) scrim.addEventListener('click', closeSideDrawer);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sideDrawer && !sideDrawer.hidden) closeSideDrawer();
    });
  }

  $('#listAdd').addEventListener('click', () => {
    if (S.section === 'work') newWorkItem();
    else if (S.section === 'role') openRoleEditor(null);
    else if (S.section === 'group') newGroup();
    else if (S.section === 'conv') newConversation();
    else if (S.section === 'ws') addWorkspace();
    else toast('该入口将在下一阶段迁移');
  });
  $('#listRefresh').addEventListener('click', async () => {
    if (S.section === 'work') await loadWorkItems();
    else await loadAll();
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
        <div class="field"><label>群头像</label>${avatarFieldHtml('ngAvatar', 'team', '', '群')}</div>
        <div class="field"><label>协作方式</label><select class="input" id="ngMode">
          <option value="orchestrate">主持编排</option><option value="roundtable">圆桌</option><option value="single">单角色</option>
        </select></div>
        <div class="field"><label>成员</label><div style="display:flex;flex-direction:column;gap:6px">
        ${S.roles.map((r) => `<label class="member"><input type="checkbox" data-ng-member="${h(r.id)}" />
          ${av(r.name, 'round', r.avatar)}<span class="row-main"><span class="row-title">${h(r.name)}</span><span class="row-sub">${h([r.title, r.department, r.expertise].filter(Boolean).join(' · '))}</span></span></label>`).join('')}
        </div></div>`,
      onMount: () => bindAvatarField('ngAvatar', 'team', '群'),
      read: () => ({
        name: $('#ngName').value.trim(),
        avatar: $('#ngAvatar').value || '',
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
      const team = await api('/teams', { method: 'POST', body: JSON.stringify(saved) });
      await loadAll(); toast('群组已创建', 'ok');
      if (team && team.id) openGroupDetail(team.id);
    } catch (err) { toast(err.message, 'bad'); }
  }

  // 初始化（?section=role&conv=conv-xxx 便于深链 / 截图；?pick=ws 直接打开目录选择器）
  initTheme();
  applySectionLayout(S.section);
  loadAttention();
  loadWorkItems({ quiet: true });
  setInterval(() => { if (!document.hidden) loadWorkItems({ quiet: true }); }, 10000);
  setInterval(() => { if (!document.hidden) refreshTeamTasks(); }, 2500);
  loadAll().then(() => {
    const p = new URLSearchParams(location.search);
    if (p.get('work')) S.workSelected = p.get('work');
    if (p.get('section')) setSection(p.get('section'));
    const conv = p.get('conv');
    if (conv) openConversation(conv);
    if (p.get('pick') === 'ws') setTimeout(() => addWorkspace(), 200);
  }).catch((err) => toast(err.message, 'bad'));
})();

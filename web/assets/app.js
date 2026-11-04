/* ==========================================================================
   AtlasX — static chat client
   Vanilla JS, hash routing, SSE run streaming. No bundler, no framework.
   ========================================================================== */

(() => {
  'use strict';

  const API = '/api/agent-zero';
  const TERMINAL = new Set(['done', 'failed', 'stopped']);
  // 附件：与后端 attachment_service 的限制保持一致。
  const MAX_ATTACHMENTS = 8;
  const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;
  const MAX_ATTACHMENTS_TOTAL = 50 * 1024 * 1024;
  const LONG_TEXT_CONFIRM_CHARS = 6000;
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
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

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
  // 任务清单不在 dock 体系里：它是对话区左上角的常驻浮动面板（进程面板下方），
  // 否则 dock 一收起，任务清单就连入口带内容一起消失。
  const DOCK_PANELS = ['workspace', 'viewer', 'vcs', 'mem', 'codegraph'];

  function loadJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return { ...fallback };
      const parsed = JSON.parse(raw);
      return { ...fallback, ...parsed };
    } catch (err) {
      return { ...fallback };
    }
  }

  const state = {
    app: null,
    settings: null,
    workspace: null,
    workspaces: [],
    workspacesAll: [],
    workspaceLoading: false,
    wsMenuPage: 0,
    wsMenuItems: [],
    wsMenuTotal: 0,
    wsFilter: '',
    pvCode: new Set(),
    chatItems: [],
    chatTotal: 0,
    chatLoadingMore: false,
    chatFillBusy: false,
    activeRuns: [],
    processes: [],
    processDetailId: '',
    processDetailItem: null,
    conversations: [],
    convTotal: 0,
    convLimit: 10,
    convToken: 0,
    searchTimer: null,
    picker: { open: false, page: 1, pageSize: 10, total: 0, items: [], archived: false, wsId: '' },
    addWs: { path: '', nameTouched: false, busy: false, loading: false, parent: null },
    currentId: null,
    conv: null,
    messages: [],
    live: null,
    run: null,
    running: false,
    cursor: 0,
    es: null,
    loadToken: 0,
    view: 'empty',
    filter: '',
    mode: 'autonomous',
    permMode: 'once',
    theme: (window.AtlasTheme ? AtlasTheme.readTheme() : (localStorage.getItem('az-theme') || 'dark')),
    themeSchedule: (window.AtlasTheme ? AtlasTheme.readSchedule()
      : loadJson('az-theme-schedule', { mode: 'manual', day: 'light', night: 'dark', interval: 15, dayStart: '06:00', nightStart: '19:00' })),
    scheduleTimer: null,
    ui: (() => {
      const ui = loadJson('az-ui', {
        dockW: 340,
        dockCollapsed: true,
        active: null,
        openTabs: [],
        memView: 'list',
        codegraphView: 'tree',
        sidebarW: 276,
        // 左侧面板本身不收缩（用户可手动折叠）。
        sidebarCollapsed: false,
        // 侧栏 tab：chat / workspace；上次的选择作为下次进入的默认。
        sideTab: 'chat',
        sections: {},
        queueCollapsed: false,
        // 进程面板（左侧栏）：用户点 chevron 才收缩；没有进程时整块自动隐藏。
        processCollapsed: false,
        // 任务清单面板（进程面板下方）：开合与折叠都由用户手动控制。
        todoCollapsed: false,
        todoOpen: false,
        viewerWrap: false,
        // 工作区项目默认全部收起；展开状态只在本次运行内保留，
        // 下次进入仍从收起开始（不持久化到 localStorage / 服务端）。
        wsOpen: {},
      });
      ui.wsOpen = {};
      return ui;
    })(),
    langMode: localStorage.getItem('az-lang') || 'system',
    lang: 'en',
    models: [],
    modelPresets: [],
    subscriptions: [],
    selectedSubscription: '',
    modelMenuLevel: 'subscriptions',
    modelMenuSubscription: '',
    modelMenuQuery: '',
    subscriptionFilter: '',
    modelFilter: '',
    discoverFilter: Object.create(null),
    quotaCache: Object.create(null),
    quotaPending: new Set(),
    usageStats: null,
    convUsage: null,
    // 本轮最后的上下文估算值：run 结束后 live 会被清空，顶部读数还要接着显示到
    // 下一次真实 usage 落库为止（否则收尾瞬间会往回跳一截）。
    ctxTail: 0,
    usagePeriod: '7d',
    usageLoading: false,
    defaultRemoved: false,
    activeModel: 'default',
    openModelCards: new Set(),
    openDetails: new Map(),
    stick: true,
    ws: { open: false, path: '.', entries: [], loading: false },
    viewer: {
      open: false,
      path: '',
      data: null,
      downloadUrl: '',
      mode: 'code',
      wrap: false,
      editor: null,
    },
    runtime: null,
    runtimeOnline: null,
    pendingApprovals: [],
    pendingQuestion: null,
    pendingQuestions: {},
    subagents: new Map(),
    subagentOpen: null,
    mcp: null,
    skills: null,
    commands: null,
    hooks: null,
    slash: { open: false, index: 0, items: [] },
    queue: [],
    queueRunning: null,
    queueTimer: null,
    attachments: [],
    refs: [],
    thinkingLevel: '',
    goal: null,
    goalDismissed: false,
    todos: [],
    todo: { open: false },
    todoCardOpen: true,
    editingMsg: null,
    rewind: null,
  };

  const I18N = {
    en: {
      'app.tagline': 'local workspace',
      'sidebar.close': 'Close sidebar',
      'sidebar.collapse': 'Collapse sidebar',
      'dock.drag': 'Drag to resize',
      'dock.collapse': 'Collapse panel',
      'dock.expand': 'Expand panel',
      'dock.closeTab': 'Close tab',
      'dock.closeOthers': 'Close other tabs',
      'dock.closeAll': 'Close all tabs',
      'settings.appearance': 'Appearance',
      'appearance.schedule': 'Theme schedule',
      'appearance.scheduleManual': 'Manual',
      'appearance.scheduleDayNight': 'Day / night',
      'appearance.scheduleRotate': 'Rotate',
      'appearance.dayTheme': 'Day theme',
      'appearance.nightTheme': 'Night theme',
      'appearance.dayStart': 'Day starts',
      'appearance.nightStart': 'Night starts',
      'appearance.rotateEvery': 'Rotate every',
      'dock.tabWorkspace': 'Workspace',
      'dock.tabViewer': 'Viewer',
      'dock.tabVcs': 'Version control',
      'dock.tabMem': 'Memory',
      'dock.tabCodegraph': 'CodeGraph',
      'dock.tabTodo': 'Task list',
      'appearance.resetLayout': 'Reset layout',
      'appearance.layoutNote': "Each panel opened from a workspace, memory, version control, or task action gets its own tab. Drag tabs to reorder; use a tab's close button to close it. The chevron in the tab bar collapses the dock, and the arrow on the rail expands it. Drag the dock or sidebar edge to resize. All interface settings are saved globally on the server (with a local browser copy as fallback).",
      'appearance.noteManual': 'Theme stays as selected.',
      'appearance.noteDayNight': 'Uses {day} from {dayStart} to {nightStart}, otherwise {night}.',
      'appearance.noteRotate': 'Cycles through all themes every {interval} minutes.',
      'appearance.layoutReset': 'Layout reset',
      'appearance.details': 'Interface details',
      'appearance.sidebarWidth': 'Sidebar width',
      'appearance.dockWidth': 'Dock width',
      'appearance.sidebarCollapsed': 'Collapsed sidebar',
      'appearance.chatSection': 'Chat section expanded',
      'appearance.workspaceSection': 'Workspace section expanded',
      'appearance.queueBar': 'Queue bar expanded',
      'appearance.viewerWrap': 'Wrap long lines in viewer',
      'theme.dark': 'Dark', 'theme.light': 'Light', 'theme.midnight': 'Midnight', 'theme.slate': 'Slate', 'theme.ocean': 'Ocean', 'theme.forest': 'Forest', 'theme.sepia': 'Sepia', 'theme.violet': 'Violet',
      'sidebar.open': 'Open sidebar',
      'newConversation': 'New conversation',
      'search.placeholder': 'Search conversations',
      'nav.workspace': 'Workspace files',
      'nav.settings': 'Settings',
      'runtime.title': 'Runtime status',
      'runtime.connecting': 'Connecting…',
      'theme.toggle': 'Toggle theme',
      'runState.working': 'Working',
      'runState.queued': 'Queued',
      'activity.working': 'Working…',
      'prompt.placeholder': 'Message {app}…',
      'mode.label': 'Run mode',
      'mode.chat': 'chat',
      'mode.autonomous': 'agent',
      'mode.goal': 'goal',
      'mode.goalTitle': 'Goal mode — keep running until the goal is reached',
      'queue.title': 'Queue',
      'queue.count': '{n} waiting',
      'queue.moveUp': 'Move up',
      'queue.moveUpTitle': 'Run this one earlier',
      'queue.moveDown': 'Move down',
      'queue.moveDownTitle': 'Run this one later',
      'queue.inject': 'Stop & continue',
      'queue.injectTitle': 'Stop the current reply and run this message next',
      'queue.edit': 'Edit',
      'queue.editTitle': 'Edit this queued message',
      'queue.cancel': 'Cancel',
      'queue.queued': 'Queued (#{n}). It will run after the current reply.',
      'queue.cancelled': 'Queued message cancelled.',
      'queue.movedUp': 'Moved up.',
      'queue.movedDown': 'Moved down.',
      'queue.atTop': 'Already first in the queue.',
      'queue.atBottom': 'Already last in the queue.',
      'queue.injected': 'Inserted into the running reply.',
      'queue.stopContinue': 'Stopped the current reply; this message runs next.',
      'queue.failed': 'Queue action failed: {msg}',
      'attach.title': 'Attach image or document',
      'attach.titleVision': 'Attach image, document, or code file',
      'attach.add': 'Add attachment',
      'attach.remove': 'Remove attachment',
      'attach.tooMany': 'At most {n} attachments per message.',
      'attach.tooLarge': '{name} is larger than 25 MB.',
      'attach.totalLarge': 'Attachments exceed 50 MB in total.',
      'attach.unsupported': 'Unsupported file: {name}.',
      'attach.imageNeedsVision': 'The current model does not support images. Enable image support in Settings → Models or switch models.',
      'attach.imageRemoved': 'Current model does not support images; image attachment removed.',
      'attach.longConfirm': 'This message is {n} characters long. Convert the full text into a txt attachment (the text stays in the composer so you can review it before sending)?',
      'attach.longConfirmTitle': 'Send long text as txt?',
      'attach.longConfirmOk': 'Convert to txt',
      'attach.longConfirmCancel': 'Keep as text',
      'attach.convertedReady': 'Converted to {name}. Review it, then press Enter again to send.',
      'attach.longHint': '{n} characters — you can send it as a txt document',
      'attach.fullInFile': 'The full text is in the attachment {name}.',
      'attach.parseFailed': 'Attachment failed: {msg}',
      'attach.converted': 'Long text sent as a txt attachment.',
      'attach.convertedShort': 'txt document',
      'attach.only': 'Please review the attachment.',
      'attach.visionBadge': 'Vision',
      'attach.visionBadgeTitle': 'This model can read uploaded images',
      'ref.title': 'Reference in chat',
      'ref.added': 'Referenced {name}.',
      'ref.duplicate': 'Already referenced: {name}',
      'ref.remove': 'Remove reference',
      'ref.label': 'References',
      'ref.only': 'Please review the referenced files.',
      'thinking.title': 'Thinking level for this message',
      'thinking.label': 'Thinking level',
      'thinking.chipTitle': 'Thinking strength · {level}',
      'thinking.default': 'Default',
      'thinking.minimal': 'Minimal',
      'thinking.low': 'Low',
      'thinking.medium': 'Medium',
      'thinking.high': 'High',
      'goal.label': 'Goal',
      'goal.budget': 'Budget',
      'goal.budgetTitle': 'Token budget (0 = unlimited)',
      'goal.end': 'End goal',
      'goal.runs': '{used}/{max} runs',
      'goal.tokens': '{used} tokens',
      'goal.tokensBudget': '{used}/{budget} tokens',
      'goal.status.achieved': 'achieved',
      'goal.status.exhausted': 'budget used up',
      'goal.status.closed': 'ended',
      'goal.ended': 'Goal ended. Auto-continue stopped.',
      'goal.saved': 'Goal budget saved.',
      'goal.empty': 'No goal yet — send a message in goal mode to start one.',
      'goal.dismiss': 'Hide goal bar',
      'todo.title': 'Task list',
      'todo.cardTitle': 'Tasks',
      'todo.placeholder': 'Add a task…',
      'todo.add': 'Add',
      'todo.empty': 'No tasks yet. Ask the agent to plan the work, or add one here.',
      'todo.summary': '{doing} in progress · {pending} pending',
      'todo.summaryBar': '{done} of {total} done',
      'todo.foot': '{completed}/{total} completed',
      'todo.status.pending': 'pending',
      'todo.status.in_progress': 'in progress',
      'todo.status.completed': 'completed',
      'todo.openPanel': 'Open task list',
      'todo.allDone': 'All done',
      'todo.added': 'Task added.',
      'todo.deleted': 'Task deleted.',
      'todo.failed': 'Task update failed: {msg}',
      'perm.label': 'Permission mode',
      'perm.once': 'Ask',
      'perm.auto': 'Auto',
      'perm.always': 'Allow',
      'scroll.top': 'Scroll to top',
      'scroll.bottom': 'Scroll to bottom',
      'outline.title': 'Context',
      'outline.count': '{n} prompts',
      'outline.expand': 'Expand context outline',
      'outline.collapse': 'Collapse context outline',
      'outline.jump': 'Jump to this prompt',
      'model.title': 'Current model',
      'send.title': 'Send · Enter (double-click / long-press = send as-is)',
      'send.aria': 'Send',
      'stop.title': 'Force stop run',
      'stop.aria': 'Force stop run',
      'composer.hint': '<kbd>Enter</kbd> send · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line',
      'composer.imeConfirm': 'Press Enter again to send (a candidate was just committed).',
      'composer.local': 'local',
      'composer.workspaceHint': '{name} · local',
      'workspace.title': 'Workspace',
      'workspace.refresh': 'Refresh',
      'workspace.filter': 'Filter workspaces',
      'workspace.noMatch': 'No matching workspaces',
      'workspace.none': 'No workspaces yet',
      'chatSection.title': 'Chat',
      'chat.noMatch': 'No matching conversations',
      'conv.newChat': 'New chat conversation',
      'conv.newInWorkspace': 'New conversation in workspace',
      'workspace.parent': 'Parent folder',
      'workspace.loading': 'Loading…',
      'workspace.empty': 'This folder is empty.<br>Files created by {app} will appear here.',
      'workspace.open': 'open',
      'workspace.view': 'view',
      'workspace.switch': 'Switch workspace',
      'workspace.add': 'Add workspace',
      'workspace.collapseAll': 'Collapse all workspaces',
      'workspace.manage': 'Manage workspaces',
      'workspace.name': 'Name',
      'workspace.namePlaceholder': 'Workspace name',
      'workspace.folder': 'Folder',
      'workspace.parentFolder': 'Parent folder',
      'workspace.go': 'Go',
      'workspace.chooseNative': 'Choose in Finder',
      'workspace.chooseNative': 'Choose in Finder',
      'filemenu.open': 'Open',
      'filemenu.reveal': 'Reveal in Finder',
      'filemenu.copyPath': 'Copy path',
      'filemenu.rename': 'Rename',
      'filemenu.delete': 'Delete',
      'filemenu.download': 'Download',
      'filemenu.newFile': 'New file',
      'filemenu.newFolder': 'New folder',
      'filemenu.refresh': 'Refresh',
      'filemenu.renamePrompt': 'New name for “{name}”',
      'filemenu.newFilePrompt': 'New file name',
      'filemenu.newFolderPrompt': 'New folder name',
      'filemenu.deleteConfirm': 'Delete “{name}”? This cannot be undone.',
      'filemenu.copied': 'Path copied.',
      'filemenu.revealed': 'Opened in file manager.',
      'filemenu.done': 'Done.',
      'filemenu.failed': 'Operation failed: {msg}',
      'menu.open': 'Open',
      'menu.copyTitle': 'Copy title',
      'menu.delete': 'Delete',
      'menu.cut': 'Cut',
      'menu.copy': 'Copy',
      'menu.paste': 'Paste',
      'menu.selectAll': 'Select all',
      'menu.save': 'Save',
      'menu.copyMessage': 'Copy message',
      'menu.copyAnswer': 'Copy answer',
      'menu.fork': 'Fork from here',
      'fork.title': 'Fork conversation',
      'fork.confirm': 'Create a new conversation with this message and everything before it? Workspace files are shared by both branches.',
      'fork.confirmAll': 'Create a new conversation with the whole history? Workspace files are shared by both branches.',
      'fork.action': 'Fork',
      'fork.done': 'Conversation forked.',
      'fork.failed': 'Fork failed: {msg}',
      'menu.copySelected': 'Copy selected',
      'menu.clipboardFailed': 'Could not read the clipboard.',
      'workspace.addTitle': 'Add workspace',
      'workspace.addSubtitle': 'Choose a local folder. Conversations and files stay bound to this workspace.',
      'workspace.useFolder': 'Use this folder',
      'workspace.folderLoading': 'Loading folders…',
      'workspace.folderEmpty': 'No subfolders here.',
      'workspace.folderCount': '{n} folders',
      'workspace.folderFailed': 'Could not read this folder.',
      'workspace.chatOnly': 'chat only',
      'workspace.chatOnlyNote': 'This workspace is chat only and has no working folder. Add a workspace that points at a local folder to use file and script tools.',
      'workspace.added': 'Workspace “{name}” added.',
      'workspace.addFailed': 'Could not add workspace: {msg}',
      'workspace.default': 'Default',
      'workspace.current': 'Current',
      'workspace.conversations': '{n} conversations',
      'workspace.switched': 'Switched to {name}.',
      'workspace.rename': 'Rename',
      'workspace.renamePrompt': 'Rename workspace',
      'workspace.renamed': 'Workspace renamed.',
      'workspace.renameFailed': 'Could not rename workspace: {msg}',
      'workspace.archive': 'Archive',
      'workspace.archiveConfirm': 'Archive workspace “{name}”? Conversations and files are kept.',
      'workspace.archivedToast': 'Workspace archived.',
      'workspace.archiveFailed': 'Could not archive workspace: {msg}',
      'workspace.restore': 'Restore',
      'workspace.delete': 'Delete (hide only)',
      'workspace.deleteConfirm': 'Delete workspace “{name}”? It is only hidden from the list; the folder, conversations and memory are kept. Re-adding the same folder restores it.',
      'workspace.deletedToast': 'Workspace removed from the list (files and conversations kept).',
      'workspace.restoredOnAdd': 'Restored workspace “{name}”.',
      'workspace.live': 'A task is running in this workspace',
      'workspace.restored': 'Workspace restored.',
      'workspace.restoreFailed': 'Could not restore workspace: {msg}',
      'workspace.rootMissing': 'Folder not found',
      'vcs.title': 'Version control',
      'vcs.tabStatus': 'Changes',
      'vcs.tabLog': 'History',
      'vcs.tabBranches': 'Branches',
      'vcs.tabRemotes': 'Remotes',
      'vcs.changedCount': '{n} uncommitted changes',
      'vcs.noChanges': 'No uncommitted changes',
      'vcs.commit': 'Commit',
      'vcs.commitMsgPlaceholder': 'Commit message…',
      'vcs.stage': 'Stage',
      'vcs.unstage': 'Unstage',
      'vcs.discard': 'Discard',
      'vcs.commited': 'Committed.',
      'vcs.staged': 'Staged.',
      'vcs.unstaged': 'Unstaged.',
      'vcs.discarded': 'Changes discarded.',
      'vcs.switch': 'Switch',
      'vcs.create': 'Create',
      'vcs.newBranchPlaceholder': 'New branch name…',
      'vcs.switched': 'Switched to {name}.',
      'vcs.none': 'This workspace has no git/svn repository',
      'vcs.noCommits': 'No commits yet',
      'vcs.failed': 'Version control failed: {msg}',
      'vcs.refresh': 'Refresh',
      'vcs.viewFlat': 'Flat list',
      'vcs.viewDir': 'Directory tree',
      'vcs.viewTree': 'Directory tree',
      'vcs.viewList': 'Flat list',
      'vcs.today': 'Today',
      'vcs.yesterday': 'Yesterday',
      'vcs.unknownDate': 'Unknown date',
      'vcs.merge': 'merge',
      'vcs.refTag': 'tag',
      'vcs.refHead': 'HEAD',
      'vcs.commitsOnDay': '{n} commits',
      'vcs.remotes': 'Remotes',
      'vcs.noRemotes': 'No remotes configured yet',
      'vcs.gitOnlyRemotes': 'Remote management is available for git repositories.',
      'vcs.addRemote': 'Add remote',
      'vcs.editRemote': 'Edit URL',
      'vcs.removeRemote': 'Remove',
      'vcs.removeRemoteConfirm': 'Remove remote “{name}”?',
      'vcs.remoteName': 'Name',
      'vcs.remoteUrl': 'URL',
      'vcs.remoteNamePlaceholder': 'origin',
      'vcs.remoteUrlPlaceholder': 'git@github.com:org/repo.git',
      'vcs.remoteAdded': 'Remote {name} added.',
      'vcs.remoteRemoved': 'Remote {name} removed.',
      'vcs.remoteSaved': 'Remote URL updated.',
      'vcs.fetch': 'Fetch',
      'vcs.fetching': 'Fetching…',
      'vcs.fetched': 'Fetched {name}.',
      'vcs.upstream': 'upstream',
      'vcs.tracking': 'tracks {branch}',
      'vcs.noTracking': 'no upstream',
      'vcs.commitCount': '{n} commits',
      'vcs.localBranches': 'Local branches',
      'vcs.remoteBranches': 'Remote branches',
      'mem.title': 'Memory',
      'mem.viewList': 'List',
      'mem.viewGraph': 'Graph',
      'mem.statTotal': 'Memories',
      'mem.statLinked': 'Linked',
      'mem.statEntities': 'Entities',
      'mem.statConfidence': 'Confidence',
      'mem.sideOverview': 'Overview',
      'mem.timeline': 'Activity · 30 days',
      'mem.graphOpen': 'Memory graph',
      'mem.statsPanel': 'Stats',
      'mem.kindDist': 'Type distribution',
      'mem.sourceDist': 'Sources',
      'mem.topTags': 'Top tags',
      'mem.topEntities': 'Most linked entities',
      'mem.export': 'Export as Markdown',
      'mem.rebuild': 'Rebuild from run history',
      'mem.tidy': 'Tidy up (merge duplicates, archive low-value)',
      'mem.tidyDone': 'Tidied: {merged} merged, {archived} archived.',
      'mem.includeArchived': 'Archived',
      'mem.archived': 'archived',
      'mem.archivedCount': 'archived',
      'mem.mergedCount': 'merged {n}',
      'mem.archiveDuplicate': 'duplicate merged',
      'mem.archiveStale': 'stale',
      'mem.archiveLow': 'low confidence',
      'mem.archiveOverflow': 'over limit',
      'mem.clear': 'Clear all memories',
      'mem.rebuildConfirm': 'Rebuild clears existing memories and distils them from conversation history in the background via the LLM (deterministic fallback if the LLM is unavailable). Continue?',
      'mem.rebuildRunning': 'Rebuilding in background',
      'mem.rebuildDone': 'Rebuild done: {n} memories distilled ({calls} LLM calls).',
      'mem.rebuildError': 'Rebuild failed: {msg}',
      'mem.clearConfirm': 'Clear all memories in this workspace? This cannot be undone.',
      'mem.rebuilt': 'Rebuilt: {n} memories recovered.',
      'mem.cleared': 'Memories cleared.',
      'mem.chips': '{total} memories · {entities} entities · linked {linked}',
      'mem.searchPlaceholder': 'Search memories',
      'mem.allKinds': 'All',
      'mem.sortRecent': 'Recent',
      'mem.sortOldest': 'Oldest',
      'mem.sortConf': 'Confidence',
      'mem.empty': 'No memories yet — run experience is captured automatically after every run.',
      'mem.emptyMatch': 'No memories match the filter.',
      'mem.auto': 'auto',
      'mem.manual': 'manual',
      'mem.fail': 'Memory operation failed: {msg}',
      'mem.linkedTo': 'linked to',
      'mem.graphLegend': 'Colours = memory type · squares = code entities · solid = “about” link · dashed = dependency',
      'mem.graphHint': 'Drag nodes or empty space to move, scroll to zoom, click a node for details.',
      'team.title': 'Agent teams',
      'team.tabTeams': 'Teams',
      'team.new': 'New team',
      'team.empty': 'No agent teams yet. Create one with a host plus specialist members.',
      'team.name': 'Team name',
      'team.desc': 'Description',
      'team.leader': 'Host (orchestrator)',
      'team.expertise': 'Expertise',
      'team.members': 'Members',
      'team.addMember': 'Add member',
      'team.save': 'Save',
      'team.delete': 'Delete',
      'team.bind': 'Use in current conversation',
      'team.unbind': 'Unbind from conversation',
      'team.boundToast': 'Team “{name}” bound to this conversation',
      'team.unboundToast': 'Team unbound from this conversation',
      'team.saved': 'Team saved',
      'team.deleted': 'Team deleted',
      'team.bindNeedConv': 'Open or start a conversation first',
      'team.nameRequired': 'Team name is required',
      'mem.graphZoomIn': 'Zoom in',
      'mem.graphZoomOut': 'Zoom out',
      'mem.graphZoomFit': 'Fit',
      'mem.graphFilterAll': 'All',
      'mem.graphFilterMem': 'Memories',
      'mem.graphFilterCode': 'Entities',
      'mem.graphEmpty': 'Nothing to draw yet: this workspace has no memories.',
      'mem.graphTruncatedNodes': 'Showing {n} most recent nodes.',
      'mem.graphTruncated': 'Showing the most recent {n} memories.',
      'mem.graphOrphanNote': '{n} memories without entity links are not drawn; click a node for details.',
      'mem.statsOrphan': 'Unlinked memories',
      'mem.statsAvgConf': 'Avg confidence',
      'mem.statsOldest': 'Oldest memory',
      'vcs.currentBranch': 'branch {name}',
      'vcs.aheadBehind': '↑{ahead} ↓{behind}',
      'vcs.remoteBranches': 'Remote branches',
      'vcs.current': 'current',
      'close': 'Close',
      'cancel': 'Cancel',
      'save': 'Save',
      'saveChanges': 'Save changes',
      'download': 'Download',
      'settings.subtitle': 'Changes are saved locally.',
      'settings.sections': 'Settings sections',
      'settings.workspaces': 'Workspaces',
      'settings.workspacesNote': 'Each workspace keeps its own conversations and points at a local folder. Archiving hides it without deleting conversations or files.',
      'settings.archivedWorkspaces': 'Archived',
      'settings.modelEndpoint': 'Model endpoint',
      'settings.subscriptions': 'Subscriptions',
      'settings.subscriptionsNote': 'A subscription is one account (Base URL + API key) and can hold many models. Add the same provider more than once to use several accounts.',
      'settings.subscriptionsHint': 'Fetch a subscription\'s models, then add any number of them. Models stay grouped by subscription in the Models tab and in the picker.',
      'settings.filterSubscriptions': 'Filter subscriptions',
      'settings.filterModels': 'Filter models',
      'settings.providerOpenCodeGo': 'OpenCode Go',
      'settings.providerOpenCodeZen': 'OpenCode Zen',
      'settings.providerCustom': 'Custom',
      'settings.subscriptionName': 'Subscription name',
      'settings.protocolChatShort': 'OpenAI Chat',
      'settings.protocolResponseShort': 'OpenAI Responses',
      'settings.protocolAnthropicShort': 'Anthropic Messages',
      'settings.subscriptionCustomName': 'Custom subscription',
      'settings.subscriptionModels': '{n} models',
      'settings.subscriptionNoModels': 'No models yet',
      'settings.subscriptionActive': 'Active',
      'settings.selectedSubscription': 'Selected subscription',
      'settings.noSubscriptionSelected': 'Add a subscription on the left to manage its models.',
      'settings.deleteSubscription': 'Delete subscription',
      'settings.deleteSubscriptionConfirm': 'Delete subscription “{name}” and all {n} of its models?',
      'settings.subscriptionAdded': 'Subscription added.',
      'settings.modelFolder': 'Models',
      'settings.addModelTo': 'Add model to {name}',
      'settings.selectSubscription': 'Choose a subscription',
      'settings.selectSubscriptionHint': 'Pick an account first, then choose one of its models.',
      'settings.backToSubscriptions': 'All subscriptions',
      'settings.filterSubscriptionModels': 'Filter models in this subscription',
      'settings.noSubscriptionMatch': 'No subscriptions match.',
      'settings.noModelMatch': 'No models match.',
      'settings.subscriptionNeedsUrl': 'Fill in the subscription OpenAI Base URL first.',
      'platform.title': 'Platform presets',
      'platform.subtitle': 'Official APIs, aggregators, and self-hosted / local gateways.',
      'platform.search': 'Search platforms',
      'platform.add': 'Add platform',
      'platform.custom': 'Custom platform',
      'platform.customNote': 'Start blank, then fill in Base URL and API key.',
      'platform.note': 'Local gateway presets use the usual default ports. Adjust the Base URL after adding.',
      'platform.group.custom': 'Custom',
      'platform.group.opencode': 'OpenCode',
      'platform.group.aggregator': 'Aggregators',
      'platform.group.local': 'Local gateways',
      'platform.group.official': 'Official APIs',
      'platform.gateway': 'needs local gateway',
      'platform.empty': 'No matching platforms.',
      'platform.protocol': '{protocol}',
      'settings.models': 'Models',
      'settings.modelsNote': 'Models are grouped by subscription. The active model is used for new replies; endpoint and key come from its subscription.',
      'settings.modelsWorkspaceNote': 'Left: subscriptions (right-click to edit or delete). Right: models of the selected subscription; the first one is selected by default.',
      'settings.addModel': 'Add model',
      'settings.defaultModel': 'Default model',
      'settings.active': 'Active',
      'settings.edit': 'Edit',
      'settings.view': 'View',
      'settings.viewSubscription': 'View subscription',
      'settings.apiKeyStateSet': 'API key: set ({mask})',
      'settings.apiKeyStateUnset': 'API key: not set',
      'settings.useModel': 'Use this model',
      'settings.name': 'Name',
      'settings.namePlaceholder': 'e.g. Fast',
      'settings.baseUrl': 'OpenAI Base URL',
      'settings.useDefaultUrl': 'Inherit default endpoint',
      'settings.model': 'Model',
      'settings.modelBaseUrl': 'Base URL override',
      'settings.modelBaseUrlNote': 'Optional. Leave empty to use the subscription endpoint selected by this model\'s protocol.',
      'settings.useSubscriptionUrl': 'Subscription endpoint',
      'settings.discover': 'Fetch models',
      'settings.discovering': 'Fetching…',
      'settings.discoverHint': 'Models returned by this endpoint',
      'settings.discoverEmpty': 'No models returned by this endpoint.',
      'settings.discoverError': 'Failed to fetch models: {err}',
      'settings.useThisModel': 'Use',
      'settings.addAsProfile': 'Add profile',
      'settings.discoverAddAll': 'Add all as profiles',
      'settings.discoverCount': '{n} models',
      'settings.discoverSame': 'This model already has a profile.',
      'settings.discoverSkipped': '{n} models need Anthropic/Google protocol and were skipped.',
      'settings.discoverAdded': 'Added',
      'settings.discoverNote': 'Models you add are attached to the selected subscription.',
      'settings.addSubscription': 'Add subscription',
      'settings.editSubscription': 'Edit subscription',
      'settings.subscriptionModalHint': 'One account: an OpenAI Base URL, an optional Anthropic Base URL, and an API key. Its models are managed on the right.',
      'settings.subscriptionContextHint': 'Right-click to edit or delete',
      'settings.saveSubscription': 'Save',
      'settings.subscriptionUpdated': 'Subscription updated.',
      'settings.quickAdd': 'Quick add',
      'settings.channelName': 'Channel name',
      'settings.newModel': 'New model',
      'settings.channelModels': '{n} models',
      'settings.deleteChannel': 'Delete channel',
      'settings.deleteChannelConfirm': 'Delete channel “{name}” and all its models?',
      'settings.localChannel': 'Local',
      'settings.baseUrlRequired': 'Fill in the OpenAI Base URL first.',
      'settings.usage': 'Usage',
      'ask.title': '{app} needs your choice',
      'ask.singleHint': 'Pick one option.',
      'ask.multiHint': 'Pick any that apply.',
      'ask.other': 'Other (type your own)',
      'ask.otherPlaceholder': 'Type your answer…',
      'ask.ignore': 'Skip',
      'ask.submit': 'Confirm',
      'subagent.title': 'Subagents',
      'subagent.goal': 'Goal',
      'subagent.running': 'running',
      'subagent.done': 'done',
      'subagent.failed': 'failed',
      'subagent.noSteps': 'No steps yet',
      'diag.title': 'Diagnostics',
      'diag.success': '24h success',
      'diag.runs': 'Runs',
      'diag.p95': 'P95 duration',
      'diag.failed': 'Failed',
      'diag.queue': 'Queue / running',
      'diag.db': 'Database',
      'diag.backup': 'Latest backup',
      'diag.health': 'Health',
      'diag.healthy': 'healthy',
      'diag.degraded': 'degraded',
      'diag.export': 'Export diagnostics',
      'diag.backupNow': 'Back up now',
      'diag.backupDone': 'Backup created: {name}',
      'diag.exporting': 'Preparing diagnostics…',
      'usage.period1d': '1 day',
      'usage.period7d': '7 days',
      'usage.period30d': '30 days',
      'usage.periodAll': 'All',
      'usage.refresh': 'Refresh',
      'usage.loading': 'Loading usage…',
      'usage.empty': 'No usage recorded yet.',
      'usage.loadFailed': 'Failed to load usage: {err}',
      'usage.totalTokens': 'Total tokens',
      'usage.input': 'Input',
      'usage.output': 'Output',
      'usage.total': 'Total',
      'usage.cacheRate': 'Cache hit',
      'usage.cachedSub': 'cached {n}',
      'usage.repliesSub': '{n} replies',
      'usage.cacheSub': '{cached} / {prompt} input tokens',
      'usage.replies': 'Replies',
      'usage.rounds': 'Rounds',
      'usage.llmRoundsShort': 'LLM rounds',
      'usage.avgPerReply': 'Avg / reply',
      'usage.avgPerDay': 'Avg / day',
      'usage.activeDaysShort': 'active days',
      'usage.speedLabel': 'Speed',
      'usage.llmTimeShort': 'LLM time',
      'usage.tokensUnit': 'tokens',
      'usage.topModel': 'Top model',
      'usage.peakDay': 'Peak day',
      'usage.cacheSaved': 'Cache saved',
      'usage.cacheSavedSub': 'cached prompt tokens',
      'usage.chart': 'Last 30 days',
      'usage.chartRange': 'Last {n} days',
      'usage.chartLegend': 'input / output',
      'usage.byModel': 'By model',
      'usage.byChannel': 'By channel',
      'usage.recent': 'Recent runs',
      'usage.model': 'Model',
      'usage.channel': 'Channel',
      'usage.share': 'Share',
      'usage.colTime': 'Time',
      'usage.duration': 'Duration',
      'settings.opencodeGoQuota': 'OpenCode Go quota',
      'settings.quotaFiveHour': '5 hours',
      'settings.quotaWeekly': '1 week',
      'settings.quotaMonthly': '1 month',
      'settings.quotaUsed': '{used} / {limit}',
      'settings.quotaReset': 'resets in {time}',
      'settings.quotaRefresh': 'Refresh quota',
      'settings.quotaUnavailable': 'Quota unavailable: {err}',
      'settings.quotaNoKey': 'Add an OpenCode Go API key to show quota.',
      'settings.quotaUnsupported': 'Needs Anthropic/Google protocol',
      'settings.apiKey': 'API key',
      'settings.apiKeyNotSet': 'Not set',
      'settings.apiKeyUnchanged': '•••••••• (unchanged)',
      'settings.useDefaultKey': 'Inherit default key',
      'settings.apiKeyKeep': 'Leave empty or unchanged to keep the current key.',
      'settings.contextWindow': 'Context window',
      'settings.maxTokens': 'Max tokens',
      'settings.protocol': 'API protocol',
      'settings.protocolChat': 'OpenAI Chat (/chat/completions)',
      'settings.protocolResponse': 'OpenAI Responses (/responses)',
      'settings.protocolAnthropic': 'Anthropic Messages (/v1/messages)',
      'settings.anthropicBaseUrl': 'Anthropic Base URL',
      'settings.anthropicBaseUrlPlaceholder': 'https://api.anthropic.com',
      'settings.anthropicBaseUrlNote': 'Only for models using the Anthropic Messages protocol. Leave empty to use the OpenAI Base URL above.',
      'settings.vision': 'Image support',
      'settings.visionNote': 'Allow this model to receive uploaded images (vision).',
      'settings.visionLabel': 'This model can read images',
      'settings.thinkingLevels': 'Thinking levels',
      'settings.thinkingLevelsNote': 'Check the levels this model accepts. Checked levels appear in the composer; leave all unchecked to hide the selector.',
      'settings.thinkingCustom': 'Custom level',
      'settings.thinkingCustomPlaceholder': 'e.g. xhigh / 1 / think',
      'settings.thinkingAdd': 'Add',
      'settings.thinkingRemove': 'Remove custom level',
      'settings.loadingModels': 'Loading models…',
      'settings.modelsFailed': 'Could not load models.',
      'settings.noModels': 'No models yet.',
      'settings.deleteModel': 'Delete model',
      'settings.deleteModelConfirm': 'Delete model “{name}”?\n\nThis cannot be undone.',
      'settings.modelActivated': 'Switched to {name}.',
      'settings.manageModels': 'Manage models',
      'settings.runtime': 'Runtime',
      'settings.tools': 'Tools',
      'settings.codegraph': 'CodeGraph',
      'settings.codegraphFiles': 'Files',
      'settings.codegraphSymbols': 'Symbols',
      'settings.codegraphDeps': 'Dependencies',
      'settings.codegraphNote': 'CodeGraph is optional: the model decides when to use it. The index initializes automatically and updates incrementally after file changes.',
      'settings.codegraphRefresh': 'Rebuild index',
      'settings.codegraphRefreshed': 'CodeGraph index rebuilt.',
      'settings.codegraphUpdated': 'Index updated at {time}. The model decides when to query it.',
      'codegraph.title': 'CodeGraph',
      'codegraph.init': 'Initialize',
      'codegraph.update': 'Update',
      'codegraph.rebuild': 'Rebuild',
      'codegraph.files': '{n} files',
      'codegraph.symbols': '{n} symbols',
      'codegraph.deps': '{n} dependencies',
      'codegraph.updated': 'Updated {time}',
      'codegraph.empty': 'No indexed files. Click Initialize to build the graph.',
      'codegraph.running': 'Updating index…',
      'codegraph.done': 'CodeGraph updated.',
      'codegraph.initDone': 'CodeGraph initialized.',
      'codegraph.rebuildDone': 'CodeGraph rebuilt.',
      'codegraph.failed': 'CodeGraph failed: {msg}',
      'codegraph.openFile': 'Open file',
      'codegraph.noSymbols': 'no top-level symbols',
      'codegraph.searchPlaceholder': 'Search files or symbols',
      'codegraph.viewTree': 'Directory',
      'codegraph.viewFlat': 'Flat',
      'codegraph.noMatch': 'No files match the filter.',
      'codegraph.statFiles': 'Files',
      'codegraph.statSymbols': 'Symbols',
      'codegraph.statDeps': 'Dependencies',
      'codegraph.statUpdated': 'Updated',
      'codegraph.imports': 'imports',
      'settings.service': 'Service',
      'settings.address': 'Address',
      'settings.config': 'Config',
      'settings.notConfigured': 'Model endpoint is not configured yet. Open Settings to add a base URL and model.',
      'settings.nothingToSave': 'Nothing to save.',
      'settings.saving': 'Saving…',
      'settings.saved': 'Saved. New runs use these settings.',
      'settings.savedToast': 'Model settings saved.',
      'settings.saveFailed': 'Save failed: {msg}',
      'language.title': 'Language',
      'language.systemNamed': 'System ({lang})',
      'language.note': 'The default follows your browser language. You can switch at any time.',
      'language.switchTitle': 'Switch language',
      'empty.title': 'What should we get done?',
      'empty.body': 'Ask a question, inspect your workspace, or let {app} run a multi-step task on its own.',
      'empty.suggest1': 'Review the files in my workspace',
      'empty.suggest2': 'Write a Python script for a small task',
      'empty.suggest3': 'Research a topic and summarize it',
      'empty.suggest4': 'Turn this goal into a task list',
      'empty.suggestChat1': 'Help me think a goal through',
      'empty.suggestChat2': 'Draft an outline for this idea',
      'empty.bodyChatOnly': 'Ask a question, think something through, or let {app} run a multi-step task on its own.',
      'loading.conversation': 'Loading conversation…',
      'chat.new': 'New conversation',
      'chat.unread': 'Unread',
      'mention.title': 'Mention a member',
      'newChat.title': 'New conversation',
      'newChat.subtitle': 'Start a single chat, or create / join a group.',
      'newChat.single': 'Single chat',
      'newChat.group': 'Group chat',
      'newChat.existingGroup': 'Join a group',
      'newChat.newGroup': 'Or create a new group',
      'newChat.groupName': 'Group name',
      'newChat.groupHint': 'Leave the name empty to join the selected group; fill it to create a new one.',
      'newChat.create': 'Create',
      'newChat.emptyGroups': 'No groups yet — name one below to create it.',
      'newChat.failed': 'Create failed: {msg}',
      'conv.info': 'Conversation info',
      'conv.conversation': 'Conversation',
      'conv.workspace': 'Workspace',
      'conv.model': 'Model',
      'conv.group': 'Group',
      'conv.single': 'Single chat (no group bound)',
      'conv.teamName': 'Group name',
      'conv.mode': 'Mode',
      'conv.mode.orchestrate': 'Orchestrated',
      'conv.mode.roundtable': 'Roundtable',
      'conv.mode.single': 'Single role',
      'conv.maxSpeakers': 'Max roles per message',
      'conv.members': 'Members',
      'conv.addMember': 'Add',
      'conv.memberName': 'Member name',
      'conv.memberExpertise': 'Expertise',
      'conv.save': 'Save',
      'conv.bind': 'Bind',
      'conv.unbind': 'Unbind',
      'conv.bindExisting': 'Bind an existing group',
      'conv.createGroup': 'Create group',
      'conv.rename': 'Rename',
      'conv.noMembers': 'No members yet',
      'conv.saved': 'Saved',
      'conv.groupHint': 'New runs use this mode. Roundtable lets members reply in turn.',
      'conv.noConversation': 'Open a conversation first',
      'conv.tools': 'Tools',
      'conv.loading': 'Loading…',
      'conv.memberTools': '{n} tools',
      'conv.removeMember': 'Remove member',
      'conv.leaderName': 'Host',
      'chat.emptyPreview': 'No messages yet',
      'chat.untitled': 'Untitled conversation',
      'chat.renameTitle': 'Rename conversation',
      'chat.titleHint': 'Save a custom title, or let AI generate a concise one.',
      'chat.titleField': 'Title',
      'chat.titlePlaceholder': 'Conversation title',
      'chat.regenerateTitle': 'Regenerate with AI',
      'chat.titleGenerating': 'Generating title…',
      'chat.titleGenerated': 'Title updated',
      'chat.titleFailed': 'Could not generate a title. Check the model configuration.',
      'chat.titleSaved': 'Title saved',
      'chat.titleEmpty': 'Title cannot be empty',
      'chat.noMatch': 'No conversations match your search.',
      'chat.empty': 'No conversations yet.<br>Start one below.',
      'chat.delete': 'Delete conversation',
      'chat.clear': 'Delete all conversations',
      'chat.clearTitle': 'Delete all conversations',
      'chat.clearConfirm': 'All {n} conversation(s) in "{ws}" will be permanently deleted, including messages, runs and attachments. This cannot be undone.',
      'chat.clearNone': 'There is no conversation to delete.',
      'chat.cleared': 'Deleted {n} conversation(s).',
      'conv.messageCount': '{n} msg',
      'conv.more': 'Show more conversations',
      'conv.expand': 'Expand conversations',
      'conv.collapse': 'Collapse conversations',
      'conv.all': 'All conversations',
      'conv.recent': 'Recent',
      'conv.pickerSubtitle': '{name} · {n} conversations',
      'conv.showArchived': 'Show archived',
      'conv.hideArchived': 'Hide archived',
      'conv.archivedBadge': 'archived',
      'conv.prev': 'Previous',
      'conv.next': 'Next',
      'conv.page': 'Page {page} / {pages}',
      'conv.none': 'No conversations in this workspace yet.',
      'conv.section.active': 'In progress',
      'conv.section.done': 'Completed',
      'conv.group.today': 'Today',
      'conv.group.yesterday': 'Yesterday',
      'conv.group.week': 'Previous 7 days',
      'conv.group.older': 'Older',
      'activeRuns.title': 'Active runs',
      'activeRuns.running': 'running',
      'activeRuns.queued': 'queued',
      'activeRuns.stop': 'Stop',
      'activeRuns.stopTitle': 'Stop this run',
      'activeRuns.answer': 'Answer',
      'activeRuns.answerTitle': 'Open this question',
      'activeRuns.approve': 'Approve',
      'activeRuns.approveTitle': 'Open this approval request',
      'activeRuns.waiting': 'waiting for answer',
      'activeRuns.waitingApproval': 'waiting for approval',
      'activeRuns.badgeTitle': 'Run in progress',
      'process.title': 'Processes',
      'process.run': 'Conversation run',
      'process.subagent': 'Subagent',
      'process.kindTool': 'tool',
      'process.kindSubagent': 'subagent',
      'process.kindRun': 'run',
      'process.stop': 'Stop this process (the conversation keeps running)',
      'process.stopRun': 'Stop this run',
      'process.stopped': 'Process stopped — the conversation is unaffected',
      'process.stoppedRun': 'Run stopped',
      'process.details': 'Process details',
      'process.detailStop': 'Stop process',
      'process.fieldKind': 'Kind',
      'process.fieldStatus': 'Status',
      'process.fieldStarted': 'Started',
      'process.fieldElapsed': 'Elapsed',
      'process.fieldId': 'Process ID',
      'process.fieldRun': 'Run ID',
      'process.fieldCall': 'Call ID',
      'process.fieldArgs': 'Arguments',
      'process.fieldOutput': 'Output',
      'process.running': 'running',
      'process.cancelled': 'cancelling…',
      'process.ended': 'ended',
      'process.noOutput': 'No output yet',
      'time.justNow': 'just now',
      'header.notConfigured': 'not configured',
      'header.modelSettings': 'Model settings',
      'header.workspace': 'Workspace · {name}',
      'header.chatWorkspace': 'Chat · {name}',
      'activity.queued': 'Queued — waiting for a worker slot…',
      'activity.starting': 'Starting run…',
      'activity.calling': 'Calling {tool}…',
      'activity.toolArgs': 'Model is preparing arguments for {tool}… {s}s',
      'activity.background': '{tool} is running in background — supervising',
      'activity.thinking': 'Thinking…',
      'activity.compressing': 'Compacting context…',
      'activity.compacting': 'Compacting context… {s}s',
      'activity.planning': 'Planning…',
      'activity.waitingFirst': 'Waiting for the model… {s}s',
      'activity.waitingMore': 'Model is still working… {s}s',
      'activity.retrying': 'Retrying ({a}/{m})…',
      'retry.notice': 'Network unstable — auto-retrying ({a}/{m})',
      'stream.unsupported': 'This browser does not support live streaming.',
      'run.stopped': 'Run stopped.',
      'run.stalled': 'No progress for {s}s - the run may be stuck. Use Stop, or send a new message to continue.',
      'run.failed': 'Run failed',
      'stop.requested': 'Force stopping…',
      'stop.pending': 'Stop requested, but the backend is still finishing. Click stop again to force it.',
      'error.openConversation': 'Could not open conversation: {msg}',
      'delete.confirm': '“{title}” and all its messages and runs will be permanently deleted. This cannot be undone.',
      'delete.title': 'Delete conversation',
      'delete.action': 'Delete',
      'confirm.title': 'Confirm',
      'confirm.ok': 'Confirm',
      'delete.thisConversation': 'this conversation',
      'delete.done': 'Conversation deleted.',
      'delete.failed': 'Delete failed: {msg}',
      'error.tooLong': 'Message is too long (max 8000 characters).',
      'error.startConversation': 'Could not start conversation: {msg}',
      'intervention.sent': 'Sent as an intervention to the running reply.',
      'error.send': 'Send failed: {msg}',
      'proposal.dismissed': 'Suggestion dismissed.',
      'proposal.started': 'Autonomous run started.',
      'proposal.status.pending': 'pending',
      'proposal.status.accepted': 'accepted',
      'proposal.status.dismissed': 'dismissed',
      'copy.copy': 'copy',
      'preview.showCode': 'code',
      'preview.showPreview': 'preview',
      'preview.open': 'open',
      'preview.preview': 'Preview',
      'copy.copied': 'copied',
      'copy.failed': 'Copy failed.',
      'copy.done': 'Copied.',
      'copy.message': 'Copy message',
      'msg.copy': 'Copy',
      'msg.edit': 'Edit',
      'msg.recall': 'Recall',
      'msg.queued': 'queued',
      'msg.cancel': 'Cancel',
      'msg.save': 'Save & send',
      'msg.editHint': 'Later messages will be removed; files are restored to before this message.',
      'msg.running': 'This message is running; stop the run before editing or recalling it.',
      'msg.editDone': 'Message updated.',
      'msg.recallConfirm': 'Recall this message? It and all later turns will be removed.',
      'msg.recallQueuedConfirm': 'Recall this queued message? It will be removed from the queue.',
      'msg.recallDone': 'Message recalled.',
      'rewind.title': 'Rewind to checkpoint',
      'rewind.action': 'Rewind',
      'rewind.subtitle': 'Restore workspace files and/or remove this message and everything after it.',
      'rewind.none': 'No checkpoint for this message yet.',
      'rewind.target': 'Checkpoint · {time}',
      'rewind.files': 'files',
      'rewind.modified': 'modified',
      'rewind.added': 'added',
      'rewind.deleted': 'deleted',
      'rewind.clean': 'The workspace matches this checkpoint.',
      'rewind.filesOption': 'Restore workspace files',
      'rewind.deleteAdded': 'Delete files created after this checkpoint',
      'rewind.convOption': 'Remove this message and later turns',
      'rewind.filesNote': '{count} files will be restored',
      'rewind.noRestore': 'No file contents need restoring',
      'rewind.deleteNote': '{count} new files will be deleted',
      'rewind.convNote': '{count} messages will be removed',
      'rewind.pickOne': 'Choose files, conversation, or both.',
      'rewind.working': 'Rewinding…',
      'rewind.done': 'Rewound: {files} files restored, {deleted} deleted, {messages} messages removed.',
      'rewind.failed': 'Rewind failed: {msg}',
      'rewind.unavailable': 'Unavailable: {reason}',
      'rewind.incomplete': 'This checkpoint is incomplete; new files will not be deleted.',
      'rewind.messageGone': 'This message was already removed.',
      'rewind.deleteAddedNA': 'Not available for an incomplete checkpoint.',
      'rewind.noWorkspace': 'This conversation has no workspace folder.',
      'error.openFile': 'Could not open file: {msg}',
      'preview.truncated': 'truncated preview',
      'preview.empty': '(empty file)',
      'preview.lines': '{lines} lines',
      'preview.modePreview': 'Preview',
      'preview.modeCode': 'Code',
      'preview.modeLabel': 'Viewer mode',
      'preview.wrap': 'Word wrap',
      'preview.copy': 'Copy contents',
      'preview.edit': 'Edit',
      'preview.save': 'Save',
      'preview.files': 'File list',
      'preview.editing': 'Editing · Ctrl+S to save',
      'preview.readonly': 'Read only',
      'preview.saved': 'File saved.',
      'preview.saveFailed': 'Could not save: {msg}',
      'preview.pos': 'Ln {line}, Col {col}',
      'preview.editorFallback': 'code editor unavailable — plain text',
      'preview.unavailable': 'This file type can not be previewed here. Download it to open.',
      'preview.corrupt': 'This file looks damaged and can not be parsed. Download it to open.',
      'runtime.running': '{running} running',
      'runtime.runningQueued': '{running} running · {queued} queued',
      'runtime.ready': 'Ready',
      'runtime.offline': 'Offline',
      'error.unreachable': '{app} is not reachable: {msg}',
      'trace.if': 'if {condition}',
      'trace.acceptRun': 'Accept & run',
      'trace.dismiss': 'Dismiss',
      'trace.proposalTitle': 'Agent suggests autonomous run',
      'trace.arguments': 'Arguments',
      'trace.retry': 'Retry',
      'trace.packCall': 'Called through a tool pack',
      'trace.thinking': 'Thinking',
      'trace.diff': 'Changed lines',
      'trace.content': 'Contents',
      'trace.files': 'Entries',
      'trace.process': 'Process',
      'process.steps': '{n} steps',
      'process.tools': '{n} tools',
      'process.thoughts': '{n} thoughts',
      'process.retries': '{n} retries',
      'trace.working': 'working',
      'trace.running': 'running',
      'trace.streamPlaceholder': 'Thinking…',
      'trace.interrupted': 'This reply was interrupted; the partial output is kept.',
      'trace.contextCompressed': 'Context compacted: {before} → {after} tokens (summary via {source}, {keep} recent tokens kept).',
      'trace.compressing': 'Compacting context',
      'trace.compressed': 'Context compacted',
      'trace.compressStartDetail': 'Context {before} / {budget} tokens (tool outputs {tool} · conversation {dialog} · system {system}) — compacting…',
      'trace.compressDoneDetail': '{before} → {after} tokens (summary via {source}, {keep} recent tokens kept, {duration}).',
      'trace.compressPruned': 'Context pruned in place: {before} → {after} tokens ({n} old tool outputs cleared, no summary call).',
      'trace.thought.one': '{n} thought',
      'trace.thought.other': '{n} thoughts',
      'trace.tool.one': '{n} tool',
      'trace.tool.other': '{n} tools',
      'trace.suggestion.one': '{n} suggestion',
      'trace.suggestion.other': '{n} suggestions',
      'trace.note.one': '{n} note',
      'trace.note.other': '{n} notes',
      'trace.step.one': '{n} step',
      'trace.step.other': '{n} steps',
      'usage.in': 'in {n}',
      'usage.convIn': 'in {n} total',
      'usage.convOut': 'out {n} total',
      'usage.convCache': 'avg cache {p}%',
      'usage.convSpeed': 'avg {n} tok/s',
      'usage.convDetail': '{turns} replies · {rounds} model rounds',
      'usage.convSpeedDetail': 'Averaged over model time (tools and waiting excluded).',
      'context.chip': 'Context {k} · {p}% of the input budget ({used} / {budget} tokens; window {window}, {out} reserved for output, peak {peak})',
      'usage.out': 'out {n}',
      'usage.time': 'time {t}',
      'usage.speed': '{n} tok/s',
      'usage.cache': 'cache {p}%',
      'usage.cacheDetail': 'cached {c} / {p} tokens · {r} rounds',
      'usage.timeDetail': 'model {l} · tools & waiting {o}',
      'tool.read_file': 'Read File',
      'tool.read_office': 'Read document',
      'tool.write_file': 'Write File',
      'tool.edit_file': 'Edit File',
      'tool.preparing': 'preparing…',
      'tool.background': 'background',
      'tool.backgroundHint': 'Exceeded the foreground budget, handed to background supervision. The model keeps working and checks it with tool_job.',
      'tool.list_files': 'List Files',
      'tool.shell': 'Shell Command',
      'tool.web': 'Web',
      'tool.memory_read': 'Memory read',
      'tool.memory_write': 'Memory write',
      'tool.workspace': 'Workspace',
      'tool.time': 'Time',
      'tool.calculate': 'Calculate',
      'tool.report_plan': 'Execution plan',
      'tool.delegate': 'Delegate',
      'tool.propose_task': 'Propose',
      'tool.call_agent': 'Delegate',
      'tool.call_role': 'Delegate',
      'tool.call_subagent': 'Subagent',
      'tool.list_skills': 'List skills',
      'tool.use_skill': 'Use skill',
      'tool.create_skill': 'Create skill',
      'tool.install_skill': 'Install skill',
      'tool.list_mcp': 'List MCP servers',
      'tool.add_mcp_server': 'Add MCP server',
      'tool.install_mcp': 'Install MCP',
      'tool.vcs': 'Version control',
      'tool.codegraph': 'Code graph',
      'tool.tool_job': 'Background job',
      'tool.memory_forget': 'Forget memory',
      'tool.todo': 'Task list',
      'tool.goal_report': 'Goal progress',
      'tool.goal_manage': 'Goal',
      'tool.plan_manage': 'Plan steps',
      'tool.team_manage': 'Team setup',
      'tool.role_manage': 'Role library',
      'tool.app_manage': 'Global app manager',
      'tool.call_team': 'Delegate to team',
      'tool.ask_user': 'Ask user',
      'tool.save_analysis': 'Save analysis',
      'tool.fallback': 'Tool',
      'tools.note': 'Same-family tools are merged into packs: one function per pack, with an op choosing the action. Development and repo tools (jobs, VCS, code graph) live in the dev pack; shell, workspace and ask_user stay standalone. Concrete tools are used for execution, permissions and previews.',
      'tools.standalone': 'Standalone tools',
      'tools.chatOnly': 'A chat-only workspace has no project folder: shell and file tools run in a managed folder; only the workspace tool is unavailable.',
      'tools.needWorkspace': 'Needs a local folder',
      'tools.needTeam': 'Requires a bound team',
      'tools.condTeam': 'team',
      'tools.pack.fs': 'Files',
      'tools.pack.net': 'Web & compute',
      'tools.pack.memory': 'Memory',
      'tools.pack.plan': 'Tasks & collaboration',
      'tools.pack.dev': 'Development & repo',
      'tools.pack.skill': 'Skills',
      'tools.pack.mcp': 'MCP',
      'approval.title': 'Needs your approval',
      'approval.pending': 'Agent wants to use {tool}',
      'approval.deny': 'Deny',
      'approval.once': 'Allow once',
      'approval.session': 'Allow in this conversation',
      'approval.always': 'Always allow',
      'approval.jump': 'Show request',
      'approval.approved': 'Allowed',
      'approval.denied': 'Denied',
      'approval.expired': 'No answer in time',
      'approval.risk.high': 'high risk',
      'approval.risk.medium': 'medium risk',
      'approval.risk.low': 'low risk',
      'approval.waiting': 'waiting for you…',
      'perm.title': 'Ask: confirm every risky call · Auto: run low/medium risk automatically, ask for high risk · Allow: run everything',
      'settings.mcp': 'MCP servers',
      'mcp.name': 'Name',
      'mcp.type': 'Type',
      'mcp.package': 'npm package',
      'mcp.installCommand': 'Install command (optional)',
      'mcp.installCommandNote': 'The app does not inherit your terminal PATH. If npm is missing, put a full command or absolute path here.',
      'mcp.installNeedsPackage': 'Fill in a package or an install command.',
      'mcp.command': 'command (instead of npm)',
      'mcp.url': 'URL',
      'mcp.args': 'Arguments',
      'mcp.install': 'Install & add',
      'mcp.add': 'Add',
      'mcp.export': 'Export',
      'mcp.import': 'Import',
      'mcp.enable': 'Enable',
      'mcp.disable': 'Disable',
      'mcp.enabled': 'Enabled',
      'mcp.edit': 'Edit',
      'mcp.editTitle': 'Edit MCP server',
      'mcp.save': 'Save',
      'mcp.saved': 'MCP server saved.',
      'mcp.note': 'Note',
      'mcp.notePlaceholder': 'What this server is for',
      'mcp.env': 'Environment variables',
      'mcp.addVariable': 'Add variable',
      'mcp.envNote': 'Passed only to this stdio process, e.g. API keys and data paths.',
      'mcp.headers': 'HTTP headers',
      'mcp.addHeader': 'Add header',
      'mcp.headersNote': 'Sent with every request, e.g. Authorization or a tenant header.',
      'mcp.removePair': 'Remove',
      'mcp.test': 'Test',
      'mcp.delete': 'Delete',
      'mcp.tools': '{n} tools',
      'mcp.empty': 'No MCP server configured. Add one below, or install an npm package.',
      'mcp.npmMissing': 'npm is not available on this machine, so automatic install is not possible.',
      'mcp.addServer': 'Add MCP server',
      'mcp.panelNote': 'stdio servers run a local npm package or command; http/sse servers point at a remote URL.',
      'mcp.addTitle': 'Add MCP server',
      'mcp.addSubtitle': 'stdio runs a local npm package or command; http/sse connects to a remote endpoint.',
      'mcp.typeStdio': 'stdio · local',
      'mcp.typeHttp': 'http / sse · remote',
      'mcp.addNote': 'Installed packages land under data/agent_zero/mcp. Tools show up as mcp__server__tool.',
      'settings.skills': 'Skills',
      'settings.extensions': 'Commands & hooks',
      'ext.note': 'Slash commands are Markdown files in .atlas/commands (Claude Code’s .claude/commands also works). Hooks run local shell commands around tools and runs; they are your own scripts and never replace permission approvals.',
      'ext.commands': 'Slash commands',
      'ext.hooks': 'Hooks',
      'ext.addCommand': 'New command',
      'ext.addHook': 'New hook',
      'ext.hookTemplate': 'Create hooks.json',
      'ext.refresh': 'Refresh',
      'ext.name': 'Name',
      'ext.argumentHint': 'Argument hint',
      'ext.description': 'Description',
      'ext.body': 'Prompt body',
      'ext.event': 'Event',
      'ext.matcher': 'Tool matcher (regex)',
      'ext.matcherAll': 'All tools',
      'ext.matcherFiles': 'Files',
      'ext.matcherShell': 'Shell',
      'ext.command': 'Shell command',
      'ext.timeout': 'Timeout (s)',
      'ext.save': 'Save',
      'ext.cancel': 'Cancel',
      'ext.delete': 'Delete',
      'ext.emptyCommands': 'No slash commands yet. Create one, or add a Markdown file under .atlas/commands.',
      'ext.emptyHooks': 'No hooks configured.',
      'ext.builtin': 'built-in',
      'ext.workspace': 'workspace',
      'ext.commandSaved': 'Command saved.',
      'ext.commandDeleted': 'Command deleted.',
      'ext.hookSaved': 'Hook saved.',
      'ext.hookDeleted': 'Hook deleted.',
      'ext.templateCreated': 'hooks.json is ready.',
      'ext.deleteCommandConfirm': 'Delete command /{name}?',
      'ext.deleteHookConfirm': 'Delete this hook?',
      'ext.badName': 'Use letters, digits, hyphen, underscore, Chinese or colon in the name.',
      'slash.title': 'Slash commands',
      'slash.help': 'Show available slash commands',
      'slash.empty': 'No matching command',
      'skill.name': 'Name',
      'skill.add': 'Add skill',
      'skill.panelNote': 'Write a SKILL.md, upload .md / .zip, install an npm package, or pull skills with npx skills add <owner/repo>.',
      'skill.addTitle': 'Add skill',
      'skill.addSubtitle': 'Write a SKILL.md, upload one, or install from npm / npx skills.',
      'skill.srcManual': 'Write',
      'skill.srcUpload': 'Upload',
      'skill.srcNpm': 'npm',
      'skill.srcNpx': 'npx skills',
      'skill.uploadHint': 'Pick a .md file, or a .zip with SKILL.md plus extra files',
      'skill.uploadNote': 'The skill name comes from the frontmatter or the first heading.',
      'skill.npmPackage': 'npm package',
      'skill.npmNote': 'The package must contain a SKILL.md.',
      'skill.description': 'Description',
      'skill.content': 'SKILL.md',
      'skill.save': 'Save skill',
      'skill.upload': 'Upload .md / .zip',
      'skill.npm': 'Install from npm',
      'skill.npxTitle': 'npx skills (Agent Skills registry)',
      'skill.npxNote': 'Runs `npx skills add <owner/repo>` into this app’s skill library only — it does not touch other agents’ skill folders.',
      'skill.npxSource': 'Source',
      'skill.npxSkills': 'Skills (optional)',
      'skill.npxList': 'List skills in source',
      'skill.npxSearch': 'Search registry',
      'skill.npxInstall': 'Install',
      'skill.npxNeedSource': 'Enter a skills source, e.g. vercel-labs/agent-skills',
      'skill.npxBusy': 'Querying skills registry…',
      'skill.npxInstalling': 'Installing skills… this can take a minute.',
      'skill.npxNone': 'Nothing found.',
      'skill.npxInstalled': '{n} skill(s) installed',
      'skill.npxSkipped': '{n} more skills in this source were not imported (limit 30).',
      'skill.npxPicked': 'Selected: {n}',
      'skill.download': 'Download',
      'skill.files': '{n} files',
      'skill.delete': 'Delete',
      'skill.empty': 'No skills yet. Write one, upload one, or install from npm.',
      'trace.approval.one': '{n} approval',
      'trace.approval.other': '{n} approvals',
      'perm.set': 'Saved',
      'mcp.probeOk': 'Connected · {n} tools',
      'mcp.probeFail': 'Connection failed',
    },
    zh: {
      'app.tagline': '本地工作区',
      'sidebar.close': '关闭侧边栏',
      'sidebar.collapse': '收缩侧栏',
      'dock.drag': '拖动调整宽度',
      'dock.collapse': '收缩面板',
      'dock.expand': '展开面板',
      'dock.closeTab': '关闭标签页',
      'dock.closeOthers': '关闭其他',
      'dock.closeAll': '关闭全部',
      'settings.appearance': '外观',
      'appearance.schedule': '主题定时切换',
      'appearance.scheduleManual': '手动',
      'appearance.scheduleDayNight': '昼夜切换',
      'appearance.scheduleRotate': '轮换',
      'appearance.dayTheme': '日间主题',
      'appearance.nightTheme': '夜间主题',
      'appearance.dayStart': '白天开始',
      'appearance.nightStart': '夜间开始',
      'appearance.rotateEvery': '轮换间隔',
      'dock.tabWorkspace': '工作区',
      'dock.tabViewer': '预览',
      'dock.tabVcs': '版本控制',
      'dock.tabMem': '记忆',
      'dock.tabCodegraph': '代码图谱',
      'dock.tabTodo': '任务清单',
      'appearance.resetLayout': '重置布局',
      'appearance.layoutNote': '工作区、记忆、版本控制、任务等功能每打开一个面板都会新增一个标签页；标签页可拖拽重排，也可点击关闭按钮关闭。标签栏上的箭头可把 dock 收缩到边栏，边栏上的箭头可展开；拖拽 dock 或侧栏边缘可调整宽度。所有界面设置都会全局保存到服务器（浏览器本地有一份备份）。',
      'appearance.noteManual': '主题保持当前选择。',
      'appearance.noteDayNight': '{dayStart}-{nightStart} 使用 {day}，其余时间自动切换为 {night}。',
      'appearance.noteRotate': '每 {interval} 分钟轮换一次主题。',
      'appearance.layoutReset': '布局已重置',
      'appearance.details': '界面细节',
      'appearance.sidebarWidth': '侧栏宽度',
      'appearance.dockWidth': 'Dock 宽度',
      'appearance.sidebarCollapsed': '侧栏已收缩',
      'appearance.chatSection': '聊天区展开',
      'appearance.workspaceSection': '工作区列表展开',
      'appearance.queueBar': '队列条展开',
      'appearance.viewerWrap': '预览自动换行',
      'theme.dark': '深色', 'theme.light': '浅色', 'theme.midnight': '午夜', 'theme.slate': '石板', 'theme.ocean': '海洋', 'theme.forest': '森林', 'theme.sepia': '复古', 'theme.violet': '紫罗兰',
      'sidebar.open': '打开侧边栏',
      'newConversation': '新建对话',
      'search.placeholder': '搜索对话',
      'nav.workspace': '工作区文件',
      'nav.settings': '设置',
      'runtime.title': '运行状态',
      'runtime.connecting': '连接中…',
      'theme.toggle': '切换主题',
      'runState.working': '处理中',
      'runState.queued': '排队中',
      'activity.working': '处理中…',
      'prompt.placeholder': '给 {app} 发消息…',
      'mode.label': '运行模式',
      'mode.chat': 'chat',
      'mode.autonomous': 'agent',
      'mode.goal': '目标',
      'mode.goalTitle': '目标模式 —— 持续执行直到目标达成',
      'queue.title': '消息队列',
      'queue.count': '{n} 条等待',
      'queue.moveUp': '上移',
      'queue.moveUpTitle': '提前一位执行',
      'queue.moveDown': '下移',
      'queue.moveDownTitle': '推后一位执行',
      'queue.inject': '停止并继续',
      'queue.injectTitle': '停止当前回复，紧接着执行这条消息',
      'queue.edit': '编辑',
      'queue.editTitle': '编辑这条排队消息',
      'queue.cancel': '取消',
      'queue.queued': '已排队（第 {n} 条），当前回复结束后执行。',
      'queue.cancelled': '已取消排队消息。',
      'queue.movedUp': '已上移一位。',
      'queue.movedDown': '已下移一位。',
      'queue.atTop': '已经在队首。',
      'queue.atBottom': '已经在队尾。',
      'queue.injected': '已插入到正在执行的回复。',
      'queue.stopContinue': '已停止当前回复，这条消息紧接着执行。',
      'queue.failed': '队列操作失败：{msg}',
      'attach.title': '添加图片或文档',
      'attach.titleVision': '添加图片、文档或代码文件',
      'attach.add': '添加附件',
      'attach.remove': '移除附件',
      'attach.tooMany': '每条消息最多 {n} 个附件。',
      'attach.tooLarge': '{name} 超过 25 MB。',
      'attach.totalLarge': '附件总大小超过 50 MB。',
      'attach.unsupported': '不支持的文件：{name}。',
      'attach.imageNeedsVision': '当前模型未开启图片支持。请在「设置 → 模型」中开启图片支持，或切换到支持图片的模型。',
      'attach.imageRemoved': '当前模型不支持图片，已移除图片附件。',
      'attach.longConfirm': '这条消息有 {n} 个字符。是否把全文转为 txt 附件（原文留在输入框，核对后再发送）？',
      'attach.longConfirmTitle': '长文本转 txt 附件？',
      'attach.longConfirmOk': '转为 txt',
      'attach.longConfirmCancel': '保持原文',
      'attach.convertedReady': '已转为 {name}。核对附件后，再按一次 Enter 发送。',
      'attach.longHint': '{n} 个字符 —— 可作为 txt 文档发送',
      'attach.fullInFile': '完整内容见附件 {name}。',
      'attach.parseFailed': '附件处理失败：{msg}',
      'attach.converted': '长文本已作为 txt 文档附件发送。',
      'attach.convertedShort': 'txt 文档',
      'attach.only': '请查看附件。',
      'attach.visionBadge': '图片',
      'attach.visionBadgeTitle': '该模型可以读取上传的图片',
      'ref.title': '引用到对话',
      'ref.added': '已引用 {name}。',
      'ref.duplicate': '已经引用过：{name}',
      'ref.remove': '移除引用',
      'ref.label': '引用',
      'ref.only': '请查看引用的文件。',
      'thinking.title': '本条消息的思考级别',
      'thinking.label': '思考级别',
      'thinking.chipTitle': '思考强度 · {level}',
      'thinking.default': '默认',
      'thinking.minimal': '极低',
      'thinking.low': '低',
      'thinking.medium': '中',
      'thinking.high': '高',
      'goal.label': '目标',
      'goal.budget': '预算',
      'goal.budgetTitle': 'token 预算（0 = 不限）',
      'goal.end': '结束目标',
      'goal.runs': '已运行 {used}/{max} 次',
      'goal.tokens': '已用 {used} token',
      'goal.tokensBudget': '已用 {used}/{budget} token',
      'goal.status.achieved': '已达成',
      'goal.status.exhausted': '预算用尽',
      'goal.status.closed': '已结束',
      'goal.ended': '目标已结束，停止自动续跑。',
      'goal.saved': '目标预算已保存。',
      'goal.empty': '还没有目标——在目标模式下发送一条消息即可创建。',
      'goal.dismiss': '隐藏目标条',
      'todo.title': '任务清单',
      'todo.cardTitle': '任务',
      'todo.placeholder': '添加任务…',
      'todo.add': '添加',
      'todo.empty': '还没有任务。可以让 Agent 列一份清单，或在这里手动添加。',
      'todo.summary': '{doing} 进行中 · {pending} 待处理',
      'todo.summaryBar': '{done}/{total} 已完成',
      'todo.foot': '{completed}/{total} 已完成',
      'todo.status.pending': '待处理',
      'todo.status.in_progress': '进行中',
      'todo.status.completed': '已完成',
      'todo.openPanel': '打开任务清单',
      'todo.allDone': '全部完成',
      'todo.added': '任务已添加。',
      'todo.deleted': '任务已删除。',
      'todo.failed': '任务更新失败：{msg}',
      'model.title': '当前模型',
      'send.title': '发送 · Enter（双击/长按 = 原样直接发送）',
      'send.aria': '发送',
      'stop.title': '强制停止运行',
      'stop.aria': '强制停止运行',
      'composer.hint': '<kbd>Enter</kbd> 发送 · <kbd>Shift</kbd>+<kbd>Enter</kbd> 换行',
      'composer.imeConfirm': '再按一次 Enter 发送（刚确认了输入法候选词）。',
      'composer.local': '本地',
      'composer.workspaceHint': '{name} · 本地',
      'workspace.title': '工作区',
      'workspace.refresh': '刷新',
      'workspace.filter': '筛选工作区',
      'workspace.noMatch': '没有匹配的工作区',
      'workspace.none': '暂无工作区',
      'chatSection.title': '聊天',
      'chat.noMatch': '没有匹配的对话',
      'conv.newChat': '新建聊天会话',
      'conv.newInWorkspace': '在工作区新建会话',
      'workspace.parent': '上级目录',
      'workspace.loading': '加载中…',
      'workspace.empty': '此文件夹为空。<br>{app} 创建的文件会显示在这里。',
      'workspace.open': '打开',
      'workspace.view': '查看',
      'workspace.switch': '切换工作区',
      'workspace.add': '添加工作区',
      'workspace.collapseAll': '收缩全部工作区',
      'filemenu.open': '打开',
      'filemenu.reveal': '在 Finder 中显示',
      'filemenu.copyPath': '复制路径',
      'filemenu.rename': '重命名',
      'filemenu.delete': '删除',
      'filemenu.download': '下载',
      'filemenu.newFile': '新建文件',
      'filemenu.newFolder': '新建文件夹',
      'filemenu.refresh': '刷新',
      'filemenu.renamePrompt': '“{name}”的新名称',
      'filemenu.newFilePrompt': '新文件名',
      'filemenu.newFolderPrompt': '新文件夹名',
      'filemenu.deleteConfirm': '删除“{name}”？此操作不可撤销。',
      'filemenu.copied': '路径已复制。',
      'filemenu.revealed': '已在文件管理器中打开。',
      'filemenu.done': '已完成。',
      'filemenu.failed': '操作失败：{msg}',
      'menu.open': '打开',
      'menu.copyTitle': '复制标题',
      'menu.delete': '删除',
      'menu.cut': '剪切',
      'menu.copy': '复制',
      'menu.paste': '粘贴',
      'menu.selectAll': '全选',
      'menu.save': '保存',
      'menu.copyMessage': '复制消息',
      'menu.copyAnswer': '复制回复',
      'menu.fork': '从此处分支',
      'fork.title': '分支会话',
      'fork.confirm': '新建一个会话，包含这条消息及之前的全部对话？工作区文件由两个分支共用。',
      'fork.confirmAll': '新建一个会话，包含当前全部对话？工作区文件由两个分支共用。',
      'fork.action': '创建分支',
      'fork.done': '已创建会话分支。',
      'fork.failed': '分支失败：{msg}',
      'menu.copySelected': '复制选中',
      'menu.clipboardFailed': '无法读取剪贴板。',
      'workspace.manage': '管理工作区',
      'workspace.name': '名称',
      'workspace.namePlaceholder': '工作区名称',
      'workspace.folder': '文件夹',
      'workspace.parentFolder': '上级目录',
      'workspace.go': '前往',
      'workspace.addTitle': '添加工作区',
      'workspace.addSubtitle': '选择一个本地文件夹。对话和文件都会绑定到这个工作区。',
      'workspace.useFolder': '使用此文件夹',
      'workspace.folderLoading': '正在加载文件夹…',
      'workspace.folderEmpty': '这里没有子文件夹。',
      'workspace.folderCount': '{n} 个文件夹',
      'workspace.folderFailed': '无法读取此文件夹。',
      'workspace.chatOnly': '纯聊天',
      'workspace.chatOnlyNote': '该工作区是纯聊天工作区，没有工作目录；需要读写文件或执行脚本，请添加一个指向本地文件夹的工作区。',
      'workspace.added': '已添加工作区“{name}”。',
      'workspace.addFailed': '添加工作区失败：{msg}',
      'workspace.default': '默认',
      'workspace.current': '当前',
      'workspace.conversations': '{n} 个对话',
      'workspace.switched': '已切换到 {name}。',
      'workspace.rename': '重命名',
      'workspace.renamePrompt': '重命名工作区',
      'workspace.renamed': '工作区已重命名。',
      'workspace.renameFailed': '重命名工作区失败：{msg}',
      'workspace.archive': '归档',
      'workspace.archiveConfirm': '归档工作区“{name}”？对话和文件都会保留。',
      'workspace.archivedToast': '工作区已归档。',
      'workspace.archiveFailed': '归档工作区失败：{msg}',
      'workspace.restore': '恢复',
      'workspace.delete': '删除（仅隐藏）',
      'workspace.deleteConfirm': '删除工作区「{name}」？只会从列表隐藏，目录、会话与记忆全部保留；重新添加同一目录会自动恢复。',
      'workspace.deletedToast': '已从列表移除（目录与会话保留）。',
      'workspace.restoredOnAdd': '已恢复工作区「{name}」。',
      'workspace.live': '该工作区有任务正在运行',
      'workspace.restored': '工作区已恢复。',
      'workspace.restoreFailed': '恢复工作区失败：{msg}',
      'workspace.rootMissing': '文件夹不存在',
      'vcs.title': '版本控制',
      'vcs.tabStatus': '变更',
      'vcs.tabLog': '历史',
      'vcs.tabBranches': '分支',
      'vcs.tabRemotes': '远端',
      'vcs.changedCount': '{n} 处未提交改动',
      'vcs.noChanges': '没有未提交改动',
      'vcs.commit': '提交',
      'vcs.commitMsgPlaceholder': '提交信息…',
      'vcs.stage': '暂存',
      'vcs.unstage': '取消暂存',
      'vcs.discard': '丢弃',
      'vcs.commited': '已提交。',
      'vcs.staged': '已暂存。',
      'vcs.unstaged': '已取消暂存。',
      'vcs.discarded': '改动已丢弃。',
      'vcs.switch': '切换',
      'vcs.create': '新建',
      'vcs.newBranchPlaceholder': '新分支名…',
      'vcs.switched': '已切换到 {name}。',
      'vcs.none': '该工作区没有 git / svn 仓库',
      'vcs.noCommits': '尚无提交',
      'vcs.failed': '版本控制操作失败: {msg}',
      'vcs.refresh': '刷新',
      'vcs.viewFlat': '平铺列表',
      'vcs.viewDir': '目录树',
      'vcs.viewTree': '目录树',
      'vcs.viewList': '平铺列表',
      'vcs.today': '今天',
      'vcs.yesterday': '昨天',
      'vcs.unknownDate': '未知日期',
      'vcs.merge': '合并',
      'vcs.refTag': '标签',
      'vcs.refHead': 'HEAD',
      'vcs.commitsOnDay': '{n} 次提交',
      'vcs.remotes': '远端仓库',
      'vcs.noRemotes': '还没有配置远端仓库',
      'vcs.gitOnlyRemotes': '远端管理仅适用于 git 仓库。',
      'vcs.addRemote': '添加远端',
      'vcs.editRemote': '修改地址',
      'vcs.removeRemote': '移除',
      'vcs.removeRemoteConfirm': '移除远端「{name}」？',
      'vcs.remoteName': '名称',
      'vcs.remoteUrl': '地址',
      'vcs.remoteNamePlaceholder': 'origin',
      'vcs.remoteUrlPlaceholder': 'git@github.com:org/repo.git',
      'vcs.remoteAdded': '已添加远端 {name}。',
      'vcs.remoteRemoved': '已移除远端 {name}。',
      'vcs.remoteSaved': '远端地址已更新。',
      'vcs.fetch': '获取',
      'vcs.fetching': '获取中…',
      'vcs.fetched': '已获取 {name}。',
      'vcs.upstream': '上游',
      'vcs.tracking': '跟踪 {branch}',
      'vcs.noTracking': '未设置上游',
      'vcs.commitCount': '{n} 次提交',
      'vcs.localBranches': '本地分支',
      'vcs.remoteBranches': '远端分支',
      'mem.title': '记忆管理',
      'mem.viewList': '列表',
      'mem.viewGraph': '图谱',
      'mem.statTotal': '记忆',
      'mem.statLinked': '已关联',
      'mem.statEntities': '实体',
      'mem.statConfidence': '置信度',
      'mem.sideOverview': '概览',
      'mem.timeline': '活跃度 · 30 天',
      'mem.graphOpen': '记忆图谱',
      'mem.statsPanel': '统计',
      'mem.kindDist': '类型分布',
      'mem.sourceDist': '来源',
      'mem.topTags': '高频标签',
      'mem.topEntities': '挂接最多的实体',
      'mem.export': '导出 Markdown',
      'mem.rebuild': '从执行历史重建记忆',
      'mem.tidy': '整理记忆（合并重复、归档低价值）',
      'mem.tidyDone': '整理完成：合并 {merged} 条，归档 {archived} 条。',
      'mem.includeArchived': '含归档',
      'mem.archived': '已归档',
      'mem.archivedCount': '已归档',
      'mem.mergedCount': '合并 {n} 条',
      'mem.archiveDuplicate': '重复合并',
      'mem.archiveStale': '长期未用',
      'mem.archiveLow': '低置信度',
      'mem.archiveOverflow': '超出上限',
      'mem.clear': '清空全部记忆',
      'mem.rebuildConfirm': '重建会清空现有记忆，并在后台用 LLM 从对话历史蒸馏记忆（LLM 不可用时退回确定性提取）。继续？',
      'mem.rebuildRunning': '后台重建中',
      'mem.rebuildDone': '重建完成：蒸馏 {n} 条记忆（{calls} 次 LLM 调用）。',
      'mem.rebuildError': '重建失败：{msg}',
      'mem.clearConfirm': '清空该工作区的全部记忆？此操作不可恢复。',
      'mem.rebuilt': '重建完成：恢复 {n} 条记忆。',
      'mem.cleared': '记忆已清空。',
      'mem.chips': '{total} 条记忆 · {entities} 个实体 · {linked} 条挂接',
      'mem.searchPlaceholder': '搜索记忆',
      'mem.allKinds': '全部',
      'mem.sortRecent': '最新',
      'mem.sortOldest': '最旧',
      'mem.sortConf': '置信度',
      'mem.empty': '还没有记忆 — 每轮执行会自动沉淀经验。记忆的新增 / 修正 / 遗忘可通过对话完成。',
      'mem.emptyMatch': '没有匹配的记忆。',
      'mem.auto': '自动',
      'mem.manual': '手动',
      'mem.fail': '记忆操作失败：{msg}',
      'mem.linkedTo': '挂接',
      'mem.graphLegend': '颜色 = 记忆类型 · 方形 = 代码实体 · 实线 = 挂接 · 虚线 = 依赖',
      'mem.graphHint': '拖动节点或空白处移动，滚轮缩放，点击节点查看详情。',
      'team.title': 'Agent 团队',
      'team.tabTeams': '团队',
      'team.new': '新建团队',
      'team.empty': '还没有团队。创建一个主持 + 若干专长成员。',
      'team.name': '团队名称',
      'team.desc': '描述',
      'team.leader': '主持（编排者）',
      'team.expertise': '专长',
      'team.members': '成员',
      'team.addMember': '添加成员',
      'team.save': '保存',
      'team.delete': '删除',
      'team.bind': '用于当前对话',
      'team.unbind': '解除绑定',
      'team.boundToast': '已绑定团队「{name}」到当前对话',
      'team.unboundToast': '团队已从当前对话解绑',
      'team.saved': '团队已保存',
      'team.deleted': '团队已删除',
      'team.bindNeedConv': '先打开或新建一个对话',
      'team.nameRequired': '团队名称不能为空',
      'mem.graphZoomIn': '放大',
      'mem.graphZoomOut': '缩小',
      'mem.graphZoomFit': '适应',
      'mem.graphFilterAll': '全部',
      'mem.graphFilterMem': '记忆',
      'mem.graphFilterCode': '实体',
      'mem.graphEmpty': '图谱暂无内容：这个工作区还没有记忆。',
      'mem.graphTruncatedNodes': '仅显示最近的 {n} 个节点。',
      'mem.graphTruncated': '仅显示最近 {n} 条记忆。',
      'mem.graphOrphanNote': '{n} 条未挂接实体的记忆不在图谱中；点击节点查看详情。',
      'mem.statsOrphan': '未挂接记忆',
      'mem.statsAvgConf': '平均置信度',
      'mem.statsOldest': '最早记忆',
      'vcs.currentBranch': '分支 {name}',
      'vcs.aheadBehind': '↑{ahead} ↓{behind}',
      'vcs.remoteBranches': '远端分支',
      'vcs.current': '当前',
      'close': '关闭',
      'cancel': '取消',
      'save': '保存',
      'saveChanges': '保存修改',
      'download': '下载',
      'settings.subtitle': '修改保存在本地。',
      'settings.sections': '设置分区',
      'settings.workspaces': '工作区',
      'settings.workspacesNote': '每个工作区拥有自己的对话，并指向一个本地文件夹。归档只会隐藏工作区，不会删除对话或文件。',
      'settings.archivedWorkspaces': '已归档',
      'settings.modelEndpoint': '模型端点',
      'settings.subscriptions': '订阅',
      'settings.subscriptionsNote': '一个订阅就是一个账号（Base URL + API 密钥），下面可以挂多个模型。同一服务商可以添加多次，用来区分不同账号。',
      'settings.subscriptionsHint': '先获取订阅可用的模型，再按需添加任意多个。模型会在「模型」页和选择器中始终按订阅分组。',
      'settings.filterSubscriptions': '筛选订阅',
      'settings.filterModels': '筛选模型',
      'settings.providerOpenCodeGo': 'OpenCode Go',
      'settings.providerOpenCodeZen': 'OpenCode Zen',
      'settings.providerCustom': '自定义',
      'settings.subscriptionName': '订阅名称',
      'settings.protocolChatShort': 'OpenAI Chat',
      'settings.protocolResponseShort': 'OpenAI Responses',
      'settings.protocolAnthropicShort': 'Anthropic Messages',
      'settings.subscriptionCustomName': '自定义订阅',
      'settings.subscriptionModels': '{n} 个模型',
      'settings.subscriptionNoModels': '还没有模型',
      'settings.subscriptionActive': '当前',
      'settings.selectedSubscription': '当前选中的订阅',
      'settings.noSubscriptionSelected': '请先在左侧添加订阅，再管理它的模型。',
      'settings.deleteSubscription': '删除订阅',
      'settings.deleteSubscriptionConfirm': '删除订阅“{name}”及其下全部 {n} 个模型？',
      'settings.subscriptionAdded': '订阅已添加。',
      'settings.modelFolder': '模型',
      'settings.addModelTo': '向 {name} 添加模型',
      'settings.selectSubscription': '选择订阅',
      'settings.selectSubscriptionHint': '先选择账号，再选择该订阅下的模型。',
      'settings.backToSubscriptions': '全部订阅',
      'settings.filterSubscriptionModels': '筛选该订阅下的模型',
      'settings.noSubscriptionMatch': '没有匹配的订阅。',
      'settings.noModelMatch': '没有匹配的模型。',
      'settings.subscriptionNeedsUrl': '请先填写订阅的 OpenAI Base URL。',
      'platform.title': '平台预设',
      'platform.subtitle': '官方 API、聚合平台，以及自建 / 本地网关。',
      'platform.search': '搜索平台',
      'platform.add': '添加平台',
      'platform.custom': '自定义平台',
      'platform.customNote': '从空白开始，添加后填写 Base URL 与 API 密钥。',
      'platform.note': '本地网关预设使用常见默认端口；添加后可在订阅卡片里改成自己的地址。',
      'platform.group.custom': '自定义',
      'platform.group.opencode': 'OpenCode',
      'platform.group.aggregator': '聚合平台',
      'platform.group.local': '本地网关',
      'platform.group.official': '官方 API',
      'platform.gateway': '需本地网关',
      'platform.empty': '没有匹配的平台。',
      'platform.protocol': '{protocol}',
      'settings.models': '模型',
      'settings.modelsNote': '模型按订阅分组。新回复使用当前模型；端点与密钥来自所属订阅。',
      'settings.modelsWorkspaceNote': '左侧选择订阅（右键可编辑或删除），右侧只显示该订阅下的模型；默认选中第一个订阅。',
      'settings.addModel': '添加模型',
      'settings.defaultModel': '默认模型',
      'settings.active': '当前',
      'settings.edit': '编辑',
      'settings.view': '查看',
      'settings.viewSubscription': '查看订阅',
      'settings.apiKeyStateSet': 'API 密钥：已设置（{mask}）',
      'settings.apiKeyStateUnset': 'API 密钥：未设置',
      'settings.useModel': '使用此模型',
      'settings.name': '名称',
      'settings.namePlaceholder': '例如 Fast',
      'settings.baseUrl': 'OpenAI Base URL',
      'settings.useDefaultUrl': '沿用默认端点',
      'settings.model': '模型',
      'settings.modelBaseUrl': 'Base URL 覆盖',
      'settings.modelBaseUrlNote': '可选。留空时按该模型的协议使用所属订阅的端点。',
      'settings.useSubscriptionUrl': '订阅端点',
      'settings.discover': '获取模型',
      'settings.discovering': '获取中…',
      'settings.discoverHint': '该端点返回的模型',
      'settings.discoverEmpty': '该端点没有返回模型。',
      'settings.discoverError': '获取模型失败：{err}',
      'settings.useThisModel': '使用',
      'settings.addAsProfile': '添加为档案',
      'settings.discoverAddAll': '全部添加为档案',
      'settings.discoverCount': '{n} 个模型',
      'settings.discoverSame': '该模型已有档案。',
      'settings.discoverSkipped': '已跳过 {n} 个需要 Anthropic/Google 协议的模型。',
      'settings.discoverAdded': '已添加',
      'settings.discoverNote': '添加的模型会挂到当前选中的订阅下。',
      'settings.addSubscription': '添加订阅',
      'settings.editSubscription': '编辑订阅',
      'settings.subscriptionModalHint': '一个账号：OpenAI Base URL + 可选的 Anthropic Base URL + API 密钥；模型在右侧管理。',
      'settings.subscriptionContextHint': '右键可编辑或删除',
      'settings.saveSubscription': '保存',
      'settings.subscriptionUpdated': '订阅已更新。',
      'settings.quickAdd': '快速接入',
      'settings.channelName': '渠道名称',
      'settings.newModel': '新模型',
      'settings.channelModels': '{n} 个模型',
      'settings.deleteChannel': '删除渠道',
      'settings.deleteChannelConfirm': '删除渠道“{name}”及其下所有模型？',
      'settings.localChannel': '本地',
      'settings.baseUrlRequired': '请先填写 OpenAI Base URL。',
      'settings.usage': '用量',
      'ask.title': '{app} 需要你选择',
      'ask.singleHint': '选择一个选项。',
      'ask.multiHint': '可多选。',
      'ask.other': '其他（手动输入）',
      'ask.otherPlaceholder': '输入你的答案…',
      'ask.ignore': '忽略',
      'ask.submit': '确定',
      'subagent.title': '子代理 / 专家',
      'subagent.goal': '目标',
      'subagent.running': '运行中',
      'subagent.done': '完成',
      'subagent.failed': '失败',
      'subagent.noSteps': '还没有步骤',
      'diag.title': '诊断',
      'diag.success': '24h 成功率',
      'diag.runs': '运行数',
      'diag.p95': 'P95 时长',
      'diag.failed': '失败',
      'diag.queue': '队列 / 运行中',
      'diag.db': '数据库',
      'diag.backup': '最近备份',
      'diag.health': '健康',
      'diag.healthy': '正常',
      'diag.degraded': '异常',
      'diag.export': '导出诊断包',
      'diag.backupNow': '立即备份',
      'diag.backupDone': '已备份：{name}',
      'diag.exporting': '正在生成诊断包…',
      'usage.period1d': '1 天',
      'usage.period7d': '7 天',
      'usage.period30d': '30 天',
      'usage.periodAll': '合计',
      'usage.refresh': '刷新',
      'usage.loading': '正在加载用量…',
      'usage.empty': '还没有用量记录。',
      'usage.loadFailed': '加载用量失败：{err}',
      'usage.totalTokens': '总 token',
      'usage.input': '输入',
      'usage.output': '输出',
      'usage.total': '合计',
      'usage.cacheRate': '缓存命中',
      'usage.cachedSub': '缓存 {n}',
      'usage.repliesSub': '{n} 次回复',
      'usage.cacheSub': '{cached} / {prompt} 输入 token',
      'usage.replies': '回复数',
      'usage.rounds': '轮数',
      'usage.llmRoundsShort': 'LLM 轮',
      'usage.avgPerReply': '每次回复均值',
      'usage.avgPerDay': '日均',
      'usage.activeDaysShort': '活跃天',
      'usage.speedLabel': '速度',
      'usage.llmTimeShort': 'LLM 耗时',
      'usage.tokensUnit': 'token',
      'usage.topModel': '使用最多',
      'usage.peakDay': '峰值日期',
      'usage.cacheSaved': '缓存节省',
      'usage.cacheSavedSub': '命中的输入 token',
      'usage.chart': '最近 30 天',
      'usage.chartRange': '最近 {n} 天',
      'usage.chartLegend': '输入 / 输出',
      'usage.byModel': '按模型',
      'usage.byChannel': '按渠道',
      'usage.recent': '最近运行',
      'usage.model': '模型',
      'usage.channel': '渠道',
      'usage.share': '占比',
      'usage.colTime': '时间',
      'usage.duration': '耗时',
      'settings.opencodeGoQuota': 'OpenCode Go 额度',
      'settings.quotaFiveHour': '5 小时',
      'settings.quotaWeekly': '1 周',
      'settings.quotaMonthly': '1 个月',
      'settings.quotaUsed': '{used} / {limit}',
      'settings.quotaReset': '{time} 后重置',
      'settings.quotaRefresh': '刷新额度',
      'settings.quotaUnavailable': '额度不可用：{err}',
      'settings.quotaNoKey': '填写 OpenCode Go API key 后显示额度。',
      'settings.quotaUnsupported': '需要 Anthropic/Google 协议',
      'settings.apiKey': 'API 密钥',
      'settings.apiKeyNotSet': '未设置',
      'settings.apiKeyUnchanged': '••••••••（保持不变）',
      'settings.useDefaultKey': '沿用默认密钥',
      'settings.apiKeyKeep': '留空或保持不变即可保留当前密钥。',
      'settings.contextWindow': '上下文窗口',
      'settings.maxTokens': '最大 Token 数',
      'settings.protocol': 'API 协议',
      'settings.protocolChat': 'OpenAI Chat（chat/completions）',
      'settings.protocolResponse': 'OpenAI Responses（Responses API）',
      'settings.protocolAnthropic': 'Anthropic Messages（/v1/messages）',
      'settings.anthropicBaseUrl': 'Anthropic Base URL',
      'settings.anthropicBaseUrlPlaceholder': 'https://api.anthropic.com',
      'settings.anthropicBaseUrlNote': '仅 Anthropic Messages 协议的模型使用；留空则使用上方的 OpenAI Base URL。',
      'settings.vision': '图片支持',
      'settings.visionNote': '允许该模型接收上传的图片（vision）。',
      'settings.visionLabel': '该模型可以读取图片',
      'settings.thinkingLevels': '思考级别',
      'settings.thinkingLevelsNote': '勾选该模型支持的思考级别；勾选后输入框发送处会出现选择器，全部不勾选则不显示。',
      'settings.thinkingCustom': '自定义级别',
      'settings.thinkingCustomPlaceholder': '例如 xhigh / 1 / think',
      'settings.thinkingAdd': '添加',
      'settings.thinkingRemove': '移除自定义级别',
      'settings.loadingModels': '正在加载模型…',
      'settings.modelsFailed': '无法加载模型。',
      'settings.noModels': '还没有模型。',
      'settings.deleteModel': '删除模型',
      'settings.deleteModelConfirm': '删除模型“{name}”？\n\n此操作无法撤销。',
      'settings.modelActivated': '已切换到 {name}。',
      'settings.manageModels': '管理模型',
      'settings.runtime': '运行时',
      'settings.tools': '工具',
      'settings.codegraph': '代码图谱',
      'settings.codegraphFiles': '文件',
      'settings.codegraphSymbols': '符号',
      'settings.codegraphDeps': '依赖',
      'settings.codegraphNote': 'CodeGraph 是可选工具，是否使用由模型判断。索引会自动初始化，并在文件变化后增量更新。',
      'settings.codegraphRefresh': '重建索引',
      'settings.codegraphRefreshed': 'CodeGraph 索引已重建。',
      'settings.codegraphUpdated': '索引更新于 {time}；是否查询由模型决定。',
      'codegraph.title': '代码图谱',
      'codegraph.init': '初始化',
      'codegraph.update': '更新',
      'codegraph.rebuild': '重建',
      'codegraph.files': '{n} 个文件',
      'codegraph.symbols': '{n} 个符号',
      'codegraph.deps': '{n} 个依赖',
      'codegraph.updated': '更新于 {time}',
      'codegraph.empty': '还没有索引文件。点击「初始化」建立代码图谱。',
      'codegraph.running': '正在更新索引…',
      'codegraph.done': 'CodeGraph 已更新。',
      'codegraph.initDone': 'CodeGraph 已初始化。',
      'codegraph.rebuildDone': 'CodeGraph 已重建。',
      'codegraph.failed': 'CodeGraph 失败：{msg}',
      'codegraph.openFile': '打开文件',
      'codegraph.noSymbols': '没有顶层符号',
      'codegraph.searchPlaceholder': '搜索文件或符号',
      'codegraph.viewTree': '目录树',
      'codegraph.viewFlat': '平铺',
      'codegraph.noMatch': '没有匹配的文件。',
      'codegraph.statFiles': '文件',
      'codegraph.statSymbols': '符号',
      'codegraph.statDeps': '依赖',
      'codegraph.statUpdated': '更新',
      'codegraph.imports': '导入',
      'settings.service': '服务',
      'settings.address': '地址',
      'settings.config': '配置文件',
      'settings.notConfigured': '模型端点尚未配置。打开设置添加基础 URL 和模型。',
      'settings.nothingToSave': '没有需要保存的修改。',
      'settings.saving': '保存中…',
      'settings.saved': '已保存，新运行将使用这些设置。',
      'settings.savedToast': '模型设置已保存。',
      'settings.saveFailed': '保存失败：{msg}',
      'language.title': '语言',
      'language.systemNamed': '跟随系统（{lang}）',
      'language.note': '默认跟随浏览器语言，可随时切换。',
      'language.switchTitle': '切换语言',
      'empty.title': '今天想完成什么？',
      'empty.body': '提问、查看工作区，或让 {app} 自主执行多步任务。',
      'empty.suggest1': '检查工作区里的文件',
      'empty.suggest2': '写一个 Python 脚本处理小任务',
      'empty.suggest3': '调研一个主题并总结',
      'empty.suggest4': '把这个目标拆成任务清单',
      'empty.suggestChat1': '帮我把一个目标理清楚',
      'empty.suggestChat2': '为这个想法梳理一份提纲',
      'empty.bodyChatOnly': '提问、梳理目标，或让 {app} 自主执行多步任务。',
      'loading.conversation': '正在加载对话…',
      'chat.new': '新对话',
      'chat.untitled': '未命名对话',
      'chat.unread': '未读',
      'mention.title': '@ 成员',
      'newChat.title': '新建会话',
      'newChat.subtitle': '开一个单聊，或者创建 / 加入一个群聊。',
      'newChat.single': '单聊',
      'newChat.group': '群聊',
      'newChat.existingGroup': '加入已有群',
      'newChat.newGroup': '或新建一个群',
      'newChat.groupName': '群名',
      'newChat.groupHint': '群名留空 = 加入选中的群；填写群名 = 新建并绑定。',
      'newChat.create': '创建',
      'newChat.emptyGroups': '还没有群——在下面填个群名就能创建。',
      'newChat.failed': '创建失败：{msg}',
      'conv.info': '会话信息',
      'conv.conversation': '会话',
      'conv.workspace': '工作区',
      'conv.model': '模型',
      'conv.group': '群聊',
      'conv.single': '单聊（未绑定群）',
      'conv.teamName': '群名',
      'conv.mode': '协作模式',
      'conv.mode.orchestrate': '主持编排',
      'conv.mode.roundtable': '圆桌',
      'conv.mode.single': '单角',
      'conv.maxSpeakers': '每条消息最多角色数',
      'conv.members': '成员',
      'conv.addMember': '添加',
      'conv.memberName': '成员名字',
      'conv.memberExpertise': '专长',
      'conv.save': '保存',
      'conv.bind': '绑定',
      'conv.unbind': '解绑',
      'conv.bindExisting': '绑定已有群',
      'conv.createGroup': '新建群聊',
      'conv.rename': '重命名',
      'conv.noMembers': '还没有成员',
      'conv.saved': '已保存',
      'conv.groupHint': '之后的运行按此模式执行；圆桌模式下成员会轮流应答。',
      'conv.noConversation': '先打开一个会话',
      'conv.tools': '工具',
      'conv.loading': '加载中…',
      'conv.memberTools': '{n} 个工具',
      'conv.removeMember': '移除成员',
      'conv.leaderName': '主持',
      'chat.emptyPreview': '暂无消息',
      'chat.renameTitle': '重命名对话',
      'chat.titleHint': '可以手动保存标题，也可以让 AI 生成一个简短标题。',
      'chat.titleField': '标题',
      'chat.titlePlaceholder': '对话标题',
      'chat.regenerateTitle': 'AI 重新生成',
      'chat.titleGenerating': '正在生成标题…',
      'chat.titleGenerated': '标题已更新',
      'chat.titleFailed': '标题生成失败，请检查模型配置。',
      'chat.titleSaved': '标题已保存',
      'chat.titleEmpty': '标题不能为空',
      'chat.noMatch': '没有匹配的对话。',
      'chat.empty': '还没有对话。<br>在下方开始一个。',
      'chat.delete': '删除对话',
      'chat.clear': '删除全部对话',
      'chat.clearTitle': '删除全部对话',
      'chat.clearConfirm': '“{ws}”下的 {n} 个对话将被永久删除（含消息、运行记录与附件），此操作无法撤销。',
      'chat.clearNone': '没有可删除的对话。',
      'chat.cleared': '已删除 {n} 个对话。',
      'conv.messageCount': '{n} 条消息',
      'conv.more': '显示更多会话',
      'conv.expand': '展开会话',
      'conv.collapse': '收起会话',
      'conv.all': '全部会话',
      'conv.recent': '最近会话',
      'conv.pickerSubtitle': '{name} · {n} 个对话',
      'conv.showArchived': '显示已归档',
      'conv.hideArchived': '隐藏已归档',
      'conv.archivedBadge': '已归档',
      'conv.prev': '上一页',
      'conv.next': '下一页',
      'conv.page': '第 {page} / {pages} 页',
      'conv.none': '这个工作区还没有对话。',
      'conv.section.active': '进行中',
      'conv.section.done': '完结',
      'conv.group.today': '今天',
      'conv.group.yesterday': '昨天',
      'conv.group.week': '过去 7 天',
      'conv.group.older': '更早',
      'activeRuns.title': '运行中的任务',
      'activeRuns.running': '运行中',
      'activeRuns.queued': '排队中',
      'activeRuns.stop': '停止',
      'activeRuns.stopTitle': '停止该任务',
      'activeRuns.answer': '回答',
      'activeRuns.answerTitle': '打开这个问题',
      'activeRuns.approve': '授权',
      'activeRuns.approveTitle': '打开这个授权请求',
      'activeRuns.waiting': '等待回答',
      'activeRuns.waitingApproval': '等待授权',
      'activeRuns.badgeTitle': '正在运行',
      'process.title': '进程',
      'process.run': '当前对话运行',
      'process.subagent': '子代理',
      'process.kindTool': '工具',
      'process.kindSubagent': '子代理',
      'process.kindRun': '对话',
      'process.stop': '停止该进程（当前会话不受影响）',
      'process.stopRun': '停止这一轮运行',
      'process.stopped': '进程已停止，当前会话不受影响',
      'process.stoppedRun': '运行已停止',
      'process.details': '进程详情',
      'process.detailStop': '停止进程',
      'process.fieldKind': '类型',
      'process.fieldStatus': '状态',
      'process.fieldStarted': '开始时间',
      'process.fieldElapsed': '已运行',
      'process.fieldId': '进程 ID',
      'process.fieldRun': '运行 ID',
      'process.fieldCall': '调用 ID',
      'process.fieldArgs': '参数',
      'process.fieldOutput': '输出',
      'process.running': '运行中',
      'process.cancelled': '正在停止…',
      'process.ended': '已结束',
      'process.noOutput': '暂无输出',
      'time.justNow': '刚刚',
      'header.notConfigured': '未配置',
      'header.modelSettings': '模型设置',
      'header.workspace': '工作区 · {name}',
      'header.chatWorkspace': '纯聊天 · {name}',
      'activity.queued': '排队中 — 等待执行…',
      'activity.starting': '正在启动…',
      'activity.calling': '正在调用 {tool}…',
      'activity.toolArgs': '模型正在准备 {tool} 的参数… {s}s',
      'activity.background': '{tool} 已转入后台运行 — 正在监督',
      'activity.thinking': '思考中…',
      'activity.compressing': '正在压缩上下文…',
      'activity.compacting': '正在压缩上下文… {s}s',
      'activity.planning': '规划中…',
      'activity.waitingFirst': '等待模型响应… {s}s',
      'activity.waitingMore': '模型仍在输出… {s}s',
      'activity.retrying': '自动重试中（{a}/{m}）…',
      'retry.notice': '网络不稳定，正在自动重试（{a}/{m}）',
      'stream.unsupported': '当前浏览器不支持实时流式传输。',
      'run.stopped': '运行已停止。',
      'run.stalled': '已经 {s}s 没有任何进展，可能卡住了。可以点停止结束这一轮，或直接发新消息继续。',
      'run.failed': '运行失败',
      'stop.requested': '正在强制停止…',
      'stop.pending': '已请求停止，但后端仍在收尾；可再次点击停止强制结束。',
      'error.openConversation': '无法打开对话：{msg}',
      'delete.confirm': '“{title}”及其消息、运行记录都会被永久删除，此操作无法撤销。',
      'delete.title': '删除对话',
      'delete.action': '删除',
      'confirm.title': '确认',
      'confirm.ok': '确认',
      'delete.thisConversation': '此对话',
      'delete.done': '对话已删除。',
      'delete.failed': '删除失败：{msg}',
      'error.tooLong': '消息过长（最多 8000 字符）。',
      'error.startConversation': '无法创建对话：{msg}',
      'intervention.sent': '已作为干预消息发送给正在生成的回复。',
      'error.send': '发送失败：{msg}',
      'proposal.dismissed': '建议已忽略。',
      'proposal.started': '自主执行已开始。',
      'proposal.status.pending': '待确认',
      'proposal.status.accepted': '已接受',
      'proposal.status.dismissed': '已忽略',
      'copy.copy': '复制',
      'preview.showCode': '源码',
      'preview.showPreview': '预览',
      'preview.open': '打开',
      'preview.preview': '预览',
      'copy.copied': '已复制',
      'copy.failed': '复制失败。',
      'copy.done': '已复制。',
      'copy.message': '复制消息',
      'msg.copy': '复制',
      'msg.edit': '编辑',
      'msg.recall': '撤回',
      'msg.queued': '排队中',
      'msg.cancel': '取消',
      'msg.save': '保存并重新发送',
      'msg.editHint': '其后的对话会被删除，文件恢复到这条消息之前。',
      'msg.running': '消息正在执行，请先停止运行后再编辑或撤回。',
      'msg.editDone': '消息已更新。',
      'msg.recallConfirm': '撤回这条消息？它及其后的对话都会被删除。',
      'msg.recallQueuedConfirm': '撤回这条排队消息？它会从队列中删除。',
      'msg.recallDone': '消息已撤回。',
      'rewind.title': '回退到检查点',
      'rewind.action': '回退',
      'rewind.subtitle': '恢复工作区文件，或删除这条消息及其后的对话。',
      'rewind.none': '这条消息还没有检查点。',
      'rewind.target': '检查点 · {time}',
      'rewind.files': '文件',
      'rewind.modified': '修改',
      'rewind.added': '新增',
      'rewind.deleted': '删除',
      'rewind.clean': '工作区与检查点一致。',
      'rewind.filesOption': '恢复工作区文件',
      'rewind.deleteAdded': '删除检查点之后新建的文件',
      'rewind.convOption': '删除这条消息及其后的对话',
      'rewind.filesNote': '将恢复 {count} 个文件',
      'rewind.noRestore': '文件内容无需恢复',
      'rewind.deleteNote': '将删除 {count} 个新文件',
      'rewind.convNote': '将删除 {count} 条消息',
      'rewind.pickOne': '至少勾选文件或对话。',
      'rewind.working': '正在回退…',
      'rewind.done': '已回退：恢复 {files} 个文件，删除 {deleted} 个文件，移除 {messages} 条消息。',
      'rewind.failed': '回退失败：{msg}',
      'rewind.unavailable': '不可用：{reason}',
      'rewind.incomplete': '检查点不完整，不会删除新增文件。',
      'rewind.messageGone': '这条消息已被删除。',
      'rewind.deleteAddedNA': '检查点不完整时不可用。',
      'rewind.noWorkspace': '该会话没有工作区目录。',
      'error.openFile': '无法打开文件：{msg}',
      'preview.truncated': '预览已截断',
      'preview.empty': '（空文件）',
      'preview.lines': '{lines} 行',
      'preview.modePreview': '预览',
      'preview.modeCode': '代码',
      'preview.modeLabel': '查看方式',
      'preview.wrap': '自动换行',
      'preview.copy': '复制内容',
      'preview.edit': '编辑',
      'preview.save': '保存',
      'preview.files': '文件列表',
      'preview.editing': '编辑中 · Ctrl+S 保存',
      'preview.readonly': '只读',
      'preview.saved': '文件已保存。',
      'preview.saveFailed': '保存失败：{msg}',
      'preview.pos': '行 {line} · 列 {col}',
      'preview.editorFallback': '编辑器未加载 · 纯文本',
      'preview.unavailable': '该文件类型无法在线预览，请下载后打开。',
      'preview.corrupt': '文件损坏或无法解析，请下载后打开。',
      'runtime.running': '{running} 个运行中',
      'runtime.runningQueued': '{running} 个运行中 · {queued} 个排队',
      'runtime.ready': '就绪',
      'runtime.offline': '离线',
      'error.unreachable': '无法连接 {app}：{msg}',
      'trace.if': '条件：{condition}',
      'trace.acceptRun': '接受并运行',
      'trace.dismiss': '忽略',
      'trace.proposalTitle': 'Agent 建议自主执行',
      'trace.arguments': '参数',
      'trace.thinking': '思考',
      'trace.diff': '改动内容',
      'trace.content': '内容',
      'trace.files': '条目',
      'trace.retry': '自动重试',
      'trace.packCall': '由工具包调用',
      'trace.process': '过程',
      'process.steps': '{n} 步',
      'process.tools': '工具 {n}',
      'process.thoughts': '思考 {n}',
      'process.retries': '重试 {n}',
      'trace.working': '处理中',
      'trace.running': '运行中',
      'trace.streamPlaceholder': '思考中…',
      'trace.interrupted': '本轮输出已中断，中间内容已保留。',
      'trace.contextCompressed': '上下文已压缩：{before} → {after} tokens（摘要来源 {source}，保留近期 {keep} tokens）。',
      'trace.compressing': '正在压缩上下文',
      'trace.compressed': '上下文已压缩',
      'trace.compressStartDetail': '上下文 {before} / 上限 {budget} tokens（工具输出 {tool} · 对话 {dialog} · 系统 {system}），开始压缩…',
      'trace.compressDoneDetail': '{before} → {after} tokens（摘要来源 {source}，保留近期 {keep} tokens，耗时 {duration}）。',
      'trace.compressPruned': '上下文就地清理：{before} → {after} tokens（清理 {n} 条旧工具输出，未调用摘要模型）。',
      'trace.thought.one': '{n} 条思考',
      'trace.thought.other': '{n} 条思考',
      'trace.tool.one': '{n} 个工具',
      'trace.tool.other': '{n} 个工具',
      'trace.suggestion.one': '{n} 条建议',
      'trace.suggestion.other': '{n} 条建议',
      'trace.note.one': '{n} 条说明',
      'trace.note.other': '{n} 条说明',
      'trace.step.one': '{n} 步',
      'trace.step.other': '{n} 步',
      'usage.in': '输入 {n}',
      'usage.convIn': '输入合计 {n}',
      'usage.convOut': '输出合计 {n}',
      'usage.convCache': '缓存均值 {p}%',
      'usage.convSpeed': '均速 {n} token/s',
      'usage.convDetail': '{turns} 次回复 · {rounds} 轮模型调用',
      'usage.convSpeedDetail': '按模型耗时计算（不含工具与等待）。',
      'context.chip': '当前上下文 {k} · 占可用输入预算 {p}%（{used} / {budget} tokens；窗口 {window}，输出预留 {out}，历史峰值 {peak}）',
      'usage.out': '输出 {n}',
      'usage.time': '用时 {t}',
      'usage.speed': '{n} token/s',
      'usage.cache': '缓存 {p}%',
      'usage.cacheDetail': '缓存 {c} / 输入 {p} token · {r} 轮',
      'usage.timeDetail': '模型 {l} · 工具与等待 {o}',
      'tool.read_file': '读取文件',
      'tool.read_office': '读取文档',
      'tool.write_file': '写入文件',
      'tool.edit_file': '编辑文件',
      'tool.preparing': '正在准备…',
      'tool.background': '后台运行',
      'tool.backgroundHint': '超过前台预算，已转入后台监督：模型继续做别的事，用 tool_job 查看或终止。',
      'tool.list_files': '列出文件',
      'tool.shell': 'Shell 命令',
      'tool.web': '网页',
      'tool.memory_read': '读取记忆',
      'tool.memory_write': '写入记忆',
      'tool.workspace': '工作区',
      'tool.time': '时间',
      'tool.calculate': '计算',
      'tool.report_plan': '执行计划',
      'tool.delegate': '委派子任务',
      'tool.propose_task': '建议',
      'tool.call_agent': '委派',
      'tool.call_role': '委派',
      'tool.call_subagent': '子代理',
      'tool.list_skills': '列出技能',
      'tool.use_skill': '使用技能',
      'tool.create_skill': '创建技能',
      'tool.install_skill': '安装技能',
      'tool.list_mcp': '列出 MCP 服务',
      'tool.add_mcp_server': '接入 MCP 服务',
      'tool.install_mcp': '安装 MCP 服务',
      'tool.vcs': '版本控制',
      'tool.codegraph': '代码图谱',
      'tool.tool_job': '后台任务',
      'tool.memory_forget': '遗忘记忆',
      'tool.todo': '任务清单',
      'tool.goal_report': '目标进度',
      'tool.goal_manage': '目标',
      'tool.plan_manage': '计划步骤',
      'tool.team_manage': '团队管理',
      'tool.role_manage': '角色库',
      'tool.app_manage': '全局 App 管理',
      'tool.call_team': '委派团队',
      'tool.ask_user': '向用户提问',
      'tool.save_analysis': '保存分析',
      'tool.fallback': '工具',
      'tools.note': '同类工具合并成「工具包」：一个包一个 function，用 op 选择动作；开发与仓库类（后台任务、版本控制、代码图谱）在 dev 包里，shell / workspace / ask_user 是独立工具；具体工具只在执行、权限与预览时使用。',
      'tools.standalone': '独立工具',
      'tools.chatOnly': '纯聊天工作区没有用户文件夹：命令与文件工具在托管目录里执行，仅中间存储（workspace）不可用。',
      'tools.needWorkspace': '需要本地文件夹',
      'tools.needTeam': 'target=team 需要先绑定团队',
      'tools.condTeam': '团队',
      'tools.pack.fs': '文件',
      'tools.pack.net': '联网与计算',
      'tools.pack.memory': '记忆',
      'tools.pack.plan': '任务与协作',
      'tools.pack.dev': '开发与仓库',
      'tools.pack.skill': '技能',
      'tools.pack.mcp': 'MCP',
      'approval.title': '需要你的批准',
      'approval.pending': 'Agent 准备执行 {tool}',
      'approval.deny': '拒绝',
      'approval.once': '允许一次',
      'approval.session': '本次对话允许',
      'approval.always': '始终允许',
      'approval.jump': '查看请求',
      'approval.approved': '已允许',
      'approval.denied': '已拒绝',
      'approval.expired': '未在规定时间内回答',
      'approval.risk.high': '高风险',
      'approval.risk.medium': '中风险',
      'approval.risk.low': '低风险',
      'approval.waiting': '等待你的决定…',
      'perm.label': '权限档位',
      'perm.once': 'Ask',
      'perm.auto': 'Auto',
      'perm.always': 'Allow',
      'perm.title': 'Ask：每次危险操作都询问；Auto：低/中危自动执行，高危询问；Allow：全部直接执行',
      'scroll.top': '回到顶部',
      'scroll.bottom': '回到底部',
      'outline.title': '上下文',
      'outline.count': '{n} 条',
      'outline.expand': '展开上下文大纲',
      'outline.collapse': '收起上下文大纲',
      'outline.jump': '跳到这条消息',
      'settings.mcp': 'MCP 服务',
      'mcp.name': '名称',
      'mcp.type': '类型',
      'mcp.package': 'npm 包',
      'mcp.installCommand': '安装命令（可选）',
      'mcp.installCommandNote': 'App 不继承终端 PATH；本机找不到 npm 时，可在这里填完整命令或绝对路径。',
      'mcp.installNeedsPackage': '请填写包名或安装命令。',
      'mcp.command': '命令（不用 npm）',
      'mcp.url': 'URL',
      'mcp.args': '参数',
      'mcp.install': '安装并接入',
      'mcp.add': '添加',
      'mcp.export': '导出',
      'mcp.import': '导入',
      'mcp.enable': '启用',
      'mcp.disable': '停用',
      'mcp.enabled': '启用',
      'mcp.edit': '编辑',
      'mcp.editTitle': '编辑 MCP 服务',
      'mcp.save': '保存',
      'mcp.saved': 'MCP 服务已保存。',
      'mcp.note': '备注',
      'mcp.notePlaceholder': '这个服务用来做什么',
      'mcp.env': '环境变量',
      'mcp.addVariable': '添加变量',
      'mcp.envNote': '只传给这个 stdio 进程，例如 API Key、数据目录。',
      'mcp.headers': '请求头',
      'mcp.addHeader': '添加请求头',
      'mcp.headersNote': '每次请求都会带上，例如 Authorization 或租户标识。',
      'mcp.removePair': '删除',
      'mcp.test': '测试',
      'mcp.delete': '删除',
      'mcp.tools': '{n} 个工具',
      'mcp.empty': '还没有 MCP 服务。下方添加一个，或安装 npm 包。',
      'mcp.npmMissing': '本机没有 npm，无法自动安装，可以手动指定命令。',
      'mcp.addServer': '添加 MCP 服务',
      'mcp.panelNote': 'stdio 服务运行本地 npm 包或命令；http/sse 服务指向远程 URL。',
      'mcp.addTitle': '添加 MCP 服务',
      'mcp.addSubtitle': 'stdio 运行本地 npm 包或命令；http/sse 连接远程端点。',
      'mcp.typeStdio': 'stdio · 本地',
      'mcp.typeHttp': 'http / sse · 远程',
      'mcp.addNote': '安装的包位于 data/agent_zero/mcp，工具名为 mcp__服务__工具。',
      'settings.skills': '技能',
      'settings.extensions': '命令与钩子',
      'ext.note': '斜杠命令放在 .atlas/commands（也兼容 Claude Code 的 .claude/commands）。钩子在工具前后和运行结束时执行本地 shell 命令；它是你自己的脚本，不替代权限审批。',
      'ext.commands': '斜杠命令',
      'ext.hooks': '钩子',
      'ext.addCommand': '新建命令',
      'ext.addHook': '新建钩子',
      'ext.hookTemplate': '创建 hooks.json',
      'ext.refresh': '刷新',
      'ext.name': '名称',
      'ext.argumentHint': '参数提示',
      'ext.description': '说明',
      'ext.body': '提示词正文',
      'ext.event': '事件',
      'ext.matcher': '工具匹配（正则）',
      'ext.matcherAll': '全部工具',
      'ext.matcherFiles': '文件',
      'ext.matcherShell': 'Shell',
      'ext.command': 'Shell 命令',
      'ext.timeout': '超时（秒）',
      'ext.save': '保存',
      'ext.cancel': '取消',
      'ext.delete': '删除',
      'ext.emptyCommands': '还没有斜杠命令。新建一个，或在 .atlas/commands 下放一个 Markdown 文件。',
      'ext.emptyHooks': '还没有配置钩子。',
      'ext.builtin': '内置',
      'ext.workspace': '工作区',
      'ext.commandSaved': '命令已保存。',
      'ext.commandDeleted': '命令已删除。',
      'ext.hookSaved': '钩子已保存。',
      'ext.hookDeleted': '钩子已删除。',
      'ext.templateCreated': 'hooks.json 已创建。',
      'ext.deleteCommandConfirm': '删除命令 /{name}？',
      'ext.deleteHookConfirm': '删除这个钩子？',
      'ext.badName': '名称只能包含字母、数字、短横线、下划线、中文与冒号。',
      'slash.title': '斜杠命令',
      'slash.help': '显示可用的斜杠命令',
      'slash.empty': '没有匹配的命令',
      'skill.name': '名称',
      'skill.add': '添加技能',
      'skill.panelNote': '手写 SKILL.md、上传 .md / .zip、安装 npm 包，或用 npx skills add <owner/repo> 拉取技能。',
      'skill.addTitle': '添加技能',
      'skill.addSubtitle': '手写 SKILL.md、上传文件，或从 npm / npx skills 安装。',
      'skill.srcManual': '手写',
      'skill.srcUpload': '上传',
      'skill.srcNpm': 'npm',
      'skill.srcNpx': 'npx skills',
      'skill.uploadHint': '选择 .md 文件，或包含 SKILL.md 及附属文件的 .zip',
      'skill.uploadNote': '技能名称取自 frontmatter 或首个标题。',
      'skill.npmPackage': 'npm 包',
      'skill.npmNote': '该包必须包含 SKILL.md。',
      'skill.description': '说明',
      'skill.content': 'SKILL.md',
      'skill.save': '保存技能',
      'skill.upload': '上传 .md / .zip',
      'skill.npm': '从 npm 安装',
      'skill.npxTitle': 'npx skills（Agent Skills 注册表）',
      'skill.npxNote': '执行 `npx skills add <owner/repo>`，只安装到本应用的技能库，不会改动其它 agent 的技能目录。',
      'skill.npxSource': '技能源',
      'skill.npxSkills': '技能名（可选）',
      'skill.npxList': '列出源里的技能',
      'skill.npxSearch': '搜索注册表',
      'skill.npxInstall': '安装',
      'skill.npxNeedSource': '填写技能源，例如 vercel-labs/agent-skills',
      'skill.npxBusy': '查询技能注册表…',
      'skill.npxInstalling': '安装技能中，可能需要一分钟…',
      'skill.npxNone': '没有找到。',
      'skill.npxInstalled': '已安装 {n} 个技能',
      'skill.npxSkipped': '源里还有 {n} 个技能未导入（上限 30）。',
      'skill.npxPicked': '已选择：{n}',
      'skill.download': '下载',
      'skill.files': '{n} 个文件',
      'skill.delete': '删除',
      'skill.empty': '还没有技能。写一份、上传一份，或从 npm 安装。',
      'trace.approval.one': '{n} 次审批',
      'trace.approval.other': '{n} 次审批',
      'perm.set': '已保存',
      'mcp.probeOk': '已连接 · {n} 个工具',
      'mcp.probeFail': '连接失败',
    },
  };

  function uiLocale() {
    return state.lang === 'zh' ? 'zh-CN' : 'en-US';
  }

  function systemLanguage() {
    const list = Array.isArray(navigator.languages) && navigator.languages.length
      ? navigator.languages
      : [navigator.language || 'en'];
    for (const item of list) {
      const code = String(item || '').toLowerCase();
      if (code.startsWith('zh')) return 'zh';
      if (code.startsWith('en')) return 'en';
    }
    return 'en';
  }

  function resolveLanguage(mode) {
    if (mode === 'zh' || mode === 'en') return mode;
    return systemLanguage();
  }

  function langDisplayName(lang) {
    return lang === 'zh' ? '中文' : 'English';
  }

  // 品牌名只有一个来源：/app/info 的 name（兜底 AtlasX）。
  // i18n 里的 {app} 由 t() 统一注入，改名不用再逐条改文案。
  function appBrand() {
    return (state.app && state.app.name) || 'AtlasX';
  }

  function t(key, params) {
    const dict = I18N[state.lang] || I18N.en;
    let value = dict[key];
    if (value === undefined) value = I18N.en[key];
    if (value === undefined) return key;
    if (value.indexOf('{') >= 0) {
      const all = { app: appBrand(), ...(params || {}) };
      value = value.replace(/\{(\w+)\}/g, (match, name) => (
        all[name] === undefined ? match : String(all[name])
      ));
    }
    return value;
  }

  function proposalStatus(status) {
    const key = `proposal.status.${status}`;
    const label = t(key);
    return label === key ? String(status || '') : label;
  }

  // Backend validation errors are written in Chinese. Keep the English UI
  // readable for the handful of messages the local API can return.
  const SERVER_ERROR_EN = [
    [/^工作区不存在[:：]\s*(.+)$/, 'Workspace not found: $1'],
    [/^工作区已归档[:：]\s*(.+)$/, 'This workspace is archived: $1'],
    [/^工作区目录不存在[:：]\s*(.+)$/, 'Workspace folder not found: $1'],
    [/^工作区目录必须是绝对路径[:：]\s*(.+)$/, 'Workspace folder must be an absolute path: $1'],
    [/^工作区目录不可用[:：]\s*(.+)$/, 'Workspace folder is unavailable: $1'],
    [/^工作区名称不能为空$/, 'Workspace name cannot be empty.'],
    [/^默认工作区不能归档$/, 'The default workspace cannot be archived.'],
    [/^该文件夹已作为工作区存在[:：]\s*(.+)$/, 'This folder is already a workspace: $1'],
    [/^文件夹不存在[:：]\s*(.+)$/, 'Folder not found: $1'],
    [/^不是文件夹[:：]\s*(.+)$/, 'Not a folder: $1'],
    [/^请使用绝对路径[:：]\s*(.+)$/, 'Use an absolute path: $1'],
    [/^文件夹不可用[:：]\s*(.+)$/, 'Folder is unavailable: $1'],
    [/^无法读取文件夹[:：]\s*(.+)$/, 'Could not read folder: $1'],
    [/^对话不存在[:：]\s*(.+)$/, 'Conversation not found: $1'],
    [/^运行不存在[:：]\s*(.+)$/, 'Run not found: $1'],
    [/^执行建议不存在[:：]\s*(.+)$/, 'Suggestion not found: $1'],
    [/^转任务建议不存在[:：]\s*(.+)$/, 'Suggestion not found: $1'],
    [/^非法工作区类型[:：]\s*(.+?)（允许[:：]\s*(.+)）$/, 'Invalid workspace kind: $1 (allowed: $2)'],
    [/^非法工作区类型[:：]\s*(.+)$/, 'Invalid workspace kind: $1'],
    [/^非法工作区 id[:：]\s*(.+)$/, 'Invalid workspace id: $1'],
    [/^路径不存在[:：]\s*(.+)$/, 'Path not found: $1'],
    [/^不是目录[:：]\s*(.+)$/, 'Not a directory: $1'],
    [/^文件不存在[:：]\s*(.+)$/, 'File not found: $1'],
    [/^目标是目录，无法写入[:：]\s*(.+)$/, 'Target is a directory and cannot be written: $1'],
    [/^搜索目录不存在[:：]\s*(.+)$/, 'Search directory not found: $1'],
    [/^非法运行模式[:：]\s*(.+)$/, 'Invalid run mode: $1'],
    [/^该对话已有正在运行的回复[:：]\s*(.+)$/, 'This conversation already has a running reply: $1'],
    [/^非法终态[:：]\s*(.+)$/, 'Invalid terminal state: $1'],
    [/^运行创建失败（并发冲突）$/, 'Could not create the run (concurrent request).'],
    [/^该建议已处理，无需重复执行$/, 'This suggestion has already been handled.'],
    [/^仅允许工作区内的相对路径$/, 'Only relative paths inside the workspace are allowed.'],
    [/^路径超出工作区范围$/, 'Path is outside the workspace.'],
    [/^禁止删除工作区根$/, 'The workspace root cannot be deleted.'],
    [/^消息为空$/, 'Message is empty.'],
    [/^缺少执行目标 goal$/, 'Missing execution goal.'],
    [/^模型不存在[:：]\s*(.+)$/, 'Model not found: $1'],
    [/^模型 code 重复[:：]\s*(.+)$/, 'Duplicate model code: $1'],
    [/^当前模型未开启图片支持，无法上传图片$/, 'The current model does not support images. Enable image support in Settings → Models or switch models.'],
    [/^一次最多上传\s*(\d+)\s*个附件$/, 'At most $1 attachments per message.'],
    [/^附件过大（单个上限 25 MB）[:：]\s*(.+)$/, 'Attachment is larger than 25 MB: $1'],
    [/^附件总大小超过 50 MB$/, 'Attachments exceed 50 MB in total.'],
    [/^不支持的文件类型[:：]\s*(.+)$/, 'Unsupported file type: $1'],
    [/^附件为空[:：]\s*(.+)$/, 'Attachment is empty: $1'],
    [/^无法解析\s*(.+?)[:：]\s*(.*)$/, 'Could not parse $1: $2'],
    [/^无法从\s*(.+?)\s*中提取文本$/, 'Could not extract text from $1.'],
    [/^服务重启，运行中断$/, 'Service restarted; the run was interrupted.'],
    [/^LLM 配置不完整[:：]\s*(.*)$/, 'Incomplete LLM configuration: set base_url and model in config/agent_zero/config.yaml (or use AGENT_ZERO_LLM_BASE_URL / AGENT_ZERO_LLM_MODEL).'],
    [/^LLM 网络错误\s*\(([^)]+)\)[:：]\s*(.*)$/, 'LLM network error ($1): $2'],
    [/^LLM 调用失败\s*\(([^)]+)\)[:：]\s*(.*)$/, 'LLM request failed ($1): $2'],
    [/^LLM 流式调用失败\s*\(([^)]+)\)[:：]\s*(.*)$/, 'LLM streaming request failed ($1): $2'],
    [/^LLM 响应解析失败[:：]\s*(.*)$/, 'Could not parse the LLM response: $1'],
    [/^无法从 LLM 输出解析 JSON[:：]\s*(.*)$/, 'Could not parse JSON from the LLM output: $1'],
  ];

  function localizeServerMessage(message) {
    const raw = String(message || '');
    if (state.lang !== 'en') return raw;
    for (const [pattern, replacement] of SERVER_ERROR_EN) {
      if (pattern.test(raw)) return raw.replace(pattern, replacement);
    }
    return raw;
  }

  let renderQueued = false;
  let activityTimer = null;

  /* ── small utilities ─────────────────────────────────────────────────── */

  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escAttr(s) {
    return escapeHtml(s).replace(/`/g, '&#96;');
  }

  function truncate(s, n = 80) {
    const t = String(s ?? '').replace(/\s+/g, ' ').trim();
    return t.length > n ? `${t.slice(0, n - 1)}…` : t;
  }

  function bytes(n) {
    const v = Number(n) || 0;
    if (v < 1024) return `${v} B`;
    if (v < 1024 * 1024) return `${(v / 1024).toFixed(v < 10240 ? 1 : 0)} KB`;
    return `${(v / 1024 / 1024).toFixed(1)} MB`;
  }

  function duration(ms) {
    const v = Math.max(0, Number(ms) || 0);
    if (!v) return '';
    if (v < 1000) return `${v}ms`;
    if (v < 60_000) return `${(v / 1000).toFixed(v < 10_000 ? 1 : 0)}s`;
    const m = Math.floor(v / 60_000);
    const s = Math.round((v % 60_000) / 1000);
    return `${m}m ${s}s`;
  }

  function num(n) {
    return (Number(n) || 0).toLocaleString();
  }

  // 紧凑数字：1234 -> 1.2k，123456 -> 12万，123456789 -> 1.2亿。
  // 只用于 token 类大数字（面板里塞得下、一眼能读）；普通计数仍用 num()。
  function compact(n) {
    const raw = Number(n) || 0;
    const sign = raw < 0 ? '-' : '';
    const v = Math.abs(raw);
    if (v < 1000) return sign + (Number.isInteger(v) ? String(v) : v.toFixed(1));
    // 一位小数只在 <10 时保留，再大的数取整；.0 一律去掉。
    const fix = (x) => (x < 10 ? x.toFixed(1) : x.toFixed(0)).replace(/\.0$/, '');
    if (v < 1e4) return sign + fix(v / 1e3) + 'k';
    if (v < 1e8) {
      const wan = v / 1e4;
      // 四舍五入后跨到 1 亿（9999.9 万）时交给亿格式化，避免出现「10000 万」。
      if (Number(fix(wan)) < 1e4) return sign + fix(wan) + '万';
      return sign + fix(v / 1e8) + '亿';
    }
    return sign + fix(v / 1e8) + '亿';
  }

  function toDate(value) {
    if (!value) return null;
    const d = new Date(String(value).replace(' ', 'T'));
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function clock(value) {
    const d = toDate(value);
    if (!d) return '';
    return d.toLocaleTimeString(uiLocale(), { hour: '2-digit', minute: '2-digit' });
  }

  function relativeTime(value) {
    const d = toDate(value);
    if (!d) return '';
    const diff = Date.now() - d.getTime();
    if (diff < 45_000) return t('time.justNow');
    const rtf = new Intl.RelativeTimeFormat(uiLocale(), { numeric: 'auto' });
    if (diff < 3_600_000) return rtf.format(-Math.floor(diff / 60_000), 'minute');
    if (diff < 86_400_000) return rtf.format(-Math.floor(diff / 3_600_000), 'hour');
    if (diff < 7 * 86_400_000) return rtf.format(-Math.floor(diff / 86_400_000), 'day');
    return d.toLocaleDateString(uiLocale(), { month: 'short', day: 'numeric' });
  }

  function localStamp() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  async function api(path, options = {}) {
    const opts = { ...options };
    opts.headers = { ...(options.headers || {}) };
    if (opts.body && !opts.headers['Content-Type']) {
      opts.headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(API + path, opts);
    const text = await res.text();
    let body = null;
    if (text) {
      try { body = JSON.parse(text); } catch { body = text; }
    }
    if (!res.ok) {
      const detail = (body && body.detail) || (typeof body === 'string' && body) || res.statusText;
      const message = Array.isArray(detail)
        ? detail.map((d) => d.msg || d).join('; ')
        : String(detail);
      throw new Error(localizeServerMessage(message));
    }
    if (body && typeof body === 'object' && 'data' in body) return body.data;
    return body;
  }

  function toast(message, kind = 'ok', timeout = 4200) {
    const wrap = $('#toasts');
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `<svg><use href="#i-${kind === 'bad' ? 'alert' : 'check'}"/></svg><div></div>`;
    el.lastElementChild.textContent = String(message || '');
    wrap.appendChild(el);
    const remove = () => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 220);
    };
    el.addEventListener('click', remove);
    if (timeout) setTimeout(remove, timeout);
  }

  /* ── markdown ────────────────────────────────────────────────────────── */

  function inlineMd(raw) {
    const codes = [];
    let s = escapeHtml(raw).replace(/`([^`\n]+)`/g, (_, code) => {
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

  function preBlock(code, lang) {
    const cls = lang ? ` class="language-${escAttr(lang)}"` : '';
    return `<pre><button class="code-copy" type="button" data-copy>${escapeHtml(t('copy.copy'))}</button>` +
      `<code${cls}>${escapeHtml(code)}</code></pre>`;
  }

  const PREVIEW_EXT_RE = /\.(html|htm|svg)$/i;

  function pvHash(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i += 1) {
      h ^= s.charCodeAt(i);
      h = (h * 16777619) >>> 0;
    }
    return h.toString(36) + s.length.toString(36);
  }

  function isPreviewableLang(lang) {
    return ['html', 'htm', 'svg'].includes(String(lang || '').toLowerCase());
  }

  function pvBlock(code, lang) {
    const key = pvHash(code);
    const showCode = state.pvCode.has(key);
    const l = String(lang || 'html').toLowerCase();
    return `<div class="pv" data-pv-key="${escAttr(key)}">
      <div class="pv-bar">
        <span class="pv-lang mono">${escapeHtml(l)}</span>
        <div class="pv-actions">
          <button type="button" class="pv-toggle" data-pv-toggle aria-pressed="${String(showCode)}">${escapeHtml(showCode ? t('preview.showPreview') : t('preview.showCode'))}</button>
          <button type="button" class="pv-open" data-pv-open="${escAttr(code)}" title="${escAttr(t('preview.open'))}">${escapeHtml(t('preview.open'))}</button>
          <button type="button" class="pv-copy" data-pv-copy>${escapeHtml(t('copy.copy'))}</button>
        </div>
      </div>
      <iframe class="pv-frame" sandbox="allow-scripts allow-modals" srcdoc="${escAttr(code)}" title="${escapeHtml(t('preview.preview'))}"></iframe>
      <pre class="pv-code"${showCode ? '' : ' hidden'}><code class="language-${escAttr(l)}">${escapeHtml(code)}</code></pre>
    </div>`;
  }

  function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(line);
  }

  function cellsOf(line) {
    let s = line.trim();
    if (s.startsWith('|')) s = s.slice(1);
    if (s.endsWith('|')) s = s.slice(0, -1);
    return s.split('|').map((c) => c.trim());
  }

  function renderMarkdown(src, preview = true) {
    const text = String(src || '').replace(/\r\n?/g, '\n');
    const lines = text.split('\n');
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
        const code = buf.join('\n');
        if (preview && isPreviewableLang(lang) && code.trim()) {
          out.push(pvBlock(code, lang));
        } else {
          out.push(preBlock(code, lang));
        }
        continue;
      }

      if (!line.trim()) { flush(); i += 1; continue; }

      const h = line.match(/^\s*(#{1,4})\s+(.+)$/);
      if (h) {
        flush();
        const level = h[1].length;
        out.push(`<h${level}>${inlineMd(h[2].replace(/\s+#+\s*$/, ''))}</h${level}>`);
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
        const head = cellsOf(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
          rows.push(cellsOf(lines[i]));
          i += 1;
        }
        out.push('<table><thead><tr>' +
          head.map((c) => `<th>${inlineMd(c)}</th>`).join('') +
          '</tr></thead><tbody>' +
          rows.map((r) => '<tr>' + r.map((c) => `<td>${inlineMd(c)}</td>`).join('') + '</tr>').join('') +
          '</tbody></table>');
        continue;
      }

      para.push(line);
      i += 1;
    }
    flush();
    return out.join('');
  }

  /* ── trace normalisation ─────────────────────────────────────────────── */

  const TOOL_LABELS = {
    read_file: 'tool.read_file',
    read_office: 'tool.read_office',
    read_document: 'tool.read_office',
    read_pdf: 'tool.read_office',
    write_file: 'tool.write_file',
    edit_file: 'tool.edit_file',
    list_files: 'tool.list_files',
    shell: 'tool.shell',
    web: 'tool.web',
    memory_read: 'tool.memory_read',
    memory_write: 'tool.memory_write',
    workspace: 'tool.workspace',
    time: 'tool.time',
    calculate: 'tool.calculate',
    report_plan: 'tool.report_plan',
    delegate: 'tool.delegate',
    propose_task: 'tool.propose_task',
    call_agent: 'tool.call_agent',
    call_role: 'tool.call_role',
    call_subagent: 'tool.call_subagent',
    call_team: 'tool.call_team',
    vcs: 'tool.vcs',
    codegraph: 'tool.codegraph',
    tool_job: 'tool.tool_job',
    memory_forget: 'tool.memory_forget',
    todo_write: 'tool.todo',
    todo: 'tool.todo',
    goal_report: 'tool.goal_report',
    goal_manage: 'tool.goal_manage',
    plan_manage: 'tool.plan_manage',
    team_manage: 'tool.team_manage',
    role_manage: 'tool.role_manage',
    app_manage: 'tool.app_manage',
    ask_user: 'tool.ask_user',
    save_analysis: 'tool.save_analysis',
    list_skills: 'tool.list_skills',
    use_skill: 'tool.use_skill',
    create_skill: 'tool.create_skill',
    install_skill: 'tool.install_skill',
    list_mcp: 'tool.list_mcp',
    add_mcp_server: 'tool.add_mcp_server',
    install_mcp: 'tool.install_mcp',
  };

  const TOOL_ICONS = {
    read_file: 'eye',
    read_office: 'file',
    read_document: 'file',
    read_pdf: 'file',
    write_file: 'file',
    edit_file: 'file',
    list_files: 'list',
    shell: 'terminal',
    tool_job: 'clock',
    web: 'globe',
    memory_read: 'brain',
    memory_write: 'brain',
    report_plan: 'list',
    propose_task: 'spark',
    call_agent: 'spark',
    call_role: 'spark',
    call_subagent: 'spark',
    delegate: 'agent',
  };

  function toolLabel(name) {
    return TOOL_LABELS[name] ? t(TOOL_LABELS[name]) : (name || t('tool.fallback'));
  }

  function toolIcon(name) {
    return TOOL_ICONS[name] || 'terminal';
  }

  /* opencode 式一行：动作名后面跟它**作用的对象**（路径 / 命令 / 查询），
     而不是重复工具参数或结果摘要。完成后折叠起来，一条调用只占一行。 */

  function oneLine(text, max = 220) {
    const s = String(text || '').replace(/\s+/g, ' ').trim();
    return s.length > max ? `${s.slice(0, max)}…` : s;
  }

  function stepArgsObject(step) {
    const a = step && step.args;
    if (!a) return {};
    if (typeof a === 'object') return a;
    try {
      const obj = JSON.parse(a);
      return obj && typeof obj === 'object' ? obj : {};
    } catch { return {}; }
  }

  // 长路径中段省略（macos/Sources/…/ServerSettings.swift）：尾部省略会吃掉文件名
  function shortPath(path, max = 64) {
    const p = String(path || '');
    if (p.length <= max) return p;
    const parts = p.split('/');
    if (parts.length < 4) return p;
    return `${parts.slice(0, 2).join('/')}/…/${parts[parts.length - 1]}`;
  }

  // 优先取「动作对象」这类参数；顺序即优先级（路径 > 命令 > 网址 > 查询…）
  const SUBJECT_KEYS = [
    'path', 'file', 'files', 'cmd', 'command', 'url', 'query', 'pattern',
    'goal', 'title', 'name', 'key', 'op', 'action',
  ];

  function stepSubject(step) {
    const disp = step && step.display;
    if (disp && ['diff', 'file', 'directory'].includes(disp.type)) {
      return oneLine(shortPath(disp.path || ''));
    }
    const args = stepArgsObject(step);
    for (const k of SUBJECT_KEYS) {
      const v = args[k];
      if (typeof v === 'string' && v.trim()) return oneLine(v);
      if (Array.isArray(v)) {
        const str = v.filter((x) => typeof x === 'string' && x.trim()).join(' ');
        if (str) return oneLine(str);
      }
    }
    const first = Object.values(args).find((v) => typeof v === 'string' && v.trim());
    if (first) return oneLine(first);
    return '';
  }

  function stepSubjectTitle(step, subject) {
    const disp = step && step.display;
    if (disp && disp.path) return String(disp.path);
    return subject;
  }

  // 行尾的紧凑统计：diff 给 +n/-n，读文件给行号区间，列目录给条目数。
  function stepStatHtml(step) {
    const disp = step && step.display;
    if (!disp) return '';
    if (disp.type === 'diff') {
      return `<span class="step-stat"><b class="is-add">+${Number(disp.added) || 0}</b>` +
        `<i class="is-sep">/</i>` +
        `<b class="is-del">-${Number(disp.removed) || 0}</b></span>`;
    }
    if (disp.type === 'file') {
      return `<span class="step-stat">${escapeHtml(`${disp.lineStart || 1}-${disp.lineEnd || 0} / ${disp.totalLines || 0}`)}</span>`;
    }
    if (disp.type === 'directory') {
      return `<span class="step-stat">${escapeHtml(String(disp.totalEntries || 0))}</span>`;
    }
    return '';
  }


  function stepTime(step) {
    const n = Number(step.ts);
    return Number.isFinite(n) ? n : null;
  }

  function normalizeTrace(trace) {
    const items = [];
    const tools = new Map();
    for (const raw of trace || []) {
      if (!raw || typeof raw !== 'object') continue;
      const s = { ...raw };
      if (s.kind === 'tool') {
        if (s.id && tools.has(s.id)) {
          Object.assign(tools.get(s.id), s);
        } else {
          if (s.status === undefined && (s.ok !== undefined || s.duration_ms !== undefined)) {
            s.status = 'done';
          }
          if (s.status === undefined) s.status = 'done';
          items.push(s);
          if (s.id) tools.set(s.id, s);
        }
      } else {
        items.push(s);
      }
    }
    const timed = items.every((s) => stepTime(s) !== null);
    if (timed) {
      items.sort((a, b) => stepTime(a) - stepTime(b));
    }
    return items;
  }

  // 一条回复 = 按真实发生顺序交错的时间线：思考 / 工具 / 中间正文 / 最终答复。
  // 后端落库时会把「收尾答复」从 trace 摘出来放进 content，这里再把它接回
  // 时间线末尾（已经在末尾的文本不重复追加）。
  function buildFlow(turn) {
    const steps = normalizeTrace(turn.trace);
    const entries = [];
    const counts = new Map();
    const keyOf = (step) => {
      if (step.kind === 'tool' && step.id) return `tool:${step.id}`;
      const base = step.final ? 'text.final' : `${step.kind || 'text'}:${step.round ?? ''}`;
      const n = counts.get(base) || 0;
      counts.set(base, n + 1);
      return `${base}:${n}`;
    };
    for (const step of steps) {
      // 旧会话里可能残留 kind=plan 的执行计划轨迹（功能已下线）：跳过，不渲染空块。
      if (!step || step.kind === 'plan') continue;
      entries.push({ step, key: keyOf(step) });
    }

    const body = String(turn.content || '').trim();
    if (!turn.pending && body) {
      let lastText = null;
      for (let i = entries.length - 1; i >= 0; i -= 1) {
        if (entries[i].step.kind === 'text') { lastText = entries[i]; break; }
      }
      if (lastText && String(lastText.step.text || '').trim() === body) {
        lastText.step.final = true;
      } else {
        const step = { kind: 'text', text: turn.content, final: true };
        entries.push({ step, key: keyOf(step) });
      }
    }
    return entries;
  }

  let transcriptTraceSeq = 0;
  function touchStep(step) {
    if (!step) return step;
    transcriptTraceSeq += 1;
    step._v = transcriptTraceSeq;
    return step;
  }

  function detailOpen(key, fallback) {
    return state.openDetails.has(key) ? state.openDetails.get(key) : fallback;
  }

  function renderProposal(step, turnId) {
    const status = step.status || 'pending';
    const id = step.proposal_id || '';
    const actions = status === 'pending'
      ? `<div class="proposal-actions">
           <button class="btn small primary" data-proposal="accept" data-proposal-id="${escAttr(id)}" data-turn="${escAttr(turnId)}">${escapeHtml(t('trace.acceptRun'))}</button>
           <button class="btn small ghost" data-proposal="dismiss" data-proposal-id="${escAttr(id)}" data-turn="${escAttr(turnId)}">${escapeHtml(t('trace.dismiss'))}</button>
         </div>`
      : `<div class="proposal-actions"><span class="pill" style="font-size:10px;color:var(--text-faint)">${escapeHtml(proposalStatus(status))}</span></div>`;
    return `<div class="proposal-card">
      <div class="proposal-head"><svg><use href="#i-spark"/></svg>${escapeHtml(t('trace.proposalTitle'))}</div>
      <div class="proposal-goal">${escapeHtml(step.goal || '')}</div>
      ${step.reason ? `<div class="proposal-reason">${escapeHtml(step.reason)}</div>` : ''}
      ${actions}
    </div>`;
  }

  function diffLineHtml(line) {
    if (line.startsWith('+') && !line.startsWith('+++')) return `<span class="diff-add">${escapeHtml(line)}</span>`;
    if (line.startsWith('-') && !line.startsWith('---')) return `<span class="diff-del">${escapeHtml(line)}</span>`;
    return `<span class="diff-ctx">${escapeHtml(line)}</span>`;
  }

  function diffHeadHtml(disp) {
    return `<span class="diff-path">${escapeHtml(disp.path || '')}</span>` +
      `<span class="diff-stat"><b class="diff-plus">+${disp.added || 0}</b>` +
      `<b class="diff-minus">-${disp.removed || 0}</b>${disp.truncated ? '…' : ''}</span>`;
  }

  function renderApproval(step) {
    const status = step.status || 'pending';
    const pending = status === 'pending';
    const risk = ['low', 'medium', 'high'].includes(step.risk) ? step.risk : 'medium';
    const reasons = (step.reasons || []).filter(Boolean).join(' · ');
    let args = '';
    try {
      args = typeof step.args === 'string' ? step.args : JSON.stringify(step.args || {}, null, 2);
    } catch { args = String(step.args || ''); }
    if (args === '{}') args = '';
    if (args.length > 900) args = `${args.slice(0, 900)}…`;
    const preview = step.preview;
    // opencode 式：写/改文件先给 diff 预览，用户看「改什么」再批准
    const previewHtml = preview && preview.diff
      ? `<div class="diff-block">
          <div class="diff-head">${diffHeadHtml(preview)}</div>
          <pre class="diff-pre">${preview.diff.split('\n').map(diffLineHtml).join('\n')}</pre>
        </div>`
      : '';
    const buttons = pending ? `<div class="approval-inline">
        <button class="btn small ghost" type="button" data-approval="deny" data-approval-id="${escAttr(step.id)}">${escapeHtml(t('approval.deny'))}</button>
        <button class="btn small" type="button" data-approval="approve_once" data-approval-id="${escAttr(step.id)}">${escapeHtml(t('approval.once'))}</button>
        <button class="btn small" type="button" data-approval="approve_session" data-approval-id="${escAttr(step.id)}">${escapeHtml(t('approval.session'))}</button>
        <button class="btn small primary" type="button" data-approval="approve_always" data-approval-id="${escAttr(step.id)}">${escapeHtml(t('approval.always'))}</button>
      </div>` : '';
    return `<div class="step approval ${escAttr(status)}" data-approval-step="${escAttr(step.id)}">
      <div class="step-icon"><svg><use href="#i-shield"/></svg></div>
      <div class="step-body">
        <div class="step-title">
          <span>${escapeHtml(t('approval.title'))}</span>
          <span class="name">${escapeHtml(step.name || '')}</span>
          <span class="risk-pill risk-${risk}">${escapeHtml(t(`approval.risk.${risk}`))}</span>
        </div>
        ${step.note ? `<div class="step-text">${escapeHtml(step.note)}</div>` : ''}
        ${reasons ? `<div class="approval-reasons">${escapeHtml(reasons)}</div>` : ''}
        ${previewHtml || (args ? `<pre class="step-pre">${escapeHtml(args)}</pre>` : '')}
        ${buttons || `<div class="approval-decided">${escapeHtml(
          status === 'approved' ? t('approval.approved')
            : status === 'expired' ? t('approval.expired') : t('approval.denied'))}</div>`}
      </div>
    </div>`;
  }

  function renderCompression(step) {
    const running = step.status === 'running';
    const statusIcon = running
      ? '<span class="spinner"></span>'
      : '<span class="ok"><svg><use href="#i-check"/></svg></span>';
    return `<div class="step thinking compress">
      <div class="step-icon"><svg><use href="#i-archive"/></svg></div>
      <div class="step-body">
        <div class="step-title" data-fold-toggle>
          ${renderStepCaret()}
          <span>${escapeHtml(running ? t('trace.compressing') : t('trace.compressed'))}</span>
          <span class="tool-status">${statusIcon}</span>
        </div>
        <div class="step-text">${escapeHtml(step.text || '')}</div>
      </div>
    </div>`;
  }

  function renderStep(step, turn, opts = {}) {
    if (step.kind === 'proposal') return renderProposal(step, turn.id);
    if (step.kind === 'approval') return renderApproval(step);
    if (step.kind === 'compress') return renderCompression(step);

    if (step.kind === 'tool') {
      const name = step.name || 'tool';
      const preparing = step.status === 'preparing';
      const running = preparing || step.status === 'running' || step.status === 'background';
      const ok = step.ok !== false;
      const ms = step.duration_ms !== undefined ? duration(step.duration_ms) : '';
      // 成功不打勾（一屏几十行全是绿勾就是噪声）：失败才给红色告警
      const statusIcon = running
        ? '<span class="spinner"></span>'
        : (ok ? '' : '<span class="fail"><svg><use href="#i-alert"/></svg></span>');
      let args = '';
      if (step.args !== undefined && step.args !== null && step.args !== '') {
        try {
          args = typeof step.args === 'string' ? step.args : JSON.stringify(step.args, null, 2);
        } catch { args = String(step.args); }
        if (args === '{}') args = '';
        if (args.length > 3500) args = `${args.slice(0, 3500)}\n…`;
      }
      const summary = step.summary || '';
      // opencode 式文件展示：write/edit 给彩色 diff，read/list 给路径+范围
      const disp = step.display;
      const subject = stepSubject(step);
      const statHtml = stepStatHtml(step);
      let displayHtml = '';
      if (disp && disp.type === 'diff') {
        const key = `${turn.id}:${step.id || step.seq || name}:diff`;
        const pv = disp.preview
          ? pvBlock(disp.preview, PREVIEW_EXT_RE.test(disp.path || '') && (disp.path || '').toLowerCase().endsWith('.svg') ? 'svg' : 'html')
          : '';
        displayHtml = `<details class="tool-args" data-detail="${escAttr(key)}"${detailOpen(key, false) ? ' open' : ''}>
          <summary>${escapeHtml(t('trace.diff'))}</summary>
          <pre class="diff-pre">${(disp.diff || '').split('\n').map(diffLineHtml).join('\n')}</pre>
          ${pv}
        </details>`;
      } else if (disp && disp.type === 'file') {
        const key = `${turn.id}:${step.id || step.seq || name}:file`;
        const path = disp.path || '';
        const pv = PREVIEW_EXT_RE.test(path) && !disp.truncated && disp.text
          ? pvBlock(disp.text, path.toLowerCase().endsWith('.svg') ? 'svg' : 'html')
          : '';
        displayHtml = `<details class="tool-args" data-detail="${escAttr(key)}"${detailOpen(key, false) ? ' open' : ''}>
          <summary>${escapeHtml(t('trace.content'))}</summary>
          <pre class="diff-pre">${escapeHtml(disp.text || '')}</pre>
          ${pv}
        </details>`;
      } else if (disp && disp.type === 'directory') {
        const key = `${turn.id}:${step.id || step.seq || name}:dir`;
        displayHtml = `<details class="tool-args" data-detail="${escAttr(key)}"${detailOpen(key, false) ? ' open' : ''}>
          <summary>${escapeHtml(t('trace.files'))}</summary>
          <pre class="diff-pre">${escapeHtml((disp.entries || []).join('\n'))}</pre>
        </details>`;
      }
      // 徽标里的 op 只保留「像个动作名」的短串：历史上模型会把整条命令塞进
      // op，徽标一旦照抄就会把时间线顶出容器（见 packs.pack_op_label）。
      const packOp = step.op && /^[A-Za-z0-9_-]{1,32}$/.test(String(step.op))
        ? String(step.op) : '';
      const packCall = step.pack
        ? `${String(step.pack).slice(0, 24)}${packOp ? `.${packOp}` : ''}`
        : '';
      return `<div class="step tool">
        <div class="step-icon"><svg><use href="#i-${toolIcon(name)}"/></svg></div>
        <div class="step-body">
          <div class="step-title" data-fold-toggle>
            ${renderStepCaret()}
            ${packCall ? `<span class="pack-badge" title="${escAttr(t('trace.packCall'))}">${escapeHtml(packCall)}</span>` : ''}
            <span class="step-label">${escapeHtml(toolLabel(name))}</span>
            ${preparing || ms
              ? `<span class="meta">${escapeHtml(preparing ? t('tool.preparing') : ms)}</span>`
              : ''}
            ${subject
              ? `<span class="step-subject" title="${escAttr(stepSubjectTitle(step, subject))}">${escapeHtml(subject)}</span>`
              : `<span class="name">${escapeHtml(name)}</span>`}
            ${statHtml}
            ${step.status === 'background' && step.job_id
              ? `<span class="tool-bg" title="${escAttr(t('tool.backgroundHint'))}">${escapeHtml(t('tool.background'))}</span>`
              : ''}
            ${statusIcon ? `<span class="tool-status">${statusIcon}</span>` : ''}
          </div>
          ${summary && !disp ? `<div class="step-text">${escapeHtml(summary)}</div>` : ''}
          ${displayHtml || (args ? (() => {
            const key = `${turn.id}:${step.id || step.seq || name}:args`;
            return `<details class="tool-args" data-detail="${escAttr(key)}"${detailOpen(key, false) ? ' open' : ''}>
            <summary>${escapeHtml(t('trace.arguments'))}</summary><pre class="step-pre">${escapeHtml(args)}</pre>
          </details>`;
          })() : '')}
        </div>
      </div>`;
    }

    if (step.kind === 'thinking') {
      // 折叠时只留一行：首句摘要（展开仍是全文）
      const thinkBrief = oneLine(step.text || '', 160);
      return `<div class="step thinking">
        <div class="step-icon"><svg><use href="#i-brain"/></svg></div>
        <div class="step-body">
          <div class="step-title" data-fold-toggle>${renderStepCaret()}<span class="step-label">${escapeHtml(t('trace.thinking'))}</span>${
            thinkBrief ? `<span class="step-subject" title="${escAttr(thinkBrief)}">${escapeHtml(thinkBrief)}</span>` : ''}</div>
          <div class="step-text">${escapeHtml(step.text || '')}</div>
        </div>
      </div>`;
    }

    if (step.kind === 'text') {
      if (!String(step.text || '').trim()) return '';
      // 正文也按时间线排列：前面的过程说明留在原位，最终答复就是时间线最后一块。
      return `<div class="step text">
        <div class="step-body"><div class="md">${renderMarkdown(step.text, !!step.final && !turn.pending)}</div>${
          opts.streaming ? '<span class="streaming-caret"></span>' : ''}</div>
      </div>`;
    }

    if (step.kind === 'retry') {
      // 自动重试：让用户看到「网络在恢复」而不是「卡死」。
      const a = Number(step.attempt || 0);
      const m = Number(step.max_attempts || 0);
      return `<div class="step retry">
        <div class="step-icon"><svg><use href="#i-refresh"/></svg></div>
        <div class="step-body">
          <div class="step-title">${escapeHtml(t('trace.retry'))}${
            a || m ? `<span class="meta">${escapeHtml(`${a}/${m}`)}</span>` : ''}</div>
          <div class="step-text">${escapeHtml(step.text || '')}</div>
        </div>
      </div>`;
    }

    if (step.kind === 'error') {
      return `<div class="error-box"><svg><use href="#i-alert"/></svg><span>${escapeHtml(step.text || step.error || '')}</span></div>`;
    }

    return '';
  }

  // 时间线逐块输出（opencode 式）：不再把所有「过程」塞进一个折叠块，
  // 思考 / 工具 / 过程正文 / 最终答复都按发生时间各占一块。
  function flowBlockClass(step) {
    return `flow-block ${step.kind || 'text'}${step.final ? ' is-final' : ''}`;
  }

  function stepFoldable(step) {
    return ['thinking', 'tool', 'compress'].includes(step && step.kind);
  }

  function stepFinished(step, pending, isLast) {
    if (!step) return true;
    if (step.kind === 'thinking') return !(pending && isLast);
    if (step.kind === 'tool') {
      return !['preparing', 'running', 'background'].includes(step.status || 'done');
    }
    if (step.kind === 'compress') return step.status !== 'running';
    return true;
  }

  function stepFoldKey(turn, key) {
    return `${(turn && turn.id) || 'live'}:${key}`;
  }

  // 写文件 / 改文件 / 失败的命令默认展开：这几类是「产出物」，折叠会让用户
  // 多点一次；普通读 / 查仍然默认一行。
  function stepAutoOpen(step) {
    if (!step || step.kind !== 'tool') return false;
    if (step.ok === false) return true;
    return step.name === 'write_file' || step.name === 'edit_file';
  }

  function stepCollapsed(step, pending, isLast, foldKey) {
    if (!stepFoldable(step)) return false;
    if (stepClosed.has(foldKey)) return true;
    if (stepOpen.has(foldKey)) return false;
    return stepFinished(step, pending, isLast) && !stepAutoOpen(step);
  }

  function renderStepCaret() {
    return '<span class="step-caret" aria-hidden="true"><svg><use href="#i-chevron-right"/></svg></span>';
  }

  function renderFlowBlock(entry, turn, pending, isLast) {
    const step = entry.step;
    const streaming = !!pending && isLast && step.kind === 'text';
    const foldKey = stepFoldKey(turn, entry.key);
    const foldable = stepFoldable(step);
    const collapsed = stepCollapsed(step, pending, isLast, foldKey);
    const cls = `${flowBlockClass(step)}${foldable ? ' foldable' : ''}${collapsed ? ' is-collapsed' : ''}`;
    return `<div class="${cls}" data-step-key="${escAttr(entry.key)}" data-fold-key="${escAttr(foldKey)}">${
      renderStep(step, turn, { streaming })}</div>`;
  }

  /* ── 一轮结束后的「大折叠」────────────────────────────────────────────
     过程（思考 / 工具 / 重试 / 压缩 / 过程正文）全部折进一条可展开的栏，
     正文单独留在外面。栏展开后有最大高度 + 内部滚动条，长任务不会把页面撑爆。 */

  function lastFinalIndex(entries) {
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      if (entries[i].step.final) return i;
    }
    return -1;
  }

  function splitFlow(turn, pending) {
    const entries = buildFlow(turn);
    if (pending) return { entries, process: [], finalEntry: null, actions: [] };
    const finalIdx = lastFinalIndex(entries);
    const actions = [];
    const process = [];
    entries.forEach((entry, idx) => {
      // 待确认的「转任务」卡片是操作入口，不是过程：留在折叠外面
      if (entry.step.kind === 'proposal') actions.push(entry);
      else if (idx !== finalIdx) process.push(entry);
    });
    return { entries, process, finalEntry: finalIdx >= 0 ? entries[finalIdx] : null, actions };
  }

  function processFoldHtml(turn, entries, open) {
    const count = (kind) => entries.filter((e) => e.step.kind === kind).length;
    const tools = count('tool');
    const thoughts = count('thinking');
    const retries = count('retry');
    const ms = Number(turn.duration_ms || turn.durationMs || 0);
    const meta = [t('process.steps', { n: entries.length })];
    if (tools) meta.push(t('process.tools', { n: tools }));
    if (thoughts) meta.push(t('process.thoughts', { n: thoughts }));
    if (retries) meta.push(t('process.retries', { n: retries }));
    if (ms) meta.push(duration(ms));
    const key = `fold:${(turn && turn.id) || 'live'}`;
    return `<details class="turn-fold" data-detail="${escAttr(key)}"${open ? ' open' : ''}>
      <summary class="turn-fold-bar">
        <svg class="turn-fold-icon"><use href="#i-flow"/></svg>
        <span class="turn-fold-title">${escapeHtml(t('trace.process'))}</span>
        <span class="turn-fold-meta">${escapeHtml(meta.join(' · '))}</span>
        <span class="turn-fold-chevron"><svg><use href="#i-chevron-right"/></svg></span>
      </summary>
      <div class="turn-fold-body">${
        entries.map((entry) => renderFlowBlock(entry, turn, false, false)).join('')
      }</div>
    </details>`;
  }

  /* ── message rendering ───────────────────────────────────────────────── */

  // 任务清单是对话里的「一张牌」：挂在最近一次写过清单的 AI 输出下方，
  // 与 DSH 的 todo 卡片同构——标题 + 进度摘要 + 可折叠的条目列表。
  function turnHasTodo(turn) {
    return !!turn && (turn.trace || []).some((s) =>
      s.kind === 'tool' && TODO_TOOL_NAMES.has(String(s.name || '')));
  }

  const TODO_TOOL_NAMES = new Set(['todo_write', 'todo', 'todo_list', 'task_list']);
  const TODO_STATUS_RANK = { in_progress: 0, pending: 1, completed: 2 };

  function todoCounts(items = state.todos || []) {
    const out = { pending: 0, in_progress: 0, completed: 0, total: items.length };
    for (const it of items) {
      const st = it && TODO_STATUS_RANK[it.status] !== undefined ? it.status : 'pending';
      out[st] += 1;
    }
    return out;
  }

  // 与 DSH 卡片一致：只展示「N 进行中 · M 待处理」；全部完成时给一句话。
  function todoSummaryText(c) {
    if (c.total && c.completed === c.total) return t('todo.allDone');
    return t('todo.summary', { doing: c.in_progress, pending: c.pending });
  }

  function todoMarkHtml(status) {
    const st = TODO_STATUS_RANK[status] !== undefined ? status : 'pending';
    return `<span class="turn-todo-mark is-${st}" aria-hidden="true">${st === 'completed' ? '<svg><use href="#i-check"/></svg>' : ''}</span>`;
  }

  function todoCardHtml() {
    const items = state.todos || [];
    const c = todoCounts(items);
    const open = !!state.todoCardOpen;
    const rows = items.map((it) => {
      const st = TODO_STATUS_RANK[it.status] !== undefined ? it.status : 'pending';
      const text = it.content || it.title || '';
      return `<li class="turn-todo-item is-${st}">
        ${todoMarkHtml(st)}
        <span class="turn-todo-text">${escapeHtml(text)}</span>
        <span class="turn-todo-state">${escapeHtml(t('todo.status.' + st))}</span>
      </li>`;
    }).join('');
    return `<section class="turn-todo${open ? '' : ' is-collapsed'}" data-todo-card>
      <header class="turn-todo-head">
        <span class="turn-todo-icon"><svg><use href="#i-list"/></svg></span>
        <span class="turn-todo-title">${escapeHtml(t('todo.cardTitle'))}</span>
        <span class="turn-todo-meta" data-todo-card-meta>${escapeHtml(todoSummaryText(c))}</span>
        <button type="button" class="icon-btn turn-todo-open" data-open-todo title="${escAttr(t('todo.openPanel'))}" aria-label="${escAttr(t('todo.openPanel'))}"><svg><use href="#i-panel-left"/></svg></button>
        <button type="button" class="turn-todo-chevron" data-todo-card-toggle aria-expanded="${open ? 'true' : 'false'}" title="${escAttr(t(open ? 'dock.collapse' : 'dock.expand'))}"><svg><use href="#i-chevron-down"/></svg></button>
      </header>
      <ul class="turn-todo-list"${open ? '' : ' hidden'}>${rows}</ul>
    </section>`;
  }

  function headChips() {
    // 头像右侧不再挂 chip：任务清单在正文上方以卡片展示（见 todoCardHtml）。
    return '';
  }

  function assistantHead(turn, pending, showTodo) {
    const rawName = turn.role_name || turn.roleName;
    const name = rawName && rawName !== 'Agent Zero' ? rawName : appBrand();
    const model = turn.model || '';
    const ms = turn.duration_ms || turn.durationMs || 0;
    // 流式中的耗时按 startedAt 算：每次重绘都写回静态 0ms，
    // 计时会被刷成 0 又跳回，看着像是闪烁
    const liveMs = pending && turn.startedAt ? Date.now() - turn.startedAt : 0;
    const meta = pending
      ? `<span>${escapeHtml(t('trace.working'))}</span><span class="sep"></span><span class="live-time" data-live-time>${escapeHtml(duration(liveMs))}</span>`
      : [model, ms ? duration(ms) : ''].filter(Boolean).map(escapeHtml).join('<span class="sep"></span>');
    return `<div class="assistant-head">
      <div class="avatar"><svg><use href="#i-logo"/></svg></div>
      ${headChips(turn, showTodo)}
      <span class="assistant-name">${escapeHtml(name)}</span>
      <span class="assistant-meta">${pending ? meta : `<span class="sep"></span>${meta}`}</span>
      <span class="assistant-spacer"></span>
      ${turn.content && turn.id !== 'live' ? `<button class="icon-btn msg-copy" data-copy-message="${escAttr(turn.id)}" title="${escAttr(t('copy.message'))}"><svg><use href="#i-copy"/></svg></button>` : ''}
    </div>`;
  }

  function turnStats(turn) {
    const usage = turn.usage || {};
    const ms = Number(turn.duration_ms || turn.durationMs || 0);
    const llmMs = Number(usage.llm_ms || 0);
    const inTok = Number(usage.prompt_tokens || 0);
    const outTok = Number(usage.completion_tokens || 0);
    // 速度用 LLM 往返耗时（不含工具执行），没有它就用整轮耗时兜底
    const base = llmMs || ms;
    const stats = [];
    if (ms) {
      const other = Math.max(0, ms - llmMs);
      // 模型耗时 / 其它耗时（工具、重试）分开提示，悬停可见；
      // 差值不足 100ms 时没必要提示，那是噪声不是信息。
      stats.push(['clock', t('usage.time', { t: duration(ms) }),
        llmMs && other >= 100 ? t('usage.timeDetail', { l: duration(llmMs), o: duration(other) }) : '']);
    }
    // 顺序固定为 输入 → 输出 → 缓存 → 速度：窄屏只保留前两项（token 数最有用）。
    if (inTok) stats.push(['cpu', t('usage.in', { n: compact(inTok) })]);
    if (outTok) stats.push(['file', t('usage.out', { n: compact(outTok) })]);
    if (inTok) {
      const cached = Number(usage.cached_tokens || 0);
      const rounds = Number(usage.rounds || 0);
      const detail = (cached || rounds > 1)
        ? t('usage.cacheDetail', { c: compact(cached), p: compact(inTok), r: num(rounds) })
        : '';
      stats.push(['cache', t('usage.cache', { p: usage.cache_rate === undefined ? 0 : usage.cache_rate }), detail]);
    }
    if (outTok && base) stats.push(['spark', t('usage.speed', { n: num(Math.round(outTok / (base / 1000))) })]);
    return stats;
  }

  function usageStatsHtml(turn) {
    const stats = turnStats(turn);
    if (!stats.length) return '';
    return stats.map(([icon, text, title]) =>
      `<span class="stat"${title ? ` title="${escAttr(title)}"` : ''}><svg><use href="#i-${icon}"/></svg><span>${escapeHtml(text)}</span></span>`).join('');
  }

  function streamPlaceholderHtml(hidden) {
    return `<div class="stream-placeholder" data-stream-placeholder${hidden ? ' hidden' : ''}><span class="typing-dots"><i></i><i></i><i></i></span><span>${escapeHtml(t('trace.streamPlaceholder'))}</span></div>`;
  }

  function assistantBodyHtml(turn, pending) {
    const { entries, process, finalEntry, actions } = splitFlow(turn, pending);
    let flow = '';
    if (pending) {
      flow = entries.map((entry, i) => renderFlowBlock(
        entry, turn, true, i === entries.length - 1)).join('');
    } else {
      if (process.length) {
        const key = `fold:${(turn && turn.id) || 'live'}`;
        // 没有最终正文时默认展开：否则整轮只剩一条空栏，什么都看不见。
        flow += processFoldHtml(turn, process, detailOpen(key, !finalEntry));
      }
      if (finalEntry) flow += renderFlowBlock(finalEntry, turn, false, true);
      if (actions.length) flow += actions.map(
        (entry) => renderFlowBlock(entry, turn, false, false)).join('');
    }
    const placeholder = pending ? streamPlaceholderHtml(!!flow) : '';
    const error = turn.error || '';
    const interrupted = turn.interrupted && !error
      ? `<div class="interrupted-note" data-interrupted-note><svg><use href="#i-alert"/></svg><span>${escapeHtml(t('trace.interrupted'))}</span></div>`
      : '';
    return `<div class="turn-flow" data-flow>${flow}</div>
      ${placeholder}
      ${error ? `<div class="error-box" data-turn-error><svg><use href="#i-alert"/></svg><span>${escapeHtml(localizeServerMessage(error))}</span></div>` : ''}
      ${interrupted}`;
  }

  function assistantInner(turn, pending, showTodo) {
    return `${assistantHead(turn, pending, showTodo)}
      ${showTodo ? todoCardHtml() : ''}
      <div class="assistant-body">${assistantBodyHtml(turn, pending)}</div>`;
  }

  function renderAssistant(turn, pending, showTodo) {
    // data-live 让流式重绘只命中这一条，不必重建整条转录
    return `<article class="msg assistant${pending ? ' is-live' : ''}"
      data-msg="${escAttr(turn.id || 'live')}"${pending ? ' data-live="1"' : ''}>
      ${assistantInner(turn, pending, showTodo)}
    </article>`;
  }

  function messageRunState(message) {
    const id = message && message.id;
    if (!id) return '';
    if (state.running && state.run && state.run.queued_msg_id === id) return 'running';
    if (state.queueRunning && state.queueRunning.queued_msg_id === id) return 'running';
    if ((state.queue || []).some((r) => r.queued_msg_id === id)) return 'queued';
    return message.queued ? 'queued' : '';
  }

  function renderMessageAttachments(attachments) {
    const list = (attachments || []).filter((a) => a && a.name);
    if (!list.length) return '';
    const items = list.map((a) => {
      const name = escapeHtml(a.name);
      const href = a.url ? ` href="${escAttr(a.url)}" target="_blank" rel="noopener"` : '';
      const tag = a.url ? 'a' : 'span';
      if (a.kind === 'image' && a.url) {
        return `<a class="user-attach-image" href="${escAttr(a.url)}" target="_blank" rel="noopener" title="${name}"><img src="${escAttr(a.url)}" alt="${name}" loading="lazy" /></a>`;
      }
      const icon = a.kind === 'image' ? 'image' : 'file';
      return `<${tag} class="user-attach-file"${href} title="${name}">
        <svg><use href="#i-${icon}"/></svg>
        <span class="user-attach-name">${name}</span>
        ${a.kind === 'image' ? '' : `<span class="user-attach-size">${escapeHtml(bytes(a.size))}</span>`}
      </${tag}>`;
    }).join('');
    return `<div class="user-attachments">${items}</div>`;
  }

  function renderMessageRefs(refs) {
    const list = (refs || []).filter(Boolean);
    if (!list.length) return '';
    const items = list.map((path) => {
      const isDir = String(path).endsWith('/');
      const label = isDir ? String(path).replace(/\/+$/, '') : String(path);
      return `<span class="user-ref" title="${escAttr(path)}">
        <svg><use href="#i-${isDir ? 'folder' : 'file'}"/></svg>
        <span>${escapeHtml(label)}</span>
      </span>`;
    }).join('');
    return `<div class="user-refs">${items}</div>`;
  }

  function renderSystem(message) {
    const text = String(message.content || '').trim();
    if (!text) return '';
    return `<div class="msg-system" data-msg="${escAttr(message.id || '')}"><span>${escapeHtml(text)}</span></div>`;
  }

  function renderUser(message) {
    const when = message.ts ? clock(message.ts) : '';
    if (state.editingMsg && state.editingMsg.id === message.id) {
      return `<article class="msg user is-editing" data-msg="${escAttr(message.id || '')}">
        <div class="user-bubble editing">
          <textarea class="msg-edit-input" data-msg-edit-input rows="2" spellcheck="false">${escapeHtml(state.editingMsg.text || '')}</textarea>
          <div class="msg-edit-actions">
            <span class="msg-edit-hint">${escapeHtml(t('msg.editHint'))}</span>
            <button type="button" class="btn small ghost" data-msg-edit-cancel>${escapeHtml(t('msg.cancel'))}</button>
            <button type="button" class="btn small primary" data-msg-edit-save>${escapeHtml(t('msg.save'))}</button>
          </div>
        </div>
      </article>`;
    }
    const runState = messageRunState(message);
    const actionable = message.id && !String(message.id).startsWith('local-');
    const badges = [];
    if (message.queued || runState === 'queued') {
      badges.push(`<span class="msg-queued-badge">${escapeHtml(t('msg.queued'))}</span>`);
    }
    const actions = actionable && runState !== 'running'
      ? `<div class="msg-actions">
          <button type="button" class="msg-action" data-msg-action="copy" data-msg-id="${escAttr(message.id)}" title="${escAttr(t('msg.copy'))}" aria-label="${escAttr(t('msg.copy'))}"><svg><use href="#i-copy"/></svg></button>
          <button type="button" class="msg-action" data-msg-action="edit" data-msg-id="${escAttr(message.id)}" title="${escAttr(t('msg.edit'))}" aria-label="${escAttr(t('msg.edit'))}"><svg><use href="#i-edit"/></svg></button>
          <button type="button" class="msg-action" data-msg-action="fork" data-msg-id="${escAttr(message.id)}" title="${escAttr(t('menu.fork'))}" aria-label="${escAttr(t('menu.fork'))}"><svg><use href="#i-flow"/></svg></button>
          ${isChatOnlyWorkspace() ? '' : `<button type="button" class="msg-action" data-msg-action="rewind" data-msg-id="${escAttr(message.id)}" title="${escAttr(t('rewind.action'))}" aria-label="${escAttr(t('rewind.action'))}"><svg><use href="#i-clock"/></svg></button>`}
          <button type="button" class="msg-action danger" data-msg-action="recall" data-msg-id="${escAttr(message.id)}" title="${escAttr(t('msg.recall'))}" aria-label="${escAttr(t('msg.recall'))}"><svg><use href="#i-undo"/></svg></button>
        </div>`
      : '';
    return `<article class="msg user" data-msg="${escAttr(message.id || '')}">
      <div class="user-bubble">
        <div class="user-text">${escapeHtml(message.content || '')}</div>
        ${renderMessageRefs(message.refs)}
        ${renderMessageAttachments(message.attachments)}
        ${badges.length ? `<div class="msg-badges">${badges.join('')}</div>` : ''}
        ${when ? `<span class="msg-time">${escapeHtml(when)}</span>` : ''}
      </div>
      ${actions}
    </article>`;
  }

  function emptyStateHtml() {
    const chatOnly = isChatOnlyWorkspace();
    const suggestions = [
      { icon: chatOnly ? 'message' : 'folder', text: t(chatOnly ? 'empty.suggestChat1' : 'empty.suggest1') },
      { icon: chatOnly ? 'brain' : 'code', text: t(chatOnly ? 'empty.suggestChat2' : 'empty.suggest2') },
      { icon: 'globe', text: t('empty.suggest3') },
      { icon: 'list', text: t('empty.suggest4') },
    ];
    return `<section class="empty-state">
      <div class="empty-mark"><svg><use href="#i-logo"/></svg></div>
      <h2>${escapeHtml(t('empty.title'))}</h2>
      <p>${escapeHtml(t(chatOnly ? 'empty.bodyChatOnly' : 'empty.body'))}</p>
      <div class="suggestions">
        ${suggestions.map((s) => `<button class="suggestion" data-suggest="${escAttr(s.text)}">
          <svg><use href="#i-${s.icon}"/></svg><span>${escapeHtml(s.text)}</span></button>`).join('')}
      </div>
    </section>`;
  }

  function loadingHtml() {
    return `<div class="stream-placeholder" style="justify-content:center;min-height:50vh">
      <span class="spinner"></span><span>${escapeHtml(t('loading.conversation'))}</span></div>`;
  }

  let transcriptSig = '';
  let transcriptSkipNext = false;
  // 单独展开 / 收起的步骤（思考、工具、压缩）；默认完成后自动收起
  const stepOpen = new Set();
  const stepClosed = new Set();

  function transcriptSignature() {
    const lastMsg = state.messages.length ? state.messages[state.messages.length - 1] : null;
    return `${state.view}|${state.messages.length}|${(lastMsg && lastMsg.id) || ''}|${state.live ? 'live' : ''}|${state.editingMsg ? `edit:${state.editingMsg.id}` : ''}|${(state.queue || []).length}:${state.queueRunning ? state.queueRunning.id : ''}`;
  }

  // 对话大纲：把用户消息收成右侧的一列上下文入口，点一下跳到对应位置。
  let outlineSig = '';
  let outlineEntries = [];
  let outlineFrame = 0;
  let outlinePinned = '';
  let outlinePinTimer = 0;
  let outlineActiveKey = '';
  // 面板默认收起：鼠标悬浮在右侧大纲区域（收起时的刻度条 / 展开后的面板）就展开，
  // 移开自动收起；点「展开 / 收起」按钮仍然有效（触摸屏、键盘用）。
  // 点过「收起」后先压住悬停展开，等指针离开再恢复，否则刚点完关闭会被同一位置的
  // 悬停立刻重新打开。
  let outlineOpen = false;
  let outlineConvId = null;
  let outlineHoverSuppressed = false;
  let outlineHoverTimer = 0;
  // 最近一次鼠标进出区域时的位置：收起前用它反查指针是否还在面板上
  let outlineHoverPoint = { x: 0, y: 0 };

  function outlineSource() {
    const users = [];
    let domIndex = 0;
    for (const m of state.messages) {
      if (!m || m.by === 'assistant') continue;
      // 排队中的消息不在转录里（只在队列条），大纲也不列：domIndex 与
      // #chatInner .msg.user 的实际顺序必须一致
      if (isQueuedUserMessage(m)) continue;
      const idx = domIndex++;
      const text = String(m.content || '').replace(/\s+/g, ' ').trim();
      if (!text) continue;
      users.push({
        key: m.id || `idx-${idx}`,
        text,
        domIndex: idx,
        title: truncate(text, 180),
      });
    }
    return users;
  }

  function outlineTextHash(text) {
    let h = 0;
    for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) | 0;
    return h;
  }

  function renderOutline(force = false) {
    const wrap = $('#convOutline');
    if (!wrap) return;
    // 切换会话只换内容，不自动收起大纲面板（收起只由用户点关闭按钮触发）。
    if (outlineConvId !== state.currentId) {
      outlineConvId = state.currentId;
      outlineActiveKey = '';
      outlinePinned = '';
    }
    const scroller = $('#chatScroll');
    const width = scroller ? scroller.clientWidth : 0;
    const users = outlineSource();
    const visible = state.view === 'chat' && users.length > 0 && width >= 560;
    wrap.hidden = !visible;
    if (!visible) {
      outlineEntries = [];
      outlineSig = '';
      return;
    }
    wrap.dataset.open = outlineOpen ? '1' : '0';
    const sig = [state.lang,
      users.map((u) => `${u.key}:${u.text.length}:${outlineTextHash(u.text)}`).join('|')].join('~');
    if (!force && sig === outlineSig) {
      updateOutlineActive();
      return;
    }
    outlineSig = sig;
    outlineEntries = users;
    outlineActiveKey = '';
    const count = $('#convOutlineCount');
    if (count) count.textContent = t('outline.count', { n: users.length });
    const list = $('#convOutlineList');
    if (list) {
      list.innerHTML = users.map((u) => `<button type="button" class="conv-outline-item" data-outline-key="${escAttr(u.key)}" title="${escAttr(u.title)}" aria-label="${escAttr(u.title)}"><span class="conv-outline-text">${escapeHtml(u.text)}</span></button>`).join('');
    }
    const ticks = $('#convOutlineTicks');
    if (ticks) {
      ticks.innerHTML = users.map((u) => `<button type="button" class="conv-outline-tick" data-outline-key="${escAttr(u.key)}" title="${escAttr(u.title)}" aria-label="${escAttr(t('outline.jump'))}"></button>`).join('');
    }
    updateOutlineActive();
    if (outlineOpen) revealOutlineActive();
  }

  function updateOutlineActive() {
    const wrap = $('#convOutline');
    if (!wrap || wrap.hidden) return;
    const scroller = $('#chatScroll');
    if (!scroller) return;
    const items = $$('[data-outline-key]', wrap);
    if (!items.length) return;
    const byDomIndex = new Map(outlineEntries.map((u) => [u.domIndex, u]));
    const limit = scroller.getBoundingClientRect().top
      + scroller.clientHeight * 0.55;
    let activeKey = outlineEntries.length ? outlineEntries[0].key : '';
    $$('#chatInner .msg.user').forEach((el, i) => {
      if (el.getBoundingClientRect().top > limit) return;
      const entry = byDomIndex.get(i);
      const key = (entry && entry.key) || el.dataset.msg;
      if (key) activeKey = key;
    });
    if (outlinePinned) activeKey = outlinePinned;
    items.forEach((el) => {
      const active = el.dataset.outlineKey === activeKey;
      el.classList.toggle('is-active', active);
      if (active) el.setAttribute('aria-current', 'true');
      else el.removeAttribute('aria-current');
    });
    if (activeKey !== outlineActiveKey) {
      outlineActiveKey = activeKey;
      revealOutlineActive();
    }
  }

  function revealOutlineActive() {
    const wrap = $('#convOutline');
    const list = $('#convOutlineList');
    if (!wrap || !list || wrap.hidden || wrap.dataset.open !== '1') return;
    const active = list.querySelector('.conv-outline-item.is-active');
    if (!active) return;
    const pad = 8;
    const top = active.offsetTop;
    const bottom = top + active.offsetHeight;
    if (top < list.scrollTop + pad) {
      list.scrollTop = Math.max(0, top - pad);
    } else if (bottom > list.scrollTop + list.clientHeight - pad) {
      list.scrollTop = bottom - list.clientHeight + pad;
    }
  }

  function scheduleOutlineActive() {
    if (outlineFrame) return;
    outlineFrame = requestAnimationFrame(() => {
      outlineFrame = 0;
      updateOutlineActive();
    });
  }

  function jumpToOutline(key) {
    const inner = $('#chatInner');
    if (!inner || !key) return;
    const entry = outlineEntries.find((u) => u.key === key);
    let target = inner.querySelector(`.msg.user[data-msg="${CSS.escape(key)}"]`);
    if (!target && entry) target = $$('.msg.user', inner)[entry.domIndex] || null;
    if (!target) return;
    state.stick = false;
    outlinePinned = key;
    if (outlinePinTimer) clearTimeout(outlinePinTimer);
    outlinePinTimer = setTimeout(() => {
      outlinePinned = '';
      outlinePinTimer = 0;
      updateOutlineActive();
    }, 2400);
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.add('is-outline-target');
    setTimeout(() => target.classList.remove('is-outline-target'), 1700);
    updateOutlineActive();
  }

  function setOutlineOpen(open) {
    outlineOpen = !!open;
    const wrap = $('#convOutline');
    if (wrap) wrap.dataset.open = outlineOpen ? '1' : '0';
    if (outlineOpen) {
      updateOutlineActive();
      revealOutlineActive();
    }
  }

  // 鼠标悬浮打开 / 移开收起。收起留 120ms：指针从收起时的刻度条滑到展开后的
  // 面板上时（两者贴合在同一右侧边缘）不会闪一下又收回去。
  function outlineHoverEnter() {
    if (outlineHoverTimer) {
      clearTimeout(outlineHoverTimer);
      outlineHoverTimer = 0;
    }
    if (outlineHoverSuppressed || outlineOpen) return;
    setOutlineOpen(true);
  }

  function outlineHoverLeave() {
    outlineHoverSuppressed = false;
    if (outlineHoverTimer) {
      clearTimeout(outlineHoverTimer);
      outlineHoverTimer = 0;
    }
    if (!outlineOpen) return;
    outlineHoverTimer = setTimeout(() => {
      outlineHoverTimer = 0;
      const wrap = $('#convOutline');
      // 指针其实还在大纲上（例如收起时的刻度条刚变成展开面板）：用坐标反查
      // 命中元素，不依赖 :hover（外层是 pointer-events:none 的空高容器）。
      const el = document.elementFromPoint(outlineHoverPoint.x, outlineHoverPoint.y);
      if (outlineHoverSuppressed || (wrap && el && wrap.contains(el))) return;
      setOutlineOpen(false);
    }, 140);
  }

  function bindOutlineHover() {
    const wrap = $('#convOutline');
    if (!wrap) return;
    // 整个大纲区域（收起时的刻度条 / 展开后的面板）都算悬停区。外层是
    // height:0 + pointer-events:none，mouseenter 不一定派发得到；改用冒泡的
    // pointerover/out + relatedTarget 判断是否真的进出区域。只认鼠标，
    // 触摸 / 触控笔仍走点击（点「展开」「收起」按钮），不会点一下就被收起。
    wrap.addEventListener('pointerover', (e) => {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      if (e.relatedTarget && wrap.contains(e.relatedTarget)) return;
      outlineHoverPoint = { x: e.clientX, y: e.clientY };
      outlineHoverEnter();
    });
    wrap.addEventListener('pointerout', (e) => {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      if (e.relatedTarget && wrap.contains(e.relatedTarget)) return;
      outlineHoverPoint = { x: e.clientX, y: e.clientY };
      outlineHoverLeave();
    });
  }

  // 离底部这么近就算"跟着最新消息"，不必精确到 0。
  const STICK_EDGE = 90;
  let lastChatTop = 0;
  // 必须持有引用：观察器只挂在元素上时可能被 GC 回收，
  // 之后高度再变化就再也收不到回调（表现为对话停在半路）。
  let chatResizeObserver = null;

  function chatDistanceToBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight;
  }

  // 贴底必须瞬时完成（容器已不再 scroll-behavior: smooth）。
  // 以前直接写 scrollTop 会被 CSS 平滑滚动接管，动画期间内容还在增长，
  // 加上滚动事件又把 state.stick 判成 false，聊天就停在半路不动了。
  // 上一次「程序化贴底」的时刻（ms）。流式输出时每帧都会写 scrollTop，
  // 每次都会触发 scroll 事件——全局 scroll 监听据此区分「用户滚动」与
  // 「自动贴底」，否则流式期间右键菜单一开就被关掉（表现为频闪）。
  let lastAutoScrollAt = 0;

  function pinChatBottom() {
    const scroller = $('#chatScroll');
    if (!scroller) return;
    const top = scroller.scrollHeight - scroller.clientHeight;
    if (top <= 0) return;
    if (Math.abs(scroller.scrollTop - top) < 1) {
      lastChatTop = scroller.scrollTop;
      return;                     // 已经在底部：不写 scrollTop，也就不产生 scroll 事件
    }
    lastAutoScrollAt = Date.now();
    scroller.scrollTop = top;
    lastChatTop = scroller.scrollTop;
  }

  function stickScroll() {
    if (!state.stick) return;
    pinChatBottom();
  }

  // 只有用户真的把视口往上移了才解除跟随。内容变高（流式输出、图片加载）、
  // 程序化贴底、会话切换时清空内容造成的 scrollTop 回弹都不算。
  function onChatScroll() {
    const el = $('#chatScroll');
    if (!el) return;
    const movedUp = el.scrollTop < lastChatTop - 2;
    lastChatTop = el.scrollTop;
    if (state.view === 'chat') {
      if (chatDistanceToBottom(el) <= STICK_EDGE) state.stick = true;
      else if (movedUp) state.stick = false;
    }
    updateScrollQuick();
    scheduleOutlineActive();
  }

  // 渲染之后高度才确定的场景：图片 / 字体 / 代码块 / 折叠展开 / 输入框变高 /
  // 窗口或抽屉改变可视高度。只在 renderMessages 里同步滚一次会漏掉这些，
  // 于是对话就停在离底部还有一截的位置。
  function bindChatScrollWatchers() {
    const scroller = $('#chatScroll');
    const inner = $('#chatInner');
    if (!scroller) return;
    lastChatTop = scroller.scrollTop;
    scroller.addEventListener('scroll', onChatScroll, { passive: true });
    const repin = () => {
      if (state.view !== 'chat') return;
      if (state.stick) pinChatBottom();
      updateScrollQuick();
    };
    if (typeof ResizeObserver === 'function') {
      const ro = new ResizeObserver(repin);
      chatResizeObserver = ro;
      ro.observe(scroller);
      if (inner) ro.observe(inner);
    }
    window.addEventListener('resize', repin);
    // 兜底：后台标签页 / 嵌入式 WebView 里 ResizeObserver 和 rAF 可能被节流
    // 甚至暂停，高度变化收不到回调。轮询只看 scrollHeight 有没有变，
    // 变了才写 scrollTop，不干扰平滑滚动，也不会和用户抢滚动条。
    let lastHeight = -1;
    setInterval(() => {
      if (state.view !== 'chat' || !state.stick) return;
      const el = $('#chatScroll');
      if (!el) return;
      const h = el.scrollHeight;
      if (h === lastHeight) return;
      lastHeight = h;
      if (chatDistanceToBottom(el) > 1) pinChatBottom();
    }, 400);
    // 贴底变得积极之后，用户的上滑意图要在 content 增长前就生效，
    // 否则同一帧内的流式渲染会把人拽回底部。
    let touchY = 0;
    scroller.addEventListener('wheel', (e) => {
      if (e.deltaY < 0) state.stick = false;
    }, { passive: true });
    scroller.addEventListener('touchstart', (e) => {
      const t = e.touches && e.touches[0];
      touchY = t ? t.clientY : 0;
    }, { passive: true });
    scroller.addEventListener('touchmove', (e) => {
      const t = e.touches && e.touches[0];
      if (t && t.clientY > touchY + 4) state.stick = false;
    }, { passive: true });
    scroller.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowUp' || e.key === 'PageUp' || e.key === 'Home') {
        state.stick = false;
      }
    });
  }

  // 右上/右下浮动按钮：只有确实有一段内容在上方/下方时才出现
  function updateScrollQuick() {
    const quick = $('#scrollQuick');
    if (!quick) return;
    if (state.view !== 'chat') { quick.hidden = true; return; }
    const el = $('#chatScroll');
    const showUp = el.scrollTop > 260;
    const showDown = el.scrollHeight - el.scrollTop - el.clientHeight > 260;
    quick.hidden = !showUp && !showDown;
    const up = $('#scrollTopBtn');
    const down = $('#scrollBottomBtn');
    if (up) up.hidden = !showUp;
    if (down) down.hidden = !showDown;
  }

  // 排队中的用户消息不进转录：只在「消息队列」条上等待（上移 / 下移 / 立即插入 /
  // 取消）。等真的轮到它（队列里消失、服务端清掉 queued 标记）才作为用户消息出现，
  // 观感上就是「等到这一条才发」——排队的消息不会被当成已经发出去的消息。
  // 判定同时看两处：服务端 queued 标记（刷新后的历史消息）与队列条里的
  // queued_msg_id（刚发出、还没重拉转录的本地消息）；pendingQueue 是发送途中的
  // 乐观标记（请求还没返回，不知道服务端会给它排在哪）。
  function isQueuedUserMessage(m) {
    if (!m || m.by === 'assistant') return false;
    // 从队列条点「编辑」：临时露出来渲染编辑器，保存 / 取消后重新隐藏
    if (state.editingMsg && m.id && state.editingMsg.id === m.id) return false;
    if (m.pendingQueue || m.queued) return true;
    const id = m.id;
    if (!id) return false;
    return (state.queue || []).some((r) => r && r.queued_msg_id && r.queued_msg_id === id);
  }

  // 排队消息 vs 正在生成的那条：谁先执行？
  // 排队消息现在不渲染（见 isQueuedUserMessage），这里仍保留判定给收尾排序用：
  // 刚完成的那一轮要插在仍在排队的消息之前，数组顺序才和回合顺序一致。
  function liveBeforeMessage(m) {
    if (!m) return false;
    // 仍在队列里的消息 = 还没开始跑，排在 live 之后
    const queued = state.queue || [];
    if (queued.some((r) => r && r.queued_msg_id && r.queued_msg_id === m.id)) return true;
    // 「立即插入」被当前 run 取走的消息：它的内容插进了这一轮回答，展示位置在
    // 回答**之后**。它的排队 run 已被删（不在 state.queue 里），只看队列会漏掉
    // 它，于是又落回「挂在当前回答上面」（用户反馈「立即插入也是一样」）。
    const runId = (state.run && state.run.id) || '';
    return !!(runId && m.injected_into_run && m.injected_into_run === runId);
  }

  // 乐观消息落库后换用服务端 id：发送接口返回的 run 上就带着 queued_msg_id，
  // 它指向刚落库的那条用户消息。liveBeforeMessage / messageRunState 都靠这个 id
  // 把「排队消息」和「正在生成的回答」对上号；一直留着 local-xxx 就对不上，
  // 运行中连发的消息于是又跑到当前回答上面（用户反馈「队列的还有 bug」）。
  function adoptSentMessageId(optimistic, run) {
    const id = String((run && run.queued_msg_id) || '');
    if (!optimistic || !id) return '';
    // 期间重拉过转录：乐观行已被服务端行替换，别再动这条游离对象
    if (!(state.messages || []).includes(optimistic)) return '';
    optimistic.id = id;
    return id;
  }

  // 收尾时把「刚完成的那条」按回合顺序插回去：不能无条件 push —— 它后面可能已有
  // 排队消息 / 被立即插入的消息，那些都属于这一轮**之后**。
  function insertMessageInOrder(turn) {
    const at = state.messages.findIndex((m) => liveBeforeMessage(m));
    if (at < 0) state.messages.push(turn);
    else state.messages.splice(at, 0, turn);
  }

  function renderMessages() {
    const inner = $('#chatInner');
    if (state.view === 'loading') {
      inner.innerHTML = loadingHtml();
      inner.classList.remove('has-messages');
      transcriptSig = '';
      transcriptSkipNext = false;
      renderOutline();
      return;
    }
    if (state.view === 'empty') {
      inner.innerHTML = emptyStateHtml();
      inner.classList.remove('has-messages');
      transcriptSig = '';
      transcriptSkipNext = false;
      renderOutline();
      return;
    }
    inner.classList.toggle('has-messages', state.messages.length > 0 || !!state.live);

    // 流式增量：历史消息没有增减时**只增量更新正在生成的那一条**。
    // 整条转录 innerHTML 重建会把所有已完成消息重新解析 markdown，
    // 每帧一次，长对话明显卡顿、滚动和动画抖动 —— 输出看起来就不流畅。
    const sig = transcriptSignature();
    const liveEl = state.live ? inner.querySelector('[data-live]') : null;
    if (sig === transcriptSig) {
      if (liveEl) {
        updateLiveArticle(liveEl);
        stickScroll();
        return;
      }
      if (transcriptSkipNext) {
        // 刚在 finishRun 里把流式那条就地转正，DOM 已是最终状态，
        // 不能再整条重绘闪一下。
        transcriptSkipNext = false;
        stickScroll();
        updateScrollQuick();
        return;
      }
    }
    transcriptSkipNext = false;
    transcriptSig = sig;

    const todoAnchor = transcriptTodoAnchor();
    const parts = [];
    // 正在生成的那一条要按**回合顺序**插进去，不能无条件放最后：队列模式下用户
    // 可以在回复还在跑时继续发消息，那些排队消息仍在 state.messages 里（但按
    // isQueuedUserMessage 不渲染）。把 live 插到第一条「排在它之后」的消息前面，
    // 数组顺序与回合顺序保持一致（判定见 liveBeforeMessage）。
    const liveHtml = state.live
      ? renderAssistant(state.live, !!state.live.pending,
        turnHasTodo(state.live) || (!todoAnchor && (state.todos || []).length))
      : '';
    let livePlaced = !liveHtml;
    for (const m of state.messages) {
      if (!m) continue;
      // 排队中的消息只出现在队列条上，等轮到它才进转录
      if (isQueuedUserMessage(m)) continue;
      if (!livePlaced && liveBeforeMessage(m)) {
        parts.push(liveHtml);
        livePlaced = true;
      }
      if (m.by === 'assistant') parts.push(renderAssistant(m, false, m.id === todoAnchor));
      else if (m.by === 'system') parts.push(renderSystem(m));
      else parts.push(renderUser(m));
    }
    if (!livePlaced) parts.push(liveHtml);
    inner.innerHTML = parts.join('') || emptyStateHtml();
    if (state.editingMsg && state.editingMsg.focus) {
      const ta = inner.querySelector('[data-msg-edit-input]');
      if (ta) {
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
      }
      state.editingMsg.focus = false;
    }

    stickScroll();
    updateScrollQuick();
    renderOutline();
  }

  // 任务清单是对话级的：挂在最近一次用了 todo 工具的 AI 输出上；
  // 没有工具痕迹时（例如用户手动添加）挂到最后一条 AI 输出
  function transcriptTodoAnchor() {
    let anchor = '';
    for (const m of state.messages) {
      if (m && m.by === 'assistant' && turnHasTodo(m)) anchor = m.id;
    }
    if (!anchor && (state.todos || []).length) {
      for (let i = state.messages.length - 1; i >= 0; i--) {
        const m = state.messages[i];
        if (m && m.by === 'assistant') { anchor = m.id; break; }
      }
    }
    return anchor;
  }

  function syncStreamCaret(el, show) {
    const cur = el.querySelector('.streaming-caret');
    if (!show) {
      if (cur) cur.remove();
      return;
    }
    if (cur) return;
    const body = el.querySelector('.step-body');
    if (body) body.insertAdjacentHTML('beforeend', '<span class="streaming-caret"></span>');
  }

  // 单块增量：流式过程中只改发生变化的那个块（正文只换 md 内容），
  // 不重建整条消息，更不重建整条转录 —— 高频重建正是「频闪」的来源。
  function patchFlowBlock(el, step, turn, streaming, foldKey, collapsed) {
    const kind = step.kind || 'text';
    const foldable = stepFoldable(step);
    el.className = `${flowBlockClass(step)}${foldable ? ' foldable' : ''}${collapsed ? ' is-collapsed' : ''}`;
    if (foldKey) el.dataset.foldKey = foldKey;
    const text = String(step.text || '');
    const preview = !!step.final && !turn.pending;
    if (kind === 'text') {
      let md = el.querySelector('.md');
      if (!md) {
        el.innerHTML = renderStep(step, turn, { streaming });
        md = el.querySelector('.md');
        el._text = text;
        el._preview = preview;
      } else if (el._text !== text || el._preview !== preview) {
        md.innerHTML = renderMarkdown(text, preview);
        el._text = text;
        el._preview = preview;
      }
    } else if (kind === 'thinking') {
      let txt = el.querySelector('.step-text');
      if (!txt) {
        el.innerHTML = renderStep(step, turn);
        txt = el.querySelector('.step-text');
        el._text = text;
      } else if (el._text !== text) {
        txt.textContent = text;
        // 标题行的一行摘要跟着流式输出走，收尾时不会停在半截旧文案上
        const brief = el.querySelector('.step-title .step-subject');
        if (brief) {
          brief.textContent = oneLine(text, 160);
          brief.setAttribute('title', oneLine(text, 160));
        }
        el._text = text;
      }
    } else if (!el._mounted || el._v !== step._v) {
      el.innerHTML = renderStep(step, turn);
    }
    el._v = step._v;
    el._mounted = true;
    syncStreamCaret(el, streaming);
  }

  function syncTurnFlow(body, turn, pending) {
    let flow = body.querySelector('[data-flow]');
    if (!flow) {
      body.insertAdjacentHTML('afterbegin', '<div class="turn-flow" data-flow></div>');
      flow = body.querySelector('[data-flow]');
    }
    const entries = buildFlow(turn);
    const existing = new Map();
    for (const el of Array.from(flow.children)) {
      if (el.dataset && el.dataset.stepKey) existing.set(el.dataset.stepKey, el);
      else el.remove();
    }

    // 收尾时后端会把「最终答复」从 trace 摘到 content：若它对应正在流的
    // 那个正文块，就复用该 DOM（只换 key），避免最后一下删了又插造成闪动。
    const desired = new Set(entries.map((entry) => entry.key));
    const tail = entries[entries.length - 1];
    if (tail && tail.step.kind === 'text' && tail.step.final && !existing.has(tail.key)) {
      const target = String(tail.step.text || '').trim();
      for (const [key, el] of existing) {
        if (desired.has(key)) continue;
        if (!el.classList.contains('text')) continue;
        if (String(el._text || '').trim() !== target) continue;
        existing.delete(key);
        el.dataset.stepKey = tail.key;
        existing.set(tail.key, el);
        break;
      }
    }

    let cursor = flow.firstElementChild;
    entries.forEach((entry, index) => {
      const isLast = index === entries.length - 1;
      const foldKey = stepFoldKey(turn, entry.key);
      let el = existing.get(entry.key);
      if (el) {
        existing.delete(entry.key);
        if (el !== cursor) flow.insertBefore(el, cursor);
        else cursor = cursor.nextElementSibling;
      } else {
        el = document.createElement('div');
        el.dataset.stepKey = entry.key;
        el.dataset.foldKey = foldKey;
        flow.insertBefore(el, cursor);
      }
      patchFlowBlock(
        el, entry.step, turn,
        !!pending && isLast && entry.step.kind === 'text',
        foldKey, stepCollapsed(entry.step, !!pending, isLast, foldKey));
    });
    for (const el of existing.values()) el.remove();
    return entries.length;
  }

  function syncLiveExtras(body, turn, pending, count) {
    let placeholder = body.querySelector('[data-stream-placeholder]');
    if (pending) {
      if (!placeholder) {
        const flow = body.querySelector('[data-flow]');
        if (flow) flow.insertAdjacentHTML('afterend', streamPlaceholderHtml(true));
        placeholder = body.querySelector('[data-stream-placeholder]');
      }
      if (placeholder) placeholder.hidden = count > 0;
    } else if (placeholder) {
      placeholder.remove();
    }

    let errorEl = body.querySelector('[data-turn-error]');
    const error = turn.error ? localizeServerMessage(turn.error) : '';
    if (error) {
      if (!errorEl) {
        body.insertAdjacentHTML('beforeend',
          `<div class="error-box" data-turn-error><svg><use href="#i-alert"/></svg><span>${escapeHtml(error)}</span></div>`);
      } else if (errorEl.textContent !== error) {
        errorEl.innerHTML = `<svg><use href="#i-alert"/></svg><span>${escapeHtml(error)}</span>`;
      }
    } else if (errorEl) {
      errorEl.remove();
    }

    let interruptedEl = body.querySelector('[data-interrupted-note]');
    if (turn.interrupted && !turn.error) {
      if (!interruptedEl) {
        body.insertAdjacentHTML('beforeend',
          `<div class="interrupted-note" data-interrupted-note><svg><use href="#i-alert"/></svg><span>${escapeHtml(t('trace.interrupted'))}</span></div>`);
      }
    } else if (interruptedEl) {
      interruptedEl.remove();
    }
  }

  function syncLiveHead(liveEl, turn, pending, showTodo) {
    const chips = headChips(turn, showTodo);
    const sig = `${turn.role_name || ''}|${turn.model || ''}|${pending ? 'live' : String(turn.duration_ms || 0)}|${turn.id || ''}|${chips}`;
    let head = liveEl.querySelector('.assistant-head');
    if (head && head._sig === sig) return;
    const html = assistantHead(turn, pending, showTodo);
    if (head) head.outerHTML = html;
    else liveEl.insertAdjacentHTML('afterbegin', html);
    head = liveEl.querySelector('.assistant-head');
    if (head) head._sig = sig;
  }

  // 任务卡片在流式过程中出现 / 消失：补齐或摘掉卡片，已有卡片只就地更新条目。
  function syncLiveTodoCard(liveEl, turn, pending, showTodo) {
    const card = liveEl.querySelector('[data-todo-card]');
    if (!showTodo) {
      if (card) card.remove();
      return;
    }
    if (!card) {
      const head = liveEl.querySelector('.assistant-head');
      if (head) head.insertAdjacentHTML('afterend', todoCardHtml());
      else liveEl.insertAdjacentHTML('afterbegin', todoCardHtml());
      return;
    }
    updateTodoBadge();
  }

  // 流式重绘入口：头部按需更新、正文/过程按块增量更新，DOM 不整体替换。
  function updateLiveArticle(liveEl) {
    const turn = state.live;
    if (!turn) return;
    const pending = !!turn.pending;
    const showTodo = turnHasTodo(turn) ||
      ((state.todos || []).length && !state.messages.some((m) => m && m.by === 'assistant' && turnHasTodo(m)));
    syncLiveHead(liveEl, turn, pending, showTodo);
    syncLiveTodoCard(liveEl, turn, pending, showTodo);
    let body = liveEl.querySelector('.assistant-body');
    if (!body) {
      liveEl.innerHTML = assistantInner(turn, pending, showTodo);
      return;
    }
    const count = syncTurnFlow(body, turn, pending);
    syncLiveExtras(body, turn, pending, count);
  }

  function chatTitle() {
    if (!state.currentId) return t('chat.new');
    const firstUser = state.messages.find((m) => m.by !== 'assistant' && String(m.content || '').trim());
    return (state.conv && state.conv.title)
      || (firstUser && truncate(firstUser.content, 52))
      || t('chat.new');
  }

  function applyConversationTitle(title, source) {
    const ttl = String(title || '').trim();
    if (!ttl) return;
    if (state.conv) {
      state.conv.title = ttl;
      state.conv.title_source = source || state.conv.title_source || '';
    }
    for (const list of [state.conversations, state.chatItems]) {
      if (!Array.isArray(list)) continue;
      const row = list.find((c) => c && c.id === state.currentId);
      if (row) {
        row.title = ttl;
        row.title_source = source || row.title_source || '';
      }
    }
    renderHeader();
    renderConversations();
    renderChatList();
  }

  function openRenameModal() {
    if (!state.currentId) return;
    const modal = $('#renameModal');
    const input = $('#renameInput');
    if (!modal || !input) return;
    input.value = (state.conv && state.conv.title) || chatTitle();
    modal.hidden = false;
    const note = $('#renameNote');
    if (note) { note.hidden = true; note.textContent = ''; }
    input.focus();
    input.select();
  }

  async function saveConversationTitle() {
    if (!state.currentId) return;
    const input = $('#renameInput');
    const saveBtn = $('#renameSave');
    const title = String((input && input.value) || '').trim();
    if (!title) {
      toast(t('chat.titleEmpty'), 'bad', 2500);
      return;
    }
    if (saveBtn) saveBtn.disabled = true;
    try {
      const data = await api(`/conversations/${encodeURIComponent(state.currentId)}`, {
        method: 'PATCH',
        body: JSON.stringify({ title }),
      });
      applyConversationTitle((data && data.title) || title, 'user');
      $('#renameModal').hidden = true;
      toast(t('chat.titleSaved'), 'ok', 2000);
    } catch (err) {
      toast(err.message, 'bad', 4000);
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  async function regenerateConversationTitle() {
    if (!state.currentId) return;
    const btn = $('#renameRegenerate');
    const note = $('#renameNote');
    if (btn) btn.disabled = true;
    if (note) {
      note.hidden = false;
      note.className = 'save-note';
      note.textContent = t('chat.titleGenerating');
    }
    try {
      const data = await api(
        `/conversations/${encodeURIComponent(state.currentId)}/title/regenerate`,
        { method: 'POST' });
      if (data && data.generated && data.title) {
        applyConversationTitle(data.title, 'auto');
        const input = $('#renameInput');
        if (input) input.value = data.title;
        if (note) {
          note.className = 'save-note ok';
          note.textContent = t('chat.titleGenerated');
        }
      } else if (note) {
        note.className = 'save-note bad';
        note.textContent = t('chat.titleFailed');
      }
    } catch (err) {
      if (note) {
        note.className = 'save-note bad';
        note.textContent = err.message || t('chat.titleFailed');
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ── workspaces ──────────────────────────────────────────────────────── */

  function currentWorkspace() {
    if (state.workspace) return state.workspace;
    if (state.app && state.app.workspace) return state.app.workspace;
    return null;
  }

  function workspaceId() {
    return (currentWorkspace() && currentWorkspace().id) || 'default';
  }

  function isChatOnlyWorkspace(ws) {
    const cur = ws || currentWorkspace();
    return !!(cur && (cur.chat_only || cur.managed));
  }

  // 记忆入口（顶栏 🧠 + 侧栏 rail）：纯聊天工作区没有任何可挂接的代码实体，
  // 记忆视图（列表 / 图谱 / 统计）在那里只会是一张空表，所以入口一并收起。
  function syncMemoryEntry(chatOnly) {
    const hide = chatOnly === undefined ? isChatOnlyWorkspace() : !!chatOnly;
    document.querySelectorAll('[data-sidebar-action="memory"]').forEach((el) => {
      el.hidden = hide;
    });
    // 从纯聊天工作区回来时，服务端保存的 openTabs 里可能还留着 mem：
    // 把它从 dock 标签里摘掉，避免恢复出一个永远空着的记忆面板。
    if (hide && dockOpenTabs().includes('mem')) closeDockTab('mem');
  }

  function renderWorkspaceNav() {
    const chatOnly = isChatOnlyWorkspace();
    syncMemoryEntry(chatOnly);
    if (chatOnly) {
      const title = $('#drawerWorkspaceName');
      const path = $('#workspacePath');
      const footer = $('#wsFooter');
      if (title) title.textContent = currentWorkspace() ? currentWorkspace().name : t('workspace.title');
      if (path) path.textContent = t('workspace.chatOnly');
      if (footer) footer.textContent = t('workspace.chatOnlyNote');
      const list = $('#wsList');
      if (list) list.innerHTML = '';
      // 这里不再顺手关面板 / 摘标签页：换工作区时 resetWorkspaceDock() 已经
      // 按「面板绑定当前工作区」处理过一遍，面板的收起只由用户点收缩按钮触发。
    }
  }

  function applyWorkspace(ws) {
    if (!ws || !ws.id) return;
    const prevId = workspaceId();
    state.workspace = ws;
    if (state.app) state.app.workspace = ws;
    localStorage.setItem('az-workspace', ws.id);
    // 真换了工作区：右侧面板绑定的是「当前工作区」，
    // 不清掉就会继续显示上一个工作区的文件 / VCS / 记忆。
    // （从会话列表点进别的工作区的对话走的就是这条路，
    // 不经过 switchWorkspace。）
    // 清掉数据后立刻按新工作区把同一个面板拉回来 —— 换工作区只换内容，
    // 不把面板收起来（收起只由用户点收缩按钮触发）。
    if (prevId && prevId !== ws.id) {
      const prevActive = state.ui.active;
      resetWorkspaceDock();
      if (bootFinished && !wsSwitching && prevActive) {
        setTimeout(() => { restoreWorkspaceDock(prevActive).catch(() => {}); }, 0);
      }
    }
    renderWorkspaceSwitcher();
    renderHeader();
    scheduleRender();
    if (state.settings && $('#settingsModal') && !$('#settingsModal').hidden) {
      renderSettingsWorkspaces();
    }
  }

  async function loadWorkspaces() {
    state.workspaceLoading = true;
    try {
      const data = await api('/workspaces?include_archived=true');
      state.workspacesAll = (data && data.items) || [];
      state.workspaces = state.workspacesAll.filter((w) => !w.archived);
      const current = state.workspace && state.workspace.id;
      const fresh = current && state.workspacesAll.find((w) => w.id === current);
      if (fresh) {
        state.workspace = fresh;
        if (state.app) state.app.workspace = fresh;
        renderHeader();
      }
      renderWorkspaceSwitcher();
      loadWorkspaceMenuPage(state.wsMenuPage);
      if (state.ws.open && $('#drawerWorkspaceName')) {
        $('#drawerWorkspaceName').textContent = currentWorkspace()
          ? currentWorkspace().name : t('workspace.title');
      }
      return state.workspacesAll;
    } catch (err) {
      console.warn('workspace list failed', err);
      return state.workspacesAll;
    } finally {
      state.workspaceLoading = false;
    }
  }

  function renderWorkspaceSwitcher() {
    renderWorkspaceGroups();
  }

  const WS_MENU_PAGE = 10;
  const CONV_PREVIEW = 6;
  const CHAT_PAGE = 3;

  function chatWorkspace() {
    return state.workspaces.find((w) => w.is_default) || null;
  }

  function renderChatList() {
    const list = $('#chatList');
    if (!list) return;
    const filter = state.filter.trim().toLowerCase();
    const items = filter
      ? state.chatItems.filter((c) => (c.title || '').toLowerCase().includes(filter))
      : state.chatItems;
    if (!items.length) {
      list.innerHTML = `<div class="conv-mini-empty">${escapeHtml(filter ? t('chat.noMatch') : t('chat.empty'))}</div>`;
    } else {
      list.innerHTML = convSectionsHtml(items, convMiniRow, { doneLabel: false, counts: false });
    }
    fillChatListIfNeeded();
  }

  // 列表不足一屏时没有滚动条可拖，直接续页补齐，保证「滚轮加载」始终可用。
  async function fillChatListIfNeeded() {
    if (state.chatFillBusy || state.chatLoadingMore) return;
    if (state.filter.trim()) return;          // 搜索时不做全量补齐，避免连续翻页
    state.chatFillBusy = true;
    try {
      for (let i = 0; i < 20; i += 1) {
        const box = $('#chatBox');
        if (!box || state.chatItems.length >= state.chatTotal) break;
        if (box.scrollHeight > box.clientHeight + 4) break;
        const before = state.chatItems.length;
        await loadChatMore();
        if (state.chatItems.length === before) break;
      }
    } finally {
      state.chatFillBusy = false;
    }
  }

  async function loadChatTop() {
    const ws = chatWorkspace();
    if (!ws) {
      state.chatItems = [];
      state.chatTotal = 0;
      renderChatList();
      return;
    }
    const limit = Math.max(CHAT_PAGE, Math.min(state.chatItems.length, 30));
    try {
      const data = await api(`/conversations?workspace_id=${encodeURIComponent(ws.id)}&limit=${limit}&offset=0`);
      state.chatItems = (data && data.items) || [];
      state.chatTotal = Number(data && data.total) || 0;
    } catch (err) {
      console.warn('chat list failed', err);
    }
    renderChatList();
  }

  async function loadChatMore() {
    const ws = chatWorkspace();
    if (!ws || state.chatItems.length >= state.chatTotal || state.chatLoadingMore) return;
    state.chatLoadingMore = true;
    try {
      const data = await api(`/conversations?workspace_id=${encodeURIComponent(ws.id)}&limit=${CHAT_PAGE}&offset=${state.chatItems.length}`);
      const items = (data && data.items) || [];
      const seen = new Set(state.chatItems.map((c) => c.id));
      items.forEach((c) => { if (!seen.has(c.id)) state.chatItems.push(c); });
      state.chatTotal = Number(data && data.total) || state.chatTotal;
      renderChatList();
    } catch (err) {
      console.warn('chat page failed', err);
    }
    state.chatLoadingMore = false;
  }

  // 会话是否「进行中」：后端按 run / goal 算好的 active，前端再用本地 activeRuns
  // 兜一层（刚发出的消息在下次刷新列表前就已经在跑了）。
  function convIsActive(conv) {
    if (!conv) return false;
    if (conv.active) return true;
    return (state.activeRuns || []).some((r) => r.conversation_id === conv.id);
  }

  // 排序 = 进行中在前，两组各自按时间倒序（与后端 list_conversations 一致）。
  function convTimestamp(conv) {
    const raw = String(conv.updated_at || conv.created_at || '').replace(' ', 'T');
    const ms = new Date(raw).getTime();
    return Number.isNaN(ms) ? 0 : ms;
  }

  function sortConversations(items) {
    return items.slice().sort((a, b) => {
      const live = Number(convIsActive(b)) - Number(convIsActive(a));
      if (live) return live;
      return convTimestamp(b) - convTimestamp(a);
    });
  }

  function splitConversations(items) {
    const active = [];
    const done = [];
    sortConversations(items).forEach((c) => (convIsActive(c) ? active : done).push(c));
    return { active, done };
  }

  // 两段式列表：进行中 → 完结。空组不渲染（不出现孤立标题）。
  // opts.doneLabel=false 时不渲染「完结」标题行；opts.counts=false 时不渲染组内计数
  // （工作区预览用，减少左栏 DOM）。
  function convSectionsHtml(items, rowHtml = convMiniRow, opts = {}) {
    const { active, done } = splitConversations(items);
    const group = (key, labelKey, list) => {
      if (!list.length) return '';
      const count = opts.counts === false ? '' : `<span class="conv-group-count">${list.length}</span>`;
      const label = key === 'done' && opts.doneLabel === false
        ? ''
        : `<div class="conv-group-label" data-i18n="${labelKey}">${escapeHtml(t(labelKey))}${count}</div>`;
      return `<div class="conv-group" data-conv-group="${key}">
        ${label}
        ${list.map(rowHtml).join('')}
      </div>`;
    };
    return group('active', 'conv.section.active', active)
      + group('done', 'conv.section.done', done);
  }

  // 会话头像：用标题首字 + 稳定色相，IM 列表里一眼区分（不需要上传头像）。
  function convAvatarHtml(conv) {
    const name = String(conversationTitle(conv) || '').trim();
    const ch = (name[0] || '·').toUpperCase();
    let h = 0;
    for (let i = 0; i < name.length; i += 1) h = ((h * 31) + name.charCodeAt(i)) >>> 0;
    return `<span class="conv-mini-avatar" style="--av-h:${h % 360}" aria-hidden="true">${escapeHtml(ch)}</span>`;
  }

  function convMiniRow(conv) {
    const running = convIsActive(conv);
    const time = relativeTime(conv.updated_at || conv.created_at);
    const preview = String(conv.last_message || '').replace(/\s+/g, ' ').trim();
    const unread = !!conv.unread;
    return `<div class="conv-mini${unread ? ' is-unread' : ''}" data-conv-row="${escAttr(conv.id)}">
      <button type="button" class="conv-mini-main" data-conv="${escAttr(conv.id)}" title="${escAttr(conversationTitle(conv))}">
        ${convAvatarHtml(conv)}
        <span class="conv-mini-body">
          <span class="conv-mini-line">
            <span class="conv-mini-title">${running ? '<span class="conv-running-dot"></span>' : ''}${escapeHtml(truncate(conversationTitle(conv), 46))}</span>
            <span class="conv-mini-time">${escapeHtml(time)}</span>
            ${unread ? `<span class="conv-mini-unread" title="${escAttr(t('chat.unread'))}" aria-label="${escAttr(t('chat.unread'))}"></span>` : ''}
          </span>
          <span class="conv-mini-preview">${escapeHtml(truncate(preview || t('chat.emptyPreview'), 64))}</span>
        </span>
      </button>
      <button type="button" class="conv-mini-del" data-conv-del="${escAttr(conv.id)}" title="${escAttr(t('chat.delete'))}" aria-label="${escAttr(t('chat.delete'))}">
        <svg><use href="#i-trash"/></svg>
      </button>
    </div>`;
  }

  // 打开会话 / 本轮收尾：把已读水位推到当前时间，并就地清掉列表红点。
  async function markConversationRead(convId, { render = true } = {}) {
    if (!convId) return;
    for (const list of [state.conversations, state.chatItems]) {
      const row = Array.isArray(list) ? list.find((c) => c && c.id === convId) : null;
      if (row) row.unread = false;
    }
    if (state.conv && state.conv.id === convId) state.conv.unread = false;
    if (render) renderConversations();
    try {
      await api(`/conversations/${encodeURIComponent(convId)}/read`, { method: 'POST' });
    } catch (err) {
      console.warn('mark read failed', err);
    }
  }

  /* ── 会话信息抽屉（IM 的「群资料」）──────────────────────────────────
     入口：顶栏 #convInfoBtn。展示会话 / 工作区 / 模型，以及绑定的团队：
     群名、协作模式、圆桌上限、成员增删、绑定 / 解绑。团队改动写 REST，
     与模型侧 team_manage 改的是同一份数据。 */
  const CONV_DRAWER_MODES = ['orchestrate', 'roundtable', 'single'];
  let convDrawerData = { team: null, teams: [] };

  function isConvDrawerOpen() {
    const el = $('#convDrawer');
    return !!el && !el.hidden;
  }

  function openConvDrawer() {
    if (!state.currentId) {
      toast(t('conv.noConversation'), 'bad', 2200);
      return;
    }
    const drawer = $('#convDrawer');
    const backdrop = $('#convDrawerBackdrop');
    if (!drawer || !backdrop) return;
    drawer.hidden = false;
    backdrop.hidden = false;
    const body = $('#convDrawerBody');
    if (body) body.innerHTML = `<div class="conv-empty">${escapeHtml(t('conv.loading'))}</div>`;
    refreshConvDrawer();
  }

  function closeConvDrawer() {
    const drawer = $('#convDrawer');
    const backdrop = $('#convDrawerBackdrop');
    if (drawer) drawer.hidden = true;
    if (backdrop) backdrop.hidden = true;
  }

  function convMemberBrief(m) {
    const brief = { id: m.id, name: m.name || '', expertise: m.expertise || '' };
    if (Array.isArray(m.tools) && m.tools.length) brief.tools = m.tools;
    if (m.model_code) brief.model_code = m.model_code;
    return brief;
  }

  function convMemberHtml(m) {
    const tools = Array.isArray(m.tools) ? m.tools : [];
    let h = 0;
    const name = String(m.name || m.id || '');
    for (let i = 0; i < name.length; i += 1) h = ((h * 31) + name.charCodeAt(i)) >>> 0;
    const sub = [m.expertise, tools.length ? t('conv.memberTools', { n: tools.length }) : '',
      m.model_code || ''].filter(Boolean).join(' · ');
    return `<div class="conv-member">
      <span class="conv-member-avatar" style="--av-h:${h % 360}" aria-hidden="true">${escapeHtml((name[0] || '·').toUpperCase())}</span>
      <span class="conv-member-main">
        <span class="conv-member-name">${escapeHtml(name)}</span>
        ${sub ? `<span class="conv-member-sub">${escapeHtml(sub)}</span>` : ''}
      </span>
      <button class="icon-btn conv-member-del" type="button" data-team-member-remove="${escAttr(m.id)}" title="${escAttr(t('conv.removeMember'))}" aria-label="${escAttr(t('conv.removeMember'))}"><svg><use href="#i-x"/></svg></button>
    </div>`;
  }

  function convToolsHtml() {
    const ws = currentWorkspace();
    const chatOnly = isChatOnlyWorkspace(ws);
    const tools = [];
    if (!chatOnly) tools.push({ key: 'workspace', label: t('nav.workspace'), icon: 'folder' });
    if (ws && ws.vcs) tools.push({ key: 'vcs', label: t('vcs.title'), icon: 'vcs' });
    if (!chatOnly) tools.push({ key: 'codegraph', label: t('codegraph.title'), icon: 'code' });
    if (!chatOnly) tools.push({ key: 'mem', label: t('mem.title'), icon: 'brain' });
    if (!tools.length) return '';
    return `<section class="conv-section">
      <div class="conv-section-head"><span>${escapeHtml(t('conv.tools'))}</span></div>
      <div class="conv-tools">${tools.map((x) =>
        `<button class="btn small ghost conv-tool" type="button" data-conv-tool="${x.key}"><svg><use href="#i-${x.icon}"/></svg><span>${escapeHtml(x.label)}</span></button>`).join('')}</div>
    </section>`;
  }

  function renderConvDrawer() {
    const body = $('#convDrawerBody');
    if (!body) return;
    const conv = state.conv || {};
    const ws = currentWorkspace();
    const workspaceLabel = (ws && ws.name) || conv.workspace_id || '';
    const model = conv.model_code || (state.activeModel && state.activeModel !== 'default'
      ? state.activeModel : ((state.app && state.app.llm && state.app.llm.model) || ''));
    const { team, teams } = convDrawerData;
    const modeOptions = CONV_DRAWER_MODES.map((mode) =>
      `<option value="${mode}"${team && team.mode === mode ? ' selected' : ''}>${escapeHtml(t(`conv.mode.${mode}`))}</option>`).join('');
    const members = (team && team.members) || [];
    const teamSection = team
      ? `<section class="conv-section">
          <div class="conv-section-head"><span>${escapeHtml(t('conv.group'))}</span></div>
          <label class="conv-field"><span>${escapeHtml(t('conv.teamName'))}</span>
            <input class="input" id="convTeamName" value="${escAttr(team.name || '')}" maxlength="120" /></label>
          <div class="conv-row">
            <label class="conv-field" style="flex:1"><span>${escapeHtml(t('conv.mode'))}</span>
              <select class="input" id="convTeamMode">${modeOptions}</select></label>
            <label class="conv-field" style="width:120px"><span>${escapeHtml(t('conv.maxSpeakers'))}</span>
              <input class="input mono" id="convTeamSpeakers" type="number" min="1" max="12" value="${Number(team.max_speakers) || 4}" /></label>
          </div>
          <div class="conv-row">
            <span class="conv-meta">${escapeHtml(t('conv.groupHint'))}</span>
            <span style="flex:1"></span>
            <button class="btn small primary" type="button" id="convTeamSave">${escapeHtml(t('conv.save'))}</button>
          </div>
          <div class="conv-section-head"><span>${escapeHtml(t('conv.members'))}</span><span class="conv-meta">${members.length}</span></div>
          ${members.length ? members.map(convMemberHtml).join('') : `<div class="conv-empty">${escapeHtml(t('conv.noMembers'))}</div>`}
          <div class="conv-row">
            <input class="input" id="convMemberName" maxlength="120" placeholder="${escAttr(t('conv.memberName'))}" />
            <input class="input" id="convMemberExpertise" maxlength="200" placeholder="${escAttr(t('conv.memberExpertise'))}" />
            <button class="btn small" type="button" id="convMemberAdd">${escapeHtml(t('conv.addMember'))}</button>
          </div>
          <div class="conv-row">
            <button class="btn small ghost" type="button" id="convTeamUnbind">${escapeHtml(t('conv.unbind'))}</button>
          </div>
        </section>`
      : `<section class="conv-section">
          <div class="conv-section-head"><span>${escapeHtml(t('conv.group'))}</span></div>
          <div class="conv-empty">${escapeHtml(t('conv.single'))}</div>
          ${teams.length ? `<label class="conv-field"><span>${escapeHtml(t('conv.bindExisting'))}</span>
            <select class="input" id="convBindTeam">${teams.map((x) =>
              `<option value="${escAttr(x.id)}">${escapeHtml(x.name || x.id)}</option>`).join('')}</select></label>
          <button class="btn small" type="button" id="convBindTeamBtn">${escapeHtml(t('conv.bind'))}</button>` : ''}
          <label class="conv-field"><span>${escapeHtml(t('conv.createGroup'))}</span>
            <input class="input" id="convNewTeamName" maxlength="120" placeholder="${escAttr(t('conv.teamName'))}" /></label>
          <button class="btn small primary" type="button" id="convNewTeamBtn">${escapeHtml(t('conv.createGroup'))}</button>
        </section>`;
    body.innerHTML = `
      <section class="conv-section">
        <div class="conv-section-head"><span>${escapeHtml(t('conv.conversation'))}</span></div>
        <div class="conv-row">
          <span class="conv-conv-title">${escapeHtml(chatTitle())}</span>
          <span style="flex:1"></span>
          <button class="btn small ghost" type="button" id="convRenameBtn">${escapeHtml(t('conv.rename'))}</button>
        </div>
        <div class="conv-meta">${escapeHtml(t('conv.workspace'))}：${escapeHtml(workspaceLabel)}</div>
        ${model ? `<div class="conv-meta">${escapeHtml(t('conv.model'))}：${escapeHtml(model)}</div>` : ''}
      </section>
      ${teamSection}
      ${convToolsHtml()}`;
  }

  async function refreshConvDrawer() {
    const convId = state.currentId;
    if (!convId) return;
    const ws = workspaceId();
    try {
      const [teamData, teamsData] = await Promise.all([
        api(`/conversations/${encodeURIComponent(convId)}/team`).catch(() => ({ team: null })),
        api(`/teams?workspace_id=${encodeURIComponent(ws)}`).catch(() => ({ items: [] })),
      ]);
      if (convId !== state.currentId || !isConvDrawerOpen()) return;
      convDrawerData = {
        team: (teamData && teamData.team) || null,
        teams: (teamsData && teamsData.items) || [],
      };
      if (state.conv) state.conv.team_id = (teamData && teamData.team_id) || '';
      setMentionMembers(
        (convDrawerData.team && convDrawerData.team.members) || []);
      renderConvDrawer();
    } catch (err) {
      const body = $('#convDrawerBody');
      if (body) body.innerHTML = `<div class="conv-empty">${escapeHtml(err.message)}</div>`;
    }
  }

  async function patchConvTeam(payload) {
    const team = convDrawerData.team;
    if (!team) return;
    try {
      const updated = await api(`/teams/${encodeURIComponent(team.id)}`, {
        method: 'PATCH', body: JSON.stringify(payload),
      });
      convDrawerData.team = updated;
      renderConvDrawer();
      toast(t('conv.saved'), 'ok', 1500);
    } catch (err) {
      toast(err.message, 'bad', 5000);
    }
  }

  async function bindConvTeam(teamId) {
    if (!state.currentId || !teamId) return;
    try {
      await api(`/teams/${encodeURIComponent(teamId)}/bind`, {
        method: 'POST', body: JSON.stringify({ conversation_id: state.currentId }),
      });
      if (state.conv) state.conv.team_id = teamId;
      await refreshConvDrawer();
      refreshConversations();
    } catch (err) {
      toast(err.message, 'bad', 5000);
    }
  }

  async function unbindConvTeam() {
    const team = convDrawerData.team;
    if (!state.currentId || !team) return;
    try {
      await api(`/teams/${encodeURIComponent(team.id)}/unbind`, {
        method: 'POST', body: JSON.stringify({ conversation_id: state.currentId }),
      });
      if (state.conv) state.conv.team_id = '';
      await refreshConvDrawer();
      refreshConversations();
    } catch (err) {
      toast(err.message, 'bad', 5000);
    }
  }

  function wireConvDrawer() {
    const drawer = $('#convDrawer');
    const close = $('#convDrawerClose');
    const backdrop = $('#convDrawerBackdrop');
    if (close) close.addEventListener('click', closeConvDrawer);
    if (backdrop) backdrop.addEventListener('click', closeConvDrawer);
    $('#convInfoBtn').addEventListener('click', () => {
      if (isConvDrawerOpen()) closeConvDrawer();
      else openConvDrawer();
    });
    if (!drawer) return;
    drawer.addEventListener('click', async (e) => {
      const remove = e.target.closest('[data-team-member-remove]');
      if (remove) {
        const team = convDrawerData.team;
        if (!team) return;
        const members = (team.members || []).filter((m) => m.id !== remove.dataset.teamMemberRemove)
          .map(convMemberBrief);
        await patchConvTeam({ members });
        return;
      }
      const tool = e.target.closest('[data-conv-tool]');
      if (tool) {
        const key = tool.dataset.convTool;
        closeConvDrawer();
        if (key === 'workspace') openDrawer();
        else if (key === 'vcs') openVcsDrawer(workspaceId());
        else if (key === 'codegraph') openCodegraphPanel(workspaceId());
        else if (key === 'mem') openMemDrawer(workspaceId());
        return;
      }
      if (e.target.closest('#convRenameBtn')) {
        openRenameModal();
        return;
      }
      if (e.target.closest('#convTeamSave')) {
        const name = String(($('#convTeamName') || {}).value || '').trim();
        const mode = String(($('#convTeamMode') || {}).value || '');
        const speakers = Number(($('#convTeamSpeakers') || {}).value || 0) || null;
        if (!name) { toast(t('chat.titleEmpty'), 'bad', 2200); return; }
        await patchConvTeam({ name, mode, max_speakers: speakers });
        return;
      }
      if (e.target.closest('#convTeamUnbind')) {
        await unbindConvTeam();
        return;
      }
      if (e.target.closest('#convMemberAdd')) {
        const team = convDrawerData.team;
        if (!team) return;
        const name = String(($('#convMemberName') || {}).value || '').trim();
        const expertise = String(($('#convMemberExpertise') || {}).value || '').trim();
        if (!name) { toast(t('conv.memberName'), 'bad', 2200); return; }
        const members = (team.members || []).map(convMemberBrief)
          .concat([{ name, expertise }]);
        await patchConvTeam({ members });
        return;
      }
      if (e.target.closest('#convBindTeamBtn')) {
        const sel = $('#convBindTeam');
        await bindConvTeam(sel && sel.value);
        return;
      }
      if (e.target.closest('#convNewTeamBtn')) {
        const name = String(($('#convNewTeamName') || {}).value || '').trim();
        if (!name) { toast(t('conv.teamName'), 'bad', 2200); return; }
        try {
          const team = await api('/teams', {
            method: 'POST',
            body: JSON.stringify({
              workspace_id: workspaceId(), name, mode: 'orchestrate',
              leader: { name: t('conv.leaderName') },
            }),
          });
          await bindConvTeam(team.id);
          toast(t('conv.createGroup') + ' ✓', 'ok', 1800);
        } catch (err) {
          toast(err.message, 'bad', 5000);
        }
      }
    });
    drawer.addEventListener('change', async (e) => {
      if (e.target.closest('#convTeamMode') || e.target.closest('#convTeamSpeakers')) {
        const mode = String(($('#convTeamMode') || {}).value || '');
        const speakers = Number(($('#convTeamSpeakers') || {}).value || 0) || null;
        await patchConvTeam({ mode, max_speakers: speakers });
      }
    });
  }

  async function loadWorkspaceMenuPage(page) {
    state.wsMenuPage = Math.max(0, page);
    try {
      const kw = state.wsFilter ? `&keyword=${encodeURIComponent(state.wsFilter)}` : '';
      const data = await api(`/workspaces?limit=${WS_MENU_PAGE}&offset=${state.wsMenuPage * WS_MENU_PAGE}${kw}`);
      state.wsMenuItems = (data && data.items) || [];
      state.wsMenuTotal = Number(data && data.total) || 0;
    } catch (err) {
      console.warn('workspace page failed', err);
    }
    renderWorkspaceGroups();
  }

  // 每个工作区独立的「项目」展开状态：默认收起，进入时先看到全部工作区。
  // state.ui.wsOpen 只记「用户展开过」的项目，未记录 = 收起；
  // 展开/收起只在用户点该工作区的 chevron（data-ws-toggle）时发生，任何自动场景都不动它。
  function wsGroupOpen(wsId) {
    const map = state.ui.wsOpen && typeof state.ui.wsOpen === 'object' ? state.ui.wsOpen : {};
    return map[wsId] === true;
  }

  function setWsGroupOpen(wsId, open) {
    if (!state.ui.wsOpen || typeof state.ui.wsOpen !== 'object') state.ui.wsOpen = {};
    state.ui.wsOpen[wsId] = !!open;
    saveUi();
  }

  // 展开后只显示最近几条会话，不再有第二层完整列表；
  // 「更多会话」直接跟在最近会话下面。
  function wsPreviewHtml(recents, total, wsId, filter = '') {
    const rows = recents.length
      ? convSectionsHtml(recents, convMiniRow, { doneLabel: false, counts: false })
      : `<div class="conv-mini-empty">${escapeHtml(
        (filter ? t('chat.noMatch') : t('chat.empty')).replace(/<br\s*\/?>/gi, ' '),
      )}</div>`;
    const more = total > recents.length
      ? `<button class="more-convs" type="button" data-ws-more="${escAttr(wsId)}">
          <span data-i18n="conv.more">${escapeHtml(t('conv.more'))}</span>
        </button>`
      : '';
    return rows + more;
  }

  function toggleWsGroup(wsId) {
    if (!wsId) return;
    const group = $(`.ws-group[data-ws-group="${CSS.escape(wsId)}"]`);
    if (!group) return;
    const preview = group.querySelector('.ws-group-recent');
    const chevron = group.querySelector('[data-ws-toggle]');
    if (!preview || !chevron) return;
    const open = preview.hidden;
    preview.hidden = !open;
    const label = open ? t('conv.collapse') : t('conv.expand');
    chevron.setAttribute('aria-expanded', String(open));
    chevron.setAttribute('title', label);
    chevron.setAttribute('aria-label', label);
    setWsGroupOpen(wsId, open);
    if (open && wsId === workspaceId()) renderConversations();
  }

  // 一键收缩：把侧栏「工作区」里所有分组都收起（只收起分组，不动整栏和右侧面板）。
  // 与单个分组的 chevron / 名称点击互不影响，也不持久化（wsOpen 只在本次运行内有效）。
  function collapseAllWsGroups() {
    const groups = $$('#wsGroups .ws-group');
    if (!groups.length) return 0;
    state.ui.wsOpen = {};
    groups.forEach((group) => {
      const preview = group.querySelector('.ws-group-recent');
      const chevron = group.querySelector('[data-ws-toggle]');
      if (preview) preview.hidden = true;
      if (chevron) {
        const label = t('conv.expand');
        chevron.setAttribute('aria-expanded', 'false');
        chevron.setAttribute('title', label);
        chevron.setAttribute('aria-label', label);
      }
    });
    return groups.length;
  }

  function renderWorkspaceGroups() {
    const container = $('#wsGroups');
    if (!container) return;
    const hasFilter = !!state.wsFilter;
    const chatWs = chatWorkspace();
    const items = (state.wsMenuItems.length ? state.wsMenuItems.slice() : (hasFilter ? [] : state.workspaces.slice()))
      .filter((w) => !(chatWs && w.id === chatWs.id));
    const current = currentWorkspace();
    const activeId = workspaceId();
    if (current && !(chatWs && current.id === chatWs.id) && !items.some((w) => w.id === current.id)
      && (!hasFilter || (current.name || '').toLowerCase().includes(state.wsFilter.toLowerCase()))) {
      items.unshift(current);
    }
    if (!items.length) {
      container.innerHTML = `<div class="ws-groups-empty">${escapeHtml(hasFilter ? t('workspace.noMatch') : t('workspace.none'))}</div>`;
    } else {
      container.innerHTML = items.map((ws) => {
        const active = ws.id === activeId && !(chatWs && ws.id === chatWs.id);
        const tip = ws.chat_only ? t('workspace.chatOnlyNote') : (ws.root_path || '');
        const open = wsGroupOpen(ws.id);
        const recent = (ws.recent_conversations || []).slice(0, CONV_PREVIEW);
        // 活动工作区的内容由 renderConversations() 用实时数据覆盖；
        // 其它工作区用列表接口带回的最近会话。
        const preview = active
          ? `<div class="ws-group-recent" id="wsGroupPreview"${open ? '' : ' hidden'}></div>`
          : `<div class="ws-group-recent"${open ? '' : ' hidden'}>${wsPreviewHtml(recent, Number(ws.conversation_count) || 0, ws.id)}</div>`;
        const label = open ? t('conv.collapse') : t('conv.expand');
        const live = (state.activeRuns || []).some(
          (r) => r.workspace_id === ws.id && r.status === 'running');
        return `<div class="ws-group${active ? ' active' : ''}" data-ws-group="${escAttr(ws.id)}">
          <div class="ws-group-header">
            <button type="button" class="ws-group-switch" data-ws-switch="${escAttr(ws.id)}" title="${escAttr(tip)}">
              <span class="ws-group-icon"><svg><use href="#i-folder"/></svg></span>
              <span class="ws-group-name">${escapeHtml(ws.name)}</span>
              ${live ? `<span class="ws-group-live" title="${escAttr(t('workspace.live'))}" aria-label="${escAttr(t('workspace.live'))}"></span>` : ''}
            </button>
            <button type="button" class="ws-group-new" data-ws-new="${escAttr(ws.id)}" title="${escAttr(t('conv.newInWorkspace'))}" aria-label="${escAttr(t('conv.newInWorkspace'))}"><svg><use href="#i-plus"/></svg></button>
            <button type="button" class="ws-group-chevron" data-ws-toggle="${escAttr(ws.id)}" aria-expanded="${open}" title="${escAttr(label)}" aria-label="${escAttr(label)}"><svg><use href="#i-chevron-down"/></svg></button>
          </div>
          ${preview}
        </div>`;
      }).join('');
    }
    renderConversations();
    // 没有工作区分组时「一键收缩」没有意义，置灰。
    const collapseBtn = $('#wsCollapseAllBtn');
    if (collapseBtn) {
      const disabled = !items.length;
      collapseBtn.disabled = disabled;
      if (disabled) collapseBtn.setAttribute('aria-disabled', 'true');
      else collapseBtn.removeAttribute('aria-disabled');
    }
    const pager = $('#wsGroupsPager');
    if (pager) {
      const total = state.wsMenuTotal || items.length;
      const pages = Math.max(1, Math.ceil(total / WS_MENU_PAGE));
      if (pages > 1) {
        pager.hidden = false;
        $('#wsGroupsPrev').disabled = state.wsMenuPage <= 0;
        $('#wsGroupsNext').disabled = state.wsMenuPage >= pages - 1;
        $('#wsGroupsPageInfo').textContent = t('conv.page', { page: state.wsMenuPage + 1, pages });
      } else {
        pager.hidden = true;
      }
    }
  }

  // ── 版本控制（git / svn）面板 ──────────────────────────────────────
  function isVcsDrawerOpen() {
    return isPanelOpen('vcs');
  }

  function closeVcsDrawer() {
    closePanel('vcs');
  }

  function vcsPrefs(wsId) {
    try {
      const all = JSON.parse(localStorage.getItem('az-vcs-view') || '{}');
      return (all && all[wsId]) || {};
    } catch {
      return {};
    }
  }

  function setVcsPref(wsId, key, value) {
    try {
      const all = JSON.parse(localStorage.getItem('az-vcs-view') || '{}') || {};
      all[wsId] = { ...(all[wsId] || {}), [key]: value };
      localStorage.setItem('az-vcs-view', JSON.stringify(all));
    } catch {
      /* localStorage 不可用时只影响记忆，不影响当前视图 */
    }
  }

  function vcsViewBar(active, entries) {
    const items = [
      ['dir', t('vcs.viewDir'), '#i-folder'],
      ['flat', t('vcs.viewFlat'), '#i-list'],
    ];
    return `<div class="vcs-view-bar">
      <span class="vcs-view-count">${escapeHtml(t('vcs.changedCount', { n: (entries || []).length }))}</span>
      <div class="vcs-seg" role="group">`
      + items.map(([value, label, icon]) => `<button type="button" data-vcs-view="${value}"
          aria-pressed="${active === value}"><svg><use href="${icon}"/></svg><span>${escapeHtml(label)}</span></button>`).join('')
      + '</div></div>';
  }

  function vcsFileRow(e, vcsType, label) {
    const letter = { modified: 'M', added: 'A', deleted: 'D', untracked: '?', renamed: 'R',
      copied: 'C', conflict: '!', unknown: '·' }[e.state] || '·';
    const actions = vcsType === 'git'
      ? `<button type="button" class="vcs-row-btn" data-vcs-action="${e.staged ? 'unstage' : 'stage'}" data-vcs-path="${escAttr(e.path)}">${escapeHtml(t(e.staged ? 'vcs.unstage' : 'vcs.stage'))}</button>`
      : '';
    const name = label || e.path;
    return `<div class="vcs-file-row" data-vcs-state="${escAttr(e.state)}">
      <span class="vcs-state ${escAttr(e.state)}" title="${escAttr(e.state)}">${letter}</span>
      <button type="button" class="vcs-file-path" data-vcs-diff="${escAttr(e.path)}" title="${escAttr(e.path)}">${escapeHtml(name)}</button>
      <span class="vcs-file-actions">
        ${actions}
        <button type="button" class="vcs-row-btn" data-vcs-action="discard" data-vcs-path="${escAttr(e.path)}">${escapeHtml(t('vcs.discard'))}</button>
      </span>
      <div class="vcs-file-diff" hidden></div>
    </div>`;
  }

  function vcsTreeBuild(entries) {
    const root = { dirs: new Map(), files: [] };
    for (const entry of entries || []) {
      const parts = String(entry.path || '').split('/').filter(Boolean);
      if (!parts.length) continue;
      const name = parts.pop();
      let node = root;
      for (const part of parts) {
        if (!node.dirs.has(part)) node.dirs.set(part, { dirs: new Map(), files: [] });
        node = node.dirs.get(part);
      }
      node.files.push({ ...entry, name });
    }
    return root;
  }

  function vcsTreeStats(node) {
    const stats = { total: 0, modified: 0, added: 0, deleted: 0, untracked: 0,
      renamed: 0, copied: 0, conflict: 0, unknown: 0 };
    for (const file of node.files) {
      stats.total += 1;
      const key = String(file.state || 'unknown');
      if (stats[key] === undefined) stats.unknown += 1;
      else stats[key] += 1;
    }
    for (const child of node.dirs.values()) {
      const c = vcsTreeStats(child);
      for (const key of Object.keys(stats)) stats[key] += c[key] || 0;
    }
    return stats;
  }

  function vcsTreeBadges(stats) {
    const order = [
      ['modified', 'M'], ['added', 'A'], ['deleted', 'D'],
      ['untracked', '?'], ['renamed', 'R'], ['conflict', '!'],
    ];
    return order.filter(([key]) => stats[key]).map(([key, letter]) =>
      `<span class="vcs-tree-badge state-${key}" title="${escAttr(key)}">${letter}${stats[key]}</span>`).join('');
  }

  function vcsTreeHtml(node, vcsType) {
    const dirs = [...node.dirs.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name));
    const dirHtml = dirs.map(([name, child]) => {
      const stats = vcsTreeStats(child);
      return `<details class="vcs-tree-dir" open>
        <summary class="vcs-tree-summary">
          <svg class="vcs-tree-chevron"><use href="#i-chevron-right"/></svg>
          <svg class="vcs-tree-folder"><use href="#i-folder"/></svg>
          <span class="vcs-tree-name">${escapeHtml(name)}</span>
          <span class="vcs-tree-badges">${vcsTreeBadges(stats)}</span>
          <span class="vcs-tree-count">${stats.total}</span>
        </summary>
        <div class="vcs-tree-children">${vcsTreeHtml(child, vcsType)}</div>
      </details>`;
    }).join('');
    return dirHtml + files.map((e) => vcsFileRow(e, vcsType, e.name)).join('');
  }

  async function openVcsDrawer(wsId) {
    if (!panelEl('vcs')) return;
    const prefs = vcsPrefs(wsId);
    state.vcs = {
      ws: wsId, tab: 'status', summary: null,
      statusView: prefs.status === 'flat' ? 'flat' : 'dir',
      remoteEdit: '', fetching: '',
    };
    openPanel('vcs');
    await refreshVcs();
  }

  async function refreshVcs() {
    if (!isVcsDrawerOpen()) return;
    const wsId = state.vcs.ws;
    const content = $('#vcsContent');
    content.innerHTML = `<div class="vcs-loading"><span class="spinner"></span> ${escapeHtml(t('workspace.folderLoading'))}</div>`;
    try {
      const summary = await api(`/workspaces/${encodeURIComponent(wsId)}/vcs`);
      state.vcs.summary = summary;
      updateRailBadges();
      const chipCounts = summary.type
        ? {
          vcsCountStatus: summary.changed || 0,
          vcsCountLog: summary.commits || 0,
          vcsCountBranches: summary.branch_count || 0,
          vcsCountRemotes: summary.remote_count || 0,
        }
        : { vcsCountStatus: '', vcsCountLog: '', vcsCountBranches: '', vcsCountRemotes: '' };
      Object.entries(chipCounts).forEach(([id, n]) => {
        const el = $(`#${id}`);
        if (el) el.textContent = String(n);
      });
      const chips = [];
      if (summary.type) {
        chips.push(`<span class="vcs-chip vcs-chip-type">${escapeHtml(summary.type)}</span>`);
        if (summary.branch) chips.push(`<span class="vcs-chip">${escapeHtml(summary.branch)}</span>`);
        if (summary.ahead || summary.behind) chips.push(`<span class="vcs-chip vcs-chip-info">${escapeHtml(t('vcs.aheadBehind', { ahead: summary.ahead, behind: summary.behind }))}</span>`);
        chips.push(summary.changed
          ? `<span class="vcs-chip vcs-chip-warn">${escapeHtml(t('vcs.changedCount', { n: summary.changed }))}</span>`
          : `<span class="vcs-chip vcs-chip-ok">${escapeHtml(t('vcs.noChanges'))}</span>`);
        if (summary.last_commit) chips.push(`<span class="vcs-chip">${escapeHtml(String(summary.last_commit.rev))} · ${escapeHtml(relativeTime(summary.last_commit.date))}</span>`);
      }
      $('#vcsSubtitle').innerHTML = chips.join('');
      if (!summary.type) {
        content.innerHTML = `<div class="vcs-empty"><svg><use href="#i-alert"/></svg>${escapeHtml(summary.error || t('vcs.none'))}</div>`;
        return;
      }
      await renderVcsTab();
    } catch (err) {
      content.innerHTML = `<div class="vcs-empty"><svg><use href="#i-alert"/></svg>${escapeHtml(err.message)}</div>`;
    }
  }

  function vcsHistoryDayKey(date) {
    return String(date || '').slice(0, 10);
  }

  function vcsLocalDayKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  }

  function vcsHistoryDayLabel(key) {
    if (!key) return t('vcs.unknownDate');
    const now = new Date();
    const today = vcsLocalDayKey(now);
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (key === today) return t('vcs.today');
    if (key === vcsLocalDayKey(yesterday)) return t('vcs.yesterday');
    const d = new Date(`${key}T00:00:00`);
    return Number.isNaN(d.getTime())
      ? key
      : d.toLocaleDateString(uiLocale(), { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function vcsInitials(name) {
    const text = String(name || '').trim();
    if (!text) return '?';
    const parts = text.split(/\s+/).filter(Boolean);
    if (parts.length === 1) return Array.from(parts[0]).slice(0, 2).join('').toUpperCase();
    return (Array.from(parts[0])[0] + Array.from(parts[parts.length - 1])[0]).toUpperCase();
  }

  function vcsNameHue(name) {
    let hue = 0;
    for (const c of String(name || '')) hue = ((hue * 31) + c.charCodeAt(0)) >>> 0;
    return hue % 360;
  }

  function vcsRefChips(refs) {
    return (refs || []).slice(0, 4).map((raw) => {
      const value = String(raw || '').trim();
      if (!value) return '';
      let label = value;
      let cls = '';
      if (value.startsWith('tag: ')) {
        label = value.slice(5);
        cls = ' is-tag';
      } else if (value.includes('HEAD -> ')) {
        label = value.split('HEAD -> ').pop().trim();
        cls = ' is-head';
      } else if (value === 'HEAD') {
        label = t('vcs.refHead');
        cls = ' is-head';
      }
      return `<span class="vcs-ref${cls}">${escapeHtml(label)}</span>`;
    }).join('');
  }

  function vcsCommitHtml(c) {
    const initials = vcsInitials(c.author);
    const hue = vcsNameHue(c.author);
    const refs = vcsRefChips(c.refs);
    const merge = (c.parents || []).length > 1;
    return `<article class="vcs-log-row vcs-commit">
      <span class="vcs-commit-rail" aria-hidden="true"><i></i></span>
      <button type="button" class="vcs-commit-card" data-vcs-rev="${escAttr(String(c.rev))}">
        <span class="vcs-commit-top">
          <span class="vcs-commit-hash mono">${escapeHtml(String(c.rev))}</span>
          ${refs}
          ${merge ? `<span class="vcs-commit-badge">${escapeHtml(t('vcs.merge'))}</span>` : ''}
          <span class="vcs-commit-time">${escapeHtml(relativeTime(c.date))}</span>
        </span>
        <span class="vcs-commit-msg">${escapeHtml(truncate(c.message || '', 160))}</span>
        <span class="vcs-commit-meta">
          <span class="vcs-author-avatar" style="--av-h:${hue}">${escapeHtml(initials)}</span>
          <span class="vcs-commit-author">${escapeHtml(c.author || '')}</span>
          <svg class="vcs-expand"><use href="#i-chevron-down"/></svg>
        </span>
      </button>
      <div class="vcs-file-diff" hidden></div>
    </article>`;
  }

  function vcsHistoryHtml(commits) {
    const groups = [];
    let current = null;
    for (const c of commits || []) {
      const key = vcsHistoryDayKey(c.date);
      if (!current || current.key !== key) {
        current = { key, items: [] };
        groups.push(current);
      }
      current.items.push(c);
    }
    return groups.map((group) => `<section class="vcs-history-day">
      <div class="vcs-day-head">
        <span class="vcs-day-label">${escapeHtml(vcsHistoryDayLabel(group.key))}</span>
        <span class="vcs-day-count">${escapeHtml(t('vcs.commitsOnDay', { n: group.items.length }))}</span>
      </div>
      <div class="vcs-timeline">${group.items.map(vcsCommitHtml).join('')}</div>
    </section>`).join('');
  }

  function vcsRemoteCardHtml(remote) {
    const editing = state.vcs && state.vcs.remoteEdit === remote.name;
    const pushDiff = remote.push_url && remote.push_url !== remote.fetch_url;
    const url = remote.fetch_url || remote.url || '';
    return `<section class="vcs-remote-card${remote.tracking_branch ? ' is-upstream' : ''}" data-vcs-remote-card="${escAttr(remote.name)}">
      <div class="vcs-remote-head">
        <span class="vcs-remote-icon"><svg><use href="#i-globe"/></svg></span>
        <span class="vcs-remote-title">
          <span class="vcs-remote-name mono">${escapeHtml(remote.name)}</span>
          ${remote.tracking_branch ? `<span class="vcs-pill vcs-pill-up">${escapeHtml(t('vcs.upstream'))}</span>` : ''}
        </span>
        <span class="vcs-remote-actions">
          <button type="button" class="vcs-row-btn" data-vcs-fetch="${escAttr(remote.name)}">${escapeHtml(t('vcs.fetch'))}</button>
          <button type="button" class="vcs-row-btn" data-vcs-remote-edit="${escAttr(remote.name)}">${escapeHtml(t('vcs.editRemote'))}</button>
          <button type="button" class="vcs-row-btn danger" data-vcs-remote-remove="${escAttr(remote.name)}">${escapeHtml(t('vcs.removeRemote'))}</button>
        </span>
      </div>
      ${editing
        ? `<div class="vcs-remote-edit">
            <input class="input mono" data-vcs-remote-input value="${escAttr(url)}" spellcheck="false" aria-label="${escAttr(t('vcs.remoteUrl'))}">
            <button type="button" class="btn small" data-vcs-remote-save="${escAttr(remote.name)}">${escapeHtml(t('save'))}</button>
            <button type="button" class="vcs-row-btn" data-vcs-remote-cancel>${escapeHtml(t('cancel'))}</button>
          </div>`
        : `<div class="vcs-remote-url mono" title="${escAttr(url)}">${escapeHtml(url)}</div>`}
      ${pushDiff ? `<div class="vcs-remote-url vcs-remote-push mono" title="${escAttr(remote.push_url)}"><span>push</span>${escapeHtml(remote.push_url)}</div>` : ''}
      ${remote.tracking_branch ? `<div class="vcs-remote-track">${escapeHtml(t('vcs.tracking', { branch: remote.tracking_branch }))}</div>` : ''}
    </section>`;
  }

  function vcsRemotesHtml(data) {
    const remotes = (data && data.remotes) || [];
    const list = remotes.length
      ? `<div class="vcs-remote-list">${remotes.map(vcsRemoteCardHtml).join('')}</div>`
      : `<div class="vcs-empty"><svg><use href="#i-globe"/></svg>${escapeHtml(t('vcs.noRemotes'))}</div>`;
    return `<div class="vcs-remotes">
      <div class="vcs-section-head">
        <span class="vcs-section-title"><svg><use href="#i-globe"/></svg>${escapeHtml(t('vcs.remotes'))}</span>
        <span class="vcs-section-count">${remotes.length}</span>
      </div>
      ${list}
      <div class="vcs-remote-add">
        <div class="vcs-remote-add-head">${escapeHtml(t('vcs.addRemote'))}</div>
        <div class="vcs-remote-add-grid">
          <input class="input mono" id="vcsRemoteName" placeholder="${escAttr(t('vcs.remoteNamePlaceholder'))}" spellcheck="false" aria-label="${escAttr(t('vcs.remoteName'))}">
          <input class="input mono" id="vcsRemoteUrl" placeholder="${escAttr(t('vcs.remoteUrlPlaceholder'))}" spellcheck="false" aria-label="${escAttr(t('vcs.remoteUrl'))}">
          <button type="button" class="btn small" id="vcsRemoteAddBtn">${escapeHtml(t('vcs.addRemote'))}</button>
        </div>
      </div>
    </div>`;
  }

  function vcsBranchRow(name, current, extra = '', target = name) {
    return `<div class="vcs-branch-row${current ? ' current' : ''}">
      <span class="vcs-branch-mark"><svg><use href="#i-vcs"/></svg></span>
      <span class="vcs-branch-main">
        <span class="vcs-branch-name mono">${escapeHtml(name)}</span>
        ${extra}
      </span>
      ${current
        ? `<span class="vcs-badge">${escapeHtml(t('vcs.current'))}</span>`
        : `<button type="button" class="vcs-row-btn" data-vcs-branch="${escAttr(target)}">${escapeHtml(t('vcs.switch'))}</button>`}
    </div>`;
  }

  async function renderVcsTab() {
    const wsId = state.vcs.ws;
    const tab = state.vcs.tab;
    const content = $('#vcsContent');
    content.classList.toggle('is-remotes', tab === 'remotes');
    if (tab === 'status') {
      const data = await api(`/workspaces/${encodeURIComponent(wsId)}/vcs/status`);
      const entries = data.entries || [];
      const view = state.vcs.statusView === 'dir' ? 'dir' : 'flat';
      let html = vcsViewBar(view, entries);
      html += `<div class="vcs-commit-bar">
        <svg class="vcs-bar-icon"><use href="#i-vcs"/></svg>
        <input class="input vcs-commit-msg" id="vcsCommitMsg" type="text" placeholder="${escAttr(t('vcs.commitMsgPlaceholder'))}">
        <button type="button" class="btn small" id="vcsCommitBtn">${escapeHtml(t('vcs.commit'))}</button>
      </div>`;
      if (!entries.length) {
        html += `<div class="vcs-empty"><svg><use href="#i-check"/></svg>${escapeHtml(t('vcs.noChanges'))}</div>`;
      } else if (view === 'dir') {
        html += `<div class="vcs-tree">${vcsTreeHtml(vcsTreeBuild(entries), data.type)}</div>`;
      } else {
        html += '<div class="vcs-file-list">'
          + entries.map((e) => vcsFileRow(e, data.type)).join('') + '</div>';
      }
      content.innerHTML = html;
    } else if (tab === 'log') {
      const data = await api(`/workspaces/${encodeURIComponent(wsId)}/vcs/log?limit=40`);
      const commits = data.commits || [];
      content.innerHTML = commits.length
        ? `<div class="vcs-history">${vcsHistoryHtml(commits)}</div>`
        : `<div class="vcs-empty"><svg><use href="#i-clock"/></svg>${escapeHtml(t('vcs.noCommits'))}</div>`;
    } else if (tab === 'remotes') {
      if (state.vcs.summary && state.vcs.summary.type !== 'git') {
        content.innerHTML = `<div class="vcs-empty"><svg><use href="#i-globe"/></svg>${escapeHtml(t('vcs.gitOnlyRemotes'))}</div>`;
        return;
      }
      const data = await api(`/workspaces/${encodeURIComponent(wsId)}/vcs/remotes`);
      content.innerHTML = vcsRemotesHtml(data);
    } else {
      const data = await api(`/workspaces/${encodeURIComponent(wsId)}/vcs/branches`);
      let html = '';
      if (data.type === 'git') {
        const local = data.local || [];
        const remote = data.remote || [];
        html += `<div class="vcs-section-head">
          <span class="vcs-section-title"><svg><use href="#i-vcs"/></svg>${escapeHtml(t('vcs.localBranches'))}</span>
          <span class="vcs-section-count">${local.length}</span>
        </div>`;
        html += `<div class="vcs-branch-list">${local.map((b) => vcsBranchRow(b.name, b.current)).join('')}</div>`;
        if (remote.length) {
          const groups = new Map();
          for (const full of remote) {
            const idx = full.indexOf('/');
            const remoteName = idx > 0 ? full.slice(0, idx) : full;
            const branch = idx > 0 ? full.slice(idx + 1) : '';
            if (!groups.has(remoteName)) groups.set(remoteName, []);
            groups.get(remoteName).push({ full, branch });
          }
          html += `<div class="vcs-section-head">
            <span class="vcs-section-title"><svg><use href="#i-globe"/></svg>${escapeHtml(t('vcs.remoteBranches'))}</span>
            <span class="vcs-section-count">${remote.length}</span>
          </div>`;
          html += [...groups.entries()].map(([remoteName, branches]) => `<div class="vcs-branch-group">
            <div class="vcs-group-label"><span class="mono">${escapeHtml(remoteName)}</span><span>${branches.length}</span></div>
            ${branches.map((b) => vcsBranchRow(b.branch || b.full, false, '', b.full)).join('')}
          </div>`).join('');
        }
      } else {
        const list = (data.branches || []).slice();
        if (data.current && !list.includes(data.current)) list.unshift(data.current);
        html += `<div class="vcs-section-head">
          <span class="vcs-section-title"><svg><use href="#i-vcs"/></svg>${escapeHtml(t('vcs.localBranches'))}</span>
          <span class="vcs-section-count">${list.length}</span>
        </div>`;
        html += `<div class="vcs-branch-list">${list.map((b) => vcsBranchRow(b, b === data.current)).join('')}</div>`;
      }
      html += `<div class="vcs-commit-bar">
        <svg class="vcs-bar-icon"><use href="#i-plus"/></svg>
        <input class="input vcs-commit-msg" id="vcsNewBranch" type="text" placeholder="${escAttr(t('vcs.newBranchPlaceholder'))}">
        <button type="button" class="btn small" id="vcsCreateBranchBtn">${escapeHtml(t('vcs.create'))}</button>
      </div>`;
      content.innerHTML = html;
    }
  }

  async function showVcsDiff(container, query) {
    const data = await api(`/workspaces/${encodeURIComponent(state.vcs.ws)}/vcs/diff${query}`);
    const text = (data.diff || '').trim();
    container.hidden = false;
    container.innerHTML = text
      ? `<pre class="diff-pre">${text.split('\n').map(diffLineHtml).join('\n')}</pre>`
      : `<div class="vcs-empty"><svg><use href="#i-check"/></svg>${escapeHtml(t('vcs.noChanges'))}</div>`;
  }

  // ── 记忆管理（列表 / 图谱 / 统计）───────────────────────────────────
  const MEM_PAGE = 20;
  const MEM_KIND_COLORS = {
    fact: 'var(--info)',
    decision: 'var(--success)',
    preference: 'var(--warning)',
    experience: 'var(--text-dim)',
  };

  function isMemDrawerOpen() {
    return isPanelOpen('mem');
  }

  function closeMemDrawer() {
    closePanel('mem');
  }

  /* ── Agent 团队：完全动态 ──────────────────────────────────────
     团队没有界面配置入口：全部由 agent 在对话中通过 team_manage 定义、绑定。 */

  async function openMemDrawer(wsId) {
    if (!panelEl('mem')) return;
    // 纯聊天工作区不提供记忆视图：入口已隐藏，这里再兜一层，
    // 避免快捷键 / 恢复面板时把空面板重新拉出来。
    if (isChatOnlyWorkspace()) {
      if (isMemDrawerOpen()) closeMemDrawer();
      return;
    }
    state.mem = {
      ws: wsId, q: '', kind: '', sort: 'recent', page: 0, includeArchived: false,
      total: 0, graphFilter: 'all', stats: null, graph: null, rebuild: null,
      view: state.ui.memView === 'graph' ? 'graph' : 'list',
    };
    openPanel('mem');
    await refreshMem();
    renderMemView(false);
    try {
      const job = await api(`/workspaces/${encodeURIComponent(wsId)}/memories/rebuild/status`);
      if (job && job.status === 'running') {
        state.mem.rebuild = job;
        renderMemSummary();
        startRebuildPoll();
      }
    } catch (err) {
      /* status unavailable is fine */
    }
  }

  /* ── CodeGraph panel（纯本地索引，不调用 LLM） ───────────────────── */

  let codegraphBusy = false;

  async function openCodegraphPanel(wsId) {
    if (!wsId || !panelEl('codegraph')) return;
    state.codegraph = {
      data: null, q: '',
      view: state.ui.codegraphView === 'flat' ? 'flat' : 'tree',
    };
    const search = $('#codegraphSearchInput');
    if (search) search.value = '';
    openPanel('codegraph');
    await loadCodegraphPanel();
  }

  async function loadCodegraphPanel() {
    const wsId = workspaceId();
    if (!wsId) return;
    const list = $('#codegraphList');
    const summary = $('#codegraphSummary');
    if (list) list.innerHTML = `<div class="vcs-loading"><span class="spinner"></span> ${escapeHtml(t('workspace.folderLoading'))}</div>`;
    if (summary) summary.innerHTML = '';
    try {
      const data = await api(`/app/codegraph?workspace_id=${encodeURIComponent(wsId)}&entries=1`);
      renderCodegraphPanel(data);
    } catch (err) {
      if (list) list.innerHTML = `<div class="codegraph-empty">${escapeHtml(t('codegraph.failed', { msg: err.message }))}</div>`;
    }
  }

  function codegraphFileRow(e, label, full = false) {
    const symbols = e.symbols || [];
    const lines = e.lines || {};
    const shown = symbols.slice(0, 6);
    const chips = shown.map((s) => `<span class="cg-symbol">${escapeHtml(s)}<i>${Number(lines[s]) || 1}</i></span>`).join('');
    const more = symbols.length > shown.length ? `<span class="cg-symbol cg-symbol-more">+${symbols.length - shown.length}</span>` : '';
    const imports = (e.imports || []).length;
    const name = label || e.path || '';
    return `<button type="button" class="cg-file-row${full ? ' is-flat' : ''}" data-codegraph-path="${escAttr(e.path || '')}" title="${escAttr(e.path || '')}">
      <span class="cg-file-icon"><svg><use href="#i-file"/></svg></span>
      <span class="cg-file-main">
        <span class="cg-file-head">
          <span class="cg-file-name mono">${escapeHtml(full ? (e.path || name) : name)}</span>
          <span class="cg-file-count">${escapeHtml(t('codegraph.symbols', { n: symbols.length }))}</span>
        </span>
        <span class="cg-file-symbols">${chips}${more}${chips || more ? '' : `<span class="cg-file-none">${escapeHtml(t('codegraph.noSymbols'))}</span>`}</span>
        ${imports ? `<span class="cg-file-imports">${escapeHtml(t('codegraph.deps', { n: imports }))}</span>` : ''}
      </span>
      <svg class="cg-file-open"><use href="#i-chevron-right"/></svg>
    </button>`;
  }

  function codegraphTreeBuild(entries) {
    const root = { dirs: new Map(), files: [] };
    for (const entry of entries || []) {
      const parts = String(entry.path || '').split('/').filter(Boolean);
      if (!parts.length) continue;
      const name = parts.pop();
      let node = root;
      for (const part of parts) {
        if (!node.dirs.has(part)) node.dirs.set(part, { dirs: new Map(), files: [] });
        node = node.dirs.get(part);
      }
      node.files.push({ ...entry, name });
    }
    return root;
  }

  function codegraphTreeStats(node) {
    const stats = { files: 0, symbols: 0 };
    for (const file of node.files) {
      stats.files += 1;
      stats.symbols += (file.symbols || []).length;
    }
    for (const child of node.dirs.values()) {
      const c = codegraphTreeStats(child);
      stats.files += c.files;
      stats.symbols += c.symbols;
    }
    return stats;
  }

  function codegraphTreeHtml(node) {
    const dirs = [...node.dirs.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name));
    const dirHtml = dirs.map(([name, child]) => {
      const stats = codegraphTreeStats(child);
      return `<details class="cg-dir" open>
        <summary class="cg-dir-summary">
          <svg class="cg-dir-chevron"><use href="#i-chevron-right"/></svg>
          <svg class="cg-dir-folder"><use href="#i-folder"/></svg>
          <span class="cg-dir-name mono">${escapeHtml(name)}</span>
          <span class="cg-dir-meta">${escapeHtml(t('codegraph.files', { n: stats.files }))} · ${escapeHtml(t('codegraph.symbols', { n: stats.symbols }))}</span>
        </summary>
        <div class="cg-dir-children">${codegraphTreeHtml(child)}</div>
      </details>`;
    }).join('');
    return dirHtml + files.map((e) => codegraphFileRow(e, e.name)).join('');
  }

  function codegraphEntries() {
    const data = state.codegraph && state.codegraph.data;
    const q = String((state.codegraph && state.codegraph.q) || '').trim().toLowerCase();
    const entries = (data && data.entries) || [];
    if (!q) return entries;
    return entries.filter((e) => {
      if (String(e.path || '').toLowerCase().includes(q)) return true;
      if ((e.symbols || []).some((s) => String(s).toLowerCase().includes(q))) return true;
      return (e.imports || []).some((s) => String(s).toLowerCase().includes(q));
    });
  }

  function renderCodegraphList() {
    const list = $('#codegraphList');
    if (!list) return;
    const data = state.codegraph && state.codegraph.data;
    const view = state.codegraph && state.codegraph.view === 'flat' ? 'flat' : 'tree';
    $$('#codegraphViewSeg [data-codegraph-view]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.codegraphView === view));
    });
    if (!data) return;
    const entries = codegraphEntries();
    if (!entries.length) {
      list.innerHTML = `<div class="codegraph-empty"><svg><use href="#i-code"/></svg>${escapeHtml(
        state.codegraph.q ? t('codegraph.noMatch') : t('codegraph.empty'))}</div>`;
      return;
    }
    list.innerHTML = view === 'flat'
      ? `<div class="cg-file-list">${entries.map((e) => codegraphFileRow(e, e.path, true)).join('')}</div>`
      : `<div class="cg-tree">${codegraphTreeHtml(codegraphTreeBuild(entries))}</div>`;
  }

  function renderCodegraphPanel(data) {
    const d = data || {};
    if (!state.codegraph) state.codegraph = { data: null, q: '', view: 'tree' };
    state.codegraph.data = d;
    const summary = $('#codegraphSummary');
    if (summary) {
      const tiles = [
        [t('codegraph.statFiles'), num(d.files || 0)],
        [t('codegraph.statSymbols'), num(d.symbols || 0)],
        [t('codegraph.statDeps'), num(d.edges || 0)],
        [t('codegraph.statUpdated'), d.updated || '—'],
      ];
      summary.innerHTML = `<div class="cg-stat-grid">${tiles.map(([label, value]) => `<span class="cg-stat-tile"><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></span>`).join('')}</div>`;
    }
    const sub = $('#codegraphSubtitle');
    if (sub) sub.textContent = t('codegraph.files', { n: num(d.files || 0) });
    renderCodegraphList();
  }

  async function runCodegraphAction(action) {
    const wsId = workspaceId();
    if (!wsId || codegraphBusy) return;
    codegraphBusy = true;
    const buttons = ['#codegraphInitBtn', '#codegraphUpdateBtn', '#codegraphRebuildBtn'];
    buttons.forEach((sel) => { const b = $(sel); if (b) b.disabled = true; });
    const summary = $('#codegraphSummary');
    if (summary) summary.innerHTML = `<span class="cg-running"><span class="spinner"></span>${escapeHtml(t('codegraph.running'))}</span>`;
    try {
      await api('/app/codegraph/action', {
        method: 'POST',
        body: JSON.stringify({ workspace_id: wsId, action }),
      });
      const msg = action === 'rebuild' ? t('codegraph.rebuildDone')
        : action === 'init' ? t('codegraph.initDone') : t('codegraph.done');
      toast(msg, 'ok', 2400);
      await loadCodegraphPanel();
    } catch (err) {
      toast(t('codegraph.failed', { msg: err.message || String(err) }), 'bad', 4500);
    } finally {
      codegraphBusy = false;
      buttons.forEach((sel) => { const b = $(sel); if (b) b.disabled = false; });
    }
  }

  async function refreshMem() {
    if (!isMemDrawerOpen()) return;
    const wsId = state.mem.ws;
    const content = $('#memContent');
    content.innerHTML = `<div class="vcs-loading"><span class="spinner"></span> ${escapeHtml(t('workspace.folderLoading'))}</div>`;
    try {
      const stats = await api(`/workspaces/${encodeURIComponent(wsId)}/memories/stats`);
      state.mem.stats = stats;
      renderMemSummary();
      renderSideStats();
      await renderMemList();
      if (state.mem.view === 'graph') await renderSideGraph(true);
      renderMemView(false);
    } catch (err) {
      content.innerHTML = `<div class="vcs-empty"><svg><use href="#i-alert"/></svg>${escapeHtml(err.message)}</div>`;
    }
  }

  function renderMemSummary() {
    const s = state.mem && state.mem.stats;
    const summary = $('#memSummary');
    if (!s || !summary) return;
    const tiles = [
      [t('mem.statTotal'), num(s.total || 0)],
      [t('mem.statLinked'), num(s.linked_memory_count || 0)],
      [t('mem.statEntities'), num(s.entity_count || 0)],
      [t('mem.statConfidence'), `${Math.round((s.avg_confidence || 0) * 100)}%`],
    ];
    const kinds = ['fact', 'decision', 'preference', 'experience'];
    const kindTotal = Math.max(1, kinds.reduce((sum, k) => sum + (s.kind_counts[k] || 0), 0));
    const segments = kinds.filter((k) => (s.kind_counts[k] || 0) > 0).map((k) => {
      const n = s.kind_counts[k] || 0;
      return `<span class="mem-kind-seg" style="width:${(n / kindTotal) * 100}%;background:${MEM_KIND_COLORS[k] || 'var(--text-dim)'}" title="${escAttr(k)} ${n}"></span>`;
    }).join('');
    const notes = [];
    if (s.orphan_memory_count) notes.push(`${escapeHtml(t('mem.statsOrphan'))} ${s.orphan_memory_count}`);
    if (s.archived_count) notes.push(`${escapeHtml(t('mem.archivedCount'))} ${s.archived_count}`);
    if (s.oldest) notes.push(`${escapeHtml(t('mem.statsOldest'))} ${escapeHtml(relativeTime(s.oldest))}`);
    const job = state.mem.rebuild;
    summary.innerHTML = `<div class="mem-summary-tiles">
      ${tiles.map(([label, value]) => `<span class="mem-summary-tile"><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></span>`).join('')}
    </div>
    <div class="mem-summary-meter">
      <span class="mem-kind-meter">${segments || '<i></i>'}</span>
      <span class="mem-summary-notes">${notes.map((n) => `<span>${n}</span>`).join('')}</span>
      ${job && job.status === 'running' ? `<span class="mem-rebuild-status">${escapeHtml(t('mem.rebuildRunning'))}…</span>` : ''}
    </div>`;
    const btn = $('#memRebuildBtn');
    if (btn) btn.disabled = !!(job && job.status === 'running');
  }

  const MEM_ARCHIVE_REASONS = {
    duplicate: 'mem.archiveDuplicate',
    stale: 'mem.archiveStale',
    low_value: 'mem.archiveLow',
    overflow: 'mem.archiveOverflow',
  };

  function memRowHtml(m) {
    const tags = (m.tags || []).map((x) => `<span class="mem-tag">${escapeHtml(x)}</span>`).join('');
    const ents = (m.linked_entities || []).map((e) => `<span class="mem-entity-chip mono">${escapeHtml(String(e).split(':').slice(-1)[0])}</span>`).join('');
    const mergedChip = m.merged_count
      ? `<span class="mem-archived-reason">${escapeHtml(t('mem.mergedCount', { n: m.merged_count }))}</span>`
      : '';
    let meta = '';
    if (tags || ents || mergedChip) {
      meta = `<div class="mem-row-meta">${tags}${ents ? `<span class="mem-link-label">${escapeHtml(t('mem.linkedTo'))}: ${ents}</span>` : ''}${mergedChip}</div>`;
    }
    if (m.archived) {
      const key = MEM_ARCHIVE_REASONS[m.archived_reason] || '';
      meta += `<div class="mem-row-meta"><span class="mem-archived-badge">${escapeHtml(t('mem.archived'))}</span>${key ? `<span class="mem-archived-reason">${escapeHtml(t(key))}</span>` : ''}</div>`;
    }
    const color = MEM_KIND_COLORS[m.memory_type] || 'var(--text-dim)';
    const conf = Math.round((m.confidence || 0) * 100);
    return `<div class="mem-row${m.archived ? ' archived' : ''}" style="--mem-accent:${color}" data-mem-id="${escAttr(m.id)}">
      <div class="mem-row-head">
        <span class="mem-kind-badge" style="color:${color}">${escapeHtml(m.memory_type)}</span>
        <span class="mem-src">${escapeHtml(m.source === 'auto' ? t('mem.auto') : m.source === 'llm' ? 'LLM' : t('mem.manual'))}</span>
        <span class="mem-conf" title="${escapeHtml(t('mem.statsAvgConf'))}">
          <span class="mem-conf-track"><i style="width:${conf}%;background:${color}"></i></span>
          <span>${conf}%</span>
        </span>
        <span class="mem-time mono">${escapeHtml(relativeTime(m.created_at))}</span>
      </div>
      <div class="mem-row-content">${escapeHtml(m.content)}</div>
      ${meta}
    </div>`;
  }

  async function renderMemList() {
    const wsId = state.mem.ws;
    const content = $('#memContent');
    const params = new URLSearchParams({
      limit: String(MEM_PAGE), offset: String(state.mem.page * MEM_PAGE), sort: state.mem.sort,
    });
    if (state.mem.q) params.set('q', state.mem.q);
    if (state.mem.kind) params.set('kind', state.mem.kind);
    if (state.mem.includeArchived) params.set('include_archived', 'true');
    const data = await api(`/workspaces/${encodeURIComponent(wsId)}/memories?${params}`);
    state.mem.total = Number(data.total) || 0;
    const kinds = ['fact', 'decision', 'preference', 'experience'];
    let html = `<div class="mem-filter-row">
      <div class="search-box mem-search">
        <svg><use href="#i-search"/></svg>
        <input id="memSearchInput" type="search" value="${escAttr(state.mem.q)}" placeholder="${escAttr(t('mem.searchPlaceholder'))}" autocomplete="off" />
      </div>
      <div class="seg" id="memKindSeg" role="group">
        <button type="button" data-mem-filter="" aria-pressed="${String(!state.mem.kind)}">${escapeHtml(t('mem.allKinds'))}</button>
        ${kinds.map((k) => `<button type="button" data-mem-filter="${k}" aria-pressed="${String(state.mem.kind === k)}">${escapeHtml(k)}</button>`).join('')}
      </div>
      <div class="seg" id="memSortSeg" role="group">
        ${[['recent', t('mem.sortRecent')], ['oldest', t('mem.sortOldest')], ['confidence', t('mem.sortConf')]].map(([s, label]) => `<button type="button" data-mem-sort="${s}" aria-pressed="${String(state.mem.sort === s)}">${escapeHtml(label)}</button>`).join('')}
      </div>
      <div class="seg" id="memArchiveSeg" role="group">
        <button type="button" data-mem-archived aria-pressed="${String(!!state.mem.includeArchived)}" title="${escAttr(t('mem.includeArchived'))}">${escapeHtml(t('mem.includeArchived'))}${state.mem.stats && state.mem.stats.archived_count ? ` ${state.mem.stats.archived_count}` : ''}</button>
      </div>
    </div>`;
    if (!data.items.length) {
      html += `<div class="vcs-empty"><svg><use href="#i-brain"/></svg>${escapeHtml(state.mem.q || state.mem.kind ? t('mem.emptyMatch') : t('mem.empty'))}</div>`;
    } else {
      html += `<div class="mem-list">${data.items.map(memRowHtml).join('')}</div>`;
      const pages = Math.max(1, Math.ceil((state.mem.total || 0) / MEM_PAGE));
      if (pages > 1) {
        html += `<div class="mem-pager">
          <button type="button" class="pager-btn" data-mem-page="prev" ${state.mem.page <= 0 ? 'disabled' : ''}><svg><use href="#i-chevron-left"/></svg></button>
          <span class="pager-info">${escapeHtml(t('conv.page', { page: state.mem.page + 1, pages }))}</span>
          <button type="button" class="pager-btn" data-mem-page="next" ${state.mem.page >= pages - 1 ? 'disabled' : ''}><svg><use href="#i-chevron-right"/></svg></button>
        </div>`;
      }
    }
    content.innerHTML = html;
  }

  function graphLayout(nodes, edges, W, H, rad) {
    const idStr = nodes.map((n) => n.id).join('|');
    let seed = 2166136261;
    for (let i = 0; i < idStr.length; i++) {
      seed ^= idStr.charCodeAt(i);
      seed = (seed * 16777619) >>> 0;
    }
    const rand = () => {
      seed = (seed * 1103515245 + 12345) >>> 0;
      return seed / 4294967296;
    };
    const radius = (i) => (rad && rad[nodes[i].id]) || 30;
    const pos = new Map();
    const n = nodes.length;
    nodes.forEach((node, i) => {
      const a = (i / n) * Math.PI * 2;
      const r = Math.min(W, H) * 0.32;
      pos.set(node.id, {
        x: W / 2 + Math.cos(a) * r + (rand() - 0.5) * 30,
        y: H / 2 + Math.sin(a) * r + (rand() - 0.5) * 30,
      });
    });
    const idx = new Map(nodes.map((node, i) => [node.id, i]));
    const springs = [];
    const seenPair = new Set();
    for (const e of edges) {
      const a = idx.get(e.from_id);
      const b = idx.get(e.to_id);
      if (a === undefined || b === undefined || a === b) continue;
      const key = a < b ? a + ':' + b : b + ':' + a;
      if (seenPair.has(key)) continue;
      seenPair.add(key);
      springs.push([a, b, e.kind === 'references' ? 170 : 130]);
    }
    for (let it = 0; it < 320; it++) {
      const fx = new Array(n).fill(0);
      const fy = new Array(n).fill(0);
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const pi = pos.get(nodes[i].id);
          const pj = pos.get(nodes[j].id);
          let dx = pi.x - pj.x;
          let dy = pi.y - pj.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) { dx = rand() - 0.5; dy = rand() - 0.5; d2 = 1; }
          const f = 34000 / d2;
          const d = Math.sqrt(d2);
          fx[i] += dx / d * f; fy[i] += dy / d * f;
          fx[j] -= dx / d * f; fy[j] -= dy / d * f;
        }
      }
      for (const [a, b, ideal] of springs) {
        const pa = pos.get(nodes[a].id);
        const pb = pos.get(nodes[b].id);
        const dx = pb.x - pa.x;
        const dy = pb.y - pa.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - ideal) * 0.04;
        fx[a] += dx / d * f; fy[a] += dy / d * f;
        fx[b] -= dx / d * f; fy[b] -= dy / d * f;
      }
      const damp = Math.max(0.15, 0.8 - it / 400);
      for (let i = 0; i < n; i++) {
        const p = pos.get(nodes[i].id);
        fx[i] += (W / 2 - p.x) * 0.005;
        fy[i] += (H / 2 - p.y) * 0.005;
        p.x += fx[i] * damp;
        p.y += fy[i] * damp;
      }
    }
    // collision resolution: keep node bodies and their labels apart
    for (let pass = 0; pass < 60; pass++) {
      let moved = false;
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const pi = pos.get(nodes[i].id);
          const pj = pos.get(nodes[j].id);
          const min = radius(i) + radius(j);
          let dx = pi.x - pj.x;
          let dy = pi.y - pj.y;
          let d = Math.sqrt(dx * dx + dy * dy);
          if (d >= min) continue;
          if (d < 1) { dx = rand() - 0.5; dy = rand() - 0.5; d = 1; }
          const f = (min - d) / 2;
          pi.x += dx / d * f; pi.y += dy / d * f;
          pj.x -= dx / d * f; pj.y -= dy / d * f;
          moved = true;
        }
      }
      if (!moved) break;
    }
    for (let i = 0; i < n; i++) {
      const p = pos.get(nodes[i].id);
      const r = Math.min(radius(i), Math.min(W, H) / 2);
      p.x = Math.max(r, Math.min(W - r, p.x));
      p.y = Math.max(r, Math.min(H - r, p.y));
    }
    return pos;
  }

  async function loadMemGraph() {
    state.mem.graph = await api(`/workspaces/${encodeURIComponent(state.mem.ws)}/memories/graph?limit=200`);
    return state.mem.graph;
  }

  function graphControlsHtml() {
    const filters = [['all', t('mem.graphFilterAll')], ['mem', t('mem.graphFilterMem')], ['code', t('mem.graphFilterCode')]];
    return `
      <div class="seg" role="group">
        ${filters.map(([f, label]) => `<button type="button" data-mem-graph-filter="${f}" aria-pressed="${String(state.mem.graphFilter === f)}">${escapeHtml(label)}</button>`).join('')}
      </div>
      <div class="mem-graph-legend">
        ${Object.entries(MEM_KIND_COLORS).map(([k, c]) => `<span class="mem-legend-item"><span class="mem-legend-dot" style="background:${c}"></span>${escapeHtml(k)}</span>`).join('')}
        <span class="mem-legend-item"><span class="mem-legend-square"></span>entity</span>
      </div>
      <div class="mem-graph-zoom" role="group">
        <button type="button" data-zoom="out" title="${escAttr(t('mem.graphZoomOut'))}" aria-label="${escAttr(t('mem.graphZoomOut'))}">−</button>
        <button type="button" data-zoom="in" title="${escAttr(t('mem.graphZoomIn'))}" aria-label="${escAttr(t('mem.graphZoomIn'))}">＋</button>
        <button type="button" data-zoom="fit" title="${escAttr(t('mem.graphZoomFit'))}" aria-label="${escAttr(t('mem.graphZoomFit'))}">${escapeHtml(t('mem.graphZoomFit'))}</button>
      </div>`;
  }

  function layeredLayout(nodes, edges, W, H) {
    const mems = nodes.filter((n) => n.kind === 'memory');
    const ents = nodes.filter((n) => n.kind === 'entity');
    const deg = {};
    for (const e of edges) {
      deg[e.from_id] = (deg[e.from_id] || 0) + 1;
      deg[e.to_id] = (deg[e.to_id] || 0) + 1;
    }
    const rows = Math.max(mems.length, 1);
    const step = Math.min(52, Math.max(22, (H - 28) / rows));
    const top = Math.max(14, (H - step * mems.length) / 2);
    mems.sort((a, b) => (deg[b.id] || 0) - (deg[a.id] || 0));
    const pos = new Map();
    mems.forEach((n, i) => pos.set(n.id, { x: 18 + 85, y: top + i * step + step / 2 }));
    const memIdx = new Map(mems.map((n, i) => [n.id, i]));
    const avgMemIdx = (id) => {
      const list = [];
      for (const e of edges) {
        if (e.from_id === id && memIdx.has(e.to_id)) list.push(memIdx.get(e.to_id));
        else if (e.to_id === id && memIdx.has(e.from_id)) list.push(memIdx.get(e.from_id));
      }
      return list.length ? list.reduce((a, b) => a + b, 0) / list.length : -1;
    };
    ents.sort((a, b) => (deg[b.id] || 0) - (deg[a.id] || 0) || avgMemIdx(a.id) - avgMemIdx(b.id));
    const span = mems.length * step;
    ents.forEach((n, i) => {
      const y = ents.length === 1 ? H / 2 : top + (i + 0.5) * (span / ents.length);
      pos.set(n.id, { x: W - 18 - 75, y });
    });
    return pos;
  }

  function edgePathD(a, b, wa, wb) {
    const dir = b.x >= a.x ? 1 : -1;
    const ax = a.x + dir * wa / 2;
    const ay = a.y;
    const bx = b.x - dir * wb / 2;
    const by = b.y;
    const dx = bx - ax;
    const dy = by - ay;
    if (Math.abs(dx) < 60) {
      const bulge = 80;
      return `M ${ax.toFixed(1)} ${ay.toFixed(1)} C ${(ax + bulge).toFixed(1)} ${(ay + dy / 2).toFixed(1)}, ${(bx + bulge).toFixed(1)} ${(by - dy / 2).toFixed(1)}, ${bx.toFixed(1)} ${by.toFixed(1)}`;
    }
    return `M ${ax.toFixed(1)} ${ay.toFixed(1)} C ${(ax + dx / 2).toFixed(1)} ${ay.toFixed(1)}, ${(bx - dx / 2).toFixed(1)} ${by.toFixed(1)}, ${bx.toFixed(1)} ${by.toFixed(1)}`;
  }

  function memNodeLabel(n) {
    return String(n.label || '').replace(/^目标\s*[:：]\s*\[[^\]]*\]\s*/, '');
  }

  function graphSvgHtml(nodes, edges, pos, widths, view, labelLen, svgId, W) {
    let svg = `<svg class="mem-graph" viewBox="${view.vx.toFixed(1)} ${view.vy.toFixed(1)} ${view.vw.toFixed(1)} ${view.vh.toFixed(1)}" role="img" aria-label="memory graph">`
      + `<defs><marker id="${svgId}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--text-faint)"/></marker></defs>`;
    for (const e of edges) {
      svg += `<path class="mem-edge${e.kind === 'references' ? ' mem-edge-ref' : ''}" data-from="${escAttr(e.from_id)}" data-to="${escAttr(e.to_id)}" d="${edgePathD(pos.get(e.from_id), pos.get(e.to_id), widths[e.from_id] || 160, widths[e.to_id] || 160)}" marker-end="url(#${svgId}-arrow)"/>`;
    }
    for (const n of nodes) {
      const p = pos.get(n.id);
      const w = widths[n.id] || 160;
      if (n.kind === 'memory') {
        const color = MEM_KIND_COLORS[n.memory_type] || 'var(--text-dim)';
        svg += `<g class="mem-gnode" data-mem-node="${escAttr(n.id)}" transform="translate(${p.x.toFixed(1)}, ${p.y.toFixed(1)})">
          <rect x="${(-w / 2).toFixed(1)}" y="-16" width="${w}" height="32" rx="9" fill="var(--panel-3)" stroke="${color}" stroke-width="1.6"/>
          <circle cx="${(-w / 2 + 13).toFixed(1)}" cy="0" r="4.5" fill="${color}"/>
          <text x="${(-w / 2 + 24).toFixed(1)}" y="3.5">${escapeHtml(truncate(memNodeLabel(n), 16))}</text>
        </g>`;
      } else {
        svg += `<g class="mem-gnode" data-mem-node="${escAttr(n.id)}" transform="translate(${p.x.toFixed(1)}, ${p.y.toFixed(1)})">
          <rect x="${(-w / 2).toFixed(1)}" y="-16" width="${w}" height="32" rx="9" fill="var(--panel-3)" stroke="var(--info)" stroke-width="1.6"/>
          <text x="0" y="3.5" text-anchor="middle">${escapeHtml(truncate(n.label, Math.max(6, Math.floor((w - 22) / 7.6))))}</text>
        </g>`;
      }
    }
    svg += '</svg>';
    return svg;
  }

  const MEM_GRAPH_W = 560, MEM_GRAPH_H = 440;

  function renderMemView(refetch) {
    const graph = state.mem.view === 'graph';
    const list = $('#memContent');
    const pane = $('#memGraphPane');
    if (list) list.hidden = graph;
    if (pane) pane.hidden = !graph;
    $$('#memViewSeg [data-mem-view]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.memView === state.mem.view));
    });
    if (graph) renderSideGraph(!!refetch).catch(() => {});
  }

  function setMemView(view) {
    state.mem.view = view === 'graph' ? 'graph' : 'list';
    state.ui.memView = state.mem.view;
    saveUi();
    renderMemView(true);
  }

  async function renderSideGraph(refetch) {
    if (refetch || !state.mem.graph) await loadMemGraph();
    const data = state.mem.graph;
    const controlsEl = $('#memGraphSideControls');
    const infoEl = $('#memGraphSideInfo');
    const bodyEl = $('#memGraphSideBody');
    controlsEl.innerHTML = graphControlsHtml();
    infoEl.hidden = true;
    let nodes = data.nodes;
    if (state.mem.graphFilter === 'mem') nodes = nodes.filter((n) => n.kind === 'memory');
    else if (state.mem.graphFilter === 'code') nodes = nodes.filter((n) => n.kind === 'entity');
    const MAX_GRAPH_NODES = 18;
    let capped = false;
    for (const kind of ['memory', 'entity']) {
      const same = nodes.filter((n) => n.kind === kind);
      if (same.length > MAX_GRAPH_NODES) {
        const keepIds = new Set(same.slice(0, MAX_GRAPH_NODES).map((n) => n.id));
        nodes = nodes.filter((n) => n.kind !== kind || keepIds.has(n.id));
        capped = true;
      }
    }
    const keep = new Set(nodes.map((n) => n.id));
    const edges = data.edges.filter((e) => keep.has(e.from_id) && keep.has(e.to_id));
    if (!nodes.length) {
      bodyEl.innerHTML = `<div class="vcs-empty"><svg><use href="#i-brain"/></svg>${escapeHtml(t('mem.graphEmpty'))}</div>`;
      return;
    }
    const notes = [];
    if (data.truncated) notes.push(t('mem.graphTruncated', { n: data.memory_count }));
    if (capped) notes.push(t('mem.graphTruncatedNodes', { n: nodes.length }));
    const W = MEM_GRAPH_W, H = MEM_GRAPH_H;
    const sig = nodes.map((n) => n.id).join('|') + '~' + edges.map((e) => `${e.from_id}>${e.to_id}`).join(',');
    if (state.mem.graphSig !== sig) {
      const hasMems = nodes.some((n) => n.kind === 'memory');
      const hasEnts = nodes.some((n) => n.kind === 'entity');
      const widths = {};
      for (const n of nodes) {
        widths[n.id] = n.kind === 'memory'
          ? Math.min(240, 60 + memNodeLabel(n).length * 7)
          : Math.min(150, 26 + (n.label || '').length * 6.4);
      }
      let pos;
      if (hasMems && hasEnts) {
        pos = layeredLayout(nodes, edges, W, H);
      } else {
        const deg = {};
        for (const e of edges) {
          deg[e.from_id] = (deg[e.from_id] || 0) + 1;
          deg[e.to_id] = (deg[e.to_id] || 0) + 1;
        }
        const rad = {};
        for (const n of nodes) rad[n.id] = (widths[n.id] || 160) / 2 + 14;
        pos = graphLayout(nodes, edges, W, H, rad);
      }
      state.mem.graphSig = sig;
      state.mem.graphPos = pos;
      state.mem.graphW = widths;
      state.mem.graphView = { vx: 0, vy: 0, vw: W, vh: H };
    }
    bodyEl.innerHTML = graphSvgHtml(nodes, edges, state.mem.graphPos, state.mem.graphW, state.mem.graphView, 10, 'gs', W)
      + notes.map((note) => `<p class="mem-graph-note">${escapeHtml(note)}</p>`).join('')
      + `<p class="mem-graph-note">${escapeHtml(t('mem.graphHint'))}</p>`;
    wireGraphInteractivity(bodyEl);
  }

  function wireGraphInteractivity(bodyEl) {
    const svg = bodyEl.querySelector('.mem-graph');
    if (!svg) return;
    const W = MEM_GRAPH_W;
    let drag = null;
    const setViewBox = () => {
      const v = state.mem.graphView;
      svg.setAttribute('viewBox', `${v.vx.toFixed(1)} ${v.vy.toFixed(1)} ${v.vw.toFixed(1)} ${v.vh.toFixed(1)}`);
    };
    svg.addEventListener('pointerdown', (e) => {
      const nodeEl = e.target.closest('.mem-gnode');
      const v = state.mem.graphView;
      if (nodeEl) drag = { type: 'node', id: nodeEl.dataset.memNode, el: nodeEl, moved: 0 };
      else drag = { type: 'pan', sx: e.clientX, sy: e.clientY, vx: v.vx, vy: v.vy, moved: 0 };
      state.mem.dragMoved = false;
    });
    svg.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const rect = svg.getBoundingClientRect();
      const v = state.mem.graphView;
      const scale = v.vw / rect.width;
      if (drag.type === 'node') {
        const p = state.mem.graphPos.get(drag.id);
        if (!p) return;
        p.x = v.vx + (e.clientX - rect.left) * scale;
        p.y = v.vy + (e.clientY - rect.top) * scale;
        drag.moved += 1;
        drag.el.setAttribute('transform', `translate(${p.x.toFixed(1)}, ${p.y.toFixed(1)})`);
        drag.el.classList.add('dragging');
        svg.querySelectorAll('.mem-edge').forEach((edgeEl) => {
          if (edgeEl.dataset.from !== drag.id && edgeEl.dataset.to !== drag.id) return;
          edgeEl.setAttribute('d', edgePathD(
            state.mem.graphPos.get(edgeEl.dataset.from),
            state.mem.graphPos.get(edgeEl.dataset.to),
            state.mem.graphW[edgeEl.dataset.from] || 160,
            state.mem.graphW[edgeEl.dataset.to] || 160
          ));
        });
      } else {
        drag.moved += 1;
        v.vx = drag.vx - (e.clientX - drag.sx) * scale;
        v.vy = drag.vy - (e.clientY - drag.sy) * scale;
        setViewBox();
      }
    });
    const endDrag = () => {
      if (!drag) return;
      if (drag.moved > 2) state.mem.dragMoved = true;
      if (drag.el) drag.el.classList.remove('dragging');
      drag = null;
    };
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointerleave', endDrag);
    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      if (!rect.width) return;
      const v = state.mem.graphView;
      const f = e.deltaY < 0 ? 0.9 : 1.1;
      const nvw = Math.min(W * 4, Math.max(W * 0.3, v.vw * f));
      const ratio = nvw / v.vw;
      const wx = v.vx + (e.clientX - rect.left) / rect.width * v.vw;
      const wy = v.vy + (e.clientY - rect.top) / rect.height * v.vh;
      v.vx = wx - (wx - v.vx) * ratio;
      v.vy = wy - (wy - v.vy) * ratio;
      v.vw = nvw;
      v.vh = v.vh * ratio;
      setViewBox();
    }, { passive: false });
  }

  function applyGraphZoom(kind) {
    const v = state.mem.graphView;
    const svg = $('#memGraphSideBody .mem-graph');
    if (!v || !svg) return;
    const W = MEM_GRAPH_W, H = MEM_GRAPH_H;
    if (kind === 'fit') {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const p of state.mem.graphPos.values()) {
        minX = Math.min(minX, p.x);
        minY = Math.min(minY, p.y);
        maxX = Math.max(maxX, p.x);
        maxY = Math.max(maxY, p.y);
      }
      if (minX === Infinity) {
        v.vx = 0; v.vy = 0; v.vw = W; v.vh = H;
      } else {
        const pad = 90;
        let vw = Math.max(maxX - minX + pad * 2, 300);
        let vh = Math.max(maxY - minY + pad * 2, 160);
        if (vw / vh < W / H) vw = vh * (W / H);
        else vh = vw * (H / W);
        v.vx = (minX + maxX) / 2 - vw / 2;
        v.vy = (minY + maxY) / 2 - vh / 2;
        v.vw = vw;
        v.vh = vh;
      }
    } else {
      const f = kind === 'in' ? 0.85 : 1.18;
      const nvw = Math.min(W * 4, Math.max(W * 0.3, v.vw * f));
      const ratio = nvw / v.vw;
      const cx = v.vx + v.vw / 2;
      const cy = v.vy + v.vh / 2;
      v.vw = nvw;
      v.vh = v.vh * ratio;
      v.vx = cx - v.vw / 2;
      v.vy = cy - v.vh / 2;
    }
    svg.setAttribute('viewBox', `${v.vx.toFixed(1)} ${v.vy.toFixed(1)} ${v.vw.toFixed(1)} ${v.vh.toFixed(1)}`);
  }

  function renderSideStats() {
    const el = $('#memStatsPanel');
    const s = state.mem && state.mem.stats;
    if (!el || !s) return;
    const kinds = ['fact', 'decision', 'preference', 'experience'];
    const maxKind = Math.max(1, ...kinds.map((k) => s.kind_counts[k] || 0));
    const bars = kinds.map((k) => {
      const n = s.kind_counts[k] || 0;
      const color = MEM_KIND_COLORS[k] || 'var(--text-dim)';
      return `<div class="mem-kind-bar-row">
        <span class="mem-kind-bar-name" style="color:${color}">${escapeHtml(k)}</span>
        <span class="mem-kind-bar"><i style="width:${Math.round((n / maxKind) * 100)}%;background:${color}"></i></span>
        <span class="mem-stat-num">${n}</span>
      </div>`;
    }).join('');
    const sources = Object.entries(s.source_counts || {}).map(([src, n]) => `<span class="mem-stat-item">${escapeHtml(src === 'auto' ? t('mem.auto') : t('mem.manual'))} <span class="mem-stat-num">${n}</span></span>`).join('');
    const tags = (s.top_tags || []).slice(0, 10).map((x) => `<span class="mem-stat-item">${escapeHtml(x.tag)} <span class="mem-stat-num">${x.count}</span></span>`).join('');
    const ents = (s.top_entities || []).slice(0, 10).map((x) => `<span class="mem-stat-item mono">${escapeHtml(truncate(x.label, 24))} <span class="mem-stat-num">${x.count}</span></span>`).join('');
    const timeline = (s.timeline || []).slice(-30);
    const maxDay = Math.max(1, ...timeline.map((d) => Number(d.count) || 0));
    const timelineBars = timeline.length
      ? timeline.map((d) => `<span class="mem-timeline-bar" title="${escAttr(`${d.date} · ${d.count}`)}" style="height:${Math.max(8, Math.round((Number(d.count) / maxDay) * 100))}%"></span>`).join('')
      : '<span class="mem-timeline-empty">—</span>';
    el.innerHTML = `
      <section class="mem-side-card">
        <div class="mem-side-head"><svg><use href="#i-brain"/></svg><span>${escapeHtml(t('mem.sideOverview'))}</span></div>
        <div class="mem-side-kpis">
          <span><b>${num(s.total || 0)}</b><small>${escapeHtml(t('mem.statTotal'))}</small></span>
          <span><b>${num(s.linked_memory_count || 0)}</b><small>${escapeHtml(t('mem.statLinked'))}</small></span>
          <span><b>${num(s.orphan_memory_count || 0)}</b><small>${escapeHtml(t('mem.statsOrphan'))}</small></span>
          <span><b>${num(s.archived_count || 0)}</b><small>${escapeHtml(t('mem.archivedCount'))}</small></span>
        </div>
        <div class="mem-kind-bars">${bars}</div>
        ${sources ? `<div class="mem-stat-list" style="margin-top:8px">${sources}</div>` : ''}
      </section>
      <section class="mem-side-card">
        <div class="mem-side-head"><svg><use href="#i-clock"/></svg><span>${escapeHtml(t('mem.timeline'))}</span></div>
        <div class="mem-timeline">${timelineBars}</div>
      </section>
      ${tags ? `<section class="mem-side-card"><div class="mem-side-head"><svg><use href="#i-list"/></svg><span>${escapeHtml(t('mem.topTags'))}</span></div><div class="mem-stat-list">${tags}</div></section>` : ''}
      ${ents ? `<section class="mem-side-card"><div class="mem-side-head"><svg><use href="#i-code"/></svg><span>${escapeHtml(t('mem.topEntities'))}</span></div><div class="mem-stat-list">${ents}</div></section>` : ''}`;
  }

  function memGraphInfoHtml(n) {
    if (n.kind === 'memory') {
      return `<div class="mem-info-kind" style="color:${MEM_KIND_COLORS[n.memory_type] || 'var(--text-dim)'}">${escapeHtml(n.memory_type)} · ${escapeHtml(n.source === 'auto' ? t('mem.auto') : t('mem.manual'))}</div>
        <div class="mem-info-text">${escapeHtml(n.snippet || n.label)}</div>
        <div class="mem-info-meta mono">${escapeHtml(n.created_at)} · ${Math.round((n.confidence || 0) * 100)}%</div>`;
    }
    const syms = (n.symbols || []).slice(0, 10).join(', ');
    return `<div class="mem-info-kind">entity</div>
      <div class="mem-info-text mono">${escapeHtml(n.label)}</div>
      ${syms ? `<div class="mem-info-meta">${escapeHtml(syms)}</div>` : ''}`;
  }

  // 右侧 dock 里与工作区绑定的面板
  const WORKSPACE_DOCK_TABS = ['workspace', 'viewer', 'vcs', 'mem', 'codegraph'];

  // switchWorkspace 自己会在结尾按 prevActive 恢复面板；置真时 applyWorkspace
  // 不再重复调度一次恢复（否则同一个面板会被加载两遍）。
  let wsSwitching = false;

  function resetWorkspaceDock() {
    state.ws = { open: false, path: '.', entries: [], loading: false };
    state.viewer = {
      open: false, path: '', data: null, downloadUrl: '', mode: 'code',
      wrap: !!state.ui.viewerWrap, editor: null,
    };
    state.vcs = null;
    state.mem = null;
    const open = dockOpenTabs().filter((name) => !WORKSPACE_DOCK_TABS.includes(name));
    state.ui.openTabs = open;
    if (WORKSPACE_DOCK_TABS.includes(state.ui.active)) {
      // 只换当前标签页，不顺手把 dock 收成 rail —— 收起只由用户点收缩按钮触发。
      state.ui.active = open[open.length - 1] || null;
    }
    saveUi();
    syncDock();
  }

  async function restoreWorkspaceDock(prevActive) {
    const wsId = workspaceId();
    if (!wsId) return;
    // 纯聊天工作区没有工作目录：切过去时不能把旧工作区的文件 / VCS / 记忆面板
    // 重新拉起来（否则右侧显示的仍是上一个工作区的内容）。
    if (isChatOnlyWorkspace() && WORKSPACE_DOCK_TABS.includes(prevActive)) return;
    if (prevActive === 'workspace') {
      openDrawer();
    } else if (prevActive === 'vcs') {
      await openVcsDrawer(wsId);
    } else if (prevActive === 'mem') {
      await openMemDrawer(wsId);
    } else if (prevActive === 'codegraph') {
      await openCodegraphPanel(wsId);
    }
    // viewer 不恢复：旧工作区的文件路径在新工作区不存在（按需重新打开）
  }

  async function switchWorkspace(id) {
    if (!id) return;
    let ws = state.workspaces.find((w) => w.id === id);
    if (!ws) {
      try {
        ws = await api(`/workspaces/${encodeURIComponent(id)}`);
      } catch (err) {
        toast(err.message, 'bad', 5000);
        return;
      }
    }
    if (ws.archived) {
      toast(t('workspace.archive'), 'bad', 3000);
      return;
    }
    const changed = workspaceId() !== ws.id;
    const prevActive = state.ui.active;
    wsSwitching = changed;
    // 没有换工作区（点的是当前工作区）时不再关闭任何面板：
    // 右侧面板只由用户点标签 / 收缩按钮关闭。
    applyWorkspace(ws);
    if (changed) {
      state.filter = '';
      const search = $('#searchInput');
      if (search) search.value = '';
      newChat();
      await refreshConversations();
      loadCommands();
      // 换工作区只换内容：同一个面板按新工作区重新加载，不收起 dock。
      try { await restoreWorkspaceDock(prevActive); } catch (err) { console.warn('restore panel failed', err); }
    }
    wsSwitching = false;
    if (window.innerWidth <= 900) {
      $('#app').classList.remove('sidebar-open');
      $('#mobileScrim').hidden = true;
    }
  }

  // ── 设置 → 工具：工具包卡片 ──────────────────────────────────────────
  const PACK_ICONS = {
    fs: 'folder', net: 'globe', memory: 'brain',
    plan: 'list', dev: 'code', skill: 'book', mcp: 'plug',
  };

  function renderToolsCatalog() {
    const grid = $('#toolPackGrid');
    const alone = $('#toolStandaloneGrid');
    if (!grid || !alone) return;
    const cat = (state.app && state.app.tool_catalog) || {};
    const packs = cat.packs || [];
    const chatOnly = isChatOnlyWorkspace();
    const usable = (entry) => !(chatOnly && entry && entry.workspace_required);
    const count = $('#tabCount-tools');
    if (count) count.textContent = String(packs.length);
    const note = $('#toolChatOnlyNote');
    if (note) note.hidden = !chatOnly;

    grid.innerHTML = packs.map((pack) => {
      const packOn = (pack.ops || []).some(usable);
      const ops = (pack.ops || []).map((op) => {
        const off = !usable(op);
        const cond = op.conditional
          ? `<span class="tool-cond" title="${escAttr(t('tools.needTeam'))}">${escapeHtml(t('tools.condTeam'))}</span>`
          : '';
        return `<div class="tool-op${off ? ' is-off' : ''}" title="${escAttr(op.tool + (op.conditional ? ` · ${t('tools.needTeam')}` : ''))}">
          <code class="tool-op-key">${escapeHtml(op.op)}</code>
          <span class="tool-op-label">${escapeHtml(toolLabel(op.tool))}</span>
          ${cond}
          <code class="tool-op-code">${escapeHtml(op.tool)}</code>
          ${op.risk && op.risk !== 'low' ? `<span class="tool-risk risk-${escAttr(op.risk)}"></span>` : ''}
        </div>`;
      }).join('');
      return `<article class="tool-pack${packOn ? '' : ' is-off'}">
        <div class="tool-pack-head">
          <span class="tool-pack-icon"><svg><use href="#i-${PACK_ICONS[pack.code] || 'terminal'}"/></svg></span>
          <span class="tool-pack-title"><strong>${escapeHtml(t('tools.pack.' + pack.code))}</strong><code>${escapeHtml(pack.code)}</code></span>
          <span class="tool-pack-count">${(pack.ops || []).length}</span>
        </div>
        <div class="tool-op-list">${ops}</div>
        ${packOn ? '' : `<div class="tool-pack-off">${escapeHtml(t('tools.needWorkspace'))}</div>`}
      </article>`;
    }).join('');

    alone.innerHTML = (cat.standalone || []).map((tool) => {
      const off = !usable(tool);
      const hint = off ? ` · ${t('tools.needWorkspace')}` : '';
      return `<div class="tool-alone${off ? ' is-off' : ''}" title="${escAttr(tool.code + hint)}">
        <svg><use href="#i-${toolIcon(tool.code)}"/></svg>
        <span class="tool-alone-label">${escapeHtml(toolLabel(tool.code))}</span>
        <code>${escapeHtml(tool.code)}</code>
      </div>`;
    }).join('');
  }

  function renderSettingsWorkspaces() {
    const activeList = $('#settingsWorkspaceList');
    const archivedList = $('#archivedWorkspaceList');
    if (!activeList) return;
    const active = state.workspacesAll.filter(
      (w) => !w.archived && !w.chat_only);
    const archived = state.workspacesAll.filter(
      (w) => w.archived && !w.chat_only);
    setTabCount('workspaces', active.length + archived.length);
    const row = (ws) => {
      const current = ws.id === workspaceId();
      const badges = [
        ws.is_default ? `<span class="workspace-admin-badge">${escapeHtml(t('workspace.default'))}</span>` : '',
        current ? `<span class="workspace-admin-badge current">${escapeHtml(t('workspace.current'))}</span>` : '',
      ].join('');
      return `<div class="workspace-admin-row${current ? ' current' : ''}" data-ws-admin="${escAttr(ws.id)}">
        <div class="workspace-admin-main">
          <div class="workspace-admin-title"><strong>${escapeHtml(ws.name)}</strong>${badges}</div>
          <div class="workspace-admin-path mono" title="${escAttr(ws.chat_only ? t('workspace.chatOnlyNote') : (ws.root_path || ''))}">${escapeHtml(ws.chat_only ? t('workspace.chatOnly') : (ws.root_path || ''))}</div>
          ${!ws.chat_only && ws.root_exists === false ? `<div class="workspace-admin-missing">${escapeHtml(t('workspace.rootMissing'))}</div>` : ''}
        </div>
        <div class="workspace-admin-side">
          <span class="workspace-admin-count">${escapeHtml(t('workspace.conversations', { n: ws.conversation_count || 0 }))}</span>
          <div class="workspace-admin-actions">
            <button type="button" class="icon-btn" data-ws-rename="${escAttr(ws.id)}" title="${escAttr(t('workspace.rename'))}" aria-label="${escAttr(t('workspace.rename'))}"><svg><use href="#i-edit"/></svg></button>
            ${ws.archived
              ? `<button type="button" class="icon-btn" data-ws-restore="${escAttr(ws.id)}" title="${escAttr(t('workspace.restore'))}" aria-label="${escAttr(t('workspace.restore'))}"><svg><use href="#i-refresh"/></svg></button>`
              : (ws.is_default ? '' : `<button type="button" class="icon-btn danger-hover" data-ws-delete="${escAttr(ws.id)}" title="${escAttr(t('workspace.delete'))}" aria-label="${escAttr(t('workspace.delete'))}"><svg><use href="#i-trash"/></svg></button>`)}
          </div>
        </div>
      </div>`;
    };
    activeList.innerHTML = active.length
      ? active.map(row).join('')
      : `<div class="model-empty">${escapeHtml(t('workspace.folderEmpty'))}</div>`;
    if (archivedList) archivedList.innerHTML = archived.map(row).join('');
    if ($('#archivedWorkspaceBox')) $('#archivedWorkspaceBox').hidden = !archived.length;
  }

  async function loadSettingsWorkspaces() {
    const list = $('#settingsWorkspaceList');
    if (list) list.innerHTML = `<div class="model-loading"><span class="spinner"></span> ${escapeHtml(t('workspace.loading'))}</div>`;
    await loadWorkspaces();
    renderSettingsWorkspaces();
  }

  async function workspaceAction(id, action) {
    const ws = state.workspacesAll.find((w) => w.id === id)
      || state.workspaces.find((w) => w.id === id);
    if (!ws) return;
    try {
      if (action === 'rename') {
        const name = window.prompt(t('workspace.renamePrompt'), ws.name);
        if (name === null) return;
        const trimmed = name.trim();
        if (!trimmed || trimmed === ws.name) return;
        await api(`/workspaces/${encodeURIComponent(id)}`, {
          method: 'PATCH', body: JSON.stringify({ name: trimmed }),
        });
        await loadWorkspaces();
        renderSettingsWorkspaces();
        toast(t('workspace.renamed'), 'ok', 2200);
        return;
      }
      if (action === 'archive') {
        if (!window.confirm(t('workspace.archiveConfirm', { name: ws.name }))) return;
        await api(`/workspaces/${encodeURIComponent(id)}`, {
          method: 'PATCH', body: JSON.stringify({ archived: true }),
        });
        await loadWorkspaces();
        if (workspaceId() === id) {
          await switchWorkspace('default');
        }
        renderSettingsWorkspaces();
        toast(t('workspace.archivedToast'), 'ok', 2400);
        return;
      }
      if (action === 'delete') {
        if (!window.confirm(t('workspace.deleteConfirm', { name: ws.name }))) return;
        await api(`/workspaces/${encodeURIComponent(id)}`, { method: 'DELETE' });
        await loadWorkspaces();
        if (workspaceId() === id) {
          await switchWorkspace('default');
        }
        renderSettingsWorkspaces();
        toast(t('workspace.deletedToast'), 'ok', 2600);
        return;
      }
      if (action === 'restore') {
        await api(`/workspaces/${encodeURIComponent(id)}`, {
          method: 'PATCH', body: JSON.stringify({ archived: false }),
        });
        await loadWorkspaces();
        renderSettingsWorkspaces();
        toast(t('workspace.restored'), 'ok', 2200);
      }
    } catch (err) {
      const key = action === 'rename' ? 'workspace.renameFailed'
        : action === 'archive' ? 'workspace.archiveFailed'
          : action === 'restore' ? 'workspace.restoreFailed' : 'workspace.addFailed';
      toast(t(key, { msg: err.message }), 'bad', 6000);
    }
  }

  /* ── add workspace / folder picker ───────────────────────────────────── */

  function syncNativePicker() {
    const btn = $('#folderPickerNative');
    if (!btn) return;
    const api = window.pywebview && window.pywebview.api;
    btn.hidden = !(api && typeof api.pick_folder === 'function');
  }

  async function openAddWorkspace() {
    closeModelMenu();
    const name = $('#addWorkspaceName');
    const note = $('#addWorkspaceNote');
    if (name) name.value = '';
    if (note) { note.className = 'save-note'; note.textContent = ''; }
    state.addWs.nameTouched = false;
    const cw = currentWorkspace();
    const managedRoot = cw && cw.managed ? (cw.root_path || '') : '';
    let start = localStorage.getItem('az-addws-path') || '';
    if (!start || start === managedRoot) {
      start = cw && !cw.managed ? (cw.root_path || '') : '';
    }
    $('#addWorkspaceModal').hidden = false;
    syncNativePicker();
    await loadFolderPicker(start, { fallbackHome: true });
    setTimeout(() => { if (name) name.focus(); }, 40);
  }

  function renderFolderPicker(items) {
    const list = $('#folderPickerList');
    if (!list) return;
    if (!items.length) {
      list.innerHTML = `<div class="folder-empty">${escapeHtml(t('workspace.folderEmpty'))}</div>`;
      return;
    }
    list.innerHTML = items.map((item) => `<button type="button" class="folder-row${item.hidden ? ' hidden-folder' : ''}" data-folder-path="${escAttr(item.path)}">
      <svg><use href="#i-folder"/></svg>
      <span>${escapeHtml(item.name)}</span>
      <svg class="folder-open-icon"><use href="#i-chevron-right"/></svg>
    </button>`).join('');
  }

  async function loadFolderPicker(path, { fallbackHome = false } = {}) {
    const list = $('#folderPickerList');
    const note = $('#folderPickerNote');
    const input = $('#folderPickerPath');
    if (!list) return;
    state.addWs.loading = true;
    list.innerHTML = `<div class="folder-empty"><span class="spinner"></span> ${escapeHtml(t('workspace.folderLoading'))}</div>`;
    try {
      const query = path ? `?path=${encodeURIComponent(path)}` : '';
      const data = await api(`/workspaces/browse${query}`);
      state.addWs.path = data.path;
      state.addWs.parent = data.parent;
      if (input) input.value = data.path;
      localStorage.setItem('az-addws-path', data.path);
      if (!state.addWs.nameTouched) {
        const name = $('#addWorkspaceName');
        if (name) name.value = data.name || '';
      }
      renderFolderPicker(data.items || []);
      if (note) {
        note.className = 'field-note';
        note.textContent = t('workspace.folderCount', { n: (data.items || []).length });
      }
      const up = $('#folderPickerUp');
      if (up) up.disabled = !data.parent;
    } catch (err) {
      if (fallbackHome && path) {
        // 记住的旧路径已不可用：回到主目录，避免弹框一打开就是错误态。
        localStorage.removeItem('az-addws-path');
        if (input) input.value = '';
        await loadFolderPicker('');
        return;
      }
      if (note) { note.className = 'save-note bad'; note.textContent = err.message; }
      list.innerHTML = `<div class="folder-empty">${escapeHtml(t('workspace.folderFailed'))}</div>`;
    } finally {
      state.addWs.loading = false;
    }
  }

  async function createWorkspaceFromFolder() {
    const btn = $('#createWorkspaceBtn');
    const note = $('#addWorkspaceNote');
    const nameInput = $('#addWorkspaceName');
    const name = (nameInput && nameInput.value.trim()) || '';
    if (!state.addWs.path) return;
    btn.disabled = true;
    if (note) { note.className = 'save-note'; note.textContent = t('settings.saving'); }
    try {
      const ws = await api('/workspaces', {
        method: 'POST',
        body: JSON.stringify({ name, path: state.addWs.path }),
      });
      $('#addWorkspaceModal').hidden = true;
      await loadWorkspaces();
      await switchWorkspace(ws.id);
      if ($('#settingsModal') && !$('#settingsModal').hidden) {
      renderSettingsWorkspaces();
      renderToolsCatalog();
    }
      toast(t(ws.restored ? 'workspace.restoredOnAdd' : 'workspace.added',
        { name: ws.name }), 'ok', 2600);
    } catch (err) {
      if (note) { note.className = 'save-note bad'; note.textContent = err.message; }
      toast(t('workspace.addFailed', { msg: err.message }), 'bad', 6000);
    } finally {
      btn.disabled = false;
    }
  }

  /* ── conversation picker (paginated) ─────────────────────────────────── */

  async function openConvPicker(page = 1, wsId = '') {
    state.picker.open = true;
    state.picker.page = Math.max(1, Number(page) || 1);
    state.picker.wsId = wsId || workspaceId();
    $('#convPickerModal').hidden = false;
    await loadConvPicker(state.picker.page);
  }

  async function loadConvPicker(page) {
    const list = $('#convPickerList');
    if (list) list.innerHTML = `<div class="model-loading"><span class="spinner"></span> ${escapeHtml(t('workspace.loading'))}</div>`;
    const keyword = state.filter.trim();
    const offset = (Math.max(1, page) - 1) * state.picker.pageSize;
    try {
      const data = await api(`/conversations?workspace_id=${encodeURIComponent(state.picker.wsId || workspaceId())}`
        + `&limit=${state.picker.pageSize}&offset=${offset}`
        + (keyword ? `&keyword=${encodeURIComponent(keyword)}` : '')
        + (state.picker.archived ? '&include_archived=true' : ''));
      state.picker.total = Number(data.total) || 0;
      state.picker.items = data.items || [];
      const pages = Math.max(1, Math.ceil(state.picker.total / state.picker.pageSize));
      state.picker.page = Math.min(Math.max(1, page), pages);
      renderConvPicker();
    } catch (err) {
      if (list) list.innerHTML = `<div class="model-empty">${escapeHtml(err.message)}</div>`;
    }
  }

  function renderConvPicker() {
    const list = $('#convPickerList');
    if (!list) return;
    const pickerWsId = state.picker.wsId || workspaceId();
    const ws = state.workspacesAll.find((w) => w.id === pickerWsId)
      || state.workspaces.find((w) => w.id === pickerWsId)
      || currentWorkspace();
    const pages = Math.max(1, Math.ceil(state.picker.total / state.picker.pageSize));
    const sub = $('#convPickerSubtitle');
    if (sub) {
      sub.textContent = t('conv.pickerSubtitle', {
        name: ws ? ws.name : '',
        n: state.picker.total,
      }) + (state.picker.archived ? ' · ' + t('conv.showArchived') : '');
    }
    const archivedBtn = $('#convPickerArchived');
    if (archivedBtn) {
      archivedBtn.setAttribute('aria-pressed', String(state.picker.archived));
      archivedBtn.textContent = state.picker.archived ? t('conv.hideArchived') : t('conv.showArchived');
    }
    if (!state.picker.items.length) {
      list.innerHTML = `<div class="conv-picker-empty">${escapeHtml(t('conv.none'))}</div>`;
    } else {
      list.innerHTML = state.picker.items.map((conv) => {
        const active = conv.id === state.currentId;
        const meta = [relativeTime(conv.updated_at || conv.created_at),
          conv.message_count ? t('conv.messageCount', { n: conv.message_count }) : ''].filter(Boolean).join(' · ');
        return `<div class="conv-picker-row${active ? ' active' : ''}${conv.archived ? ' archived' : ''}" data-conv-row="${escAttr(conv.id)}">
          <button type="button" class="conv-picker-open" data-picker-conv="${escAttr(conv.id)}">
            <span class="conv-picker-main">
              <span class="conv-picker-title">${escapeHtml(truncate(conversationTitle(conv), 90))}</span>
              <span class="conv-picker-preview">${escapeHtml(truncate(conv.last_message || '', 130))}</span>
            </span>
            <span class="conv-picker-meta">${escapeHtml(meta)}${conv.archived ? ' · ' + escapeHtml(t('conv.archivedBadge')) : ''}</span>
          </button>
          <button type="button" class="conv-picker-del" data-conv-del="${escAttr(conv.id)}" title="${escAttr(t('chat.delete'))}" aria-label="${escAttr(t('chat.delete'))}">
            <svg><use href="#i-trash"/></svg>
          </button>
        </div>`;
      }).join('');
    }
    const info = $('#convPickerInfo');
    if (info) info.textContent = t('conv.page', { page: state.picker.page, pages });
    const prev = $('#convPickerPrev');
    const next = $('#convPickerNext');
    if (prev) prev.disabled = state.picker.page <= 1;
    if (next) next.disabled = state.picker.page >= pages;
  }

  /* ── sidebar / conversation list ─────────────────────────────────────── */

  function conversationTitle(conv) {
    return conv.title || conv.last_message || t('chat.untitled');
  }

  // 工作区展开后只显示最近会话 + 「更多会话」，没有第二层详细列表。
  function renderConversations() {
    const preview = $('#wsGroupPreview');
    if (!preview) return;
    const items = state.conversations;
    preview.innerHTML = wsPreviewHtml(
      items.slice(0, CONV_PREVIEW),
      Math.max(state.convTotal || 0, items.length),
      workspaceId(),
      state.filter.trim(),
    );
  }

  async function refreshConversations() {
    const token = ++state.convToken;
    const wid = workspaceId();
    const keyword = state.filter.trim();
    try {
      const data = await api(`/conversations?workspace_id=${encodeURIComponent(wid)}`
        + `&limit=${state.convLimit}&offset=0`
        + (keyword ? `&keyword=${encodeURIComponent(keyword)}` : ''));
      if (token !== state.convToken || wid !== workspaceId()) return;
      state.conversations = (data && data.items) || [];
      state.convTotal = Number(data && data.total) || 0;
      const ws = currentWorkspace();
      if (ws) {
        ws.conversation_count = state.convTotal;
        const all = state.workspacesAll.find((w) => w.id === ws.id);
        if (all) all.conversation_count = state.convTotal;
        const menuWs = state.wsMenuItems.find((w) => w.id === ws.id);
        if (menuWs) {
          menuWs.conversation_count = state.convTotal;
          menuWs.recent_conversations = state.conversations.slice(0, CONV_PREVIEW);
        }
      }
      renderConversations();
    } catch (err) {
      console.warn('conversation list failed', err);
    }
  }

  /* ── active runs (visible across workspaces) ───────────────────────────── */

  function elapsedShort(ts) {
    if (!ts) return '';
    const t0 = new Date(String(ts).replace(' ', 'T'));
    if (Number.isNaN(t0.getTime())) return '';
    const s = Math.max(0, Math.floor((Date.now() - t0.getTime()) / 1000));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h`;
  }

  // 进程面板的 elapsed_s 是**秒数**，不是时间戳；不能走 elapsedShort。
  function durationShort(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }

  async function refreshActiveRuns() {
    try {
      const data = await api('/runs/active');
      state.activeRuns = Array.isArray(data) ? data : [];
    } catch (err) {
      console.warn('active runs lookup failed', err);
      state.activeRuns = [];
    }
    renderActiveRuns();
    // 进程面板随活动 run 一起刷新：切会话 / run 起停都能立刻看到当前会话的进程，
    // 且无需再单独挂一个 5s 定时器（它自己 2.5s 轮一次运行中的进程）。
    if (typeof refreshProcesses === 'function') refreshProcesses();
    renderChatList();
    // renderWorkspaceGroups() 会重建工作区分组，活动工作区的会话预览是空容器
    // （等着 renderConversations() 填）——不补这一步，每 5s 轮询就会闪一下空白。
    renderWorkspaceGroups();
    renderConversations();
  }

  // 「运行中的任务」只列真正在跑的 run：排队中（queued）的不进这个面板。
  // 排队信息归队列条（#queueStrip）——任务还没开始跑，就不该占「运行中」的位置。
  // 注意 state.activeRuns 本身不过滤：会话分组 / 工作区活点还要用全量。
  function activeRunningRuns() {
    return (state.activeRuns || []).filter((r) => r.status === 'running');
  }

  function renderActiveRuns() {
    const box = $('#activeRuns');
    const list = $('#activeRunsList');
    if (!box || !list) return;
    const runs = activeRunningRuns();
    if (!runs.length) {
      box.hidden = true;
      list.innerHTML = '';
      return;
    }
    box.hidden = false;
    const count = $('#activeRunsCount');
    if (count) count.textContent = String(runs.length);
    list.innerHTML = runs.map((r) => {
      const question = r.pending_question || null;
      const approval = r.pending_approval || null;
      const pending = question || approval;
      const isApproval = !question && !!approval;
      const statusLabel = pending
        ? (isApproval ? t('activeRuns.waitingApproval') : t('activeRuns.waiting'))
        : t('activeRuns.running');
      const elapsed = elapsedShort(r.started_at || r.created_at);
      const wsName = r.workspace_name || r.workspace_id || '';
      const actionAttr = isApproval
        ? `data-run-approval="${escAttr(r.conversation_id)}" data-approval-id="${escAttr(approval.id || '')}"`
        : `data-run-ask="${escAttr(r.conversation_id)}" data-ask-id="${escAttr(question && question.id || '')}"`;
      const askBtn = pending
        ? `<button class="active-run-ask${isApproval ? ' is-approval' : ''}" type="button" ${actionAttr}
             title="${escAttr(isApproval ? t('activeRuns.approveTitle') : t('activeRuns.answerTitle'))}">
             <span class="active-run-ask-dot"></span>${escapeHtml(isApproval ? t('activeRuns.approve') : t('activeRuns.answer'))}
           </button>`
        : '';
      return `<div class="active-run-row">
        <span class="active-run-dot${pending ? ' ask' : ' on'}"></span>
        <button class="active-run-main" data-run-conv="${escAttr(r.conversation_id)}"
          title="${escAttr(r.title || t('chat.untitled'))}">
          <span class="active-run-title">${escapeHtml(truncate(r.title || t('chat.untitled'), 60))}</span>
          <span class="active-run-meta">${escapeHtml(wsName)} · ${escapeHtml(statusLabel)}${elapsed ? ' · ' + escapeHtml(elapsed) : ''}</span>
        </button>
        ${askBtn}
        <button class="active-run-stop" data-run-stop="${escAttr(r.id)}" title="${escAttr(t('activeRuns.stopTitle'))}">
          <svg><use href="#i-stop"/></svg>
        </button>
      </div>`;
    }).join('');
  }

  /* ── 进程面板（前台工具 / 后台工具 / 子代理 / 运行中的对话）─────────
     目标：模型不再「干等工具」，用户可以看见并单独停掉某个进程，
     而**不影响当前会话**（kill 只作用于那个 job；停 run 才是停整轮）。

     - 没有进程 → 整块 hidden（自动隐藏），跑完自然消失；
     - 面板收缩只由用户点 #processSectionToggle 触发；
     - 停止按钮按 kind 走不同接口：tool/subagent → /processes/{id}/stop，
       run → /runs/{id}/stop。
  */

  const PROCESS_KIND_ICON = { tool: 'terminal', subagent: 'agent', run: 'message' };

  function processLabel(item) {
    const name = String(item.name || '');
    if (item.kind === 'run') return t('process.run');
    if (item.kind === 'subagent') {
      const goal = item.args && (item.args.goal || item.args.name);
      return `${t('process.subagent')}${goal ? ` · ${truncate(String(goal), 40)}` : ''}`;
    }
    const cmd = item.args && (item.args.cmd || item.args.code || item.args.path);
    return cmd
      ? `${toolLabel(name)} · ${truncate(String(cmd).replace(/\s+/g, ' '), 48)}`
      : toolLabel(name);
  }

  function renderProcesses() {
    const box = $('#processPanel');
    const list = $('#processList');
    if (!box || !list) return;
    const items = (state.processes || []).filter((x) => x.status === 'running');
    if (!items.length) {
      box.hidden = true;
      list.innerHTML = '';
      syncProcessDetail();
      return;
    }
    box.hidden = false;
    const collapsed = !!state.ui.processCollapsed;
    box.classList.toggle('is-collapsed', collapsed);
    const btn = $('#processSectionToggle');
    if (btn) {
      btn.classList.toggle('is-collapsed', collapsed);
      btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
    const count = $('#processCount');
    if (count) count.textContent = String(items.length);
    syncProcessDetail();
    if (collapsed) return;
    list.innerHTML = items.map((it) => {
      const icon = PROCESS_KIND_ICON[it.kind] || 'terminal';
      const elapsed = it.elapsed_s ? durationShort(it.elapsed_s) : '';
      const kindLabel = it.kind === 'subagent' ? t('process.kindSubagent') : t('process.kindTool');
      const title = t('process.stop');
      const stopBtn = it.stoppable === false ? '' : `<button type="button" class="process-stop" data-process-stop="${escAttr(it.id)}" data-process-kind="${escAttr(it.kind)}" title="${escAttr(title)}" aria-label="${escAttr(title)}"><svg><use href="#i-stop"/></svg></button>`;
      return `<div class="process-row" data-process-id="${escAttr(it.id)}" data-process-kind="${escAttr(it.kind)}">
        <span class="process-dot"></span>
        <span class="process-icon"><svg><use href="#i-${icon}"/></svg></span>
        <button type="button" class="process-main" data-process-open="${escAttr(it.id)}" title="${escAttr(processLabel(it))}">
          <span class="process-name">${escapeHtml(processLabel(it))}</span>
          <span class="process-meta">${escapeHtml(kindLabel)}${elapsed ? ' · ' + escapeHtml(elapsed) : ''}</span>
        </button>
        ${stopBtn}
      </div>`;
    }).join('');
  }

  function openProcessDetail(id) {
    const item = (state.processes || []).find((x) => x.id === id) || null;
    if (!item) return;
    state.processDetailId = id;
    state.processDetailItem = item;
    renderProcessDetail();
    const modal = $('#processDetailModal');
    if (modal) modal.hidden = false;
  }

  function closeProcessDetail() {
    state.processDetailId = '';
    state.processDetailItem = null;
    const modal = $('#processDetailModal');
    if (modal) modal.hidden = true;
  }

  // 进程仍在刷新时保持详情同步；进程已结束则保留最后快照，说明它已结束。
  function syncProcessDetail() {
    if (!state.processDetailId) return;
    const live = (state.processes || []).find((x) => x.id === state.processDetailId);
    if (live) state.processDetailItem = live;
    renderProcessDetail();
  }

  function processDetailRow(label, value, mono) {
    if (!value) return '';
    return `<div class="process-detail-row">
      <span class="process-detail-label">${escapeHtml(label)}</span>
      <span class="process-detail-value${mono ? ' mono' : ''}">${escapeHtml(String(value))}</span>
    </div>`;
  }

  function renderProcessDetail() {
    const item = state.processDetailItem;
    const body = $('#processDetailBody');
    const sub = $('#processDetailSub');
    const note = $('#processDetailNote');
    const stopBtn = $('#processDetailStop');
    if (!body || !item) return;
    const kindLabel = item.kind === 'subagent' ? t('process.kindSubagent') : t('process.kindTool');
    const live = (state.processes || []).some((x) => x.id === item.id && x.status === 'running');
    if (sub) sub.textContent = processLabel(item);
    if (note) note.textContent = live ? t('process.running') : t('process.ended');
    if (stopBtn) {
      stopBtn.hidden = !(live && item.stoppable !== false);
      stopBtn.dataset.processStop = item.id;
      stopBtn.dataset.processKind = item.kind || 'tool';
      stopBtn.disabled = false;
    }
    const started = item.started_at
      ? new Date(item.started_at * 1000).toLocaleTimeString()
      : '';
    const elapsed = item.elapsed_s ? durationShort(item.elapsed_s) : '';
    let args = '';
    try {
      args = JSON.stringify(item.args || {}, null, 2);
    } catch (err) {
      args = String(item.args || '');
    }
    body.innerHTML = `
      ${processDetailRow(t('process.fieldKind'), kindLabel)}
      ${processDetailRow(t('process.fieldStatus'), live ? t('process.running') : t('process.ended'))}
      ${processDetailRow(t('process.fieldStarted'), started)}
      ${processDetailRow(t('process.fieldElapsed'), elapsed)}
      ${processDetailRow(t('process.fieldId'), item.id, true)}
      ${processDetailRow(t('process.fieldRun'), item.run_id, true)}
      ${processDetailRow(t('process.fieldCall'), item.call_id, true)}
      <div class="process-detail-section">
        <div class="process-detail-label">${escapeHtml(t('process.fieldArgs'))}</div>
        <pre class="process-detail-pre">${escapeHtml(args || '{}')}</pre>
      </div>
      <div class="process-detail-section">
        <div class="process-detail-label">${escapeHtml(t('process.fieldOutput'))}</div>
        <pre class="process-detail-pre">${escapeHtml(item.summary || t('process.noOutput'))}</pre>
      </div>`;
  }

  async function refreshProcesses() {
    const convId = state.currentId;
    if (!convId) {
      state.processes = [];
      renderProcesses();
      return;
    }
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/processes`);
      if (convId !== state.currentId) return;    // 期间切了会话：丢弃过期结果
      state.processes = (data && data.items) || [];
    } catch (err) {
      state.processes = [];
    }
    renderProcesses();
  }

  async function stopProcess(id, kind) {
    if (!id) return;
    const url = kind === 'run'
      ? `/runs/${encodeURIComponent(id)}/stop`
      : `/processes/${encodeURIComponent(id)}/stop?kind=${encodeURIComponent(kind || 'tool')}`;
    try {
      await api(url, { method: 'POST' });
      toast(kind === 'run' ? t('process.stoppedRun') : t('process.stopped'), 'ok', 2500);
    } catch (err) {
      toast(err.message, 'bad', 4000);
    }
    refreshProcesses();
    refreshActiveRuns();
  }

  /* ── header / composer / activity ────────────────────────────────────── */

  function renderHeader() {
    const title = chatTitle();
    const titleEl = $('#chatTitle');
    titleEl.textContent = title;
    titleEl.title = state.currentId ? t('chat.renameTitle') : '';
    document.title = state.currentId ? `${title} — ${appBrand()}` : appBrand();

    // 副标题只放工作区，单独一行，和会话标题彻底分开：
    // 不再拼接角色名（默认角色即品牌名），也不再跟标题挤在同一行。
    const ws = currentWorkspace();
    $('#chatSubtitle').textContent = ws
      ? t(isChatOnlyWorkspace(ws) ? 'header.chatWorkspace' : 'header.workspace', { name: ws.name })
      : appBrand();
    $('#deleteChatBtn').hidden = !state.currentId;
    renderWorkspaceNav();

    const running = state.running;
    $('#runState').hidden = !running;
    // 队列模式：运行中仍可继续输入（新消息排队），发送按钮保持可用
    $('#sendBtn').hidden = false;
    $('#stopBtn').hidden = !running;
    $('#promptInput').disabled = false;
    if (running) {
      $('#runStateText').textContent = (state.run && state.run.status === 'queued')
        ? t('runState.queued')
        : t('runState.working');
    }
    $('#composerBox').classList.toggle('is-running', !!running);

    const llm = (state.app && state.app.llm) || {};
    const code = activeModelCode();
    const active = activeModelProfile()
      || (llm.models || []).find((m) => m.code === code);
    const model = llm.model || t('header.notConfigured');
    const chipLabel = active
      ? (active.model || active.effective_model || active.name || active.code)
      : model;
    const chip = $('#modelChip');
    chip.dataset.baseLabel = chipLabel;
    chip.textContent = chipLabel;
    chip.title = active
      ? `${chipLabel} · ${active.effective_base_url || active.base_url || llm.base_url || '—'}`
      : (llm.model ? `${chipLabel} · ${llm.base_url || '—'}` : t('header.modelSettings'));
    chip.setAttribute('aria-label', chip.title);
    syncAttachAvailability();
    syncThinkingLevels();

    if (ws) $('#workspaceHint').textContent = t('composer.workspaceHint', { name: ws.name });

    const goalBtn = $('#goalModeBtn');
    if (goalBtn) {
      const goalOn = state.mode === 'goal';
      goalBtn.classList.toggle('active', goalOn);
      goalBtn.setAttribute('aria-pressed', String(goalOn));
    }
    applyPermMode(state.permMode);
  }

  function currentActivity() {
    if (!state.running) return '';
    if (state.run && state.run.status === 'queued') return t('activity.queued');
    const live = state.live;
    if (!live) return t('activity.starting');
    // 重试 / 等模型：优先于其它状态显示 —— 这正是用户以为「卡死」的时刻，
    // 必须告诉他是在等上游、还是在自动恢复，而不是一直转圈。
    if (live.retrying && Date.now() - live.retrying.at < 60000) {
      return t('activity.retrying', {
        a: live.retrying.attempt, m: live.retrying.max_attempts,
      });
    }
    // 等待状态**永不过期**：过去 90s 后这里会掉到「思考中」，秒数不再刷新，
    // 长任务看起来就像卡死了（2026-10-29 现场）。后端心跳一直每 20s 在发，
    // 前端必须持续显示「等了多久、在等什么」。
    if (live.waiting) {
      const ws = Number(live.waiting.elapsed_s || 0)
        || Math.round((Date.now() - (live.waiting.at || Date.now())) / 1000);
      if (live.waiting.kind === 'compact') {
        return t('activity.compacting', { s: ws });
      }
      if (live.waiting.kind === 'tool') {
        // 「上游仍在生成工具参数」与「等模型首字」是两种不同等待，
        // 过去混在一起显示，用户看不出到底卡在哪一步。
        return t('activity.toolArgs', {
          tool: toolLabel(live.waiting.name || ''), s: ws,
        });
      }
      return t(live.waiting.phase === 'mid_output'
        ? 'activity.waitingMore' : 'activity.waitingFirst',
      { s: ws });
    }
    const steps = live.trace || [];
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      const s = steps[i];
      if (s.kind === 'tool' && s.status === 'background') {
        // 已转入后台监督（前台不再等它）：说明模型可以继续干别的。
        return t('activity.background', { tool: toolLabel(s.name) });
      }
      if (s.kind === 'tool' && s.status === 'running') {
        return t('activity.calling', { tool: toolLabel(s.name) });
      }
    }
    const last = steps[steps.length - 1];
    if (last && last.kind === 'thinking') return t('activity.thinking');
    if (last && last.kind === 'compress' && last.status === 'running') return t('activity.compressing');
    if (last && last.kind === 'plan') return t('activity.planning');
    return t('activity.working');
  }

  function renderActivity() {
    const line = $('#activityLine');
    const text = currentActivity();
    line.hidden = !text;
    if (text) $('#activityText').textContent = text;
    // 单次（本轮）统计放在「处理中」同行右侧；会话总体在输入框下方。
    const statsEl = $('#activityStats');
    if (statsEl) {
      const html = (text && state.running && state.live) ? usageStatsHtml(state.live) : '';
      statsEl.hidden = !html;
      statsEl.innerHTML = html;
    }
  }

  function addUsage(total, usage) {
    const u = usage || {};
    const p = Number(u.prompt_tokens || u.input_tokens || 0);
    const c = Number(u.completion_tokens || u.output_tokens || 0);
    // last_prompt_tokens = 当前上下文占用：后到的快照**覆盖**先前的
    // （取 max 会让压缩前的旧值一直留着）；本次没带就保留原值。
    const lastPrompt = Number(u.last_prompt_tokens || 0)
      || Number(total.last_prompt_tokens || 0);
    return {
      prompt_tokens: Number(total.prompt_tokens || 0) + p,
      completion_tokens: Number(total.completion_tokens || 0) + c,
      cached_tokens: Number(total.cached_tokens || 0) + Number(u.cached_tokens || 0),
      llm_ms: Number(total.llm_ms || 0) + Number(u.llm_ms || 0),
      rounds: Number(total.rounds || 0) + Number(u.rounds || 0),
      max_context_tokens: Math.max(Number(total.max_context_tokens || 0),
        Number(u.max_context_tokens || 0)),
      last_prompt_tokens: lastPrompt,
      turns: Number(total.turns || 0) + ((p || c) ? 1 : 0),
    };
  }

  function conversationUsage() {
    // 后端 usage_total 聚合全部已落库回复（不受转录 200 条上限影响）；
    // 拿不到时退回当前已加载消息，运行中的那一轮再实时叠加。
    let base = state.convUsage;
    if (base) {
      base = { ...base };
    } else {
      base = { prompt_tokens: 0, completion_tokens: 0, cached_tokens: 0, llm_ms: 0, rounds: 0, turns: 0 };
      for (const m of state.messages || []) {
        if (m && m.by === 'assistant' && m.usage) base = addUsage(base, m.usage);
      }
    }
    if (state.running && state.live && state.live.usage) {
      base = addUsage(base, state.live.usage);
    }
    return base;
  }

  function conversationStatsHtml() {
    const total = conversationUsage();
    const p = Number(total.prompt_tokens || 0);
    const c = Number(total.completion_tokens || 0);
    if (!p && !c) return '';
    const cached = Number(total.cached_tokens || 0);
    const rounds = Number(total.rounds || 0);
    const turns = Number(total.turns || 0);
    const cacheRate = p ? Math.round((cached / p) * 1000) / 10 : 0;
    const speed = total.llm_ms ? Math.round(c / (Number(total.llm_ms) / 1000)) : 0;
    const detail = t('usage.convDetail', { turns: num(turns), rounds: num(rounds) });
    const stats = [];
    if (p) stats.push(['cpu', t('usage.convIn', { n: compact(p) }), detail]);
    if (c) stats.push(['file', t('usage.convOut', { n: compact(c) }), detail]);
    if (p) stats.push(['cache', t('usage.convCache', { p: cacheRate }),
      t('usage.cacheDetail', { c: compact(cached), p: compact(p), r: num(rounds) })]);
    if (c && speed) stats.push(['spark', t('usage.convSpeed', { n: num(speed) }),
      t('usage.convSpeedDetail')]);
    return stats.map(([icon, text, title]) =>
      `<span class="stat"${title ? ` title="${escAttr(title)}"` : ''}><svg><use href="#i-${icon}"/></svg><span>${escapeHtml(text)}</span></span>`).join('');
  }

  // 上下文百分比的分母 = 模型**可用的输入预算**（窗口 − 输出预留），不是整个窗口：
  // prompt 永远吃不满窗口，留出来的那部分是给模型输出的；用满窗口当分母，
  // 100% 是永远到不了的数字，压缩真正发生时读数也对不上。
  function contextBudget() {
    const active = activeModelProfile() || {};
    const win = Number(active.effective_context_window
      || (state.settings && state.settings.context_window) || 0);
    const out = Number(active.effective_max_tokens
      || (state.settings && state.settings.max_tokens) || 0);
    if (!win) return { window: 0, output: 0, budget: 0 };
    const reserved = Math.max(0, Math.min(out, win - 1));
    return { window: win, output: reserved, budget: Math.max(1, win - reserved) };
  }

  // ── 当前上下文占用的读数 ──────────────────────────────────────────────
  // 真实 token 只有模型返回的 prompt_tokens 说了算，而那是**每轮一次**；两次请求
  // 之间（长生成 / 一串工具）靠估算增量补齐，下一轮真实值回来整体校正。估算口径
  // 与后端 compress.count_tokens 一致：CJK 按字，其余 4 字符 ≈ 1 token。
  function ctxAcc() { return { chars: 0, cjk: 0 }; }

  function ctxAdd(acc, text) {
    const box = acc || ctxAcc();
    const s = String(text || '');
    for (let i = 0; i < s.length; i += 1) {
      const code = s.charCodeAt(i);
      if (code >= 0x4e00 && code <= 0x9fff) box.cjk += 1;
      else box.chars += 1;
    }
    return box;
  }

  function ctxAccTokens(acc) {
    if (!acc) return 0;
    return Number(acc.cjk || 0) + Math.floor(Number(acc.chars || 0) / 4);
  }

  // 当前占用 = 后端锚点（真实值）+ 本地流式增量；run 结束后沿用最后算出的值。
  function liveContextTokens() {
    const live = state.live;
    if (live && live.contextTokens) {
      return Number(live.contextTokens) + ctxAccTokens(live.ctxText);
    }
    return Number(state.ctxTail || 0);
  }

  // 按 k 显示：≥100k 取整（128k），其余一位小数（62.4k / 9.1k），不足 1k 直接给数。
  function ctxTokensLabel(n) {
    const v = Math.max(0, Number(n) || 0);
    if (v < 1000) return String(Math.round(v));
    const k = v / 1000;
    return `${k >= 100 ? Math.round(k) : k.toFixed(1)}k`;
  }

  function renderContextChip() {
    const chip = $('#contextChip');
    if (!chip) return;
    const { window: win, output, budget } = contextBudget();
    const usage = conversationUsage();
    const peak = Number(usage.max_context_tokens || 0);
    // 分子是**当前上下文占用**：运行中以后端推来的 context_tokens（真实 prompt +
    // 估算增量）为准，本地再叠上正在流式生成的那部分；空闲时用最近一轮真实
    // prompt。历史峰值只进 tooltip——它不会回落，不能当「现在用了多少」。
    const liveCtx = liveContextTokens();
    const after = Number((state.live && state.live.contextAfter) || 0);
    const used = liveCtx || after || Number(usage.last_prompt_tokens || 0) || peak;
    if (!state.currentId || !budget || !used) {
      chip.hidden = true;
      return;
    }
    const raw = (used / budget) * 100;
    // 上限 100；没真的吃满之前不显示 100%（99.6% 会被 round 成 100）。
    const pct = raw >= 99.95 ? 100 : Math.max(0, Math.min(99, Math.floor(raw)));
    chip.hidden = false;
    chip.dataset.tone = raw >= 90 ? 'danger' : raw >= 70 ? 'warn' : 'ok';
    const ring = chip.querySelector('.ctx-progress');
    if (ring) {
      const C = 44;                       // 2πr，r = 7（viewBox 20×20）
      ring.style.strokeDashoffset = String(C * (1 - Math.min(100, pct) / 100));
    }
    const label = chip.querySelector('.ctx-pct');
    const text = ctxTokensLabel(used);
    if (label) label.textContent = text;
    chip.title = t('context.chip', {
      k: text, p: pct, used: num(used), budget: num(budget),
      window: num(win), out: num(output), peak: num(peak),
    });
    chip.setAttribute('aria-label', chip.title);
  }

  function renderUsageHint() {
    const el = $('#usageHint');
    if (!el) return;
    const html = state.currentId ? conversationStatsHtml() : '';
    if (!html) {
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    el.hidden = false;
    el.innerHTML = html;
  }

  function render() {
    renderHeader();
    renderMessages();
    renderActivity();
    renderUsageHint();
    renderContextChip();
  }

  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    const run = () => {
      // rAF 与下面的兜底定时器谁先到都只渲染一次；此前没有这个判断，
      // 每次调度实际都会渲染两遍（第二遍在第 120ms），这就是肉眼可见的频闪。
      if (!renderQueued) return;
      renderQueued = false;
      render();
    };
    requestAnimationFrame(run);
    // 兜底：后台标签页 / 无渲染帧的环境（打印、某些嵌入式浏览器）里
    // requestAnimationFrame 永不触发，没有兜底界面会一直卡在"加载中"。
    // 谁先触发都只渲染一次——run 开头复位 renderQueued，另一个直接 return。
    setTimeout(run, 120);
  }

  /* ── run stream ──────────────────────────────────────────────────────── */

  function closeStream() {
    if (state.es) {
      state.es.close();
      state.es = null;
    }
  }

  function parseServerTime(value) {
    const s = String(value || '').trim();
    if (!s) return 0;
    // sqlite 里是本地时间字符串（YYYY-MM-DD HH:MM:SS），不要当 UTC 解析
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/);
    if (m) {
      return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
    }
    const t = Date.parse(s);
    return Number.isFinite(t) ? t : 0;
  }

  function newLiveTurn(run) {
    // 切换会话/重连时以服务端的 started_at 为准，否则计时会从 0 重新开始，
    // 多任务并行时看起来像「刚启动」，时长也会算错。
    const serverStarted = parseServerTime((run && (run.started_at || run.created_at)) || '');
    return {
      id: 'live',
      role_name: appBrand(),
      model: (state.app && state.app.llm && state.app.llm.model) || '',
      content: '',
      trace: [],
      pending: true,
      startedAt: serverStarted || Date.now(),
      duration_ms: 0,
      usage: {},
      // 上下文读数：后端锚点 + 本地流式增量（见 renderContextChip）。
      contextTokens: 0,
      ctxText: ctxAcc(),
      error: '',
      lastTextRound: undefined,
    };
  }

  function startRun(run, replay = false) {
    if (!run || !run.id) return;
    if (state.running && state.run && state.run.id === run.id) return;
    closeStream();
    state.run = run;
    state.running = true;
    state.cursor = 0;
    refreshActiveRuns();
    state.pendingApprovals = [];
    renderApprovalBar();
    state.live = newLiveTurn(run);
    state.ctxTail = 0;          // 新一轮从库里最近一次真实值起算，别带上一轮的估算
    state.subagents.clear();
    state.subagentOpen = null;
    renderSubagents();
    for (const key of Array.from(state.openDetails.keys())) {
      if (String(key).startsWith('live:')) state.openDetails.delete(key);
    }
    for (const key of Array.from(stepOpen)) {
      if (String(key).startsWith('live:')) stepOpen.delete(key);
    }
    for (const key of Array.from(stepClosed)) {
      if (String(key).startsWith('live:')) stepClosed.delete(key);
    }
    state.stick = true;
    if (!replay) state.messages = state.messages; // keep optimistic/current messages
    // 新一代 run 开始：复位停止按钮。上一轮点过停止会把它置 disabled，
    // 不复位就表现为「按钮灰了、点不动」，看起来像停止失灵。
    clearStopFallback();
    const stopBtnEl = $('#stopBtn');
    if (stopBtnEl) stopBtnEl.disabled = false;
    scheduleRender();
    openStream(run.id, 0);
    startActivityTicker();
  }

  function openStream(runId, since) {
    closeStream();
    if (!window.EventSource) {
      toast(t('stream.unsupported'), 'bad');
      return;
    }
    const es = new EventSource(`${API}/runs/${encodeURIComponent(runId)}/stream?since=${Math.max(0, since)}`);
    state.es = es;
    let warned = false;

    es.addEventListener('run', (event) => {
      let payload;
      try { payload = JSON.parse(event.data); } catch { return; }
      if (payload.run) {
        state.run = payload.run;
        if (state.live) state.live.model = state.live.model || (state.app?.llm?.model || '');
      }
      for (const evt of payload.events || []) {
        const seq = Number(evt && evt._seq);
        if (seq && seq <= state.cursor) continue;
        applyEvent(evt);
        if (seq) state.cursor = seq;
      }
      if (payload.last_seq && payload.last_seq > state.cursor) state.cursor = payload.last_seq;
      scheduleRender();
    });

    es.addEventListener('run_end', (event) => {
      let payload = {};
      try { payload = JSON.parse(event.data); } catch { /* ignore */ }
      es.close();
      if (state.es === es) state.es = null;
      // 只收尾「这条连接对应的 run」：停止并继续时旧 run 的 run_end 可能晚于
      // 新 run 的 startRun 到达，直接 finishRun 会把刚开始的新一轮界面打掉
      // （表现为「点了继续却卡住不动」）。
      const ended = (payload.run && payload.run.id) || runId;
      if (state.run && state.run.id && ended !== state.run.id) return;
      finishRun(payload.run || state.run);
    });

    es.addEventListener('error', (event) => {
      if (event && event.data) {
        try {
          const payload = JSON.parse(event.data);
          if (payload.detail && !warned) {
            warned = true;
            toast(localizeServerMessage(payload.detail), 'bad');
          }
        } catch { /* ignore */ }
      }
      if (es.readyState === EventSource.CLOSED && state.running) {
        // 连接被彻底关掉（服务端结束了流但没发 run_end，或代理中断了长连接）。
        // **不能**在这里直接 finishRun：服务端 run 可能还在跑，本地提前收尾会让
        // 界面显示「已结束」而任务继续，下一步操作就会错位（过去的 800ms 兜底
        // 正是「卡住后只能重发一条」的来源之一）。
        // 正确做法：按游标重连续看；终态由 run_end 或状态守护轮询判定。
        if (state.es !== es) return;
        state.es = null;
        const runId = state.run && state.run.id;
        if (!runId) return;
        setTimeout(() => {
          if (state.running && !state.es && state.run && state.run.id === runId) {
            openStream(runId, state.cursor || 0);
          }
        }, 1000);
      }
    });
  }

  function applyEvent(evt) {
    if (!evt || !state.live) return;
    const turn = state.live;

    // 后端推来的上下文占用（真实 prompt 锚点 + 它自己的增量）：以它为准，
    // 本地流式增量清零重新累加——两边不会重复计数。
    if (evt.context_tokens !== undefined) {
      turn.contextTokens = Number(evt.context_tokens) || 0;
      turn.ctxText = ctxAcc();
    }

    if (evt.ping) {
      // 心跳：上游仍在等待 / 仍在生成。只更新活动条与「实时状态」，
      // 不进 trace（否则几十条心跳会把时间线刷满）。
      turn.waiting = {
        kind: evt.waiting || 'llm',
        phase: evt.phase || '',
        elapsed_s: Number(evt.elapsed_s || 0),
        name: evt.name || '',
        at: Date.now(),
      };
      renderActivity();
      return;
    }

    if (evt.retry) {
      // 网络抖动自动重试：状态可见 + 时间线留一条（合并同轮的重试，
      // 20 次重试不该刷出 20 条）。
      const r = evt.retry;
      turn.retrying = {
        attempt: Number(r.attempt || 0),
        max_attempts: Number(r.max_attempts || 0),
        label: r.label || '',
        resume: !!r.resume,
        at: Date.now(),
      };
      turn.waiting = null;
      const last = turn.trace.length ? turn.trace[turn.trace.length - 1] : null;
      if (last && last.kind === 'retry' && last.round === r.round) {
        last.attempt = turn.retrying.attempt;
        last.max_attempts = turn.retrying.max_attempts;
        last.text = r.message || last.text;
        touchStep(last);
      } else {
        turn.trace.push(touchStep({
          kind: 'retry', round: r.round, attempt: turn.retrying.attempt,
          max_attempts: turn.retrying.max_attempts,
          text: r.message || '',
          ts: Date.now() - turn.startedAt,
        }));
      }
      renderActivity();
      return;
    }

    if (evt.start) {
      turn.role_name = evt.role_name || turn.role_name;
      turn.pending = true;
      return;
    }

    if (evt.delta !== undefined) {
      // 真实内容到了：等待 / 重试状态结束，活动条回到正常语义。
      turn.waiting = null;
      turn.retrying = null;
      // 正在生成的正文马上就会进入下一轮 prompt：本地先按字符计进上下文读数，
      // 不必等这一轮请求结束（那可能要好几分钟）。
      turn.ctxText = ctxAdd(turn.ctxText, evt.delta);
      if (turn.lastTextRound !== evt.round) {
        turn.content = '';
        turn.lastTextRound = evt.round;
      }
      turn.content += String(evt.delta || '');
      const trace = turn.trace;
      const last = trace[trace.length - 1];
      if (last && last.closed) last.closed = true;
      if (last && last.kind === 'text' && last.round === evt.round && !last.closed) {
        last.text = (last.text || '') + String(evt.delta || '');
        touchStep(last);
      } else {
        trace.push(touchStep({
          kind: 'text', round: evt.round, text: String(evt.delta || ''),
          ts: Date.now() - turn.startedAt,
        }));
      }
      return;
    }

    if (evt.usage !== undefined && !evt.done) {
      // 每次模型请求结束后的累计用量：运行中实时刷新统计条。
      turn.usage = evt.usage || turn.usage;
      // 真实 prompt 已回来，压缩后的临时估算值不再需要。
      turn.contextAfter = 0;
      if (evt.model) turn.model = evt.model;
      return;
    }

    if (evt.interrupted) {
      const info = evt.interrupted || {};
      if (info.id) turn.id = String(info.id);
      turn.interrupted = true;
      turn.outcome = info.outcome || turn.outcome || '';
      if (info.error && info.outcome !== 'stopped') turn.error = String(info.error);
      return;
    }

    if (evt.thinking !== undefined) {
      turn.waiting = null;
      turn.retrying = null;
      const trace = turn.trace;
      const last = trace[trace.length - 1];
      if (last) last.closed = true;
      const last2 = trace[trace.length - 1];
      if (last2 && last2.kind === 'thinking' && last2.round === evt.round) {
        last2.text = (last2.text || '') + String(evt.thinking || '');
        touchStep(last2);
      } else {
        trace.push(touchStep({
          kind: 'thinking', round: evt.round, text: String(evt.thinking || ''),
          ts: Date.now() - turn.startedAt,
        }));
      }
      return;
    }

    if (evt.tool_call) {
      turn.waiting = null;
      turn.retrying = null;
      const c = evt.tool_call;
      const trace = turn.trace;
      const last = trace[trace.length - 1];
      if (last) last.closed = true;
      let hit = trace.find((s) => s.kind === 'tool' && s.id === c.id);
      // 工具已转后台：后台 job 跑完补的那条 tool_result 只带 job_id（id 可能为空），
      // 按 job_id 认领，避免出现「一条 background + 一条 done」的重复条目。
      if (!hit && c.job_id) {
        hit = trace.find((s) => s.kind === 'tool' && s.job_id === c.job_id);
      }
      // 「准备中」条目可能没有 call id（上游第一个 delta 未必带 id）：
      // 正式 running 事件到达时按 名字+轮次 认领它，避免重复条目。
      if (!hit && c.status !== 'preparing') {
        hit = trace.find((s) => s.kind === 'tool' && s.status === 'preparing'
          && !s.id && s.name === c.name && s.round === c.round);
        if (hit) hit.id = c.id;
      }
      if (hit) {
        Object.assign(hit, c, { kind: 'tool', status: c.status || hit.status || 'running' });
        touchStep(hit);
      } else {
        trace.push(touchStep({
          kind: 'tool', phase: 'start', id: c.id, name: c.name,
          args: c.args, round: c.round, status: c.status || 'running',
          ts: Date.now() - turn.startedAt,
        }));
      }
      return;
    }

    if (evt.tool_result) {
      const r = evt.tool_result;
      const trace = turn.trace;
      // 后台 job 完成时补发的结果只带 job_id：按 job_id 认领那条 background 条目。
      let hit = trace.find((s) => s.kind === 'tool' && r.id && s.id === r.id);
      if (!hit && r.job_id) {
        hit = trace.find((s) => s.kind === 'tool' && s.job_id === r.job_id);
      }
      if (hit) {
        Object.assign(hit, r, {
          kind: 'tool', phase: 'end', status: 'done',
          duration_ms: r.duration_ms,
        });
        touchStep(hit);
      } else {
        trace.push(touchStep({
          kind: 'tool', phase: 'end', id: r.id, name: r.name,
          args: r.args, ok: r.ok, summary: r.summary, display: r.display,
          duration_ms: r.duration_ms, round: r.round, status: 'done',
          ts: Date.now() - turn.startedAt,
        }));
      }
      return;
    }

    if (evt.task_proposal) {
      const p = evt.task_proposal;
      turn.trace.push(touchStep({
        kind: 'proposal', status: 'pending', proposal_id: p.id,
        goal: p.goal, reason: p.reason, ts: Date.now() - turn.startedAt,
      }));
      return;
    }

    if (evt.context_compress_start) {
      const c = evt.context_compress_start;
      const b = c.breakdown || {};
      const dialog = Number(b.user || 0) + Number(b.assistant || 0);
      turn.trace.push(touchStep({
        kind: 'compress', status: 'running', round: evt.round,
        before: c.before || 0, budget: c.budget || 0,
        reason: c.reason || '', mode: c.mode || '', breakdown: b,
        text: t('trace.compressStartDetail', {
          before: (c.before || 0).toLocaleString(),
          budget: (c.budget || 0).toLocaleString(),
          tool: Number(b.tool || 0).toLocaleString(),
          dialog: dialog.toLocaleString(),
          system: Number(b.system || 0).toLocaleString(),
        }),
        startedAt: Date.now(), ts: Date.now() - turn.startedAt,
      }));
      return;
    }

    if (evt.context_compress_done) {
      const c = evt.context_compress_done;
      const steps = turn.trace || [];
      // 顶部读数立刻跟着下降：压缩结果的实测值就是新锚点，
      // 下一轮 usage 回来后再用真实 prompt 校正（见 usage 分支）。
      turn.contextAfter = Number(c.after || 0) || 0;
      if (turn.contextAfter) {
        turn.contextTokens = turn.contextAfter;
        turn.ctxText = ctxAcc();
      }
      let step = null;
      for (let i = steps.length - 1; i >= 0; i -= 1) {
        if (steps[i].kind === 'compress' && steps[i].status === 'running') {
          step = steps[i];
          break;
        }
      }
      const ms = Number(c.duration_ms)
        || (step && step.startedAt ? Date.now() - step.startedAt : 0);
      // 一级压缩只清理旧工具输出，没有摘要，别写成「来源 prune」
      const text = c.source === 'prune'
        ? t('trace.compressPruned', {
          before: (c.before || 0).toLocaleString(),
          after: (c.after || 0).toLocaleString(),
          n: Number(c.pruned_tool_outputs || 0) + Number(c.deduped_tool_outputs || 0),
        })
        : t('trace.compressDoneDetail', {
          before: (c.before || 0).toLocaleString(),
          after: (c.after || 0).toLocaleString(),
          source: c.source || 'summary',
          keep: (c.keep || 0).toLocaleString(),
          duration: ms ? duration(ms) : '—',
        });
      if (step) {
        step.status = 'done';
        step.after = c.after || 0;
        step.source = c.source || '';
        step.duration_ms = ms;
        step.text = text;
        touchStep(step);
      } else {
        turn.trace.push(touchStep({
          kind: 'compress', status: 'done', round: evt.round,
          after: c.after || 0, source: c.source || '',
          duration_ms: ms, text,
          ts: Date.now() - turn.startedAt,
        }));
      }
      return;
    }

    if (evt.context_compressed) {
      const c = evt.context_compressed;
      turn.contextAfter = Number(c.after || 0) || 0;
      turn.trace.push(touchStep({
        kind: 'thinking', round: evt.round,
        text: t('trace.contextCompressed', {
          before: (c.before || 0).toLocaleString(),
          after: (c.after || 0).toLocaleString(),
          source: c.source || 'summary',
          keep: (c.keep || 0).toLocaleString(),
        }),
        ts: Date.now() - turn.startedAt, closed: true,
      }));
      return;
    }

    // 危险操作的审批请求：规则拦下，模型此刻的正文作为「它打算做什么」的说明
    if (evt.tool_approval) {
      const a = evt.tool_approval;
      turn.trace.push(touchStep({
        kind: 'approval', id: a.id, name: a.tool, risk: a.risk || 'medium',
        reasons: a.reasons || [], note: a.note || '', args: a.args,
        preview: a.preview || null,
        status: 'pending', round: a.round, ts: Date.now() - turn.startedAt,
      }));
      state.pendingApprovals.push({
        id: a.id, tool: a.tool, risk: a.risk || 'medium',
        reasons: a.reasons || [], note: a.note || '', status: 'pending',
      });
      renderApprovalBar();
      refreshActiveRuns();
      return;
    }

    if (evt.approval_decided) {
      const d = evt.approval_decided;
      const hit = turn.trace.find((s) => s.kind === 'approval' && s.id === d.id);
      if (hit) {
        hit.status = d.status || 'denied';
        touchStep(hit);
      }
      const bar = state.pendingApprovals.find((a) => a.id === d.id);
      if (bar) bar.status = d.status || 'denied';
      state.pendingApprovals = state.pendingApprovals.filter((a) => a.status === 'pending');
      renderApprovalBar();
      refreshActiveRuns();
      return;
    }

    if (evt.subagent_event) {
      applySubagentEvent(evt.subagent_event);
      return;
    }

    if (evt.ask_user) {
      showAskUser(evt.ask_user);
      return;
    }

    if (evt.ask_user_done) {
      hideAskUserIf(evt.ask_user_done.id);
      return;
    }

    if (evt.error) {
      turn.error = String(evt.error);
      turn.pending = false;
      return;
    }

    if (evt.todo_update) {
      const upd = evt.todo_update;
      if (Array.isArray(upd.items)) {
        state.todos = upd.items;
        updateTodoBadge();
        if (isTodoDrawerOpen()) renderTodoDrawer();
      }
      return;
    }

    if (evt.goal_update) {
      const upd = evt.goal_update;
      if (upd && upd.id) {
        state.goal = upd;
        renderGoalBar();
      }
      return;
    }

    if (evt.done) {
      turn.pending = false;
      if (typeof evt.text === 'string') turn.content = evt.text;
      if (Array.isArray(evt.trace) && evt.trace.length) {
        // 服务端收尾 trace 是「过程」部分的最终版本；逐条打版本，
        // 让增量渲染只更新真正变化的块。
        for (const s of evt.trace) touchStep(s);
        turn.trace = evt.trace;
      }
      if (evt.reasoning && !turn.trace.some((s) => s.kind === 'thinking')) {
        turn.trace.unshift(touchStep({ kind: 'thinking', text: evt.reasoning, ts: 0 }));
      }
      turn.duration_ms = evt.duration_ms || 0;
      turn.model = evt.model || turn.model;
      turn.role_name = evt.role_name || turn.role_name;
      turn.usage = evt.usage || turn.usage;
      turn.contextAfter = 0;
      if (evt.id) turn.id = evt.id;
      return;
    }
  }

  async function finishRun(run) {
    if (!state.running) return;
    state.running = false;
    state.run = run || state.run;
    // 停止兜底轮询到此为止，并复位按钮，避免影响下一轮 run。
    clearStopFallback();
    const stopBtnDone = $('#stopBtn');
    if (stopBtnDone) stopBtnDone.disabled = false;
    stopActivityTicker();
    refreshActiveRuns();
    const live = state.live;
    if (live) {
      live.pending = false;
      if (!live.duration_ms && live.startedAt) live.duration_ms = Date.now() - live.startedAt;
      // 本轮最后的上下文读数：live 马上要被清空，顶部还得接着显示这个数。
      state.ctxTail = liveContextTokens();
    }

    const status = (state.run && state.run.status) || 'done';
    const convId = state.currentId;
    // 静默停止（「立即插入 = 停止并继续」）：这轮停下是正常流程的一部分，
    // 不提示「运行已停止」，也不显示「本轮输出已中断」——用户只看到紧接着开跑的新消息。
    const silentStop = !!state.silentStop;
    state.silentStop = false;
    if (status === 'stopped' && !silentStop) toast(t('run.stopped'), 'ok', 2600);
    if (status === 'failed') {
      const message = localizeServerMessage(
        (state.run && state.run.error) || (live && live.error) || t('run.failed'),
      );
      if (!live || !live.error) toast(message, 'bad', 6000);
    }

    // 收尾：done 事件已经带正文 / trace / usage，把正在生成的那条**就地转正**。
    // 之前整段 loadConversation 会把已渲染的转录重建一遍——跑完"闪一下"，
    // 流式动画被打断，长对话还慢。只有确实没有内容时才重新拉取。
    const finalized = live && (live.content || (live.trace && live.trace.length) || live.error);
    if (finalized) {
      const finalTurn = {
        id: live.id && live.id !== 'live' ? live.id : `local-${(state.run && state.run.id) || Date.now()}`,
        by: 'assistant',
        role_name: live.role_name,
        model: live.model,
        content: live.content || '',
        trace: live.trace || [],
        usage: live.usage || {},
        duration_ms: live.duration_ms || 0,
        error: live.error || (status === 'failed' ? ((state.run && state.run.error) || '') : ''),
        // 静默停止不落中断标记：正文照常转正，只是没有「已中断」红条。
        interrupted: silentStop ? false : !!live.interrupted,
        outcome: live.outcome || (status === 'stopped' ? 'stopped' : ''),
        ts: localStamp(),
        local: true,
      };
      // 会话总体基线是进入会话时取的（不含本轮）；就地转正后补上本轮，避免总数少一轮。
      if (state.convUsage && live.usage) state.convUsage = addUsage(state.convUsage, live.usage);
      const liveEl = $('#chatInner') ? $('#chatInner').querySelector('[data-live]') : null;
      if (liveEl) {
        // 就地把流式消息转成完成态：只增量补齐最终块，不整条重绘。
        state.live = finalTurn;
        updateLiveArticle(liveEl);
        state.live = null;
        // 这一轮结束了：过程整体折成一条栏，只留最终正文。就地换 body，
        // 不重建整条转录（长对话里那样会明显闪一下）。
        const body = liveEl.querySelector('.assistant-body');
        if (body) body.innerHTML = assistantBodyHtml(finalTurn, false);
        liveEl.removeAttribute('data-live');
        liveEl.classList.remove('is-live');
        liveEl.setAttribute('data-msg', finalTurn.id);
        insertMessageInOrder(finalTurn);
        transcriptSkipNext = true;
        transcriptSig = transcriptSignature();
        scheduleRender();
      } else {
        insertMessageInOrder(finalTurn);
        state.live = null;
        scheduleRender();
      }
    } else {
      state.live = null;
      scheduleRender();
      if (convId && convId === state.currentId) {
        try {
          await loadConversation(convId, { silent: true });
        } catch (err) {
          console.warn('refresh after run failed', err);
        }
      }
    }
    state.pendingApprovals = [];
    if (state.currentId) delete state.pendingQuestions[state.currentId];
    state.pendingQuestion = null;
    renderAskUser();
    state.subagents.clear();
    state.subagentOpen = null;
    renderSubagents();
    state.subagents.clear();
    state.subagentOpen = null;
    renderSubagents();
    renderApprovalBar();
    if (convId && convId === state.currentId) {
      await markConversationRead(convId, { render: false });
    }
    refreshConversations();
    pollRuntime();
    // 队列模式：接管下一条排队 run（worker 会在 ~1s 内认领）
    syncNextQueuedRun(convId);
  }

  let activityTicks = 0;

  function startActivityTicker() {
    stopActivityTicker();
    activityTicks = 0;
    activityTimer = setInterval(() => {
      if (!state.running) { stopActivityTicker(); return; }
      activityTicks += 1;
      renderActivity();
      // Keep "working" elapsed text fresh without rebuilding the transcript.
      const el = $('#runStateText');
      if (el) el.textContent = (state.run && state.run.status === 'queued')
        ? t('runState.queued')
        : t('runState.working');
      const liveEl = $('[data-live-time]');
      if (liveEl && state.live && state.live.startedAt) {
        liveEl.textContent = duration(Date.now() - state.live.startedAt);
      }
      // 状态守护：每 10s 直接问一次服务端。SSE 断流 / 代理缓冲 / 浏览器把连接
      // 挂死时，前端可能永远收不到 run_end —— 界面就停在「处理中」，只能靠用户
      // 点停止或再发一条消息（那会 startRun 强制换一轮）来「碰」一下。
      // 这里按库里的真实状态收尾，界面不再依赖单条连接的存活。
      if (activityTicks % 10 === 0) reconcileRunState();
    }, 1000);
  }

  // 后端事件停滞多久就提示「可能卡住」（秒）。心跳间隔后端是 20s，
  // 这里留两倍余量：真的两个心跳周期一条事件都没有，就不该再让用户干等。
  const STALL_NOTICE_S = 45;
  // 每次提示后最少间隔这么久才再提示一次，避免反复弹。
  const STALL_NOTICE_GAP_S = 120;
  let lastStallNoticeAt = 0;

  async function reconcileRunState() {
    const runId = state.run && state.run.id;
    if (!state.running || !runId) return;
    try {
      const data = await api(`/runs/${encodeURIComponent(runId)}`);
      const run = (data && data.run) || null;
      if (!state.running || (state.run && state.run.id !== runId)) return;
      if (run && TERMINAL.has(run.status)) {
        state.run = run;
        finishRun(run);              // 服务端已收尾：本地按终态落地，恢复可交互
        return;
      }
      // 还在跑但本地连接已断：按当前游标接回去（靠 seq 去重，幂等）
      if (!state.es) openStream(runId, state.cursor || 0);
      // 后端还在 running、但很久没有任何新事件：如实提示一句（附停止入口），
      // 而不是让界面永远停在「处理中」——用户只能自己琢磨要不要重发消息。
      const lastSeq = Number((data && data.last_seq) || 0);
      const now = Date.now();
      if (lastSeq !== state.stallSeq) {
        state.stallSeq = lastSeq;
        state.stallSince = now;
        return;
      }
      const stalledS = Math.floor((now - (state.stallSince || now)) / 1000);
      if (stalledS >= STALL_NOTICE_S && now - lastStallNoticeAt >= STALL_NOTICE_GAP_S * 1000) {
        lastStallNoticeAt = now;
        toast(t('run.stalled', { s: stalledS }), 'bad', 6000);
      }
    } catch (err) {
      console.warn('run state reconcile failed', err);
    }
  }

  function stopActivityTicker() {
    if (activityTimer) clearInterval(activityTimer);
    activityTimer = null;
  }

  async function stopRun() {
    if (!state.run || !state.running) return;
    const btn = $('#stopBtn');
    if (btn) btn.disabled = true;
    try {
      const data = await api(`/runs/${encodeURIComponent(state.run.id)}/stop`, { method: 'POST' });
      toast(t('stop.requested'), 'ok', 2400);
      // 服务端是同步执行的（标记 + 取消任务），返回即已生效；剩下的收尾
      // （落部分回复 / run_end 帧）通常 1s 内到。这里挂一个短轮询兜底：
      // 若 6s 内仍没收到 run_end，直接按 stopped 收尾，不让界面卡在「处理中」。
      stopRunFallback();
    } catch (err) {
      if (btn) btn.disabled = false;
      toast(err.message, 'bad');
    }
  }

  let stopFallbackTimer = null;

  function stopRunFallback() {
    if (stopFallbackTimer) clearTimeout(stopFallbackTimer);
    // 锁定「发起停止时」的那一条 run：停止并继续会立刻起下一轮，
    // 若不锁定就会去轮询新 run 并把它的界面收掉 / 误报「仍在收尾」。
    const targetRunId = (state.run && state.run.id) || '';
    stopFallbackTimer = setTimeout(async () => {
      stopFallbackTimer = null;
      if (!state.running) return;
      const runId = targetRunId;
      if (!runId || (state.run && state.run.id && state.run.id !== runId)) return;
      try {
        const data = await api(`/runs/${encodeURIComponent(runId)}`);
        const run = (data && data.run) || null;
        const terminal = !run || !!(data && data.terminal)
          || ['done', 'failed', 'stopped'].includes(run.status);
        if (terminal) {
          finishRun(run || { ...(state.run || {}), status: 'stopped' });
        } else {
          // 后端还挂在 running：如实提示，不再假装已停（按钮保持可用可重试）。
          toast(t('stop.pending'), 'bad', 5000);
          const btn = $('#stopBtn');
          if (btn) btn.disabled = false;
        }
      } catch (err) {
        console.warn('stop fallback lookup failed', err);
        const btn = $('#stopBtn');
        if (btn) btn.disabled = false;
      }
    }, 6000);
  }

  function clearStopFallback() {
    if (stopFallbackTimer) clearTimeout(stopFallbackTimer);
    stopFallbackTimer = null;
  }

  /* ── 队列模式 / 目标模式 / 任务清单 ─────────────────────────────────── */

  function queuePreview(text) {
    const one = String(text || '').replace(/\s+/g, ' ').trim();
    return one.length > 90 ? `${one.slice(0, 90)}…` : one;
  }

  function renderQueue() {
    const strip = $('#queueStrip');
    if (!strip) return;
    const items = state.queue || [];
    strip.hidden = items.length === 0;
    if (!items.length) {
      $('#queueItems').innerHTML = '';
      return;
    }
    $('#queueCount').textContent = t('queue.count', { n: items.length });
    const hasRunning = !!state.running || !!state.queueRunning;
    const last = items.length - 1;
    $('#queueItems').innerHTML = items.map((run, i) => {
      const id = escAttr(run.id);
      // 上下移动是首位操作：队首不能再上移，队尾不能再下移（按钮置灰而非隐藏，
      // 避免每行按钮数量变化导致行高抖动）
      const upDis = i === 0 ? ' disabled' : '';
      const downDis = i === last ? ' disabled' : '';
      return `<div class="queue-item">
        <span class="queue-index">${i + 1}</span>
        <span class="queue-text" title="${escAttr(run.content || '')}">${escapeHtml(queuePreview(run.content))}</span>
        <span class="queue-actions">
          <button type="button" class="queue-btn icon" data-queue-action="up" data-run-id="${id}" title="${escAttr(t('queue.moveUpTitle'))}" aria-label="${escAttr(t('queue.moveUp'))}"${upDis}><svg><use href="#i-chevron-up"/></svg></button>
          <button type="button" class="queue-btn icon" data-queue-action="down" data-run-id="${id}" title="${escAttr(t('queue.moveDownTitle'))}" aria-label="${escAttr(t('queue.moveDown'))}"${downDis}><svg><use href="#i-chevron-down"/></svg></button>
          <button type="button" class="queue-btn icon" data-queue-action="edit" data-run-id="${id}" title="${escAttr(t('queue.editTitle'))}" aria-label="${escAttr(t('queue.edit'))}"><svg><use href="#i-edit"/></svg></button>
          ${hasRunning ? `<button type="button" class="queue-btn" data-queue-action="inject" data-run-id="${id}" title="${escAttr(t('queue.injectTitle'))}">${escapeHtml(t('queue.inject'))}</button>` : ''}
          <button type="button" class="queue-btn danger" data-queue-action="cancel" data-run-id="${id}">${escapeHtml(t('queue.cancel'))}</button>
        </span>
      </div>`;
    }).join('');
  }

  async function refreshQueue(convId = state.currentId) {
    if (!convId) {
      state.queue = [];
      state.queueRunning = null;
      renderQueue();
      return null;
    }
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/queue`);
      if (convId !== state.currentId) return null;
      state.queue = (data && data.items) || [];
      state.queueRunning = (data && data.running) || null;
      renderQueue();
      return data;
    } catch (err) {
      console.warn('queue refresh failed', err);
      return null;
    }
  }

  async function queueAction(action, runId) {
    if (!state.currentId || !runId) return;
    const convId = state.currentId;
    const base = `/conversations/${encodeURIComponent(convId)}/queue/${encodeURIComponent(runId)}`;
    if (action === 'edit') {
      // 排队消息不在转录里，编辑入口放在队列条上：点编辑时把它临时露出来渲染
      // 编辑器（见 isQueuedUserMessage），保存后重新排队、再隐藏。
      const row = (state.queue || []).find((it) => it.id === runId);
      const msgId = (row && row.queued_msg_id) || '';
      if (!msgId) return;
      if (!findMessage(msgId)) {
        await loadConversation(convId, { silent: true }).catch(() => {});
      }
      if (convId === state.currentId) startEditMessage(msgId);
      return;
    }
    try {
      let data;
      if (action === 'up' || action === 'down') {
        data = await api(`${base}/move`, {
          method: 'POST',
          body: JSON.stringify({ direction: action }),
        });
        if (data && data.moved === false) {
          // 已在队首 / 队尾：不动数据，只如实提示（这不是错误）
          toast(t(action === 'up' ? 'queue.atTop' : 'queue.atBottom'), 'ok', 1600);
          return;
        }
        toast(t(action === 'up' ? 'queue.movedUp' : 'queue.movedDown'), 'ok', 1400);
      } else if (action === 'inject') {
        // 「停止并继续」：后端会停掉当前回复，把这条提到队首。
        // 这里**不做本地乐观改写**（不写 injected_into_run、不把它当成已插入）：
        // 旧的「插入到正在生成的回答里」正是界面异常的来源——回答继续在原位输出，
        // 消息却被从队列里摘掉并在转录里跳位。现在它仍是排队消息，等 run_end 后由
        // syncNextQueuedRun 接管下一轮，位置自然正确。
        data = await api(`${base}/inject`, { method: 'POST' });
        // 静默停止：当前 run 被停是「接着跑新消息」的正常一步，不弹「运行已停止」，
        // 也不打中断红条。这里只标记本轮为静默停止，并挂停止兜底轮询
        // （run_end 迟迟不来时仍能收尾，界面不会卡在「处理中」）。
        state.silentStop = true;
        stopRunFallback();
      } else {
        data = await api(base, { method: 'DELETE' });
        toast(t('queue.cancelled'), 'ok', 1800);
      }
      if (convId !== state.currentId) return;
      const items = (data && data.items) || [];
      // 停止并继续：这条消息**仍在队列里**（已提到队首），保持排队展示，
      // 等当前 run 收尾后由 syncNextQueuedRun 接管。
      state.queue = items;
      renderQueue();
      if (action === 'cancel') {
        // 服务端已删除排队消息节点：重拉转录，去掉本地乐观行
        await loadConversation(convId, { silent: true }).catch(() => {});
      }
    } catch (err) {
      toast(t('queue.failed', { msg: localizeServerMessage(err.message) }), 'bad', 5000);
    }
  }

  async function syncNextQueuedRun(convId, tries = 0) {
    if (!convId || convId !== state.currentId) return;
    if (state.queueTimer) {
      clearTimeout(state.queueTimer);
      state.queueTimer = null;
    }
    const data = await refreshQueue(convId);
    if (!data || convId !== state.currentId || state.running) return;
    if (data.running) {
      // worker 已经认领了下一条：接管它的流（先补拉转录，拿到新消息）
      try {
        await loadConversation(convId, { silent: true });
      } catch (err) {
        console.warn('refresh before next run failed', err);
      }
      if (convId === state.currentId && !state.running) startRun(data.running, true);
      return;
    }
    const waiting = (data.items || []).length > 0;
    // 目标模式：续跑 run 可能比 run_end 稍晚入队，多等两拍
    const goalPending = state.goal && state.goal.status === 'active' && tries < 3;
    if ((waiting || goalPending) && tries < 15) {
      state.queueTimer = setTimeout(() => syncNextQueuedRun(convId, tries + 1), 1200);
    }
  }

  function renderGoalBar() {
    const bar = $('#goalBar');
    if (!bar) return;
    const goal = state.goal;
    const showEmpty = !goal && state.mode === 'goal' && !!state.currentId
      && !state.goalDismissed;
    if (!goal && !showEmpty) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    const status = goal ? (goal.status || 'active') : 'empty';
    bar.dataset.status = status;
    bar.classList.toggle('is-empty', !goal);
    const active = !!goal && status === 'active';
    $('#goalText').textContent = goal ? (goal.objective || '') : t('goal.empty');
    const note = $('#goalNote');
    note.hidden = !goal || !goal.note;
    note.textContent = goal && goal.note ? `· ${goal.note}` : '';
    const statusLabel = goal && !active ? `${t('goal.status.' + status)} · ` : '';
    $('#goalRuns').textContent = goal ? statusLabel + t('goal.runs', {
      used: goal.runs_used || 0, max: goal.max_runs > 0 ? goal.max_runs : '∞',
    }) : '';
    const budget = goal ? Number(goal.token_budget || 0) : 0;
    $('#goalTokens').textContent = goal
      ? (budget
        ? t('goal.tokensBudget', { used: goal.tokens_used || 0, budget })
        : t('goal.tokens', { used: goal.tokens_used || 0 }))
      : '';
    const input = $('#goalBudgetInput');
    if (document.activeElement !== input) input.value = budget || '';
    input.disabled = !active;
    $('#goalEndBtn').hidden = !active;
    $('#goalDismissBtn').hidden = active || !goal;
  }

  async function refreshGoal(convId = state.currentId) {
    if (!convId) {
      state.goal = null;
      renderGoalBar();
      return;
    }
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/goal`);
      if (convId !== state.currentId) return;
      state.goal = data || null;
      if (data) state.goalDismissed = false;
      renderGoalBar();
    } catch (err) {
      console.warn('goal refresh failed', err);
    }
  }

  async function saveGoalBudget() {
    if (!state.currentId || !state.goal) return;
    const convId = state.currentId;
    const budget = Math.max(0, Math.floor(Number($('#goalBudgetInput').value || 0)));
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/goal`, {
        method: 'PUT', body: JSON.stringify({ token_budget: budget }),
      });
      if (convId !== state.currentId) return;
      state.goal = data;
      renderGoalBar();
      toast(t('goal.saved'), 'ok', 1800);
    } catch (err) {
      toast(err.message, 'bad');
    }
  }

  async function endGoal() {
    if (!state.currentId || !state.goal) return;
    const convId = state.currentId;
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/goal`, {
        method: 'DELETE',
      });
      if (convId !== state.currentId) return;
      state.goal = (data && data.goal) || null;
      renderGoalBar();
      if (state.mode === 'goal') setMode('autonomous');
      toast(t('goal.ended'), 'ok', 2400);
    } catch (err) {
      toast(err.message, 'bad');
    }
  }

  function updateTodoBadge() {
    const items = state.todos || [];
    const c = todoCounts(items);
    const open = c.pending + c.in_progress;
    const live = c.in_progress > 0;
    // 顶栏常驻入口：未完成数徽标 + 进行中高亮
    const topCount = $('#topTodoCount');
    if (topCount) {
      topCount.hidden = !open;
      topCount.textContent = String(open);
      topCount.classList.toggle('is-live', live);
    }
    // 对话内的任务卡片就地更新（避免整条转录重绘打断流式输出）
    const meta = document.querySelector('[data-todo-card-meta]');
    if (meta) meta.textContent = todoSummaryText(c);
    const list = document.querySelector('[data-todo-card] .turn-todo-list');
    if (list) {
      list.innerHTML = items.map((it) => {
        const st = TODO_STATUS_RANK[it.status] !== undefined ? it.status : 'pending';
        return `<li class="turn-todo-item is-${st}">
          ${todoMarkHtml(st)}
          <span class="turn-todo-text">${escapeHtml(it.content || it.title || '')}</span>
          <span class="turn-todo-state">${escapeHtml(t('todo.status.' + st))}</span>
        </li>`;
      }).join('');
    }
  }

  /* 任务清单面板：对话区左上角、进程面板**下方**的常驻浮动面板。
     入口有两个：顶栏 #topTodoBtn（常驻，带未完成数）和对话内的「任务」卡片。
     以前它挂在右侧 dock 的标签组里：dock 默认收起，标签没打开过就找不到入口；
     打开后又被 dock 的宽度 / 折叠状态绑住。现在只由用户点开、点关。 */

  function todoPanelEl() { return $('#todoPanel'); }

  function isTodoDrawerOpen() {
    const el = todoPanelEl();
    return !!el && !el.hidden;
  }

  function syncTodoToggle() {
    const btn = $('#topTodoBtn');
    if (btn) btn.setAttribute('aria-pressed', isTodoDrawerOpen() ? 'true' : 'false');
    const collapse = $('#todoSectionToggle');
    if (collapse) {
      const collapsed = !!state.ui.todoCollapsed;
      collapse.classList.toggle('is-collapsed', collapsed);
      collapse.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
  }

  function openTodoDrawer() {
    const el = todoPanelEl();
    if (!el) return;
    el.hidden = false;
    state.todo.open = true;
    state.ui.todoOpen = true;
    saveUi();
    renderTodoDrawer();
    syncTodoToggle();
    refreshTodos();
    setTimeout(() => { const input = $('#todoAddInput'); if (input) input.focus(); }, 30);
  }

  function closeTodoDrawer() {
    const el = todoPanelEl();
    if (el) el.hidden = true;
    state.todo.open = false;
    state.ui.todoOpen = false;
    saveUi();
    syncTodoToggle();
  }

  function renderTodoDrawer() {
    const list = $('#todoList');
    if (!list) return;
    const items = state.todos || [];
    const c = todoCounts(items);
    $('#todoSummary').textContent = items.length
      ? t('todo.summaryBar', { done: c.completed, total: c.total }) : '';
    $('#todoFoot').textContent = items.length
      ? t('todo.foot', { completed: c.completed, total: c.total }) : '';
    const panel = todoPanelEl();
    if (panel) panel.classList.toggle('is-collapsed', !!state.ui.todoCollapsed);
    updateTodoBadge();
    syncTodoToggle();
    if (state.ui.todoCollapsed) return;
    if (!items.length) {
      list.innerHTML = `<div class="todo-empty">${escapeHtml(t('todo.empty'))}</div>`;
      return;
    }
    list.innerHTML = items.map((it) => {
      const id = escAttr(it.id);
      const status = TODO_STATUS_RANK[it.status] !== undefined ? it.status : 'pending';
      const content = it.content || it.title || '';
      return `<div class="todo-item" data-status="${status}">
        <button type="button" class="todo-check" data-todo-action="toggle" data-todo-id="${id}" aria-label="${escAttr(t('todo.status.completed'))}">${status === 'completed' ? '<svg><use href="#i-check"/></svg>' : ''}</button>
        <div class="todo-main">
          <span class="todo-title">${escapeHtml(content)}</span>
          ${it.note ? `<span class="todo-note">${escapeHtml(it.note)}</span>` : ''}
        </div>
        <button type="button" class="todo-status" data-todo-action="cycle" data-todo-id="${id}">${escapeHtml(t('todo.status.' + status))}</button>
        <button type="button" class="icon-btn todo-del" data-todo-action="delete" data-todo-id="${id}" title="${escAttr(t('todo.deleted'))}" aria-label="${escAttr(t('todo.deleted'))}"><svg><use href="#i-trash"/></svg></button>
      </div>`;
    }).join('');
  }

  async function refreshTodos(convId = state.currentId) {
    if (!convId) {
      state.todos = [];
      updateTodoBadge();
      renderTodoDrawer();
      return;
    }
    try {
      const items = await api(`/conversations/${encodeURIComponent(convId)}/todos`);
      if (convId !== state.currentId) return;
      state.todos = items || [];
      updateTodoBadge();
      if (isTodoDrawerOpen()) renderTodoDrawer();
    } catch (err) {
      console.warn('todos refresh failed', err);
    }
  }

  async function addTodo(content) {
    if (!state.currentId) return;
    const convId = state.currentId;
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/todos`, {
        method: 'POST', body: JSON.stringify({ content }),
      });
      if (convId !== state.currentId) return;
      state.todos = (data && data.items) || state.todos;
      updateTodoBadge();
      renderTodoDrawer();
      toast(t('todo.added'), 'ok', 1600);
    } catch (err) {
      toast(t('todo.failed', { msg: localizeServerMessage(err.message) }), 'bad', 5000);
    }
  }

  async function todoAction(action, todoId) {
    if (!state.currentId || !todoId) return;
    const convId = state.currentId;
    const item = (state.todos || []).find((it) => it.id === todoId);
    const base = `/conversations/${encodeURIComponent(convId)}/todos/${encodeURIComponent(todoId)}`;
    try {
      let data;
      if (action === 'delete') {
        data = await api(base, { method: 'DELETE' });
        toast(t('todo.deleted'), 'ok', 1600);
      } else {
        const current = item ? item.status : 'pending';
        const next = action === 'toggle'
          ? (current === 'completed' ? 'pending' : 'completed')
          : (current === 'pending' ? 'in_progress'
            : current === 'in_progress' ? 'completed' : 'pending');
        data = await api(base, { method: 'PATCH', body: JSON.stringify({ status: next }) });
      }
      if (convId !== state.currentId) return;
      state.todos = (data && data.items) || state.todos;
      updateTodoBadge();
      renderTodoDrawer();
    } catch (err) {
      toast(t('todo.failed', { msg: localizeServerMessage(err.message) }), 'bad', 5000);
    }
  }

  /* ── 消息操作：编辑 / 撤回 / 复制 ───────────────────────────────────── */

  function findMessage(msgId) {
    return (state.messages || []).find((m) => m.id === msgId) || null;
  }

  function fallbackCopy(value, done) {
    try {
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      done();
    } catch (err) {
      toast(t('copy.failed'), 'bad', 2500);
    }
  }

  function copyText(value) {
    const text = String(value || '');
    if (!text) return;
    const done = () => toast(t('copy.done'), 'ok', 1500);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  }

  function startEditMessage(msgId) {
    const msg = findMessage(msgId);
    if (!msg || !msg.id) return;
    if (messageRunState(msg) === 'running') {
      toast(t('msg.running'), 'bad', 3000);
      return;
    }
    state.editingMsg = { id: msg.id, text: msg.content || '', focus: true };
    scheduleRender();
  }

  function cancelEditMessage() {
    if (!state.editingMsg) return;
    state.editingMsg = null;
    scheduleRender();
  }

  async function saveEditMessage() {
    const editing = state.editingMsg;
    if (!editing || !state.currentId) return;
    const ta = $('[data-msg-edit-input]');
    const content = (ta ? ta.value : editing.text || '').trim();
    if (!content) {
      if (ta) ta.focus();
      return;
    }
    const convId = state.currentId;
    try {
      await api(`/conversations/${encodeURIComponent(convId)}/messages/${encodeURIComponent(editing.id)}`, {
        method: 'PATCH', body: JSON.stringify({ content }),
      });
      state.editingMsg = null;
      await loadConversation(convId, { silent: true });
      await refreshQueue(convId);
      toast(t('msg.editDone'), 'ok', 2200);
      if (convId === state.currentId) syncNextQueuedRun(convId);
    } catch (err) {
      toast(localizeServerMessage(err.message), 'bad', 5000);
    }
  }

  async function recallMessage(msgId) {
    const msg = findMessage(msgId);
    if (!msg || !state.currentId) return;
    const queued = messageRunState(msg) === 'queued' || !!msg.queued;
    if (!window.confirm(queued ? t('msg.recallQueuedConfirm') : t('msg.recallConfirm'))) return;
    const convId = state.currentId;
    try {
      const data = await api(
        `/conversations/${encodeURIComponent(convId)}/messages/${encodeURIComponent(msgId)}/recall`,
        { method: 'POST' },
      );
      await loadConversation(convId, { silent: true });
      await refreshQueue(convId);
      if (state.editingMsg && !findMessage(state.editingMsg.id)) {
        state.editingMsg = null;
        scheduleRender();
      }
      toast(t('msg.recallDone'), 'ok', 3200);
    } catch (err) {
      toast(localizeServerMessage(err.message), 'bad', 5000);
    }
  }

  async function forkConversationFrom(msgId, convIdArg) {
    const convId = convIdArg || state.currentId;
    if (!convId) return;
    const ok = await openConfirm({
      title: t('fork.title'),
      message: t(msgId ? 'fork.confirm' : 'fork.confirmAll'),
      okText: t('fork.action'),
    });
    if (!ok) return;
    try {
      const data = await api(
        `/conversations/${encodeURIComponent(convId)}/fork`,
        { method: 'POST', body: JSON.stringify({ message_id: msgId || '' }) },
      );
      await refreshConversations();
      toast(t('fork.done'), 'ok', 3000);
      if (data && data.id) openConversation(data.id);
    } catch (err) {
      toast(t('fork.failed', { msg: localizeServerMessage(err.message) }),
        'bad', 5000);
    }
  }

  /* ── 文件检查点：回退（rewind）──────────────────────────────────────── */

  function rewindRow(kind, item) {
    const label = kind === 'modified' ? t('rewind.modified')
      : kind === 'added' ? t('rewind.added') : t('rewind.deleted');
    const tag = kind === 'modified' ? 'M' : kind === 'added' ? 'A' : 'D';
    const path = String(item.path || '');
    return `<div class="rewind-file">
      <span class="rewind-tag ${kind}" title="${escAttr(label)}">${tag}</span>
      <span class="rewind-path" title="${escAttr(path)}">${escapeHtml(path)}</span>
      <span class="rewind-size">${bytes(Number(item.size || 0))}</span>
    </div>`;
  }

  function renderRewindModal(message) {
    const info = state.rewind || {};
    const cp = info.cp || {};
    const diff = info.diff || {};
    const counts = diff.counts || { added: 0, modified: 0, deleted: 0 };
    const filesEl = $('#rewindFiles');
    const delEl = $('#rewindDeleteAdded');
    const convEl = $('#rewindConv');
    const deleteRow = $('#rewindDeleteRow');
    const noteEl = $('#rewindNote');
    if (!filesEl || !convEl) return;

    const target = $('#rewindTarget');
    if (target) {
      target.innerHTML = `<div class="rewind-target-main">${escapeHtml(cp.label || t('rewind.title'))}</div>
        <div class="rewind-target-meta">${escapeHtml(t('rewind.target', { time: cp.created_at || '' }))}
          <span>·</span><span>${escapeHtml(t('rewind.files'))} ${Number(cp.file_count || 0)}</span></div>`;
    }

    const box = $('#rewindDiff');
    if (box) {
      if (!diff.available) {
        box.innerHTML = `<div class="rewind-empty">${escapeHtml(t('rewind.unavailable', { reason: diff.reason || '—' }))}</div>`;
      } else {
        const total = Number(counts.added || 0) + Number(counts.modified || 0)
          + Number(counts.deleted || 0);
        const stats = `<div class="rewind-stats">
          <span class="rewind-stat modified"><b>${Number(counts.modified || 0)}</b> ${escapeHtml(t('rewind.modified'))}</span>
          <span class="rewind-stat added"><b>${Number(counts.added || 0)}</b> ${escapeHtml(t('rewind.added'))}</span>
          <span class="rewind-stat deleted"><b>${Number(counts.deleted || 0)}</b> ${escapeHtml(t('rewind.deleted'))}</span>
        </div>`;
        let rows = '';
        for (const it of (diff.modified || [])) rows += rewindRow('modified', it);
        for (const it of (diff.added || [])) rows += rewindRow('added', it);
        for (const it of (diff.deleted || [])) rows += rewindRow('deleted', it);
        const shown = rows ? `<div class="rewind-files">${rows}</div>` : '';
        const more = Number(counts.added || 0) + Number(counts.modified || 0)
          + Number(counts.deleted || 0) - (diff.modified || []).length
          - (diff.added || []).length - (diff.deleted || []).length;
        box.innerHTML = stats + (total ? shown : `<div class="rewind-empty">${escapeHtml(t('rewind.clean'))}</div>`)
          + (more > 0 ? `<div class="rewind-more">+${more}</div>` : '');
      }
    }

    const canFiles = !!diff.available;
    filesEl.disabled = !canFiles;
    filesEl.checked = canFiles;
    if (delEl) {
      delEl.disabled = !canFiles || !cp.complete || Number(counts.added || 0) === 0;
      delEl.checked = !delEl.disabled;
    }
    if (deleteRow) deleteRow.classList.toggle('disabled', !canFiles || !cp.complete);

    const idx = (state.messages || []).findIndex((m) => m.id === (message && message.id));
    const remaining = idx >= 0 ? Math.max(0, (state.messages || []).length - idx) : 0;
    const canConv = cp.message_present !== false && remaining > 0;
    convEl.disabled = !canConv;
    convEl.checked = canConv;

    const filesNote = $('#rewindFilesNote');
    if (filesNote) {
      const changed = Number(counts.added || 0) + Number(counts.modified || 0)
        + Number(counts.deleted || 0);
      filesNote.textContent = !canFiles ? ''
        : (Number(counts.modified || 0) + Number(counts.deleted || 0))
          ? t('rewind.filesNote', {
            count: Number(counts.modified || 0) + Number(counts.deleted || 0),
          })
          : (changed ? t('rewind.noRestore') : t('rewind.clean'));
    }
    const delNote = $('#rewindDeleteNote');
    if (delNote) {
      delNote.textContent = !canFiles ? ''
        : !cp.complete ? t('rewind.deleteAddedNA')
          : t('rewind.deleteNote', { count: Number(counts.added || 0) });
    }
    const convNote = $('#rewindConvNote');
    if (convNote) {
      convNote.textContent = cp.message_present === false
        ? t('rewind.messageGone')
        : t('rewind.convNote', { count: remaining });
    }
    if (noteEl) {
      let note = '';
      if (!diff.available) note = t('rewind.unavailable', { reason: diff.reason || '—' });
      else if (!cp.complete) note = t('rewind.incomplete');
      noteEl.textContent = note;
      noteEl.hidden = !note;
    }
  }

  async function openRewindModal(msgId) {
    if (!state.currentId) return;
    const convId = state.currentId;
    const message = findMessage(msgId);
    if (!message) return;
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/checkpoints`);
      const items = (data && data.items) || [];
      const cp = items.find((c) => String(c.message_id || '') === String(msgId));
      if (!cp) {
        toast(t('rewind.none'), 'bad', 3200);
        return;
      }
      const diff = await api(
        `/conversations/${encodeURIComponent(convId)}/checkpoints/${encodeURIComponent(cp.id)}/diff`);
      if (convId !== state.currentId) return;
      state.rewind = { convId, cp, diff, text: String(message.content || '') };
      renderRewindModal(message);
      $('#rewindModal').hidden = false;
      const btn = $('#rewindConfirm');
      if (btn) btn.focus();
    } catch (err) {
      toast(t('rewind.failed', { msg: localizeServerMessage(err.message) }), 'bad', 5000);
    }
  }

  async function performRewind() {
    const info = state.rewind;
    if (!info || !info.convId) return;
    const filesEl = $('#rewindFiles');
    const delEl = $('#rewindDeleteAdded');
    const convEl = $('#rewindConv');
    const files = !!(filesEl && filesEl.checked && !filesEl.disabled);
    const conversation = !!(convEl && convEl.checked && !convEl.disabled);
    if (!files && !conversation) {
      toast(t('rewind.pickOne'), 'bad', 3000);
      return;
    }
    const convId = info.convId;
    const btn = $('#rewindConfirm');
    if (btn) btn.disabled = true;
    try {
      const data = await api(
        `/conversations/${encodeURIComponent(convId)}/checkpoints/${encodeURIComponent(info.cp.id)}/rewind`,
        {
          method: 'POST',
          body: JSON.stringify({
            files,
            conversation,
            delete_added: !!(delEl && delEl.checked && !delEl.disabled),
          }),
        },
      );
      const modal = $('#rewindModal');
      if (modal) modal.hidden = true;
      state.rewind = null;
      const fr = (data && data.files) || {};
      const cr = (data && data.conversation) || {};
      if (convId === state.currentId) {
        await loadConversation(convId, { silent: true });
        refreshQueue(convId);
        if (state.ws && state.ws.open) loadWorkspace(state.ws.path || '.');
        if (state.editingMsg && !findMessage(state.editingMsg.id)) {
          state.editingMsg = null;
          scheduleRender();
        }
        // 对话回退后把这条消息放回输入框，改完可以直接重发。
        if (conversation && info.text) {
          const input = $('#promptInput');
          if (input && !String(input.value || '').trim()) {
            input.value = info.text;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
          }
        }
      }
      toast(t('rewind.done', {
        files: Number(fr.restored || 0),
        deleted: Number(fr.deleted || 0),
        messages: Number(cr.messages_removed || 0),
      }), 'ok', 5200);
    } catch (err) {
      toast(t('rewind.failed', { msg: localizeServerMessage(err.message) }), 'bad', 5000);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ── conversations ───────────────────────────────────────────────────── */

  function go(hash, replace = false) {
    if (location.hash === hash) return;
    if (replace) history.replaceState(null, '', hash);
    else history.pushState(null, '', hash);
  }

  /* ── 新建会话「单聊 / 群聊」──────────────────────────────────────────
     单聊 = 不绑定团队的普通会话；群聊 = 选已有团队或现场新建一个团队并绑定。
     创建完成后直接进入该会话；团队的管理仍在「会话信息」抽屉里。 */
  let newChatKind = 'single';
  let newChatTeams = [];

  function openNewChatModal() {
    const modal = $('#newChatModal');
    if (!modal) return;
    newChatKind = 'single';
    const box = $('#newChatGroupBox');
    if (box) box.hidden = true;
    const note = $('#newChatNote');
    if (note) { note.textContent = ''; note.className = 'save-note'; }
    const name = $('#newChatNewTeamName');
    if (name) name.value = '';
    const kindBox = $('#newChatKind');
    if (kindBox) {
      for (const btn of kindBox.querySelectorAll('[data-new-chat-kind]')) {
        btn.setAttribute('aria-pressed', String(btn.dataset.newChatKind === 'single'));
      }
    }
    modal.hidden = false;
    loadNewChatTeams();
    setTimeout(() => { const el = $('#newChatCreate'); if (el) el.focus(); }, 30);
  }

  async function loadNewChatTeams() {
    try {
      const data = await api(`/teams?workspace_id=${encodeURIComponent(workspaceId())}`);
      newChatTeams = (data && data.items) || [];
    } catch (err) {
      newChatTeams = [];
    }
    const sel = $('#newChatTeam');
    if (!sel) return;
    sel.innerHTML = newChatTeams.length
      ? newChatTeams.map((x) => `<option value="${escAttr(x.id)}">${escapeHtml(x.name || x.id)}</option>`).join('')
      : `<option value="">${escapeHtml(t('newChat.emptyGroups'))}</option>`;
  }

  function setNewChatKind(kind) {
    newChatKind = kind === 'group' ? 'group' : 'single';
    const box = $('#newChatGroupBox');
    if (box) box.hidden = newChatKind !== 'group';
    const kindBox = $('#newChatKind');
    if (kindBox) {
      for (const btn of kindBox.querySelectorAll('[data-new-chat-kind]')) {
        btn.setAttribute('aria-pressed', String(btn.dataset.newChatKind === newChatKind));
      }
    }
  }

  async function createFromNewChatModal() {
    const wsId = workspaceId();
    const btn = $('#newChatCreate');
    const note = $('#newChatNote');
    if (btn) btn.disabled = true;
    if (note) note.textContent = '';
    try {
      let teamId = '';
      if (newChatKind === 'group') {
        const name = String(($('#newChatNewTeamName') || {}).value || '').trim();
        if (name) {
          const team = await api('/teams', {
            method: 'POST',
            body: JSON.stringify({
              workspace_id: wsId, name, mode: 'orchestrate',
              leader: { name: t('conv.leaderName') },
            }),
          });
          teamId = team.id;
        } else {
          const sel = $('#newChatTeam');
          teamId = String((sel && sel.value) || '');
          if (!teamId) {
            if (note) {
              note.className = 'save-note bad';
              note.textContent = t('newChat.emptyGroups');
            }
            return;
          }
        }
      }
      const conv = await api('/conversations', {
        method: 'POST',
        body: JSON.stringify({ workspace_id: wsId, team_id: teamId || undefined }),
      });
      const modal = $('#newChatModal');
      if (modal) modal.hidden = true;
      await openConversation(conv.id);
      refreshConversations();
    } catch (err) {
      if (note) {
        note.className = 'save-note bad';
        note.textContent = t('newChat.failed', { msg: err.message });
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function wireNewChatModal() {
    const kindBox = $('#newChatKind');
    if (kindBox) {
      kindBox.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-new-chat-kind]');
        if (btn) setNewChatKind(btn.dataset.newChatKind);
      });
    }
    const create = $('#newChatCreate');
    if (create) create.addEventListener('click', () => createFromNewChatModal());
    const modal = $('#newChatModal');
    if (modal) {
      modal.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.target.closest('select')) {
          e.preventDefault();
          createFromNewChatModal();
        }
      });
    }
  }

  function newChat({ updateHash = true } = {}) {
    closeConvDrawer();
    closeStream();
    stopActivityTicker();
    state.loadToken += 1;
    state.currentId = null;
    state.conv = null;
    state.messages = [];
    stepOpen.clear();
    stepClosed.clear();
    state.thinkingLevel = thinkingDefaultForModel(activeModelCode());
    state.activeModel = activeModelCode();
    state.live = null;
    state.run = null;
    state.running = false;
    state.queue = [];
    state.queueRunning = null;
    state.goal = null;
    state.goalDismissed = false;
    state.todos = [];
    setMentionMembers([]);
    closeMentionMenu();
    state.editingMsg = null;
    state.rewind = null;
    clearComposerAttachments();
    clearComposerRefs();
    if (state.queueTimer) { clearTimeout(state.queueTimer); state.queueTimer = null; }
    // 同上：任务清单面板不再被自动关闭，只刷新内容。
    renderQueue();
    renderGoalBar();
    updateTodoBadge();
    if (isTodoDrawerOpen()) renderTodoDrawer();
    state.view = 'empty';
    state.stick = true;
    if (updateHash) go('#/new');
    renderConversations();
    scheduleRender();
    setTimeout(() => $('#promptInput').focus(), 30);
  }

  async function createConversation() {
    const data = await api('/conversations', {
      method: 'POST',
      body: JSON.stringify({
        workspace_id: workspaceId(),
        model_code: activeModelCode(),
        thinking_level: state.thinkingLevel || '',
        permission_mode: state.permMode,
        run_mode: state.mode,
      }),
    });
    state.currentId = data.id;
    state.conv = data;
    applyPermMode(data.permission_mode);
    state.mode = data.run_mode === 'goal' ? 'goal' : 'autonomous';
    if (!state.thinkingLevel) state.thinkingLevel = thinkingDefaultForModel(activeModelCode());
    renderHeader();
    go(`#/c/${encodeURIComponent(data.id)}`);
    state.view = 'chat';
    refreshConversations();
    return data.id;
  }

  function applyConversationPrefs(conv) {
    state.thinkingLevel = String((conv && conv.thinking_level) || '');
    state.activeModel = activeModelCode();
  }

  async function saveConversationPrefs(patch) {
    if (!state.currentId) return;
    try {
      const data = await api(`/conversations/${encodeURIComponent(state.currentId)}/preferences`, {
        method: 'PUT', body: JSON.stringify(patch),
      });
      if (state.conv && data) {
        if (data.model_code !== undefined) state.conv.model_code = data.model_code;
        if (data.thinking_level !== undefined) state.conv.thinking_level = data.thinking_level;
      }
    } catch (err) {
      console.warn('save conversation preferences failed', err);
    }
  }

  async function openConversation(id) {
    if (!id) { newChat(); return; }
    if (state.currentId === id && state.view === 'chat' && !state.live) {
      return;
    }
    closeConvDrawer();
    closeStream();
    stopActivityTicker();
    const token = ++state.loadToken;
    state.currentId = id;
    state.pendingQuestion = null;
    renderAskUser();
    state.pendingApprovals = [];
    renderApprovalBar();
    state.conv = null;
    state.messages = [];
    stepOpen.clear();
    stepClosed.clear();
    state.live = null;
    state.run = null;
    state.running = false;
    state.queue = [];
    state.queueRunning = null;
    state.goal = null;
    state.goalDismissed = false;
    state.todos = [];
    setMentionMembers([]);
    closeMentionMenu();
    state.editingMsg = null;
    state.rewind = null;
    clearComposerAttachments();
    clearComposerRefs();
    if (state.queueTimer) { clearTimeout(state.queueTimer); state.queueTimer = null; }
    renderQueue();
    renderGoalBar();
    updateTodoBadge();
    state.view = 'loading';
    state.stick = true;
    go(`#/c/${encodeURIComponent(id)}`);
    renderConversations();
    scheduleRender();

    try {
      const conv = await api(`/conversations/${encodeURIComponent(id)}`);
      if (token !== state.loadToken) return;
      if (conv.workspace_id && conv.workspace_id !== workspaceId()) {
        let ws = state.workspacesAll.find((w) => w.id === conv.workspace_id)
          || state.workspaces.find((w) => w.id === conv.workspace_id);
        if (!ws) {
          try {
            ws = await api(`/workspaces/${encodeURIComponent(conv.workspace_id)}`);
          } catch (err) {
            console.warn('conversation workspace lookup failed', err);
          }
        }
        if (ws) {
          state.filter = '';
          const search = $('#searchInput');
          if (search) search.value = '';
          applyWorkspace(ws);
        }
      }
      state.conv = conv;
      applyConversationPrefs(conv);
      applyPermMode(conv.permission_mode);
      state.mode = conv.run_mode === 'goal' ? 'goal' : 'autonomous';
      renderHeader();
      state.messages = Array.isArray(conv.messages) ? conv.messages : [];
      state.view = 'chat';
      scheduleRender();
      await markConversationRead(id, { render: false });
      loadConvTeamMembers(id);
      refreshConversations();
      await Promise.all([refreshQueue(id), refreshGoal(id), refreshTodos(id)]);
      await loadPendingQuestions(id);
      await resumeActiveRun(id, token);
      await loadPendingApprovals(id);
    } catch (err) {
      if (token !== state.loadToken) return;
      state.view = 'empty';
      scheduleRender();
      toast(t('error.openConversation', { msg: err.message }), 'bad', 6000);
    }
  }

  async function loadConversation(id, { silent = false } = {}) {
    const token = silent ? state.loadToken : ++state.loadToken;
    const conv = await api(`/conversations/${encodeURIComponent(id)}`);
    if (token !== state.loadToken) return;
    state.conv = conv;
    applyConversationPrefs(conv);
    state.messages = Array.isArray(conv.messages) ? conv.messages : [];
    state.convUsage = conv.usage_total || null;
    state.ctxTail = 0;          // 库里的真实值接管，丢掉上一轮残留的估算
    state.pendingQuestion = null;
    renderAskUser();
    loadPendingQuestions(id);
    loadPendingApprovals(id);
    refreshQueue(id);
    refreshGoal(id);
    refreshTodos(id);
    if (!silent) state.view = 'chat';
    scheduleRender();
  }

  async function resumeActiveRun(convId, token) {
    if (token !== state.loadToken || convId !== state.currentId) return;
    // 队列模式：优先接管正在执行的那条；否则接管队首（下一条会执行）
    const active = state.queueRunning || (state.queue || [])[0];
    if (active) startRun(active, true);
  }

  let confirmResolver = null;
  let confirmPreviousFocus = null;

  function closeConfirm(result) {
    const modal = $('#confirmModal');
    if (modal) modal.hidden = true;
    if (confirmPreviousFocus && typeof confirmPreviousFocus.focus === 'function') {
      try { confirmPreviousFocus.focus(); } catch { /* ignore */ }
    }
    confirmPreviousFocus = null;
    if (confirmResolver) {
      const resolve = confirmResolver;
      confirmResolver = null;
      resolve(result);
    }
  }

  function openConfirm({ title, message, okText, cancelText = '', danger = false }) {
    const modal = $('#confirmModal');
    if (!modal) return Promise.resolve(false);
    // 已有的确认框被新请求覆盖时，旧请求按取消处理，避免 Promise 永远挂起。
    if (confirmResolver) {
      const previous = confirmResolver;
      confirmResolver = null;
      previous(false);
    }
    $('#confirmTitle').textContent = title || t('confirm.title');
    $('#confirmMessage').textContent = message || '';
    const ok = $('#confirmOkBtn');
    ok.textContent = okText || t('confirm.ok');
    ok.className = `btn ${danger ? 'danger' : 'primary'}`;
    // 自定义取消按钮文案时摘掉 data-i18n，避免切语言把它覆盖回「取消」。
    const cancel = modal.querySelector('.modal-buttons [data-confirm-cancel].btn');
    if (cancel) {
      if (cancelText) {
        cancel.textContent = cancelText;
        cancel.removeAttribute('data-i18n');
      } else {
        cancel.textContent = t('cancel');
        cancel.setAttribute('data-i18n', 'cancel');
      }
    }
    confirmPreviousFocus = document.activeElement;
    modal.hidden = false;
    setTimeout(() => ok.focus(), 30);
    return new Promise((resolve) => { confirmResolver = resolve; });
  }

  async function deleteConversation(id) {
    const conv = state.conversations.find((c) => c.id === id)
      || (state.picker.items || []).find((c) => c.id === id);
    const title = conv ? conversationTitle(conv) : t('delete.thisConversation');
    const confirmed = await openConfirm({
      title: t('delete.title'),
      message: t('delete.confirm', { title: truncate(title, 70) }),
      okText: t('delete.action'),
      danger: true,
    });
    if (!confirmed) return;
    try {
      await api(`/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
      toast(t('delete.done'), 'ok', 2200);
      if (state.currentId === id) newChat();
      await refreshConversations();
      if (state.picker.open) await loadConvPicker(state.picker.page);
    } catch (err) {
      toast(t('delete.failed', { msg: err.message }), 'bad');
    }
  }

  // 一键删除聊天区的全部对话（当前聊天工作区；纯聊天工作区就是它自己）。
  // 列表是分页的，前端只有一页的 id，所以由后端按工作区取全量并逐条删。
  async function deleteAllChatConversations() {
    const ws = chatWorkspace() || currentWorkspace();
    if (!ws) return;
    const total = Math.max(Number(state.chatTotal) || 0, state.chatItems.length);
    if (!total) {
      toast(t('chat.clearNone'), 'ok', 2600);
      return;
    }
    const ok = await openConfirm({
      title: t('chat.clearTitle'),
      message: t('chat.clearConfirm', { n: total, ws: ws.name || ws.id }),
      okText: t('delete.action'),
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await api(`/conversations?workspace_id=${encodeURIComponent(ws.id)}`
        + '&include_archived=true&confirm=true', { method: 'DELETE' });
      const n = res && res.deleted !== undefined ? res.deleted : total;
      toast(t('chat.cleared', { n }), 'ok', 2600);
      const curWs = (state.conv && state.conv.workspace_id) || workspaceId();
      if (state.currentId && curWs === ws.id) newChat();
      await Promise.all([loadChatTop(), refreshConversations()]);
      if (state.picker.open) await loadConvPicker(state.picker.page);
    } catch (err) {
      toast(t('delete.failed', { msg: err.message }), 'bad');
    }
  }

  /* ── send ────────────────────────────────────────────────────────────── */

  /* ── composer attachments ─────────────────────────────────────────────── */

  function activeModelCode() {
    const convModel = state.conv && String(state.conv.model_code || '').trim();
    if (convModel) return convModel;
    const llm = (state.app && state.app.llm) || {};
    return llm.active_model || state.activeModel || 'default';
  }

  function activeModelProfile() {
    const llm = (state.app && state.app.llm) || {};
    const code = activeModelCode();
    return (state.models || []).find((m) => m.code === code)
      || (llm.models || []).find((m) => m.code === code)
      || null;
  }

  function activeVision() {
    const profile = activeModelProfile();
    if (profile) {
      if (typeof profile.effective_vision === 'boolean') return profile.effective_vision;
      if (typeof profile.vision === 'boolean') return profile.vision;
    }
    return !!(state.app && state.app.llm && state.app.llm.vision);
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

  function syncAttachAvailability() {
    const btn = $('#attachBtn');
    const input = $('#attachInput');
    if (!btn) return;
    const vision = activeVision();
    btn.classList.toggle('vision', vision);
    btn.title = t(vision ? 'attach.titleVision' : 'attach.title');
    btn.setAttribute('aria-label', btn.title);
    btn.setAttribute('data-vision', vision ? '1' : '0');
    if (input) input.accept = vision ? `${DOC_ACCEPT},${IMAGE_ACCEPT}` : DOC_ACCEPT;
  }

  function renderComposerAttachments() {
    const box = $('#composerAttachments');
    if (!box) return;
    const list = state.attachments || [];
    if (!list.length) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    box.hidden = false;
    box.innerHTML = list.map((a) => {
      const preview = a.preview
        ? `<img src="${escAttr(a.preview)}" alt="" />`
        : `<svg><use href="#i-${a.kind === 'image' ? 'image' : 'file'}"/></svg>`;
      return `<div class="composer-attachment${a.kind === 'image' ? ' is-image' : ''}" data-att-id="${escAttr(a.localId)}">
        <span class="composer-attachment-thumb">${preview}</span>
        <span class="composer-attachment-main">
          <span class="composer-attachment-name" title="${escAttr(a.name)}">${escapeHtml(a.name)}</span>
          <span class="composer-attachment-meta">${escapeHtml(bytes(a.size))}${a.converted ? ` · ${escapeHtml(t('attach.convertedShort'))}` : ''}</span>
        </span>
        <button type="button" class="icon-btn composer-attachment-remove" data-att-remove="${escAttr(a.localId)}" title="${escAttr(t('attach.remove'))}" aria-label="${escAttr(t('attach.remove'))}"><svg><use href="#i-x"/></svg></button>
      </div>`;
    }).join('');
  }

  function renderComposerRefs() {
    const box = $('#composerRefs');
    if (!box) return;
    const list = state.refs || [];
    if (!list.length) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    box.hidden = false;
    box.innerHTML = list.map((path) => {
      const isDir = String(path).endsWith('/');
      const label = isDir ? String(path).replace(/\/+$/, '') : String(path);
      return `<span class="composer-ref" title="${escAttr(path)}">
        <svg><use href="#i-${isDir ? 'folder' : 'file'}"/></svg>
        <span class="composer-ref-path">${escapeHtml(label)}</span>
        <button type="button" class="icon-btn composer-ref-remove" data-ref-remove="${escAttr(path)}" title="${escAttr(t('ref.remove'))}" aria-label="${escAttr(t('ref.remove'))}"><svg><use href="#i-x"/></svg></button>
      </span>`;
    }).join('');
  }

  function addComposerRef(path) {
    const value = String(path || '').trim();
    if (!value) return;
    if ((state.refs || []).includes(value)) {
      toast(t('ref.duplicate', { name: value }), 'warn', 2600);
      return;
    }
    state.refs.push(value);
    renderComposerRefs();
    toast(t('ref.added', { name: value }), 'ok', 2000);
  }

  function removeComposerRef(path) {
    state.refs = (state.refs || []).filter((p) => p !== path);
    renderComposerRefs();
  }

  function clearComposerRefs() {
    state.refs = [];
    renderComposerRefs();
  }

  function thinkingLevelLabel(level) {
    const key = `thinking.${String(level || '').toLowerCase()}`;
    const label = t(key);
    return label === key ? String(level || '') : label;
  }

  function activeThinkingLevels() {
    const profile = activeModelProfile();
    let levels = [];
    if (profile) {
      levels = profile.effective_thinking_levels || profile.thinking_levels || [];
    }
    if (!Array.isArray(levels) || !levels.length) {
      levels = (state.app && state.app.llm && state.app.llm.thinking_levels) || [];
    }
    return Array.isArray(levels) ? levels.filter(Boolean) : [];
  }

  function thinkingDefaultForModel(code) {
    try {
      return localStorage.getItem(`az-thinking:${code || 'default'}`) || '';
    } catch {
      return '';
    }
  }

  function rememberThinkingLevel(code, level) {
    try {
      localStorage.setItem(`az-thinking:${code || 'default'}`, level || '');
    } catch { /* ignore */ }
  }

  function updateThinkingChip() {
    const chip = $('#thinkingChip');
    if (!chip) return;
    const levels = activeThinkingLevels();
    if (!levels.length) {
      chip.hidden = true;
      return;
    }
    const label = state.thinkingLevel
      ? thinkingLevelLabel(state.thinkingLevel)
      : t('thinking.default');
    const title = t('thinking.chipTitle', { level: label });
    const text = $('#thinkingChipText');
    if (text) text.textContent = label;
    chip.hidden = false;
    chip.title = title;
    chip.setAttribute('aria-label', title);
  }

  function syncThinkingLevels() {
    const levels = activeThinkingLevels();
    if (!levels.length) {
      state.thinkingLevel = '';
    } else {
      if (state.thinkingLevel && !levels.includes(state.thinkingLevel)) {
        const fallback = thinkingDefaultForModel(activeModelCode());
        state.thinkingLevel = levels.includes(fallback) ? fallback : '';
      }
      if (!state.currentId && !state.thinkingLevel) {
        const saved = thinkingDefaultForModel(activeModelCode());
        if (levels.includes(saved)) state.thinkingLevel = saved;
      }
    }
    const menu = $('#modelMenu');
    if (menu && !menu.hidden) renderModelMenu();
    renderThinkingMenu();
    updateThinkingChip();
  }

  function setThinkingLevel(level) {
    const levels = activeThinkingLevels();
    state.thinkingLevel = levels.includes(level) ? level : '';
    const code = activeModelCode();
    rememberThinkingLevel(code, state.thinkingLevel);
    if (state.currentId) saveConversationPrefs({ thinking_level: state.thinkingLevel });
    syncThinkingLevels();
  }

  function clearComposerAttachments() {
    for (const a of state.attachments || []) {
      if (a.preview && !a.keepPreview) URL.revokeObjectURL(a.preview);
    }
    state.attachments = [];
    renderComposerAttachments();
  }

  function removeComposerAttachment(localId) {
    const att = (state.attachments || []).find((a) => a.localId === localId);
    if (att && att.preview) URL.revokeObjectURL(att.preview);
    state.attachments = (state.attachments || []).filter((a) => a.localId !== localId);
    renderComposerAttachments();
  }

  function addComposerFiles(files) {
    const incoming = Array.from(files || []);
    if (!incoming.length) return;
    const vision = activeVision();
    let total = (state.attachments || []).reduce((n, a) => n + Number(a.size || 0), 0);
    for (const file of incoming) {
      if ((state.attachments || []).length >= MAX_ATTACHMENTS) {
        toast(t('attach.tooMany', { n: MAX_ATTACHMENTS }), 'bad', 4000);
        break;
      }
      const image = isImageFile(file);
      if (image && !vision) {
        toast(t('attach.imageNeedsVision'), 'bad', 5200);
        continue;
      }
      if (!image && !isDocumentFile(file)) {
        toast(t('attach.unsupported', { name: file.name }), 'bad', 4000);
        continue;
      }
      if (file.size > MAX_ATTACHMENT_BYTES) {
        toast(t('attach.tooLarge', { name: file.name }), 'bad', 4200);
        continue;
      }
      if (total + file.size > MAX_ATTACHMENTS_TOTAL) {
        toast(t('attach.totalLarge'), 'bad', 4200);
        break;
      }
      total += file.size;
      state.attachments.push({
        localId: `att-local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: file.name,
        mime: file.type || '',
        size: file.size,
        kind: image ? 'image' : 'file',
        file,
        preview: image ? URL.createObjectURL(file) : '',
      });
    }
    renderComposerAttachments();
  }

  function addConvertedTextAttachment(text) {
    const stamp = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const name = `message-${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}`
      + `-${pad(stamp.getHours())}${pad(stamp.getMinutes())}${pad(stamp.getSeconds())}.txt`;
    const data = new TextEncoder().encode(text);
    const localId = `att-local-${Date.now()}-txt`;
    state.attachments.push({
      localId,
      name,
      mime: 'text/plain',
      size: data.byteLength,
      kind: 'file',
      converted: true,
      textData: text,
    });
    renderComposerAttachments();
    return { name, localId };
  }

  function updateLongTextHint() {
    const input = $('#promptInput');
    const hint = $('#longTextHint');
    if (!input || !hint) return;
    const n = input.value.length;
    hint.hidden = n <= LONG_TEXT_CONFIRM_CHARS;
    if (!hint.hidden) hint.textContent = t('attach.longHint', { n });
  }

  async function attachmentPayloads(list) {
    const out = [];
    for (const a of list || []) {
      let data = '';
      if (a.file) {
        const buffer = await a.file.arrayBuffer();
        data = bytesToBase64(buffer);
      } else if (a.textData != null) {
        data = bytesToBase64(new TextEncoder().encode(a.textData).buffer);
      }
      if (!data) continue;
      out.push({ name: a.name, mime: a.mime || '', size: a.size || 0, data });
    }
    return out;
  }

  // 发送去重：同一条内容在极短时间内（双击 / 长按后浏览器补发 click）只提交一次，
  // 避免重复发出两条消息。
  let lastSubmitKey = '';
  let lastSubmitAt = 0;
  const SUBMIT_DEDUPE_MS = 600;

  // opts.force：用户用双击 / 长按发送按钮（或 IME 冷却期内的第二次 Enter）明确表示"就发这个"，
  // 此时跳过长文本转 txt 的确认，按原样发送全文。
  // 从正文里解析 @名字（圆桌点名）；后端同时也会按内容 @ 兜底，这里显式传，
  // 让 mentions 落库、队列重放时仍然知道用户点了谁。
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

  async function sendMessage(opts = {}) {
    const force = !!opts.force;
    const input = $('#promptInput');
    const originalText = input.value.trim();
    let text = originalText;
    let attachments = (state.attachments || []).slice();

    if (originalText === '/help') {
      updateSlashMenu(true);
      input.focus();
      return;
    }

    if (!text && !attachments.length && !(state.refs || []).length) { input.focus(); return; }

    // 长文本：先询问是否转为 txt 文档附件（附件里的全文仍会进入模型上下文）。
    // 转换后**只转换、不发送**：把预览留在输入框，等用户核对附件后再按一次 Enter / 点发送。
    if (!force
        && text.length > LONG_TEXT_CONFIRM_CHARS
        && !attachments.some((a) => a.converted)) {
      const ok = await openConfirm({
        title: t('attach.longConfirmTitle'),
        message: t('attach.longConfirm', { n: text.length }),
        okText: t('attach.longConfirmOk'),
        cancelText: t('attach.longConfirmCancel'),
      });
      if (ok) {
        const conv = addConvertedTextAttachment(text);
        const preview = text.slice(0, 160);
        text = `${preview}${text.length > 160 ? '…' : ''}\n\n`
          + t('attach.fullInFile', { name: conv.name });
        input.value = text;
        autoGrow(input);
        updateLongTextHint();
        toast(t('attach.convertedReady', { name: conv.name }), 'ok', 4200);
        input.focus();
        return;  // 交回用户确认，不自动发出
      }
      // 保持原文：不转换，按原样发送全文（避免再次 Enter 又弹同一个框）
    }
    // 去重：双击 / 长按后浏览器可能补发 click，或 Enter 与 click 同时触发，
    // 同一份内容（文本 + 附件 + 引用）在 600ms 内只提交一次。
    const submitKey = `${text}|${attachments.map((a) => a.localId).join(',')}|${(state.refs || []).join(',')}`;
    const nowTs = Date.now();
    if (submitKey === lastSubmitKey && nowTs - lastSubmitAt < SUBMIT_DEDUPE_MS) return;
    lastSubmitKey = submitKey;
    lastSubmitAt = nowTs;

    if (!activeVision() && attachments.some((a) => a.kind === 'image')) {
      toast(t('attach.imageNeedsVision'), 'bad', 5200);
      return;
    }
    if (!text && attachments.length) text = t('attach.only');
    if (!text && (state.refs || []).length) text = t('ref.only');
    const hasConverted = attachments.some((a) => a.converted);

    let convId = state.currentId;
    try {
      if (!convId) convId = await createConversation();
    } catch (err) {
      toast(t('error.startConversation', { msg: err.message }), 'bad');
      return;
    }

    const optimisticAttachments = attachments.map((a) => ({
      id: a.localId,
      name: a.name,
      mime: a.mime || '',
      size: a.size || 0,
      kind: a.kind === 'image' ? 'image' : 'file',
      url: a.preview || '',
    }));

    input.value = '';
    autoGrow(input);
    updateLongTextHint();
    closeSlashMenu();
    $('#sendBtn').disabled = true;
    const optimistic = {
      id: `local-${Date.now()}`,
      by: 'local',
      content: text,
      refs: (state.refs || []).slice(),
      attachments: optimisticAttachments,
      ts: localStamp(),
      // 正在跑的时候发出去 = 会进队列：先按排队隐藏，别让它闪一下「已发送」
      pendingQueue: !!state.running,
    };
    state.messages.push(optimistic);
    state.stick = true;
    scheduleRender();

    try {
      const payloadAttachments = await attachmentPayloads(attachments);
      const run = await api(`/conversations/${encodeURIComponent(convId)}/messages/run`, {
        method: 'POST',
        body: JSON.stringify({
          content: text,
          mode: state.mode,
          auto_execute: state.permMode === 'always',
          refs: state.refs || [],
          thinking_level: state.thinkingLevel || '',
          attachments: payloadAttachments,
          mentions: mentionedNames(text),
        }),
      });
      if (convId !== state.currentId) return;
      // 换成服务端 id（见 adoptSentMessageId）：条数没变，显式作废转录签名，
      // 否则已渲染的「live 与排队消息」顺序不会重排。
      if (adoptSentMessageId(optimistic, run)) {
        transcriptSig = '';
        scheduleRender();
      }
      // 乐观消息里的图片仍引用 object URL：保留它直到本次页面刷新，避免刚发送就裂图。
      for (const a of attachments) {
        if (a.preview) a.keepPreview = true;
      }
      clearComposerAttachments();
      clearComposerRefs();
      const otherRunning = !!run.running_run_id && run.running_run_id !== run.id;
      if (state.running && otherRunning) {
        // 队列模式：当前回复继续跑，这条排进队列
        state.queue = run.queue || state.queue;
        renderQueue();
        const pos = run.queue_position || 1;
        toast(t('queue.queued', { n: pos }), 'ok', 2800);
      } else {
        startRun(run, false);
      }
      // 队列判定已交给服务端（queued_msg_id / state.queue）：撤掉本地乐观隐藏。
      // 真排队的话 state.queue 里已经有它，isQueuedUserMessage 会继续把它挡在
      // 转录外；没排队（这条直接开跑）则立刻作为正常用户消息显示。
      optimistic.pendingQueue = false;
      if (hasConverted) toast(t('attach.converted'), 'ok', 2600);
      if (run.goal_started) refreshGoal(convId);
      refreshQueue(convId);
    } catch (err) {
      toast(t('error.send', { msg: err.message }), 'bad', 6000);
      // Remove the optimistic row only when the server rejected the message outright.
      state.messages = state.messages.filter((m) => !String(m.id).startsWith('local-'));
      input.value = originalText;
      autoGrow(input);
      updateLongTextHint();
      scheduleRender();
    } finally {
      $('#sendBtn').disabled = false;
      input.focus();
    }
  }

  // 发送按钮：单击 = 普通发送（长文本仍会先问要不要转 txt）；
  // 双击 / 长按 = 强制直接发送（跳过长文本确认，按原样发出全文）。
  // 长按期间按钮上显示进度条，松手未达阈值则退化为单击。
  function bindSendButton() {
    const btn = $('#sendBtn');
    if (!btn) return;
    const LONG_PRESS_MS = 1200;
    const DOUBLE_MS = 320;
    let pressTimer = null;
    let pressStart = 0;
    let longFired = false;
    let clickTimer = null;
    let suppressClicksUntil = 0;
    let raf = 0;

    const clearPressVisual = () => {
      btn.classList.remove('pressing');
      btn.style.removeProperty('--press');
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    };
    const stopPress = () => {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
      clearPressVisual();
    };
    const tick = () => {
      const pct = Math.min(1, (Date.now() - pressStart) / LONG_PRESS_MS);
      btn.style.setProperty('--press', pct.toFixed(3));
      if (pct < 1) raf = requestAnimationFrame(tick);
    };

    btn.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || btn.disabled) return;
      longFired = false;
      pressStart = Date.now();
      btn.classList.add('pressing');
      tick();
      pressTimer = setTimeout(() => {
        pressTimer = null;
        longFired = true;
        clearPressVisual();
        if (navigator.vibrate) { try { navigator.vibrate(18); } catch (_) { /* 忽略 */ } }
        sendMessage({ force: true });
      }, LONG_PRESS_MS);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach((evt) => {
      btn.addEventListener(evt, stopPress);
    });
    // 长按触发后浏览器还会补一个 click，这里吃掉，避免重复发送
    btn.addEventListener('click', () => {
      if (longFired) { longFired = false; return; }
      const now = Date.now();
      // 普通长度的消息：立即发送，并吞掉紧随其后的第二下（避免双击发出两条）
      const input = $('#promptInput');
      const longText = !!(input && input.value.trim().length > LONG_TEXT_CONFIRM_CHARS)
        && !(state.attachments || []).some((a) => a.converted);
      if (!longText) {
        if (now < suppressClicksUntil) return;
        suppressClicksUntil = now + 420;
        sendMessage();
        return;
      }
      // 长文本：延迟一个双击窗口再发，第二下可升级为「强制发送」（跳过转 txt 询问、原样发出）
      if (clickTimer) {
        clearTimeout(clickTimer);
        clickTimer = null;
        sendMessage({ force: true });
        return;
      }
      clickTimer = setTimeout(() => {
        clickTimer = null;
        sendMessage();
      }, DOUBLE_MS);
    });
  }

  async function decideProposal(action, proposalId, turnId) {
    if (!state.currentId || !proposalId) return;
    try {
      if (action === 'dismiss') {
        await api(`/conversations/${encodeURIComponent(state.currentId)}/proposals/${encodeURIComponent(proposalId)}`, {
          method: 'POST', body: JSON.stringify({ action: 'dismiss' }),
        });
        toast(t('proposal.dismissed'), 'ok', 2200);
        patchProposal(turnId, proposalId, 'dismissed');
      } else {
        const run = await api(`/conversations/${encodeURIComponent(state.currentId)}/proposals/${encodeURIComponent(proposalId)}`, {
          method: 'POST', body: JSON.stringify({ action: 'accept' }),
        });
        patchProposal(turnId, proposalId, 'accepted');
        toast(t('proposal.started'), 'ok', 2600);
        // 已有运行在流式输出：新 run 进队列，不打断当前视图
        if (state.running && state.run && state.run.id !== run.id) {
          refreshQueue(state.currentId);
        } else {
          startRun(run, false);
        }
      }
      scheduleRender();
    } catch (err) {
      toast(err.message, 'bad', 6000);
    }
  }

  function patchProposal(turnId, proposalId, status) {
    const patch = (msg) => {
      for (const s of msg.trace || []) {
        if (s.kind === 'proposal' && (s.proposal_id === proposalId || !proposalId)) s.status = status;
      }
    };
    for (const m of state.messages) {
      if (!turnId || m.id === turnId) patch(m);
    }
    if (state.live) patch(state.live);
  }

  /* ── 危险操作审批（底部固定条） ──────────────────────────────────────── */

  function applySubagentEvent(ev) {
    if (!ev || !ev.call_id) return;
    let item = state.subagents.get(ev.call_id);
    if (!item) {
      item = {
        call_id: ev.call_id,
        agent: ev.agent || 'Subagent',
        goal: ev.goal || '',
        status: 'running',
        startedAt: Date.now(),
        tools: [],
        current: '',
        text: '',
        error: '',
      };
      state.subagents.set(ev.call_id, item);
    }
    if (ev.agent) item.agent = ev.agent;
    if (ev.goal) item.goal = ev.goal;
    if (ev.event === 'tool_call' || ev.event === 'tool_result') {
      const t = ev.tool || {};
      const key = t.id || t.name || 'tool';
      let row = item.tools.find((x) => x.key === key);
      if (!row) {
        row = { key, name: t.name || key, status: 'running', summary: '' };
        item.tools.push(row);
      }
      row.name = t.name || row.name;
      row.status = ev.event === 'tool_result' ? 'done' : 'running';
      row.summary = t.summary || row.summary || '';
      item.current = row.status === 'running' ? row.name : '';
    } else if (ev.event === 'progress') {
      const piece = String(ev.text || '');
      if (piece) item.text = (item.text + piece).slice(-4000);
    } else if (ev.event === 'error') {
      item.status = 'failed';
      item.error = String(ev.error || '');
      item.current = '';
    } else if (ev.event === 'done') {
      item.status = 'done';
      item.current = '';
    }
    renderSubagents();
  }

  function subagentElapsed(item) {
    return Math.max(0, Math.round((Date.now() - item.startedAt) / 1000));
  }

  function renderSubagents() {
    const bar = $('#subagentBar');
    if (!bar) return;
    const items = Array.from(state.subagents.values());
    if (!items.length) {
      bar.hidden = true;
      bar.innerHTML = '';
      renderSubagentDetail();
      return;
    }
    bar.hidden = false;
    bar.innerHTML = items.map((it) => {
      const tone = it.status === 'failed' ? ' is-failed' : it.status === 'done' ? ' is-done' : '';
      const step = it.status === 'running'
        ? (it.current ? `· ${it.current}` : `· ${t('subagent.running')}`)
        : `· ${t(it.status === 'failed' ? 'subagent.failed' : 'subagent.done')}`;
      const secs = subagentElapsed(it);
      return `<button type="button" class="subagent-chip${tone}" data-subagent="${escAttr(it.call_id)}" title="${escAttr(it.goal || '')}">
        <span class="sa-dot"></span>
        <span class="sa-name">${escapeHtml(it.agent)}</span>
        <span class="sa-step">${escapeHtml(step)}</span>
        <span class="sa-time">${secs}s</span>
      </button>`;
    }).join('');
    if (!bar.dataset.bound) {
      bar.dataset.bound = '1';
      bar.addEventListener('click', (e) => {
        const chip = e.target.closest('[data-subagent]');
        if (!chip) return;
        state.subagentOpen = chip.dataset.subagent;
        renderSubagentDetail();
      });
    }
    if (state.subagentOpen && !state.subagents.has(state.subagentOpen)) {
      state.subagentOpen = null;
    }
    renderSubagentDetail();
  }

  function renderSubagentDetail() {
    let host = $('#subagentModal');
    const id = state.subagentOpen;
    const item = id ? state.subagents.get(id) : null;
    if (!item) {
      if (host) host.remove();
      return;
    }
    if (!host) {
      host = document.createElement('div');
      host.id = 'subagentModal';
      host.className = 'modal-backdrop';
      host.addEventListener('click', (e) => {
        if (e.target === host) {
          state.subagentOpen = null;
          renderSubagentDetail();
        }
      });
      document.body.appendChild(host);
    }
    const steps = (item.tools || []).map((x) =>
      `<div class="sa-step-row"><span class="sa-dot" style="width:6px;height:6px;border-radius:50%;background:${x.status === 'done' ? 'var(--success)' : 'var(--info)'}"></span><span class="sa-tool">${escapeHtml(x.name)}</span><span class="oc-muted">${escapeHtml(x.summary || x.status)}</span></div>`).join('');
    host.innerHTML = `<div class="modal ask-modal subagent-detail" role="dialog" aria-modal="true" aria-labelledby="subagentTitle">
      <div class="modal-head"><div><h2 id="subagentTitle">${escapeHtml(t('subagent.title'))} · ${escapeHtml(item.agent)}</h2>
        <p>${escapeHtml(t(item.status === 'failed' ? 'subagent.failed' : item.status === 'done' ? 'subagent.done' : 'subagent.running'))} · ${subagentElapsed(item)}s</p></div>
        <button class="icon-btn" type="button" id="subagentClose" aria-label="${escAttr(t('close'))}"><svg><use href="#i-x"/></svg></button>
      </div>
      <div class="modal-body">
        <div class="sa-goal"><span class="sa-label">${escapeHtml(t('subagent.goal'))}</span>：${escapeHtml(item.goal || '—')}</div>
        <div class="sa-steps">${steps || `<div class="oc-muted">${escapeHtml(t('subagent.noSteps'))}</div>`}</div>
        ${item.text ? `<div class="sa-preview">${escapeHtml(item.text)}</div>` : ''}
        ${item.error ? `<div class="error-box"><svg><use href="#i-alert"/></svg><span>${escapeHtml(item.error)}</span></div>` : ''}
      </div>
    </div>`;
    const close = $('#subagentClose');
    if (close) close.addEventListener('click', () => {
      state.subagentOpen = null;
      renderSubagentDetail();
    });
  }

  function showAskUser(question) {
    if (!question || !question.id) return;
    const convId = state.currentId || '';
    if (convId) state.pendingQuestions[convId] = question;
    if (state.pendingQuestion && state.pendingQuestion.id === question.id) return;
    state.pendingQuestion = {
      ...question,
      convId,
      selected: new Set(),
      other: '',
      otherOn: false,
    };
    renderAskUser();
    refreshActiveRuns();
  }

  function hideAskUserIf(id) {
    for (const convId of Object.keys(state.pendingQuestions)) {
      if (state.pendingQuestions[convId] && state.pendingQuestions[convId].id === id) {
        delete state.pendingQuestions[convId];
      }
    }
    if (state.pendingQuestion && state.pendingQuestion.id === id) {
      state.pendingQuestion = null;
      renderAskUser();
      refreshActiveRuns();
    }
  }

  async function loadPendingQuestions(convId) {
    if (!convId) return;
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/questions`);
      if (convId !== state.currentId) return;
      const items = (data && data.items) || [];
      const q = items[items.length - 1] || null;
      if (q) {
        state.pendingQuestions[convId] = q;
        showAskUser(q);
        return;
      }
      delete state.pendingQuestions[convId];
      if (state.pendingQuestion && state.pendingQuestion.convId === convId) {
        state.pendingQuestion = null;
        renderAskUser();
        refreshActiveRuns();
      }
    } catch (err) {
      /* 拉取失败不影响当前会话，等 SSE 事件或超时兜底 */
    }
  }

  function normalizePendingApproval(a) {
    let reasons = a && a.reasons;
    if (typeof reasons === 'string') {
      try { reasons = JSON.parse(reasons); } catch { reasons = []; }
    }
    return {
      id: a && a.id,
      tool: (a && a.tool) || '',
      risk: (a && a.risk) || 'medium',
      reasons: Array.isArray(reasons) ? reasons : [],
      note: (a && a.note) || '',
      status: 'pending',
    };
  }

  async function loadPendingApprovals(convId) {
    if (!convId) return;
    try {
      const data = await api(
        `/conversations/${encodeURIComponent(convId)}/approvals?status=pending`);
      if (convId !== state.currentId) return;
      const items = (data && data.items) || [];
      state.pendingApprovals = items.map(normalizePendingApproval);
      renderApprovalBar();
    } catch (err) {
      /* 拉取失败不影响当前会话，等 SSE 事件或超时兜底 */
    }
  }

  function renderAskUser() {
    const host = $('#askDrawer');
    const q = state.pendingQuestion;
    if (!host) return;
    if (!q) {
      host.hidden = true;
      host.innerHTML = '';
      return;
    }
    host.hidden = false;
    const options = (q.options || []).map((o) => {
      const label = typeof o === 'string' ? o : (o.label || '');
      const desc = typeof o === 'string' ? '' : (o.description || '');
      const on = q.selected.has(label);
      return `<button type="button" class="ask-option${on ? ' is-on' : ''}"
        data-ask-option="${escAttr(label)}" aria-pressed="${on}">
        <span class="ask-option-label">${escapeHtml(label)}</span>
        ${desc ? `<span class="ask-option-desc">${escapeHtml(desc)}</span>` : ''}
      </button>`;
    }).join('');
    host.innerHTML = `
      <div class="ask-drawer-grip" aria-hidden="true"></div>
      <div class="ask-drawer-head">
        <div class="ask-drawer-title">
          <span class="ask-drawer-pulse"></span>
          <span>${escapeHtml(t('ask.title'))}</span>
        </div>
        <span class="ask-drawer-hint">${escapeHtml(q.multi ? t('ask.multiHint') : t('ask.singleHint'))}</span>
        <button type="button" class="icon-btn ask-drawer-close" id="askDrawerClose" title="${escAttr(t('ask.ignore'))}" aria-label="${escAttr(t('ask.ignore'))}"><svg><use href="#i-x"/></svg></button>
      </div>
      <div class="ask-drawer-body">
        <div class="ask-question">${escapeHtml(q.question || '')}</div>
        ${options ? `<div class="ask-options">${options}</div>` : ''}
        ${q.allow_other === false ? '' : `<label class="ask-other-toggle"><input type="checkbox" id="askOtherOn" ${q.otherOn ? 'checked' : ''}/> ${escapeHtml(t('ask.other'))}</label>
        <textarea class="input ask-other-input" id="askOtherInput" rows="2"
          placeholder="${escAttr(t('ask.otherPlaceholder'))}" ${q.otherOn ? '' : 'hidden'}>${escapeHtml(q.other || '')}</textarea>`}
      </div>
      <div class="ask-drawer-foot">
        <button class="btn small ghost" type="button" id="askIgnoreBtn">${escapeHtml(t('ask.ignore'))}</button>
        <button class="btn small primary" type="button" id="askSubmitBtn">${escapeHtml(t('ask.submit'))}</button>
      </div>`;
    host.querySelectorAll('[data-ask-option]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const value = btn.dataset.askOption;
        if (!q.multi) q.selected.clear();
        if (q.selected.has(value)) q.selected.delete(value);
        else q.selected.add(value);
        renderAskUser();
      });
    });
    const otherOn = $('#askOtherOn');
    if (otherOn) {
      otherOn.addEventListener('change', () => {
        q.otherOn = otherOn.checked;
        renderAskUser();
      });
    }
    const otherInput = $('#askOtherInput');
    if (otherInput) {
      otherInput.addEventListener('input', () => { q.other = otherInput.value; });
    }
    const ignoreBtn = $('#askIgnoreBtn');
    if (ignoreBtn) ignoreBtn.addEventListener('click', () => submitAskUser(true));
    const closeBtn = $('#askDrawerClose');
    if (closeBtn) closeBtn.addEventListener('click', () => submitAskUser(true));
    const submitBtn = $('#askSubmitBtn');
    if (submitBtn) submitBtn.addEventListener('click', () => submitAskUser(false));
    if (otherInput && q.otherOn) otherInput.focus();
  }

  async function submitAskUser(ignored) {
    const q = state.pendingQuestion;
    if (!q) return;
    const selected = ignored ? [] : Array.from(q.selected);
    const other = (!ignored && q.otherOn) ? String(q.other || '').trim() : '';
    try {
      await api(`/questions/${encodeURIComponent(q.id)}`, {
        method: 'POST',
        body: JSON.stringify({ selected, other, ignored }),
      });
      hideAskUserIf(q.id);
    } catch (err) {
      toast(String(err.message || err), 'bad');
    }
  }

  function renderApprovalBar() {
    const bar = $('#approvalBar');
    if (!bar) return;
    const list = (state.pendingApprovals || []).filter((a) => a.status === 'pending');
    if (!list.length) {
      bar.hidden = true;
      bar.dataset.approvalId = '';
      return;
    }
    const a = list[list.length - 1];
    const risk = ['low', 'medium', 'high'].includes(a.risk) ? a.risk : 'medium';
    bar.hidden = false;
    bar.dataset.approvalId = a.id;
    bar.className = `approval-bar risk-${risk}`;
    const riskEl = $('#approvalRiskText');
    if (riskEl) riskEl.textContent = t(`approval.risk.${risk}`);
    const desc = $('#approvalDesc');
    if (desc) {
      // 审批栏是单行截断（为了保持紧凑高度），长命令 / 长路径看不全：
      // 悬停给出完整描述 + 判定原因，别让用户为了看清它去点开 trace。
      const text = toolLabel(a.tool) + (a.note ? ' · ' + a.note : '');
      desc.textContent = text;
      desc.title = [text].concat(a.reasons || []).filter(Boolean).join(' · ');
    }
    const count = $('#approvalCount');
    if (count) count.textContent = list.length > 1 ? String(list.length) : '';
  }

  function jumpToApproval(id) {
    const inner = $('#chatInner');
    if (!inner || !id) return;
    const target = inner.querySelector(`[data-approval-step="${CSS.escape(id)}"]`)
      || inner.querySelector('[data-live]');
    if (!target) return;
    const details = target.closest('.trace');
    if (details) {
      details.open = true;
      if (details.dataset.detail) state.openDetails.set(details.dataset.detail, true);
    }
    target.classList.add('is-jump');
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => target.classList.remove('is-jump'), 1800);
  }

  async function decideApproval(approvalId, action) {
    if (!approvalId) return;
    try {
      const data = await api(`/approvals/${encodeURIComponent(approvalId)}`, {
        method: 'POST', body: JSON.stringify({ action }),
      });
      const status = (data && data.status) || 'denied';
      const patch = (msg) => {
        for (const s of msg.trace || []) {
          if (s.kind === 'approval' && s.id === approvalId) {
            s.status = status;
            touchStep(s);
          }
        }
      };
      for (const m of state.messages) patch(m);
      if (state.live) patch(state.live);
      const bar = state.pendingApprovals.find((a) => a.id === approvalId);
      if (bar) bar.status = status;
      state.pendingApprovals = state.pendingApprovals.filter((a) => a.status === 'pending');
      renderApprovalBar();
      refreshActiveRuns();
      scheduleRender();
      if (action === 'approve_always') {
        // 「始终允许」把当前会话的档位升为完全允许，composer 同步更新
        applyPermMode('always');
        toast(t('perm.set'), 'ok', 2000);
      }
    } catch (err) {
      toast(err.message || String(err), 'bad', 4000);
    }
  }

  /* ── 会话权限（composer 档位，会话级、实时保存、新会话延续上一条） ────── */

  const PERM_MODES = ['once', 'auto', 'always'];

  function applyPermMode(mode) {
    if (!PERM_MODES.includes(mode)) mode = 'once';
    state.permMode = mode;
    const select = $('#permSelect');
    if (select) select.value = mode;
  }

  async function loadPermMode() {
    try {
      const data = await api('/app/permissions');
      const policy = (data && data.policy) || {};
      applyPermMode(policy.default || 'once');
      state.mode = policy.run_mode === 'goal' ? 'goal' : 'autonomous';
      renderHeader();
    } catch (err) {
      console.warn('permission mode load failed', err);
    }
  }

  async function setPermMode(mode) {
    applyPermMode(mode);
    try {
      if (state.currentId && state.conv) {
        await api(`/conversations/${encodeURIComponent(state.currentId)}/permission`, {
          method: 'PUT', body: JSON.stringify({ mode: state.permMode }),
        });
      } else {
        // 还没有对话：保存为默认档位，新对话创建时继承
        await api('/app/permissions', {
          method: 'PUT', body: JSON.stringify({ default: state.permMode }),
        });
      }
    } catch (err) {
      toast(err.message || String(err), 'bad', 4000);
    }
  }

  async function loadMcp() {
    try {
      const data = await api('/app/mcp');
      state.mcp = data || { servers: [], npm: false };
      renderMcp();
    } catch (err) {
      console.warn('mcp load failed', err);
    }
  }

  function renderMcp() {
    const list = $('#mcpList');
    if (!list) return;
    const servers = (state.mcp && state.mcp.servers) || [];
    const npmOk = state.mcp && state.mcp.npm;
    setTabCount('mcp', servers.length);
    const head = npmOk ? '' : `<div class="mcp-note">${escapeHtml(t('mcp.npmMissing'))}</div>`;
    if (!servers.length) {
      list.innerHTML = head + `<div class="mcp-empty">${escapeHtml(t('mcp.empty'))}</div>`;
      return;
    }
    list.innerHTML = head + servers.map((s) => `<div class="mcp-row" data-mcp-id="${escAttr(s.id)}">
      <div class="mcp-row-main">
        <span class="mcp-name">${escapeHtml(s.name || s.id)}</span>
        <span class="mcp-type mono">${escapeHtml(s.type)}</span>
        ${s.package ? `<span class="mcp-pkg mono">${escapeHtml(s.package)}</span>` : ''}
        ${s.url ? `<span class="mcp-url mono">${escapeHtml(s.url)}</span>` : ''}
        <span class="mcp-tools">${escapeHtml(t('mcp.tools', { n: s.tools || 0 }))}</span>
        ${s.installed ? '<span class="pill" data-tone="success">npm</span>' : ''}
      </div>
      <div class="mcp-row-actions">
        <label class="switch" title="${escAttr(s.enabled ? t('mcp.disable') : t('mcp.enable'))}">
          <input type="checkbox" data-mcp-enabled="${escAttr(s.id)}"${s.enabled ? ' checked' : ''} />
          <span class="track"></span>
        </label>
        <button class="icon-btn" type="button" data-mcp-edit="${escAttr(s.id)}" title="${escAttr(t('mcp.edit'))}" aria-label="${escAttr(t('mcp.edit'))}"><svg><use href="#i-edit"/></svg></button>
        <button class="btn small ghost" type="button" data-mcp-probe="${escAttr(s.id)}">${escapeHtml(t('mcp.test'))}</button>
        <button class="btn small ghost" type="button" data-mcp-delete="${escAttr(s.id)}"><svg><use href="#i-trash"/></svg></button>
      </div>
    </div>`).join('');
  }

  async function loadSkills() {
    try {
      const data = await api('/app/skills');
      state.skills = (data && data.items) || [];
      renderSkills();
    } catch (err) {
      console.warn('skills load failed', err);
    }
  }

  function renderSkills() {
    const list = $('#skillList');
    if (!list) return;
    const items = state.skills || [];
    setTabCount('skills', items.length);
    if (!items.length) {
      list.innerHTML = `<div class="skill-empty">${escapeHtml(t('skill.empty'))}</div>`;
      return;
    }
    list.innerHTML = items.map((s) => `<div class="skill-row" data-skill-id="${escAttr(s.id)}">
      <div class="skill-row-main">
        <span class="skill-name">${escapeHtml(s.name)}</span>
        <span class="skill-desc">${escapeHtml(s.description || '')}</span>
        <span class="skill-src mono">${escapeHtml(s.source || 'local')}</span>
        ${(s.files || []).length > 1 ? `<span class="skill-files">${escapeHtml(t('skill.files', { n: (s.files || []).length }))}</span>` : ''}
      </div>
      <div class="skill-row-actions">
        <label class="switch" title="${escAttr(s.enabled ? t('mcp.disable') : t('mcp.enable'))}">
          <input type="checkbox" data-skill-enabled="${escAttr(s.id)}"${s.enabled ? ' checked' : ''} />
          <span class="track"></span>
        </label>
        <button class="btn small ghost" type="button" data-skill-download="${escAttr(s.id)}"><svg><use href="#i-download"/></svg>${escapeHtml(t('skill.download'))}</button>
        <button class="btn small ghost" type="button" data-skill-delete="${escAttr(s.id)}"><svg><use href="#i-trash"/></svg></button>
      </div>
    </div>`).join('');
  }

  /* ── extensions: slash commands / hooks ─────────────────────────────── */

  async function loadExtensions() {
    const ws = encodeURIComponent(workspaceId());
    try {
      const [cmds, hooks] = await Promise.all([
        api(`/app/commands?workspace_id=${ws}`),
        api(`/app/hooks?workspace_id=${ws}`),
      ]);
      state.commands = cmds || { commands: [] };
      state.hooks = hooks || { hooks: [] };
    } catch (err) {
      console.warn('extensions load failed', err);
      state.commands = state.commands || { commands: [] };
      state.hooks = state.hooks || { hooks: [] };
    }
    renderExtensions();
  }

  function renderExtensions() {
    const items = (state.commands && state.commands.commands) || [];
    setTabCount('commands', items.length);
    const cmdList = $('#slashCommandList');
    if (cmdList) {
      if (!items.length) {
        cmdList.innerHTML = `<div class="mcp-empty">${escapeHtml(t('ext.emptyCommands'))}</div>`;
      } else {
        cmdList.innerHTML = items.map((c) => `<div class="mcp-row">
          <div class="mcp-row-main">
            <span class="mcp-name mono">/${escapeHtml(c.name)}</span>
            <span class="skill-desc">${escapeHtml(c.description || '')}</span>
            ${c.argument_hint ? `<span class="mcp-type mono">${escapeHtml(c.argument_hint)}</span>` : ''}
            <span class="mcp-type">${escapeHtml(c.builtin ? t('ext.builtin') : (c.source || ''))}</span>
          </div>
          <div class="mcp-row-actions">
            ${(!c.builtin && (c.source === 'workspace' || c.source === 'claude'))
              ? `<button class="btn small ghost" type="button" data-command-delete="${escAttr(c.name)}"><svg><use href="#i-trash"/></svg></button>`
              : ''}
          </div>
        </div>`).join('');
      }
    }
    const cmdPaths = $('#slashCommandPaths');
    if (cmdPaths) {
      const dirs = ((state.commands && state.commands.workspace_dirs) || []).join('  ·  ');
      cmdPaths.textContent = [dirs, (state.commands && state.commands.global_dir) || '']
        .filter(Boolean).join('  ·  ');
    }

    const hookList = $('#hookList');
    if (hookList) {
      const hooks = (state.hooks && state.hooks.hooks) || [];
      const wsPath = (state.hooks && state.hooks.workspace_path) || '';
      const counters = {};
      const rows = hooks.map((h) => {
        const isWs = !!wsPath && h.source === wsPath;
        let idx = -1;
        if (isWs) {
          counters[h.event] = counters[h.event] || 0;
          idx = counters[h.event]++;
        }
        return `<div class="mcp-row">
          <div class="mcp-row-main">
            <span class="pill" data-tone="info">${escapeHtml(h.event)}</span>
            <span class="mcp-type mono">${escapeHtml(h.matcher || '*')}</span>
            <span class="mcp-url mono" title="${escAttr(h.command || '')}">${escapeHtml(h.command || '')}</span>
            <span class="mcp-type">${escapeHtml(String(h.timeout || ''))}s</span>
          </div>
          <div class="mcp-row-actions">
            ${isWs ? `<button class="btn small ghost" type="button" data-hook-delete="${escAttr(h.event)}" data-hook-index="${idx}"><svg><use href="#i-trash"/></svg></button>` : ''}
          </div>
        </div>`;
      }).join('');
      hookList.innerHTML = rows || `<div class="mcp-empty">${escapeHtml(t('ext.emptyHooks'))}</div>`;
    }
    const hookPaths = $('#hookPaths');
    if (hookPaths) {
      hookPaths.textContent = [(state.hooks && state.hooks.global_path) || '',
        (state.hooks && state.hooks.workspace_path) || ''].filter(Boolean).join('  ·  ');
    }
  }

  function bindExtensionControls() {
    const addCmd = $('#addSlashCommandBtn');
    if (addCmd) {
      addCmd.addEventListener('click', () => {
        const form = $('#slashCommandForm');
        if (!form) return;
        form.hidden = !form.hidden;
        if (!form.hidden) {
          $('#slashCommandName').value = '';
          $('#slashCommandHint').value = '';
          $('#slashCommandDesc').value = '';
          $('#slashCommandBody').value = '';
          $('#slashCommandName').focus();
        }
      });
    }
    const cancelCmd = $('#slashCommandCancelBtn');
    if (cancelCmd) cancelCmd.addEventListener('click', () => { $('#slashCommandForm').hidden = true; });
    const saveCmd = $('#slashCommandSaveBtn');
    if (saveCmd) {
      saveCmd.addEventListener('click', async () => {
        const name = ($('#slashCommandName').value || '').trim().replace(/^\//, '');
        const body = ($('#slashCommandBody').value || '').trim();
        if (!name || !body) { toast(t('ext.badName'), 'bad', 4000); return; }
        saveCmd.disabled = true;
        try {
          await api('/app/commands', {
            method: 'POST',
            body: JSON.stringify({
              name,
              body,
              description: ($('#slashCommandDesc').value || '').trim(),
              argument_hint: ($('#slashCommandHint').value || '').trim(),
              workspace_id: workspaceId(),
            }),
          });
          $('#slashCommandForm').hidden = true;
          toast(t('ext.commandSaved'), 'ok', 2400);
          await loadExtensions();
          loadCommands();
        } catch (err) { toast(err.message || String(err), 'bad', 5000); }
        finally { saveCmd.disabled = false; }
      });
    }

    const cmdList = $('#slashCommandList');
    if (cmdList) {
      cmdList.addEventListener('click', async (e) => {
        const del = e.target.closest('[data-command-delete]');
        if (!del) return;
        const name = del.dataset.commandDelete;
        const ok = await openConfirm({
          title: t('ext.deleteCommandConfirm', { name }),
          message: t('ext.deleteCommandConfirm', { name }),
          okText: t('ext.delete'),
          cancelText: t('ext.cancel'),
        });
        if (!ok) return;
        try {
          await api(`/app/commands/${encodeURIComponent(name)}?workspace_id=${encodeURIComponent(workspaceId())}`,
            { method: 'DELETE' });
          toast(t('ext.commandDeleted'), 'ok', 2200);
          await loadExtensions();
          loadCommands();
        } catch (err) { toast(err.message || String(err), 'bad', 5000); }
      });
    }

    const hookTimeoutInput = $('#hookTimeout');
    const hookTimeoutOutput = $('#hookTimeoutVal');
    const paintHookTimeout = () => {
      if (hookTimeoutInput && hookTimeoutOutput) hookTimeoutOutput.textContent = hookTimeoutInput.value;
    };
    if (hookTimeoutInput) {
      hookTimeoutInput.addEventListener('input', paintHookTimeout);
      paintHookTimeout();
    }
    $$('[data-hook-matcher-value]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const input = $('#hookMatcher');
        if (input) input.value = btn.dataset.hookMatcherValue || '*';
      });
    });

    const addHook = $('#addHookBtn');
    if (addHook) {
      addHook.addEventListener('click', () => {
        const form = $('#hookForm');
        if (!form) return;
        form.hidden = !form.hidden;
        if (!form.hidden) {
          $('#hookMatcher').value = '*';
          $('#hookCommand').value = '';
          $('#hookTimeout').value = '10';
          paintHookTimeout();
          $('#hookCommand').focus();
        }
      });
    }
    const cancelHook = $('#hookCancelBtn');
    if (cancelHook) cancelHook.addEventListener('click', () => { $('#hookForm').hidden = true; });
    const saveHook = $('#hookSaveBtn');
    if (saveHook) {
      saveHook.addEventListener('click', async () => {
        const command = ($('#hookCommand').value || '').trim();
        if (!command) { toast(t('ext.command'), 'bad', 3000); return; }
        saveHook.disabled = true;
        try {
          await api('/app/hooks', {
            method: 'POST',
            body: JSON.stringify({
              event: $('#hookEvent').value,
              matcher: ($('#hookMatcher').value || '*').trim() || '*',
              command,
              timeout: Number($('#hookTimeout').value) || 10,
              workspace_id: workspaceId(),
            }),
          });
          $('#hookForm').hidden = true;
          toast(t('ext.hookSaved'), 'ok', 2400);
          await loadExtensions();
        } catch (err) { toast(err.message || String(err), 'bad', 5000); }
        finally { saveHook.disabled = false; }
      });
    }
    const template = $('#hookTemplateBtn');
    if (template) {
      template.addEventListener('click', async () => {
        try {
          await api('/app/hooks/template', {
            method: 'POST', body: JSON.stringify({ workspace_id: workspaceId() }),
          });
          toast(t('ext.templateCreated'), 'ok', 2600);
          await loadExtensions();
        } catch (err) { toast(err.message || String(err), 'bad', 5000); }
      });
    }
    const refresh = $('#refreshExtensionsBtn');
    if (refresh) refresh.addEventListener('click', () => { loadExtensions(); loadCommands(); });

    const hookList = $('#hookList');
    if (hookList) {
      hookList.addEventListener('click', async (e) => {
        const del = e.target.closest('[data-hook-delete]');
        if (!del) return;
        const ok = await openConfirm({
          title: t('ext.deleteHookConfirm'),
          message: t('ext.deleteHookConfirm'),
          okText: t('ext.delete'),
          cancelText: t('ext.cancel'),
        });
        if (!ok) return;
        try {
          await api(`/app/hooks?event=${encodeURIComponent(del.dataset.hookDelete)}&index=${encodeURIComponent(del.dataset.hookIndex)}&workspace_id=${encodeURIComponent(workspaceId())}`,
            { method: 'DELETE' });
          toast(t('ext.hookDeleted'), 'ok', 2200);
          await loadExtensions();
        } catch (err) { toast(err.message || String(err), 'bad', 5000); }
      });
    }
  }

  /* ── composer slash-command palette ─────────────────────────────────── */

  async function loadCommands() {
    try {
      const data = await api(`/app/commands?workspace_id=${encodeURIComponent(workspaceId())}`);
      state.commands = data || { commands: [] };
      if (state.slash.open) updateSlashMenu();
    } catch (err) {
      console.warn('slash commands load failed', err);
    }
  }

  function allSlashCommands() {
    const items = ((state.commands && state.commands.commands) || []).map((c) => ({
      name: c.name, description: c.description || '',
      hint: c.argument_hint || '', source: c.source || '',
    }));
    items.unshift({
      name: 'help', description: t('slash.help'), hint: '', source: 'builtin',
    });
    return items;
  }

  /* ── @成员 补全 ─────────────────────────────────────────────────────
     只在当前会话绑定了团队、且输入光标前是 `@查询词` 时出现。
     选中后把 `@查询词` 替换成 `@成员名 `，发送时正文里的 @ 会被解析成 mentions。 */
  let mentionMembers = [];
  const mentionState = { open: false, index: 0, items: [] };

  function setMentionMembers(list) {
    mentionMembers = (Array.isArray(list) ? list : [])
      .filter((m) => m && (m.name || m.id))
      .map((m) => ({ id: m.id, name: String(m.name || m.id), expertise: String(m.expertise || '') }));
    if (!mentionMembers.length) closeMentionMenu();
  }

  async function loadConvTeamMembers(convId) {
    try {
      const data = await api(`/conversations/${encodeURIComponent(convId)}/team`);
      if (convId !== state.currentId) return;
      setMentionMembers((data && data.team && data.team.members) || []);
    } catch (err) {
      setMentionMembers([]);
    }
  }

  function mentionQuery() {
    const input = $('#promptInput');
    if (!input) return null;
    const caret = typeof input.selectionStart === 'number'
      ? input.selectionStart : String(input.value || '').length;
    const before = String(input.value || '').slice(0, caret);
    const m = /(^|\s)@([A-Za-z0-9_\-\u4e00-\u9fff]{0,32})$/.exec(before);
    if (!m) return null;
    return { start: caret - m[2].length - 1, query: m[2], caret };
  }

  function renderMentionMenu() {
    const menu = $('#mentionMenu');
    if (!menu) return;
    menu.innerHTML = `<div class="slash-head">${escapeHtml(t('mention.title'))}</div>` + mentionState.items.map((m, i) =>
      `<button type="button" role="option" class="slash-item${i === mentionState.index ? ' is-active' : ''}" data-mention-index="${i}" aria-selected="${i === mentionState.index ? 'true' : 'false'}">
        <span class="slash-name">@${escapeHtml(m.name)}</span>
        ${m.expertise ? `<span class="slash-desc">${escapeHtml(m.expertise)}</span>` : ''}
      </button>`).join('');
    menu.hidden = false;
  }

  function updateMentionMenu() {
    const menu = $('#mentionMenu');
    if (!menu) return;
    const hit = mentionQuery();
    if (!hit || !mentionMembers.length) { closeMentionMenu(); return; }
    const q = hit.query.toLowerCase();
    const items = mentionMembers
      .filter((m) => !q || m.name.toLowerCase().includes(q))
      .slice(0, 8);
    if (!items.length) { closeMentionMenu(); return; }
    if (mentionState.index >= items.length) mentionState.index = 0;
    mentionState.open = true;
    mentionState.items = items;
    renderMentionMenu();
  }

  function closeMentionMenu() {
    const menu = $('#mentionMenu');
    if (menu) { menu.hidden = true; menu.innerHTML = ''; }
    mentionState.open = false;
    mentionState.items = [];
    mentionState.index = 0;
  }

  function chooseMention(index) {
    const item = mentionState.items[index];
    const hit = mentionQuery();
    const input = $('#promptInput');
    if (!item || !hit || !input) { closeMentionMenu(); return; }
    const before = String(input.value || '').slice(0, hit.start);
    const after = String(input.value || '').slice(hit.caret);
    input.value = `${before}@${item.name} ${after}`;
    const pos = before.length + item.name.length + 2;
    try { input.setSelectionRange(pos, pos); } catch (_) { /* ignore */ }
    closeMentionMenu();
    autoGrow(input);
    updateLongTextHint();
    input.focus();
  }

  function handleMentionKeydown(e) {
    if (!mentionState.open) return false;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const n = mentionState.items.length;
      mentionState.index = (mentionState.index + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      renderMentionMenu();
      return true;
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      chooseMention(mentionState.index);
      return true;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      closeMentionMenu();
      return true;
    }
    return false;
  }

  function updateSlashMenu(forceOpen = false) {
    const input = $('#promptInput');
    const menu = $('#slashMenu');
    if (!input || !menu) return;
    const value = input.value;
    const match = /^\/([A-Za-z0-9_\-\u4e00-\u9fff:]*)$/.exec(value);
    if (!match && !forceOpen) { closeSlashMenu(); return; }
    const query = match ? match[1].toLowerCase() : '';
    const items = allSlashCommands().filter((c) => !query || c.name.toLowerCase().includes(query));
    if (!items.length) {
      state.slash = { open: true, index: 0, items: [] };
      menu.innerHTML = `<div class="slash-empty">${escapeHtml(t('slash.empty'))}</div>`;
      menu.hidden = false;
      return;
    }
    if (state.slash.index >= items.length) state.slash.index = 0;
    state.slash = { open: true, index: state.slash.index || 0, items };
    menu.innerHTML = `<div class="slash-head">${escapeHtml(t('slash.title'))}</div>` + items.map((c, i) =>
      `<button type="button" role="option" class="slash-item${i === state.slash.index ? ' is-active' : ''}" data-slash-index="${i}" aria-selected="${i === state.slash.index ? 'true' : 'false'}">
        <span class="slash-name mono">/${escapeHtml(c.name)}</span>
        <span class="slash-desc">${escapeHtml(c.description)}</span>
        ${c.hint ? `<span class="slash-hint mono">${escapeHtml(c.hint)}</span>` : ''}
      </button>`).join('');
    menu.hidden = false;
  }

  function closeSlashMenu() {
    const menu = $('#slashMenu');
    if (menu) { menu.hidden = true; menu.innerHTML = ''; }
    state.slash = { open: false, index: 0, items: [] };
  }

  function chooseSlashCommand(index) {
    const item = state.slash.items[index];
    if (!item) return;
    const input = $('#promptInput');
    closeSlashMenu();
    if (item.name === 'help') {
      updateSlashMenu(true);
      input.focus();
      return;
    }
    input.value = `/${item.name} `;
    autoGrow(input);
    updateLongTextHint();
    input.focus();
    const pos = input.value.length;
    input.setSelectionRange(pos, pos);
  }

  function handleSlashKeydown(e) {
    if (!state.slash.open) return false;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const len = state.slash.items.length;
      if (!len) return true;
      const dir = e.key === 'ArrowDown' ? 1 : -1;
      state.slash.index = (state.slash.index + dir + len) % len;
      updateSlashMenu();
      return true;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      closeSlashMenu();
      return true;
    }
    if ((e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey) {
      const keyCode = e.keyCode || e.which || 0;
      if (e.isComposing || keyCode === 229) return false;
      e.preventDefault();
      chooseSlashCommand(state.slash.index);
      return true;
    }
    return false;
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 500);
  }

  function bytesToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let chunk = '';
    for (let i = 0; i < bytes.length; i += 8192) {
      chunk += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    }
    return btoa(chunk);
  }

  function bindIntegrationControls() {
    const permSelect = $('#permSelect');
    if (permSelect) {
      permSelect.addEventListener('change', () => setPermMode(permSelect.value));
    }

    const mcpList = $('#mcpList');
    if (mcpList) {
      mcpList.addEventListener('change', async (e) => {
        const toggle = e.target.closest('[data-mcp-enabled]');
        if (!toggle) return;
        try {
          await api(`/app/mcp/servers/${encodeURIComponent(toggle.dataset.mcpEnabled)}`, {
            method: 'PUT', body: JSON.stringify({ enabled: toggle.checked }),
          });
          loadMcp();
        } catch (err) {
          toggle.checked = !toggle.checked;
          toast(err.message, 'bad', 4000);
        }
      });
      mcpList.addEventListener('click', async (e) => {
        const edit = e.target.closest('[data-mcp-edit]');
        if (edit) {
          const srv = ((state.mcp && state.mcp.servers) || []).find((s) => s.id === edit.dataset.mcpEdit);
          if (srv) openEditMcp(srv);
          return;
        }
        const probe = e.target.closest('[data-mcp-probe]');
        if (probe) {
          probe.disabled = true;
          try {
            const data = await api(`/app/mcp/servers/${encodeURIComponent(probe.dataset.mcpProbe)}/probe`,
              { method: 'POST' });
            toast(data && data.ok
              ? t('mcp.probeOk', { n: Number(data.tools) || 0 })
              : `${t('mcp.probeFail')}：${(data && data.error) || ''}`,
              data && data.ok ? 'ok' : 'bad', 6000);
            loadMcp();
          } catch (err) { toast(err.message, 'bad', 5000); }
          probe.disabled = false;
          return;
        }
        const del = e.target.closest('[data-mcp-delete]');
        if (del) {
          try {
            await api(`/app/mcp/servers/${encodeURIComponent(del.dataset.mcpDelete)}`, { method: 'DELETE' });
            loadMcp();
          } catch (err) { toast(err.message, 'bad', 4000); }
        }
      });
    }

    const mcpModal = $('#addMcpModal');
    let mcpType = 'stdio';
    let mcpEditingId = '';

    // key-value 行：环境变量 / 请求头共用，避免用户手写 JSON。
    function kvRowHtml(key = '', value = '') {
      return `<div class="kv-row">
        <input class="input mono" data-kv-key placeholder="KEY" value="${escAttr(key)}" spellcheck="false" />
        <input class="input mono" data-kv-value placeholder="value" value="${escAttr(value)}" spellcheck="false" />
        <button class="kv-remove" type="button" data-kv-remove title="${escAttr(t('mcp.removePair'))}" aria-label="${escAttr(t('mcp.removePair'))}"><svg><use href="#i-x"/></svg></button>
      </div>`;
    }
    function addKvRow(sel, key = '', value = '') {
      const host = $(sel);
      if (!host) return;
      host.insertAdjacentHTML('beforeend', kvRowHtml(key, value));
      const row = host.lastElementChild;
      const input = row && row.querySelector('[data-kv-key]');
      if (input) input.focus();
    }
    function fillKv(sel, obj) {
      const host = $(sel);
      if (!host) return;
      const entries = Object.entries(obj || {}).filter(([k]) => String(k || '').trim());
      host.innerHTML = entries.map(([k, v]) => kvRowHtml(k, v)).join('');
      if (!entries.length) addKvRow(sel);
    }
    function readKv(sel) {
      const out = {};
      $$('.kv-row', $(sel)).forEach((row) => {
        const key = (row.querySelector('[data-kv-key]').value || '').trim();
        const value = row.querySelector('[data-kv-value]').value || '';
        if (key) out[key] = value;
      });
      return out;
    }

    function setMcpType(type) {
      mcpType = type === 'http' ? 'http' : 'stdio';
      const seg = $('#mcpTypeSeg');
      if (seg) {
        seg.querySelectorAll('button').forEach((b) =>
          b.classList.toggle('active', b.dataset.mcpType === mcpType));
      }
      const stdioFields = $('#mcpStdioFields');
      const httpFields = $('#mcpHttpFields');
      if (stdioFields) stdioFields.hidden = mcpType !== 'stdio';
      if (httpFields) httpFields.hidden = mcpType !== 'http';
      const installBtn = $('#mcpInstallBtn');
      if (installBtn) installBtn.hidden = !!mcpEditingId || mcpType !== 'stdio';
    }

    function mcpBody() {
      return {
        name: ($('#mcpName').value || '').trim(),
        type: mcpType,
        package: ($('#mcpPackage').value || '').trim(),
        install_command: ($('#mcpInstallCommand').value || '').trim(),
        url: ($('#mcpUrl').value || '').trim(),
        command: ($('#mcpCommand').value || '').trim(),
        args: ($('#mcpArgs').value || '').split(/\s+/).filter(Boolean),
        env: readKv('#mcpEnvRows'),
        headers: readKv('#mcpHeaderRows'),
        enabled: $('#mcpEnabled') ? $('#mcpEnabled').checked : true,
        note: ($('#mcpNote') ? $('#mcpNote').value : '').trim(),
      };
    }

    function setMcpMode(editing, server = {}) {
      mcpEditingId = editing ? String(server.id || '') : '';
      const title = $('#addMcpTitle');
      if (title) title.textContent = mcpEditingId ? t('mcp.editTitle') : t('mcp.addTitle');
      const addBtn = $('#mcpAddBtn');
      if (addBtn) addBtn.textContent = mcpEditingId ? t('mcp.save') : t('mcp.add');
      setMcpType(server.type || 'stdio');
    }

    function openAddMcp() {
      if (!mcpModal) return;
      ['#mcpName', '#mcpPackage', '#mcpInstallCommand', '#mcpCommand', '#mcpArgs', '#mcpUrl', '#mcpNote']
        .forEach((sel) => { const el = $(sel); if (el) el.value = ''; });
      const enabled = $('#mcpEnabled');
      if (enabled) enabled.checked = true;
      fillKv('#mcpEnvRows', {});
      fillKv('#mcpHeaderRows', {});
      setMcpMode(false);
      const note = $('#mcpAddNote');
      if (note) note.textContent = '';
      mcpModal.hidden = false;
      const name = $('#mcpName');
      if (name) name.focus();
    }

    function openEditMcp(server) {
      if (!mcpModal) return;
      $('#mcpName').value = server.name || '';
      $('#mcpPackage').value = server.package || '';
      $('#mcpInstallCommand').value = server.install_command || '';
      $('#mcpCommand').value = server.command || '';
      $('#mcpArgs').value = (server.args || []).join(' ');
      $('#mcpUrl').value = server.url || '';
      $('#mcpNote').value = server.note || '';
      const enabled = $('#mcpEnabled');
      if (enabled) enabled.checked = server.enabled !== false;
      fillKv('#mcpEnvRows', server.env || {});
      fillKv('#mcpHeaderRows', server.headers || {});
      setMcpMode(true, server);
      const note = $('#mcpAddNote');
      if (note) note.textContent = '';
      mcpModal.hidden = false;
      const name = $('#mcpName');
      if (name) name.focus();
    }

    if (mcpModal) mcpModal.addEventListener('click', (e) => {
      const add = e.target.closest('[data-kv-add]');
      if (add) { addKvRow(add.dataset.kvAdd); return; }
      const rm = e.target.closest('[data-kv-remove]');
      if (rm) {
        const row = rm.closest('.kv-row');
        const host = row && row.parentElement;
        row.remove();
        if (host && !host.querySelector('.kv-row')) addKvRow('#' + host.id);
      }
    });

    if (mcpModal) {
      const openBtn = $('#addMcpBtn');
      if (openBtn) openBtn.addEventListener('click', openAddMcp);

      const typeSeg = $('#mcpTypeSeg');
      if (typeSeg) {
        typeSeg.addEventListener('click', (e) => {
          const b = e.target.closest('[data-mcp-type]');
          if (b) setMcpType(b.dataset.mcpType);
        });
      }

      const exportBtn = $('#mcpExportBtn');
      if (exportBtn) {
        exportBtn.addEventListener('click', async () => {
          try {
            const data = await api('/app/mcp/export');
            downloadBlob(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
              'agent-zero-mcp.json');
          } catch (err) { toast(err.message, 'bad', 4000); }
        });
      }

      const importBtn = $('#mcpImportBtn');
      if (importBtn) importBtn.addEventListener('click', () => $('#mcpImportFile').click());

      const installBtn = $('#mcpInstallBtn');
      if (installBtn) {
        installBtn.addEventListener('click', async () => {
          const body = mcpBody();
          if (!body.package && !body.install_command) {
            toast(t('mcp.installNeedsPackage'), 'bad', 2500);
            return;
          }
          if (!body.package && !body.command) {
            toast(t('mcp.empty'), 'bad', 2500);
            return;
          }
          installBtn.disabled = true;
          const note = $('#mcpAddNote');
          if (note) note.textContent = body.package || body.install_command;
          try {
            const server = await api('/app/mcp/servers', { method: 'POST', body: JSON.stringify(body) });
            const result = await api(`/app/mcp/servers/${encodeURIComponent(server.id)}/install`,
              { method: 'POST' });
            if (result && result.ok) {
              toast(`${body.package} ✓`, 'ok', 3000);
              await api(`/app/mcp/servers/${encodeURIComponent(server.id)}/probe`, { method: 'POST' });
              mcpModal.hidden = true;
            } else {
              toast((result && result.error) || 'install failed', 'bad', 6000);
            }
            loadMcp();
          } catch (err) { toast(err.message, 'bad', 6000); }
          installBtn.disabled = false;
        });
      }

      const addBtn = $('#mcpAddBtn');
      if (addBtn) {
        addBtn.addEventListener('click', async () => {
          const body = mcpBody();
          if (!body.package && !body.command && !body.url) {
            toast(t('mcp.empty'), 'bad', 2500);
            return;
          }
          try {
            if (mcpEditingId) {
              await api(`/app/mcp/servers/${encodeURIComponent(mcpEditingId)}`, {
                method: 'PUT', body: JSON.stringify(body),
              });
              toast(t('mcp.saved'), 'ok', 2000);
            } else {
              await api('/app/mcp/servers', { method: 'POST', body: JSON.stringify(body) });
            }
            loadMcp();
            mcpModal.hidden = true;
          } catch (err) { toast(err.message, 'bad', 4000); }
        });
      }
    }

    const mcpFile = $('#mcpImportFile');
    if (mcpFile) {
      mcpFile.addEventListener('change', async () => {
        const f = mcpFile.files && mcpFile.files[0];
        if (!f) return;
        try {
          const data = await api('/app/mcp/import', {
            method: 'POST', body: JSON.stringify(JSON.parse(await f.text())),
          });
          toast(`${t('mcp.import')} · ${((data && data.added) || 0)}`, 'ok', 3000);
          loadMcp();
        } catch (err) { toast(err.message, 'bad', 4000); }
        mcpFile.value = '';
      });
    }

    const skillList = $('#skillList');
    if (skillList) {
      skillList.addEventListener('change', async (e) => {
        const toggle = e.target.closest('[data-skill-enabled]');
        if (!toggle) return;
        try {
          await api(`/app/skills/${encodeURIComponent(toggle.dataset.skillEnabled)}`, {
            method: 'PUT', body: JSON.stringify({ enabled: toggle.checked }),
          });
          loadSkills();
        } catch (err) {
          toggle.checked = !toggle.checked;
          toast(err.message, 'bad', 4000);
        }
      });
      skillList.addEventListener('click', async (e) => {
        const dl = e.target.closest('[data-skill-download]');
        if (dl) {
          window.open(`${API}/skills/${encodeURIComponent(dl.dataset.skillDownload)}/download`, '_blank');
          return;
        }
        const del = e.target.closest('[data-skill-delete]');
        if (del) {
          try {
            await api(`/app/skills/${encodeURIComponent(del.dataset.skillDelete)}`, { method: 'DELETE' });
            loadSkills();
          } catch (err) { toast(err.message, 'bad', 4000); }
        }
      });
    }

    const skillModal = $('#addSkillModal');
    let skillSrc = 'manual';

    function setSkillSrc(src) {
      skillSrc = ['manual', 'upload', 'npm', 'npx'].includes(src) ? src : 'manual';
      const seg = $('#skillSrcSeg');
      if (seg) {
        seg.querySelectorAll('button').forEach((b) =>
          b.classList.toggle('active', b.dataset.skillSrc === skillSrc));
      }
      ['manual', 'upload', 'npm', 'npx'].forEach((key) => {
        const panel = document.getElementById('skillSrc' + key[0].toUpperCase() + key.slice(1));
        if (panel) panel.hidden = key !== skillSrc;
      });
      const action = $('#skillActionBtn');
      if (action) {
        action.textContent = skillSrc === 'upload' ? t('skill.upload')
          : skillSrc === 'npm' ? t('skill.npm')
          : skillSrc === 'npx' ? t('skill.npxInstall')
          : t('skill.save');
      }
    }

    function openAddSkill() {
      if (!skillModal) return;
      skillModal.hidden = false;
      setSkillSrc(skillSrc);
      const note = $('#skillAddNote');
      if (note) note.textContent = '';
      const field = skillSrc === 'npm' ? $('#skillNpmPackage')
        : skillSrc === 'npx' ? $('#skillNpxSource')
        : $('#skillName');
      if (field) field.focus();
    }

    if (skillModal) {
      const openBtn = $('#addSkillBtn');
      if (openBtn) openBtn.addEventListener('click', openAddSkill);

      const srcSeg = $('#skillSrcSeg');
      if (srcSeg) {
        srcSeg.addEventListener('click', (e) => {
          const b = e.target.closest('[data-skill-src]');
          if (b) setSkillSrc(b.dataset.skillSrc);
        });
      }

      const drop = $('#skillUploadDrop');
      if (drop) drop.addEventListener('click', () => $('#skillUploadFile').click());

      const npxResult = $('#skillNpxResult');
      const showNpx = (html) => {
        if (!npxResult) return;
        npxResult.innerHTML = html;
        npxResult.hidden = false;
      };

      async function npxDiscover(kind, btn) {
        const source = ($('#skillNpxSource').value || '').trim();
        if (!source) { toast(t('skill.npxNeedSource'), 'bad', 3000); return; }
        btn.disabled = true;
        showNpx(`<div class="model-loading"><span class="spinner"></span> ${escapeHtml(t('skill.npxBusy'))}</div>`);
        try {
          const data = kind === 'list'
            ? await api('/app/skills/npx/list', { method: 'POST', body: JSON.stringify({ source }) })
            : await api('/app/skills/npx/search', { method: 'POST', body: JSON.stringify({ query: source }) });
          const items = (data && (data.skills || data.items)) || [];
          if (!items.length) {
            showNpx(`<div class="npx-empty">${escapeHtml((data && data.error) || t('skill.npxNone'))}</div>`);
          } else {
            showNpx(`<div class="npx-chips">${items.map((s) => {
              const isObj = s && typeof s === 'object';
              const name = isObj ? (s.skill || s.name || '') : String(s);
              const src = isObj ? (s.source || '') : '';
              const tip = isObj ? `${src}${isObj && s.installs ? ' · ' + s.installs : ''}` : name;
              return `<button class="npx-chip" type="button" data-npx-skill="${escAttr(name)}"${src ? ` data-npx-source="${escAttr(src)}"` : ''} title="${escAttr(tip)}">${escapeHtml(name)}</button>`;
            }).join('')}</div>`);
          }
        } catch (err) {
          showNpx(`<div class="npx-empty">${escapeHtml(err.message)}</div>`);
        }
        btn.disabled = false;
      }

      async function npxInstall(btn) {
        const source = ($('#skillNpxSource').value || '').trim();
        if (!source) { toast(t('skill.npxNeedSource'), 'bad', 3000); return; }
        const skills = ($('#skillNpxSkills').value || '').split(',').map((s) => s.trim()).filter(Boolean);
        btn.disabled = true;
        showNpx(`<div class="model-loading"><span class="spinner"></span> ${escapeHtml(t('skill.npxInstalling'))}</div>`);
        try {
          const res = await api('/app/skills/npx', {
            method: 'POST', body: JSON.stringify({ source, skills }),
          });
          if (res && res.ok) {
            toast(t('skill.npxInstalled', { n: res.imported || 0 }), 'ok', 6000);
            showNpx(`<div class="npx-chips">${(res.skills || []).map((s) =>
              `<span class="npx-chip done">${escapeHtml(s.name)}</span>`).join('')}</div>`
              + (res.skipped ? `<p class="oc-muted">${escapeHtml(t('skill.npxSkipped', { n: res.skipped }))}</p>` : ''));
            loadSkills();
          } else {
            showNpx(`<div class="npx-empty">${escapeHtml((res && res.error) || 'failed')}</div>`);
          }
        } catch (err) {
          showNpx(`<div class="npx-empty">${escapeHtml(err.message)}</div>`);
        }
        btn.disabled = false;
      }

      const listBtn = $('#skillNpxListBtn');
      if (listBtn) listBtn.addEventListener('click', () => npxDiscover('list', listBtn));
      const searchBtn = $('#skillNpxSearchBtn');
      if (searchBtn) searchBtn.addEventListener('click', () => npxDiscover('search', searchBtn));

      if (npxResult) {
        npxResult.addEventListener('click', (e) => {
          const chip = e.target.closest('[data-npx-skill]');
          if (!chip) return;
          const skillsInput = $('#skillNpxSkills');
          const sourceInput = $('#skillNpxSource');
          if (chip.dataset.npxSource && sourceInput) sourceInput.value = chip.dataset.npxSource;
          if (skillsInput) skillsInput.value = chip.dataset.npxSkill;
          toast(t('skill.npxPicked', { n: chip.dataset.npxSkill }), 'ok', 2500);
        });
      }

      const action = $('#skillActionBtn');
      if (action) {
        action.addEventListener('click', async () => {
          if (skillSrc === 'manual') {
            try {
              await api('/app/skills', {
                method: 'POST',
                body: JSON.stringify({
                  name: ($('#skillName').value || '').trim(),
                  description: ($('#skillDescription').value || '').trim(),
                  content: $('#skillContent').value || '',
                }),
              });
              loadSkills();
              skillModal.hidden = true;
            } catch (err) { toast(err.message, 'bad', 4000); }
            return;
          }
          if (skillSrc === 'upload') {
            $('#skillUploadFile').click();
            return;
          }
          if (skillSrc === 'npm') {
            const pkg = ($('#skillNpmPackage').value || '').trim();
            if (!pkg) { toast(t('skill.npmPackage'), 'bad', 2500); return; }
            action.disabled = true;
            try {
              const data = await api('/app/skills/install', {
                method: 'POST', body: JSON.stringify({ package: pkg }),
              });
              toast(data && data.ok ? `${pkg} ✓` : ((data && data.error) || 'failed'),
                data && data.ok ? 'ok' : 'bad', 6000);
              loadSkills();
              if (data && data.ok) skillModal.hidden = true;
            } catch (err) { toast(err.message, 'bad', 6000); }
            action.disabled = false;
            return;
          }
          npxInstall(action);
        });
      }
    }

    const skillFile = $('#skillUploadFile');
    if (skillFile) {
      skillFile.addEventListener('change', async () => {
        const f = skillFile.files && skillFile.files[0];
        if (!f) return;
        try {
          const buffer = await f.arrayBuffer();
          await api('/app/skills/upload', {
            method: 'POST',
            body: JSON.stringify({
              filename: f.name,
              data: btoa(String.fromCharCode(...new Uint8Array(buffer))),
            }),
          });
          loadSkills();
          if (skillModal) skillModal.hidden = true;
        } catch (err) { toast(err.message, 'bad', 4000); }
        skillFile.value = '';
      });
    }
  }

  /* ── settings ────────────────────────────────────────────────────────── */

  function profileLabel(profile) {
    return modelDisplayName(profile);
  }

  function closeModelMenu() {
    const menu = $('#modelMenu');
    if (menu) menu.hidden = true;
    const chip = $('#modelChip');
    if (chip) chip.setAttribute('aria-expanded', 'false');
  }

  function closeThinkingMenu() {
    const menu = $('#thinkingMenu');
    if (menu) menu.hidden = true;
    const chip = $('#thinkingChip');
    if (chip) chip.setAttribute('aria-expanded', 'false');
  }

  function thinkingOptionsHtml() {
    const levels = activeThinkingLevels();
    const current = levels.includes(state.thinkingLevel) ? state.thinkingLevel : '';
    return [
      `<button type="button" class="thinking-option${current ? '' : ' active'}" data-thinking-pick="" aria-pressed="${current ? 'false' : 'true'}">${escapeHtml(t('thinking.default'))}</button>`,
      ...levels.map((level) => {
        const active = current === level;
        return `<button type="button" class="thinking-option${active ? ' active' : ''}" data-thinking-pick="${escAttr(level)}" aria-pressed="${active ? 'true' : 'false'}">${escapeHtml(thinkingLevelLabel(level))}</button>`;
      }),
    ].join('');
  }

  function renderThinkingMenu() {
    const menu = $('#thinkingMenu');
    if (!menu) return;
    const levels = activeThinkingLevels();
    if (!levels.length) {
      menu.hidden = true;
      menu.innerHTML = '';
      return;
    }
    menu.innerHTML = `<div class="thinking-menu-head"><svg><use href="#i-brain"/></svg><span>${escapeHtml(t('thinking.label'))}</span></div>
      <div class="thinking-options" role="group" aria-label="${escAttr(t('thinking.label'))}">${thinkingOptionsHtml()}</div>`;
  }

  function openThinkingMenu() {
    const menu = $('#thinkingMenu');
    if (!menu) return;
    closeModelMenu();
    renderThinkingMenu();
    if (!activeThinkingLevels().length) return;
    menu.hidden = false;
    const chip = $('#thinkingChip');
    if (chip) chip.setAttribute('aria-expanded', 'true');
  }

  function toggleThinkingMenu() {
    const menu = $('#thinkingMenu');
    if (!menu) return;
    if (!menu.hidden) {
      closeThinkingMenu();
      return;
    }
    openThinkingMenu();
  }

  function modelMenuFilterKey(value) {
    return String(value || '').toLowerCase().trim();
  }

  function modelMenuEmptyHtml(text) {
    return `<div class="model-menu-empty" data-model-menu-empty hidden>${escapeHtml(text)}</div>`;
  }

  function subscriptionIsActive(sub) {
    return (sub.models || []).some((profile) => profile.code === state.activeModel);
  }

  function renderModelMenu() {
    const menu = $('#modelMenu');
    if (!menu) return;
    const subs = buildSubscriptions();
    let sub = subs.find((item) => item.id === state.modelMenuSubscription);
    if (state.modelMenuLevel === 'models' && !sub) state.modelMenuLevel = 'subscriptions';
    const manage = `<button type="button" class="model-menu-block model-menu-manage" data-model-manage>${escapeHtml(t('settings.manageModels'))}</button>`;

    if (state.modelMenuLevel === 'models' && sub) {
      const rows = (sub.models || []).map((profile) => {
        const active = profile.code === state.activeModel;
        const label = profileLabel(profile);
        const modelId = profile.effective_model || profile.model || '';
        const filterText = `${label} ${modelId} ${profile.code || ''}`;
        const subtitle = modelId && modelId !== label ? modelId : '';
        return `<button type="button" class="model-menu-item${active ? ' active' : ''}" data-model-pick="${escAttr(profile.code)}" data-menu-filter="${escAttr(modelMenuFilterKey(filterText))}" role="option" aria-selected="${active}">
          <span class="model-menu-check">${active ? '<svg><use href="#i-check"/></svg>' : ''}</span>
          <span class="model-menu-main">
            <span class="model-menu-name">${escapeHtml(label)}</span>
            ${subtitle ? `<span class="model-menu-id mono">${escapeHtml(subtitle)}</span>` : ''}
          </span>
          ${(profile.effective_vision ?? profile.vision) ? `<span class="model-menu-vision">${escapeHtml(t('attach.visionBadge'))}</span>` : ''}
        </button>`;
      }).join('');
      menu.innerHTML = `<div class="model-menu-block model-menu-block-models">
          <div class="model-menu-drill-head">
            <button type="button" class="model-menu-back" data-model-menu-back aria-label="${escAttr(t('settings.backToSubscriptions'))}" title="${escAttr(t('settings.backToSubscriptions'))}"><svg><use href="#i-chevron-left"/></svg></button>
            <span class="model-menu-drill-title">${escapeHtml(sub.name || t('settings.subscriptions'))}</span>
            <span class="model-menu-drill-count">${escapeHtml(t('settings.subscriptionModels', { n: (sub.models || []).length }))}</span>
          </div>
          <label class="model-menu-search">
            <svg><use href="#i-search"/></svg>
            <input type="search" data-model-menu-search value="${escAttr(state.modelMenuQuery || '')}" placeholder="${escAttr(t('settings.filterSubscriptionModels'))}" autocomplete="off" spellcheck="false" />
          </label>
          <div class="model-menu-models">${rows || modelMenuEmptyHtml(t('settings.noModels'))}</div>
          ${modelMenuEmptyHtml(t('settings.noModelMatch'))}
        </div>${manage}`;
    } else {
      const rows = subs.map((item) => {
        const active = subscriptionIsActive(item);
        const models = item.models || [];
        const provider = subscriptionProviderLabel(item.provider);
        const filterText = `${item.name || ''} ${item.base_url || ''} ${provider} ${models.map((p) => `${profileLabel(p)} ${p.model || ''}`).join(' ')}`;
        return `<button type="button" class="model-menu-sub${active ? ' active' : ''}" data-model-sub-pick="${escAttr(item.id)}" data-menu-filter="${escAttr(modelMenuFilterKey(filterText))}" role="option" aria-selected="${active}">
          <span class="model-menu-sub-icon"><svg><use href="#i-cache"/></svg></span>
          <span class="model-menu-main">
            <span class="model-menu-name">${escapeHtml(item.name || provider)}</span>
            <span class="model-menu-id">${escapeHtml(provider)} · ${escapeHtml(t('settings.subscriptionModels', { n: models.length }))}</span>
          </span>
          <span class="model-menu-check">${active ? '<svg><use href="#i-check"/></svg>' : ''}</span>
          <svg class="model-menu-chevron"><use href="#i-chevron-right"/></svg>
        </button>`;
      }).join('');
      menu.innerHTML = `<div class="model-menu-block model-menu-block-models">
          <div class="model-menu-head">${escapeHtml(t('settings.selectSubscription'))}</div>
          <label class="model-menu-search">
            <svg><use href="#i-search"/></svg>
            <input type="search" data-model-menu-search value="${escAttr(state.modelMenuQuery || '')}" placeholder="${escAttr(t('settings.filterSubscriptions'))}" autocomplete="off" spellcheck="false" />
          </label>
          <div class="model-menu-hint">${escapeHtml(t('settings.selectSubscriptionHint'))}</div>
          <div class="model-menu-models model-menu-subs">${rows || modelMenuEmptyHtml(t('settings.noModels'))}</div>
          ${modelMenuEmptyHtml(t('settings.noSubscriptionMatch'))}
        </div>${manage}`;
    }
    applyModelMenuFilter();
  }

  function applyModelMenuFilter() {
    const menu = $('#modelMenu');
    if (!menu) return;
    const input = $('[data-model-menu-search]', menu);
    const q = modelMenuFilterKey(input ? input.value : state.modelMenuQuery);
    let visible = 0;
    $$('[data-menu-filter]', menu).forEach((row) => {
      const on = !q || row.dataset.menuFilter.includes(q);
      row.hidden = !on;
      if (on) visible += 1;
    });
    const empty = $('[data-model-menu-empty]', menu);
    if (empty) empty.hidden = visible > 0;
  }

  async function openModelMenu() {
    const menu = $('#modelMenu');
    if (!menu || !menu.hidden) return;
    closeThinkingMenu();
    try {
      const data = await api('/app/models');
      state.models = (data && data.items) || [];
      state.subscriptions = (data && data.subscriptions) || state.subscriptions || [];
      state.activeModel = activeModelCode();
      state.modelMenuLevel = 'subscriptions';
      state.modelMenuSubscription = '';
      state.modelMenuQuery = '';
      renderModelMenu();
      const composer = $('#composerBox');
      const topbar = document.querySelector('.topbar');
      const rect = composer ? composer.getBoundingClientRect() : null;
      const topbarBottom = topbar ? topbar.getBoundingClientRect().bottom : 0;
      const available = rect ? Math.max(220, rect.top - topbarBottom - 12) : 560;
      menu.style.maxHeight = `${Math.min(560, Math.round(available))}px`;
      menu.hidden = false;
      $('#modelChip').setAttribute('aria-expanded', 'true');
    } catch (err) {
      toast(err.message, 'bad', 6000);
    }
  }

  async function toggleModelMenu() {
    const menu = $('#modelMenu');
    if (!menu) return;
    if (!menu.hidden) {
      closeModelMenu();
      return;
    }
    await openModelMenu();
  }

  async function activateModel(code) {
    try {
      const view = await api('/app/models/active', {
        method: 'POST',
        body: JSON.stringify({ code }),
      });
      state.app.llm = view;
      state.settings = view;
      state.models = view.models || state.models;
      state.activeModel = view.active_model || code || 'default';
      if (state.conv) state.conv.model_code = code;
      closeModelMenu();
      syncThinkingLevels();
      if (state.currentId) {
        saveConversationPrefs({
          model_code: code,
          thinking_level: state.thinkingLevel || '',
        });
      }
      renderHeader();
      const modal = $('#settingsModal');
      if (modal && !modal.hidden) {
        // Keep any unsaved profile edits; only move the active marker.
        $$('#modelList .model-card').forEach((card) => {
          const isActive = card.dataset.modelCode === state.activeModel;
          card.classList.toggle('active', isActive);
          const badge = $('[data-model-active-badge]', card);
          if (badge) badge.hidden = !isActive;
          const radio = $('input[name="activeModel"]', card);
          if (radio) radio.checked = isActive;
        });
      }
      const active = state.models.find((m) => m.code === state.activeModel);
      toast(t('settings.modelActivated', { name: profileLabel(active) }), 'ok', 2200);
    } catch (err) {
      toast(err.message, 'bad', 6000);
    }
  }

  const SETTINGS_TABS = ['workspaces', 'models', 'usage', 'mcp', 'skills', 'extensions', 'appearance', 'tools', 'runtime'];

  function setTabCount(tab, n) {
    const el = document.getElementById(`tabCount-${tab}`);
    if (!el) return;
    el.textContent = String(n || 0);
    el.hidden = !n;
  }

  function setSettingsTab(tab) {
    const active = SETTINGS_TABS.includes(tab) ? tab : 'workspaces';
    state.settingsTab = active;
    const modal = $('#settingsModal');
    if (!modal) return;
    const dialog = $('.settings-modal', modal) || modal;
    dialog.classList.toggle('models-wide', active === 'models');
    modal.querySelectorAll('.settings-tab').forEach((btn) => {
      const on = btn.dataset.settingsTab === active;
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    modal.querySelectorAll('.settings-panel').forEach((panel) => {
      panel.hidden = panel.dataset.settingsPanel !== active;
    });
    // 右栏独立滚动：切 tab 时回到顶部，左栏 tab 位置不动（两栏互不影响）。
    const panels = modal.querySelector('.settings-panels');
    if (panels) panels.scrollTop = 0;
    const saveBtn = $('#saveSettingsBtn');
    if (saveBtn) saveBtn.hidden = active !== 'models';
    if (active === 'usage') loadUsageStats();
    if (active === 'appearance') {
      renderThemeGrid();
      syncThemeScheduleControls();
      renderThemeScheduleNote();
    }
    if (active === 'extensions') { loadExtensions(); }
    if (active === 'tools') renderToolsCatalog();
    if (active === 'runtime') {
      loadCodegraphStats();
      loadDiagnostics();
    }
  }

  function openSettings(tab) {
    if (!state.app) return;
    closeModelMenu();
    // 不再顺手关掉右侧面板：打开设置不影响 dock 的展开状态。
    $('#settingsNote').textContent = '';
    $('#settingsNote').className = 'save-note';
    $('#aboutService').textContent = `${state.app.name} ${state.app.version}`;
    $('#aboutUrl').textContent = state.app.url || '';
    $('#aboutConfig').textContent = state.app.config_path || '';
    renderToolsCatalog();
    syncLanguageControls();
    // 模型卡片的展开 / 收起沿用用户上次点的结果，打开设置不再自动全部收起。
    $('#settingsModal').hidden = false;
    setSettingsTab(tab || state.settingsTab || 'workspaces');
    loadModels();
    loadSettingsWorkspaces();
    loadMcp();
    loadSkills();
    loadExtensions();
    loadCodegraphStats();
    setTimeout(() => {
      const btn = $('#settingsModal .modal-head [data-close]');
      if (btn) btn.focus();
    }, 40);
  }

  async function loadDiagnostics() {
    const set = (id, text, tone) => {
      const el = $(`#${id}`);
      if (!el) return;
      el.textContent = text;
      if (tone) el.dataset.tone = tone;
    };
    try {
      const [m, b] = await Promise.all([api('/app/metrics'), api('/app/backups')]);
      const r = m.runs || {};
      const now = m.now || {};
      set('diagSuccess', `${Number(r.success_rate || 0).toFixed(1)}%`);
      set('diagRuns', `${r.total || 0} · ${r.done || 0}/${r.failed || 0}/${r.stopped || 0}`,
        r.failed ? 'warn' : 'ok');
      set('diagP95', `${Math.round(Number(r.p95_s || 0))}s`);
      set('diagFailed', String(r.failed || 0), r.failed ? 'warn' : 'ok');
      set('diagQueue', `${now.queued || 0} / ${now.running || 0}`);
      set('diagDb', bytes(Number((m.db && m.db.size) || 0)));
      const latest = b && b.latest;
      set('diagBackup', latest ? `${latest.created} · ${bytes(latest.size)}` : '—');
      const errEl = $('#diagErrors');
      if (errEl) {
        const errs = Object.entries(m.errors || {});
        errEl.hidden = !errs.length;
        errEl.textContent = errs.map(([k, v]) => `${k} × ${v}`).join(' · ');
      }
    } catch (err) {
      console.warn('diagnostics metrics failed', err);
    }
    try {
      const res = await fetch('/healthz');
      const h = await res.json();
      set('diagHealth', h.ok ? t('diag.healthy') : t('diag.degraded'),
        h.ok ? 'ok' : 'bad');
    } catch {
      set('diagHealth', t('diag.degraded'), 'bad');
    }
  }

  async function exportDiagnostics() {
    try {
      const a = document.createElement('a');
      a.href = `${API}/app/diagnostics`;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
      toast(t('diag.exporting'), 'ok', 2600);
    } catch (err) {
      toast(String(err.message || err), 'bad');
    }
  }

  async function backupNow() {
    const btn = $('#diagBackupBtn');
    if (btn) btn.disabled = true;
    try {
      const info = await api('/app/backups', { method: 'POST' });
      toast(t('diag.backupDone', { name: info.name }), 'ok');
      await loadDiagnostics();
    } catch (err) {
      toast(String(err.message || err), 'bad');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function loadCodegraphStats() {
    const wsId = workspaceId();
    if (!wsId) return;
    try {
      const data = await api(`/app/codegraph?workspace_id=${encodeURIComponent(wsId)}`);
      if ($('#aboutCodegraphFiles')) $('#aboutCodegraphFiles').textContent = num(data.files || 0);
      if ($('#aboutCodegraphSymbols')) $('#aboutCodegraphSymbols').textContent = num(data.symbols || 0);
      if ($('#aboutCodegraphDeps')) $('#aboutCodegraphDeps').textContent = num(data.edges || 0);
      if ($('#aboutCodegraphNote')) {
        $('#aboutCodegraphNote').textContent = t('settings.codegraphUpdated', { time: data.updated || '—' });
      }
    } catch (err) {
      if ($('#aboutCodegraphFiles')) $('#aboutCodegraphFiles').textContent = '—';
    }
  }

  async function refreshCodegraph() {
    const wsId = workspaceId();
    if (!wsId) return;
    const btn = $('#codegraphRefreshBtn');
    if (btn) btn.disabled = true;
    try {
      const data = await api('/app/codegraph/refresh', {
        method: 'POST',
        body: JSON.stringify({ workspace_id: wsId }),
      });
      if ($('#aboutCodegraphFiles')) $('#aboutCodegraphFiles').textContent = num(data.files || 0);
      if ($('#aboutCodegraphSymbols')) $('#aboutCodegraphSymbols').textContent = num(data.symbols || 0);
      if ($('#aboutCodegraphDeps')) $('#aboutCodegraphDeps').textContent = num(data.edges || 0);
      if ($('#aboutCodegraphNote')) {
        $('#aboutCodegraphNote').textContent = t('settings.codegraphUpdated', { time: data.updated || '—' });
      }
      toast(t('settings.codegraphRefreshed'), 'ok', 2600);
    } catch (err) {
      toast(t('filemenu.failed', { msg: err.message }), 'bad', 4000);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  const THINKING_PRESETS = ['minimal', 'low', 'medium', 'high'];

  function modelThinkingLevels(profile) {
    if (!profile) return [];
    const own = profile.thinking_levels;
    if (Array.isArray(own)) return own.filter(Boolean);
    return (profile.effective_thinking_levels || []).filter(Boolean);
  }

  function thinkingLevelChipHtml(level, checked, custom) {
    const label = thinkingLevelLabel(level);
    return `<label class="thinking-level${custom ? ' custom' : ''}">
      <input type="checkbox" data-thinking-level value="${escAttr(level)}"${checked ? ' checked' : ''} />
      <span>${escapeHtml(label)}</span>
      ${custom ? `<button type="button" class="thinking-level-remove" data-thinking-remove title="${escAttr(t('settings.thinkingRemove'))}" aria-label="${escAttr(t('settings.thinkingRemove'))}"><svg><use href="#i-x"/></svg></button>` : ''}
    </label>`;
  }

  function modelDisplayName(profile) {
    if (!profile) return t('header.notConfigured');
    return profile.model || profile.effective_model || profile.name || t('settings.newModel');
  }

  function channelIdForProfile(profile) {
    const explicit = String(profile.subscription_id || profile.channel_id || '').trim();
    if (explicit) return explicit;
    if (profile.code === 'default') return 'channel-default';
    const base = String(profile.base_url || profile.effective_base_url || '').replace(/\/+$/, '');
    if (!base) return 'channel-local';
    let h = 0;
    for (let i = 0; i < base.length; i += 1) {
      h = ((h * 31) + base.charCodeAt(i)) >>> 0;
    }
    return `base-${h.toString(36)}`;
  }

  function channelNameForProfile(profile) {
    if (profile.subscription_name) return profile.subscription_name;
    if (profile.channel_name) return profile.channel_name;
    const base = String(profile.base_url || profile.effective_base_url || '');
    if (!base) return t('settings.localChannel');
    try { return new URL(base).host; } catch (_) { return base; }
  }

  function subscriptionProviderFor(value) {
    const text = String(value || '').toLowerCase();
    if (text.includes('opencode.ai/inference/go')) return 'opencode-go';
    if (text.includes('opencode.ai/inference/')) return 'opencode-zen';
    return 'custom';
  }

  function subscriptionProviderLabel(provider) {
    if (provider === 'opencode-go') return t('settings.providerOpenCodeGo');
    if (provider === 'opencode-zen') return t('settings.providerOpenCodeZen');
    if (provider === 'custom' || !provider) return t('settings.providerCustom');
    const preset = (state.modelPresets || []).find(
      (item) => item.code === provider || item.provider === provider);
    if (preset && preset.name) return preset.name;
    return String(provider);
  }

  function subscriptionProtocolLabel(protocol) {
    if (protocol === 'anthropic') return t('settings.protocolAnthropicShort');
    if (protocol === 'openai-response') return t('settings.protocolResponseShort');
    return t('settings.protocolChatShort');
  }

  function subscriptionHost(url) {
    const text = String(url || '').trim();
    if (!text) return '';
    try { return new URL(text).host; } catch (_) { return text; }
  }

  function inferredAnthropicBaseUrl(baseUrl) {
    const text = String(baseUrl || '').toLowerCase();
    if (text.includes('opencode.ai/inference/go/openai')) {
      return 'https://opencode.ai/inference/go/anthropic';
    }
    if (text.includes('opencode.ai/inference/openai')) {
      return 'https://opencode.ai/inference/anthropic';
    }
    return '';
  }

  function buildSubscriptions() {
    const map = new Map();
    for (const raw of (state.subscriptions || [])) {
      if (!raw || !raw.id) continue;
      const preset = (state.modelPresets || []).find((item) => item.code === raw.provider);
      map.set(raw.id, {
        ...raw,
        id: raw.id,
        name: raw.name || '',
        provider: raw.provider || subscriptionProviderFor(raw.base_url),
        protocol: raw.protocol || (preset && preset.protocol) || 'openai-chat',
        base_url: raw.base_url || '',
        anthropic_base_url: raw.anthropic_base_url || inferredAnthropicBaseUrl(raw.base_url),
        note: raw.note || (preset && preset.note) || '',
        has_key: !!raw.has_api_key,
        models: [],
      });
    }
    for (const profile of (state.models || [])) {
      const id = channelIdForProfile(profile);
      let sub = map.get(id);
      if (!sub) {
        sub = {
          id,
          name: channelNameForProfile(profile),
          provider: subscriptionProviderFor(profile.base_url || profile.effective_base_url),
          protocol: profile.protocol || profile.effective_protocol || 'openai-chat',
          base_url: profile.base_url || profile.effective_base_url || '',
          anthropic_base_url: inferredAnthropicBaseUrl(profile.base_url || profile.effective_base_url),
          note: '',
          has_key: false,
          models: [],
        };
        map.set(id, sub);
      }
      sub.models.push(profile);
      if (!sub.name) sub.name = channelNameForProfile(profile);
      if (!sub.base_url) sub.base_url = profile.base_url || profile.effective_base_url || '';
      if (!sub.anthropic_base_url) sub.anthropic_base_url = inferredAnthropicBaseUrl(sub.base_url);
      if (profile.api_key || profile.has_api_key || profile.effective_api_key) sub.has_key = true;
    }
    return Array.from(map.values());
  }

  function subscriptionById(id) {
    return buildSubscriptions().find((sub) => sub.id === id) || null;
  }

  function ensureSelectedSubscription() {
    const subs = buildSubscriptions();
    if (!subs.length) {
      state.selectedSubscription = '';
      return null;
    }
    let selected = subs.find((sub) => sub.id === state.selectedSubscription);
    if (!selected) {
      selected = subs[0];
      state.selectedSubscription = selected.id;
    }
    return selected;
  }

  function updateModelPaneContext(sub) {
    const el = $('#modelsPaneContext');
    if (!el) return;
    el.textContent = sub
      ? `${sub.name || subscriptionProviderLabel(sub.provider)} · ${t('settings.subscriptionModels', { n: (sub.models || []).length })}`
      : '';
    el.hidden = !sub;
  }

  function modelCardHtml(profile) {
    const code = profile.code;
    const isActive = code === state.activeModel;
    const title = modelDisplayName(profile);
    const modelId = profile.effective_model || profile.model || '';
    const contextWindow = profile.context_window ? String(profile.context_window) : '';
    const maxTokens = profile.max_tokens ? String(profile.max_tokens) : '';
    const isOpen = state.openModelCards.has(code);
    const protocol = profile.protocol || profile.effective_protocol || 'openai-chat';
    const thinkingLevels = modelThinkingLevels(profile);
    const customThinking = thinkingLevels.filter((l) => !THINKING_PRESETS.includes(l));
    const searchText = `${title} ${modelId} ${profile.name || ''} ${profile.code || ''}`;
    return `<section class="model-card${isActive ? ' active' : ''}${isOpen ? ' open' : ''}" data-model-code="${escAttr(code)}" data-search-text="${escAttr(searchText.toLowerCase())}">
      <div class="model-card-head">
        <label class="model-choose" title="${escAttr(t('settings.useModel'))}" data-i18n-title="settings.useModel">
          <input type="radio" name="activeModel" value="${escAttr(code)}"${isActive ? ' checked' : ''} />
          <span class="model-card-name">${escapeHtml(title)}</span>
        </label>
        <span class="model-card-id mono"${modelId && modelId !== title ? '' : ' hidden'}>${escapeHtml(modelId)}</span>
        <span class="model-badge model-protocol"${protocol === 'openai-response' ? '' : ' hidden'} data-i18n-title="settings.protocol">Responses</span>
        <span class="model-badge model-anthropic"${protocol === 'anthropic' ? '' : ' hidden'} data-i18n-title="settings.protocol">Anthropic</span>
        <span class="model-badge model-vision"${(profile.effective_vision ?? profile.vision) ? '' : ' hidden'} data-i18n-title="attach.visionBadgeTitle" data-i18n="attach.visionBadge">${escapeHtml(t('attach.visionBadge'))}</span>
        <span class="model-badge" data-model-active-badge data-i18n="settings.active"${isActive ? '' : ' hidden'}>${escapeHtml(t('settings.active'))}</span>
        <button class="model-toggle" type="button" data-model-toggle title="${escAttr(t('settings.edit'))}" aria-label="${escAttr(t('settings.edit'))}" aria-expanded="${isOpen ? 'true' : 'false'}" data-i18n-title="settings.edit" data-i18n-aria-label="settings.edit">
          <svg><use href="#i-chevron-down"/></svg>
        </button>
        <button class="icon-btn model-delete" type="button" data-model-delete title="${escAttr(t('settings.deleteModel'))}" aria-label="${escAttr(t('settings.deleteModel'))}" data-i18n-title="settings.deleteModel" data-i18n-aria-label="settings.deleteModel"><svg><use href="#i-trash"/></svg></button>
      </div>
      <div class="model-card-body"${isOpen ? '' : ' hidden'}>
        <label class="field">
          <span data-i18n="settings.name">Name</span>
          <input class="input" data-model-field="name" value="${escAttr(profile.name || '')}" placeholder="${escAttr(modelId || 'Model')}" spellcheck="false" />
        </label>
        <label class="field">
          <span data-i18n="settings.model">Model</span>
          <input class="input mono" data-model-field="model" list="modelOptions" value="${escAttr(profile.model || '')}" placeholder="${escAttr(modelId || 'gpt-4o-mini')}" spellcheck="false" />
        </label>
        <label class="field">
          <span data-i18n="settings.modelBaseUrl">Base URL override</span>
          <input class="input mono" data-model-field="base_url" type="url" value="${escAttr(profile.base_url || '')}" placeholder="${escAttr(profile.effective_base_url || t('settings.useSubscriptionUrl'))}" spellcheck="false" />
          <small data-i18n="settings.modelBaseUrlNote">Optional. Leave empty to use the subscription endpoint selected by this model's protocol.</small>
        </label>
        <label class="field">
          <span data-i18n="settings.protocol">API protocol</span>
          <select class="input" data-model-field="protocol">
            <option value="openai-chat"${protocol === 'openai-chat' ? ' selected' : ''} data-i18n="settings.protocolChat">OpenAI Chat (/chat/completions)</option>
            <option value="openai-response"${protocol === 'openai-response' ? ' selected' : ''} data-i18n="settings.protocolResponse">OpenAI Responses (/responses)</option>
            <option value="anthropic"${protocol === 'anthropic' ? ' selected' : ''} data-i18n="settings.protocolAnthropic">Anthropic Messages (/v1/messages)</option>
          </select>
        </label>
        <div class="field-row">
          <label class="field">
            <span data-i18n="settings.contextWindow">Context window</span>
            <input class="input mono" data-model-field="context_window" type="number" min="0" step="1000" value="${escAttr(contextWindow)}" />
          </label>
          <label class="field">
            <span data-i18n="settings.maxTokens">Max tokens</span>
            <input class="input mono" data-model-field="max_tokens" type="number" min="0" step="1" value="${escAttr(maxTokens)}" />
          </label>
        </div>
        <div class="field model-vision-field">
          <span data-i18n="settings.vision">Image support</span>
          <label class="switch">
            <input type="checkbox" data-model-field="vision"${(profile.effective_vision ?? profile.vision) ? ' checked' : ''} />
            <span class="track"></span>
            <span class="switch-label" data-i18n="settings.visionLabel">This model can read images</span>
          </label>
          <small data-i18n="settings.visionNote">Allow this model to receive uploaded images (vision).</small>
        </div>
        <div class="field model-thinking-field">
          <span data-i18n="settings.thinkingLevels">Thinking levels</span>
          <div class="thinking-levels">
            ${THINKING_PRESETS.map((level) => thinkingLevelChipHtml(
              level, thinkingLevels.includes(level), false)).join('')}
            ${customThinking.map((level) => thinkingLevelChipHtml(
              level, true, true)).join('')}
          </div>
          <div class="thinking-custom-row">
            <input class="input mono" data-thinking-custom value="" placeholder="${escAttr(t('settings.thinkingCustomPlaceholder'))}" data-i18n-placeholder="settings.thinkingCustomPlaceholder" spellcheck="false" />
            <button type="button" class="btn small ghost" data-thinking-add data-i18n="settings.thinkingAdd">${escapeHtml(t('settings.thinkingAdd'))}</button>
          </div>
          <small data-i18n="settings.thinkingLevelsNote">Check the levels this model accepts. Checked levels appear in the composer; leave all unchecked to hide the selector.</small>
        </div>
      </div>
    </section>`;
  }

  function modelGroupHtml(sub, flat = false) {
    const modelsHtml = sub.models.length
      ? sub.models.map(modelCardHtml).join('')
      : `<div class="model-empty compact">${escapeHtml(t('settings.subscriptionNoModels'))}</div>`;
    const provider = subscriptionProviderLabel(sub.provider);
    const searchText = `${sub.name || ''} ${provider} ${sub.base_url || ''}`;
    const head = flat ? '' : `<div class="model-group-head">
        <span class="model-group-icon"><svg><use href="#i-cache"/></svg></span>
        <span class="model-group-title">${escapeHtml(sub.name || provider)}</span>
        <span class="model-group-provider">${escapeHtml(provider)}</span>
        <span class="model-group-count">${escapeHtml(t('settings.subscriptionModels', { n: sub.models.length }))}</span>
        <button type="button" class="btn small ghost model-group-add" data-group-add="${escAttr(sub.id)}">
          <svg><use href="#i-plus"/></svg><span>${escapeHtml(t('settings.addModel'))}</span>
        </button>
      </div>`;
    return `<section class="model-group${flat ? ' model-group-flat' : ''}" data-subscription-id="${escAttr(sub.id)}" data-search-text="${escAttr(searchText.toLowerCase())}">
      ${head}
      <div class="channel-models">${modelsHtml}</div>
    </section>`;
  }

  function subscriptionCardHtml(sub) {
    const provider = subscriptionProviderLabel(sub.provider);
    const active = subscriptionIsActive(sub);
    const selected = state.selectedSubscription === sub.id;
    const name = sub.name || provider;
    const host = subscriptionHost(sub.base_url);
    const meta = [
      host || provider,
      t('settings.subscriptionModels', { n: sub.models.length }),
    ].filter(Boolean).join(' · ');
    const searchText = [name, provider, sub.base_url, sub.protocol]
      .filter(Boolean).join(' ').toLowerCase();
    return `<section class="subscription-card channel-card${selected ? ' selected' : ''}" data-subscription-id="${escAttr(sub.id)}" data-provider="${escAttr(sub.provider || 'custom')}" data-search-text="${escAttr(searchText)}" aria-current="${selected ? 'true' : 'false'}" title="${escAttr(t('settings.subscriptionContextHint'))}">
      <div class="subscription-head">
        <span class="subscription-icon"><svg><use href="#i-cache"/></svg></span>
        <div class="subscription-title">
          <span class="subscription-name">${escapeHtml(name)}</span>
        </div>
        ${selected ? `<span class="subscription-selected-mark" title="${escAttr(t('settings.selectedSubscription'))}"><svg><use href="#i-check"/></svg></span>` : ''}
        <div class="subscription-subrow">
          <span class="subscription-meta">${escapeHtml(meta)}</span>
          ${active ? `<span class="model-badge subscription-active-badge">${escapeHtml(t('settings.active'))}</span>` : ''}
          ${provider !== name ? `<span class="subscription-badge">${escapeHtml(provider)}</span>` : ''}
        </div>
        <div class="subscription-quota-row" data-quota-summary hidden></div>
      </div>
    </section>`;
  }

  function renderModelCards(openCode) {
    const list = $('#modelList');
    if (!list) return;
    setTabCount('models', state.models.length);
    if (openCode) state.openModelCards.add(openCode);
    const sub = ensureSelectedSubscription();
    updateModelPaneContext(sub);
    const discoverBtn = $('#discoverModelsBtn');
    if (discoverBtn) discoverBtn.disabled = !sub;
    if (!sub) {
      list.innerHTML = `<div class="model-empty">${escapeHtml(t('settings.noSubscriptionSelected'))}</div>`;
      return;
    }
    if (!sub.models.length) {
      list.innerHTML = `<div class="model-empty">${escapeHtml(t('settings.subscriptionNoModels'))}</div>`;
      return;
    }
    list.innerHTML = modelGroupHtml(sub, true);
    translateStatic();
    refreshModelPlaceholders();
    applyModelFilter();
  }

  function applyModelFilter() {
    const q = String(state.modelFilter || '').toLowerCase().trim();
    $$('#modelList .model-group').forEach((group) => {
      const groupMatch = !q || (group.dataset.searchText || '').includes(q);
      let visible = 0;
      $$('.model-card', group).forEach((card) => {
        const on = groupMatch || (card.dataset.searchText || '').includes(q);
        card.hidden = !on;
        if (on) visible += 1;
      });
      const empty = $('.model-empty', group);
      if (empty) empty.hidden = visible > 0;
      group.hidden = q ? visible === 0 : false;
    });
  }

  function applySubscriptionFilter() {
    const q = String(state.subscriptionFilter || '').toLowerCase().trim();
    $$('#subscriptionList .subscription-card').forEach((card) => {
      card.hidden = q ? !(card.dataset.searchText || '').includes(q) : false;
    });
  }

  function selectSubscription(subId) {
    if (!subId) return;
    if (state.selectedSubscription === subId) {
      renderModelCards();
      return;
    }
    state.selectedSubscription = subId;
    state.modelFilter = '';
    const filter = $('#modelFilterInput');
    if (filter) filter.value = '';
    renderSubscriptionCards();
    renderModelCards();
  }

  const API_KEY_MASK = '••••••••';

  function subscriptionKey(sub) {
    const key = String((sub && sub.api_key) || '');
    return key === API_KEY_MASK ? '' : key;
  }

  // 订阅卡片只做展示：state.subscriptions 是唯一数据源，保存时直接序列化。
  function readSubscriptionCards() {
    return (state.subscriptions || [])
      .filter((sub) => sub && sub.id)
      .map((sub) => {
        const item = {
          id: sub.id,
          name: sub.name || '',
          provider: sub.provider || 'custom',
          protocol: sub.protocol || 'openai-chat',
          base_url: sub.base_url || '',
          anthropic_base_url: sub.anthropic_base_url || inferredAnthropicBaseUrl(sub.base_url),
          note: sub.note || '',
          has_api_key: !!(sub.has_api_key || sub.has_key),
        };
        const key = subscriptionKey(sub);
        if (key) item.api_key = key;
        return item;
      });
  }

  function renderSubscriptionCards() {
    const list = $('#subscriptionList');
    if (!list) return;
    const subs = buildSubscriptions();
    setTabCount('subscriptions', subs.length);
    if (!subs.length) {
      list.innerHTML = `<div class="model-empty">${escapeHtml(t('settings.subscriptionsNote'))}</div>`;
      return;
    }
    list.innerHTML = subs.map(subscriptionCardHtml).join('');
    translateStatic();
    applySubscriptionFilter();
    for (const sub of subs) loadCardQuota(sub);
  }

  function nextSubscriptionId(baseId) {
    const base = baseId || 'subscription';
    let id = base;
    let n = 2;
    const used = new Set((state.subscriptions || []).map((s) => s.id));
    while (used.has(id)) id = `${base}-${n++}`;
    return id;
  }

  function subscriptionDraftFromPreset(presetCode) {
    const preset = (state.modelPresets || []).find((p) => p.code === presetCode) || {};
    const isCustom = presetCode === 'custom' || (!preset.code && !preset.base_url);
    const provider = isCustom ? 'custom' : (preset.provider || presetCode || 'custom');
    const label = isCustom
      ? t('settings.subscriptionCustomName')
      : (preset.name || subscriptionProviderLabel(provider));
    const sameProvider = (state.subscriptions || [])
      .filter((s) => (s.provider || 'custom') === provider).length;
    return {
      id: nextSubscriptionId(isCustom ? 'subscription' : (preset.code || provider)),
      name: sameProvider ? `${label} ${sameProvider + 1}` : label,
      provider,
      protocol: preset.protocol || 'openai-chat',
      base_url: preset.base_url || '',
      anthropic_base_url: preset.anthropic_base_url || '',
      models_url: preset.models_url || '',
      note: preset.note || '',
      api_key: preset.api_key || '',
      has_api_key: !!preset.api_key,
    };
  }

  // 没有任何订阅时兜底：静默建一个自定义订阅，保证模型一定有归属。
  function addSubscription(presetCode) {
    const draft = subscriptionDraftFromPreset(presetCode);
    state.subscriptions.push(draft);
    state.selectedSubscription = draft.id;
    return draft;
  }

  let subscriptionModalMode = 'add';
  let subscriptionModalDraft = null;

  function openSubscriptionModal(options = {}) {
    const modal = $('#subscriptionModal');
    if (!modal) return;
    const subId = options.subId || '';
    let sub;
    if (subId) {
      const found = subscriptionById(subId);
      if (!found) return;
      sub = { ...found, has_api_key: !!found.has_key || !!found.has_api_key };
      subscriptionModalMode = options.mode === 'view' ? 'view' : 'edit';
    } else {
      sub = subscriptionDraftFromPreset(options.presetCode || 'custom');
      subscriptionModalMode = 'add';
    }
    subscriptionModalDraft = sub;
    const viewMode = subscriptionModalMode === 'view';
    modal.classList.toggle('view-mode', viewMode);
    const titleKey = viewMode
      ? 'settings.viewSubscription'
      : subscriptionModalMode === 'edit'
        ? 'settings.editSubscription' : 'settings.addSubscription';
    $('#subscriptionModalTitle').textContent = t(titleKey);
    const nameInput = $('#subscriptionModalName');
    const baseInput = $('#subscriptionModalBaseUrl');
    const anthropicInput = $('#subscriptionModalAnthropicBaseUrl');
    nameInput.value = sub.name || '';
    baseInput.value = sub.base_url || '';
    anthropicInput.value = sub.anthropic_base_url || '';
    nameInput.readOnly = viewMode;
    baseInput.readOnly = viewMode;
    anthropicInput.readOnly = viewMode;
    const keyField = $('#subscriptionModalKeyField');
    const keyInput = $('#subscriptionModalApiKey');
    keyInput.value = viewMode ? '' : (subscriptionKey(sub) || sub.default_api_key || '');
    keyInput.placeholder = sub.has_api_key
      ? t('settings.apiKeyUnchanged') : t('settings.apiKeyNotSet');
    keyField.hidden = viewMode;
    const keyState = $('#subscriptionModalKeyState');
    keyState.hidden = !viewMode;
    if (viewMode) {
      keyState.textContent = sub.has_api_key
        ? t('settings.apiKeyStateSet', { mask: API_KEY_MASK })
        : t('settings.apiKeyStateUnset');
    }
    $('#subscriptionModalSave').hidden = viewMode;
    const closeBtn = $('#subscriptionModalClose');
    if (closeBtn) {
      closeBtn.textContent = t(viewMode ? 'close' : 'cancel');
      closeBtn.dataset.i18n = viewMode ? 'close' : 'cancel';
    }
    const note = $('#subscriptionModalNote');
    note.textContent = sub.note || '';
    note.hidden = !sub.note;
    const status = $('#subscriptionModalStatus');
    status.textContent = '';
    status.className = 'save-note';
    const quota = $('#subscriptionModalQuota');
    quota.hidden = true;
    quota.innerHTML = '';
    modal.hidden = false;
    if (subscriptionModalMode === 'edit' || viewMode) loadChannelQuota(quota, sub);
    if (!viewMode) setTimeout(() => $('#subscriptionModalName').focus(), 40);
  }

  function closeSubscriptionModal() {
    const modal = $('#subscriptionModal');
    if (modal) modal.hidden = true;
    subscriptionModalDraft = null;
  }

  function saveSubscriptionModal() {
    const draft = subscriptionModalDraft;
    if (!draft || subscriptionModalMode === 'view') return;
    const name = $('#subscriptionModalName').value.trim();
    const baseUrl = $('#subscriptionModalBaseUrl').value.trim();
    const anthropic = $('#subscriptionModalAnthropicBaseUrl').value.trim();
    const protocol = draft.protocol || 'openai-chat';
    const key = $('#subscriptionModalApiKey').value.trim();
    const record = {
      ...draft,
      name: name || draft.name || subscriptionProviderLabel(draft.provider),
      base_url: baseUrl,
      anthropic_base_url: anthropic || inferredAnthropicBaseUrl(baseUrl),
      protocol,
    };
    delete record.models;
    delete record.has_key;
    if (key) {
      record.api_key = key;
      record.has_api_key = true;
    } else if (record.api_key && record.api_key !== API_KEY_MASK) {
      record.has_api_key = true;
    } else {
      record.has_api_key = !!draft.has_api_key;
    }
    const existing = (state.subscriptions || []).find((s) => s.id === record.id);
    if (existing) Object.assign(existing, record);
    else state.subscriptions.push(record);
    state.selectedSubscription = record.id;
    state.modelFilter = '';
    const filter = $('#modelFilterInput');
    if (filter) filter.value = '';
    closeSubscriptionModal();
    renderSubscriptionCards();
    renderModelCards();
    toast(t(existing ? 'settings.subscriptionUpdated' : 'settings.subscriptionAdded'), 'ok', 2400);
  }

  function openSubscriptionMenu(x, y, subId) {
    const sub = subscriptionById(subId);
    if (!sub) return;
    openUiMenu(x, y, [
      {
        icon: 'eye',
        label: t('settings.view'),
        action: () => openSubscriptionModal({ subId, mode: 'view' }),
      },
      {
        icon: 'edit',
        label: t('settings.edit'),
        action: () => openSubscriptionModal({ subId }),
      },
      { separator: true },
      {
        icon: 'trash',
        label: t('settings.deleteSubscription'),
        danger: true,
        action: () => deleteSubscription(subId),
      },
    ]);
  }

  function platformGroupLabel(group) {
    const key = `platform.group.${group || 'official'}`;
    const value = t(key);
    return value === key ? t('platform.group.official') : value;
  }

  function platformPresetSearchText(preset) {
    return [
      preset.code, preset.name, preset.provider, preset.group,
      preset.base_url, preset.anthropic_base_url, preset.note,
      preset.protocol,
    ].filter(Boolean).join(' ').toLowerCase();
  }

  function renderPlatformPicker(query = '') {
    const box = $('#platformGroups');
    if (!box) return;
    const q = String(query || '').toLowerCase().trim();
    const groups = ['custom', 'opencode', 'aggregator', 'local', 'official'];
    const customPreset = {
      code: 'custom',
      name: t('platform.custom'),
      group: 'custom',
      protocol: 'openai-chat',
      note: t('platform.customNote'),
    };
    const presets = [customPreset].concat(state.modelPresets || []);
    const items = presets.filter((preset) =>
      !q || platformPresetSearchText(preset).includes(q));
    if (!items.length) {
      box.innerHTML = `<div class="model-empty">${escapeHtml(t('platform.empty'))}</div>`;
      return;
    }
    box.innerHTML = groups.map((group) => {
      const rows = items.filter((preset) => (preset.group || 'official') === group);
      if (!rows.length) return '';
      const cards = rows.map((preset) => {
        const protocol = subscriptionProtocolLabel(preset.protocol || 'openai-chat');
        const host = subscriptionHost(preset.base_url || preset.anthropic_base_url);
        return `<button type="button" class="platform-item" data-platform-pick="${escAttr(preset.code)}">
          <span class="platform-item-main">
            <span class="platform-item-name">${escapeHtml(preset.name || preset.code)}</span>
            <span class="platform-item-meta">${escapeHtml(protocol)}${host ? ` · ${escapeHtml(host)}` : ''}${preset.note ? ` · ${escapeHtml(preset.note)}` : ''}</span>
          </span>
          ${preset.requires_gateway ? `<span class="platform-item-badge">${escapeHtml(t('platform.gateway'))}</span>` : ''}
          <span class="platform-item-add"><svg><use href="#i-plus"/></svg></span>
        </button>`;
      }).join('');
      return `<section class="platform-group">
        <h4>${escapeHtml(platformGroupLabel(group))}</h4>
        <div class="platform-group-list">${cards}</div>
      </section>`;
    }).join('');
  }

  function openPlatformPicker() {
    const modal = $('#platformPickerModal');
    if (!modal) return;
    const input = $('#platformSearchInput');
    if (input) input.value = '';
    renderPlatformPicker('');
    modal.hidden = false;
    if (!(state.modelPresets || []).length) {
      loadModels().then(() => renderPlatformPicker(input ? input.value : ''));
    }
    if (input) setTimeout(() => input.focus(), 30);
  }

  function deleteSubscription(subId) {
    const sub = subscriptionById(subId);
    if (!sub) return;
    const n = (sub.models || []).length;
    if (!window.confirm(t('settings.deleteSubscriptionConfirm', {
      name: sub.name || sub.id, n,
    }))) return;
    const snapshot = readModelCards();
    if (snapshot.allModels.some((m) => m.code === 'default'
      && channelIdForProfile(m) === subId)) state.defaultRemoved = true;
    state.subscriptions = (state.subscriptions || []).filter((s) => s.id !== subId);
    state.models = snapshot.allModels.filter((m) => channelIdForProfile(m) !== subId);
    if (state.selectedSubscription === subId) {
      const next = buildSubscriptions()[0];
      state.selectedSubscription = next ? next.id : '';
      state.modelFilter = '';
      const filter = $('#modelFilterInput');
      if (filter) filter.value = '';
    }
    if (!state.models.some((m) => m.code === state.activeModel)) {
      const fallback = state.models.find((m) => m.code !== 'default') || state.models[0];
      state.activeModel = fallback ? fallback.code : 'default';
    }
    renderSubscriptionCards();
    renderModelCards();
    fillModelOptions();
  }

  function fillModelOptions() {
    const datalist = $('#modelOptions');
    if (!datalist) return;
    const ids = Array.from(new Set((state.models || [])
      .map((m) => m.effective_model || m.model)
      .filter(Boolean)));
    datalist.innerHTML = ids
      .map((id) => `<option value="${escAttr(id)}"></option>`)
      .join('');
  }

  function refreshModelPlaceholders() {
    const byCode = new Map((state.models || []).map((m) => [m.code, m]));
    $$('#modelList .model-card').forEach((card) => {
      const profile = byCode.get(card.dataset.modelCode);
      if (!profile) return;
      const model = $('[data-model-field="model"]', card);
      if (model) {
        model.placeholder = (!profile.model && profile.effective_model)
          ? profile.effective_model
          : 'gpt-4o-mini';
      }
      const name = $('[data-model-field="name"]', card);
      if (name) {
        name.placeholder = profile.model || profile.effective_model || 'Model';
      }
      const base = $('[data-model-field="base_url"]', card);
      if (base) {
        base.placeholder = profile.effective_base_url || t('settings.useSubscriptionUrl');
      }
      const cw = $('[data-model-field="context_window"]', card);
      if (cw) cw.placeholder = profile.effective_context_window ? String(profile.effective_context_window) : '';
      const mt = $('[data-model-field="max_tokens"]', card);
      if (mt) mt.placeholder = profile.effective_max_tokens ? String(profile.effective_max_tokens) : '';
    });
  }

  function isGoChannel(channel) {
    return String((channel && channel.base_url) || '').includes('opencode.ai/inference/go/');
  }

  const QUOTA_CACHE_TTL = 60 * 1000;

  function formatQuotaPct(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    return `${Math.round(n * 10) / 10}%`;
  }

  function quotaSummaryHtml(data) {
    const windows = (data && data.windows) || {};
    const parts = [
      ['5h', windows.five_hour],
      ['1w', windows.weekly],
      ['1m', windows.monthly],
    ].filter(([, w]) => w && Number.isFinite(Number(w.percent)))
      .map(([label, w]) => {
        const pct = Number(w.percent);
        const cls = pct >= 95 ? ' bad' : pct >= 80 ? ' warn' : ' ok';
        return `<span class="sub-quota-item"><span class="sub-quota-label">${label}</span> `
          + `<b class="sub-quota-pct${cls}">${formatQuotaPct(pct)}</b></span>`;
      });
    return parts.join('<span class="sub-quota-sep">·</span>');
  }

  function cardQuotaEl(subId) {
    const card = $$('#subscriptionList .subscription-card')
      .find((el) => el.dataset.subscriptionId === subId);
    return card ? $('[data-quota-summary]', card) : null;
  }

  function renderCardQuota(sub, data) {
    const el = cardQuotaEl(sub.id);
    if (!el) return;
    const html = quotaSummaryHtml(data);
    el.innerHTML = html;
    el.hidden = !html;
    if (html) el.title = t('settings.opencodeGoQuota');
  }

  async function loadCardQuota(sub, force = false) {
    if (!(isGoChannel(sub) || sub.provider === 'opencode-go')) return;
    if (!(sub.has_key || sub.has_api_key)) return;
    const profiles = sub.models || [];
    const profile = profiles.find((m) => m.api_key || m.has_api_key) || profiles[0];
    if (!profile) return;
    const cached = state.quotaCache[sub.id];
    if (!force && cached && Date.now() - cached.at < QUOTA_CACHE_TTL) {
      renderCardQuota(sub, cached.data);
      return;
    }
    if (state.quotaPending.has(sub.id)) return;
    state.quotaPending.add(sub.id);
    try {
      const data = await api(`/app/models/usage?code=${encodeURIComponent(profile.code)}`);
      state.quotaCache[sub.id] = { at: Date.now(), data: data || {} };
      renderCardQuota(sub, data || {});
    } catch (_) {
      // 没有额度（非 Go 渠道 / 未开通 / 临时失败）时第三行保持隐藏。
    } finally {
      state.quotaPending.delete(sub.id);
    }
  }

  function quotaMoney(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
    return `$${Number(value).toFixed(2)}`;
  }

  function quotaReset(seconds) {
    let s = Math.max(0, Math.floor(Number(seconds) || 0));
    if (!s) return '';
    const d = Math.floor(s / 86400); s %= 86400;
    const h = Math.floor(s / 3600); s %= 3600;
    const m = Math.floor(s / 60);
    const parts = [];
    if (d) parts.push(`${d}d`);
    if (h) parts.push(`${h}h`);
    if (!d && m) parts.push(`${m}m`);
    return parts.join(' ') || '<1m';
  }

  function quotaHeader(channel) {
    return `<div class="quota-head-row">
      <span class="quota-title">${escapeHtml(t('settings.opencodeGoQuota'))}
        <span class="quota-profile">${escapeHtml(channel.name || 'OpenCode Go')}</span>
      </span>
      <button type="button" class="btn small ghost" data-channel-quota-refresh>${escapeHtml(t('settings.quotaRefresh'))}</button>
    </div>`;
  }

  function quotaBox(target) {
    if (!target) return null;
    if (target.matches && target.matches('[data-channel-quota]')) return target;
    return $('[data-channel-quota]', target);
  }

  function renderChannelQuota(target, channel, data) {
    const box = quotaBox(target);
    if (!box) return;
    const windows = (data && data.windows) || {};
    const defs = [
      ['five_hour', 'settings.quotaFiveHour'],
      ['weekly', 'settings.quotaWeekly'],
      ['monthly', 'settings.quotaMonthly'],
    ];
    const rows = defs.map(([key, label]) => {
      const w = windows[key];
      if (!w) return '';
      const pct = Math.max(0, Math.min(100, Number(w.percent) || 0));
      const cls = pct >= 95 ? 'bad' : pct >= 80 ? 'warn' : '';
      const reset = quotaReset(w.reset_in_sec);
      const hasMoney = w.used !== null && w.used !== undefined
        && w.limit !== null && w.limit !== undefined;
      const right = hasMoney
        ? `${t('settings.quotaUsed', { used: quotaMoney(w.used), limit: quotaMoney(w.limit) })} · ${pct.toFixed(1)}%`
        : `${pct.toFixed(1)}%`;
      const status = w.status && w.status !== 'ok' ? ` · ${w.status}` : '';
      return `<div class="quota-row ${cls}">
        <div class="quota-head">
          <span>${escapeHtml(t(label))}</span>
          <span class="mono">${escapeHtml(right + status)}</span>
        </div>
        <div class="quota-bar"><span style="width:${pct}%"></span></div>
        ${reset ? `<div class="quota-reset">${escapeHtml(t('settings.quotaReset', { time: reset }))}</div>` : ''}
      </div>`;
    }).join('');
    box.hidden = false;
    box.innerHTML = quotaHeader(channel)
      + (rows || `<div class="model-discover-note">${escapeHtml(t('settings.quotaUnavailable', { err: 'empty' }))}</div>`);
  }

  async function loadChannelQuota(target, channel, force = false) {
    const box = quotaBox(target);
    if (!box) return;
    if (!isGoChannel(channel) || !(channel.has_key || channel.has_api_key)) {
      box.hidden = true;
      return;
    }
    const profile = channel.models.find((m) => m.api_key || m.has_api_key) || channel.models[0];
    if (!profile) { box.hidden = true; return; }
    if (force) box._quotaRetried = false;
    box.hidden = false;
    box.innerHTML = quotaHeader(channel)
      + `<div class="model-discover-note"><span class="spinner"></span> ${escapeHtml(t('settings.loadingModels'))}</div>`;
    try {
      const url = `/app/models/usage?code=${encodeURIComponent(profile.code)}`
        + (force ? '&refresh=1' : '');
      const data = await api(url);
      box._quotaRetried = false;
      const payload = data || {};
      state.quotaCache[channel.id] = { at: Date.now(), data: payload };
      renderCardQuota(channel, payload);
      renderChannelQuota(box, channel, payload);
    } catch (err) {
      const msg = (err && err.message) || String(err);
      box.innerHTML = quotaHeader(channel)
        + `<div class="model-discover-note bad">${escapeHtml(t('settings.quotaUnavailable', {
          err: msg,
        }))}</div>`;
      const permanent = /401|403|无效|未开通|不是 OpenCode Go|缺少/.test(msg);
      if (!permanent && !box._quotaRetried && box.isConnected) {
        box._quotaRetried = true;
        setTimeout(() => {
          if (box.isConnected) loadChannelQuota(box, channel, false);
        }, 8000);
      }
    }
  }

  function discoverBox() {
    return $('#discoverList');
  }

  function renderChannelDiscovered(channel, data) {
    const box = discoverBox();
    if (!box) return;
    state.discovered = state.discovered || {};
    state.discovered[channel.id] = data || {};
    const models = (data && data.models) || [];
    box.hidden = false;
    if (!models.length) {
      box.innerHTML = `<div class="model-discover-note">${escapeHtml(t('settings.discoverEmpty'))}</div>`;
      return;
    }
    const added = new Set((channel.models || [])
      .map((m) => String(m.model || ''))
      .filter(Boolean));
    const query = state.discoverFilter[channel.id] || '';
    const rows = models.map((m) => {
      const supported = m.supported !== false;
      const note = m.note || (supported ? '' : t('settings.quotaUnsupported'));
      const filterText = `${m.id || ''} ${m.owned_by || ''} ${m.protocol || ''}`.toLowerCase();
      const isAdded = added.has(String(m.id || ''));
      let action = '';
      if (!supported) {
        action = `<span class="model-discover-note-inline" title="${escAttr(note)}">${escapeHtml(note)}</span>`;
      } else if (isAdded) {
        action = `<span class="model-discover-note-inline">${escapeHtml(t('settings.discoverAdded'))}</span>`;
      } else {
        action = `<button type="button" class="btn small ghost" data-discover-add="${escAttr(m.id)}">${escapeHtml(t('settings.addAsProfile'))}</button>`;
      }
      return `<div class="model-discover-row${supported ? '' : ' unsupported'}" data-discover-filter="${escAttr(filterText)}">
        <span class="mono model-discover-id" title="${escAttr(m.id)}">${escapeHtml(m.id)}</span>
        ${m.protocol ? `<span class="model-discover-owner">${escapeHtml(m.protocol)}</span>` : ''}
        ${action}
      </div>`;
    }).join('');
    box.innerHTML = `<div class="model-discover-head">
      <span data-discover-count>${escapeHtml(t('settings.discoverCount', { n: models.length }))}</span>
      <button type="button" class="btn small ghost" data-discover-add-all>${escapeHtml(t('settings.discoverAddAll'))}</button>
    </div>
    <label class="model-discover-search">
      <svg><use href="#i-search"/></svg>
      <input type="search" data-discover-search value="${escAttr(query)}" placeholder="${escAttr(t('settings.filterModels'))}" autocomplete="off" spellcheck="false" />
    </label>
    <div class="model-discover-rows">${rows}</div>
    <div class="model-discover-note" data-discover-no-match hidden>${escapeHtml(t('settings.noModelMatch'))}</div>`;
    applyDiscoverFilter();
  }

  function applyDiscoverFilter() {
    const box = discoverBox();
    if (!box) return;
    const input = $('[data-discover-search]', box);
    const q = modelMenuFilterKey(input ? input.value : '');
    const rows = $$('[data-discover-filter]', box);
    let visible = 0;
    rows.forEach((row) => {
      const on = !q || (row.dataset.discoverFilter || '').includes(q);
      row.hidden = !on;
      if (on) visible += 1;
    });
    const empty = $('[data-discover-no-match]', box);
    if (empty) empty.hidden = visible > 0;
    const count = $('[data-discover-count]', box);
    if (count) count.textContent = t('settings.discoverCount', { n: visible });
  }

  async function discoverChannelModels(btn) {
    const channel = subscriptionById(state.selectedSubscription);
    if (!channel) {
      toast(t('settings.noSubscriptionSelected'), 'warn', 3000);
      return;
    }
    if (!channel.base_url) {
      toast(t('settings.baseUrlRequired'), 'warn', 3000);
      return;
    }
    const box = discoverBox();
    const modal = $('#discoverModal');
    const subtitle = $('#discoverModalSubtitle');
    if (subtitle) {
      subtitle.textContent = [
        channel.name || subscriptionProviderLabel(channel.provider),
        subscriptionHost(channel.base_url),
      ].filter(Boolean).join(' · ');
    }
    state.discoverFilter[channel.id] = '';
    if (box) {
      box.hidden = false;
      box.innerHTML = `<div class="model-discover-note"><span class="spinner"></span> ${escapeHtml(t('settings.discovering'))}</div>`;
    }
    if (modal) modal.hidden = false;
    if (btn) btn.disabled = true;
    try {
      const data = await api('/app/models/discover', {
        method: 'POST',
        body: JSON.stringify({
          code: (channel.models[0] || {}).code || '',
          base_url: channel.base_url,
          api_key: subscriptionKey(channel),
        }),
      });
      renderChannelDiscovered(channel, data);
    } catch (err) {
      if (box) {
        box.innerHTML = `<div class="model-discover-note bad">${escapeHtml(
          t('settings.discoverError', { err: (err && err.message) || String(err) }))}</div>`;
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function discoveredMeta(channelId, modelId) {
    const data = (state.discovered || {})[channelId] || {};
    return (data.models || []).find((m) => m.id === modelId) || null;
  }

  function refreshDiscoverList(channelId) {
    const data = (state.discovered || {})[channelId];
    if (!data) return;
    const channel = subscriptionById(channelId);
    if (channel) renderChannelDiscovered(channel, data);
  }

  function addDiscoveredModel(modelId) {
    syncStateFromCards();
    const channel = subscriptionById(state.selectedSubscription);
    if (!channel) return false;
    const meta = discoveredMeta(channel.id, modelId);
    if (meta && meta.supported === false) {
      toast(meta.note || t('settings.quotaUnsupported'), 'warn', 3200);
      return false;
    }
    const existing = state.models.some((m) => String(m.model || '') === modelId
      && channelIdForProfile(m) === channel.id);
    if (existing) return false;
    const empty = state.models.find((m) => channelIdForProfile(m) === channel.id
      && !String(m.model || '').trim());
    if (empty) {
      empty.model = modelId;
      if (!empty.name) empty.name = modelId;
      if (meta && meta.protocol) empty.protocol = meta.protocol;
      state.openModelCards.add(empty.code);
    } else {
      createModelForSubscription(channel, {
        name: modelId,
        model: modelId,
        protocol: (meta && meta.protocol) || 'openai-chat',
      });
    }
    state.selectedSubscription = channel.id;
    renderModelCards();
    renderSubscriptionCards();
    fillModelOptions();
    refreshDiscoverList(channel.id);
    return true;
  }

  function addAllDiscoveredModels() {
    syncStateFromCards();
    const channel = subscriptionById(state.selectedSubscription);
    if (!channel) return;
    const data = (state.discovered || {})[channel.id] || {};
    const models = data.models || [];
    let added = 0;
    let skipped = 0;
    for (const m of models) {
      if (m.supported === false) { skipped += 1; continue; }
      const existing = state.models.some((profile) => String(profile.model || '') === m.id
        && channelIdForProfile(profile) === channel.id);
      if (existing) continue;
      const empty = state.models.find((profile) => channelIdForProfile(profile) === channel.id
        && !String(profile.model || '').trim());
      if (empty) {
        empty.model = m.id;
        if (!empty.name) empty.name = m.id;
        if (m.protocol) empty.protocol = m.protocol;
      } else {
        createModelForSubscription(channel, {
          name: m.id,
          model: m.id,
          protocol: m.protocol || 'openai-chat',
        });
      }
      added += 1;
    }
    state.selectedSubscription = channel.id;
    renderModelCards();
    renderSubscriptionCards();
    fillModelOptions();
    refreshDiscoverList(channel.id);
    if (!added && !skipped) toast(t('settings.discoverSame'), 'warn', 2400);
    else if (skipped) toast(t('settings.discoverSkipped', { n: skipped }), 'warn', 3000);
  }

  async function loadModels() {
    const list = $('#modelList');
    const subList = $('#subscriptionList');
    if (list) {
      list.innerHTML = `<div class="model-loading"><span class="spinner"></span> ${escapeHtml(t('settings.loadingModels'))}</div>`;
    }
    if (subList) {
      subList.innerHTML = `<div class="model-loading"><span class="spinner"></span> ${escapeHtml(t('settings.loadingModels'))}</div>`;
    }
    try {
      const data = await api('/app/models');
      state.models = (data && data.items) || [];
      state.subscriptions = (data && data.subscriptions) || [];
      state.activeModel = (data && data.active) || '';
      state.defaultRemoved = !state.models.some((m) => m.code === 'default');
      try {
        const presets = await api('/app/models/presets');
        state.modelPresets = (presets && presets.items) || [];
      } catch (_) {
        state.modelPresets = [];
      }
      const allSubs = buildSubscriptions();
      if (!allSubs.some((sub) => sub.id === state.selectedSubscription)) {
        state.selectedSubscription = allSubs[0] ? allSubs[0].id : '';
      }
      renderModelCards();
      renderSubscriptionCards();
      fillModelOptions();
      syncAttachAvailability();
      syncThinkingLevels();
      return true;
    } catch (err) {
      state.models = [];
      state.subscriptions = [];
      state.modelPresets = [];
      state.selectedSubscription = '';
      if (list) list.innerHTML = `<div class="model-empty">${escapeHtml(t('settings.modelsFailed'))}</div>`;
      if (subList) subList.innerHTML = `<div class="model-empty">${escapeHtml(t('settings.modelsFailed'))}</div>`;
      return false;
    }
  }

  function modelCodeFrom(id) {
    const base = String(id || 'model').toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'model';
    const used = new Set(state.models.map((m) => m.code));
    let code = base;
    let n = 2;
    while (used.has(code)) code = `${base}-${n++}`;
    return code;
  }

  function makeProfile(seed = {}) {
    const def = state.models.find((m) => m.code === 'default') || {};
    const model = String(seed.model || '').trim();
    const code = String(seed.code || '').trim()
      || (model ? modelCodeFrom(model) : `model-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`);
    return {
      code,
      name: String(seed.name || model || '').trim(),
      model,
      base_url: String(seed.base_url || '').trim(),
      api_key: String(seed.api_key || ''),
      channel_id: String(seed.channel_id || seed.subscription_id || ''),
      channel_name: String(seed.channel_name || seed.subscription_name || ''),
      subscription_id: String(seed.subscription_id || seed.channel_id || ''),
      subscription_name: String(seed.subscription_name || seed.channel_name || ''),
      context_window: Number(seed.context_window) || 0,
      max_tokens: Number(seed.max_tokens) || 0,
      protocol: String(seed.protocol || 'openai-chat'),
      vision: !!seed.vision,
      thinking_levels: Array.isArray(seed.thinking_levels) ? seed.thinking_levels : [],
      effective_model: def.effective_model || '',
      effective_base_url: def.effective_base_url || '',
      effective_context_window: def.effective_context_window || 0,
      effective_max_tokens: def.effective_max_tokens || 0,
      effective_vision: false,
      effective_thinking_levels: [],
      effective_api_key: def.effective_api_key || '',
      configured: false,
      active: false,
      deletable: true,
    };
  }

  function createModelForSubscription(sub, seed = {}) {
    const item = makeProfile({
      ...seed,
      protocol: seed.protocol || sub.protocol || 'openai-chat',
      base_url: seed.base_url || sub.base_url || '',
      channel_id: sub.id,
      channel_name: sub.name,
      subscription_id: sub.id,
      subscription_name: sub.name,
    });
    state.models.push(item);
    state.openModelCards.add(item.code);
    return item;
  }

  function addModel(seed = null, subId = '') {
    if (seed instanceof Event) seed = null;
    syncStateFromCards();
    readSubscriptionCards();
    const seeds = (seed && typeof seed === 'object') ? seed : {};
    let sub = subscriptionById(subId || seeds.subscription_id || state.selectedSubscription || '');
    if (!sub) {
      const all = buildSubscriptions();
      sub = all[0] || null;
    }
    if (!sub) {
      // 没有任何订阅时先建一个本地订阅，保证模型一定有所属账号。
      addSubscription('custom');
      sub = buildSubscriptions()[0] || null;
    }
    if (!sub) return;
    state.selectedSubscription = sub.id;
    const item = createModelForSubscription(sub, seeds);
    renderModelCards(item.code);
    renderSubscriptionCards();
    fillModelOptions();
    const card = $$('#modelList .model-card').find((c) => c.dataset.modelCode === item.code);
    const input = card && $('[data-model-field="model"]', card);
    if (input) setTimeout(() => input.focus(), 20);
  }

  function addModelToSubscription(subId) {
    syncStateFromCards();
    readSubscriptionCards();
    const sub = subscriptionById(subId);
    if (!sub) return;
    state.selectedSubscription = sub.id;
    createModelForSubscription(sub, {});
    renderModelCards();
    renderSubscriptionCards();
    fillModelOptions();
    const card = $$('#modelList .model-card').find((c) => {
      const input = $('[data-model-field="model"]', c);
      return input && !input.value;
    });
    const input = card && $('[data-model-field="model"]', card);
    if (input) setTimeout(() => input.focus(), 20);
  }

  function deleteModel(code) {
    const snapshot = readModelCards();
    if (code === 'default') state.defaultRemoved = true;
    state.models = snapshot.allModels.filter((m) => m.code !== code);
    state.openModelCards.delete(code);
    if (state.activeModel === code) {
      const fallback = state.models.find((m) => m.code !== 'default') || state.models[0];
      state.activeModel = fallback ? fallback.code : 'default';
    }
    fillModelOptions();
    renderModelCards();
    renderSubscriptionCards();
  }

  function numberOrZero(value) {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : 0;
  }

  // 结构性重绘（增删订阅 / 模型）前，把卡片上的未保存编辑收进 state，
  // 避免用户刚改完名字、点「添加模型」又被打回旧值。
  function syncStateFromCards() {
    if (!$('#modelList .model-group')) return;
    const snapshot = readModelCards();
    state.models = snapshot.allModels;
    state.activeModel = snapshot.active;
  }

  function readModelCards() {
    readSubscriptionCards();
    const subs = new Map(buildSubscriptions().map((sub) => [sub.id, sub]));
    const visibleSubIds = new Set();
    const visibleModels = [];
    let active = state.activeModel || 'default';
    $$('#modelList .model-group').forEach((section) => {
      const subId = section.dataset.subscriptionId;
      visibleSubIds.add(subId);
      const sub = subs.get(subId) || { id: subId, name: '', base_url: '' };
      $$('.model-card', section).forEach((card) => {
        const value = (field) => {
          const input = $(`[data-model-field="${field}"]`, card);
          return input ? input.value.trim() : '';
        };
        const code = card.dataset.modelCode;
        const visionInput = $('[data-model-field="vision"]', card);
        const thinkingLevels = $$('[data-thinking-level]', card)
          .filter((el) => el.checked)
          .map((el) => el.value);
        visibleModels.push({
          code,
          name: value('name'),
          model: value('model'),
          base_url: value('base_url'),
          context_window: numberOrZero(value('context_window')),
          max_tokens: numberOrZero(value('max_tokens')),
          protocol: value('protocol') || 'openai-chat',
          vision: !!(visionInput && visionInput.checked),
          thinking_levels: thinkingLevels,
          channel_id: sub.id,
          channel_name: sub.name,
          subscription_id: sub.id,
          subscription_name: sub.name,
        });
      });
    });
    // 右栏只渲染当前选中的订阅；未选中的模型继续留在 state 里，保存时不能丢。
    const keptModels = (state.models || [])
      .filter((profile) => !visibleSubIds.has(channelIdForProfile(profile)))
      .map((profile) => {
        const sub = subs.get(channelIdForProfile(profile));
        return sub ? {
          ...profile,
          channel_id: sub.id,
          channel_name: sub.name,
          subscription_id: sub.id,
          subscription_name: sub.name,
        } : profile;
      });
    const allModels = [...keptModels, ...visibleModels];
    const defaultModel = allModels.find((item) => item.code === 'default') || null;
    const models = allModels.filter((item) => item.code !== 'default');
    const checked = $('#modelList input[name="activeModel"]:checked');
    if (checked) active = checked.value;
    return { allModels, defaultModel, models, active };
  }

  async function saveSettings() {
    const btn = $('#saveSettingsBtn');
    const note = $('#settingsNote');
    const subscriptions = readSubscriptionCards();
    const { defaultModel, models, active } = readModelCards();
    const hadDefault = (state.models || []).some((m) => m.code === 'default');
    const removeDefault = !!state.defaultRemoved || (hadDefault && !defaultModel);
    const activeCode = (active && active !== 'default')
      ? active
      : ((models[0] || {}).code || 'default');
    const payload = {
      models,
      subscriptions,
      active_model: activeCode,
    };
    if (removeDefault) payload.remove_default = true;
    if (defaultModel) {
      if (defaultModel.base_url) payload.base_url = defaultModel.base_url;
      if (defaultModel.model) payload.model = defaultModel.model;
      if (defaultModel.api_key) payload.api_key = defaultModel.api_key;
      if (defaultModel.channel_id) payload.channel_id = defaultModel.channel_id;
      if (defaultModel.channel_name) payload.channel_name = defaultModel.channel_name;
      if (defaultModel.subscription_id) payload.subscription_id = defaultModel.subscription_id;
      if (defaultModel.context_window) payload.context_window = defaultModel.context_window;
      if (defaultModel.max_tokens) payload.max_tokens = defaultModel.max_tokens;
      payload.protocol = defaultModel.protocol || 'openai-chat';
      payload.vision = !!defaultModel.vision;
      payload.thinking_levels = defaultModel.thinking_levels || [];
    }

    btn.disabled = true;
    note.className = 'save-note';
    note.textContent = t('settings.saving');
    try {
      const view = await api('/app/settings', { method: 'PUT', body: JSON.stringify(payload) });
      state.app.llm = view;
      state.settings = view;
      state.models = view.models || [];
      state.subscriptions = view.subscriptions || [];
      state.defaultRemoved = !state.models.some((m) => m.code === 'default');
      state.activeModel = view.active_model || (state.models[0] || {}).code || '';
      if (state.conv) state.conv.model_code = state.activeModel;
      if (state.currentId) saveConversationPrefs({ model_code: state.activeModel });
      renderModelCards();
      renderSubscriptionCards();
      fillModelOptions();
      note.className = 'save-note ok';
      note.textContent = t('settings.saved');
      renderHeader();
      toast(t('settings.savedToast'), 'ok', 2600);
    } catch (err) {
      note.className = 'save-note bad';
      note.textContent = err.message;
      toast(t('settings.saveFailed', { msg: err.message }), 'bad', 6000);
    } finally {
      btn.disabled = false;
    }
  }

  /* ── Token usage stats ─────────────────────────────────────────── */

  function usageCardHtml(label, value, sub = '', exact = '') {
    // exact 是精确数值（value 已是 1.2万 这类紧凑写法），鼠标悬停看得到原始数字。
    const title = exact && exact !== value ? ` title="${escAttr(exact)}"` : '';
    return `<div class="usage-card">
      <span class="usage-card-label">${escapeHtml(label)}</span>
      <strong class="usage-card-value"${title}>${escapeHtml(value)}</strong>
      ${sub ? `<span class="usage-card-sub">${escapeHtml(sub)}</span>` : ''}
    </div>`;
  }

  // 图表标题按实际数据点数量显示，避免固定写死「最近 30 天」与实际周期不符。
  function chartTitle(daily) {
    const n = Array.isArray(daily) ? daily.length : 0;
    return n ? t('usage.chartRange', { n: String(n) }) : t('usage.chart');
  }

  function usageChartSvg(daily) {
    const days = Array.isArray(daily) ? daily : [];
    const W = 760, H = 172, padL = 10, padR = 10, padT = 16, padB = 22;
    const max = Math.max(1, ...days.map((d) => Number(d.total_tokens) || 0));
    const n = days.length || 1;
    const slot = (W - padL - padR) / n;
    const barW = Math.max(2, slot * 0.62);
    const base = H - padB;
    const chartH = H - padT - padB;
    const grid = [0.25, 0.5, 0.75, 1].map((p) => {
      const y = base - chartH * p;
      return `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" class="usage-grid-line"/>`;
    }).join('');
    const bars = days.map((d, i) => {
      const x = padL + i * slot + (slot - barW) / 2;
      const prompt = Number(d.prompt_tokens) || 0;
      const completion = Number(d.completion_tokens) || 0;
      const ph = chartH * (prompt / max);
      const ch = chartH * (completion / max);
      return `<g>
        <title>${escapeHtml(d.date)} · ${escapeHtml(t('usage.totalTokens'))}: ${num(d.total_tokens)} · ${num(prompt)} in / ${num(completion)} out</title>
        <rect x="${x.toFixed(2)}" y="${(base - ph).toFixed(2)}" width="${barW.toFixed(2)}" height="${ph.toFixed(2)}" class="usage-bar-in"/>
        <rect x="${x.toFixed(2)}" y="${(base - ph - ch).toFixed(2)}" width="${barW.toFixed(2)}" height="${ch.toFixed(2)}" class="usage-bar-out"/>
      </g>`;
    }).join('');
    const labels = days.map((d, i) => {
      if (i % 5 !== 0 && i !== days.length - 1) return '';
      const x = padL + i * slot + slot / 2;
      return `<text x="${x.toFixed(2)}" y="${H - 6}" text-anchor="middle" class="usage-axis-label">${escapeHtml(String(d.date).slice(5))}</text>`;
    }).join('');
    return `<svg viewBox="0 0 ${W} ${H}" class="usage-chart" role="img" aria-label="${escAttr(t('usage.chart'))}">${grid}${bars}${labels}</svg>`;
  }

  // 表格里的数字：紧凑显示 + 悬停看精确值（避免 8 列表格被撑宽）
  function usageNum(value, unit = '') {
    const exact = num(value);
    return `<td class="usage-num" title="${escAttr(exact)}${unit ? ' ' + escAttr(unit) : ''}">${escapeHtml(compact(value))}</td>`;
  }

  function renderUsageStats() {
    const body = $('#usageStatsBody');
    if (!body) return;
    const periodKey = state.usagePeriod || '7d';
    $$('#usagePeriodSeg [data-usage-period]').forEach((b) =>
      b.classList.toggle('active', b.dataset.usagePeriod === periodKey));
    const data = state.usageStats;
    if (!data) {
      body.innerHTML = `<div class="model-empty">${escapeHtml(t('usage.empty'))}</div>`;
      return;
    }
    const key = state.usagePeriod || '7d';
    const period = (data.periods || {})[key] || (data.periods || {}).all || {};
    const totals = period.totals || {};
    if (!totals.messages) {
      body.innerHTML = `<div class="model-empty">${escapeHtml(t('usage.empty'))}</div>`;
      return;
    }
    const channels = data.channels || {};
    const fmtPct = (v) => `${(Number(v) || 0).toFixed(1)}%`;
    // 卡片：token 类数字用 compact（1.2万 / 3.4亿），比率和计数保持原样。
    const cards = [
      [t('usage.totalTokens'), compact(totals.total_tokens), `${compact(totals.prompt_tokens)} in · ${compact(totals.completion_tokens)} out`, num(totals.total_tokens)],
      [t('usage.input'), compact(totals.prompt_tokens), t('usage.cachedSub', { n: compact(totals.cached_tokens) }), num(totals.prompt_tokens)],
      [t('usage.output'), compact(totals.completion_tokens), t('usage.repliesSub', { n: num(totals.messages) }), num(totals.completion_tokens)],
      [t('usage.cacheRate'), fmtPct(totals.cache_rate), t('usage.cacheSub', { cached: compact(totals.cached_tokens), prompt: compact(totals.prompt_tokens) }), ''],
      [t('usage.replies'), num(totals.messages), `${num(totals.rounds)} ${t('usage.llmRoundsShort')}`, ''],
      [t('usage.avgPerReply'), compact(totals.avg_tokens_per_message), t('usage.tokensUnit'), num(Math.round(totals.avg_tokens_per_message || 0))],
      [t('usage.avgPerDay'), compact(totals.avg_tokens_per_day), `${num(totals.active_days)} ${t('usage.activeDaysShort')}`, num(Math.round(totals.avg_tokens_per_day || 0))],
      [t('usage.speedLabel'), `${num(totals.tokens_per_second)} tok/s`, `${duration(totals.llm_ms)} ${t('usage.llmTimeShort')}`, ''],
    ].map(([label, value, sub, exact]) => usageCardHtml(label, value, sub, exact)).join('');

    const top = period.top_model;
    const peak = period.peak_day;
    const highlights = `<div class="usage-highlights">
      <div><span>${escapeHtml(t('usage.topModel'))}</span><strong title="${escAttr(top ? top.name : '')}">${escapeHtml(top ? top.name : '—')}</strong><small>${top ? `${compact(top.total_tokens)} · ${fmtPct(top.share)}` : ''}</small></div>
      <div><span>${escapeHtml(t('usage.peakDay'))}</span><strong>${escapeHtml(peak ? peak.date : '—')}</strong><small>${peak ? `${compact(peak.total_tokens)} ${t('usage.tokensUnit')}` : ''}</small></div>
      <div><span>${escapeHtml(t('usage.cacheSaved'))}</span><strong title="${escAttr(num(totals.cached_tokens))}">${escapeHtml(compact(totals.cached_tokens))}</strong><small>${escapeHtml(t('usage.cacheSavedSub'))}</small></div>
    </div>`;

    const modelRows = (period.models || []).map((m) => `<tr>
      <td class="usage-model-name" title="${escAttr(m.name)}">
        <span class="usage-name">${escapeHtml(m.name)}</span>
        <span class="usage-sub">${escapeHtml(channels[m.name] || '—')}</span>
      </td>
      ${usageNum(m.total_tokens)}
      ${usageNum(m.prompt_tokens)}
      ${usageNum(m.completion_tokens)}
      <td class="usage-num">${fmtPct(m.cache_rate)}</td>
      <td class="usage-num">${num(m.messages)}</td>
      <td class="usage-share-cell"><span class="usage-share-bar"><span style="width:${Math.min(100, Number(m.share) || 0)}%"></span></span><span>${fmtPct(m.share)}</span></td>
    </tr>`).join('');
    const channelRows = (period.channels || []).map((c) => `<tr>
      <td class="usage-model-name"><span class="usage-name">${escapeHtml(c.name)}</span></td>
      ${usageNum(c.total_tokens)}
      ${usageNum(c.prompt_tokens)}
      ${usageNum(c.completion_tokens)}
      <td class="usage-num">${num(c.messages)}</td>
      <td class="usage-num">${fmtPct(c.share)}</td>
    </tr>`).join('');
    const recentRows = (period.recent || []).map((r) => `<tr>
      <td class="mono usage-ts">${escapeHtml(r.ts)}</td>
      <td class="usage-model-name" title="${escAttr(`${r.model} · ${r.channel || '—'}`)}">
        <span class="usage-name">${escapeHtml(r.model)}</span>
        <span class="usage-sub">${escapeHtml(r.channel || '—')}</span>
      </td>
      ${usageNum(r.prompt_tokens)}
      ${usageNum(r.completion_tokens)}
      <td class="usage-num">${num(r.rounds)}</td>
      <td class="usage-num">${duration(r.duration_ms)}</td>
    </tr>`).join('');

    body.innerHTML = `
      <div class="usage-summary-grid">${cards}</div>
      ${highlights}
      <div class="usage-panel">
        <div class="usage-panel-head"><span>${escapeHtml(chartTitle(data.daily))}</span><span>${escapeHtml(t('usage.chartLegend'))}</span></div>
        ${usageChartSvg(data.daily || [])}
      </div>
      <div class="usage-panel">
        <div class="usage-panel-head"><span>${escapeHtml(t('usage.byModel'))}</span></div>
        <div class="usage-table-wrap"><table class="usage-table usage-table-model">
          <colgroup><col class="col-name"/><col/><col/><col/><col/><col/><col class="col-share"/></colgroup>
          <thead><tr><th>${escapeHtml(t('usage.model'))}</th><th class="usage-num">${escapeHtml(t('usage.total'))}</th><th class="usage-num">${escapeHtml(t('usage.input'))}</th><th class="usage-num">${escapeHtml(t('usage.output'))}</th><th class="usage-num">${escapeHtml(t('usage.cacheRate'))}</th><th class="usage-num">${escapeHtml(t('usage.replies'))}</th><th>${escapeHtml(t('usage.share'))}</th></tr></thead>
          <tbody>${modelRows || `<tr><td colspan="7" class="usage-empty">${escapeHtml(t('usage.empty'))}</td></tr>`}</tbody>
        </table></div>
      </div>
      <div class="usage-panel">
        <div class="usage-panel-head"><span>${escapeHtml(t('usage.byChannel'))}</span></div>
        <div class="usage-table-wrap"><table class="usage-table usage-table-channel">
          <colgroup><col class="col-name"/><col/><col/><col/><col/><col/></colgroup>
          <thead><tr><th>${escapeHtml(t('usage.channel'))}</th><th class="usage-num">${escapeHtml(t('usage.total'))}</th><th class="usage-num">${escapeHtml(t('usage.input'))}</th><th class="usage-num">${escapeHtml(t('usage.output'))}</th><th class="usage-num">${escapeHtml(t('usage.replies'))}</th><th class="usage-num">${escapeHtml(t('usage.share'))}</th></tr></thead>
          <tbody>${channelRows || `<tr><td colspan="6" class="usage-empty">${escapeHtml(t('usage.empty'))}</td></tr>`}</tbody>
        </table></div>
      </div>
      <div class="usage-panel">
        <div class="usage-panel-head"><span>${escapeHtml(t('usage.recent'))}</span></div>
        <div class="usage-table-wrap"><table class="usage-table usage-table-recent">
          <colgroup><col class="col-ts"/><col class="col-name"/><col/><col/><col/><col/></colgroup>
          <thead><tr><th>${escapeHtml(t('usage.colTime'))}</th><th>${escapeHtml(t('usage.model'))}</th><th class="usage-num">${escapeHtml(t('usage.input'))}</th><th class="usage-num">${escapeHtml(t('usage.output'))}</th><th class="usage-num">${escapeHtml(t('usage.rounds'))}</th><th class="usage-num">${escapeHtml(t('usage.duration'))}</th></tr></thead>
          <tbody>${recentRows || `<tr><td colspan="6" class="usage-empty">${escapeHtml(t('usage.empty'))}</td></tr>`}</tbody>
        </table></div>
      </div>`;
  }

  async function loadUsageStats(force = false) {
    const body = $('#usageStatsBody');
    if (!body) return;
    if (state.usageLoading) return;
    if (state.usageStats && !force) {
      renderUsageStats();
      return;
    }
    state.usageLoading = true;
    if (!state.usageStats) {
      body.innerHTML = `<div class="model-loading"><span class="spinner"></span> ${escapeHtml(t('usage.loading'))}</div>`;
    }
    try {
      state.usageStats = await api('/app/usage/stats');
      renderUsageStats();
    } catch (err) {
      body.innerHTML = `<div class="model-empty">${escapeHtml(t('usage.loadFailed', { err: (err && err.message) || String(err) }))}</div>`;
    } finally {
      state.usageLoading = false;
    }
  }

  /* ── Dock: 内嵌式面板（收缩 / 拖拉 / 排序） ──────────────────────────── */

  function panelEl(name) { return document.getElementById('panel-' + name); }

  // dock 最大宽度从 640 提到 1280（+100%），但给聊天区保留最小可用宽度。
  const DOCK_MAX_W = 1280;
  const DOCK_MIN_CHAT_W = 360;

  function dockMaxWidth() {
    const sidebar = state.ui.sidebarCollapsed ? 58 : (state.ui.sidebarW || 276);
    return Math.max(320, Math.min(DOCK_MAX_W, window.innerWidth - sidebar - DOCK_MIN_CHAT_W));
  }

  function applyDockWidth() {
    const app = $('#app');
    if (!app) return;
    const w = Math.min(state.ui.dockW || 340, dockMaxWidth());
    app.style.setProperty('--dock-w', `${Math.round(w)}px`);
  }

  function isPanelOpen(name) {
    return !state.ui.dockCollapsed && state.ui.active === name;
  }

  const PANEL_ICONS = { workspace: 'folder', viewer: 'file', vcs: 'vcs', mem: 'brain', codegraph: 'code', todo: 'list' };

  function dockOpenTabs() {
    if (!Array.isArray(state.ui.openTabs)) state.ui.openTabs = [];
    state.ui.openTabs = [...new Set(state.ui.openTabs.filter((name) => DOCK_PANELS.includes(name)))];
    return state.ui.openTabs;
  }

  function renderDockTabs() {
    const tabs = $('#dockTabs');
    const strip = $('#dockTabStrip');
    if (!tabs || !strip) return;
    const open = dockOpenTabs();
    strip.innerHTML = open.map((name) => {
      const active = state.ui.active === name;
      const label = t('dock.tab' + name.charAt(0).toUpperCase() + name.slice(1)) || name;
      return `<div class="dock-tab${active ? ' is-active' : ''}" draggable="true" data-dock-tab="${name}">
        <button type="button" class="dock-tab-main" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="panel-${name}" tabindex="0" title="${escAttr(label)}">
          <svg><use href="#i-${PANEL_ICONS[name]}"/></svg><span>${escAttr(label)}</span><span class="tab-badge" id="tabBadge-${name}" hidden></span>
        </button>
        <button type="button" class="dock-tab-close" data-dock-close="${name}" title="${escAttr(t('dock.closeTab'))}" aria-label="${escAttr(t('dock.closeTab'))}"><svg><use href="#i-x"/></svg></button>
      </div>`;
    }).join('');
    const activeEl = $('.dock-tab.is-active', strip);
    if (activeEl) {
      const r = activeEl.getBoundingClientRect();
      const sr = strip.getBoundingClientRect();
      if (r.left < sr.left) strip.scrollLeft -= (sr.left - r.left) + 4;
      else if (r.right > sr.right) strip.scrollLeft += (r.right - sr.right) + 4;
    }
    if (tabs.dataset.bound) return;
    tabs.dataset.bound = '1';
    tabs.addEventListener('click', (e) => {
      const close = e.target.closest('[data-dock-close]');
      if (close) {
        closeDockTab(close.dataset.dockClose);
        return;
      }
      if (e.target.closest('[data-dock-collapse]')) {
        state.ui.dockCollapsed = true;
        saveUi();
        syncDock();
        return;
      }
      const tab = e.target.closest('.dock-tab');
      if (tab) activateDockTab(tab.dataset.dockTab);
    });
    tabs.addEventListener('contextmenu', (e) => {
      const tab = e.target.closest('.dock-tab');
      if (!tab) return;
      e.preventDefault();
      const name = tab.dataset.dockTab;
      if (!name) return;
      openUiMenu(e.clientX, e.clientY, [
        { icon: 'x', label: t('dock.closeOthers'), action: () => closeOtherDockTabs(name) },
        { icon: 'x', label: t('dock.closeAll'), action: closeAllDockTabs },
      ]);
    });
    let dragging = null;
    tabs.addEventListener('dragstart', (e) => {
      if (e.target.closest('.dock-tab-close')) {
        e.preventDefault();
        return;
      }
      const tab = e.target.closest('.dock-tab');
      if (!tab) return;
      dragging = tab;
      tab.classList.add('is-dragging');
    });
    tabs.addEventListener('dragover', (e) => {
      if (!dragging) return;
      const target = e.target.closest('.dock-tab');
      if (!target || target === dragging) return;
      e.preventDefault();
      const all = $$('.dock-tab', strip);
      const from = all.indexOf(dragging);
      const to = all.indexOf(target);
      if (from < to) strip.insertBefore(dragging, target.nextSibling);
      else strip.insertBefore(dragging, target);
    });
    tabs.addEventListener('dragend', () => {
      if (!dragging) return;
      dragging.classList.remove('is-dragging');
      dragging = null;
      state.ui.openTabs = $$('.dock-tab', strip).map((tab) => tab.dataset.dockTab);
      saveUi();
    });
  }

  function activateDockTab(name) {
    const open = dockOpenTabs();
    if (!open.includes(name)) return;
    state.ui.active = name;
    state.ui.dockCollapsed = false;
    saveUi();
    syncDock();
  }

  function closeDockTab(name) {
    const open = dockOpenTabs();
    const idx = open.indexOf(name);
    if (idx < 0) {
      if (state.ui.active === name) {
        state.ui.active = null;
        state.ui.dockCollapsed = true;
        saveUi();
        syncDock();
      }
      return;
    }
    open.splice(idx, 1);
    if (state.ui.active === name) {
      state.ui.active = open[Math.min(idx, open.length - 1)] || null;
      if (!open.length) state.ui.dockCollapsed = true;
    }
    saveUi();
    syncAppearanceControls();
    syncDock();
  }

  function closeOtherDockTabs(keep) {
    const open = dockOpenTabs();
    if (!open.includes(keep)) return;
    state.ui.openTabs = [keep];
    state.ui.active = keep;
    state.ui.dockCollapsed = false;
    saveUi();
    syncAppearanceControls();
    syncDock();
  }

  function closeAllDockTabs() {
    state.ui.openTabs = [];
    state.ui.active = null;
    state.ui.dockCollapsed = true;
    saveUi();
    syncAppearanceControls();
    syncDock();
  }

  function syncDock() {
    const dock = $('#dock');
    const app = $('#app');
    if (!dock || !app) return;
    const open = dockOpenTabs();
    if (!open.length) {
      state.ui.active = null;
      dock.hidden = true;
      dock.setAttribute('aria-hidden', 'true');
      app.dataset.dock = 'closed';
      return;
    }
    if (!state.ui.active || !open.includes(state.ui.active)) {
      state.ui.active = open[open.length - 1];
    }
    const active = state.ui.active;
    const rail = $('#dockRail');
    const tabs = $('#dockTabs');
    const panels = $('#dockPanels');
    dock.hidden = false;
    dock.setAttribute('aria-hidden', 'false');
    if (state.ui.dockCollapsed) {
      app.dataset.dock = 'rail';
      if (rail) rail.hidden = false;
      if (tabs) tabs.hidden = true;
      if (panels) panels.hidden = true;
      const railActive = $('#dockRailActive');
      if (railActive) railActive.innerHTML = `<svg><use href="#i-${PANEL_ICONS[active]}"/></svg>`;
      DOCK_PANELS.forEach((name) => {
        const el = panelEl(name);
        if (el) el.hidden = true;
      });
      return;
    }
    app.dataset.dock = 'open';
    if (rail) rail.hidden = true;
    if (tabs) tabs.hidden = false;
    if (panels) panels.hidden = false;
    DOCK_PANELS.forEach((name) => {
      const el = panelEl(name);
      if (el) el.hidden = name !== active;
    });
    renderDockTabs();
    updateRailBadges();
  }

  function openPanel(name) {
    const el = panelEl(name);
    if (!el || !DOCK_PANELS.includes(name)) return false;
    const open = dockOpenTabs();
    if (!open.includes(name)) open.push(name);
    state.ui.dockCollapsed = false;
    state.ui.active = name;
    saveUi();
    syncAppearanceControls();
    syncDock();
    return true;
  }

  function closePanel(name) {
    closeDockTab(name);
  }

  function updateRailBadges() {
    // 任务清单已移出 dock（顶栏 #topTodoCount 自己带徽标），这里只管 dock 内面板
    const counts = {
      vcs: state.vcs && state.vcs.summary ? (state.vcs.summary.changed || 0) : 0,
    };
    ['vcs'].forEach((name) => {
      const n = counts[name];
      const tabBadge = $('#tabBadge-' + name);
      if (tabBadge) {
        tabBadge.hidden = !n;
        tabBadge.textContent = String(n);
      }
    });
  }

  let uiSyncTimer = null;

  function uiPrefs() {
    const { wsOpen, ...rest } = state.ui;   // 工作区展开状态仅本次运行有效，不落盘
    return { ...rest, theme: state.theme, themeSchedule: state.themeSchedule, lang: state.langMode };
  }

  function saveUi() {
    localStorage.setItem('az-ui', JSON.stringify(uiPrefs()));
    if (uiSyncTimer) clearTimeout(uiSyncTimer);
    uiSyncTimer = setTimeout(() => {
      api('/app/ui', { method: 'PUT', body: JSON.stringify({ prefs: uiPrefs() }) }).catch(() => {});
    }, 800);
  }

  async function loadUiPrefs() {
    try {
      const prefs = await api('/app/ui');
      if (!prefs || typeof prefs !== 'object') return;
      state.ui = { ...state.ui, ...prefs };
      delete state.ui.outlineOpen;
      delete state.ui.tabs;
      delete state.ui.order;
      if (!Array.isArray(state.ui.openTabs)) state.ui.openTabs = [];
      state.ui.openTabs = [...new Set(state.ui.openTabs.filter((name) => DOCK_PANELS.includes(name)))];
      if (!state.ui.sections || typeof state.ui.sections !== 'object') state.ui.sections = {};
      // 工作区项目每次进入都从全部收起开始，忽略上次的展开状态。
      state.ui.wsOpen = {};
      state.ui.sideTab = sideTabName(state.ui.sideTab);
      if (prefs.dockCollapsed === undefined) state.ui.dockCollapsed = true;
      else state.ui.dockCollapsed = !!prefs.dockCollapsed;
      if (prefs.theme) state.theme = prefs.theme;
      if (prefs.themeSchedule) state.themeSchedule = { ...state.themeSchedule, ...prefs.themeSchedule };
      if (['system', 'zh', 'en'].includes(prefs.lang)) state.langMode = prefs.lang;
    } catch (err) {
      /* 后端不可达时，localStorage 里的副本已经够用 */
    }
  }

  function bindDockResizer() {
    const resizer = $('#dockResizer');
    const app = $('#app');
    if (!resizer || !app) return;
    resizer.addEventListener('pointerdown', (e) => {
      if (app.dataset.dock !== 'open') return;
      e.preventDefault();
      resizer.classList.add('dragging');
      const startX = e.clientX;
      const startW = Math.min(state.ui.dockW || 340, dockMaxWidth());
      const onMove = (ev) => {
        const delta = startX - ev.clientX;
        const clamped = Math.min(dockMaxWidth(), Math.max(220, startW + delta));
        state.ui.dockW = clamped;
        app.style.setProperty('--dock-w', Math.round(clamped) + 'px');
      };
      const onUp = () => {
        resizer.classList.remove('dragging');
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        saveUi();
        syncAppearanceControls();
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
  }

  // 侧栏 chat / workspace 两个 pane 用 tab 切换（互斥显示），
  // 上次的选择存在 state.ui.sideTab 里（随 UI 偏好同步到服务端），作为下次进入的默认。
  const SIDE_TABS = ['chat', 'workspace'];

  function sideTabName(name) {
    return SIDE_TABS.includes(name) ? name : 'chat';
  }

  function renderSideTabs(name) {
    const active = sideTabName(name);
    $$('#sideTabs [data-side-tab]').forEach((btn) => {
      const on = btn.dataset.sideTab === active;
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
      btn.classList.toggle('is-active', on);
    });
    $$('[data-side-pane]').forEach((pane) => {
      pane.hidden = pane.dataset.sidePane !== active;
    });
  }

  function applySideTab(name, { persist = false } = {}) {
    const next = sideTabName(name);
    state.ui.sideTab = next;
    renderSideTabs(next);
    // 只切换左侧 pane。右侧 dock 是独立面板，切 tab 不动它 ——
    // 收起只由用户点标签的关闭 / 收缩按钮触发（2.0.8.2 起无自动收缩）。
    if (persist) saveUi();
  }

  // 注意：整栏折叠只由用户点 #sidebarToggle / 设置里的开关触发；
  // 「工作区项目」分组的收起也只由用户点分组上的 chevron 触发，没有任何自动收缩。
  function applyUi() {
    const app = $('#app');
    if (!app) return;
    app.dataset.sidebarCollapsed = state.ui.sidebarCollapsed ? '1' : '0';
    applyDockWidth();
    if (state.ui.sidebarCollapsed) {
      app.style.removeProperty('--sidebar-w');
    } else {
      app.style.setProperty('--sidebar-w', (state.ui.sidebarW || 276) + 'px');
    }
    const runs = $('#activeRuns');
    if (runs && state.ui.sections.runs) runs.classList.add('is-collapsed');
    const syncSectionBtn = (sel, collapsed) => {
      const btn = $(sel);
      if (!btn) return;
      btn.classList.toggle('is-collapsed', !!collapsed);
      btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    };
    syncSectionBtn('#runsSectionToggle', state.ui.sections.runs);
    syncSectionBtn('#processSectionToggle', state.ui.processCollapsed);
    syncSectionBtn('#todoSectionToggle', state.ui.todoCollapsed);
    applySideTab(state.ui.sideTab);
    const strip = $('#queueStrip');
    if (strip && state.ui.queueCollapsed) strip.classList.add('collapsed');
    state.viewer.wrap = !!state.ui.viewerWrap;
    syncDock();
  }

  function renderThemeScheduleNote() {
    const note = $('#themeScheduleNote');
    if (!note) return;
    const sc = state.themeSchedule;
    if (sc.mode === 'daynight') {
      note.textContent = t('appearance.noteDayNight', {
        day: themeLabel(sc.day),
        night: themeLabel(sc.night),
        dayStart: sc.dayStart || '06:00',
        nightStart: sc.nightStart || '19:00',
      });
    } else if (sc.mode === 'rotate') note.textContent = t('appearance.noteRotate', { interval: sc.interval });
    else note.textContent = t('appearance.noteManual');
  }

  function syncThemeScheduleControls() {
    const sc = state.themeSchedule;
    $$('#themeScheduleSeg [data-schedule-mode]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.scheduleMode === sc.mode);
    });
    const dayFields = $('#themeDayNightFields');
    const rotateFields = $('#themeRotateFields');
    if (dayFields) dayFields.hidden = sc.mode !== 'daynight';
    if (rotateFields) rotateFields.hidden = sc.mode !== 'rotate';
    const setVal = (sel, value) => {
      const el = $(sel);
      if (el && value) el.value = value;
    };
    setVal('#themeDayTheme', sc.day);
    setVal('#themeNightTheme', sc.night);
    setVal('#themeDayStart', sc.dayStart || '06:00');
    setVal('#themeNightStart', sc.nightStart || '19:00');
    const intervalSel = $('#themeRotateInterval');
    if (intervalSel) intervalSel.value = String(sc.interval || 15);
  }

  function bindAppearanceSettings() {
    renderThemeGrid();
    renderThemeScheduleNote();
    const fillThemes = (sel, current) => {
      if (!sel) return;
      sel.innerHTML = THEMES.map((th) => `<option value="${th.id}">${escapeHtml(t('theme.' + th.id) || th.label)}</option>`).join('');
      sel.value = current;
    };
    fillThemes($('#themeDayTheme'), state.themeSchedule.day);
    fillThemes($('#themeNightTheme'), state.themeSchedule.night);
    syncThemeScheduleControls();
    $$('#themeScheduleSeg [data-schedule-mode]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.themeSchedule.mode = btn.dataset.scheduleMode;
        syncThemeScheduleControls();
        saveThemeSchedule();
        renderThemeScheduleNote();
        startThemeSchedule();
      });
    });
    const onField = (sel, key, parse) => {
      if (!sel) return;
      sel.addEventListener('change', (e) => {
        state.themeSchedule[key] = parse ? parse(e.target.value) : e.target.value;
        saveThemeSchedule();
        renderThemeScheduleNote();
        startThemeSchedule();
      });
    };
    onField($('#themeDayTheme'), 'day');
    onField($('#themeNightTheme'), 'night');
    onField($('#themeDayStart'), 'dayStart', (v) => (v || '06:00'));
    onField($('#themeNightStart'), 'nightStart', (v) => (v || '19:00'));
    onField($('#themeRotateInterval'), 'interval', (v) => parseInt(v, 10) || 15);
    // 宽度用滑杆：拖动时即时预览，松手才落盘；右侧数字是当前值。
    const bindRange = (inputSel, outputSel, min, max, apply) => {
      const input = $(inputSel);
      if (!input) return;
      const output = $(outputSel);
      const paint = () => {
        if (!output) return;
        output.textContent = String(Math.round(Number(input.value) || 0));
      };
      const set = (value, persist) => {
        const bound = Math.min(max(), Math.max(min, Math.round(Number(value) || min)));
        input.value = String(bound);
        apply(bound);
        paint();
        if (persist) saveUi();
      };
      input.addEventListener('input', () => set(input.value, false));
      input.addEventListener('change', () => set(input.value, true));
      paint();
    };
    bindRange('#uiSidebarW', '#uiSidebarWVal', 180, () => 420, (w) => {
      state.ui.sidebarW = w;
      const app = $('#app');
      if (app && !state.ui.sidebarCollapsed) app.style.setProperty('--sidebar-w', w + 'px');
    });
    bindRange('#uiDockW', '#uiDockWVal', 220, () => dockMaxWidth(), (w) => {
      state.ui.dockW = w;
      applyDockWidth();
    });
    const bindToggle = (sel, apply) => {
      const el = $(sel);
      if (!el) return;
      el.addEventListener('change', () => {
        apply(el.checked);
        saveUi();
      });
    };
    bindToggle('#uiSidebarCollapsed', (checked) => {
      state.ui.sidebarCollapsed = checked;
      const app = $('#app');
      if (app) {
        app.dataset.sidebarCollapsed = checked ? '1' : '0';
        if (checked) app.style.removeProperty('--sidebar-w');
        else app.style.setProperty('--sidebar-w', (state.ui.sidebarW || 276) + 'px');
      }
    });
    bindToggle('#uiQueueBar', (expanded) => {
      state.ui.queueCollapsed = !expanded;
      const strip = $('#queueStrip');
      if (strip) strip.classList.toggle('collapsed', !expanded);
      const btn = $('#queueToggle');
      if (btn) btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });
    bindToggle('#uiViewerWrap', (checked) => {
      state.ui.viewerWrap = checked;
      state.viewer.wrap = checked;
      if (state.viewer.editor) state.viewer.editor.setOption('lineWrapping', checked);
      updateViewerStatus();
    });
    const resetBtn = $('#resetLayoutBtn');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        state.ui = {
          dockW: 340, dockCollapsed: true, active: null, openTabs: [], memView: 'list',
          codegraphView: 'tree',
          sidebarW: 276, sidebarCollapsed: false, sideTab: 'chat', sections: {},
          queueCollapsed: false, viewerWrap: false, wsOpen: {},
        };
        saveUi();
        applyUi();
        syncAppearanceControls();
        renderOutline(true);
        toast(t('appearance.layoutReset'), 'ok', 4000);
      });
    }
    syncAppearanceControls();
  }

  function bindSidebarResizer() {
    const resizer = $('#sidebarResizer');
    const app = $('#app');
    if (!resizer || !app) return;
    resizer.addEventListener('pointerdown', (e) => {
      if (app.dataset.sidebarCollapsed === '1') return;
      e.preventDefault();
      resizer.classList.add('dragging');
      const startX = e.clientX;
      const startW = state.ui.sidebarW || 276;
      const onMove = (ev) => {
        const w = Math.min(420, Math.max(180, startW + ev.clientX - startX));
        state.ui.sidebarW = w;
        app.style.setProperty('--sidebar-w', w + 'px');
      };
      const onUp = () => {
        resizer.classList.remove('dragging');
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        saveUi();
        syncAppearanceControls();
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
  }

  function syncAppearanceControls() {
    const setVal = (sel, v) => { const el = $(sel); if (el) el.value = v; };
    const setChk = (sel, v) => { const el = $(sel); if (el) el.checked = !!v; };
    const setOut = (sel, v) => { const el = $(sel); if (el) el.textContent = String(Math.round(v)); };
    const sidebarW = Math.round(state.ui.sidebarW || 276);
    const dockW = Math.round(Math.min(state.ui.dockW || 340, dockMaxWidth()));
    const dockInput = $('#uiDockW');
    if (dockInput) dockInput.max = String(Math.round(dockMaxWidth()));
    setVal('#uiSidebarW', sidebarW);
    setVal('#uiDockW', dockW);
    setOut('#uiSidebarWVal', sidebarW);
    setOut('#uiDockWVal', dockW);
    setChk('#uiSidebarCollapsed', state.ui.sidebarCollapsed);
    setChk('#uiQueueBar', !state.ui.queueCollapsed);
    setChk('#uiViewerWrap', state.ui.viewerWrap);
  }

  function bindDockControls() {
    const app = $('#app');
    const sidebarToggle = $('#sidebarToggle');
    if (sidebarToggle) {
      sidebarToggle.addEventListener('click', () => {
        state.ui.sidebarCollapsed = !state.ui.sidebarCollapsed;
        if (app) {
          app.dataset.sidebarCollapsed = state.ui.sidebarCollapsed ? '1' : '0';
          if (state.ui.sidebarCollapsed) app.style.removeProperty('--sidebar-w');
          else app.style.setProperty('--sidebar-w', (state.ui.sidebarW || 276) + 'px');
        }
        saveUi();
        syncAppearanceControls();
      });
    }
    const procToggle = $('#processSectionToggle');
    if (procToggle) {
      // 进程面板的收缩**只**由这里触发（没有自动收缩）。
      procToggle.addEventListener('click', () => {
        state.ui.processCollapsed = !state.ui.processCollapsed;
        saveUi();
        renderProcesses();
      });
    }
    const todoToggle = $('#todoSectionToggle');
    if (todoToggle) {
      // 任务面板的收缩同样只由这里触发。
      todoToggle.addEventListener('click', () => {
        state.ui.todoCollapsed = !state.ui.todoCollapsed;
        saveUi();
        renderTodoDrawer();
      });
    }
    $$('[data-section-collapse]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const name = btn.dataset.sectionCollapse;
        if (name !== 'runs') return;
        const runs = $('#activeRuns');
        if (!runs) return;
        const collapsed = !runs.classList.contains('is-collapsed');
        runs.classList.toggle('is-collapsed', collapsed);
        state.ui.sections.runs = collapsed;
        btn.classList.toggle('is-collapsed', collapsed);
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        saveUi();
        syncAppearanceControls();
      });
    });
    $$('#sideTabs [data-side-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        applySideTab(btn.dataset.sideTab, { persist: true });
      });
    });
    $$('[data-sidebar-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const action = btn.dataset.sidebarAction;
        if (action === 'new') {
          const nb = $('#newChatBtn');
          if (nb) nb.click();
        } else if (action === 'search') {
          applySideTab('chat', { persist: true });
          state.ui.sidebarCollapsed = false;
          applyUi();
          saveUi();
          syncAppearanceControls();
          setTimeout(() => { const input = $('#searchInput'); if (input) input.focus(); }, 60);
        } else if (action === 'workspace') {
          openDrawer();
        } else if (action === 'memory') {
          openMemDrawer(workspaceId());
        }
      });
    });
    $$('#memViewSeg [data-mem-view]').forEach((btn) => {
      btn.addEventListener('click', () => setMemView(btn.dataset.memView));
    });
    const expandBtn = $('#dockExpand');
    if (expandBtn) {
      expandBtn.addEventListener('click', () => {
        state.ui.dockCollapsed = false;
        saveUi();
        syncDock();
      });
    }
    bindDockResizer();
    bindSidebarResizer();
    const queueToggle = $('#queueToggle');
    if (queueToggle) {
      queueToggle.addEventListener('click', () => {
        const strip = $('#queueStrip');
        if (!strip) return;
        strip.classList.toggle('collapsed');
        const collapsed = strip.classList.contains('collapsed');
        queueToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        state.ui.queueCollapsed = collapsed;
        saveUi();
        syncAppearanceControls();
      });
    }
    bindAppearanceSettings();
  }

  /* ── workspace drawer ────────────────────────────────────────────────── */

  function openDrawer() {
    if (isChatOnlyWorkspace()) {
      toast(t('workspace.chatOnlyNote'), 'ok', 5200);
      return;
    }
    closeModelMenu();
    if ($('#settingsModal')) $('#settingsModal').hidden = true;
    const ws = currentWorkspace();
    const title = $('#drawerWorkspaceName');
    if (title) title.textContent = ws ? ws.name : t('workspace.title');
    openPanel('workspace');
    state.ws.open = true;
    loadWorkspace(state.ws.path || '.');
  }

  function closeDrawer() {
    closePanel('workspace');
    state.ws.open = false;
  }

  async function loadWorkspace(path = '.') {
    state.ws.path = path || '.';
    state.ws.loading = true;
    $('#workspacePath').textContent = path === '.' ? '/' : `/${path}`;
    $('#wsList').innerHTML = `<div class="ws-empty"><span class="spinner"></span> ${escapeHtml(t('workspace.loading'))}</div>`;
    try {
      const entries = await api(`/workspace/list?workspace_id=${encodeURIComponent(workspaceId())}&path=${encodeURIComponent(path || '.')}`);
      state.ws.entries = Array.isArray(entries) ? entries : [];
      renderWorkspace();
    } catch (err) {
      $('#wsList').innerHTML = `<div class="ws-empty">${escapeHtml(err.message)}</div>`;
    } finally {
      state.ws.loading = false;
    }
  }

  function renderWorkspace() {
    const list = $('#wsList');
    const entries = state.ws.entries || [];
    const root = (currentWorkspace() && currentWorkspace().root_path) || '';
    const suffix = state.ws.path === '.' ? '' : state.ws.path;
    $('#wsFooter').textContent = `${root}${suffix ? `/${suffix}` : ''}`;
    $('#wsFooter').title = $('#wsFooter').textContent;
    if (!entries.length) {
      list.innerHTML = `<div class="ws-empty">${t('workspace.empty')}</div>`;
      return;
    }
    list.innerHTML = entries.map((entry) => {
      const isDir = entry.type === 'dir';
      const active = !isDir && state.viewer.open && state.viewer.path === entry.path;
      return `<div class="ws-row${active ? ' active' : ''}">
        <button type="button" class="ws-main" data-ws-path="${escAttr(entry.path)}" data-ws-type="${isDir ? 'dir' : 'file'}">
          <svg><use href="#i-${isDir ? 'folder' : 'file'}"/></svg>
          <span class="ws-name">${escapeHtml(entry.name)}</span>
          <span class="ws-size">${isDir ? '' : bytes(entry.size)}</span>
          <span class="ws-open">${escapeHtml(isDir ? t('workspace.open') : t('workspace.view'))}</span>
        </button>
        <button type="button" class="ws-ref-btn" data-ws-ref="${escAttr(entry.path)}" data-ws-ref-dir="${isDir ? '1' : '0'}" title="${escAttr(t('ref.title'))}" aria-label="${escAttr(t('ref.title'))}">
          <svg><use href="#i-plus"/></svg>
        </button>
      </div>`;
    }).join('');
  }

  function wsParent() {
    const parts = String(state.ws.path || '.').split('/').filter((p) => p && p !== '.');
    parts.pop();
    loadWorkspace(parts.length ? parts.join('/') : '.');
  }

  /* ── 工作区文件右键菜单 ─────────────────────────────────────────────── */

  let fileMenuCtx = null;

  function wsJoin(dir, name) {
    const d = String(dir || '.').replace(/\/+$/g, '');
    return d && d !== '.' ? `${d}/${name}` : name;
  }

  function wsDirOf(path, type) {
    if (type === 'dir') return path;
    const parts = String(path || '').split('/');
    parts.pop();
    return parts.length ? parts.join('/') : '.';
  }

  function ensureFileMenu() {
    let menu = $('#fileMenu');
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'fileMenu';
    menu.className = 'context-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-file-action]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      const ctx = fileMenuCtx;
      closeFileMenu();
      if (ctx) runFileAction(btn.dataset.fileAction, ctx);
    });
    return menu;
  }

  function closeFileMenu() {
    const menu = $('#fileMenu');
    if (menu) menu.hidden = true;
    fileMenuCtx = null;
  }

  /* ── 通用右键菜单（消息 / 会话 / 编辑区） ─────────────────────────── */

  let uiMenuItems = [];

  function ensureUiMenu() {
    let menu = $('#uiMenu');
    if (menu) return menu;
    menu = document.createElement('div');
    menu.id = 'uiMenu';
    menu.className = 'context-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-ui-action]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      const item = uiMenuItems[Number(btn.dataset.uiAction)];
      closeUiMenu();
      if (item && typeof item.action === 'function') item.action();
    });
    return menu;
  }

  function closeUiMenu() {
    const menu = $('#uiMenu');
    if (menu) menu.hidden = true;
    uiMenuItems = [];
  }

  function openUiMenu(x, y, items) {
    const list = (items || []).filter(Boolean);
    if (!list.length) return;
    closeFileMenu();
    const menu = ensureUiMenu();
    uiMenuItems = list;
    menu.innerHTML = list.map((item, index) => {
      if (item.separator) return '<div class="context-menu-sep"></div>';
      const danger = item.danger ? ' danger' : '';
      const disabled = item.disabled ? ' disabled' : '';
      return `<button type="button" class="context-menu-item${danger}" data-ui-action="${index}" role="menuitem"${disabled}><svg><use href="#i-${item.icon || 'chevron-right'}"/></svg><span>${escapeHtml(item.label || '')}</span></button>`;
    }).join('');
    menu.hidden = false;
    const pad = 8;
    const rect = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(pad, Math.min(x, window.innerWidth - rect.width - pad))}px`;
    menu.style.top = `${Math.max(pad, Math.min(y, window.innerHeight - rect.height - pad))}px`;
  }

  function _selectionText() {
    try {
      return String(window.getSelection() ? window.getSelection().toString() : '');
    } catch {
      return '';
    }
  }

  function _editableTarget(target) {
    const el = target.closest('input, textarea, [contenteditable="true"], [contenteditable=""]');
    if (!el) return null;
    if (el.tagName === 'INPUT' && typeof el.selectionStart !== 'number') return null;
    return el;
  }

  function _editableSelection(el) {
    if (!el) return '';
    try {
      if (typeof el.selectionStart === 'number' && typeof el.value === 'string') {
        return el.value.slice(el.selectionStart, el.selectionEnd);
      }
    } catch { /* ignore */ }
    return _selectionText();
  }

  function _replaceEditable(el, text) {
    if (!el) return;
    try {
      el.focus();
      if (typeof el.selectionStart === 'number' && typeof el.value === 'string') {
        const start = el.selectionStart;
        const end = el.selectionEnd;
        el.setRangeText(text, start, end, 'end');
      } else {
        el.focus();
        document.execCommand('insertText', false, text);
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
    } catch (err) {
      toast(t('filemenu.failed', { msg: err.message || 'paste' }), 'bad', 3000);
    }
  }

  function _pasteInto(el) {
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      toast(t('menu.clipboardFailed'), 'bad', 3000);
      return;
    }
    navigator.clipboard.readText().then((text) => {
      if (text) _replaceEditable(el, text);
    }).catch(() => toast(t('menu.clipboardFailed'), 'bad', 3000));
  }

  function _cmSelection(editor) {
    try { return String(editor.getSelection() || ''); } catch { return ''; }
  }

  function _messageMenu(target, x, y) {
    const msgEl = target.closest('[data-msg]');
    if (!msgEl) return false;
    const id = msgEl.dataset.msg;
    if (!id || id === 'live') return false;
    const isUser = msgEl.classList.contains('user');
    const msg = findMessage(id);
    const selected = _selectionText();
    const items = [];
    if (selected) {
      items.push({ label: t('menu.copySelected'), icon: 'copy', action: () => copyText(selected) });
    }
    items.push({
      label: t(isUser ? 'menu.copyMessage' : 'menu.copyAnswer'),
      icon: 'copy',
      action: () => {
        const text = (msg && msg.content) || msgEl.innerText || '';
        copyText(text.trim());
      },
    });
    if (!String(id).startsWith('local-')) {
      items.push({
        label: t('menu.fork'), icon: 'flow',
        action: () => forkConversationFrom(id),
      });
    }
    if (isUser) {
      const running = messageRunState(msg) === 'running';
      const actionable = !String(id).startsWith('local-');
      items.push({ separator: true });
      items.push({
        label: t('msg.edit'), icon: 'edit', disabled: running || !actionable,
        action: () => startEditMessage(id),
      });
      if (!isChatOnlyWorkspace()) {
        items.push({
          label: t('rewind.action'), icon: 'clock', disabled: running || !actionable,
          action: () => openRewindModal(id),
        });
      }
      items.push({
        label: t('msg.recall'), icon: 'undo', danger: true, disabled: running || !actionable,
        action: () => recallMessage(id),
      });
    }
    openUiMenu(x, y, items);
    return true;
  }

  function _conversationMenu(target, x, y) {
    let id = '';
    const row = target.closest('[data-conv-row]');
    if (row) id = row.dataset.convRow || '';
    if (!id) {
      const main = target.closest('[data-conv], [data-picker-conv], [data-conv-del]');
      if (main) id = main.dataset.conv || main.dataset.pickerConv || main.dataset.convDel || '';
    }
    if (!id) return false;
    const conv = (state.conversations || []).find((c) => c.id === id)
      || (state.picker.items || []).find((c) => c.id === id);
    const title = conv ? conversationTitle(conv) : id;
    openUiMenu(x, y, [
      {
        label: t('menu.open'), icon: 'message',
        action: () => {
          if (state.picker.open) {
            $('#convPickerModal').hidden = true;
            state.picker.open = false;
          }
          openConversation(id);
        },
      },
      { label: t('menu.copyTitle'), icon: 'copy', action: () => copyText(title) },
      {
        label: t('fork.title'), icon: 'flow',
        action: () => forkConversationFrom('', id),
      },
      { separator: true },
      { label: t('menu.delete'), icon: 'trash', danger: true, action: () => deleteConversation(id) },
    ]);
    return true;
  }

  function _editorMenu(target, x, y) {
    const cmEl = target.closest('.CodeMirror');
    const editor = cmEl ? state.viewer.editor : null;
    if (cmEl && editor) {
      const selected = _cmSelection(editor);
      openUiMenu(x, y, [
        {
          label: t('menu.copy'), icon: 'copy', disabled: !selected,
          action: () => copyText(selected),
        },
        {
          label: t('menu.cut'), icon: 'edit', disabled: !selected,
          action: () => { copyText(selected); editor.replaceSelection(''); },
        },
        {
          label: t('menu.paste'), icon: 'copy',
          action: () => {
            if (!navigator.clipboard || !navigator.clipboard.readText) {
              toast(t('menu.clipboardFailed'), 'bad', 3000);
              return;
            }
            navigator.clipboard.readText().then((text) => {
              if (text) {
                editor.replaceSelection(text);
                editor.focus();
              }
            }).catch(() => toast(t('menu.clipboardFailed'), 'bad', 3000));
          },
        },
        {
          label: t('menu.selectAll'), icon: 'list',
          action: () => editor.execCommand('selectAll'),
        },
      ]);
      return true;
    }
    const editable = _editableTarget(target);
    if (editable) {
      const selected = _editableSelection(editable);
      openUiMenu(x, y, [
        {
          label: t('menu.cut'), icon: 'edit', disabled: !selected,
          action: () => {
            if (selected) copyText(selected);
            _replaceEditable(editable, '');
          },
        },
        {
          label: t('menu.copy'), icon: 'copy', disabled: !selected,
          action: () => copyText(selected),
        },
        {
          label: t('menu.paste'), icon: 'copy',
          action: () => _pasteInto(editable),
        },
        { separator: true },
        {
          label: t('menu.selectAll'), icon: 'list',
          action: () => {
            editable.focus();
            if (typeof editable.select === 'function') editable.select();
            else document.execCommand('selectAll');
          },
        },
      ]);
      return true;
    }
    const selected = _selectionText();
    if (selected.trim()) {
      openUiMenu(x, y, [
        { label: t('menu.copySelected'), icon: 'copy', action: () => copyText(selected) },
      ]);
      return true;
    }
    return false;
  }

  function handleContextMenu(e) {
    // 先阻止 WKWebView 原生菜单（其中会带系统 Services 栏）。
    e.preventDefault();
    closeFileMenu();
    closeUiMenu();
    if (_conversationMenu(e.target, e.clientX, e.clientY)) return;
    if (_messageMenu(e.target, e.clientX, e.clientY)) return;
    if (_editorMenu(e.target, e.clientX, e.clientY)) return;
  }

  function openFileMenu(x, y, ctx) {
    closeUiMenu();
    const menu = ensureFileMenu();
    fileMenuCtx = ctx;
    const isDir = ctx.type === 'dir';
    const row = (action, icon, key) => `<button type="button" class="context-menu-item" data-file-action="${action}" role="menuitem"><svg><use href="#i-${icon}"/></svg><span>${escapeHtml(t(key))}</span></button>`;
    const parts = [];
    if (ctx.path) {
      parts.push(row('open', isDir ? 'folder' : 'eye', 'filemenu.open'));
      parts.push(row('reveal', 'folder', 'filemenu.reveal'));
      parts.push(row('copy', 'copy', 'filemenu.copyPath'));
      if (!isDir) parts.push(row('download', 'download', 'filemenu.download'));
      parts.push('<div class="context-menu-sep"></div>');
      parts.push(row('rename', 'edit', 'filemenu.rename'));
      parts.push(row('delete', 'trash', 'filemenu.delete'));
      parts.push('<div class="context-menu-sep"></div>');
    }
    parts.push(row('newFile', 'file', 'filemenu.newFile'));
    parts.push(row('newFolder', 'folder-plus', 'filemenu.newFolder'));
    parts.push(row('refresh', 'refresh', 'filemenu.refresh'));
    menu.innerHTML = parts.join('');
    menu.hidden = false;
    const pad = 8;
    const rect = menu.getBoundingClientRect();
    const left = Math.min(x, window.innerWidth - rect.width - pad);
    const top = Math.min(y, window.innerHeight - rect.height - pad);
    menu.style.left = `${Math.max(pad, left)}px`;
    menu.style.top = `${Math.max(pad, top)}px`;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
  }

  async function runFileAction(action, ctx) {
    const wsId = workspaceId();
    const dir = ctx.path ? wsDirOf(ctx.path, ctx.type) : (state.ws.path || '.');
    try {
      if (action === 'open') {
        if (ctx.type === 'dir') await loadWorkspace(ctx.path);
        else await openViewer(ctx.path);
        return;
      }
      if (action === 'reveal') {
        await api('/workspace/reveal', {
          method: 'POST',
          body: JSON.stringify({ workspace_id: wsId, path: ctx.path }),
        });
        toast(t('filemenu.revealed'), 'ok', 1800);
        return;
      }
      if (action === 'copy') {
        await copyText(ctx.path);
        toast(t('filemenu.copied'), 'ok', 1600);
        return;
      }
      if (action === 'download') {
        window.open(`${API}/workspace/file/download?workspace_id=${encodeURIComponent(wsId)}&path=${encodeURIComponent(ctx.path)}`, '_blank');
        return;
      }
      if (action === 'rename') {
        const name = window.prompt(t('filemenu.renamePrompt', { name: ctx.name || ctx.path }), ctx.name || '');
        if (!name || name === ctx.name) return;
        await api('/workspace/rename', {
          method: 'POST',
          body: JSON.stringify({ workspace_id: wsId, path: ctx.path, name }),
        });
        if (state.viewer.open && (state.viewer.path === ctx.path
            || String(state.viewer.path || '').startsWith(`${ctx.path}/`))) closeViewer();
        await loadWorkspace(dir);
        toast(t('filemenu.done'), 'ok', 1600);
        return;
      }
      if (action === 'delete') {
        if (!window.confirm(t('filemenu.deleteConfirm', { name: ctx.name || ctx.path }))) return;
        await api(`/workspace/file?workspace_id=${encodeURIComponent(wsId)}&path=${encodeURIComponent(ctx.path)}`, { method: 'DELETE' });
        if (state.viewer.open && (state.viewer.path === ctx.path
            || String(state.viewer.path || '').startsWith(`${ctx.path}/`))) closeViewer();
        await loadWorkspace(dir);
        toast(t('filemenu.done'), 'ok', 1600);
        return;
      }
      if (action === 'newFile' || action === 'newFolder') {
        const name = window.prompt(t(action === 'newFile' ? 'filemenu.newFilePrompt' : 'filemenu.newFolderPrompt'), '');
        if (!name) return;
        const path = wsJoin(dir, name);
        if (action === 'newFolder') {
          await api('/workspace/mkdir', {
            method: 'POST',
            body: JSON.stringify({ workspace_id: wsId, path: dir, name }),
          });
          await loadWorkspace(dir);
        } else {
          await api('/workspace/file', {
            method: 'PUT',
            body: JSON.stringify({ workspace_id: wsId, path, content: '' }),
          });
          await loadWorkspace(dir);
          await openViewer(path);
        }
        toast(t('filemenu.done'), 'ok', 1600);
        return;
      }
      if (action === 'refresh') await loadWorkspace(state.ws.path || '.');
    } catch (err) {
      toast(t('filemenu.failed', { msg: localizeServerMessage(err.message) }), 'bad', 4500);
    }
  }

  /* ── file viewer (the drawer extends to a panel at the right edge) ────── */

  const CM_MODES = {
    js: 'javascript', mjs: 'javascript', jsx: 'javascript', ts: 'javascript', tsx: 'javascript',
    json: 'application/json',
    py: 'python', pyw: 'python',
    html: 'htmlmixed', htm: 'htmlmixed', xhtml: 'htmlmixed',
    xml: 'xml', svg: 'xml',
    css: 'css', scss: 'css', less: 'css',
    md: 'markdown', markdown: 'markdown', mdx: 'markdown',
    sh: 'shell', bash: 'shell', zsh: 'shell',
    sql: 'sql',
    yaml: 'yaml', yml: 'yaml',
    txt: null, log: null, csv: null, tsv: null, ini: null, toml: null, env: null,
  };
  const LANG_LABELS = {
    javascript: 'JavaScript',
    'application/json': 'JSON',
    python: 'Python',
    htmlmixed: 'HTML',
    xml: 'XML',
    css: 'CSS',
    markdown: 'Markdown',
    shell: 'Shell',
    sql: 'SQL',
    yaml: 'YAML',
  };

  function cmModeFor(ext) {
    const key = String(ext || '').toLowerCase().replace(/^\.+/, '');
    return Object.prototype.hasOwnProperty.call(CM_MODES, key) ? CM_MODES[key] : null;
  }

  // Editors detect the indentation the file actually uses.
  function detectIndent(text) {
    for (const line of String(text || '').split('\n')) {
      const match = line.match(/^( +)\S/);
      if (match) return match[1].length;
    }
    return 2;
  }

  let cmLoader = null;
  function ensureCodeMirror() {
    if (window.CodeMirror) return Promise.resolve(true);
    if (!cmLoader) {
      cmLoader = new Promise((resolve) => {
        const script = document.createElement('script');
        script.src = '/assets/vendor/codemirror/codemirror.js';
        script.onload = () => resolve(Boolean(window.CodeMirror));
        script.onerror = () => resolve(false);
        document.head.appendChild(script);
      });
    }
    return cmLoader;
  }

  async function openViewer(path) {
    try {
      const data = await api(`/workspace/file/preview?workspace_id=${encodeURIComponent(workspaceId())}&path=${encodeURIComponent(path)}`);
      const viewer = state.viewer;
      viewer.path = data.path || path;
      viewer.data = data;
      viewer.downloadUrl = `${API}/workspace/file/download?workspace_id=${encodeURIComponent(workspaceId())}&path=${encodeURIComponent(path)}`;
      viewer.dirty = false;
      viewer.editor = null;
      // html / office / markdown 可以渲染，也能看源码；图片、pdf、二进制只有一种视图。
      viewer.mode = data.render !== 'none' && data.text ? 'render' : 'code';
      viewer.open = true;
      openPanel('viewer');
      renderViewer();
      renderWorkspace();
    } catch (err) {
      toast(t('error.openFile', { msg: err.message }), 'bad', 6000);
    }
  }

  function closeViewer() {
    const viewer = state.viewer;
    viewer.open = false;
    viewer.data = null;
    viewer.dirty = false;
    viewer.editor = null;
    closePanel('viewer');
    renderWorkspace();
  }

  function renderViewer() {
    const viewer = state.viewer;
    if (!viewer.open || !viewer.data) return;
    const data = viewer.data;
    const name = data.path || viewer.path;
    const canToggle = data.render !== 'none' && !!data.text;
    $('#viewerName').textContent = name;
    $('#viewerName').title = name;
    $('#viewerMeta').textContent = [
      bytes(data.size),
      (data.ext || '').toUpperCase(),
      t('preview.lines', { lines: (data.text || '').split('\n').length }),
      data.truncated ? t('preview.truncated') : '',
    ].filter(Boolean).join(' · ');
    $('#viewerDownload').href = viewer.downloadUrl;
    $('#viewerModeSeg').hidden = !canToggle;
    $$('#viewerModeSeg [data-view-mode]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.viewMode === viewer.mode);
    });
    // 二进制 / 图片 / pdf 没有文本，不能编辑或复制。
    $('#viewerWrapBtn').hidden = !data.text;
    $('#viewerCopyBtn').hidden = !data.text;
    if (viewer.mode === 'render') renderViewerRender();
    else renderViewerCode();
    updateViewerStatus();
  }

  function renderViewerRender() {
    const viewer = state.viewer;
    const { data, downloadUrl } = viewer;
    const renderEl = $('#viewerRender');
    const codeEl = $('#viewerCode');
    codeEl.hidden = true;
    renderEl.hidden = false;
    const lang = LANG_LABELS[cmModeFor(data.ext)] || (data.ext || '').toUpperCase();
    $('#viewerLang').textContent = lang || 'TEXT';
    if (data.render === 'iframe') {
      // 沙箱 iframe：渲染 html 文件，但不执行其中的脚本。
      renderEl.innerHTML = `<iframe class="preview-frame" sandbox="" title="${escAttr(data.path || '')}"></iframe>`;
      renderEl.querySelector('iframe').srcdoc = data.html || '';
    } else if (data.render === 'image') {
      renderEl.innerHTML = `<img class="preview-image" src="${escAttr(downloadUrl)}" alt="${escAttr(data.path || '')}" />`;
    } else if (data.render === 'pdf') {
      renderEl.innerHTML = `<iframe class="preview-frame preview-pdf" src="${escAttr(downloadUrl)}" title="${escAttr(data.path || '')}"></iframe>`;
    } else {
      renderEl.innerHTML = `<div class="pv-doc">${data.html || ''}</div>`;
    }
  }

  async function renderViewerCode() {
    const viewer = state.viewer;
    const data = viewer.data;
    const text = data.text || '';
    const host = $('#viewerCode');
    const mode = cmModeFor(data.ext);
    $('#viewerLang').textContent = LANG_LABELS[mode] || (mode === null ? 'TEXT' : (data.ext || '').toUpperCase());
    renderElHide();

    if (!text) {
      // 二进制 / 无法解析的文件：没有源码可编辑，只给出提示。
      host.innerHTML = `<pre class="viewer-plain">${escapeHtml(data.note_key ? t(data.note_key) : t('preview.empty'))}</pre>`;
      return;
    }

    const ready = await ensureCodeMirror();
    if (!ready) {
      host.innerHTML = `<pre class="viewer-plain">${escapeHtml(text || t(data.note_key || 'preview.unavailable'))}</pre>`;
      $('#viewerLang').textContent = t('preview.editorFallback');
      return;
    }
    host.innerHTML = '';
    const editor = CodeMirror(host, {
      value: text,
      mode,
      lineNumbers: true,
      lineWrapping: viewer.wrap,
      readOnly: true,
      cursorLine: true,
      matchBrackets: true,
      foldGutter: true,
      gutters: ['CodeMirror-linenumbers', 'CodeMirror-foldgutter'],
      indentUnit: detectIndent(text),
      tabSize: 4,
      autoCloseTags: false,
      autoCloseBrackets: false,
    });
    viewer.editor = editor;
    const updatePos = () => {
      const cursor = editor.getCursor();
      $('#viewerPos').textContent = t('preview.pos', { line: cursor.line + 1, col: cursor.ch + 1 });
    };
    updatePos();
    host.addEventListener('click', updatePos);
    host.addEventListener('keydown', updatePos);
  }

  function renderElHide() {
    const viewer = state.viewer;
    const renderEl = $('#viewerRender');
    const codeEl = $('#viewerCode');
    renderEl.hidden = true;
    codeEl.hidden = viewer.mode !== 'code';
  }

  function updateViewerStatus() {
    const viewer = state.viewer;
    const status = $('#viewerStatus');
    status.textContent = viewer.data && viewer.data.text ? t('preview.readonly') : '';
    status.className = 'mono';
    $('#viewerWrapBtn').setAttribute('aria-pressed', String(viewer.wrap));
    $('#viewerWrapBtn').classList.toggle('active', viewer.wrap);
  }

  async function copyViewerContents() {
    const viewer = state.viewer;
    const text = viewer.editor ? viewer.editor.getValue() : (viewer.data && viewer.data.text) || '';
    try {
      await navigator.clipboard.writeText(text);
      toast(t('copy.done'), 'ok', 2500);
    } catch {
      toast(t('copy.failed'), 'bad', 4000);
    }
  }

  /* ── runtime ─────────────────────────────────────────────────────────── */

  function renderRuntime() {
    const dot = $('#runtimeDot');
    if (state.runtimeOnline === false) {
      dot.className = 'dot bad';
      $('#runtimeText').textContent = t('runtime.offline');
      return;
    }
    if (!state.runtime) {
      dot.className = 'dot';
      $('#runtimeText').textContent = t('runtime.connecting');
      return;
    }
    const running = Number(state.runtime?.scheduler?.running || 0);
    const queued = Number(state.runtime?.scheduler?.queued || 0);
    dot.className = 'dot ok';
    if (running || queued) {
      $('#runtimeText').textContent = queued
        ? t('runtime.runningQueued', { running, queued })
        : t('runtime.running', { running });
    } else {
      $('#runtimeText').textContent = t('runtime.ready');
    }
  }

  async function pollRuntime() {
    try {
      const data = await api('/app/runtime');
      state.runtime = data;
      state.runtimeOnline = true;
      renderRuntime();
    } catch {
      state.runtimeOnline = false;
      renderRuntime();
    }
  }

  /* ── theme ───────────────────────────────────────────────────────────── */

  // V3 主壳与内嵌设置页共用一个页面来源：主题变化时把结果广播给父页面，
  // 让外壳和设置页不会一边亮、一边暗。
  function notifyThemeToParent() {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({
          type: 'atlasx:theme',
          theme: state.theme,
          schedule: state.themeSchedule,
        }, location.origin);
      }
    } catch { /* 普通浏览器里没有父页面时忽略 */ }
  }

  function applyTheme(theme, opts = {}) {
    const id = THEMES.some((th) => th.id === theme) ? theme : 'dark';
    state.theme = id;
    if (window.AtlasTheme) {
      AtlasTheme.apply(id);
    } else {
      document.documentElement.dataset.theme = id;
      document.documentElement.dataset.themeMode = THEME_MODE[id] || 'dark';
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.content = THEME_BG[id] || '#0d0d0e';
    }
    localStorage.setItem('az-theme', id);
    saveUi();
    renderThemeGrid();
    if (opts.notify !== false) notifyThemeToParent();
  }

  function scheduledTheme() {
    if (!window.AtlasTheme) return state.theme;
    return AtlasTheme.scheduledTheme(state.themeSchedule, state.theme);
  }

  function saveThemeSchedule() {
    if (window.AtlasTheme) state.themeSchedule = AtlasTheme.normalizeSchedule(state.themeSchedule);
    localStorage.setItem('az-theme-schedule', JSON.stringify(state.themeSchedule));
    saveUi();
    notifyThemeToParent();
  }

  function startThemeSchedule() {
    if (state.scheduleTimer) { clearInterval(state.scheduleTimer); state.scheduleTimer = null; }
    if (state.themeSchedule.mode === 'manual') return;
    const target = scheduledTheme();
    if (target !== state.theme) applyTheme(target);
    state.scheduleTimer = setInterval(() => {
      const next = scheduledTheme();
      if (next !== state.theme) applyTheme(next);
    }, 20000);
  }

  // 外壳（V3）手动切换主题时，设置页跟着更新，避免内嵌页显示旧主题。
  window.addEventListener('message', (event) => {
    if (event.origin !== location.origin) return;
    const data = event.data || {};
    if (data.type !== 'atlasx:theme-apply') return;
    if (data.schedule) {
      state.themeSchedule = window.AtlasTheme
        ? AtlasTheme.normalizeSchedule(data.schedule)
        : { ...state.themeSchedule, ...data.schedule };
      localStorage.setItem('az-theme-schedule', JSON.stringify(state.themeSchedule));
    }
    if (data.theme) applyTheme(data.theme, { notify: false });
    syncThemeScheduleControls();
    renderThemeScheduleNote();
    startThemeSchedule();
  });

  function themeLabel(id) {
    const th = THEMES.find((x) => x.id === id);
    return th ? t('theme.' + th.id) || th.label : id;
  }

  function renderThemeGrid() {
    const grid = $('#themeGrid');
    if (!grid) return;
    grid.innerHTML = THEMES.map((th) => `
      <button type="button" class="theme-swatch${state.theme === th.id ? ' active' : ''}" data-theme-id="${th.id}">
        <span class="theme-swatch-dots">
          <i style="background:${THEME_BG[th.id]}"></i>
          <i style="background:${THEME_BG[th.id] === '#0d0d0e' ? '#a8c4e0' : '#1a1a1c'}"></i>
        </span>
        <span>${escapeHtml(t('theme.' + th.id) || th.label)}</span>
      </button>`).join('');
    $$('.theme-swatch', grid).forEach((btn) => {
      btn.addEventListener('click', () => {
        state.themeSchedule.mode = 'manual';
        saveThemeSchedule();
        applyTheme(btn.dataset.themeId);
        syncThemeScheduleControls();
        renderThemeGrid();
        renderThemeScheduleNote();
      });
    });
  }

  /* ── language ────────────────────────────────────────────────────────── */

  function translateStatic() {
    $$('[data-i18n]').forEach((el) => {
      el.textContent = t(el.dataset.i18n);
    });
    $$('[data-i18n-html]').forEach((el) => {
      el.innerHTML = t(el.dataset.i18nHtml);
    });
    $$('[data-i18n-placeholder]').forEach((el) => {
      el.setAttribute('placeholder', t(el.dataset.i18nPlaceholder));
    });
    $$('[data-i18n-title]').forEach((el) => {
      el.setAttribute('title', t(el.dataset.i18nTitle));
    });
    $$('[data-i18n-aria-label]').forEach((el) => {
      el.setAttribute('aria-label', t(el.dataset.i18nAriaLabel));
    });
  }

  function syncLanguageControls() {
    const systemBtn = $('#langSeg [data-lang-mode="system"]');
    if (systemBtn) {
      systemBtn.textContent = t('language.systemNamed', {
        lang: langDisplayName(systemLanguage()),
      });
    }
    $$('#langSeg button').forEach((btn) => {
      const active = btn.dataset.langMode === state.langMode;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', String(active));
    });
    const label = $('#langBtnText');
    if (label) label.textContent = state.lang === 'zh' ? 'EN' : '中';
  }

  function syncSettingsLanguage() {
    const modal = $('#settingsModal');
    if (modal && !modal.hidden) refreshModelPlaceholders();
    const menu = $('#modelMenu');
    if (menu && !menu.hidden) renderModelMenu();
  }

  function applyLanguage(mode = state.langMode, { persist = true } = {}) {
    state.langMode = ['system', 'zh', 'en'].includes(mode) ? mode : 'system';
    state.lang = resolveLanguage(state.langMode);
    document.documentElement.lang = uiLocale();
    document.documentElement.dataset.lang = state.lang;
    if (persist) {
      localStorage.setItem('az-lang', state.langMode);
      saveUi();
    }

    translateStatic();
    syncLanguageControls();
    syncSettingsLanguage();
    renderRuntime();
    renderWorkspaceSwitcher();

    render();
    renderConversations();
    renderOutline(true);
    if (isVcsDrawerOpen() && state.vcs) renderVcsTab().catch(() => {});
    if (isMemDrawerOpen() && state.mem) {
      renderMemSummary();
      renderSideStats();
      if (state.mem.view === 'graph') renderSideGraph(false).catch(() => {});
      else renderMemList().catch(() => {});
    }
    if (isPanelOpen('codegraph') && state.codegraph) renderCodegraphPanel(state.codegraph.data);
    if ($('#settingsModal') && !$('#settingsModal').hidden) renderSettingsWorkspaces();
    if (state.picker.open) renderConvPicker();
    if ($('#addWorkspaceModal') && !$('#addWorkspaceModal').hidden) {
      const note = $('#folderPickerNote');
      if (note) note.textContent = t('workspace.folderCount', { n: $$('#folderPickerList .folder-row').length });
    }
    if (state.ws.open) {
      const title = $('#drawerWorkspaceName');
      if (title) title.textContent = currentWorkspace() ? currentWorkspace().name : t('workspace.title');
      if (state.ws.loading) {
        $('#wsList').innerHTML = `<div class="ws-empty"><span class="spinner"></span> ${escapeHtml(t('workspace.loading'))}</div>`;
      } else {
        renderWorkspace();
      }
    }
  }

  function cycleLanguage() {
    applyLanguage(state.lang === 'zh' ? 'en' : 'zh');
  }

  /* ── composer helpers ────────────────────────────────────────────────── */

  function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 210)}px`;
  }

  function setMode(mode) {
    state.mode = mode === 'goal' ? 'goal' : 'autonomous';
    if (state.mode === 'goal') state.goalDismissed = false;
    renderHeader();
    renderGoalBar();
    // 实时保存该会话的运行模式；尚无对话时保存为默认（新对话继承）
    if (state.currentId && state.conv) {
      api(`/conversations/${encodeURIComponent(state.currentId)}/mode`, {
        method: 'PUT', body: JSON.stringify({ mode: state.mode }),
      }).catch((err) => console.warn('run mode save failed', err));
    } else {
      api('/app/permissions', {
        method: 'PUT', body: JSON.stringify({ run_mode: state.mode }),
      }).catch((err) => console.warn('run mode default save failed', err));
    }
  }

  function closeTopLayer() {
    const fileMenu = $('#fileMenu');
    if (fileMenu && !fileMenu.hidden) { closeFileMenu(); return true; }
    const thinkingMenu = $('#thinkingMenu');
    if (thinkingMenu && !thinkingMenu.hidden) { closeThinkingMenu(); return true; }
    const modelMenu = $('#modelMenu');
    if (modelMenu && !modelMenu.hidden) { closeModelMenu(); return true; }
    if (state.viewer.open) { closeViewer(); return true; }
    if (isVcsDrawerOpen()) { closeVcsDrawer(); return true; }
    if (isMemDrawerOpen()) { closeMemDrawer(); return true; }
    if (isTodoDrawerOpen()) { closeTodoDrawer(); return true; }
    if (isConvDrawerOpen()) { closeConvDrawer(); return true; }
    for (const id of ['addMcpModal', 'addSkillModal', 'convPickerModal', 'addWorkspaceModal',
      'subscriptionModal', 'discoverModal', 'platformPickerModal', 'settingsModal', 'newChatModal']) {
      const el = document.getElementById(id);
      if (el && !el.hidden) { el.hidden = true; return true; }
    }
    if (state.ws.open) { closeDrawer(); return true; }
    if ($('#app').classList.contains('sidebar-open')) {
      $('#app').classList.remove('sidebar-open');
      $('#mobileScrim').hidden = true;
      return true;
    }
    return false;
  }

  /* ── events ──────────────────────────────────────────────────────────── */

  function bindEvents() {
    $('#newChatBtn').addEventListener('click', () => openNewChatModal());
    wireNewChatModal();
    const mentionMenu = $('#mentionMenu');
    if (mentionMenu) {
      // mousedown 而不是 click：避免 textarea 先失焦导致菜单被收起
      mentionMenu.addEventListener('mousedown', (e) => {
        const btn = e.target.closest('[data-mention-index]');
        if (!btn) return;
        e.preventDefault();
        chooseMention(Number(btn.dataset.mentionIndex) || 0);
      });
    }
    $('#chatTitle').addEventListener('click', () => openRenameModal());
    const renameSave = $('#renameSave');
    if (renameSave) renameSave.addEventListener('click', () => saveConversationTitle());
    const renameRegenerate = $('#renameRegenerate');
    if (renameRegenerate) {
      renameRegenerate.addEventListener('click', () => regenerateConversationTitle());
    }
    const renameInput = $('#renameInput');
    if (renameInput) {
      renameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          saveConversationTitle();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          $('#renameModal').hidden = true;
        }
      });
    }
    const rewindConfirm = $('#rewindConfirm');
    if (rewindConfirm) {
      rewindConfirm.addEventListener('click', () => performRewind());
    }

    $('#searchInput').addEventListener('input', (e) => {
      state.filter = e.target.value || '';
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(() => {
        const chatWs = chatWorkspace();
        if (chatWs && workspaceId() === chatWs.id) renderChatList();
        else refreshConversations();
        if (state.picker.open) loadConvPicker(1);
      }, 220);
    });

    $('#deleteChatBtn').addEventListener('click', () => {
      if (state.currentId) deleteConversation(state.currentId);
    });

    const confirmModal = $('#confirmModal');
    if (confirmModal) {
      confirmModal.addEventListener('click', (e) => {
        if (e.target.closest('[data-confirm-cancel]') || e.target === confirmModal) {
          closeConfirm(false);
          return;
        }
        if (e.target.closest('#confirmOkBtn')) closeConfirm(true);
      });
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && confirmResolver) {
        e.preventDefault();
        closeConfirm(false);
      }
    });

    $('#themeBtn').addEventListener('click', () => {
      const idx = THEMES.findIndex((th) => th.id === state.theme);
      const next = THEMES[((idx < 0 ? 0 : idx) + 1) % THEMES.length].id;
      state.themeSchedule.mode = 'manual';
      saveThemeSchedule();
      applyTheme(next);
      renderThemeScheduleNote();
    });

    $('#langBtn').addEventListener('click', cycleLanguage);
    $('#langSeg').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-lang-mode]');
      if (btn) applyLanguage(btn.dataset.langMode);
    });
    window.addEventListener('languagechange', () => {
      if (state.langMode === 'system') applyLanguage('system', { persist: false });
    });

    $('#sidebarOpen').addEventListener('click', () => {
      $('#app').classList.add('sidebar-open');
      $('#mobileScrim').hidden = false;
    });
    $('#sidebarClose').addEventListener('click', () => {
      $('#app').classList.remove('sidebar-open');
      $('#mobileScrim').hidden = true;
    });
    $('#mobileScrim').addEventListener('click', () => {
      $('#app').classList.remove('sidebar-open');
      $('#mobileScrim').hidden = true;
    });

    bindSendButton();
    $('#stopBtn').addEventListener('click', stopRun);

    const input = $('#promptInput');
    let imeComposing = false;     // composition 进行中
    let imeCommitPending = false; // 刚结束的那次 input 是候选词上屏，不算"继续输入"
    let imeEndedAt = 0;           // 最近一次 compositionend 时间
    let enterConfirmAt = 0;       // 已「预告」过一次 Enter，等待第二次确认
    const IME_CONFIRM_MS = 600;         // IME 结束后的 Enter 只预告、不发送
    const ENTER_CONFIRM_WINDOW = 3000;  // 两次 Enter 之间的最长间隔
    input.addEventListener('input', () => {
      autoGrow(input);
      updateLongTextHint();
      updateSlashMenu();
      updateMentionMenu();
      // 候选词上屏也会触发 input，那种情况不算用户继续输入，不要清掉待确认状态
      if (imeCommitPending) { imeCommitPending = false; return; }
      enterConfirmAt = 0;
    });
    // 中文 / 日文输入法：确认候选词的 Enter 和"发送消息"的 Enter 是同一个键。
    // 单靠 e.isComposing 不够——Safari / WKWebView 上 compositionend 会先于 keydown 触发，
    // 等 keydown 到时 isComposing 已经是 false，于是候选词一确认就把消息发出去了。
    // 这里的做法：
    //   ① composition 进行中 / keyCode 229 → 完全不干预（不 preventDefault、不发送）；
    //   ② IME 刚结束（IME_CONFIRM_MS 内）的 Enter → 只「预告」不发送，再按一次才真正发出；
    //   ③ 其余情况 → 一次 Enter 直达（正常发送手感不变）。
    input.addEventListener('compositionstart', () => {
      imeComposing = true;
      imeCommitPending = false;
    });
    input.addEventListener('compositionend', () => {
      imeComposing = false;
      imeEndedAt = Date.now();
      imeCommitPending = true;
      // 若浏览器这次没有补发 input（少数输入法只发 compositionend），
      // 短暂窗口后自动复位，避免下一次真实输入被误判成候选词上屏。
      setTimeout(() => { imeCommitPending = false; }, 120);
    });
    input.addEventListener('blur', () => {
      imeComposing = false;
      imeCommitPending = false;
      enterConfirmAt = 0;
      setTimeout(() => {
        const active = document.activeElement;
        if (!active || !active.closest || !active.closest('#slashMenu')) closeSlashMenu();
        if (!active || !active.closest || !active.closest('#mentionMenu')) closeMentionMenu();
      }, 120);
    });
    input.addEventListener('keydown', (e) => {
      if (handleMentionKeydown(e)) return;
      if (handleSlashKeydown(e)) return;
      if (e.key !== 'Enter' || e.shiftKey) return;
      const keyCode = e.keyCode || e.which || 0;
      if (imeComposing || e.isComposing || keyCode === 229) return;  // 交给输入法
      const now = Date.now();
      // Cmd/Ctrl+Enter：明确要发，跳过长文本确认与 IME 二次确认
      if (e.metaKey || e.ctrlKey) {
        e.preventDefault();
        enterConfirmAt = 0;
        sendMessage({ force: true });
        return;
      }
      // 第二次 Enter（已预告）→ 真发送
      if (enterConfirmAt && now - enterConfirmAt < ENTER_CONFIRM_WINDOW) {
        e.preventDefault();
        enterConfirmAt = 0;
        sendMessage();
        return;
      }
      enterConfirmAt = 0;
      // IME 刚结束：这次 Enter 也可能还在提交候选词，先只预告、不发也不换行，等第二次
      if (imeEndedAt > 0 && now - imeEndedAt < IME_CONFIRM_MS) {
        e.preventDefault();
        enterConfirmAt = now;
        toast(t('composer.imeConfirm'), 'ok', 1800);
        return;
      }
      e.preventDefault();
      sendMessage();
    });
    input.addEventListener('paste', (e) => {
      const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
      if (!files.length) return;
      e.preventDefault();
      addComposerFiles(files);
    });

    const attachBtn = $('#attachBtn');
    const attachInput = $('#attachInput');
    if (attachBtn && attachInput) {
      attachBtn.addEventListener('click', () => attachInput.click());
      attachInput.addEventListener('change', () => {
        addComposerFiles(attachInput.files);
        attachInput.value = '';
      });
    }
    const composerBox = $('#composerBox');
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
        addComposerFiles(e.dataTransfer.files);
      });
    }
    const attachmentsBox = $('#composerAttachments');
    if (attachmentsBox) {
      attachmentsBox.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-att-remove]');
        if (btn) removeComposerAttachment(btn.dataset.attRemove);
      });
    }
    const refsBox = $('#composerRefs');
    if (refsBox) {
      refsBox.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-ref-remove]');
        if (btn) removeComposerRef(btn.dataset.refRemove);
      });
    }
    const slashMenu = $('#slashMenu');
    if (slashMenu) {
      slashMenu.addEventListener('mousedown', (e) => {
        // 保持输入框焦点：否则 blur 先关掉菜单，click 永远到不了
        if (e.target.closest('[data-slash-index]')) e.preventDefault();
      });
      slashMenu.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-slash-index]');
        if (btn) chooseSlashCommand(Number(btn.dataset.slashIndex));
      });
    }
    const goalModeBtn = $('#goalModeBtn');
    if (goalModeBtn) {
      goalModeBtn.addEventListener('click', () => {
        setMode(state.mode === 'goal' ? 'autonomous' : 'goal');
      });
    }

    $('#chatInner').addEventListener('keydown', (e) => {
      if (!state.editingMsg || !e.target.closest('[data-msg-edit-input]')) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        cancelEditMessage();
      } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        saveEditMessage();
      }
    });
    $('#chatInner').addEventListener('input', (e) => {
      if (state.editingMsg && e.target.matches('[data-msg-edit-input]')) {
        state.editingMsg.text = e.target.value;
      }
    });

    bindIntegrationControls();
    bindExtensionControls();

    $('#modelChip').addEventListener('click', toggleModelMenu);
    const thinkingChipBtn = $('#thinkingChip');
    if (thinkingChipBtn) thinkingChipBtn.addEventListener('click', toggleThinkingMenu);
    $('#thinkingMenu').addEventListener('click', (e) => {
      const pick = e.target.closest('[data-thinking-pick]');
      if (!pick) return;
      setThinkingLevel(pick.dataset.thinkingPick || '');
      closeThinkingMenu();
    });
    $('#addModelBtn').addEventListener('click', () => addModel());
    const platformPickerBtn = $('#platformPickerBtn');
    if (platformPickerBtn) {
      platformPickerBtn.addEventListener('click', openPlatformPicker);
    }
    const platformSearchInput = $('#platformSearchInput');
    if (platformSearchInput) {
      platformSearchInput.addEventListener('input', () => {
        renderPlatformPicker(platformSearchInput.value);
      });
    }
    const platformGroups = $('#platformGroups');
    if (platformGroups) {
      platformGroups.addEventListener('click', (e) => {
        const pick = e.target.closest('[data-platform-pick]');
        if (!pick) return;
        const modal = $('#platformPickerModal');
        if (modal) modal.hidden = true;
        openSubscriptionModal({ presetCode: pick.dataset.platformPick });
      });
    }
    const discoverModelsBtn = $('#discoverModelsBtn');
    if (discoverModelsBtn) {
      discoverModelsBtn.addEventListener('click', () => discoverChannelModels(discoverModelsBtn));
    }
    const subscriptionModalSave = $('#subscriptionModalSave');
    if (subscriptionModalSave) {
      subscriptionModalSave.addEventListener('click', saveSubscriptionModal);
    }
    const subscriptionModal = $('#subscriptionModal');
    if (subscriptionModal) {
      subscriptionModal.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' || e.target.tagName !== 'INPUT') return;
        e.preventDefault();
        saveSubscriptionModal();
      });
      subscriptionModal.addEventListener('click', (e) => {
        const refresh = e.target.closest('[data-channel-quota-refresh]');
        if (!refresh) return;
        const sub = subscriptionModalDraft
          ? subscriptionById(subscriptionModalDraft.id) : null;
        if (sub) loadChannelQuota($('#subscriptionModalQuota'), sub, true);
      });
    }
    const discoverList = $('#discoverList');
    if (discoverList) {
      discoverList.addEventListener('click', (e) => {
        const addAll = e.target.closest('[data-discover-add-all]');
        if (addAll) {
          addAllDiscoveredModels();
          return;
        }
        const add = e.target.closest('[data-discover-add]');
        if (add) addDiscoveredModel(add.dataset.discoverAdd);
      });
      discoverList.addEventListener('input', (e) => {
        const search = e.target.closest('[data-discover-search]');
        if (!search) return;
        state.discoverFilter[state.selectedSubscription] = search.value;
        applyDiscoverFilter();
      });
    }
    const subscriptionFilter = $('#subscriptionFilterInput');
    if (subscriptionFilter) {
      subscriptionFilter.addEventListener('input', () => {
        state.subscriptionFilter = subscriptionFilter.value;
        applySubscriptionFilter();
      });
    }
    const modelFilter = $('#modelFilterInput');
    if (modelFilter) {
      modelFilter.addEventListener('input', () => {
        state.modelFilter = modelFilter.value;
        applyModelFilter();
      });
    }
    const subscriptionList = $('#subscriptionList');
    if (subscriptionList) {
      subscriptionList.addEventListener('click', (e) => {
        const section = e.target.closest('.subscription-card');
        if (!section) return;
        selectSubscription(section.dataset.subscriptionId);
      });
      subscriptionList.addEventListener('contextmenu', (e) => {
        const section = e.target.closest('.subscription-card');
        if (!section) return;
        e.preventDefault();
        e.stopPropagation();
        openSubscriptionMenu(e.clientX, e.clientY, section.dataset.subscriptionId);
      });
    }
    $('#usagePeriodSeg').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-usage-period]');
      if (!btn) return;
      state.usagePeriod = btn.dataset.usagePeriod || '7d';
      $$('#usagePeriodSeg [data-usage-period]').forEach((b) =>
        b.classList.toggle('active', b === btn));
      renderUsageStats();
    });
    $('#usageRefreshBtn').addEventListener('click', () => loadUsageStats(true));
    $('#modelList').addEventListener('click', (e) => {
      const groupAdd = e.target.closest('[data-group-add]');
      if (groupAdd) {
        addModelToSubscription(groupAdd.dataset.groupAdd);
        return;
      }
      const del = e.target.closest('[data-model-delete]');
      if (del) {
        const card = del.closest('.model-card');
        if (card) deleteModel(card.dataset.modelCode);
        return;
      }
      const addThinking = e.target.closest('[data-thinking-add]');
      if (addThinking) {
        const card = addThinking.closest('.model-card');
        const input = card && $('[data-thinking-custom]', card);
        const box = card && $('.thinking-levels', card);
        const value = (input && input.value || '').trim();
        if (!card || !input || !box) return;
        if (!value) { input.focus(); return; }
        const existing = $$('[data-thinking-level]', box)
          .find((el) => el.value.toLowerCase() === value.toLowerCase());
        if (existing) {
          existing.checked = true;
        } else {
          box.insertAdjacentHTML('beforeend', thinkingLevelChipHtml(value, true, true));
        }
        input.value = '';
        input.focus();
        return;
      }
      const removeThinking = e.target.closest('[data-thinking-remove]');
      if (removeThinking) {
        e.preventDefault();
        e.stopPropagation();
        const chip = removeThinking.closest('.thinking-level');
        if (chip) chip.remove();
        return;
      }
      const toggle = e.target.closest('[data-model-toggle]');
      if (toggle) {
        const card = toggle.closest('.model-card');
        const body = card && $('.model-card-body', card);
        if (body) {
          body.hidden = !body.hidden;
          card.classList.toggle('open', !body.hidden);
          toggle.setAttribute('aria-expanded', String(!body.hidden));
          if (body.hidden) state.openModelCards.delete(card.dataset.modelCode);
          else state.openModelCards.add(card.dataset.modelCode);
        }
      }
    });
    $('#modelList').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || !e.target.matches('[data-thinking-custom]')) return;
      e.preventDefault();
      const card = e.target.closest('.model-card');
      const add = card && $('[data-thinking-add]', card);
      if (add) add.click();
    });
    $('#modelList').addEventListener('change', (e) => {
      if (e.target.matches('input[name="activeModel"]')) {
        state.activeModel = e.target.value;
        $$('#modelList .model-card').forEach((card) => {
          const isActive = card.dataset.modelCode === state.activeModel;
          card.classList.toggle('active', isActive);
          const badge = $('[data-model-active-badge]', card);
          if (badge) badge.hidden = !isActive;
        });
        return;
      }
      if (e.target.matches('[data-model-field="protocol"]')) {
        const card = e.target.closest('.model-card');
        const group = e.target.closest('.model-group');
        if (!card || !group) return;
        const protocol = e.target.value;
        const sub = subscriptionById(group.dataset.subscriptionId) || {};
        const subBase = protocol === 'anthropic'
          ? (sub.anthropic_base_url || sub.base_url || '')
          : (sub.base_url || '');
        const baseInput = $('[data-model-field="base_url"]', card);
        if (baseInput && subBase) baseInput.placeholder = subBase;
        const responseBadge = $('.model-protocol', card);
        if (responseBadge) responseBadge.hidden = protocol !== 'openai-response';
        const anthropicBadge = $('.model-anthropic', card);
        if (anthropicBadge) anthropicBadge.hidden = protocol !== 'anthropic';
      }
    });
    $('#modelList').addEventListener('input', (e) => {
      const card = e.target.closest('.model-card');
      if (!card) return;
      const field = e.target.dataset.modelField;
      const profile = state.models.find((m) => m.code === card.dataset.modelCode) || {};
      if (field === 'name') {
        const label = $('.model-card-name', card);
        if (!label) return;
        const modelInput = $('[data-model-field="model"]', card);
        const model = (modelInput && modelInput.value.trim()) || profile.effective_model || profile.model;
        const fallback = model || t('settings.newModel');
        label.textContent = e.target.value.trim() || fallback;
      }
      if (field === 'model') {
        const label = $('.model-card-name', card);
        const nameInput = $('[data-model-field="name"]', card);
        if (label && !(nameInput && nameInput.value.trim())) {
          label.textContent = e.target.value.trim() || t('settings.newModel');
        }
      }
    });
    $('#modelMenu').addEventListener('click', (e) => {
      const subPick = e.target.closest('[data-model-sub-pick]');
      if (subPick) {
        state.modelMenuLevel = 'models';
        state.modelMenuSubscription = subPick.dataset.modelSubPick;
        state.modelMenuQuery = '';
        renderModelMenu();
        const search = $('#modelMenu [data-model-menu-search]');
        if (search) setTimeout(() => search.focus(), 20);
        return;
      }
      const back = e.target.closest('[data-model-menu-back]');
      if (back) {
        state.modelMenuLevel = 'subscriptions';
        state.modelMenuQuery = '';
        renderModelMenu();
        const search = $('#modelMenu [data-model-menu-search]');
        if (search) setTimeout(() => search.focus(), 20);
        return;
      }
      const pick = e.target.closest('[data-model-pick]');
      if (pick) {
        activateModel(pick.dataset.modelPick);
        return;
      }
      if (e.target.closest('[data-model-manage]')) {
        closeModelMenu();
        openSettings('models');
      }
    });
    $('#modelMenu').addEventListener('input', (e) => {
      if (!e.target.matches('[data-model-menu-search]')) return;
      state.modelMenuQuery = e.target.value;
      applyModelMenuFilter();
    });
    $('#modelMenu').addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      if (state.modelMenuLevel === 'models') {
        state.modelMenuLevel = 'subscriptions';
        state.modelMenuQuery = '';
        renderModelMenu();
      } else {
        closeModelMenu();
      }
    });

    $('#saveSettingsBtn').addEventListener('click', saveSettings);

    if ($('#codegraphRefreshBtn')) {
      $('#codegraphRefreshBtn').addEventListener('click', refreshCodegraph);
    }
    if ($('#diagExportBtn')) {
      $('#diagExportBtn').addEventListener('click', exportDiagnostics);
    }
    if ($('#diagBackupBtn')) {
      $('#diagBackupBtn').addEventListener('click', backupNow);
    }
    const settingsTabs = $('#settingsTabs');
    if (settingsTabs) {
      settingsTabs.addEventListener('click', (e) => {
        const btn = e.target.closest('.settings-tab');
        if (btn) setSettingsTab(btn.dataset.settingsTab);
      });
      settingsTabs.addEventListener('keydown', (e) => {
        if (!['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft'].includes(e.key)) return;
        const dir = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1;
        const i = SETTINGS_TABS.indexOf(state.settingsTab || 'workspaces');
        const next = SETTINGS_TABS[(i + dir + SETTINGS_TABS.length) % SETTINGS_TABS.length];
        setSettingsTab(next);
        const btn = document.getElementById(`settingsTab-${next}`);
        if (btn) btn.focus();
        e.preventDefault();
      });
    }
    window.addEventListener('resize', () => { closeModelMenu(); });

    $('#wsGroups').addEventListener('click', (e) => {
      const more = e.target.closest('[data-ws-more]');
      if (more) { openConvPicker(1, more.dataset.wsMore); return; }
      const newBtn = e.target.closest('[data-ws-new]');
      if (newBtn) {
        const wsId = newBtn.dataset.wsNew;
        if (wsId === workspaceId()) newChat();
        else switchWorkspace(wsId);
        return;
      }
      const toggle = e.target.closest('[data-ws-toggle]');
      if (toggle) { toggleWsGroup(toggle.dataset.wsToggle); return; }
      const header = e.target.closest('[data-ws-switch]');
      if (!header) return;
      const wsId = header.dataset.wsSwitch;
      // 点工作区名称区域也能展开 / 收起：
      // - 当前工作区：直接切换该分组（展开 ↔ 收起）；
      // - 其它工作区：先切过去，并保证目标分组展开（再点一次即可收起）。
      if (wsId === workspaceId()) {
        toggleWsGroup(wsId);
        return;
      }
      if (!wsGroupOpen(wsId)) setWsGroupOpen(wsId, true);
      switchWorkspace(wsId);
    });
    $('#vcsRefresh').addEventListener('click', () => refreshVcs());
    $('#vcsDrawerClose').addEventListener('click', () => closeVcsDrawer());
    $('#memRefresh').addEventListener('click', () => refreshMem());
    $('#memDrawerClose').addEventListener('click', () => closeMemDrawer());
    $('#codegraphClose').addEventListener('click', () => closePanel('codegraph'));
    $('#codegraphInitBtn').addEventListener('click', () => runCodegraphAction('init'));
    $('#codegraphUpdateBtn').addEventListener('click', () => runCodegraphAction('update'));
    $('#codegraphRebuildBtn').addEventListener('click', () => runCodegraphAction('rebuild'));
    $('#codegraphList').addEventListener('click', (e) => {
      const row = e.target.closest('[data-codegraph-path]');
      if (row && row.dataset.codegraphPath) openViewer(row.dataset.codegraphPath);
    });
    $('#panel-codegraph').addEventListener('click', (e) => {
      const viewBtn = e.target.closest('[data-codegraph-view]');
      if (!viewBtn || !state.codegraph) return;
      state.codegraph.view = viewBtn.dataset.codegraphView === 'flat' ? 'flat' : 'tree';
      state.ui.codegraphView = state.codegraph.view;
      saveUi();
      renderCodegraphList();
    });
    let codegraphSearchTimer = 0;
    $('#panel-codegraph').addEventListener('input', (e) => {
      if (!e.target.matches('#codegraphSearchInput') || !state.codegraph) return;
      clearTimeout(codegraphSearchTimer);
      codegraphSearchTimer = setTimeout(() => {
        state.codegraph.q = e.target.value;
        renderCodegraphList();
      }, 180);
    });
    $('#memExportBtn').addEventListener('click', async () => {
      if (!state.mem || !state.mem.ws) return;
      try {
        const data = await api(`/workspaces/${encodeURIComponent(state.mem.ws)}/memories/export`);
        const blob = new Blob([data.markdown || ''], { type: 'text/markdown;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'atlasx-memories.md';
        a.click();
        URL.revokeObjectURL(a.href);
        toast(t('mem.export'), 'ok', 3000);
      } catch (err) {
        toast(t('mem.fail', { msg: err.message }), 'bad', 5000);
      }
    });
    $('#panel-mem').addEventListener('click', async (e) => {
      if (!state.mem) return;
      const kindBtn = e.target.closest('[data-mem-filter]');
      if (kindBtn) {
        state.mem.kind = kindBtn.dataset.memFilter;
        state.mem.page = 0;
        await renderMemList().catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const sortBtn = e.target.closest('[data-mem-sort]');
      if (sortBtn) {
        state.mem.sort = sortBtn.dataset.memSort;
        state.mem.page = 0;
        await renderMemList().catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const archBtn = e.target.closest('[data-mem-archived]');
      if (archBtn) {
        state.mem.includeArchived = !state.mem.includeArchived;
        state.mem.page = 0;
        await renderMemList().catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const pageBtn = e.target.closest('[data-mem-page]');
      if (pageBtn) {
        state.mem.page += pageBtn.dataset.memPage === 'next' ? 1 : -1;
        await renderMemList().catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
    });
    const handleGraphClick = async (e) => {
      if (!state.mem) return;
      const zoomBtn = e.target.closest('[data-zoom]');
      if (zoomBtn) {
        applyGraphZoom(zoomBtn.dataset.zoom);
        return;
      }
      const graphFilter = e.target.closest('[data-mem-graph-filter]');
      if (graphFilter) {
        state.mem.graphFilter = graphFilter.dataset.memGraphFilter;
        await renderSideGraph(false);
        return;
      }
      const nodeBtn = e.target.closest('[data-mem-node]');
      if (nodeBtn) {
        if (state.mem.dragMoved) { state.mem.dragMoved = false; return; }
        const info = $('#memGraphSideInfo');
        if (!info || !state.mem.graph) return;
        const node = state.mem.graph.nodes.find((n) => n.id === nodeBtn.dataset.memNode);
        if (!node) return;
        info.hidden = false;
        info.innerHTML = memGraphInfoHtml(node);
        return;
      }
    };
    $('#memGraphPane').addEventListener('click', (e) => {
      handleGraphClick(e).catch((err) => toast(err.message, 'bad', 5000));
    });
    $('#topTodoBtn').addEventListener('click', () => {
      if (isTodoDrawerOpen()) closeTodoDrawer();
      else openTodoDrawer();
    });
    wireConvDrawer();
    // 队列操作（上移 / 下移 / 立即插入 / 取消都在队列条上）
    $('#queueItems').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-queue-action]');
      if (btn) queueAction(btn.dataset.queueAction, btn.dataset.runId);
    });

    // 目标模式：预算保存 + 结束目标
    $('#goalBudgetInput').addEventListener('change', () => saveGoalBudget());
    $('#goalEndBtn').addEventListener('click', () => endGoal());
    $('#goalDismissBtn').addEventListener('click', () => {
      state.goalDismissed = true;
      state.goal = null;
      renderGoalBar();
    });

    // 任务清单抽屉（入口在每条 AI 输出头像右侧的 chip）
    $('#todoDrawerClose').addEventListener('click', () => closeTodoDrawer());
    $('#todoAddForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const input = $('#todoAddInput');
      const title = input.value.trim();
      if (!title) { input.focus(); return; }
      input.value = '';
      addTodo(title);
    });
    $('#todoList').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-todo-action]');
      if (btn) todoAction(btn.dataset.todoAction, btn.dataset.todoId);
    });
    let rebuildTimer = 0;
    function startRebuildPoll() {
      clearInterval(rebuildTimer);
      rebuildTimer = setInterval(async () => {
        if (!isMemDrawerOpen() || !state.mem || !state.mem.ws) {
          clearInterval(rebuildTimer);
          return;
        }
        try {
          const job = await api(`/workspaces/${encodeURIComponent(state.mem.ws)}/memories/rebuild/status`);
          state.mem.rebuild = job;
          renderMemSummary();
          if (job.status === 'done' || job.status === 'error') {
            clearInterval(rebuildTimer);
            if (job.status === 'done') {
              toast(t('mem.rebuildDone', { n: job.created, calls: job.llm_calls }), 'ok', 5000);
            } else {
              toast(t('mem.rebuildError', { msg: (job.errors || []).join('; ') }), 'bad', 6000);
            }
            await refreshMem();
          }
        } catch (err) {
          clearInterval(rebuildTimer);
        }
      }, 2000);
    }

    $('#memRebuildBtn').addEventListener('click', async () => {
      if (!state.mem || !state.mem.ws) return;
      if (!confirm(t('mem.rebuildConfirm'))) return;
      try {
        const job = await api(`/workspaces/${encodeURIComponent(state.mem.ws)}/memories/rebuild`, { method: 'POST' });
        state.mem.rebuild = job;
        renderMemSummary();
        startRebuildPoll();
      } catch (err) {
        toast(t('mem.fail', { msg: err.message }), 'bad', 5000);
      }
    });
    $('#memTidyBtn').addEventListener('click', async () => {
      if (!state.mem || !state.mem.ws) return;
      try {
        const res = await api(`/workspaces/${encodeURIComponent(state.mem.ws)}/memories/consolidate`, { method: 'POST' });
        toast(t('mem.tidyDone', { merged: res.merged || 0, archived: res.archived || 0 }), 'ok', 5000);
        await refreshMem();
      } catch (err) {
        toast(t('mem.fail', { msg: err.message }), 'bad', 5000);
      }
    });
    $('#memClearBtn').addEventListener('click', async () => {
      if (!state.mem || !state.mem.ws) return;
      if (!confirm(t('mem.clearConfirm'))) return;
      try {
        await api(`/workspaces/${encodeURIComponent(state.mem.ws)}/memories`, { method: 'DELETE' });
        toast(t('mem.cleared'), 'ok', 4000);
        await refreshMem();
      } catch (err) {
        toast(t('mem.fail', { msg: err.message }), 'bad', 5000);
      }
    });
    let memSearchTimer = 0;
    $('#memContent').addEventListener('input', (e) => {
      if (!e.target.matches('#memSearchInput')) return;
      clearTimeout(memSearchTimer);
      memSearchTimer = setTimeout(() => {
        state.mem.q = e.target.value;
        state.mem.page = 0;
        renderMemList().catch((err) => toast(err.message, 'bad', 5000));
      }, 300);
    });
    $('#memContent').addEventListener('click', (e) => {
      const row = e.target.closest('.mem-row');
      if (!row) return;
      const wasExpanded = row.classList.contains('expanded');
      $('#memContent').querySelectorAll('.mem-row.expanded').forEach((r) => r.classList.remove('expanded'));
      if (!wasExpanded) row.classList.add('expanded');
    });
    $('#panel-vcs').addEventListener('click', (e) => {
      const tabBtn = e.target.closest('[data-vcs-tab]');
      if (tabBtn) {
        state.vcs.tab = tabBtn.dataset.vcsTab;
        for (const b of $('#panel-vcs').querySelectorAll('[data-vcs-tab]')) b.setAttribute('aria-pressed', String(b === tabBtn));
        renderVcsTab().catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const wsId = state.vcs && state.vcs.ws;
      if (!wsId) return;
      const remoteAddBtn = e.target.closest('#vcsRemoteAddBtn');
      if (remoteAddBtn) {
        const name = (($('#vcsRemoteName') && $('#vcsRemoteName').value) || '').trim();
        const url = (($('#vcsRemoteUrl') && $('#vcsRemoteUrl').value) || '').trim();
        if (!name || !url) {
          toast(`${t('vcs.remoteName')} / ${t('vcs.remoteUrl')}`, 'bad', 3000);
          return;
        }
        api(`/workspaces/${encodeURIComponent(wsId)}/vcs/remotes`, {
          method: 'POST', body: JSON.stringify({ name, url }),
        })
          .then(() => { toast(t('vcs.remoteAdded', { name }), 'ok', 3000); return refreshVcs(); })
          .catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const remoteEditBtn = e.target.closest('[data-vcs-remote-edit]');
      if (remoteEditBtn) {
        state.vcs.remoteEdit = remoteEditBtn.dataset.vcsRemoteEdit;
        renderVcsTab().catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const remoteCancelBtn = e.target.closest('[data-vcs-remote-cancel]');
      if (remoteCancelBtn) {
        state.vcs.remoteEdit = '';
        renderVcsTab().catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const remoteSaveBtn = e.target.closest('[data-vcs-remote-save]');
      if (remoteSaveBtn) {
        const name = remoteSaveBtn.dataset.vcsRemoteSave;
        const card = remoteSaveBtn.closest('[data-vcs-remote-card]');
        const input = card && card.querySelector('[data-vcs-remote-input]');
        const url = (input && input.value || '').trim();
        if (!url) { toast(t('vcs.remoteUrl'), 'bad', 3000); return; }
        api(`/workspaces/${encodeURIComponent(wsId)}/vcs/remotes/${encodeURIComponent(name)}`, {
          method: 'PUT', body: JSON.stringify({ url }),
        })
          .then(() => {
            state.vcs.remoteEdit = '';
            toast(t('vcs.remoteSaved'), 'ok', 3000);
            return refreshVcs();
          })
          .catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const remoteRemoveBtn = e.target.closest('[data-vcs-remote-remove]');
      if (remoteRemoveBtn) {
        const name = remoteRemoveBtn.dataset.vcsRemoteRemove;
        if (!window.confirm(t('vcs.removeRemoteConfirm', { name }))) return;
        api(`/workspaces/${encodeURIComponent(wsId)}/vcs/remotes/${encodeURIComponent(name)}`, { method: 'DELETE' })
          .then(() => {
            if (state.vcs.remoteEdit === name) state.vcs.remoteEdit = '';
            toast(t('vcs.remoteRemoved', { name }), 'ok', 3000);
            return refreshVcs();
          })
          .catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const fetchBtn = e.target.closest('[data-vcs-fetch]');
      if (fetchBtn) {
        const name = fetchBtn.dataset.vcsFetch;
        const original = fetchBtn.textContent;
        fetchBtn.disabled = true;
        fetchBtn.textContent = t('vcs.fetching');
        api(`/workspaces/${encodeURIComponent(wsId)}/vcs/fetch`, {
          method: 'POST', body: JSON.stringify({ remote: name }),
        })
          .then(() => { toast(t('vcs.fetched', { name }), 'ok', 3500); return refreshVcs(); })
          .catch((err) => toast(err.message, 'bad', 6000))
          .finally(() => { fetchBtn.disabled = false; fetchBtn.textContent = original; });
        return;
      }
      const viewBtn = e.target.closest('[data-vcs-view]');
      if (viewBtn) {
        const view = viewBtn.dataset.vcsView;
        if (!state.vcs) return;
        if (view === 'flat' || view === 'dir') {
          state.vcs.statusView = view;
          setVcsPref(wsId, 'status', view);
        }
        renderVcsTab().catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const diffBtn = e.target.closest('[data-vcs-diff]');
      if (diffBtn) {
        const row = diffBtn.closest('.vcs-file-row, .vcs-log-row');
        const box = row && row.querySelector('.vcs-file-diff');
        if (box) {
          if (!box.hidden) {
            box.hidden = true;
            if (row) row.classList.remove('open');
            return;
          }
          showVcsDiff(box, `?path=${encodeURIComponent(diffBtn.dataset.vcsDiff)}`)
            .then(() => { if (row) row.classList.add('open'); })
            .catch((err) => toast(err.message, 'bad', 5000));
        }
        return;
      }
      const revBtn = e.target.closest('[data-vcs-rev]');
      if (revBtn) {
        const row = revBtn.closest('.vcs-log-row');
        const box = row && row.querySelector('.vcs-file-diff');
        if (box) {
          if (!box.hidden) {
            box.hidden = true;
            if (row) row.classList.remove('open');
            return;
          }
          showVcsDiff(box, `?rev=${encodeURIComponent(revBtn.dataset.vcsRev)}`)
            .then(() => { if (row) row.classList.add('open'); })
            .catch((err) => toast(err.message, 'bad', 5000));
        }
        return;
      }
      const actBtn = e.target.closest('[data-vcs-action]');
      if (actBtn) {
        const action = actBtn.dataset.vcsAction;
        const path = actBtn.dataset.vcsPath;
        api(`/workspaces/${encodeURIComponent(wsId)}/vcs/${action}`, { method: 'POST', body: JSON.stringify({ paths: [path] }) })
          .then(() => { toast(t(action === 'unstage' ? 'vcs.unstaged' : action === 'discard' ? 'vcs.discarded' : 'vcs.staged'), 'ok', 3000); return refreshVcs(); })
          .catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      const branchBtn = e.target.closest('[data-vcs-branch]');
      if (branchBtn) {
        const name = branchBtn.dataset.vcsBranch;
        api(`/workspaces/${encodeURIComponent(wsId)}/vcs/switch`, { method: 'POST', body: JSON.stringify({ branch: name }) })
          .then(() => { toast(t('vcs.switched', { name }), 'ok', 4000); return refreshVcs(); })
          .catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      if (e.target.id === 'vcsCommitBtn') {
        const msg = (($('#vcsCommitMsg') && $('#vcsCommitMsg').value) || '').trim();
        if (!msg) { toast(t('vcs.commitMsgPlaceholder'), 'bad', 3000); return; }
        api(`/workspaces/${encodeURIComponent(wsId)}/vcs/commit`, { method: 'POST', body: JSON.stringify({ message: msg, all: true }) })
          .then(() => { toast(t('vcs.commited'), 'ok', 4000); return refreshVcs(); })
          .catch((err) => toast(err.message, 'bad', 5000));
        return;
      }
      if (e.target.id === 'vcsCreateBranchBtn') {
        const name = (($('#vcsNewBranch') && $('#vcsNewBranch').value) || '').trim();
        if (!name) { toast(t('vcs.newBranchPlaceholder'), 'bad', 3000); return; }
        api(`/workspaces/${encodeURIComponent(wsId)}/vcs/switch`, { method: 'POST', body: JSON.stringify({ branch: name, create: true }) })
          .then(() => { toast(t('vcs.switched', { name }), 'ok', 4000); return refreshVcs(); })
          .catch((err) => toast(err.message, 'bad', 5000));
      }
    });
    $('#panel-vcs').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      if (e.target.id === 'vcsRemoteName' || e.target.id === 'vcsRemoteUrl') {
        e.preventDefault();
        const btn = $('#vcsRemoteAddBtn');
        if (btn) btn.click();
        return;
      }
      if (e.target.matches('[data-vcs-remote-input]')) {
        e.preventDefault();
        const card = e.target.closest('[data-vcs-remote-card]');
        const btn = card && card.querySelector('[data-vcs-remote-save]');
        if (btn) btn.click();
      }
    });
    $('#wsListRefresh').addEventListener('click', () => loadWorkspaceMenuPage(state.wsMenuPage));
    const wsCollapseAllBtn = $('#wsCollapseAllBtn');
    if (wsCollapseAllBtn) wsCollapseAllBtn.addEventListener('click', () => collapseAllWsGroups());
    $('#chatRefresh').addEventListener('click', () => loadChatTop());
    const chatClearBtn = $('#chatClearBtn');
    if (chatClearBtn) chatClearBtn.addEventListener('click', () => deleteAllChatConversations());
    $('#chatAddBtn').addEventListener('click', () => {
      const ws = chatWorkspace();
      if (!ws) return;
      if (workspaceId() !== ws.id) switchWorkspace(ws.id);
      newChat();
    });
    $('#wsAddConvBtn').addEventListener('click', () => {
      const chatWs = chatWorkspace();
      const chatId = chatWs && chatWs.id;
      let target = currentWorkspace();
      if (!target || target.id === chatId || target.archived) {
        target = state.wsMenuItems.find((w) => w.id !== chatId && !w.archived)
          || state.workspaces.find((w) => w.id !== chatId && !w.archived);
      }
      if (!target) {
        toast(t('workspace.none'), 'bad', 3000);
        return;
      }
      if (workspaceId() !== target.id) switchWorkspace(target.id);
      newChat();
    });
    const chatBox = $('#chatBox');
    if (chatBox) {
      // 整个会话面板滚动到底部附近时自动续页，不再显示加载提示文字。
      chatBox.addEventListener('scroll', () => {
        if (chatBox.scrollHeight - chatBox.scrollTop - chatBox.clientHeight < 80) loadChatMore();
      });
    }
    $('#wsFilterInput').addEventListener('input', () => {
      state.wsFilter = $('#wsFilterInput').value.trim();
      loadWorkspaceMenuPage(0);
    });
    $('#wsListAdd').addEventListener('click', () => openAddWorkspace());
    $('#wsGroupsPrev').addEventListener('click', () => loadWorkspaceMenuPage(state.wsMenuPage - 1));
    $('#wsGroupsNext').addEventListener('click', () => loadWorkspaceMenuPage(state.wsMenuPage + 1));
    $('#convPickerPrev').addEventListener('click', () => {
      if (state.picker.page > 1) loadConvPicker(state.picker.page - 1);
    });
    $('#convPickerNext').addEventListener('click', () => {
      const pages = Math.max(1, Math.ceil(state.picker.total / state.picker.pageSize));
      if (state.picker.page < pages) loadConvPicker(state.picker.page + 1);
    });
    $('#convPickerArchived').addEventListener('click', () => {
      state.picker.archived = !state.picker.archived;
      loadConvPicker(1);
    });

    $('#settingsAddWorkspaceBtn').addEventListener('click', () => openAddWorkspace());
    const onWorkspaceAdminClick = (e) => {
      const rename = e.target.closest('[data-ws-rename]');
      if (rename) { workspaceAction(rename.dataset.wsRename, 'rename'); return; }
      const remove = e.target.closest('[data-ws-delete]');
      if (remove) { workspaceAction(remove.dataset.wsDelete, 'delete'); return; }
      const restore = e.target.closest('[data-ws-restore]');
      if (restore) workspaceAction(restore.dataset.wsRestore, 'restore');
    };
    $('#settingsWorkspaceList').addEventListener('click', onWorkspaceAdminClick);
    $('#archivedWorkspaceList').addEventListener('click', onWorkspaceAdminClick);

    $('#folderPickerUp').addEventListener('click', () => {
      if (state.addWs.parent) loadFolderPicker(state.addWs.parent);
    });
    $('#folderPickerGo').addEventListener('click', () => {
      const path = $('#folderPickerPath').value.trim();
      if (path) loadFolderPicker(path);
    });
    const nativePick = $('#folderPickerNative');
    if (nativePick) {
      nativePick.addEventListener('click', async () => {
        const api = window.pywebview && window.pywebview.api;
        if (!api || typeof api.pick_folder !== 'function') return;
        try {
          const path = await api.pick_folder();
          if (path) await loadFolderPicker(path, { fallbackHome: true });
        } catch (err) {
          toast(String((err && err.message) || err), 'bad', 3000);
        }
      });
    }
    window.addEventListener('pywebviewready', () => syncNativePicker());
    $('#folderPickerPath').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const path = e.target.value.trim();
        if (path) loadFolderPicker(path);
      }
    });
    $('#folderPickerList').addEventListener('click', (e) => {
      const row = e.target.closest('[data-folder-path]');
      if (row) loadFolderPicker(row.dataset.folderPath);
    });
    $('#addWorkspaceName').addEventListener('input', (e) => {
      state.addWs.nameTouched = !!e.target.value.trim();
    });
    $('#createWorkspaceBtn').addEventListener('click', createWorkspaceFromFolder);

    $('#wsRefresh').addEventListener('click', () => loadWorkspace(state.ws.path));
    $('#wsUp').addEventListener('click', wsParent);
    $('#wsClose').addEventListener('click', closeDrawer);

    $$('#viewerModeSeg [data-view-mode]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (state.viewer.mode === btn.dataset.viewMode) return;
        state.viewer.mode = btn.dataset.viewMode;
        renderViewer();
      });
    });
    $('#viewerClose').addEventListener('click', closeViewer);
    $('#viewerFilesBtn').addEventListener('click', () => {
      // 从预览切回文件列表面板
      closePanel('viewer');
      openDrawer();
    });
    $('#viewerWrapBtn').addEventListener('click', () => {
      state.viewer.wrap = !state.viewer.wrap;
      state.ui.viewerWrap = state.viewer.wrap;
      saveUi();
      syncAppearanceControls();
      if (state.viewer.editor) state.viewer.editor.setOption('lineWrapping', state.viewer.wrap);
      updateViewerStatus();
    });
    $('#viewerCopyBtn').addEventListener('click', copyViewerContents);

    bindChatScrollWatchers();
    const scrollTopBtn = $('#scrollTopBtn');
    if (scrollTopBtn) {
      scrollTopBtn.addEventListener('click', () => {
        state.stick = false;
        $('#chatScroll').scrollTo({ top: 0, behavior: 'smooth' });
      });
    }
    const scrollBottomBtn = $('#scrollBottomBtn');
    if (scrollBottomBtn) {
      scrollBottomBtn.addEventListener('click', () => {
        const el = $('#chatScroll');
        state.stick = true;
        el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
      });
    }

    // Delegated clicks.
    document.addEventListener('click', (e) => {
      const outlineClose = e.target.closest('[data-outline-close]');
      if (outlineClose) {
        // 点「收起」后压住悬停展开，等指针离开大纲区域再恢复
        outlineHoverSuppressed = true;
        setOutlineOpen(false);
        return;
      }
      const outlineOpenBtn = e.target.closest('[data-outline-open]');
      if (outlineOpenBtn) {
        outlineHoverSuppressed = false;
        setOutlineOpen(true);
        return;
      }
      const outlineJump = e.target.closest('[data-outline-key]');
      if (outlineJump) {
        outlineHoverSuppressed = false;
        setOutlineOpen(true);
        jumpToOutline(outlineJump.dataset.outlineKey);
        return;
      }
      const thinkingMenu = $('#thinkingMenu');
      if (thinkingMenu && !thinkingMenu.hidden && !e.target.closest('#thinkingMenu') && !e.target.closest('#thinkingChip')) {
        closeThinkingMenu();
      }
      const menu = $('#modelMenu');
      const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
      const inMenu = path.includes(menu) || (menu && menu.contains(e.target));
      if (menu && !menu.hidden && !inMenu && !e.target.closest('#modelChip') && !e.target.closest('#thinkingChip')) {
        closeModelMenu();
      }
      const msgAction = e.target.closest('[data-msg-action]');
      if (msgAction) {
        const id = msgAction.dataset.msgId;
        const kind = msgAction.dataset.msgAction;
        if (kind === 'copy') {
          const msg = findMessage(id);
          if (msg) copyText(msg.content);
        } else if (kind === 'edit') {
          startEditMessage(id);
        } else if (kind === 'fork') {
          forkConversationFrom(id);
        } else if (kind === 'rewind') {
          openRewindModal(id);
        } else if (kind === 'recall') {
          recallMessage(id);
        }
        return;
      }
      if (e.target.closest('[data-msg-edit-save]')) {
        saveEditMessage();
        return;
      }
      if (e.target.closest('[data-msg-edit-cancel]')) {
        cancelEditMessage();
        return;
      }

      const pickerConv = e.target.closest('[data-picker-conv]');
      if (pickerConv) {
        $('#convPickerModal').hidden = true;
        state.picker.open = false;
        openConversation(pickerConv.dataset.pickerConv);
        return;
      }

      if (e.target.closest('[data-close="processDetailModal"]')) {
        closeProcessDetail();
        return;
      }

      const closeBtn = e.target.closest('[data-close]');
      if (closeBtn) {
        const el = document.getElementById(closeBtn.dataset.close);
        if (el) el.hidden = true;
        return;
      }
      if (e.target.classList && e.target.classList.contains('modal-backdrop')) {
        // Settings and workspace forms hold unsaved edits: clicking the empty
        // area around them does nothing at all. Only browse modals (preview,
        // conversation picker) close on a backdrop click.
        if (e.target.id === 'processDetailModal') {
          closeProcessDetail();
          return;
        }
        if (e.target.dataset.backdropClose !== 'false') {
          e.target.hidden = true;
        }
        return;
      }

      const action = e.target.closest('[data-action]');
      if (action) {
        if (action.dataset.action === 'workspace') openDrawer();
        if (action.dataset.action === 'settings') openSettings();
        return;
      }

      const suggest = e.target.closest('[data-suggest]');
      if (suggest) {
        const prompt = $('#promptInput');
        prompt.value = suggest.dataset.suggest;
        autoGrow(prompt);
        prompt.focus();
        return;
      }

      const pvToggle = e.target.closest('[data-pv-toggle]');
      if (pvToggle) {
        const pv = pvToggle.closest('.pv');
        if (!pv) return;
        const key = pv.dataset.pvKey;
        const showCode = !state.pvCode.has(key);
        if (showCode) state.pvCode.add(key);
        else state.pvCode.delete(key);
        pv.querySelector('.pv-frame').hidden = showCode;
        pv.querySelector('.pv-code').hidden = !showCode;
        pvToggle.setAttribute('aria-pressed', String(showCode));
        pvToggle.textContent = showCode ? t('preview.showPreview') : t('preview.showCode');
        return;
      }

      const pvOpen = e.target.closest('[data-pv-open]');
      if (pvOpen) {
        window.open('data:text/html;charset=utf-8,' + encodeURIComponent(pvOpen.dataset.pvOpen || ''), '_blank');
        return;
      }


      const todoOpen = e.target.closest('[data-open-todo]');
      if (todoOpen) {
        if (isTodoDrawerOpen()) closeTodoDrawer();
        else openTodoDrawer();
        return;
      }

      const todoCardToggle = e.target.closest('[data-todo-card-toggle]');
      if (todoCardToggle) {
        state.todoCardOpen = !state.todoCardOpen;
        const card = todoCardToggle.closest('[data-todo-card]');
        if (card) {
          card.classList.toggle('is-collapsed', !state.todoCardOpen);
          const list = card.querySelector('.turn-todo-list');
          if (list) list.hidden = !state.todoCardOpen;
          todoCardToggle.setAttribute('aria-expanded', String(state.todoCardOpen));
        }
        return;
      }

      const pvCopy = e.target.closest('[data-pv-copy]');
      if (pvCopy) {
        const pv = pvCopy.closest('.pv');
        const code = pv && pv.querySelector('.pv-code code');
        if (code) {
          navigator.clipboard.writeText(code.textContent || '').then(() => {
            pvCopy.textContent = t('copy.copied');
            setTimeout(() => { pvCopy.textContent = t('copy.copy'); }, 1200);
          }).catch(() => toast(t('copy.failed'), 'bad', 2000));
        }
        return;
      }

      const copy = e.target.closest('[data-copy]');
      if (copy) {
        const pre = copy.closest('pre');
        const code = pre && pre.querySelector('code');
        if (code) {
          navigator.clipboard.writeText(code.textContent || '').then(() => {
            copy.textContent = t('copy.copied');
            setTimeout(() => { copy.textContent = t('copy.copy'); }, 1200);
          }).catch(() => toast(t('copy.failed'), 'bad', 2000));
        }
        return;
      }

      const copyMsg = e.target.closest('[data-copy-message]');
      if (copyMsg) {
        const turn = state.messages.find((m) => m.id === copyMsg.dataset.copyMessage);
        if (turn) {
          navigator.clipboard.writeText(turn.content || '').then(() => toast(t('copy.done'), 'ok', 1500));
        }
        return;
      }

      const proposal = e.target.closest('[data-proposal]');
      if (proposal) {
        decideProposal(proposal.dataset.proposal, proposal.dataset.proposalId, proposal.dataset.turn);
        return;
      }

      const approvalBtn = e.target.closest('[data-approval]');
      if (approvalBtn) {
        decideApproval(approvalBtn.dataset.approvalId, approvalBtn.dataset.approval);
        return;
      }

      const barBtn = e.target.closest('[data-approval-action]');
      if (barBtn) {
        decideApproval($('#approvalBar') ? $('#approvalBar').dataset.approvalId : '',
                       barBtn.dataset.approvalAction);
        return;
      }

      if (e.target.closest('#approvalJump')) {
        jumpToApproval($('#approvalBar') ? $('#approvalBar').dataset.approvalId : '');
        return;
      }

      const del = e.target.closest('[data-conv-del]');
      if (del) {
        e.stopPropagation();
        deleteConversation(del.dataset.convDel);
        return;
      }

      const convBtn = e.target.closest('[data-conv]');
      if (convBtn) {
        const id = convBtn.dataset.conv;
        if (id !== state.currentId) openConversation(id);
        if (window.innerWidth <= 900) {
          $('#app').classList.remove('sidebar-open');
          $('#mobileScrim').hidden = true;
        }
        return;
      }

      const procOpen = e.target.closest('[data-process-open]');
      if (procOpen) {
        openProcessDetail(procOpen.dataset.processOpen);
        return;
      }

      const procStop = e.target.closest('[data-process-stop]');
      if (procStop) {
        e.stopPropagation();
        const id = procStop.dataset.processStop;
        const kind = procStop.dataset.processKind || 'tool';
        procStop.disabled = true;
        stopProcess(id, kind);
        return;
      }

      const runStop = e.target.closest('[data-run-stop]');
      if (runStop) {
        e.stopPropagation();
        const id = runStop.dataset.runStop;
        runStop.disabled = true;
        api(`/runs/${encodeURIComponent(id)}/stop`, { method: 'POST' })
          .then(() => {
            toast(t('stop.requested'), 'ok', 2000);
            // 立即拉一次：停止是同步生效的，列表应马上反映 stopped/消失，
            // 而不是等到 5s 轮询——否则按钮灰着、看着像没反应。
            refreshActiveRuns();
            setTimeout(refreshActiveRuns, 800);
          })
          .catch((err) => {
            runStop.disabled = false;
            toast(err.message, 'bad', 4000);
          });
        return;
      }

      const askOpen = e.target.closest('[data-run-ask]');
      if (askOpen) {
        e.stopPropagation();
        const id = askOpen.dataset.runAsk;
        if (id === state.currentId) loadPendingQuestions(id);
        else openConversation(id);
        if (window.innerWidth <= 900) {
          $('#app').classList.remove('sidebar-open');
          $('#mobileScrim').hidden = true;
        }
        return;
      }

      const approveOpen = e.target.closest('[data-run-approval]');
      if (approveOpen) {
        e.stopPropagation();
        const id = approveOpen.dataset.runApproval;
        if (id === state.currentId) loadPendingApprovals(id);
        else openConversation(id);
        if (window.innerWidth <= 900) {
          $('#app').classList.remove('sidebar-open');
          $('#mobileScrim').hidden = true;
        }
        return;
      }

      const runOpen = e.target.closest('[data-run-conv]');
      if (runOpen) {
        const id = runOpen.dataset.runConv;
        if (id !== state.currentId) openConversation(id);
        if (window.innerWidth <= 900) {
          $('#app').classList.remove('sidebar-open');
          $('#mobileScrim').hidden = true;
        }
        return;
      }

      const foldTitle = e.target.closest('[data-fold-toggle]');
      if (foldTitle) {
        const block = foldTitle.closest('.flow-block.foldable');
        if (block) {
          e.preventDefault();
          const key = block.dataset.foldKey || '';
          const nowCollapsed = block.classList.toggle('is-collapsed');
          if (nowCollapsed) {
            stepOpen.delete(key);
            stepClosed.add(key);
          } else {
            stepClosed.delete(key);
            stepOpen.add(key);
          }
          return;
        }
      }

      const wsRef = e.target.closest('[data-ws-ref]');
      if (wsRef) {
        const path = wsRef.dataset.wsRef;
        addComposerRef(wsRef.dataset.wsRefDir === '1' ? `${path.replace(/\/+$/, '')}/` : path);
        return;
      }

      const wsRow = e.target.closest('[data-ws-path]');
      if (wsRow) {
        if (wsRow.dataset.wsType === 'dir') loadWorkspace(wsRow.dataset.wsPath);
        else openViewer(wsRow.dataset.wsPath);
        return;
      }
    });

    // 工作区文件右键菜单 + 通用右键菜单（消息 / 会话 / 编辑区）
    document.addEventListener('contextmenu', (e) => {
      const row = e.target.closest('[data-ws-path]');
      const list = e.target.closest('#wsList');
      if (!row && !list) {
        handleContextMenu(e);
        return;
      }
      closeUiMenu();
      if (list && !row) {
        // 空白处：新建文件 / 文件夹、刷新
        e.preventDefault();
        openFileMenu(e.clientX, e.clientY, { path: '', name: state.ws.path || '/', type: 'dir' });
        return;
      }
      if (!row) return;
      e.preventDefault();
      const path = row.dataset.wsPath;
      const type = row.dataset.wsType === 'dir' ? 'dir' : 'file';
      const name = row.querySelector('.ws-name') ? row.querySelector('.ws-name').textContent : path;
      openFileMenu(e.clientX, e.clientY, { path, name, type });
    });
    document.addEventListener('pointerdown', (e) => {
      const menu = $('#fileMenu');
      if (menu && !menu.hidden && !e.target.closest('#fileMenu')) closeFileMenu();
      const uiMenu = $('#uiMenu');
      if (uiMenu && !uiMenu.hidden && !e.target.closest('#uiMenu')) closeUiMenu();
    }, true);
    // 菜单只跟随「用户滚动」关闭：流式输出每帧自动贴底也会触发 scroll，
    // 若一律关闭，右键菜单在输出期间根本站不住（用户反馈「正在输出中
    // 订阅右键会频闪」）。程序化贴底的时间点记在 lastAutoScrollAt 里。
    document.addEventListener('scroll', (e) => {
      const el = e.target;
      const auto = el && el.id === 'chatScroll'
        && Date.now() - lastAutoScrollAt < 300;
      if (auto) return;
      closeFileMenu();
      closeUiMenu();
    }, true);

    // <details> state is not preserved across re-renders; remember user choices.
    document.addEventListener('toggle', (e) => {
      const el = e.target;
      if (!(el instanceof HTMLDetailsElement)) return;
      if (el.dataset.detail) state.openDetails.set(el.dataset.detail, el.open);
    }, true);

    // Keyboard shortcuts.
    document.addEventListener('keydown', (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        newChat();
        return;
      }
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        $('#searchInput').focus();
        $('#searchInput').select();
        return;
      }
      if (e.key === 'Escape') {
        if (closeTopLayer()) e.preventDefault();
        closeUiMenu();
      }
    });

    // Back/forward navigation.
    window.addEventListener('popstate', routeFromHash);

    let outlineResizeTimer = null;
    window.addEventListener('resize', () => {
      applyDockWidth();
      if (outlineResizeTimer) clearTimeout(outlineResizeTimer);
      outlineResizeTimer = setTimeout(() => {
        outlineResizeTimer = null;
        renderOutline();
      }, 140);
    });
  }

  function routeFromHash() {
    const match = location.hash.match(/^#\/c\/(.+)$/);
    if (match) {
      const id = decodeURIComponent(match[1]);
      if (id !== state.currentId) openConversation(id);
      return;
    }
    if (!state.currentId && state.view !== 'empty') return;
    newChat({ updateHash: false });
  }

  /* ── boot ────────────────────────────────────────────────────────────── */

  const bootStartedAt = performance.now();
  let bootSplashHidden = false;
  // boot 完成（右栏已按上次快照恢复过）之后，换工作区才需要就地重载面板。
  let bootFinished = false;

  function hideBootSplash() {
    if (bootSplashHidden) return;
    bootSplashHidden = true;
    const el = $('#bootSplash');
    if (!el) return;
    const elapsed = performance.now() - bootStartedAt;
    // 最短露一下（550ms），最长 1.6s 必须开始消散；800ms 淡出后移除，
    // 整体进入等待不超过约 2.4s。
    const wait = Math.max(0, Math.min(550, 1600 - elapsed));
    setTimeout(() => {
      el.classList.add('is-hiding');
      setTimeout(() => el.remove(), 800);
    }, wait);
  }

  // 兜底：首次接口失败 / 卡住时也不能超过 2.5s 还挡着界面。
  setTimeout(hideBootSplash, 1600);

  async function boot() {
    await loadUiPrefs();
    // 右侧标签页 / 收起状态一律沿用上次保存的结果：这里不再清空 openTabs、
    // 也不再强制 dockCollapsed = true。右侧面板和左侧一样，只有用户点标签 /
    // 收缩按钮才改变，启动期没有任何自动收起。
    if (!Array.isArray(state.ui.openTabs)) state.ui.openTabs = [];
    // 先留一份快照：boot 里 applyWorkspace 可能触发 resetWorkspaceDock()
    // （它绑定的是「当前工作区」），不能把那一步当成用户收起面板。
    const bootTabs = state.ui.openTabs.slice();
    const bootActive = state.ui.active;

    applyTheme(scheduledTheme());
    applyLanguage(state.langMode, { persist: false });
    applyUi();
    bindEvents();
    bindDockControls();
    bindOutlineHover();
    startThemeSchedule();

    if (!location.hash) history.replaceState(null, '', '#/new');

    try {
      const [info, settings, workspaces] = await Promise.all([
        api('/app/info'),
        api('/app/settings').catch(() => null),
        api('/workspaces?include_archived=true').catch(() => null),
      ]);
      state.app = info;
      state.settings = settings || (info && info.llm);
      state.workspacesAll = (workspaces && workspaces.items) || [];
      state.workspaces = state.workspacesAll.filter((w) => !w.archived);
      const saved = localStorage.getItem('az-workspace');
      const initial = state.workspaces.find((w) => w.id === saved)
        || state.workspaces.find((w) => w.is_default)
        || state.workspaces[0]
        || (info && info.workspace);
      if (initial) applyWorkspace(initial);
      else renderHeader();
      await loadWorkspaceMenuPage(0);
      await loadChatTop();
    } catch (err) {
      toast(t('error.unreachable', { msg: err.message }), 'bad', 8000);
    }

    await refreshConversations();
    // 按上次保存的标签页恢复右侧面板内容（viewer 不恢复：文件路径没持久化，
    // 恢复出来只会是空壳）。这一步只是「还原上次的样子」，不是自动收起。
    state.ui.openTabs = bootTabs.filter((name) => DOCK_PANELS.includes(name) && name !== 'viewer');
    state.ui.active = state.ui.openTabs.includes(bootActive)
      ? bootActive
      : (state.ui.openTabs[state.ui.openTabs.length - 1] || null);
    syncDock();
    if (state.ui.active) {
      try {
        await restoreWorkspaceDock(state.ui.active);
      } catch (err) {
        console.warn('restore dock panel failed', err);
      }
    }
    loadPermMode();
    loadCommands();
    if (!state.currentId) newChat({ updateHash: false });
    routeFromHash();
    // 任务清单面板上次是开着的就照旧打开（入口在顶栏，随时可关）
    if (state.ui.todoOpen) openTodoDrawer();
    bootFinished = true;
    hideBootSplash();

    pollRuntime();
    setInterval(pollRuntime, 20_000);
    refreshActiveRuns();
    setInterval(refreshActiveRuns, 5_000);
    // 进程面板：运行中 2s 刷新（要看到耗时在走），空闲 5s（有进程时再加密）。
    refreshProcesses();
    setInterval(() => {
      const running = (state.processes || []).some((x) => x.status === 'running');
      if (running || state.running) refreshProcesses();
      else if (!(state.processes || []).length) refreshProcesses();
    }, 2_500);
    setInterval(() => {
      loadChatTop();
      loadWorkspaceMenuPage(state.wsMenuPage);
      refreshConversations();
    }, 15_000);

    if (!state.app || !state.app.llm || !state.app.llm.api_key) {
      // Not an error: keys may live in config.yaml. Only nudge when nothing is configured.
      if (state.app && state.app.llm && !state.app.llm.configured) {
        toast(t('settings.notConfigured'), 'bad', 8000);
      }
    }

    // V3 设置页内嵌：/classic?settings=1&embed=1 只显示 V2 的设置弹窗。
    const params = new URLSearchParams(location.search);
    if (params.get('settings')) {
      if (params.get('embed')) document.body.classList.add('settings-embed');
      openSettings(params.get('tab') || 'models');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();

/**
 * V3 新壳静态回归：四个独立空间 + 悬浮窗 + 角色/群组 API 接线。
 * 运行：node tests/js/v3_shell_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/v3/index.html'), 'utf8');
const JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/v3/app.css'), 'utf8');

let failed = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else { failed += 1; console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`); }
}

check('四个空间 + 关于', ['conv', 'ws', 'role', 'group', 'about'].every((s) => HTML.includes(`data-section="${s}"`)));
check('没有旧顶栏面板按钮', !HTML.includes('topVcsBtn') && !HTML.includes('topTodoBtn'));
check('会话嵌入式（chat shell + 列表切换）', HTML.includes('id="chatShellV3"') && CSS.includes('.chat-shell'));
check('多会话切换（列表 / 快捷键）', JS.includes('function switchTo(') && JS.includes('data-conv=')
  && JS.includes("/^[1-9]$/") && JS.includes("e.key === 'ArrowLeft'"));
check('后台会话未读提示', JS.includes('w.unread = true') && JS.includes('markListDirty') && CSS.includes('.dot'));
check('会话列表行点击切换', JS.includes("el.addEventListener('click', () => { closeMobileList(); openConversation(el.dataset.conv); })"));
check('会话顶栏参考 V2：信息 / 更多 / 删除 / 关闭', !HTML.includes('chatTabStrip')
  && JS.includes('data-conv-info') && JS.includes('data-conv-more') && JS.includes('data-conv-delete')
  && JS.includes('function openConversationInfo(') && JS.includes('function conversationMenuItems('));
check('运行状态 chip', JS.includes('data-run-state') && JS.includes('function setRunState(') && CSS.includes('.run-state'));
check('打开会话回写已读', JS.includes('/read`'));
check('权限改成分段按钮', JS.includes('seg-btn') && JS.includes('data-perm="auto"'));
check('旧悬浮窗样式已移除', !CSS.includes('.winlayer') && !CSS.includes('.dock{') && !JS.includes('function tileWindows('));
check('工作区新增 = 目录选择器', JS.includes('/workspaces/browse?path=') && JS.includes('function pickFolderModal(')
  && JS.includes('data-folder="') && JS.includes('data-pick-up'));
check('目录选择器支持原生选择', JS.includes('pick_folder') && JS.includes('data-pick-native'));
check('工作区不再手填路径', !JS.includes('本地文件夹绝对路径') && !JS.includes('wsAddPath'));
check('多会话状态按会话保存', JS.includes('windows: new Map()') && JS.includes('localStorage.setItem'));

check('会话接口', JS.includes("api('/conversations?limit=80')"));
check('角色库接口', JS.includes("api('/roles')") && JS.includes("api('/roles'"));
check('群组接口', JS.includes("api('/teams')"));
check('工作区接口', JS.includes("api('/workspaces?include_archived=true')"));
check('会话级工作区绑定', JS.includes('}/workspace') && JS.includes('bound_workspace_id'));
check('发送 + SSE 流', JS.includes('/messages/run') && JS.includes('new EventSource('));
check('@/工具行 / 任务卡渲染', JS.includes('function toolNode(') && JS.includes('function renderTasks('));
check('角色创建 / 编辑', JS.includes('openRoleEditor') && JS.includes("api('/roles', { method: 'POST'"));
check('群组创建 / 编辑', JS.includes("api('/teams', { method: 'POST'") && JS.includes("method: 'PATCH'"));
check('工作区文件浏览', JS.includes('/workspace/list?workspace_id=') && JS.includes('/workspace/file?workspace_id='));
check('只读预览有渲染', JS.includes('S.wsPreview') && JS.includes('data-ws-item'));
check('Markdown 渲染', JS.includes('function md(') && JS.includes('<pre class="md-pre">'));
check('@ 成员补全', JS.includes('function updateMention(') && JS.includes('function mentionKey('));
check('会话内临时角色', JS.includes('function createTempRole(') && JS.includes("scope: 'temporary'"));
check('会话标题按群名/角色名', JS.includes('function convTitle(') && JS.includes('function convMeta('));
check('审批卡 + 决策', JS.includes('function approvalCard(') && JS.includes('/approvals/'));
check('ask_user 卡 + 回答', JS.includes('function askCard(') && JS.includes('/questions/'));
check('停止运行', JS.includes('function stopRun(') && JS.includes('/stop`'));
check('模型选择器', JS.includes('function modelOptions(') && JS.includes('/app/models'));
check('权限档位', JS.includes('data-perm') && JS.includes("w.perm === 'auto'"));
check('角色更新事件', JS.includes('evt.role_update') && JS.includes('role_update'));
check('队列条 + 排序 / 取消', JS.includes('function pollQueue(') && JS.includes('/queue/'));
check('斜杠命令', JS.includes('function updateSlash(') && JS.includes('/app/commands'));
check('附件上传 / 展示', JS.includes('function addAttachments(') && JS.includes('attachmentsHtml(m.attachments)'));
check('思考档位', JS.includes('function thinkingOptions(') && JS.includes('data-think'));
check('深度思考流式显示并自动折叠', JS.includes('evt.thinking') && JS.includes('function collapseLiveThinking(')
  && CSS.includes('.turn-fold') && CSS.includes('.think-step'));
check('历史过程折叠（思考 / 工具 / 最终正文）', JS.includes('function traceEntries(')
  && JS.includes('function processFoldHtml(') && JS.includes('reasoning'));
check('自动贴底 / 回到底部', JS.includes('function nearBottom(') && JS.includes('function scrollBottom(')
  && JS.includes('data-scroll-bottom') && CSS.includes('.scroll-down'));
check('流式时内部思考 / 过程也自动滚到底', JS.includes('function scrollInner(') && JS.includes('scrollInner($(\'.turn-fold-body\', fold))'));
check('正文自动滚动的是 .win-body（不是 .msgs）', JS.includes("box.closest('.win-body')")
  && JS.includes("const scroller = $('.win-body', w.el) || box"));
check('全局 Agent 悬浮入口 + 多角标', HTML.includes('id="gaFab"') && HTML.includes('id="gaPanel"')
  && JS.includes('function loadAttention(') && JS.includes("api('/app/attention") && CSS.includes('.ga-badge'));
check('全局 Agent 是真实会话（zone + 独立工具）', JS.includes("GLOBAL_AGENT_ZONE = 'global-agent'")
  && JS.includes('function openGlobalAgent(') && JS.includes('zone: GLOBAL_AGENT_ZONE')
  && JS.includes('evt.app_update') && JS.includes('全局 Agent'));
check('全局 Agent 悬浮窗（可拖动 / 可关闭）', CSS.includes('.ga-win') && JS.includes('opts.floating')
  && JS.includes('function setupGlobalDrag(') && CSS.includes('body.ga-open') && JS.includes('saveGlobalWindowPos'));
check('全局 Agent 初始位置在右下角', /\.ga-win\{[^}]*top:auto[^}]*left:auto[^}]*right:22px[^}]*bottom:20px/.test(CSS));
check('全局 Agent 授权 / 通知 / 代处理', JS.includes('function renderGaPanel(') && JS.includes('autoHandleLowRisk')
  && JS.includes('Notification.requestPermission') && JS.includes('data-ga-perm'));
check('单聊不按 team_id 误判成群聊', JS.includes('function convIsGroup(') && JS.includes('function convParts(')
  && JS.includes('if (isGroup && team)'));
check('模型设置内嵌 V2 原版', JS.includes('/classic?settings=1&embed=1'), '没有复用 V2 设置页');
check('模型 / 订阅两栏', JS.includes('function subCardHtml(') && JS.includes('function saveModelDraft('));
check('拉取模型', JS.includes("api('/app/models/discover'"));
check('设置不含工作区', !JS.includes("S.settingsTab === 'spaces'") && !JS.includes('data-add-ws'));
check('角色点击直接单聊', JS.includes("openRoleChat(el.dataset.role)"));
check('角色右键菜单', JS.includes('function roleMenu(') && JS.includes("label: '复制角色'"));
check('会话 / 群组 / 工作区 / 窗口右键', JS.includes('function convMenu(') && JS.includes('function groupMenu(') && JS.includes('function workspaceMenu(') && JS.includes('function windowMenu('));
check('消息右键', JS.includes("label: '复制为引用'"));
check('工作区新增 / 重命名 / 删除', JS.includes('function addWorkspace(') && JS.includes('function renameWorkspace(') && JS.includes('function deleteWorkspace('));
check('关于是弹框', JS.includes('function openAboutModal(') && JS.includes("if (name === 'about')"));
check('窄屏适配', CSS.includes('@media (max-width:1100px)') && CSS.includes('@media (max-width:760px)'));

if (failed) { console.error(`\n[v3-shell] ${failed} 项失败`); process.exit(1); }
console.log('\n[v3-shell] 全部通过');

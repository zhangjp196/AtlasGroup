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

check('五个空间 + 关于', ['conv', 'work', 'ws', 'role', 'group', 'about'].every((s) => HTML.includes(`data-section="${s}"`)));
check('工单入口与角标', HTML.includes('id="workBadge"') && JS.includes('function loadWorkItems(')
  && JS.includes('function dispatchWork(') && CSS.includes('.work-detail'));
check('没有旧顶栏面板按钮', !HTML.includes('topVcsBtn') && !HTML.includes('topTodoBtn'));
check('会话嵌入式（chat shell + 列表切换）', HTML.includes('id="chatShellV3"') && CSS.includes('.chat-shell'));
check('多会话切换（列表 / 快捷键）', JS.includes('function switchTo(') && JS.includes('data-conv-card=')
  && JS.includes("/^[1-9]$/") && JS.includes("e.key === 'ArrowLeft'"));
check('后台会话未读提示', JS.includes('w.unread = true') && JS.includes('markListDirty') && CSS.includes('.dot'));
check('会话卡片点击进入会话', JS.includes("el.addEventListener('click', () => openConversation(el.dataset.convCard))"));
check('会话聊天面板是右侧抽屉', CSS.includes('.chat-shell{position:fixed') && CSS.includes('chatDrawerIn') && JS.includes('function showChat('));
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
check('群聊设置是 QQ 式抽屉', HTML.includes('id="sideDrawer"')
  && JS.includes('function renderGroupDrawer(') && JS.includes('data-gm-add')
  && JS.includes('data-gm-remove') && JS.includes('data-gm-leader')
  && CSS.includes('.side-drawer') && CSS.includes('.group-member'));
check('会话信息不再弹框，直接进群设置', JS.includes('function openConversationInfo(w)')
  && JS.includes('if (parts.isGroup && parts.team) return openGroupDetail(parts.team.id)')
  && !JS.includes("title: '会话信息'"));
check('角色资料也是抽屉', JS.includes('function renderRoleDrawer(')
  && JS.includes('data-role-chat') && JS.includes('data-role-edit')
  && JS.includes('function openRoleDetail(') && !JS.includes('title: role.name'));
check('工作区文件浏览', JS.includes('/workspace/list?workspace_id=') && JS.includes('/workspace/file?workspace_id='));
check('只读预览有渲染', JS.includes('S.wsPreview') && JS.includes('data-ws-item'));
check('Markdown 渲染', JS.includes('function md(') && JS.includes('class="md-pre"'));
check('@ 成员补全', JS.includes('function updateMention(') && JS.includes('function mentionKey('));
check('群 @ 成员输入触发 + 后台成员窗口', JS.includes('function updateMention(')
  && JS.includes('@ 成员补全') && JS.includes('输入 @ 选择群成员')
  && !JS.includes('data-mention-pick') && HTML.includes('teamTaskFloat')
  && JS.includes('function refreshTeamTasks(') && CSS.includes('.team-task-float'));
check('/clear 清空上下文', JS.includes("name: 'clear'") && JS.includes('/clear`')
  && JS.includes('/conversations/${encodeURIComponent(w.convId)}/clear'));
check('/clear 分割线与说明', JS.includes("m.kind === 'clear_context'")
  && JS.includes('上下文已清空') && CSS.includes('.ctx-clear-line'));
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
check('快捷命令：新会话 / 压缩 / 事项', JS.includes("name: 'new'") && JS.includes("name: 'compact'")
  && JS.includes("name: 'work'") && JS.includes('/compact`'));
check('内置计划命令', fs.readFileSync(path.join(ROOT, 'services/agent_zero/services/command_service.py'), 'utf8')
  .includes('"plan":'));
check('附件上传 / 展示', JS.includes('function addAttachments(') && JS.includes('attachmentsHtml(m.attachments)'));
check('思考档位', JS.includes('function thinkingOptions(') && JS.includes('data-think'));
check('深度思考流式显示并自动折叠', JS.includes('evt.thinking') && JS.includes('function collapseLiveThinking(')
  && JS.includes('function liveAppendThinking(') && CSS.includes('.turn-fold') && CSS.includes('.think-step'));
check('流式思考 / 正文按发生顺序追加', JS.includes('function liveAppendText(')
  && JS.includes('live.flow.appendChild') && CSS.includes('.turn-flow'));
check('历史时间线按发生顺序（思考 / 工具 / 正文）', JS.includes('function traceEntries(')
  && JS.includes('function markAssistantFinal(') && JS.includes('function splitAssistantEntries(')
  && JS.includes('function normalizeTrace(') && JS.includes('reasoning')
  && JS.includes('entries.map((step) => procStepHtml(step))'));
check('完成 / 失败时过程折叠，最终回答留在外面', JS.includes('function processFoldHtml(')
  && JS.includes('processFoldHtml(m, process, !final)')
  && JS.includes('final ? procStepHtml(final) : \'\''));
check('自动贴底 / 回到底部', JS.includes('function nearBottom(') && JS.includes('function scrollBottom(')
  && JS.includes('data-scroll-bottom') && CSS.includes('.scroll-down'));
check('流式时内部思考 / 过程也自动滚到底', JS.includes('function scrollInner(')
  && JS.includes('function liveAppendThinking(') && JS.includes('scrollInner(body)'));
check('正文自动滚动的是 .win-body（不是 .msgs）', JS.includes("box.closest('.win-body')")
  && JS.includes("const scroller = $('.win-body', w.el) || box"));
check('工单悬浮入口 + 多角标', HTML.includes('id="gaFab"') && HTML.includes('id="gaPanel"')
  && JS.includes('function loadAttention(') && JS.includes("api('/app/attention") && CSS.includes('.ga-badge'));
check('工单助手是真实会话（zone + 独立工具）', JS.includes("GLOBAL_AGENT_ZONE = 'global-agent'")
  && JS.includes('function openGlobalAgent(') && JS.includes('zone: GLOBAL_AGENT_ZONE')
  && JS.includes('evt.app_update') && JS.includes('工单'));
check('工单助手悬浮窗（可拖动 / 可关闭）', CSS.includes('.ga-win') && JS.includes('opts.floating')
  && JS.includes('function setupGlobalDrag(') && CSS.includes('body.ga-open') && JS.includes('saveGlobalWindowPos'));
check('工单助手初始位置在右下角', /\.ga-win\{[^}]*top:auto[^}]*left:auto[^}]*right:22px[^}]*bottom:20px/.test(CSS));
check('工单助手授权 / 通知 / 代处理', JS.includes('function renderGaPanel(') && JS.includes('autoHandleLowRisk')
  && JS.includes('Notification.requestPermission') && JS.includes('data-ga-perm'));
check('单聊不按 team_id 误判成群聊', JS.includes('function convIsGroup(') && JS.includes('function convParts(')
  && JS.includes('if (isGroup && team)'));
check('模型设置内嵌 V2 原版', JS.includes('/classic?settings=1&embed=1'), '没有复用 V2 设置页');
check('模型 / 订阅两栏', JS.includes('function subCardHtml(') && JS.includes('function saveModelDraft('));
check('拉取模型', JS.includes("api('/app/models/discover'"));
check('设置不含工作区', !JS.includes("S.settingsTab === 'spaces'") && !JS.includes('data-add-ws'));
check('角色 / 群组列表点击不再自动跳会话', JS.includes("openRoleDetail(el.dataset.role)")
  && JS.includes("openGroupDetail(el.dataset.group)") && !JS.includes("openRoleChat(el.dataset.role)")
  && !JS.includes("openGroupChat(el.dataset.group)"));
check('角色可固定模型或使用默认', JS.includes("model_code: role.model_code || undefined")
  && JS.includes('未指定（使用默认）'));
check('角色右键菜单', JS.includes('function roleMenu(') && JS.includes("label: '复制角色'"));
check('组织与角色：多级部门 + 名称/岗位分离', JS.includes("api('/departments')")
  && JS.includes('function openDeptEditor(') && JS.includes('function departmentMenu(')
  && JS.includes('data-dept-select') && JS.includes("title: $('#rTitle')")
  && CSS.includes('.org-tree') && CSS.includes('.dept-row'));
check('组织 / 群组 / 工作区平铺（无中间列表）', JS.includes('FLAT_SECTIONS')
  && JS.includes('function applySectionLayout(') && CSS.includes('.app.flat')
  && CSS.includes('.app.flat .listpane'));
check('会话 / 工单 / 工作区都是卡片 + 抽屉', JS.includes('data-conv-card')
  && JS.includes('data-work-card') && JS.includes('data-ws-card')
  && JS.includes('function renderWorkDrawer(') && JS.includes('function renderWorkspaceDrawer(')
  && CSS.includes('.tile-card') && CSS.includes('.ws-browser'));
check('工单助手重命名', JS.includes("work: '工单'") && HTML.includes('title="工单"')
  && !JS.includes('Superagent'));
check('角色专属技能（最多 20 / 合并）', JS.includes('专属技能')
  && JS.includes('data-role-skill-new') && JS.includes('function mergeRoleSkills(')
  && JS.includes("/app/skills/merge") && JS.includes('20'));
check('部门 / 角色 / 群组头像上传', JS.includes('function uploadAvatar(')
  && JS.includes('${API}/avatars') && JS.includes('function avatarFieldHtml(')
  && JS.includes("avatarFieldHtml('rAvatar', 'role'")
  && JS.includes("avatarFieldHtml('dAvatar', 'department'")
  && JS.includes("avatarFieldHtml('ngAvatar', 'team'")
  && CSS.includes('.avatar-img') && CSS.includes('.avatar-field'));
check('会话 / 群组 / 工作区 / 窗口右键', JS.includes('function convMenu(') && JS.includes('function groupMenu(') && JS.includes('function workspaceMenu(') && JS.includes('function windowMenu('));
check('消息右键', JS.includes("label: '复制为引用'"));
check('工作区新增 / 重命名 / 删除', JS.includes('function addWorkspace(') && JS.includes('function renameWorkspace(') && JS.includes('function deleteWorkspace('));
check('关于是弹框', JS.includes('function openAboutModal(') && JS.includes("if (name === 'about')"));
check('窄屏适配', CSS.includes('@media (max-width:1100px)') && CSS.includes('@media (max-width:760px)'));

if (failed) { console.error(`\n[v3-shell] ${failed} 项失败`); process.exit(1); }
console.log('\n[v3-shell] 全部通过');

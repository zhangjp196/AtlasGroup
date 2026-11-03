/**
 * 新建会话「单聊 / 群聊」入口回归。
 *
 * 覆盖：弹框骨架、单聊不带 team_id、群聊选已有群或新建群后绑定、
 * 创建后进入会话、Esc 可关闭。
 *
 * 运行：node tests/js/new_chat_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const JS = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

check('弹框骨架存在', HTML.includes('id="newChatModal"')
  && HTML.includes('id="newChatKind"')
  && HTML.includes('data-new-chat-kind="single"')
  && HTML.includes('data-new-chat-kind="group"'));
check('群聊选择 / 新建输入存在', HTML.includes('id="newChatTeam"')
  && HTML.includes('id="newChatNewTeamName"'));
check('创建按钮存在', HTML.includes('id="newChatCreate"'));
check('侧栏 + 打开选择框', JS.includes("$('#newChatBtn').addEventListener('click', () => openNewChatModal())"));
check('选择框事件已绑定', JS.includes('function wireNewChatModal()') && JS.includes('wireNewChatModal();'));
check('单聊不带 team_id', JS.includes('team_id: teamId || undefined'));
check('群聊可新建团队', JS.includes("method: 'POST'") && JS.includes("api('/teams', {"));
check('创建后进入会话并刷新列表',
  JS.includes('await openConversation(conv.id);') && JS.includes('refreshConversations();'));
check('Esc 可关选择框', JS.includes("'settingsModal', 'newChatModal']"));
check('中英文案齐全', (JS.match(/'newChat\.title':/g) || []).length === 2);

if (failures) {
  console.error(`\n[new-chat] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[new-chat] 全部通过');

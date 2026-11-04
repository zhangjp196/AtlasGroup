/**
 * 群组添加成员回归：入口是「添加成员」按钮 + 弹框，且排除已在群里的成员。
 *
 * 运行：node tests/js/group_invite_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

check('群成员头部有「添加成员」按钮', JS.includes('data-gm-add') && JS.includes('＋ 添加成员'));
check('不再使用内嵌邀请列表', !JS.includes('data-gm-invite') && !JS.includes('groupInviteSearch'));
check('点击添加成员打开弹框', JS.includes('addGroupMembersDialog()'));
check('弹框按 id 排除已有成员',
  JS.includes('const usedIds = new Set();') && JS.includes('!usedIds.has(r.id)'));
check('弹框按名字排除已有成员',
  JS.includes('const usedNames = new Set();') && JS.includes('!usedNames.has(roleNameKey(r.name))'));
check('弹框支持搜索角色', JS.includes('gmPickSearch') && JS.includes('data-pick-name'));
check('弹框提交选中角色', JS.includes("[data-pick-role]:checked"));
check('添加时再次检查 id / 名字', JS.includes('(key && roleNameKey(m.name) === key)'));
check('打开群设置时成员按 id / 名字去重并排除主持',
  JS.includes('const seenIds = new Set();') && JS.includes('m.id === leaderId'));
check('保存群设置前对成员去重',
  JS.includes('const members = (groupDraft.members || [])')
  && JS.includes('if (seenIds.has(m.id)'));

if (failures) {
  console.error(`\n[group-invite] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[group-invite] 全部通过');

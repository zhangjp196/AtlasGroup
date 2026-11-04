/**
 * 群组邀请成员回归：已有成员（按 id / 名字）不能出现在邀请列表里。
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

check('邀请列表按 id 排除成员', JS.includes('const used = new Set(all.map((m) => m.id));'));
check('邀请列表按名字排除成员',
  JS.includes('const usedNames = new Set') && JS.includes('!usedNames.has(roleNameKey(r.name))'));
check('打开群设置时成员按 id / 名字去重并排除主持',
  JS.includes('const seenIds = new Set();') && JS.includes('m.id === leaderId'));
check('点邀请时再次检查 id / 名字',
  JS.includes('(key && roleNameKey(m.name) === key)')
  && JS.includes('(key && roleNameKey(leader.name) === key)'));
check('保存群设置前对成员去重',
  JS.includes('const members = (groupDraft.members || [])')
  && JS.includes('if (seenIds.has(m.id)'));

if (failures) {
  console.error(`\n[group-invite] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[group-invite] 全部通过');

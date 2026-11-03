/**
 * 订阅弹框（新建 / 编辑 / 查看共用）表单项布局回归（无需浏览器）。
 *
 * 约定：
 *   - 每个字段独占一行：订阅名称 → OpenAI Base URL → Anthropic Base URL → API Key
 *   - 两个 Base URL 前后相邻，中间不夹别的字段或说明
 *   - API Key 在两项 Base URL 之后
 *   - 「基础 URL」改名为 OpenAI Base URL，中英文案一致
 *
 * 运行：node tests/js/subscription_form_layout_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  ok  ${name}`);
  } else {
    failures += 1;
    console.error(`  FAIL ${name}${extra !== undefined ? `\n       ${extra}` : ''}`);
  }
}

const from = HTML.indexOf('<div class="modal-backdrop" id="subscriptionModal"');
const to = HTML.indexOf('<div class="modal-backdrop" id="discoverModal"');
if (from < 0 || to <= from) {
  console.error('未能在 web/index.html 中定位订阅弹框');
  process.exit(1);
}
const MODAL = HTML.slice(from, to);
const BODY_FROM = MODAL.indexOf('<div class="modal-body">');
const BODY = MODAL.slice(BODY_FROM, MODAL.indexOf('<div class="modal-foot">'));

console.log('[sub-form] 每个字段独占一行');
check('modal-body 里不再有 .field-row 两列布局', !BODY.includes('field-row'), BODY.slice(0, 200));
const ids = ['subscriptionModalName', 'subscriptionModalBaseUrl',
  'subscriptionModalAnthropicBaseUrl', 'subscriptionModalApiKey'];
for (const id of ids) {
  check(`${id} 存在`, BODY.includes(`id="${id}"`));
}

console.log('[sub-form] 字段顺序');
const order = ids.map((id) => BODY.indexOf(`id="${id}"`));
check('顺序为 名称 → OpenAI Base URL → Anthropic Base URL → API Key',
  order.every((v, i) => v >= 0 && (i === 0 || v > order[i - 1])), JSON.stringify(order));

console.log('[sub-form] 两个 Base URL 相邻');
const between = BODY.slice(order[1], order[2]);
// between 以 OpenAI 输入框结尾、以 Anthropic 输入框开头，中间只应出现一个
// <label class="field">——也就是 Anthropic 自己的开标签，不应夹第三个字段。
check('OpenAI 与 Anthropic Base URL 之间没有其它字段',
  (between.match(/<label class="field"/g) || []).length === 1, between);

console.log('[sub-form] 文案');
check('index.html 里「基础 URL」已改为 OpenAI Base URL',
  /data-i18n="settings\.baseUrl">OpenAI Base URL</.test(MODAL),
  (MODAL.match(/data-i18n="settings\.baseUrl">[^<]*/) || [''])[0]);
check('Anthropic Base URL 标签保留', /data-i18n="settings\.anthropicBaseUrl">Anthropic Base URL</.test(MODAL));
for (const key of ['settings.baseUrl', 'settings.anthropicBaseUrl', 'settings.subscriptionName', 'settings.apiKey']) {
  check(`i18n 中英文案都有 ${key}`, SRC.split(`'${key}':`).length - 1 === 2,
    `实际 ${SRC.split(`'${key}':`).length - 1} 处`);
}
check("中文文案不再是「基础 URL」", !/'settings\.baseUrl': '基础 URL'/.test(SRC));
check('Anthropic 说明指向 OpenAI Base URL',
  /'settings\.anthropicBaseUrlNote': '[^']*OpenAI Base URL/.test(SRC)
  && /'settings\.anthropicBaseUrlNote': '[^']*OpenAI Base URL[^']*。/.test(SRC));

console.log('[sub-form] 样式随结构同步');
check('订阅弹框每个 .field 底边距归零（间距交给 grid gap）',
  /\.subscription-modal \.modal-body > \.field \{ margin-bottom: 0; \}/.test(CSS));

console.log('[sub-form] 保存逻辑仍读两个输入框');
check('saveSubscriptionModal 读 base_url', /const baseUrl = \$\('#subscriptionModalBaseUrl'\)\.value\.trim\(\);/.test(SRC));
check('saveSubscriptionModal 读 anthropic_base_url', /const anthropic = \$\('#subscriptionModalAnthropicBaseUrl'\)\.value\.trim\(\);/.test(SRC));
check('编辑回填两个 Base URL', /baseInput\.value = sub\.base_url \|\| '';/.test(SRC)
  && /anthropicInput\.value = sub\.anthropic_base_url \|\| '';/.test(SRC));

if (failures) {
  console.error(`\n[sub-form] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[sub-form] 全部通过');

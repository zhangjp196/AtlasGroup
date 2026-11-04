/**
 * v3 附件输入回归：粘贴 / 拖拽 / 模型视觉门控 / 发送后清空 / 会话区展示。
 *
 * 运行：node tests/js/v3_attachments_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/v3/app.css'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

function grab(name) {
  const start = JS.indexOf(`function ${name}(`);
  if (start < 0) throw new Error('missing ' + name);
  let i = JS.indexOf('{', start);
  let depth = 0;
  for (let j = i; j < JS.length; j += 1) {
    if (JS[j] === '{') depth += 1;
    else if (JS[j] === '}') {
      depth -= 1;
      if (depth === 0) return JS.slice(start, j + 1);
    }
  }
  throw new Error('unclosed ' + name);
}

// ── 1. 交互接线 ────────────────────────────────────────────────────────
check('输入框支持粘贴图片', JS.includes("ta.addEventListener('paste'")
  && JS.includes('e.clipboardData.files'));
check('输入框支持拖拽文件', JS.includes("composerBox.addEventListener('drop'"));
check('附件选择后重置 input', JS.includes('fileInput.value = \'\';'));
check('发送前按模型视觉能力校验', JS.includes('const vision = supportsVision(w);'));
check('发送后清空待发送区', JS.includes('clearAttachments(w);'));
check('会话区乐观消息带附件', JS.includes("messageNode({ by: 'user', content: text, attachments: optimisticAtts })"));
check('发送 payload 带 base64 附件', JS.includes('attachments: payloadAttachments.length ? payloadAttachments : undefined'));
check('待发送区按图片 / 文件渲染', JS.includes('att-pending${image')
  && CSS.includes('.att-pending.is-image'));
check('待发送区有拖拽高亮样式', CSS.includes('.composer .box.drag-over'));

// ── 2. 模型视觉门控 ────────────────────────────────────────────────────
const code = [
  'const S = ' + JSON.stringify({
    activeModel: 'vision-model',
    models: [
      { code: 'vision-model', effective_vision: true },
      { code: 'text-model', effective_vision: false },
    ],
    roles: [{ id: 'r1', model_code: 'text-model' }],
  }) + ';',
  grab('modelProfile'), grab('supportsVision'),
  'return supportsVision;',
].join('\n');
const supportsVision = new Function(code)();

check('默认使用激活模型的视觉能力', supportsVision({ conv: {} }) === true);
check('会话模型不支持图片时拦截', supportsVision({ conv: { model_code: 'text-model' } }) === false);
check('后端明确返回 vision 时以它为准', supportsVision({ conv: { vision: true, model_code: 'text-model' } }) === true);
check('无会话模型时回退到角色固定模型',
  supportsVision({ conv: { role_id: 'r1' } }) === false);

// ── 3. 附件类型判定 ────────────────────────────────────────────────────
const kindCode = [
  'const IMAGE_EXT_RE = /\\.(png|jpe?g|gif|webp|bmp|avif)$/i;',
  grab('attExt'), grab('attachmentKind'),
  'return attachmentKind;',
].join('\n');
const attachmentKind = new Function(kindCode)();
check('图片附件', attachmentKind('a.png', '') === 'image');
check('PDF 附件', attachmentKind('a.pdf', 'application/pdf') === 'pdf');
check('Office 附件', attachmentKind('a.docx', '') === 'office');
check('普通文本附件', attachmentKind('a.txt', 'text/plain') === 'file');

if (failures) {
  console.error(`\n[v3-attachments] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[v3-attachments] 全部通过');

/**
 * 会话附件预览：md / html / word / ppt / pdf / txt / 表格的统一卡片。
 * 运行：node tests/js/attachment_preview_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/v3/app.css'), 'utf8');
const ROUTES = fs.readFileSync(path.join(ROOT, 'services/agent_zero/routes/task_routes.py'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

check('附件统一用可展开卡片', JS.includes('function attachmentsHtml(')
  && JS.includes('class="att-card"') && JS.includes('data-att-toggle'));
check('预览接口按需加载', JS.includes('/preview`') && ROUTES.includes('/attachments/{attachment_id}/preview'));
check('Markdown 渲染', JS.includes("['md', 'markdown', 'mdx']") && JS.includes('att-md'));
check('HTML 用 sandbox iframe', JS.includes("['html', 'htm', 'xhtml']")
  && JS.includes('sandbox=""') && JS.includes('srcdoc'));
check('PDF 用浏览器内嵌预览 + 文本兜底', JS.includes('att-pdf') && JS.includes('att-pdf-text'));
check('CSV / TSV / Excel 渲染表格', JS.includes('function attCsvTable(')
  && JS.includes("['csv', 'tsv']") && JS.includes("['xls', 'xlsx', 'xlsm', 'ods']")
  && CSS.includes('.att-table'));
check('Word / PPT / Excel / 文本走文档预览', JS.includes("return 'Word'")
  && JS.includes("return 'PPT'") && JS.includes("return '表格'") && CSS.includes('.att-doc'));
check('预览卡片样式', CSS.includes('.att-card-head') && CSS.includes('.att-html')
  && CSS.includes('.att-pdf') && CSS.includes('.att-foot'));

if (failures) {
  console.error(`\n[attachment-preview] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[attachment-preview] 全部通过');

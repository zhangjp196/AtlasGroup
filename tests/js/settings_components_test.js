/**
 * 设置面板组件回归：宽度滑杆、开关、MCP key-value 编辑器、角色工具选择器。
 * 运行：node tests/js/settings_components_test.js
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'web/assets/app.css'), 'utf8');
const SRC = fs.readFileSync(path.join(ROOT, 'web/assets/app.js'), 'utf8');
const V3_JS = fs.readFileSync(path.join(ROOT, 'web/v3/app.js'), 'utf8');
const V3_CSS = fs.readFileSync(path.join(ROOT, 'web/v3/app.css'), 'utf8');

let failures = 0;
function check(name, cond, extra) {
  if (cond) console.log(`  ok   ${name}`);
  else {
    failures += 1;
    console.error(`  FAIL ${name}${extra ? `\n       ${extra}` : ''}`);
  }
}

// ── 外观：宽度用滑杆 + 数值回显，开关用 switch ─────────────────────────
check('外观宽度改成滑杆', HTML.includes('type="range" id="uiSidebarW"')
  && HTML.includes('type="range" id="uiDockW"'));
check('滑杆带数值回显', HTML.includes('id="uiSidebarWVal"') && HTML.includes('id="uiDockWVal"'));
check('外观开关使用 switch 组件', (HTML.match(/class="switch"/g) || []).length >= 3
  && CSS.includes('.switch-field'));
check('滑杆拖动即时预览、松手落盘', SRC.includes('const bindRange = (')
  && SRC.includes("input.addEventListener('input'") && SRC.includes("input.addEventListener('change'"));
check('同步时刷新滑杆数值', SRC.includes("setOut('#uiSidebarWVal'")
  && SRC.includes("setOut('#uiDockWVal'"));

// ── MCP：编辑回填 + 环境变量 / 请求头 key-value 编辑器 ─────────────────
check('MCP 弹框有启用开关', HTML.includes('id="mcpEnabled"') && HTML.includes('mcp.enabled'));
check('MCP 有环境变量编辑器', HTML.includes('id="mcpEnvRows"') && HTML.includes('data-kv-add="mcpEnvRows"'));
check('MCP 有请求头编辑器', HTML.includes('id="mcpHeaderRows"') && HTML.includes('data-kv-add="mcpHeaderRows"'));
check('MCP 列表支持编辑', SRC.includes('data-mcp-edit') && SRC.includes('function openEditMcp('));
check('MCP 编辑走 PUT 保存', SRC.includes("method: 'PUT', body: JSON.stringify(body)")
  && SRC.includes('mcpEditingId'));
check('MCP key-value 读写助手', SRC.includes('function kvRowHtml(') && SRC.includes('function fillKv(')
  && SRC.includes('function readKv('));
check('MCP 列表启用改成开关', SRC.includes('data-mcp-enabled='));

// ── 技能：启用也用开关 ────────────────────────────────────────────────
check('技能启用改成开关', SRC.includes('data-skill-enabled=') && !SRC.includes('data-skill-toggle='));

// ── Hooks：超时滑杆 + 匹配器快捷 chips ───────────────────────────────
check('Hook 超时改成滑杆', HTML.includes('type="range" id="hookTimeout"')
  && HTML.includes('id="hookTimeoutVal"'));
check('Hook 匹配器有快捷选项', HTML.includes('data-hook-matcher-value="*"')
  && HTML.includes('data-hook-matcher-value="fs|write|edit"')
  && SRC.includes('paintHookTimeout'));

// ── V3 角色编辑：工具勾选 + 模型下拉，不再手写逗号列表 ────────────────
check('V3 角色工具改成勾选面板', V3_JS.includes('function ensureToolCatalog(')
  && V3_JS.includes('function roleToolPickerHtml(') && V3_JS.includes('data-role-tool='));
check('V3 角色模型改成下拉', V3_JS.includes('<select class="input" id="rModel">')
  && V3_JS.includes('跟随全局'));
check('V3 工具选择器有搜索 / 全选 / 清空', V3_JS.includes('id="rToolSearch"')
  && V3_JS.includes('data-tools-all') && V3_JS.includes('data-tools-none'));
check('V3 工具选择器样式', V3_CSS.includes('.tool-picker') && V3_CSS.includes('.tool-choice'));

if (failures) {
  console.error(`\n[settings-components] ${failures} 项失败`);
  process.exit(1);
}
console.log('\n[settings-components] 全部通过');

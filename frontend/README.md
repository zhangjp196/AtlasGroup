# AtlasX 前端（3.7 迁移中）

Vue 3 + Vite + TypeScript + Pinia + Vue Router + **Element Plus**。唯一入口是 `/`，旧 V3 已移除。

## 开发

```bash
npm install
npm run dev      # http://127.0.0.1:5173/，/api 代理到 127.0.0.1:10210
```

如果后端端口不是 10210：

```bash
ATLAS_API=http://127.0.0.1:10211 npm run dev
```

## 构建

```bash
npm run build    # 类型检查 + 输出到 ../web/dist
```

后端在 `/` 托管 `web/dist`；静态资源在 `/static/`，`router.ts` 使用 `BASE_URL` 的 history 模式。

## Element Plus

- 全局注册：`main.ts` 里 `app.use(ElementPlus, { locale: zhCn })`。
- 组件：设置 tabs、表单、弹框、按钮、标签、统计卡、面包屑、组织树使用 Element Plus；
  聊天时间线 / 气泡 / 工具卡保留自定义组件，因为 Element Plus 没有对应的流式聊天原语。
- 主题：`styles/element.css` 把 `--el-*` 映射到现有 `--accent / --panel / --line` 等 token。
- npm 源：`frontend/.npmrc` 固定 `registry.npmmirror.com`。

## 页面形态

- 工单：表格工作台 + 注意力/统计。
- 会话：收件箱列表 + 右侧概览。
- 工作区：资源管理器式左列表 + 右侧详情。
- 角色：组织树 + 角色卡网格。
- 群组：画廊式群组卡。
- 设置：左侧纵向导航 + 右侧内容。

## 质量门禁

```bash
npm run typecheck
npm run test:unit
npm run build
```

## 结构

```text
src/
  api/          请求封装与类型
  components/   rail、全宽画布页头、创建弹框、聊天抽屉 / 执行画布
  composables/  useChat（SSE / 运行状态）、useNative（pywebview 桥）
  lib/          markdown、trace、主题、格式化、派生数据
  styles/       旧 V3 设计 token + Element Plus 变量映射
  stores/       Pinia：app / data / attention
  views/        工单 / 会话 / 工作区 / 角色 / 群组 / 设置 / 关于
```

## 迁移状态

- 已迁移：主壳、列表、单聊/群聊聊天主路径（SSE、工具卡、审批、提问、todo）、
  附件上传（粘贴 / 选择 / 拖拽）、`@` 成员补全、斜杠命令、排队条、临时角色、工作区绑定、
  执行画布、工作区文件浏览、角色详情与编辑、群组详情/成员增删/设为主持、工单列表与详情、
  设置（模型/外观/工作区/工具/运行时/MCP/技能/命令）、附件卡片预览（PDF / HTML / Markdown / CSV / JSON / 文本）。
- 待迁移：CodeMirror 6 代码查看、工作区终端 / 搜索 / 写文件、Superagent 完整工作台、角色工具包络编辑。
- 旧 V3 / `/classic` / `/next` 已全部移除，不再保留兼容入口。

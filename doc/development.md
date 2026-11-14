# 开发工作流

## 模块地图

- 请求入口：`services/agent_zero/routes/`
- 对话与运行生命周期：`services/agent_zero/task_layer/`
  - `conversation.py`：对话用例编排与兼容门面
  - `conversation_lifecycle.py`：会话/栏目创建、分支复制、清空和级联删除
  - `conversation_queries.py`：会话列表、消息查询及转录投影
  - `conversation_context.py`：历史上下文、压缩与检查点
  - `conversation_stream.py`：运行事件投影、用量统计及流式辅助
  - `conversation_messages.py`：GDAG 消息读写、编辑撤回、队列标记与历史截断
  - `conversation_tools.py`：对话工具 Schema、能力包络筛选和串行调度策略
  - `conversation_discussion.py`：角色讨论参与者解析、提示词和多轮协调
- 可复用领域能力：`services/agent_zero/services/`
- 模型协议与工具执行：`services/agent_zero/runtime/zero/`、`tools/`
- Vue 页面：`frontend/src/views/`
- Vue 交互：`frontend/src/components/`、`composables/`
- 持久化：`models/`、`stores/`、`database.py`

## 日常改动

1. 确认需求对应的入口、状态所有者和已有 API。
2. 保持改动在一个明确职责内；跨层需求按 route → service → store/runtime 或 view → component/composable → API 分层实现。
3. 先跑与改动范围匹配的检查；跨层或生命周期改动跑完整检查。
4. 检查 `git diff`，确认没有覆盖用户改动，并在交付时列出验证结果。

## 检查命令

- `bash devops/dev.sh check:fast`：Python lint、前端类型检查。
- `bash devops/dev.sh smoke`：核心应用与工作区 API 冒烟检查。
- `bash devops/dev.sh check:full`：前端测试/构建、Python lint/编译和完整 pytest。

## 浏览器回归

- `bash devops/dev.sh check:e2e`：使用本机 Chrome/Chromium 启动临时 Vite 页面，真实执行根目录与嵌套目录导航、文件预览、路径引用、续办提示和检查点单文件恢复。
- 可用 `CHROME_PATH=/path/to/chrome` 指定浏览器，`E2E_VITE_PORT=4179` 覆盖本地端口。
- 页面 API 返回由测试夹具模拟，不连接后端、不调用模型；该项独立于 `check:full`，因为 CI 环境不一定安装浏览器。

完整检查要求 Python 和 Node 依赖齐全；环境不完整时命令应失败并说明缺少项。

## 扩展约定

- 新工具、模型协议和集成优先接入现有 registry、adapter 或 service seam，不在中央会话循环增加供应商专属分支。
- 新 UI 行为优先做成独立组件/composable；共享能力从原组件提取后再复用。
- `.codegraph` 有待同步文件时，先同步再用结构查询结果定位调用关系。

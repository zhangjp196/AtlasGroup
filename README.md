# AtlasX · 本地智能体

> **3.7.0**：前端彻底切换为 Vue 3 + Element Plus + Vite：旧 V3、`/classic`、`/next`、`web/assets`、`web/v3` 全部移除，唯一入口是 `/`；页面按工单表格、会话收件箱、工作区资源管理器、角色组织树、群组画廊、设置纵向导航区分；新增全局Agent 悬浮窗（全局消息 / 审批 / 提问 / 已读 / 权限 / 停止运行）。
>
本地单用户的智能体服务：FastAPI 后端 + **Vue 3 / Element Plus / Vite 前端** + **sqlite 单文件**。
没有登录、RBAC、多租户；前端构建只在开发 / 发布时需要 Node，运行时不依赖 Node。
**界面**：左轨 + 全宽画布；工单表格、会话收件箱、工作区资源管理器、角色组织树、群组画廊、设置纵向导航；聊天从右侧抽屉展开。

> **安全边界**：接口无鉴权，只允许监听回环地址。把 `server.host` 改成
> `0.0.0.0` / `::` 后 `/workspace` 的写文件、删文件、改目录等接口会对同网段
> 可见；此时 `webapp.serve()` 会打 warn 日志，别忽略它。

- 后端：`services/agent_zero`（入口 `main-agent-zero.py`）
- 前端：`frontend/`（Vue 3 + Element Plus + Vite + TypeScript，唯一入口 `/`），构建产物在 `web/dist`
- 数据库：`data/agent_zero/agent_zero.db`（唯一数据源）
- LLM：经 pydantic-ai v2 直连 OpenAI Chat / Responses 与 Anthropic Messages 兼容端点，支持在 `config.yaml` 或界面「设置 → 模型」中配置多个模型并切换
- 端口：默认 `10210`；被占用时自动顺延，实际地址写入 `data/agent_zero/runtime.json`

## 界面（Vue 3 + Element Plus）

当前只有一套前端：`frontend/`，构建到 `web/dist`，唯一入口是 `/`。

- **主壳**：左侧图标 rail + 全宽画布，没有中间列表。
- **工单**：表格工作台，顶部注意力 / 统计 / 状态筛选。
- **会话**：收件箱式列表 + 右侧概览；点开从右侧展开聊天抽屉。
- **工作区**：资源管理器式左右布局。
- **角色**：组织树 + 角色卡网格。
- **群组**：画廊式群组卡。
- **设置**：左侧纵向导航 + 右侧内容。
- **全局Agent**：右下角悬浮窗，展示全局消息、待授权 / 待回答 / 未读 / 运行中，并可直接处理。
- **聊天**：SSE 流式时间线（思考 / 工具 / 正文）、任务清单、审批、提问、附件。
- **技术栈**：Vue 3 + Element Plus + Vite + TypeScript + Pinia + Vue Router。

相关文档：

- [3.7.0 前端框架化评估](doc/2026-11-04-3.7.0-前端框架化评估.md)
- [执行画布](doc/2026-11-04-执行画布-DAG与子代理进度.md)
- [Superagent 工作方式与准入](doc/2026-11-04-Superagent-工作方式与准入.md)
- [文档索引](doc/README.md)

## 目录

```
services/agent_zero/   后端服务（API / 对话引擎 / 工具 / sqlite 存储）
services/shared/       自包含共享层（路径 / 日志 / YAML / 端口绑定）
config/agent_zero/     服务配置
data/agent_zero/       运行时数据（sqlite / 工作区 / runtime.json）
sql/agent_zero/        sqlite 表结构（由 ORM 模型导出的参考 DDL）
frontend/              Vue 3 + Element Plus + Vite + TypeScript 前端源码
web/dist/              Vite 构建产物（唯一前端入口）
devops/                开发与运维脚本
scripts/init_db.py     手动初始化数据库
doc/                   设计与分析文档
```

## 快速开始

```bash
# 1) 安装后端依赖（创建 .venv）
bash devops/dev.sh env

# 2) 配置 LLM：编辑 config/agent_zero/config.yaml 的 llm 段
#    base_url / api_key / model

# 3) 启动（后台）
bash devops/dev.sh start
bash devops/dev.sh status     # 查看实际监听地址（端口顺延后以此为准）
bash devops/dev.sh logs       # 跟踪日志
bash devops/dev.sh stop
```

启动后直接访问 `runtime.json` 里的 `url`，例如 <http://127.0.0.1:10210/>。
唯一前端入口是 `/`（需先构建一次，见下）；旧 V3、`/classic`、`/next` 均已移除。

```bash
# 前端开发（Vite HMR，代理 /api 到本地 FastAPI）
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173/

# 前端构建（输出 web/dist，后端在 / 托管）
npm run build
```

日常开发改 `frontend/`，用 `npm run dev` 即可热更新；桌面端打包只在
明确发布时执行，见「macOS 桌面端」。

### 测试与 CI

依赖锁：`services/agent_zero/requirements.txt`（运行时）与 `requirements-dev.txt`
（pytest / ruff）；**品牌名**唯一来源是 `services/agent_zero/version.py` 的
`APP_NAME`，**版本号**唯一来源是仓库根目录的 `VERSION`，后端、桌面端与打包 spec
都读它。

```bash
bash devops/dev.sh env-dev   # 安装测试/CI 依赖
bash devops/dev.sh ci        # compileall + ruff + pytest
```

## macOS 桌面端

仓库自带原生桌面端（pywebview / WKWebView）：

```bash
# 构建依赖（只需一次）
.venv/bin/pip install pywebview pyinstaller pillow

# 打包（同时生成 dist/AtlasX.app 与 dist/AtlasX-<VERSION>.dmg）
bash desktop/build_macos.sh
open dist/AtlasX.app
```

- 原生窗口（WKWebView）/ Dock 图标 / macOS 菜单 / 全屏 / 窗口位置记忆
- 数据与代码分离：`~/Library/Application Support/Atlas/`（数据库、附件、日志、配置；目录名沿用旧名以保留已有数据）
- 服务跑在 127.0.0.1 临时端口，不占用 10210、不暴露局域网
- 添加工作区时可用系统文件夹选择器；附件选择走系统文件面板；外链默认浏览器
- 关窗自动停止服务；签名 / 公证说明见 [`desktop/README.md`](desktop/README.md)

源码调试：

```bash
.venv/bin/python desktop/launcher.py
```

## Windows 桌面端

```powershell
.\devops\dev.ps1 env         # 安装依赖
.\devops\dev.ps1 dev         # 开发运行
.\desktop\build_windows.ps1  # 打包（dist\AtlasX\AtlasX.exe）
```

- shell 工具在 Windows 上使用 PowerShell（无 PowerShell 时退回 cmd），输出统一按 UTF-8 解码
- 数据目录：`%APPDATA%\Atlas`（数据库、附件、日志、配置；沿用旧名）
- Windows 暂无 Keychain 等价实现，模型 api_key 仍保存在本地 sqlite
- 构建脚本尚未在实机验证，首次使用请先跑一次 `dev.ps1 dev` 冒烟

## 运行模式 / 输入队列 / 任务清单

输入框左侧的运行相关控件只保留两项：**目标模式图标**（点亮 = 目标模式，再点一次回到普通自主执行）与**权限档位**（`Ask` / `Auto` / `Allow`）。`chat` 纯对话入口与 `agent` 标签已从界面移除，默认即为自主执行。

- **权限档位**：`Ask` 每次危险操作都询问；`Auto` 由规则判定，低/中危自动执行、高危才询问；`Allow` 全部直接执行。档位按会话保存，新会话沿用上一次选择。
- **模型强度（思考级别）**：已合并进模型选择菜单，打开右上角模型下拉后可直接切换当前模型的思考级别（`Default` / `Minimal` / `Low` / `Medium` / `High` 或模型自定义级别），选择后按模型记忆并写入当前会话。
- **队列模式**：运行中仍可继续输入，消息按 FIFO 排队，当前回复结束后由 worker
  自动执行下一条。队列条上可以对已排队的消息「上移 / 下移」（自由调整执行顺序）、
  「编辑」、「立即插入」（作为干预注入正在执行的回复）或取消；输入框本身不提供插队开关。
  排队中的消息**只出现在队列条上**，不当作已发送的消息进入转录——等它自己的 run
  开始执行时才出现在转录里，排队期间也不进入上下文。
- **目标模式**：在 `goal` 模式下发送消息会创建（或复用）一个对话级目标；每次 run
  正常收尾后，只要目标仍 `active` 且未超出 `token_budget` / `max_runs`（两者 0 = 不限），
  系统会自动入队续跑 run，并把目标与进度注入系统提示。模型用 `goal_report` 汇报进展
  （progress / blocked / achieved，可带完成度、下一步、阻塞项与证据），用 `goal_manage`
  查看目标、调整预算 / 次数、暂停等待用户、恢复或结束目标；输入框上方的目标条显示运行
  次数 / token 用量，可改预算或结束目标。标记 blocked 时会暂停自动续跑，直到用户回复或
  模型调用 `goal_manage(resume)`。
- **新建会话**：会话页右上角「新建会话」，先选「单聊 / 群聊」；群聊可选已有群，或填群名现场新建并绑定。
- **群聊 / 团队**：会话绑定团队时按 `team.mode` 执行——`orchestrate`（默认，主持编排、
  成员在委派卡片里干活）/ `roundtable`（圆桌，多头像各自应答，`max_speakers` 限制每条
  消息最多几个角色）/ `single`（单角）。输入 `@` 会列出群成员补全；进群 / 退群 / 加人 /
  改模式会以系统消息出现在对话里（不进入模型上下文）。旧工作区的群组（host + 成员）会在首次对话时
  惰性归一成默认团队并绑定，历史会话不丢。
- **任务清单**：进度只有一个载体——对话里的「任务」卡片。模型用 `plan{op:"todo"}`
  （`todo_write`）**整份覆盖**写入：每条 `{content, status}`，`status` 只能是
  `pending` / `in_progress` / `completed`，同一时刻最多一条进行中；模型与用户改的是同一份
  （跨 run 持久）。

## 消息操作

已执行的消息悬停时可「复制 / 编辑 / 撤回」；还在排队的消息在队列条上操作：

- **复制**：复制消息正文到剪贴板。
- **编辑**：已执行的消息会截断它之后的对话，用新正文重新发起一条 run。
  排队中的消息点队列条上的「编辑」（替换排队 run，保留位置与优先级，编辑时
  临时显示在转录里）。正在执行的消息需要先强制停止运行。
- **撤回**：已执行的消息会截断该消息及其后的对话；排队中的消息用队列条上的
  「取消」（直接删除队列项与消息）。正在执行的消息需要先强制停止运行。

## 附件与上传

输入框左下角的回形针可以添加附件，也可以把文件拖进输入框或直接粘贴剪贴板图片：

- **图片**：只有模型设置里勾选「图片支持」的模型才能上传；图片随**最新一条用户
  消息**作为多模态内容发送，历史消息只保留图片名称，避免重复占用上下文；
- **文本 / 代码**：`txt`、`md`、`java`、`cs`、`py`、`js`、`ts`、`json`、`yaml`、
  `sql`、`sh` 等按文本读取（UTF-8 / UTF-16 / GB18030 等编码自动识别）；
- **Office / PDF**：`docx`、`xlsx`、`xlsm`、`pptx`、`odt`、`ods` 使用标准库解析；
  旧版 `doc` / `ppt` 依次尝试 `textutil`、`soffice`、`antiword` / `catdoc`，最后
  退回可读字符串；`pdf` 优先 `pypdf` 或 `pdftotext`，不可用时用标准库兜底；
- 附件抽取出的文本会随用户消息进入模型上下文（单文件与总量都有预算上限）；
- 单条消息最多 8 个附件，单个 25 MB、总计 50 MB；
- **长文本**：输入超过 6000 字符时，发送前会询问是否转成 `txt` 文档附件；确认后
  转录里显示一个 txt 附件，附件全文仍会进入模型上下文。取消后按原文直接发送
  （不再设字符上限，由上下文压缩处理长度）。

附件文件保存在 `data/agent_zero/uploads/<conversation_id>/`，消息节点只保存
`id / name / mime / size / kind` 等元数据；删除对话或截断消息时会同步清理。

## 文件引用

- 工作区文件列表每行悬停时出现「+」按钮，可把**文件或文件夹**引用到当前对话；
  文件夹会以 `path/` 形式记录，模型可据此用 `list_files` / `read_file` 查看
  （Office / PDF 也能读，见下一节）。
- 引用以 `@路径` chip 显示在输入框上方，可逐个移除；消息发送后 transcript 里也会
  显示引用 chip。
- 引用随用户消息进入模型上下文，格式为 `[引用文件: path1, path2/]`。

## 代码图谱 / CodeGraph

工作区源文件会自动建立内部代码图谱（文件 → 符号 → import → 反向引用），用于先定位再读文件，减少盲读带来的 token 消耗：

- 首次注入上下文 / 首次调用时自动初始化，之后按文件 mtime 增量更新
- 文件写入 / 编辑后自动失效并重建
- 工具 `codegraph`：`summary` 紧凑地图、`find` 按符号 / 路径 / import 定位（带行号）、
  `deps` 查依赖与反向依赖、`impact` 查改动影响面、`status` / `refresh`
- 旧的 `project_map` 名称保留为别名；纯聊天工作区照常可用（索引托管目录内容）

## 模型读 Office / PDF

- `read_file` 遇到 `docx` / `xlsx` / `xlsm` / `pptx` / `odt` / `ods`（旧版
  `doc` / `xls` / `ppt` 尽力抽取）与 `pdf`，会先抽取纯文本再返回带行号的内容，
  回执的 `<type>` 是 `office` / `pdf`，并列出有哪些工作表 / 幻灯片 / 页。
- 新增 `read_office` 工具（别名 `read_document` / `read_pdf`）：同一个抽取实现，
  额外支持 `part` 参数只读某个工作表 / 某页 / 某张幻灯片（名称或 1 起序号）。
- 抽取逻辑在 `services/agent_zero/services/document_service.py`，附件上传与文件
  工具共用，两边行为一致；只用标准库，`pypdf` / `pdftotext` / LibreOffice 只是
  提高 PDF 与旧版 Office 质量的可选项。
- `edit_file` / `write_file` 对这些格式会被拒绝（二进制容器，逐字符改写会损坏文件），
  报错文案会建议改用 shell 调用 Python 或导出 md / csv / txt。
- 已有角色的能力包络在启动时自动补上 `read_office`（只追加，不删用户自定义的工具
  白名单）；同时会**删掉已下线工具**（如独立 `python` 工具）留下的 code——归一后
  registry 里不存在的才删，别名（`read_document` → `read_office`）会被保留成正式名。

## 思考级别

- 「设置 → 模型」每个模型卡片可勾选内置思考级别（极低 / 低 / 中 / 高），也可以
  输入自定义值（如 `xhigh`）后点「添加」。
- 当前模型配置了至少一个思考级别时，输入框右下角会出现独立的**思考强度 chip**，与模型按钮并排；
  模型与思考强度在输入区用下拉选择；未配置思考级别时不显示思考下拉。
- 选择后 chip 会立即显示当前强度（如「中」）；「默认」表示不发送 reasoning 参数，由模型自己决定。
- 选中的级别随本条 run 保存：Chat 协议以 `reasoning_effort` 发送，Responses 协议以
  `reasoning: {effort}` 发送；排队消息保留各自选择。
- 思考级别和模型都是**会话级**的：切换会话会恢复该会话上次的选择；在会话里切换后
  立刻保存，下一个新会话默认沿用这次选择（按模型记在浏览器本地）。

## 端口

`config/agent_zero/config.yaml`：

```yaml
server:
  host: 127.0.0.1
  port: 10210
  auto_port: true    # false = 端口被占用时直接启动失败
  port_tries: 30     # 最多向后尝试多少个端口
```

服务在启动 uvicorn 之前先真实绑定 socket；若 `10210` 被占用，会依次尝试
`10211`、`10212`…… 实际监听地址写入 `data/agent_zero/runtime.json`：

```json
{
  "pid": 12345,
  "host": "127.0.0.1",
  "port": 10211,
  "url": "http://127.0.0.1:10211/",
  "started_at": "2026-10-24 15:00:00"
}
```

## 配置

`config/agent_zero/config.yaml` 主要字段：

```yaml
sqlalchemy:
  db_path: data/agent_zero/agent_zero.db
  echo: false

llm:
  base_url: https://api.openai.com/v1
  api_key: ""
  model: gpt-4o-mini
  protocol: openai-chat         # openai-chat / openai-response / anthropic
  context_window: 128000
  max_tokens: 4096
  compaction:                    # 上下文压缩（OpenCode V2 口径，详见下文）
    auto: true
    keep: {tokens: 15000}        # 检查点旁保留的近期原文
    buffer: 0                    # 0 = max(15% 窗口, 16000)；正数 = 固定预留 tokens
  vision: false                  # 该模型是否支持图片（界面「设置 → 模型」可改）
  thinking_levels: []            # 可选思考级别；留空则模型菜单不显示选择条
  active_model: default          # default = 上面这套；也可填 models 里的 code
  subscriptions:                 # 可选：订阅（共享 Base URL / Key 的账号）
    opencode-go:
      name: OpenCode Go
      provider: opencode-go
      base_url: https://opencode.ai/inference/go/openai/v1
      anthropic_base_url: https://opencode.ai/inference/go/anthropic
      api_key: ""
  models:                        # 可选：多个模型档案
    fast:
      name: Fast
      model: gpt-4o-mini
    smart:
      name: Smart
      model: gpt-4.1
      subscription_id: opencode-go          # 端点与密钥继承该订阅
      context_window: 200000
      max_tokens: 8192
      vision: true                           # 该模型档案支持上传图片
      thinking_levels: [low, medium, high]   # 模型菜单可选的思考级别
```

- 环境变量可覆盖默认 LLM：`AGENT_ZERO_LLM_BASE_URL`、`AGENT_ZERO_LLM_API_KEY`、`AGENT_ZERO_LLM_MODEL`、`AGENT_ZERO_LLM_PROTOCOL`
- `models` 里的档案只需填要覆盖的字段；`base_url` / `api_key` / `context_window` / `max_tokens` /
  `vision` / `thinking_levels` 未填时继承默认配置。`vision` 标记该模型是否支持图片上传；
  `thinking_levels` 是该模型可选的思考级别，发送时选中的值会以 `reasoning_effort`
  （Responses 协议为 `reasoning.effort`）传给上游。`subscription_id` 指向 `subscriptions`
  里的账号，模型未填的 `base_url` / `api_key` / 协议等从订阅继承。`protocol` 可选
  `openai-chat`、`openai-response`、`anthropic`；同一订阅可同时配置
  `base_url` 与 `anthropic_base_url`，Anthropic 模型会自动切到后者。
- 界面「设置 → 模型」是一个统一工作区：**左栏订阅、右栏模型**。左栏只做展示，
  点击选中订阅后右栏只展示该订阅下的模型；默认选中第一个订阅。添加/编辑订阅用弹窗，
  右键订阅可「查看 / 编辑 / 删除」。一个订阅就是一个账号
  （Base URL + API Key），下面可以挂多个模型；同一服务商可以添加多个订阅，用来区分不同账号。
- 右栏模型只负责模型本身：每个模型独立设置协议 / 上下文窗口 / 视觉 / 思考级别，
  端点与密钥来自所属订阅。**密钥只有一个来源**：模型挂了订阅且订阅上有 Key 时，
  模型档案不再保存 / 使用自己的 Key（老版本留下的模型级 Key 在解析时也被忽略，
  下次保存时清掉），所以改订阅 Key 会对该订阅下所有模型立即生效。
  **没有“默认模型”概念**，内置配置也作为普通订阅展示。
- 右栏顶部的「获取模型」会调用当前选中订阅的 OpenAI `GET /models`：可把返回的模型 ID
  直接加入订阅，或「全部添加为档案」；获取结果和模型列表都支持输入筛选。
- 输入框右下角只显示**模型名**；模型选择采用二级菜单：先选订阅，再选该订阅下的模型。
- 默认订阅/模型也可以直接删除；删除后不会回落到 `config.yaml` 的默认模型，
  需要时重新添加一个订阅即可。
- 修改写入 sqlite 的 `app_settings`，**即时生效、无需重启**。清空该条记录即可回退到 `config.yaml`。

### 稳定性 / 性能（多会话多模型）

- **每个会话独立模型**：会话绑定自己的 `model_code`，run 开始时解析成
  `{loop, plan, summary, fallback}` 一份快照；别的会话切模型不会改变正在跑的会话。
- **每个模型档案可单独调参**（`models.<code>` 下，未填继承 `llm` 段）：
  `retry_attempts` / `retry_backoff_s` / `retry_deadline_s` /
  `request_timeout_s` / `connect_timeout_s`。
  慢推理渠道与本地快模型的重试预算、超时互不影响。
- **连接复用**：同渠道（Base URL + 代理设置）共用一个有上限的 HTTP 连接池，
  不再每个 LLM 请求重做 DNS/TCP/TLS；不同渠道各自一个池，互不串用。
- **配置快照**：LLM 配置按（文件 mtime + 设置版本）缓存 0.5s，避免每次请求都
  解析 YAML + 查 sqlite；改设置后立即失效。
- **重试有总时长上限**：可重试错误（网络 / 超时 / 429 / 5xx）按
  `llm.retry_attempts`（默认 20 次）× `llm.retry_backoff_s`（默认 **2.5s**，
  固定间隔、不指数退避）自动重试；429 若带 `Retry-After` 则取两者较大值等待。
  连续失败超过 `llm.retry_deadline_s`（默认 900s）就放弃并报错，上游整体挂掉时
  不会把 run 挂在“运行中”几个小时（0 = 不限）。
- **MCP 进程**：持续读走子进程 stderr（否则 Node 服务日志写满管道就会卡死），
  调用超时/失败后自动重启会话；安装命令超时会杀掉子进程，不留孤儿。
- **落库韧性**：`database is locked`（备份 VACUUM 期间可能发生）自动重试；
  已完成 run 的事件保留 7 天后在启动时清理，库与备份不会无限增长。

### 工具组织（包 + 独立工具）

同类工具合并成 **7 个包**，每个包一个 function，用 `op` 选动作；不同类的不硬塞进包：

| 包 | 动作（op） |
| --- | --- |
| `fs` | read / office / write / edit / list |
| `net` | fetch / time / calc |
| `memory` | read / write / forget / save |
| `plan` | todo / goal / delegate / team_admin |
| `dev` | job / vcs / graph |
| `skill` | list / use / create / install |
| `mcp` | list / add / install |

**独立工具**（不进包，直接按名字调用）：`shell`、`workspace`、`ask_user`
（向用户提问）、`propose_task`。

- **开发与仓库合成一个包**（`dev`）：后台任务 `tool_job`、版本控制 `vcs`、
  代码图谱 `codegraph`。`vcs` / `codegraph` 自带 `op` 参数，在包里统一叫 `action`
  （例：`dev{op:vcs, action:status}`），执行前还原成它们真正读取的 `op`；
  `shell` 仍保持独立（风险最高，审批预览要看到完整命令）。
- **同类工具先在工具层合并**：`goal_report` 合入 `goal_manage`（`action=report`）；
  `call_subagent` / `call_team` 合成 `delegate`（`target` 选子代理 / 团队成员）；
  `ask_user` 移出包，作为独立工具。设置页与模型工具清单只看到合并后的入口，
  旧的单工具名仍可执行。
- **任务与协作包只留 4 个动作**：`todo` / `goal` / `delegate` / `team_admin`。
  其中 `todo` 直指整份覆盖式的 `todo_write`；`delegate` 的 `target=team` 需要先在
  当前对话绑定团队（设置页用「团队」小标记注明）；`goal` 合并了进展汇报与目标管理。
- **向后兼容**：旧包名 `agent`（含 `agent{op:subagent}` 等旧 op）、已拆除的 `code`
  包（`code{op:shell}` / `code{op:job}` 等）、旧工具名与别名都继续能解析，
  历史消息与旧提示词不会失效。
- **同一轮里的顺序类工具走串行**：propose_task / todo_write / goal_report /
  goal_manage / call_subagent / call_team / delegate / team_manage / ask_user /
  tool_job 这些「有顺序语义或会就地改共享状态」的调用，
  一轮里只要出现一个，整轮就不走并发批——它们由对话循环的专有分支处理，混进并发批
  会被先送进分发器白跑一次、再被重跑一遍（名单见 `conversation._SEQ_CALLS_TOOLS`，
  有测试钉住它必须覆盖所有专有分支）。
- 调用形如 `{"name":"fs","arguments":{"op":"read","path":"a.py"}}`，等价于旧的
  `read_file`；命令直接 `{"name":"shell","arguments":{"cmd":"ls"}}`。
- 执行、权限、审批预览、审计、UI 标签全部按**具体工具名**走，和以前完全一样。
- 用 `tools.packs: false` 可退回逐个工具下发（排查兼容问题时用）。
- `plan{op:todo}`（`todo_write`）一次交完整清单（整份覆盖），每条
  `{content, status}`；状态 `pending` / `in_progress` / `completed`，最多一条进行中，
  空列表即清空。历史消息里的旧 `todo`（`action=add/update/complete/delete/clear`）
  仍按兼容分支执行。
- `plan{op:goal, action:report}` 汇报目标进展（进度 / 完成 / 阻塞，可带完成度、
  下一步、阻塞项与证据）；`plan{op:goal, action:status|update|note|blocked|resume|close}`
  查看与调整目标。
- `plan{op:delegate, target:subagent|team}` 把子任务交给子代理或团队成员；
  `plan{op:team_admin}` 动态定义团队。
- 界面「设置 → 工具」按卡片列出每个包与动作（op / 名称 / 具体工具 / 风险点），
  独立工具单独成组；纯聊天工作区只把 `workspace`（中间存储）置灰并说明原因，
  条件可用的动作（`delegate` 的 team 目标）带「团队」标记与提示。
- **长等待不改写命令**：命令里的 `sleep` 超过 `tools.max_sleep_s`（默认 120s）时
  只在结果里加一句提示，**命令按原样执行**——截断会把 `sleep 300 && deploy.sh`
  变成提前 3 分钟执行，定时脚本因此跑错时间点。模型的等待由
  `tools.foreground_budget_s`（默认 45s）保证：超预算即转后台托管，用
  `tool_job` 主动监督结果。
- **超大工具输出落盘**：单条结果超过 `tools.max_output_chars`（默认 40000 字符）
  时写到工作区 `.atlas/tool-output/`，上下文只留预览 + 路径，模型需要细节时自己
  分页读——避免一次 `shell` / 全文读取就把窗口顶爆、逼出硬裁。

### OpenCode Zen / Go

「设置 → 模型 → 左栏订阅」内置两个订阅预设，可重复添加同一预设来使用多个账号：

| 预设 | OpenAI Base URL | Anthropic Base URL | 模型列表 |
| --- | --- | --- | --- |
| OpenCode Go | `https://opencode.ai/inference/go/openai/v1` | `https://opencode.ai/inference/go/anthropic` | `https://opencode.ai/inference/go/v1/models` |
| OpenCode Zen | `https://opencode.ai/inference/openai/v1` | `https://opencode.ai/inference/anthropic` | `https://opencode.ai/inference/v1/models` |

- API Key 在 OpenCode Console 获取，在订阅编辑弹窗里填入「API 密钥」
- 「获取模型」会标出每个模型的协议：GPT / Grok / Muse 自动用 Responses，
  其余 OpenAI 兼容模型用 Chat；Claude / 部分 Qwen / MiniMax M3 自动用
  Anthropic Messages 并切到订阅的 Anthropic Base URL；Gemini 仍走 Google
  原生协议，列表里会标注暂不支持
- Anthropic 端点同时发送 `x-api-key` 与 `Authorization: Bearer`，并固定
  `anthropic-version: 2023-06-01`，兼顾原生 Anthropic、OpenCode 与常见的
  Anthropic 兼容中转
- 获取结果支持输入筛选；加入订阅后，模型在「模型」页和选择器里始终按订阅分组
- OpenCode Go 订阅卡片的第三行会以数字显示 **5h / 1w / 1m** 的用量百分比，
  颜色与弹窗内进度条一致（正常绿 / 80% 起黄 / 95% 起红）；
  编辑/查看弹窗里还有完整进度条
  （百分比 + 重置倒计时）；接口若返回美元金额也会一起展示
- 额度请求失败会自动重试：后端最多 3 次（指数退避），前端 8 秒后再补一次；
  成功结果缓存 60 秒，避免频繁打上游
- 请求 OpenCode 时会带 `User-Agent: AtlasX/<真实版本>` 和稳定的
  `x-opencode-session`，便于上游路由 / prompt cache

额度来自 OpenCode Go 的 `GET https://opencode.ai/zen/go/v1/usage`；该接口未公开承诺
稳定，若返回变化，面板会显示「额度不可用」而不是静默错误。

### Anthropic Messages 协议

- 模型卡片的「API 协议」可选 **Anthropic Messages（/v1/messages）**；订阅编辑弹窗里可单独填写
  **Anthropic Base URL**，同一订阅下 OpenAI 与 Anthropic 模型可共用账号 / Key。
- Base URL 兼容三种常见写法：`https://api.anthropic.com`、`https://api.anthropic.com/v1`
  与 `https://api.anthropic.com/v1/messages`，都会归一到 SDK 需要的根地址，避免
  `/v1/v1/messages`。
- 鉴权同时发送 `x-api-key` 与 `Authorization: Bearer`；原生 Anthropic、OpenCode
  以及只认其中一种的兼容中转都能工作。
- 思考级别会映射到 Anthropic 的 extended thinking；启用思考时不再发送自定义
  `temperature`，避免上游 400。自定义且不受支持的思考级别会安全忽略。
- 并行工具结果会合并到同一条 user 消息的多个 `tool_result` 块，符合 Anthropic
  Messages 的协议要求；流式文本 / 思考 / 工具调用事件与 OpenAI 协议保持一致。

### 订阅平台预设

「设置 → 模型 → 左栏订阅 → 添加平台」内置 34 个平台预设，按组筛选：

- **OpenCode**：OpenCode Go、OpenCode Zen。
- **聚合平台**：OpenRouter、SiliconFlow、ModelScope、QCode。
- **本地网关**：New API / One API、10Router、Ollama、LM Studio。
- **官方 API**：OpenAI、Anthropic、Gemini、DeepSeek、Kimi、GLM、MiniMax、
  DashScope、火山方舟、千帆、混元、xAI、Mistral、Groq、Cerebras、Together、
  Fireworks、DeepInfra、Novita、Nebius、Hyperbolic、Chutes、GitHub Models。

每个预设只负责填入平台名、协议、Base URL 和 Anthropic 端点；同一平台可以添加多次，
分别对应不同账号。本地网关预设的地址来自对应项目的公开文档，实际部署端口/路径不同时在
订阅编辑弹窗里直接修改。

> 编程订阅类的反代预设（WorkBuddy、CodeBuddy、Trae、ZCode、Qoder、CatPaw、iFlow、
> MarsCode、Comate、通义灵码等）已下线：它们依赖第三方本地转换器与账号登录态，
> 无法稳定验证，也不再展示。需要的话照旧可以「添加平台 → 自定义」手填 Base URL。

### MCP / 技能安装与命令

- macOS 桌面端启动时会读取**登录 shell 的 PATH**（zsh/bash），因此 nvm / fnm /
  Homebrew 安装的 `npm`、`npx`、`node` 在 .app 里也能找到。
- 如果本机确实没有 npm，MCP 新增窗口支持手动指定：
  - **安装命令**：如 `/opt/homebrew/bin/npm install <pkg>`、`brew install node`；
  - **command + args**：直接指定可执行文件启动 MCP（推荐绝对路径）；
  - `command` 里粘贴整条命令（含空格）也会自动拆成 command + args。
- 技能安装仍支持 Write / Upload / npm / npx；没有 npm 时可用前两种方式。
- 对话输入框右侧的模型按钮可直接切换当前模型；模型按会话保存，切换会话会恢复该会话
  上一个模型，新建会话默认沿用最近使用的模型。
- 上下文压缩参考 OpenCode V2 compaction + Claude Code microcompact，分两级：
  1. **就地清理**（microcompact / prune）：先只把保留窗口之外的旧工具输出压成
     一行摘要（结构不变、不调模型）。长任务里工具输出通常是大头，多数情况到这就
     够了，也省掉一次摘要调用；
  2. **检查点**：清理后仍超上限才调摘要模型，产物是一条
     `<conversation-checkpoint>` 结构化摘要 + 最近 `keep`（默认 15000）tokens 原文。
  触发点是上游实测 prompt 达到「窗口 − 预留」（默认 max(15% 窗口, 16000)），
  不做增速预测。摘要调用单次硬超时、不重试，等待期间每 20s 推心跳，界面显示
  「正在压缩上下文」而不是假死；摘要失败自动降级为确定性摘要，run 不会因此中断。
  需要更早/更晚触发时调 `compaction.buffer`（0 = 自动）。
- **跨轮历史有独立预算**：每轮从库里取最近 `history.max_messages` 条对话（默认 60
  条 ≈ 30 轮），再按 `history.max_tokens` 从旧到新裁（0 = 模型窗口的 20%，夹在
  8k～60k）；裁剪丢整条消息、起点对齐到用户消息，最新一轮永不裁。跨轮历史只含
  用户/助手文本（工具输出不跨轮），所以这条预算平时很宽松，主要防大附件把窗口
  吃掉——早期约定因此能留得比过去（固定 20 条 ≈ 10 轮）更久。
- **不启用 precompute**（OpenCode 的「到 85% 时后台预生成摘要」）：它省的是压缩
  那一刻的等待，**不是 token**——预生成多一次大请求、换入后保留的尾巴更长，
  换入失败还要重算；本地单卡上还会和正在生成的请求抢算力。现状「有界 90s +
  20s 心跳 + 一级 prune」已经保证不假死；将来若用云端快模型跑超长上下文，
  再按 `compaction.precompute` 加开关。
- 上下文花在哪是可见的：每轮服务端日志打印 `ctx=… breakdown={system,user,
  assistant,tool,image}`，压缩开始时执行轨迹里也会显示「工具输出 / 对话 / 系统」
  各占多少，方便判断该 prune 还是该压正文。
- 输入框右侧的百分比是**当前上下文占用**：分子取最近一轮真实 prompt（压缩后立刻
  下降，不再是历史峰值），分母是可用输入预算（上下文窗口 − 最大输出 tokens）；
  悬停可看窗口、输出预留与历史峰值。压缩点是「窗口 − 预留」，换算到百分比约为
  128k/4k → 88%、32k/8k → 67%，所以到 100% 之前就已经压缩过了。

### Token 用量统计

「设置 → 用量」实时聚合每条助手消息里落库的 usage：

- 时间窗口：1 天 / 7 天 / 30 天 / 合计
- 总量：总 token、输入、输出、缓存命中率、回复数、LLM 轮数
- 均值：每次回复 token、日均 token、LLM 耗时、生成速度（tok/s）
- 高亮：使用最多的模型、峰值日期、缓存节省 token
- 明细：按模型 / 按渠道的输入输出与占比、最近 20 次运行
- 图表：最近 30 天输入 / 输出堆叠柱状图

数据来自历史消息的 `usage`，不需要额外埋点；旧消息没有 usage 的会自动跳过。

## 扩展机制：斜杠命令与钩子

对齐 Claude Code 的两种扩展点。两者都是**工作区里的纯文本配置**，改完即生效
（2 秒缓存），不需要重启；界面「设置 → 命令与钩子」可查看 / 新建 / 删除。

### 斜杠命令

在输入框输入 `/` 会弹出命令面板（↑ / ↓ 选择，Enter / Tab 补全，Esc 关闭）。
命令是一个 Markdown 文件，文件名即命令名，按下面的顺序查找，同名以更局部的为准：

1. `<workspace>/.atlas/commands/**.md`
2. `<workspace>/.claude/commands/**.md`（兼容 Claude Code）
3. `data/agent_zero/commands/**.md`（应用全局）
4. `~/.claude/commands/**.md`（兼容用户级 Claude Code）
5. 内置 `init` / `review` / `test` / `commit`

子目录形成命名空间：`git/review.md` → `/git:review`。文件可带 YAML frontmatter：

```markdown
---
description: 审查当前改动
argument-hint: "[path]"
---
请审查 @src/app.py 的改动：$ARGUMENTS
```

- `$ARGUMENTS` 替换为全部参数，`$1`…`$9` 按位置替换；正文没有占位符但带了参数时，
  参数会追加在末尾。
- `@相对路径` 在展开时读取工作区文件（文本 / Office / PDF，单文件 30k、总量 60k
  字符封顶），附在提示词末尾，省掉模型再读一遍的往返。
- 转录里仍显示 `/review src/app.py`，**模型上下文拿到的是展开后的完整指令**
  （消息节点用 `prompt` 字段保存展开结果，刷新 / 后续轮次都一致）。
- 命令只在**用户主动发送**时展开，不执行 shell；需要副作用时让模型调用工具，
  照常走权限审批。
- 内置命令：`/init` 生成或增量更新 AGENTS.md，`/review` 审查未提交改动，
  `/test` 运行测试并修复，`/commit` 整理并提交（不 push）。`/help` 由前端直接
  打开命令面板。

### 钩子（Hooks）

钩子是**本地 shell 命令**，在工具调用前后、用户消息提交前、run 结束时触发：

| 事件 | 时机 | 能做什么 |
| --- | --- | --- |
| `UserPromptSubmit` | 模型看到消息前 | 注入额外上下文；`decision: block` 拒绝本轮 |
| `PreToolUse` | 权限判定 / 工具执行前 | `deny` 拒绝调用、`ask` 强制人工确认、`updatedInput` 替换参数 |
| `PostToolUse` | 工具返回后 | `additionalContext` 追加反馈给模型，`block` 标记阻止继续 |
| `Stop` | run 正常 / 失败收尾 | 通知、记录、外部联动（不改变 run 状态） |

配置文件（两种格式都接受，工作区钩子追加在全局之后）：

```json
{
  "PreToolUse": [
    {"matcher": "shell|fs", "command": "python3 .atlas/hooks/guard.py", "timeout": 10}
  ],
  "PostToolUse": [{"command": ".atlas/hooks/format.sh"}],
  "UserPromptSubmit": [{"command": ".atlas/hooks/context.sh"}],
  "Stop": [{"command": ".atlas/hooks/notify.sh"}]
}
```

- 全局：`data/agent_zero/hooks.json`；工作区：`<workspace>/.atlas/hooks.json`
  （也接受 `{"hooks": {...}}` 外层）。`matcher` 对工具正式名（`shell` /
  `read_file` / `write_file` …）与所属包名（`fs` / `dev` …）都做正则搜索，
  省略或 `*` 匹配全部。
- 钩子从 **stdin** 读一行 JSON：`hook_event_name` / `session_id` / `run_id` /
  `cwd` / `workspace_id` / `tool_name` / `tool_pack` / `tool_input` /
  `tool_response` / `tool_ok` / `prompt`。
- stdout 可以是纯文本（`UserPromptSubmit` 视为注入上下文），也可以是 JSON：
  `{"decision":"deny","reason":"..."}`、`{"updatedInput":{...}}`、
  `{"hookSpecificOutput":{"permissionDecision":"ask",
  "permissionDecisionReason":"...","additionalContext":"..."}}`。
- 超时默认 10 秒（1–120 可配），超时会杀掉整个进程树并继续本轮；退出码非 0
  只记日志，不会阻断执行。
- **安全**：钩子配置会在后续工具调用时执行命令，因此模型通过 `write_file` /
  `edit_file` / `shell` 修改 `.atlas/hooks.json` 时**强制人工确认**，不受
  `Allow` 档位影响。钩子本身不替代工具权限审批。

## 文件检查点与回退（rewind）

每条用户消息对应的 run **真正开始执行之前**，都会给工作区拍一次文件快照；
之后可以从消息上的时钟按钮（或右键菜单「回退」）回到那一刻，类似 Claude Code
的 checkpoint。

- 文件内容按 sha256 去重存放在 `data/agent_zero/file_checkpoints/`，清单只记
  路径 / 哈希 / 大小；同一份内容在多个检查点之间不会重复占空间。
- 跳过 `.git` / `.svn` / `node_modules` / `.venv` / `dist` / `build` / `logs` /
  `data` / `.atlas` 等目录，以及单文件 2MB 以上的内容；单次最多 20000 个文件 /
  256MB。超出上限的检查点标记为**不完整**：回退时只恢复已记录的文件，不删除
  新增文件，避免误删。
- 回退对话框会先列出相对当前工作区的新增 / 修改 / 删除文件，可分别勾选：
  - **恢复工作区文件**：被修改 / 被删除的文件恢复原内容；
  - **删除检查点之后新建的文件**：只删受管的小文件，忽略目录保持不动；
  - **删除这条消息及其后的对话**：从这条消息起截断会话，正文自动放回输入框，
    可以改完直接重发。
- 有运行正在执行时不允许回退（先停止）。每个会话默认保留最近 50 条检查点；
  消息被编辑 / 撤回 / 回退后，对应检查点会一起清理，blob 也会回收。
- 只对**有工作目录**的会话产生文件快照；纯聊天会话仍可只回退对话。

## 系统提示词

系统提示拆成「稳定工作准则」与「每轮动态上下文」两层，代码在
`services/agent_zero/task_layer/prompt_builder.py`（由
`conversation._chat_system_prompt` 收集素材后调用）：

| 段落 | 内容 |
| --- | --- |
| 你是谁 | 角色名、当前时间、语言；明确「不是搜索引擎，能动手就动手」 |
| 工作方式 | 理解 → 查证 → 小步改 → 验证 → 如实报的循环 |
| 工具使用 | 工具包 / 独立工具语法、并行读取、`fs` 替代 shell 读写、codegraph 定位、计划与待办、协作、记忆、`ask_user` |
| 文件与代码 | **先读后改**、局部 `edit` 优先、遵循现有风格、最小改动、写后语法自检、安全编码、破坏性操作与版本控制约束 |
| 验证闭环 | 相关测试 → 扩大范围、检查 diff、无法验证时明确说「未验证」 |
| 输出与沟通 | 先结论后细节、`path:line` 引用、自主执行收尾四段式、不编造 |
| 安全与权限 | 危险操作审批、密钥 / 凭证、外部脚本来源 |
| 动态上下文 | 工作区快照与代码图谱、`AGENTS.md` / `CLAUDE.md` 等约定、相关记忆、技能、团队、目标、进度、权限档位 |

- 自主执行时追加动态规划要求（登记计划、按结果调整、结论收尾）；
- 子代理使用独立提示（`SUBAGENT`），只看到目标 / 补充指令 / 显式上下文。
- 测试：`tests/unit/test_system_prompt.py` 与
  `tests/integration/test_system_prompt_context.py`。

## 会话分支与子代理隔离

### 会话分支（fork）

在任意消息上右键「从此处分支」，或会话列表右键「分支会话」：复制该消息
（含）之前的全部历史到新会话，原会话保持不变。

- 新会话继承工作区 / 角色 / 团队 / 模型 / 权限 / 运行模式，消息节点换新 id；
- 附件文件一并复制，删除任一分叉不影响另一边；
- 工作区文件是**共享**的，分支不复制文件状态；需要回退文件用上一节的检查点；
- `conversations` 新增 `forked_from_id` / `forked_from_message_id` 记录来源；
- API：`POST /conversations/{id}/fork`，body `{"message_id": "...", "title": "..."}`
  （`message_id` 省略 = 复制整段）。

### 子代理上下文隔离

`delegate`（`plan{op:"delegate"}`）派出的子代理和团队成员现在拥有**独立上下文**：

- 不再读取父对话历史，也不继承父会话的团队 / 目标；
- 只接收 `goal`、`instructions` 以及父代理显式传入的 `context`；
- 子代理的工作区快照、项目约定、记忆仍会自动注入；
- 子代理的中间过程只作为事件展示，**不会写回父对话**，只有最终结论回灌；
- 需要子代理知道的信息，用 `context` 参数传；不要指望它「记得」父对话。

## API

统一前缀 `/api/agent-zero`，响应形如 `{"code": 0, "data": ...}`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（无前缀） |
| GET | `/app/info` | 服务 / 数据库 / 工作区 / LLM 概况 |
| GET | `/app/settings` | 当前 LLM 设置（key 已脱敏，含订阅） |
| PUT | `/app/settings` | 保存订阅 / 模型 / 当前模型到 sqlite（`subscriptions[]` + `models[]`） |
| GET | `/app/models` | 模型档案列表（含继承后的有效配置与订阅，key 已脱敏） |
| POST | `/app/models/discover` | 读取 OpenAI 兼容端点的 `GET {base_url}/models` 列表（可带 `code` / `base_url` / `api_key`） |
| GET | `/app/models/presets` | 订阅平台预设（官方 API / 聚合平台 / 本地网关 / OpenCode） |
| GET | `/app/usage/stats` | 模型 token 用量统计（1 天 / 7 天 / 30 天 / 合计、按模型 / 渠道、30 天趋势） |
| GET | `/app/metrics` | 24h 运行指标（成功率 / P50·P95 时长 / 错误分类 / 队列 / 备份 / LLM 错误） |
| GET | `/app/diagnostics` | 下载诊断包 zip（脱敏设置 + 健康 / 指标 / 最近运行 / 最近日志） |
| GET | `/app/backups` | 备份列表（`data/agent_zero/backups`，保留 7 份） |
| POST | `/app/backups` | 立即创建一次 sqlite 备份 |
| GET | `/healthz` | 深健康检查（DB 可读 + worker 心跳 + 队列），异常返回 503 |
| GET | `/app/models/usage?code=` | OpenCode Go 滚动额度：5 小时 / 1 周 / 1 月 |
| POST | `/app/models/active` | 切换当前模型（`{"code": "fast"}`） |
| GET | `/app/tools` | 已启用工具 |
| GET | `/app/commands?workspace_id=` | 斜杠命令列表（工作区 / 全局 / 内置，含来源与路径） |
| POST | `/app/commands` | 新建工作区斜杠命令（`name` / `body` / `description` / `argument_hint`） |
| DELETE | `/app/commands/{name}?workspace_id=` | 删除工作区斜杠命令（内置 / 全局不可删） |
| GET | `/app/hooks?workspace_id=` | 钩子配置摘要（只读，不执行命令） |
| POST | `/app/hooks` | 追加工作区钩子（`event` / `command` / `matcher` / `timeout`） |
| DELETE | `/app/hooks?event=&index=&workspace_id=` | 删除工作区钩子 |
| POST | `/app/hooks/template` | 工作区没有 hooks.json 时创建空模板 |
| GET | `/app/runtime` | 运行队列 / 并发槽位 |
| GET/POST | `/teams?workspace_id=` | 团队（群聊）列表 / 创建（`mode` / `max_speakers` / `leader` / `members`；含惰性归一的默认团队） |
| GET/PATCH/DELETE | `/teams/{id}` | 团队详情 / 修改 / 删除（删除会解绑会话） |
| POST | `/teams/{id}/bind`、`/teams/{id}/unbind` | 绑定 / 解绑会话（body：`conversation_id`） |
| GET | `/conversations/{id}/team` | 当前会话绑定的团队 |
| GET/POST | `/conversations` | 会话列表 / 新建（可带 `permission_mode`、`run_mode`、`team_id`） |
| POST | `/conversations/{id}/read` | 已读上报（清未读红点；打开会话 / 本轮收尾时调用） |
| GET/DELETE | `/conversations/{id}` | 会话详情 / 删除 |
| GET | `/conversations/{id}/messages` | 历史消息 |
| POST | `/conversations/{id}/messages/run` | 发送消息并排队执行（运行中也可发送；可带 `attachments`、`refs`、`thinking_level`；`priority=true` 仅兼容旧客户端） |
| GET | `/conversations/{id}/attachments/{attachment_id}` | 读取消息附件（图片内联 / 文档下载） |
| PATCH | `/conversations/{id}/messages/{msg_id}` | 编辑用户消息（排队项替换 run；已执行则截断后重跑） |
| POST | `/conversations/{id}/messages/{msg_id}/recall` | 撤回用户消息（排队项删队列；已执行截断该消息及其后对话） |
| POST | `/conversations/{id}/fork` | 会话分支：`{"message_id","title"}`（省略 message_id 复制整段，附件一并复制） |
| GET | `/conversations/{id}/checkpoints` | 文件检查点列表（绑定消息，最新在前） |
| POST | `/conversations/{id}/checkpoints` | 手动创建检查点（`message_id` / `label` 可选） |
| GET | `/conversations/{id}/checkpoints/{cid}/diff` | 回退预览：相对当前工作区的新增 / 修改 / 删除 |
| POST | `/conversations/{id}/checkpoints/{cid}/rewind` | 回退：`{"files","conversation","delete_added"}` |
| DELETE | `/conversations/{id}/checkpoints/{cid}` | 删除检查点（不动文件与对话） |
| GET | `/conversations/{id}/queue` | 队列：排队消息 + 正在执行的运行 |
| POST | `/conversations/{id}/queue/{run_id}/move` | 上移 / 下移排队消息（`direction`: `up` / `down`） |
| POST | `/conversations/{id}/queue/{run_id}/prioritize` | 兼容旧客户端：移到队首 |
| POST | `/conversations/{id}/queue/{run_id}/inject` | 立即插入（作为干预注入正在执行的运行） |
| DELETE | `/conversations/{id}/queue/{run_id}` | 取消排队消息 |
| GET/PUT/DELETE | `/conversations/{id}/goal` | 目标：查询 / 创建或更新（`objective`、`token_budget`、`max_runs`）/ 结束 |
| GET/POST | `/conversations/{id}/todos` | 任务清单：列表 / 添加 |
| PATCH/DELETE | `/conversations/{id}/todos/{todo_id}` | 更新 / 删除任务 |
| GET | `/conversations/{id}/runs` | 会话运行记录 |
| GET | `/runs/{id}` | 运行状态 |
| GET | `/runs/{id}/stream` | SSE 事件流（`since` 续传；事件落库即推送，不靠轮询） |
| POST | `/runs/{id}/stop` | 强制停止运行（立即取消执行任务，不等事件间隙） |
| POST | `/conversations/{id}/proposals/{node_id}` | 接受 / 忽略自主执行建议 |
| GET | `/workspaces` | 工作区列表（`include_archived`，含对话数） |
| POST | `/workspaces` | 添加本地文件夹为工作区（`{"name","path"}`） |
| GET | `/workspaces/browse` | 浏览本机文件夹（文件夹选择器） |
| GET/PATCH | `/workspaces/{id}` | 工作区详情 / 重命名 / 归档恢复 |
| GET | `/workspaces/{id}/vcs/remotes` | git 远端列表（地址 / 上游关系） |
| POST | `/workspaces/{id}/vcs/remotes` | 添加 git 远端 |
| PUT/DELETE | `/workspaces/{id}/vcs/remotes/{name}` | 修改地址 / 移除 git 远端 |
| POST | `/workspaces/{id}/vcs/fetch` | 拉取远端（可指定 remote，默认全部，带 prune） |
| GET | `/workspace/info` | 工作区根目录 |
| GET | `/workspace/list` | 列目录 |
| GET/PUT/DELETE | `/workspace/file` | 读 / 写 / 删除文件 |
| GET | `/workspace/file/download` | 下载单文件 |
| POST | `/workspace/download/zip` | 打包下载目录 |

交互式文档：`/api/docs`。

保存多个模型并切换当前模型的示例：

```json
PUT /api/agent-zero/app/settings
{
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "active_model": "fast",
  "models": [
    {"code": "fast", "name": "Fast", "model": "gpt-4o-mini"},
    {"code": "smart", "name": "Smart", "model": "gpt-4.1",
     "base_url": "https://api.example.com/v1", "api_key": "sk-...",
     "vision": true, "thinking_levels": ["low", "medium", "high"]}
  ]
}
```

`api_key` 留空表示保留原密钥或继承默认密钥；`models` 里未填的字段同样继承默认配置。

工作区示例：

```json
GET  /api/agent-zero/workspaces?include_archived=true
POST /api/agent-zero/workspaces     {"name": "my-project", "path": "/Users/me/code/my-project"}
PATCH /api/agent-zero/workspaces/ws-xxxx   {"archived": true}
```

每个工作区保存自己的 `root_path`；对话通过 `workspace_id` 绑定到工作区，文件工具与
`/workspace/*` 接口都只在该工作区的根目录内读写。默认工作区 `default` 没有 `root_path`，
是**纯聊天工作区**：没有用户文件夹，但 shell 与文件工具**照常可用**，它们落在**托管目录**
`data/agent_zero/workspace/<workspace_id>/`（`workspace_service.exec_root`）里——可以跑命令、
做计算、抓网页、写临时产物，只是不会碰到用户的项目文件；`workspace`（中间存储）在该工作区
不下发。侧边栏仍不显示「工作区文件」入口；纯聊天工作区也不能归档。需要操作本地文件时，
用「添加工作区」指向一个本地文件夹。

## 命名与标识（AtlasX / Atlas）

产品显示名是 **AtlasX**（V3）。改名分两层，避免不必要的数据迁移：

| 层 | 内容 | 现状 |
| --- | --- | --- |
| **显示层** | 窗口 / 菜单 / 通知、启动日志与终端提示、前端 `<title>` / 启动动画 / 侧栏 / 双语文案、系统提示词身份、默认角色名（新库）、诊断包与记忆导出文件名、`AtlasX.app` / `AtlasX-<VERSION>.dmg` / `AtlasX.exe` | **AtlasX** |
| **标识层** | 数据目录 `~/Library/Application Support/Atlas`（Windows `%APPDATA%\Atlas`）、单实例锁 `atlas.lock`、bundle id `com.aiplatform.atlas`、Keychain service `com.atlas.agentzero`、工作区约定目录 `.atlas/`、模块与接口路径 `services/agent_zero` / `/api/agent-zero`、环境变量 `ATLAS_HOME` / `ATLAS_DEBUG` / `ATLAS_SMOKE` | 保持 **Atlas**（兼容） |

为什么标识层不改：数据目录 / Keychain / `.atlas` 决定已有会话、附件、已保存的
API Key 与用户工作区里约定文件的归属；bundle id 决定签名、公证与更新识别。
只改显示层，升级后数据与密钥原样可用；需要彻底改标识层（含一次性数据迁移）时
单独做一次发布。

默认角色名：新库直接叫 AtlasX；旧库只有在默认角色仍叫 `Atlas`（用户没自定义过）
时才跟随改名，自定义过的角色不动。旧会话消息里已落库的 `role_name` 保持原样。

## 数据

- sqlite 表由 ORM 模型在启动时幂等创建（`init_database()`）；
  `sql/agent_zero/schema.sqlite.sql` 只是导出的参考 DDL，不需要手工执行。
- 队列 / 目标 / 清单相关：`conversation_runs.queue_pos`（排队序号）、
  `queued_msg_id`（排队消息节点）、`thinking_level`（本轮思考级别）在启动时自动补列；
  `conversations.model_code` / `thinking_level` 记会话级模型与思考强度；
  `agent_teams` 存团队（`mode` / `max_speakers` / leader / members），`roles` 存角色包络与
  `model_code`；`conversations.last_read_at` 存已读水位（未读红点）；`todo_items` 存对话任务清单；
  `goals` 存目标状态（`status` / `token_budget` /
  `max_runs` / `tokens_used` / `runs_used`）。
- 默认工作区 `default` 与默认角色在启动时自动创建（`ensure_defaults()`）；默认工作区
  是纯聊天工作区，没有用户文件夹，工具在托管目录 `data/agent_zero/workspace/default/`
  内执行；只有带 `root_path` 的工作区才有用户的工作目录。
- 新增工作区使用用户选择的本地文件夹（`workspaces.root_path`），归档只改
  `workspaces.archived`，不删除对话和文件。
- 对话附件保存在 `data/agent_zero/uploads/<conversation_id>/`；文档抽取文本以
  `.extracted.txt` 旁路文件保存，消息节点只记录元数据。
- 时间戳使用本地时间（`datetime('now','localtime')`）。

### 重置

```bash
bash devops/dev.sh stop
rm -f data/agent_zero/agent_zero.db data/agent_zero/agent_zero.db-*
rm -rf data/agent_zero/uploads
bash devops/dev.sh start
```

## 前端

### 前端（Vue 3 + Element Plus）

- 源码：`frontend/`，Vue 3 + Element Plus + Vite + TypeScript + Pinia + Vue Router。
- 唯一入口：`/`；旧 V3、`/classic`、`/next` 已全部移除，不再保留兼容入口。
- 开发：`cd frontend && npm run dev`（HMR，代理 `/api` 到 `ATLAS_API`，默认 `http://127.0.0.1:10210`）。
- 构建：`cd frontend && npm run build`，输出 `web/dist`，后端在 `/` 托管。
- 质量门禁：`npm run typecheck`、`npm run test:unit`；CI 默认只构建，不产 dmg。

- 页面形态：工单表格工作台；会话收件箱；工作区资源管理器；角色组织树 + 角色卡；群组画廊；设置左侧导航。
- 聊天：右侧抽屉，SSE 流式时间线（思考 / 工具 / 正文）、todo、审批、提问、附件上传与预览。
- 设置：模型 / 订阅 / 外观 / 工作区 / 工具 / 运行时 / MCP / 技能 / 命令 / hooks。
- 全局Agent：右下角悬浮窗，展示待授权 / 待回答 / 未读 / 运行中，并可直接处理。
- 主题：8 套配色、暗亮自动切换；Element Plus 变量映射到现有 token。

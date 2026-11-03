# Atlas 3.0「Atlas Group」：可调整点分析

> **历史文档（已归档，2026-11-03）**：本文是当时的实测 / 分析记录，不再维护。
> V3 的目标、范围与最终口径见 [AtlasX V3 目标与收尾](2026-11-03-AtlasX-V3-目标与收尾.md)，
> 执行顺序见 [V3 路线图](2026-11-03-AtlasX-V3-路线图.md)；与它们冲突时以那两份为准。

> 基于工作区实际文件扫描（2026-11-01，r1186 工作副本，VERSION = 2.4.3）。
> 计数口径：源码层 `Atlas` 共 **124 处**（不含 `dist/` 打包产物，`dist` 已被 svn 忽略）；小写 `atlas`（标识层）约 **30 处**。
> **落地结果（2026-11-03）**：产品名最终定为 **AtlasX**（不是 Atlas Group）；已按本文 P0
> 完成显示层改名 + `VERSION = 3.0.0`，**标识层按本文 2.3 的建议保持不变**（数据目录 /
> 锁 / bundle id / Keychain / `.atlas/` / 模块与 API 路径）。P2 的彻底改名改列为可选项、
> 不排期。见 [V3 改名落地](2026-11-03-AtlasX-V3-改名落地.md)。

## 1. 一句话结论

改名可以，但要分成三层：**显示层改、标识层不改、测试层常量化**。真正的 3.0 内容不在名字，而在「Group」语义——现在代码里有两套多角色机制（`kind='group'` 工作区群组 + `AgentTeam` 团队），它们重叠且团队没有界面入口，这是 3.0 最值得动的一块。

另外，3.0 的界面定位要一次说清：**V3 没有传统的 vibecoding 工作台**，界面接近 QQ 这类 IM 客户端（会话列表 + 聊天窗，工程能力收进消息卡片与抽屉）。名字里的「Group」正好对应「群聊」，两者是同一件事，不是两个功能。

## 2. 命名触点清单（按层）

### 2.1 显示层（可改，低风险）

| 位置 | 现有内容 | 说明 |
| --- | --- | --- |
| `web/index.html:8` | `<title>Atlas</title>` | 浏览器标签 |
| `web/index.html:71` | `boot-splash-name` | 启动动画大字 |
| `web/index.html:81` | `<strong>Atlas</strong>` | 侧栏品牌 |
| `web/index.html:194` | `chatSubtitle` 默认 `Atlas` | 顶栏副标题兜底 |
| `web/index.html:301` | `placeholder="Message Atlas…"` | 输入框占位（硬编码，i18n 已覆盖） |
| `web/assets/app.js` 12 条 i18n（12 英文 + 12 中文） | `prompt.placeholder` / `workspace.empty` / `ask.title` / `empty.body` / `empty.bodyChatOnly` / `error.unreachable` | 双语文案，改名字要成对改 |
| `web/assets/app.js:3472` | `rawName !== 'Agent Zero' ? rawName : 'Atlas'` | 角色名兜底 |
| `web/assets/app.js:7460` | `document.title = ... 'Atlas'` | 页面标题拼接 |
| `web/assets/app.js:7796` | `role_name: 'Atlas'` | 本地 live turn 默认角色名 |
| `services/agent_zero/webapp.py:110/187/213/216/312` | 3 条启动日志 + `FastAPI(title="Atlas")` + 终端提示 | 日志文案 / OpenAPI 标题 / CLI 输出 |
| `services/agent_zero/routes/app_routes.py:467` | `"name": "Atlas"` | `/api/agent-zero` 健康/元信息返回，前端 `aboutService` 用它 |
| `services/agent_zero/services/diagnostics_service.py:81` | `"app": "Atlas"` | 诊断包元数据 |
| `services/agent_zero/stores/zero_store.py:302` | 默认角色 `"name": "Atlas"` | 首次建库的默认角色名（**改这里只影响新库**） |
| `services/agent_zero/task_layer/prompt_builder.py:154` | `ctx.name or "Atlas"` | 系统提示词「你是「Atlas」」 |
| `services/agent_zero/services/memory_service.py:1115` | 「你是 Atlas 工作区的长期记忆蒸馏引擎」 | 蒸馏提示词 |
| `desktop/README.md` / `README.md` | 品牌叙述 | 文档 |

### 2.2 打包与产物层（可改，需一次重打包）

| 位置 | 现有内容 | 改法 |
| --- | --- | --- |
| `desktop/Atlas.spec:81/101/106/110/111` | `name="Atlas"`、`Atlas.app`、`CFBundleName`、`CFBundleDisplayName` | 改成 `Atlas Group`（`CFBundleDisplayName` 用带空格全名，`CFBundleName` 建议无空格） |
| `desktop/Atlas.spec:108` | `bundle_identifier="com.aiplatform.atlas"` | **不建议改**（见 3.2） |
| `desktop/Atlas.spec:32/92/107` | `desktop/Atlas.icns` | 文件名可保留，只是显示名改 |
| `desktop/build_macos.sh:26/31/36/48/52/55/59/62` | `dist/Atlas.app`、`dist/Atlas-<VERSION>.dmg`、`hdiutil -volname "Atlas"` | DMG 卷名与产物名可改；注意脚本内 `rm -rf` 路径要同步 |
| `desktop/Atlas-windows.spec:67/86`、`desktop/build_windows.ps1:1/12/15/16` | `Atlas.exe`、`%APPDATA%\Atlas` | 同上，Windows 数据目录同理 |
| `desktop/launcher.py:32` | `APP_NAME = "Atlas"` | 决定数据目录名，见 3.1 |

### 2.3 标识层（原本「不建议改」，理由是不兼容老数据）

> **2026-11-01 补充：V3 不需要考虑之前的数据。** 因此这一节的「不改」结论不再成立 —— 下面每一项都可以改，只是改的波及面（脚本、打包 spec、测试）需要一次同步。真正需要单独决策的是 Keychain service 与 bundle id：它们不影响数据兼容，但影响签名/公证与已存的 api_key，改名等于重新注册身份。

| 位置 | 现有内容 | 改的影响（无兼容顾虑后） |
| --- | --- | --- |
| `desktop/launcher.py:120` | `~/Library/Application Support/Atlas` | 改名 = 老用户数据（库/附件/日志/配置）孤立，需要迁移逻辑 |
| `desktop/launcher.py:46` | `atlas.lock` | 单实例锁，改名后旧锁失效，可能双实例并发写同一 sqlite |
| `services/agent_zero/stores/secret_store.py:20` | Keychain `SERVICE = "com.atlas.agentzero"` | 改名后已存的 api_key 读不到，用户需重新填 key |
| `desktop/Atlas.spec:108` | `com.aiplatform.atlas` | 签名/公证、Gatekeeper 信任、更新识别都依赖它 |
| `.atlas/commands`、`.atlas/hooks.json`、`.atlas/rules.md`、`.atlas/<kind>/…`（`command_service.py:125`、`permissions.py:267-270`、`prompt_builder.py:71`、`registry.py:676`、`dispatch.py:235`） | 用户工作区里的约定路径 | 用户已有文件与钩子配置；权限规则正则也写死 `.atlas` |
| `services/agent_zero/runtime/zero/sandbox/subprocess_sandbox.py:205/214`、`dispatch.py:439` | `[atlas]` 前缀 | 日志标记，无用户可见价值 |
| `services/agent_zero/services/diagnostics_service.py:123` | `atlas-diagnostics-*.zip` | 文件名，可改但无收益 |
| `web/assets/app.js:9` | `const API = '/api/agent-zero'` | 接口路径；改要同步后端路由与所有测试 |
| `config/agent_zero/`、`data/agent_zero/`、`services/agent_zero/` | 目录名 | 影响 `dev.sh` / `start.sh` / `stop.sh` / `init_db.py` / spec datas 路径，收益低 |

## 3. 版本跳到 3.0 的配套动作

版本号唯一来源是根 `VERSION`（`services/agent_zero/version.py` 读它，桌面端 `launcher.py:104` 也读它），所以：

1. `VERSION` → `3.0.0`（一处改动，后端/桌面端自动生效）。
2. `web/index.html:11` 与 `:1252` 的缓存戳 `?v=2.4.7-1` → `?v=3.0.0-1`（两处，静态资源无构建链，靠戳失效）。
3. `README.md:3` 版本历史：新增 3.0.0 条目，保留 2.4.x 条目。
4. `desktop/README.md:21` 的 `dist/Atlas-2.4.3.dmg` 示例 → 3.0.0。
5. **三处硬编码 `User-Agent: "Atlas/2.0"`**（`runtime/zero/llm.py:133`、`routes/app_routes.py:813`、`:1025`）：现在写死 2.0，与真实版本不一致。建议改为读 `APP_VERSION`（`version.py` 已有 `APP_VERSION`），这是 3.0 顺手修的正确性问题。
6. `SCHEMA_VERSION`（`database.py:29`）：**已经是 3**（2026-11-02 核实），`_ensure_columns` / `_apply_migrations` 已删除，改成了 `_guard_old_database` 版本守卫（旧库没有 `schema_meta.checksum` → 备份成 `.v2.bak` + 建新库）。`tests/integration/test_app_health.py:15` 仍断言 `schema_version == 1`，**必须同步改成 3**，否则接口测试挂。

## 4. 「Group」的语义：这是 3.0 真正要解决的问题

> **界面前提（2026-11-02 补充）**：V3 没有传统的 vibecoding 工作台。界面是 QQ 这类 IM 客户端——左会话列表、右聊天窗，文件/计划/diff/终端都是会话内的卡片与抽屉。因此「Group」在界面上就是**群聊**：群名、群头像、群成员、群资料，而不是一个独立的团队管理页。详细设计见 `doc/2026-11-01-角色与团队统一设计.md` §7。

现在有两套并存的多角色机制，名字和边界都不清晰：

| 机制 | 数据 | 行为 | 限制 |
| --- | --- | --- | --- |
| 群组工作区 | `Workspace.kind == 'group'`（旧库 `workspaces.kind`；V3 基线已删该列，见 `角色与团队统一设计.md` §3），`host_role_id` + `member_role_ids` | `_group_reply_stream`（`conversation.py:4977`）：主持/成员各自应答，回复里 `@成员` 触发该成员自己回复 | `GROUP_MAX_SPEAKERS = 4`（`conversation.py:431`） |
| Agent 团队 | `AgentTeam`（`models/zero.py:78`）+ `Conversation.team_id` | `delegate(target=team)` 委派子任务，计划流程图显示成员名 | `_MAX_MEMBERS = 12`（`team_service.py:21`）；**没有界面配置入口**，全靠模型 `team_manage` 动态创建 |

可调整点：

- **统一概念**：对外统一叫「Group」，内部区分两种模式（Group 对话多角色应答 / Group 编排委派），避免「群组」与「团队」两个词混用。
- **团队没有 UI**：`web/assets/app.js:596-615` / `1693-1712` 有 20 条 `team.*` i18n key（中英各一份），但 `web/index.html` 里没有任何团队 DOM 节点，代码注释明确「团队没有界面配置入口」（`app.js:5910`）。这些文案是**死文案**——V3 的界面是 QQ 式 IM，团队 = 群聊，所以这些文案的落地形态是「群资料 / 会话信息抽屉」而不是设置页里的团队 tab（见 `角色与团队统一设计.md` §7.3）；若不做群资料，则删掉这 40 行 i18n。
- **上限可配置**：`GROUP_MAX_SPEAKERS=4` / `_MAX_MEMBERS=12` 是硬编码常量，3.0 可以做成工作区级配置。
- **角色名兜底散落**：`prompt_builder.py:154`、`conversation.py:784`、`zero_store.py:302`、`app.js:3472/7796` 各自写死默认名，应收敛到单一常量（后端 `version.py` 加 `APP_NAME`，前端从 `state.app.name` 取）。

## 5. 测试与 CI 需要同步

- 11 个测试文件写死 `Atlas` 字面量（约 23 处），其中 `tests/integration/test_system_prompt_context.py:15` 断言 `"你是「Atlas」" in text`、`tests/unit/test_system_prompt.py:52/60` 断言 `主持：Atlas`。改名后这些断言会失败——**先常量化，再改名**。
- `tests/js/` 31 个前端回归测试不含 `Atlas` 字面量，改名不影响（`devops/ci.sh` 已全量接入这 31 组）。
- `tests/e2e/test_single_instance.py` 走 `launcher._acquire_instance_lock`，若改 `atlas.lock` 文件名要同步。

## 6. 建议的实施顺序

**P0（改名本身，1 次提交）**
1. 后端加单一品牌常量（`services/agent_zero/version.py` 里 `APP_NAME`），`webapp.py` / `app_routes.py` / `prompt_builder.py` / `memory_service.py` / `diagnostics_service.py` 引用它。
2. 前端 i18n 文案里的 `Atlas` 改为占位 `{app}`，由 `state.app.name` 注入；`document.title` 与 `role_name` 兜底走同一来源。
3. `VERSION` → `3.0.0` + 两处缓存戳 + README/desktop README 版本条目。
4. 三处 `User-Agent` 硬编码改为读 `APP_VERSION`。
5. 测试断言改为引用常量。
6. 重打包 dmg（`bash desktop/build_macos.sh`）并 svn 提交。

**P1（Group 语义 + IM 界面）**
7. 决定「Atlas Group」是产品名还是功能名；若是功能名，统一 Workspace group 与 AgentTeam。
8. 界面按 IM 收敛（`角色与团队统一设计.md` §7）：会话列表补最后一条消息/头像/未读红点，顶栏图标收进「会话信息」抽屉，团队以「群资料」形态落地 —— 而不是补一个设置页的团队管理 tab。
9. `panel-viewer` 去掉代码编辑态（只读预览 + 复制），`turn-flow` 工具调用默认折叠，让「vibecoding 工作台」的痕迹从主路径消失。

**P2（标识层彻底改名，可选）**
10. 既然 V3 不需要兼容旧数据，`services/agent_zero/`、`/api/agent-zero`、`config/agent_zero/`、`data/agent_zero/`、`main-agent-zero.py`、`.atlas/` 都可以改名（波及 2079 处 `agent_zero` 引用 + 26 处 `.atlas` 引用）。建议单独一次提交，改完跑 `devops/ci.sh` 全绿。

## 7. 风险

- ~~改数据目录 / Keychain service / bundle id → 老用户数据与 key 丢失~~ —— **V3 不需要兼容旧数据，这条不再成立**；但 Keychain service 与 bundle id 改名等于重新注册身份，影响签名/公证，需要单独决策。
- **旧库静默运行**：`Base.metadata.create_all()` 不会改已存在的表。V3 基线 schema 删列/加列在旧库上不生效，启动时必须做版本守卫（不匹配 → 备份旧库 + 建新库），否则团队功能随机报错。
- 同版本号重打 dmg 会产生「同号不同内容」的产物；3.0.0 首次打包前确认 `dist/` 里没有同名产物（当前只有 `Atlas-2.4.3.dmg`）。
- 前端改名若只改 i18n 不改 `index.html` 硬编码（title / splash / subtitle / placeholder），会出现「品牌不一致」的可见 bug。
- **IM 化不是改名，是产品取舍**：把常驻 dock 收成抽屉会牺牲「边改代码边看 diff」的效率。若 3.0 同时要做 IM 化和改名，建议分两次提交（先界面后命名），否则 CI 失败时无法判断是界面回归还是改名遗漏。

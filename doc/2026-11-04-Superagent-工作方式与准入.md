# Superagent 工作方式与准入（3.5.0）

参考项目：[`deepseek-ai/dsh-superagent-prototype`](https://github.com/deepseek-ai/dsh-superagent-prototype)

它的核心不是「更强的聊天」，而是一种工作方式：

> 尽量少占用人的注意力，尽量多交付价值。
> 代理只在被请求、或被团队写下的规则允许时行动；只在有人必须行动、或结果能被使用时说话。

AtlasX 把这条理念贯穿到整个项目：Superagent 是首页、是注意力入口、也是把工作交出去的统一入口；事项（work item）只是它在后台推进工作的载体。

## 1. 事项，不是聊天

`work_items` 表保存一件事项的生命周期：

| 字段 | 作用 |
| --- | --- |
| `title` / `body` | 要做什么、验收标准、背景 |
| `source` / `source_ref` / `source_actor` | 来源与发起人；当前 UI 以手动为主，字段留给后续来源 |
| `status` | `inbox` / `working` / `needs_attention` / `done` / `closed` |
| `workspace_id` / `role_id` / `team_id` | 这个事项在哪个隔离上下文里执行 |
| `conversation_id` / `run_id` | 实际执行它的会话与运行 |
| `attention_reason` | 为什么需要人 |
| `result_summary` | 完成后回写的最后一条助手结论 |
| `priority` / `meta` | 优先级、来源元数据 |

状态由运行时自动收敛：

- 排队 / 运行中 → `working`
- 待授权 / 待回答 → `needs_attention`
- 运行成功 → `done`，并回写 `result_summary`
- 失败 / 停止 → `needs_attention`，说明原因
- 用户关闭 → `closed`

界面只把 `needs_attention` 顶到最前面；其余事项在后台推进。

## 2. 准入：先授权，再行动

`superagent` 策略保存在 `app_settings`：

- **模式**
  - `off`：只手动创建事项
  - `observe`：只记录，不自动派活
  - `auto`：新建事项后自动派活
- **新会话权限**：auto / once / session / always
- **默认执行上下文**：工作区 / 角色 / 群组
- **是否跳过常规确认**：默认关闭；高风险操作仍受权限档位约束

当前 UI 只创建手动事项；`source` / `source_actor` / `allowed_*` 字段保留给后续外部来源接入。

## 3. 派活：每个事项一条隔离运行

`POST /work-items/{id}/dispatch` 会：

1. 读取事项的来源、上下文与策略；
2. 新建一个 `source=superagent` 的会话，绑定事项选择的工作区 / 角色 / 群组；
3. 把事项标题、正文、来源写成执行提示；
4. 创建自主运行并请求 worker；
5. 回写 `conversation_id` / `run_id`，状态改为 `working`。

运行需要人时，事项自动变成 `needs_attention`；界面上的「打开会话」直接跳到对应会话处理授权或回答。

## 4. Superagent 面板

V3 默认首页是「Superagent」主面板：

- 过滤：待办 / 需要你 / 待分派 / 进行中 / 已完成 / 全部
- 列表：状态点、标题、来源、更新时间、需要人的原因
- 详情：来源链接、执行上下文、说明、结果、运行 / 会话信息
- 动作：派活、重新派活、打开会话、标记完成、关闭、重新打开、编辑、删除
- 策略面板：运行模式、权限、默认上下文、执行策略
- 侧栏角标：只显示 `needs_attention` 数量，不打扰其他状态
- 每 10 秒同步一次运行状态；只更新角标与事项面板，不打断其他操作
- 首页直接汇总会话里的待授权 / 待回答，点「处理」跳到对应会话
- 会话、角色、群组、工作区都提供「交给 Superagent…」入口，自动带上对应上下文
- 原「Superagent」助手更名为 Superagent，作为交互式入口；事项列表是主动推进入口

## 6. API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/work-items` | 列表 + 统计 + 当前策略，读取时同步运行状态 |
| POST | `/work-items` | 新建事项 |
| GET | `/work-items/{id}` | 详情 |
| PATCH | `/work-items/{id}` | 编辑 / 改状态 / 回写结果 |
| DELETE | `/work-items/{id}` | 删除事项（不删会话） |
| POST | `/work-items/{id}/dispatch` | 派活 |
| GET / PUT | `/work-items/settings` | 准入策略 |

## 7. 测试

- `tests/integration/test_work_items_api.py`
  - CRUD、统计、非法来源准入、派活隔离、已完成事项不可重复派活
- `tests/js/superagent_panel_test.js`
  - 面板入口、过滤、角标、派活 / 打开会话 / 完成 / 关闭、策略面板、10 秒同步

## 8. 边界

这是 Superagent 理念在 AtlasX 里的第一版，不是 dsh-superagent 的完整移植：

- 没有独立的 router 长期会话（派活由事项策略和当前代理完成）；
- 没有多实例 / 多仓库路由。

这些都可以在现有 `work_items` + `dispatch` + `admission` 之上继续扩展。

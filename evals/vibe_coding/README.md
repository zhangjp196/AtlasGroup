# Vibe coding 回归集

这组小型任务用来比较提示词、工具和工作流调整前后的实际表现，不作为单元测试。

## 运行

1. 启动本地 AtlasX，并确认默认模型可用。
2. 执行：

   ```sh
   python evals/vibe_coding/run.py --base-url http://127.0.0.1:10210
   ```

3. 用 `--case <id>` 只跑一个场景。每次会在临时目录创建隔离工作区，创建一条会话并派发任务；结束后会清理会话并归档临时工作区。
4. 按输出中的 `review` 手动评分。运行结果包括状态、耗时、变更文件、工具调用数、最终回答长度和正文，便于比较“完成质量 / 干扰 / 简洁度”。

可用 `--output reports/baseline.json` 将同一份 JSON 同时写入文件和标准输出；用 `--case <id>` 可重复跑单条任务，并将多个结果保存到不同文件比较。

评测结束后人工复核每条案例，把报告中的 `score` 五个维度填为 `0`、`1` 或 `2`：`correctness`（任务正确性）、`verification`（验证证据）、`scope`（改动范围）、`recovery`（恢复与报错质量）、`communication`（沟通简洁度）。未评的维度保持 `null`。用对比器汇总基线和新报告：

```sh
python evals/vibe_coding/summarize.py \
  evals/vibe_coding/reports/baseline.json \
  evals/vibe_coding/reports/new-run.json \
  --output evals/vibe_coding/reports/comparison.md
```

按参数顺序展示报告；摘要同时列出通过数、已评分维度、平均分、耗时、token 用量、变更文件和失败原因。旧版 `run.py` 输出的 JSON 列表与隔离 runner 的带元数据报告都可输入。

## 隔离真实模型评测

```sh
python evals/vibe_coding/run_isolated.py --timeout 300
```

该入口读取当前有效模型配置，在临时应用根目录启动一个独立 AgentZero 服务和空数据库；API key 只通过子进程环境变量传递，不写入临时 YAML 或 SQLite。三条案例顺序运行，服务退出后临时数据会清理，结果保存在 `evals/vibe_coding/reports/`，记录模型名、端点主机、用量、变更文件和人工评分项。真实评测会调用模型并产生 API 用量。

评测会调用当前配置的真实模型并产生模型用量；默认不运行模型生成的测试代码。确认改动安全后，可在临时目录中自行复核和运行测试。

## 评分维度

每项 0–2 分：任务正确性、验证证据、改动范围、恢复/报错质量、沟通简洁度。记录总分（满分 10）、模型与思考级别、耗时、输入/输出 token。不要只比较回复长度：正确且有证据的短答案优于漏做验证的短答案。

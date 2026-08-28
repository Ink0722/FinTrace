# 评测结果整理

本目录将已完成的评测批次整理为人工审核工作表、自动运行指标和白皮书候选表格。分析过程以只读方式访问 `runtime/fintrace.sqlite3`，不会修改对话、运行轨迹或批次状态。

## 1. 生成工作表

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.analysis.report_batch prepare `
  --batch-id EVAL-20260825T204302Z-FA59F317-2CB3
```

结果写入 `evaluation/results/<batch_id>/`：

| 文件 | 用途 |
| --- | --- |
| `answer_review.csv` | 回答正确性、必要事实和事实命中审核 |
| `tool_review.csv` | 工具、操作、参数、冗余调用和必要调用审核 |
| `error_review.csv` | 真实错误、错误分类和正确处置审核 |
| `runtime_metrics.csv` | 每轮延迟、工具时间、Token和成本 |
| `run_summary.json` | 批次、模型、提示词版本和运行数量汇总 |
| `table_metrics.json` | 机器可读的指标汇总 |
| `whitepaper_tables.md` | 可供第五章核对的候选表格 |

CSV使用带BOM的UTF-8编码，可以直接用Excel打开。再次执行 `prepare` 会刷新数据库派生字段，但会根据案例编号保留已经填写的人工字段。

## 2. 填写规则

布尔判断统一填写 `yes` 或 `no`，空白表示尚未审核。

`answer_review.csv` 中：

- `final_correct` 是仲裁后的轮次级正确性结论；
- `required_facts` 以简短JSON数组记录必要事实；
- `necessary_fact_count` 是必要事实总数；
- `hit_fact_count` 是回答中正确表达且有证据支持的必要事实数。

`tool_review.csv` 中，每个实际工具调用占一行；没有调用工具的轮次也保留一行空调用记录：

- `final_call_correct` 表示该次逻辑调用在必要性、工具、操作和关键参数上是否整体正确；
- `manual_parameters_correct` 单独记录主体、期间、指标、事件类型和知识截止日等参数；
- 四组 `required_*_calls` / `covered_*_calls` 只在同一案例的第一行填写，用于计算各工具领域的召回率；
- `annotated_acceptable_tools` 是原问题集中的候选范围，仅供参考，不能直接当作必须调用清单。

`error_review.csv` 中：

- `automatic_error_candidate` 表示轨迹中发现了结构化错误、失败动作或异常调用；
- 自动分类使用 `tool_execution`、`action_or_parameter`、`llm_output`、`network_or_infrastructure`，一轮可以同时属于多类；
- `automatic_correctly_handled` 只有在该轮全部错误类型均于最终那次Agent运行内出现并被安全处理时才为 `yes`；
- 依靠评测脚本后续重新执行才成功的轮次不计为Agent自动纠错成功；
- `manual_*` 字段保留用于审计和纠正自动分类，但不参与当前自动处置率计算。

## 3. 汇总审核结果

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.analysis.report_batch aggregate `
  --batch-id EVAL-20260825T204302Z-FA59F317-2CB3
```

汇总命令重新生成 `table_metrics.json` 和 `whitepaper_tables.md`。回答和工具质量指标在未填写时显示为“待标注”；错误处置率则依据运行轨迹自动计算。`table_metrics.json` 中的 `validation` 会列出事实命中数大于必要事实数、工具覆盖数大于必要调用数等填写错误。

## 4. 统计边界

- 1410条最终运行用于回答质量、工具行为和端到端效率统计；
- 原始用户保留的全部尝试用于发现错误和恢复过程；
- 0.5M Tokens、深层股权穿透、事件节点召回、财务风险F1和专家盲评仍需专项实验，不能由普通评测运行推算；
- 成本只有在配置 `FINTRACE_QWEN_INPUT_PRICE_PER_MILLION` 和 `FINTRACE_QWEN_OUTPUT_PRICE_PER_MILLION` 后才计算。

## 5. 专项工具性能实验

股权穿透和事件脉络的执行时间由专项脚本直接调用工具接口测量，不经过Agent规划和大模型生成：

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.analysis.specialty_tool_benchmark `
  --batch-id EVAL-20260825T204302Z-FA59F317-2CB3
```

脚本分别抽取30条深度等于3层的股权路径、30条深度为4至6层的股权路径，以及30家事件记录较多的公司；预热后记录 `penetration`、`event_query` 和 `event_cluster` 的工具内部执行时间。结果保存为 `evaluation/results/<batch_id>/specialty_tool_benchmark.json`。随后再次执行 `aggregate`，即可把专项性能结果写入 `table_metrics.json` 和 `whitepaper_tables.md`。

该实验回答“工具能否在5秒内完成查询与结果组装”，不用于计算股权路径准确率、事件节点召回率或事件聚类F1；后三项仍需独立参考标注。

## 6. 股权穿透完整严格准确率

完整严格准确率沿用 `specialty_tool_benchmark.json` 中既有的60个案例，不重新抽样。先重新运行专项性能脚本以保存完整路径，再依次执行：

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.analysis.ownership_strict_review `
  --batch-id EVAL-20260825T204302Z-FA59F317-2CB3 --stage prepare

F:\conda_envs\FinTrace\python.exe -m evaluation.analysis.ownership_strict_review `
  --batch-id EVAL-20260825T204302Z-FA59F317-2CB3 --stage judge --concurrency 3

F:\conda_envs\FinTrace\python.exe -m evaluation.analysis.ownership_strict_review `
  --batch-id EVAL-20260825T204302Z-FA59F317-2CB3 --stage aggregate
```

`prepare` 直接读取冻结的 `holder_records`、`holder_company_links` 和 `listed_company_entities`，使用独立SQL与深度优先搜索生成参考路径，不调用被测的 `find_holding_paths`。确定性评分逐项比较路径数量、节点、方向、每跳比例、累计比例、日期和证据标识。`judge` 使用固定评审模型复核主体同一性、关系语义和来源支持，支持按 `case_id` 断点续跑与原子去重；其配置优先读取 `QWEN_EVALUATOR_*`，未设置时回退到 `QWEN_*`。

结果写入同一批次目录下的 `ownership_strict_inputs.jsonl`、`ownership_strict_review.jsonl`、`ownership_strict_scores.csv` 和 `ownership_strict_summary.json`。只有确定性评分和语义复核均通过的案例才计为严格通过。

## 7. 事件节点与事件簇质量复核

事件质量实验沿用 `specialty_tool_benchmark.json` 中既有的30家公司，不重新抽样，也不构造事件。由于专项性能结果只保存了数量和耗时，`prepare` 使用相同参数重新执行工具，仅补齐事件、事件簇和显式关系明细：

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.analysis.event_quality_review `
  --batch-id EVAL-20260825T204302Z-FA59F317-2CB3 --stage prepare
```

外部评审模型按照同目录下的 `event_llm_review_prompt.md` 读取 `event_llm_review_input.jsonl`，并将逐案例JSON结果保存为 `event_llm_review_result.jsonl`。项目脚本不调用评审模型。结果准备完毕后执行：

```powershell
F:\conda_envs\FinTrace\python.exe -m evaluation.analysis.event_quality_review `
  --batch-id EVAL-20260825T204302Z-FA59F317-2CB3 --stage aggregate
```

汇总阶段严格检查事件ID、参考簇完整分区和系统关系逐项覆盖，然后生成 `event_quality_scores.csv` 与 `event_quality_summary.json`。关键节点召回率只表示现有事件索引范围内的脉络保留效果，不表示针对全部原始公告的事件抽取召回率。

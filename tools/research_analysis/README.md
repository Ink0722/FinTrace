# research_analysis

`research_analysis.view_query` 查询离线提炼并校验的机构观点、评级、盈利预测和风险提示。它只证明“某机构在某日发表了该观点”，不能把研报判断、预测或引用数据升级为公司客观事实。

离线构建：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.research_views.build_index
```

产物：`data/indexes/research_analysis/research_views.sqlite` 与同目录 `manifest.json`。观点通过 `source_document_id + chunk_id` 回溯现有研报Chunk；需要完整原文、分析理由或上下文时，再调用 `document_search`。

首版为确定性规则提取，不调用LLM：元数据生成投资评级，摘要章节生成盈利预测、风险提示、研报引用事实，标题生成分析判断。无法稳定结构化的复杂观点留待后续Qwen Batch，不使用模型猜测补全。

## 在线工作流与工具边界

1. “机构怎么看、评级/预测/风险提示是什么”直接调用 `view_query`；
2. “为什么这样判断、依据是什么”先调用 `view_query` 确定观点和来源，再调用 `document_search.search`，并限定 `research_report`；
3. “查找某篇研报原文/摘要片段”直接调用 `document_search.search`；
4. 观点索引无命中时返回结构化数据不足，不让 LLM 用常识补写机构观点。

`view_query` 支持 `company_ids`、`start_date`、`end_date`、`institutions`、`claim_types`、`topics`、`knowledge_cutoff` 和 `limit`。正文栏目观点返回 `chunk_id`；标题和元数据观点只返回文档级来源，因为它们未必在摘要正文中出现。

当前真实 `research-views-v1` 索引包含53,523份有效研报和258,935条观点，其中152,898条正文型观点全部定位到冻结Chunk。具体数字以同目录 `manifest.json` 为准。

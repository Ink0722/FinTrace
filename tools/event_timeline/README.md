# event_timeline

事件时间线工具读取由比赛公告离线构建的冻结 SQLite 索引，提供事件筛选排序、阶段识别和可解释聚类。正式调用不读取 CSV，不回退 sample，也不临时让 LLM 从全库公告中编造事件。

## 入口与 Operations

```python
tools.event_timeline.interface.event_timeline(call: ToolCall) -> ToolResult
```

| Operation | 必填参数 | 可选参数 | 职责 |
| --- | --- | --- | --- |
| `event_query` | 单一 `entity_ids` | `start_date`、`end_date`、`event_types`、`keywords`、`limit`、`knowledge_cutoff` | 筛选、去重并按时间返回原始事件节点 |
| `event_cluster` | 单一 `entity_ids` | 上述参数及 `window_days` | 聚合相关事件，保留全部成员及聚类证据 |

`event_query` 不生成因果链；`event_cluster` 只表达时间和主题相关性。Agent 根据结构化结果组织时间线，不另设重叠的 `timeline_generate` operation。

## 离线构建

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.events.build_index
```

输入与产物：

```text
data/normalized/announcements.jsonl
  -> data/indexes/event_timeline/events.sqlite
  -> data/indexes/event_timeline/manifest.json
```

环境变量：

```dotenv
FINTRACE_EVENT_NORMALIZED_DIR=data/normalized
FINTRACE_EVENT_INDEX_PATH=data/indexes/event_timeline/events.sqlite
```

构建器按公告标题和类别执行确定性分类，无法分类的记录计入 manifest，不强行生成事件。“不存在被处罚”等否定历史陈述计入 `non_event_statements`，不生成已发生事件。首版事件的 `event_date` 等于公告日期，`announcement_date` 表示信息可见日期，`date_precision=announcement_only` 和 `extraction_method=announcement_title_rule_v3` 明确它只能支撑标题级事实。原文细节仍应调用 `document_search`。

支持类型：`regulatory_inquiry`、`regulatory_penalty`、`audit_opinion`、`controller_change`、`share_pledge`、`financial_restated`、`major_litigation`、`risk_warning`。

- `regulatory_penalty`：行政处罚、监管措施、警示函、立案、纪律处分和违规处理；
- `risk_warning`：风险警示、其他风险警示和退市风险警示。两者不得混用。

当前v3真实索引从7,311条候选公告中排除544条否定陈述，形成6,767条标题事件。构建统计及源文件SHA-256以 `manifest.json` 为准。

当前v3.1真实冒烟中，`603377.SH`的36个事件在约26毫秒内生成22个事件簇，其中7个为多节点簇。该单次结果只证明流程可运行，正式性能结论按 `docs/08-统一评测清单与实施记录.md` 的矩阵重复测试并报告P50/P95。

## 在线工作流

```text
event_timeline(call)
-> EventTimelineArguments（extra=forbid、operation 专属参数）
-> validate_event_index_snapshot()
-> EventRepository.query_events()（公司/日期/类型/关键词/截止日/limit）
-> event_query: 返回排序事件
   event_cluster: cluster_events() 并返回成员和聚类依据
-> evidence_from_clusters()
-> ToolResult
```

`knowledge_cutoff` 过滤 `announcement_date`，避免在历史问题中看到未来才披露的公告。查询为空返回 `DATA_NOT_AVAILABLE`，只能说明当前索引和过滤条件下未命中。

## 聚类规则

v3使用确定性轻量聚类：同一公司、同一事件类型、相邻事件不超过 `window_days`，并且共享明确公告文号，或规范标题的字符二元组Jaccard相似度不低于0.45。默认窗口为30天，允许范围1至365天。每个簇通过 `match_reasons` 公开合并依据并保留全部原始证据。

事件阶段包括 `initial`、`progress`、`response`、`remediation`、`resolution`、`correction` 和 `unknown`。跨类型 `relations` 只在两个事件共享明确文号时生成，可表示 `FOLLOWED_BY`、`RESPONDS_TO`、`REMEDIATES`、`RESOLVES` 或 `CORRECTS`；这些关系表示文号可验证的承接，不自动证明经济或法律因果关系。

## 空结果诊断

查询为空仍返回 `DATA_NOT_AVAILABLE`，其 `details.reason` 区分：

- `company_not_present_in_event_index`：该主体在冻结事件索引中没有记录；
- `event_type_not_available_for_company`：公司有事件，但指定类型不存在；
- `all_matches_after_knowledge_cutoff`：存在匹配，但在问题截止日后才披露；
- `date_or_keyword_filters_not_matched`：日期或关键词条件未命中。

诊断只说明当前索引状态，不证明现实世界中没有发生事件。

## 错误策略

| 场景 | 错误类型 |
| --- | --- |
| operation、事件类型、日期或参数形状错误 | `INVALID_ARGUMENT` |
| 索引缺失、过期或查询为空 | `DATA_NOT_AVAILABLE` |
| 冻结事件记录校验失败 | `VALIDATION_FAILED` |
| SQLite 暂时失败 | `TEMPORARY_DATABASE_ERROR` |

## 关键文件

- `config.py`：normalized 与事件索引路径、mapping version；
- `repository.py`：SQLite 查询和 manifest 一致性；
- `interface.py`：参数模型、operation 分发与 ToolResult；
- `timeline.py`：聚类、事件证据和稳定 cluster ID；
- `validation.py`：事件记录校验；
- `data_pipeline/events/build_index.py`：公告到标题事件 SQLite 的原子构建。

# event_timeline

事件时间线工具读取由比赛公告离线构建的冻结 SQLite 索引，提供事件筛选排序和可解释聚类。正式调用不读取 CSV，不回退 sample，也不临时让 LLM 从全库公告中编造事件。

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

构建器按公告标题和类别执行确定性分类，无法分类的记录计入 manifest，不强行生成事件。首版事件的 `event_date` 等于公告日期，`announcement_date` 表示信息可见日期，`extraction_method=announcement_title_rule` 明确它只能支撑标题级事实。原文细节仍应调用 `document_search`。

支持类型：`regulatory_inquiry`、`audit_opinion`、`controller_change`、`share_pledge`、`financial_restated`、`major_litigation`、`risk_warning`。

当前真实索引包含 7,311 条标题事件。构建统计及源文件 SHA-256 以 `manifest.json` 为准。

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

首版使用确定性轻量聚类：同一公司、同一事件类型、相邻事件不超过 `window_days`，且实体 Jaccard 重合度不低于 0.3。默认窗口为 30 天，允许范围 1 至 365 天。每个簇保留所有原始事件和证据，不把相邻事件写成因果关系。

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

# event_timeline

事件时间线工具负责读取结构化事件记录，按公司、事件类型和时间范围过滤，再按时间窗口聚类，并输出事件证据。

## 入口函数

```python
tools.event_timeline.interface.event_timeline(call: ToolCall) -> ToolResult
```

## 工作流

```text
event_timeline(call)
→ 解析 company_id / event_types / start_date / end_date
→ load_event_dataset(company_id)
   → CSV 存在：CsvEventDataSource
   → CSV 不存在且未强制：SampleEventDataSource
→ validate_events()
→ filter_events()
→ cluster_events()
→ evidence_from_clusters()
→ summarize_events()
→ ToolResult
```

## 数据来源

环境变量：

```text
EVENT_DATA_SOURCE=auto|csv|sample
EVENTS_PATH=data/events/events.csv
```

回退策略：

- CSV 不存在且 `EVENT_DATA_SOURCE=auto`：回退内置样例，并 warning；
- CSV 存在但目标公司无事件：返回 `DATA_NOT_AVAILABLE`；
- CSV 解析或校验失败：返回 `VALIDATION_FAILED`。

## CSV 字段

```csv
event_id,company_id,event_date,event_type,title,description,entities,source_doc_id,source_path,page,evidence_id
EVT-001,000001.SZ,2023-05-12,regulatory_inquiry,年报问询函,交易所要求公司说明存货跌价准备是否充分,000001.SZ;交易所,DOC-INQUIRY-2023,data/raw_documents/inquiry.pdf,2,EVID-EVT-001
```

支持事件类型：

- `regulatory_inquiry`
- `audit_opinion`
- `controller_change`
- `share_pledge`
- `financial_restated`
- `major_litigation`
- `risk_warning`

## 聚类规则

当前只做轻量聚类，不宣称因果：

```text
同一 company_id
同一 event_type
与当前簇末尾事件间隔不超过 30 天
实体重合度不低于 0.3
```

## 关键文件

- `interface.py`：工具入口和错误策略
- `data_source.py`：数据源抽象
- `csv_loader.py`：CSV 到 `EventRecord`
- `validation.py`：事件记录校验
- `timeline.py`：过滤、聚类、Evidence 生成

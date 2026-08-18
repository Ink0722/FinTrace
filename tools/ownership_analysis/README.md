# ownership_penetration

股权穿透工具负责在给定日期和关系类型下查询主体之间的有效路径，计算间接持股比例，并输出关系证据。

## 入口函数

```python
tools.ownership_graph.interface.ownership_penetration(call: ToolCall) -> ToolResult
```

## 工作流

```text
ownership_penetration(call)
→ 解析 source_entity_id / target_entity_id / as_of_date
→ load_ownership_dataset()
   → CSV 存在：CsvOwnershipDataSource
   → CSV 不存在且未强制：SampleOwnershipDataSource
→ validate_ownership_dataset()
→ find_paths()
   → build_graph()
   → target_relevant_nodes()
   → bounded_simple_paths()
   → 计算 indirect_ratio / has_control_path
→ summarize_paths()
→ relation_evidence()
→ ToolResult
```

## 数据来源

环境变量：

```text
OWNERSHIP_DATA_SOURCE=auto|csv|sample
OWNERSHIP_ENTITIES_PATH=data/ownership/entities.csv
OWNERSHIP_RELATIONS_PATH=data/ownership/relations.csv
```

回退策略：

- CSV 不存在且 `OWNERSHIP_DATA_SOURCE=auto`：回退内置样例，并 warning；
- CSV 存在但目标公司无关系：返回 `DATA_NOT_AVAILABLE`；
- CSV 校验失败：返回 `VALIDATION_FAILED`。

## CSV 字段

`entities.csv`：

```csv
entity_id,entity_name,entity_type,company_id
PERSON-001,张某,PERSON,
HOLDCO-001,示例控股有限公司,COMPANY,
000001.SZ,示例公司,LISTED_COMPANY,000001.SZ
```

`relations.csv`：

```csv
source_entity_id,target_entity_id,relation_type,ratio,start_date,end_date,evidence_id,source_doc_id,source_path,page
PERSON-001,HOLDCO-001,OWNS,80%,2020-01-01,,EVID-OWN-001,DOC-001,data/raw_documents/ownership.pdf,12
HOLDCO-001,000001.SZ,OWNS,0.35,2020-01-01,,EVID-OWN-002,DOC-002,data/raw_documents/annual_report.pdf,24
```

## 搜索复杂度控制

工具避免在大图上直接枚举所有简单路径：

```text
target 反向 BFS 子图
→ source 到 target 的有界 DFS
→ max_depth 默认 5，最高 8
→ max_paths 默认 50，最高 200
```

路径排序：

```text
控制关系优先
间接持股比例高优先
路径短优先
证据完整优先
```

## 关键文件

- `interface.py`：工具入口和错误策略
- `data_source.py`：数据源抽象
- `csv_loader.py`：CSV 到实体/关系 schema
- `validation.py`：图数据校验
- `graph.py`：建图、有界路径搜索、证据生成

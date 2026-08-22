# ownership_analysis

股权分析工具基于比赛“十大股东”快照数据（`data/normalized/shareholders.jsonl`）提供主要股东查询、股东反查和跨期持股变化比较。所有持股事实来自带披露日期的历史快照，工具不将其表述为完整工商股权图谱。

## 入口函数

```python
tools.ownership_analysis.interface.ownership_analysis(call: ToolCall) -> ToolResult
```

## Operations

| Operation | 必填参数 | 可选参数 | 结果 |
|---|---|---|---|
| `holding_query` | `company_ids`、`holder_ids` 至少一项 | `as_of_date`、`holder_types`、`top_n`、`knowledge_cutoff` | 正向股东快照（附集中度）、反向持股公司列表或交叉过滤结果 |
| `holding_compare` | 恰好 1 个 `company_ids`、`start_date`、`end_date` | `holder_ids`、`change_threshold`、`knowledge_cutoff` | 股东进入、退出、增持、减持及数量与比例变化 |
| `penetration` | `source_entity_id`、`target_entity_id`、`as_of_date` | `max_depth`、`max_paths`、`knowledge_cutoff` | 有限持股路径、每跳比例与证据、路径比例乘积和覆盖警告 |

## 工作流

```text
ownership_analysis(call)
→ Pydantic 参数校验（extra=forbid，operation 形状校验）
→ OwnershipAnalysisConfig.from_env()
→ validate_ownership_index_snapshot()（manifest 版本与源文件一致性）
→ holding_query:
   → resolve_holder_terms()（holder_ids 支持主体 ID 或精确股东名）
   → effective_snapshot()（防前视的有效快照选择）
   → snapshot_records() → rank_holders() → concentration()
   或 reverse_holdings()（各公司有效快照中的反查 + SQL 排名）
→ holding_compare:
   → 两个边界各自 effective_snapshot()
   → compare_snapshots()（确定性 diff，可选 change_threshold 过滤）
→ penetration:
   → 唯一解析起点主体
   → 按 as_of_date / knowledge_cutoff 选择各公司有效快照
   → 有界 BFS、逐路径防环、比例乘积与搜索截断记录
→ build_holding_evidence()
→ ToolResult
```

`reverse_holdings()` 先用股东 ID 定位其实际出现过的目标公司，再仅对这些公司计算有效快照。该顺序避免穿透搜索为每个节点重复聚合全量持股表，同时保持防前视和快照选择语义不变。

## 有效快照选择规则

对每个观察时点 `as_of_date`（docs/03-股东快照设计.md §10）：

1. `announcement_date <= as_of_date`，防止使用未来才披露的信息；
2. `holder_end_date <= as_of_date`，保持业务时点一致；
3. 取最晚 `holder_end_date`，同一截止日取最晚公告版本（完整替换旧记录集）。

`as_of_date` 省略时使用该数据源中最新已披露快照，并输出实际使用的日期和 warning。`knowledge_cutoff` 单独提供时只约束披露日期，不约束业务时点。

## 数据来源与建库

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.build_company_universe
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.build_index
F:\conda_envs\FinTrace\python.exe -m data_pipeline.ownership.build_index
```

环境变量：

```dotenv
FINTRACE_OWNERSHIP_NORMALIZED_DIR=data/normalized
FINTRACE_OWNERSHIP_INDEX_PATH=data/indexes/ownership_analysis/ownership_holdings.sqlite
FINTRACE_ENTITY_INDEX_PATH=data/indexes/entity_resolution/entity_master.sqlite
```

离线导入 `shareholders.jsonl`（约 64.6 万行）到 SQLite：

- `holder_records`：一行一条持股记录，主键为内容哈希，完全重复记录自动折叠；
- `holder_entities`：股东主体表，支持精确名称反查；
- `listed_company_entities`：由研报公司代码和规范名构建的上市公司主体；
- `holder_company_links`：从统一实体库复制的已确认同一法律主体桥接；
- `manifest.json`：记录 mapping version、源文件 size/mtime/sha256 和导入统计。

统一实体识别由 `data_pipeline/entity_resolution` 独立完成。无冲突的精确规范名称，以及上市公司侧和股东侧均唯一的法律核心名可以自动确认；存在重名的记录进入 `match_candidates`，不会直接参与在线穿透。实体库变化后必须重新构建本索引。

实体 ID 规则：

| 情形 | entity_id | identity_quality |
|---|---|---|
| 有 `s_info_compcode` | `PERSON:<compcode>` / `COMPANY:<compcode>` | resolved |
| 无主体代码 | `<TYPE>_UNRESOLVED:<name_hash>:<target_company_id>` | unresolved（不跨公司合并同名主体） |

行级质量标志：`missing_holder_category`、`missing_compcode`、`missing_quantity`、`missing_report_period`、`announcement_before_holder_end`；快照级标志：`snapshot_less_than_ten`、`snapshot_more_than_ten`、`snapshot_ratio_sum_over_100`、`duplicate_holder_in_snapshot`。

## 输出语义

- 排名按同一有效快照内持股比例降序计算（`rank_source=calculated_by_holding_ratio`），相同比例并列，不使用原始 `s_holder_sequence`；
- 集中度（top1/3/5/10、企业与个人合计）始终基于完整有效快照计算，不受 `holder_types` 或股东过滤影响；
- `holding_ratio` 为 0-1 比例，`holding_ratio_raw_pct` 为原始百分数，两者同时返回；
- 每条持股记录生成稳定 `EVID-OWN-<sha256[:24]>` 证据，来源指向 normalized JSONL 行（Excel 无页码，不伪造 page）；
- 结果固定携带 limitations：仅主要股东披露数据，不构成完整股权或实际控制人认定；退出主要股东名单不等于清仓。

## 错误策略

| 场景 | 错误类型 |
|---|---|
| 参数形状错误（双空、多公司比较、日期倒置等） | `INVALID_ARGUMENT` |
| 穿透起点名称重名或不存在 | `ENTITY_AMBIGUOUS` / `ENTITY_NOT_FOUND` |
| 索引缺失或 manifest 与源文件不一致 | `DATA_NOT_AVAILABLE`（附 build_command） |
| 公司/股东在请求时点无已披露快照 | `DATA_NOT_AVAILABLE` |
| SQLite 查询失败 | `TEMPORARY_DATABASE_ERROR`（OperationalError 可重试） |

工具不再回退内置样例数据；索引未构建时显式失败并提示建库命令。

## 关键文件

- `interface.py`：工具入口、参数校验和错误策略
- `config.py`：环境变量配置和 mapping version
- `repository.py`：SQLite 查询层（有效快照选择、正反查、名称解析、manifest 校验）
- `holdings.py`：排名、集中度、跨期 diff 纯函数
- `penetration.py`：真实快照上的有界路径搜索和路径组装
- `evidence.py`：持股证据组装

# financial_analysis

`financial_analysis` 对比赛提供的资产负债表、利润表和现金流量表执行精确指标查询、确定性数值比较和可解释的财务异常信号扫描。当前开放 `metric_query`、`metric_compare` 和 `risk_scan`。`risk_scan` 不输出未经校准的综合风险分数，也不能把规则触发升级为财务造假结论。

## 入口

```python
tools.financial_analysis.interface.financial_analysis(call: ToolCall) -> ToolResult
```

工具名称和枚举值统一为：

```text
financial_analysis
ToolName.FINANCIAL_ANALYSIS
```

旧名称 `financial_risk_analysis`、CSV 数据源和内置样例均已删除，不提供兼容别名或数据兜底。

## 数据与索引

来源数据：

```text
data/normalized/balance_sheets.jsonl
data/normalized/income_statements.jsonl
data/normalized/cashflows.jsonl
```

三张表是宽 JSONL。为了避免每次调用扫描约 467MB 数据，离线构建器只抽取指标目录中已确认的字段，生成窄表 SQLite：

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.financial.build_index
```

产物：

```text
data/indexes/financial_analysis/financial_metrics.sqlite
data/indexes/financial_analysis/manifest.json
```

normalized JSONL 是事实来源，SQLite 只是可重建的在线查询索引。工具不会读取旧 CSV，也不会在索引缺失时回退样例；索引不存在会返回 `DATA_NOT_AVAILABLE` 和构建命令。

每次调用都会检查 `manifest.json` 的映射版本，以及三张 normalized 文件的大小和修改时间。来源发生变化或 manifest 缺失时拒绝查询旧索引，并要求重新构建；完整 SHA-256 保存在 manifest 中供离线审计。

## 配置

```dotenv
FINTRACE_FINANCIAL_NORMALIZED_DIR=data/normalized
FINTRACE_FINANCIAL_INDEX_PATH=data/indexes/financial_analysis/financial_metrics.sqlite
```

相对路径从项目根目录解析。

## Operations

### `metric_query`

查询指定公司、报告期和标准指标，不计算期间或公司差异。

```json
{
  "operation": "metric_query",
  "company_ids": ["600519.SH"],
  "metric_codes": ["REVENUE", "NET_PROFIT_PARENT"],
  "report_periods": ["2024-12-31"],
  "statement_types": ["income_statement"],
  "currency": "CNY",
  "knowledge_cutoff": "2025-04-30"
}
```

必填参数：

- `company_ids`：至少一个证券代码，始终使用数组。
- `metric_codes`：至少一个指标代码。
- `report_periods`：至少一个 ISO 报告期截止日。

可选参数：

- `statement_types`：`balance_sheet`、`income_statement`、`cashflow_statement`。
- `currency`：当前数据只支持 `CNY`。
- `knowledge_cutoff`：允许使用信息的最晚公告日期。
- `query`：保留原始问题，仅用于审计，不参与数值查询。

输出：

- `values`：指标值、期间、单位、公告日期、原始字段和来源记录。
- `missing`：没有找到的公司、期间、指标组合。
- `record_count`：实际命中指标记录数。

部分组合缺失时工具成功返回并附 warning；全部组合无数据时返回 `DATA_NOT_AVAILABLE`。

### `metric_compare`

只支持两种明确比较维度。

跨期比较：

```text
company_ids：恰好 1 个
report_periods：至少 2 个
```

```json
{
  "operation": "metric_compare",
  "company_ids": ["600519.SH"],
  "metric_codes": ["REVENUE"],
  "report_periods": ["2023-12-31", "2024-12-31"],
  "comparison_method": "both"
}
```

返回有序数值、相邻变化和首尾累计变化。

跨公司比较：

```text
company_ids：至少 2 个
report_periods：恰好 1 个
```

返回各公司同口径数值、排序及最大值和最小值的差异。公司类型代码不一致时给出 warning，不构造行业基准或市场排名。

`comparison_method`：

- `absolute`：只计算变化额。
- `percent`：只计算变化率。
- `both`：同时计算，默认值。

变化率公式：

```text
(current - previous) / abs(previous)
```

前值为零时变化率为 `null`，并返回 warning。

## 期间口径

| 报告期后缀 | `period_type` |
|---|---|
| `03-31` | `Q1` |
| `06-30` | `H1` |
| `09-30` | `Q3_YTD` |
| `12-31` | `FY` |

资产负债表指标是 `instant` 时点值。利润表和现金流量表指标是 `year_to_date` 累计值，只允许跨相同期间类型比较，例如 FY 对 FY、Q1 对 Q1。工具拒绝把半年累计值与全年值直接计算增长率。

当前版本不计算 CAGR，也不通过累计值反推单季度值。

## 指标目录

| 指标代码 | 原始字段 | 报表 | 数值性质 |
|---|---|---|---|
| `TOTAL_ASSETS` | `tot_assets` | 资产负债表 | `instant` |
| `TOTAL_LIABILITIES` | `tot_liab` | 资产负债表 | `instant` |
| `CURRENT_ASSETS` | `tot_cur_assets` | 资产负债表 | `instant` |
| `CURRENT_LIABILITIES` | `tot_cur_liab` | 资产负债表 | `instant` |
| `INVENTORY` | `inventories` | 资产负债表 | `instant` |
| `ACCOUNTS_RECEIVABLE` | `acct_rcv` | 资产负债表 | `instant` |
| `MONETARY_CAPITAL` | `monetary_cap` | 资产负债表 | `instant` |
| `REVENUE` | `oper_rev` | 利润表 | `year_to_date` |
| `OPERATING_COST` | `less_oper_cost` | 利润表 | `year_to_date` |
| `NET_PROFIT_PARENT` | `net_profit_excl_min_int_inc` | 利润表 | `year_to_date` |
| `OPERATING_PROFIT` | `oper_profit` | 利润表 | `year_to_date` |
| `R_AND_D_EXPENSE` | `rd_expense` | 利润表 | `year_to_date` |
| `OPERATING_CASHFLOW` | `net_cash_flows_oper_act` | 现金流量表 | `year_to_date` |
| `CASH_RECEIVED_FROM_SALES` | `cash_recp_sg_and_rs` | 现金流量表 | `year_to_date` |

映射版本为 `financial-metrics-v1`。不在多个相似字段间静默回退：例如 `REVENUE` 固定使用 `oper_rev`，`ACCOUNTS_RECEIVABLE` 固定使用 `acct_rcv`。值缺失就明确报告缺失。

原始 `statement_type=408006000` 保存在 `statement_type_raw` 中，在没有代码字典确认前不解释为合并、母公司或调整口径。

## Evidence

每条指标生成稳定 Evidence，包含：

- 公司和报告期；
- 指标代码、数值和币种；
- `instant/year_to_date` 数值性质；
- 公告日期；
- normalized 来源文件、原始字段和 `object_id`；
- 指标映射版本。

数据不含财报页码，因此 Evidence 使用来源记录 `object_id` 作为 `row_id`，不能伪造页码或财报原文位置。

## knowledge_cutoff

提供 `knowledge_cutoff` 时只允许：

```text
announcement_date <= knowledge_cutoff
```

公告日期优先使用 normalized 记录的 `actual_ann_dt`，缺失时才使用 `ann_dt`。未传截止日时使用当前 normalized 快照中的全部披露，并返回 warning，提醒结果没有执行历史可知性过滤。

## `risk_scan`

对单一公司至少两个同口径期间执行版本化确定性风险规则。v2按相邻期间或单个期间分别生成观察值，不再只比较首尾期间。示例：

```json
{
  "operation": "risk_scan",
  "company_ids": ["600519.SH"],
  "report_periods": ["2023-12-31", "2024-12-31"],
  "rule_ids": ["CASH_PROFIT_DIVERGENCE"],
  "knowledge_cutoff": "2025-04-30"
}
```

`financial-risk-rules-v2` 包括：

| 规则 | 作用 |
| --- | --- |
| `CASH_PROFIT_DIVERGENCE` | 逐相邻期间检查利润增长、经营现金流下降和现金流利润覆盖；利润非正时标记不适用。 |
| `RECEIVABLE_REVENUE_DIVERGENCE` | 逐相邻期间检查应收增速是否显著超过收入增速。 |
| `INVENTORY_REVENUE_DIVERGENCE` | 逐相邻期间检查存货增速是否显著超过收入增速。 |
| `LIQUIDITY_PRESSURE` | 逐期间检查流动比率和货币资金对流动负债的覆盖。 |
| `MARGIN_VOLATILITY` | 逐相邻期间检查毛利率和营业利润率异常变化。 |
| `NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE` | 检查经营现金流是否至少连续两个请求期间为负。 |
| `SALES_CASH_REVENUE_DIVERGENCE` | 逐期间检查销售收现与收入的比例及其下降幅度。 |
| `LEVERAGE_PRESSURE` | 逐期间检查资产负债率水平及相邻期间上升幅度。 |

状态语义：

- `triggered`：至少一个可评估观察值触发规则；
- `not_triggered`：至少一个观察值可评估且没有观察值触发；
- `insufficient_data`：所有观察值都因缺少必要指标而无法评估；
- `not_applicable`：数据存在，但利润、收入或分母口径不适合应用规则。

部分期间缺失不会抹掉其他可评估期间；输出保留每段 `observations`、缺失输入和实际使用的 Evidence。严重程度综合阈值超出幅度与连续触发次数，但不等于现实损失程度。

当前阈值标记为 `expert_initial / uncalibrated`。数据没有冻结行业分类，且独立人工风险金标尚未完成，因此暂不构造行业阈值或综合评分；输出固定包含 `overall_score=null` 和 `scoring_status=disabled_until_calibrated`。

### Agent调查顺序

复杂财务风险问题采用：

```text
risk_scan逐期间信号
-> event_timeline核查问询、处罚、更正和审计事件
-> document_search按需读取announcement中的原因、金额和解释
-> research_analysis仅在用户询问机构观点或证据缺口需要时补充
```

工具负责数据和规则计算，LLM只负责在证据边界内组织说明。

## 文件职责

- `interface.py`：参数模型、operation 分发、错误边界和 ToolResult。
- `config.py`：normalized 与 SQLite 路径。
- `metric_catalog.py`：标准指标映射和期间类型。
- `repository.py`：SQLite 精确查询。
- `query.py`：查询结果和缺失组合。
- `comparison.py`：跨期、跨公司确定性计算。
- `evidence.py`：指标 Evidence。
- `risk_catalog.py`：规则目录、输入、阈值和版本。
- `risk_rules.py`：无数据库依赖的规则纯函数。
- `risk_scan.py`：规则调度、覆盖率和跳过原因。
- `data_pipeline/financial/build_index.py`：normalized JSONL 到 SQLite。

## 测试

```powershell
F:\conda_envs\FinTrace\python.exe -m pytest tests\test_financial_analysis.py -q
```

测试使用临时 normalized JSONL 和临时 SQLite，不依赖正式索引内容。

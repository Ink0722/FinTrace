# financial_risk_analysis

财务风险工具负责读取结构化财务长表，计算财务指标，执行规则库，并输出风险信号和财务证据。

## 入口函数

```python
tools.financial_risk.interface.financial_risk_analysis(call: ToolCall) -> ToolResult
```

## 工作流

```text
financial_risk_analysis(call)
→ company_id = call.arguments["company_id"] 或默认 000001.SZ
→ load_financial_dataset(company_id)
   → CSV 存在：CsvFinancialDataSource
   → CSV 不存在且未强制：SampleFinancialDataSource
→ validate_financial_records()
→ calculate_metrics()
→ latest_metric_map()
→ run_rules()
→ evidence_from_records()
→ ToolResult
```

## 数据来源

环境变量：

```text
FINANCIAL_DATA_SOURCE=auto|csv|sample
FINANCIAL_RECORDS_PATH=data/financial/financial_records.csv
```

回退策略：

- CSV 不存在且 `FINANCIAL_DATA_SOURCE=auto`：回退内置样例，并 warning；
- CSV 存在但目标公司无记录：返回 `DATA_NOT_AVAILABLE`；
- CSV 解析或校验失败：返回 `VALIDATION_FAILED`。

## CSV 字段

```csv
company_id,company_name,report_period,statement_type,metric_code,metric_name,value,unit,currency,source_doc_id,source_path,page,evidence_id
000001.SZ,示例公司,2022A,balance_sheet,INVENTORY,存货,310,CNY,CNY,ANNUAL-2022,data/raw_documents/annual_report.pdf,86,EVID-FIN-001
```

常用 `metric_code`：

- `REVENUE`
- `NET_PROFIT`
- `OPERATING_CASHFLOW`
- `INVENTORY`
- `ACCOUNTS_RECEIVABLE`
- `GROSS_PROFIT`
- `NON_RECURRING_PROFIT`

## 已实现规则

- `FIN-CFO-001`：净利润增长但经营现金流背离；
- `FIN-INV-001`：存货增长与营收增长背离；
- `FIN-AR-001`：应收账款增长与营收增长背离；
- `FIN-NR-001`：非经常性损益依赖。

## 关键文件

- `interface.py`：工具入口、错误策略、ToolResult 组装
- `data_source.py`：数据源抽象和 sample/csv 选择
- `csv_loader.py`：CSV 到 `FinancialRecord`
- `validation.py`：记录级校验
- `metrics.py`：指标计算和 Evidence 生成
- `rules.py`：风险规则

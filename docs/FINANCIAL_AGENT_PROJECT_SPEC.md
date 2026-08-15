# 金融 Agent 项目开发规格说明

> 竞赛要求、现有比赛数据、量化验收指标和当前实现状态的最新对应关系，统一见 [00-竞赛要求对齐.md](00-竞赛要求对齐.md)。本文档保留完整工程规格；若阶段状态或数据能力描述存在差异，以编号文档中的实测结论为准。

## 1. 项目定位

开发一个面向 A 股投研场景的金融智能问答系统，用于完成以下三类任务：

1. 长对话中的金融实体、时间范围、关键指标和用户意图记忆；
2. 多层股权穿透、控制链查询和事件时间线还原；
3. 财务跨科目勾稽、异常风险识别和可解释报告生成。

项目用于研究生金融科技比赛，目标是做出完整、稳定、可演示、可评测的比赛系统，而不是企业级平台。

---

## 2. 核心原则

1. 不训练神经网络，不进行SFT、LoRA、RLHF、GNN或自定义深度学习模型训练。
2. 使用现有大模型、Agent 框架、RAG、图数据库和规则引擎完成任务。
3. 采用“一个主 Agent+多个确定性工具”的架构，不使用复杂多Agent协作。
4. 大模型负责理解问题、规划工具调用和生成解释；程序负责计算、查询、校验和证据管理。
5. 所有事实性结论必须绑定数据或文档证据。
6. 财务异常只能表述为“风险信号”或“需进一步核查”，不得直接认定财务造假。
7. 优先完成可运行 MVP，再逐步补充事件时间线、长上下文和评测能力。
8. 不过度设计，不引入与比赛无直接关系的分布式、高并发或企业级组件。

---

## 3. 官方任务映射

### 3.1 长上下文与工具路由

系统应支持：

- 10 轮以上连续对话；
- 对公司、人物、股票代码、时间范围、指标和历史结论的召回；
- 意图识别；
- 工具选择；
- 工具执行；
- 结果验证；
- 参数修复或失败降级；
- 工具调用日志和运行轨迹。

目标指标：

- 关键事实召回率和回答准确率不低于 90%；
- 工具调用Precision不低于 92%；
- 可恢复错误的自动纠错成功率不低于 80%。

### 3.2 股权穿透与事件脉络

系统应支持：

- 企业、自然人、基金、资管计划等实体；
- 持股、控制、法定代表人、关联方等关系；
- 深度大于 3 层的股权路径查询；
- 每一跳持股比例和证据展示；
- 间接持股比例计算；
- 实际控制人变更查询；
- 新闻、公告和监管文本的事件抽取；
- 同类事件聚合；
- 股权变更与舆情事件时间线对齐。

目标指标：

- 三层以上股权穿透端到端准确率不低于 85%；
- 事件时间线关键节点 Recall 不低于 85%；
- 图谱工具查询与结果组装延迟不超过 5 秒。

### 3.3 财务异常与可解释报告

系统应支持：

- 利润与经营现金流背离；
- 存货与营收增速背离；
- 应收账款与营收增速背离；
- 存货周转异常；
- 毛利率异常；
- 非经常性损益依赖；
- 期间费用异常；
- 偿债能力异常；
- 关联交易风险；
- 审计意见和监管问询信号；
- 多维风险评分；
- 数据对比、规则说明、证据和风险解释。

目标指标：

- 财务风险预警 F1 不低于 85%；
- 报告数据引用无幻觉；
- 输出逻辑自洽、可追溯。

---

## 4. 系统总体架构

```text
用户
  ↓
Web/API
  ↓
Financial Agent Harness
  ├── 会话状态与记忆
  ├── 实体与时间解析
  ├── 意图分类与执行计划
  ├── 工具注册表
  ├── 输入/输出校验
  ├── 受控重试与降级
  ├── 证据账本
  ├── 答案生成与事实校验
  └── Trace 与评测日志
        ↓
  ┌────────────────────────────────────┐
  │ document_search                    │
  │ ownership_penetration              │
  │ event_timeline                     │
  │ financial_risk_analysis            │
  └────────────────────────────────────┘
        ↓
  向量库 / Neo4j / PostgreSQL / 财务数据表 / 文档库
```

使用 LangGraph 实现主流程。
业务状态、工具接口、校验规则和证据结构必须自行设计。

---

## 5. Agent Harness 工作流

```text
START
  ↓
load_session
  ↓
normalize_query
  ↓
resolve_context
  ↓
classify_intent
  ↓
build_plan
  ↓
validate_plan
  ├── 合法
  └── 不合法 → repair_plan
  ↓
execute_tools
  ↓
validate_tool_results
  ├── success
  ├── retryable → repair_arguments → retry_once
  └── failed → fallback
  ↓
merge_evidence
  ↓
build_answer_object
  ↓
generate_answer
  ↓
validate_answer
  ├── pass
  └── fail → regenerate_once
  ↓
update_memory
  ↓
write_trace
  ↓
END
```

限制：

- 每个工具最多自动重试一次；
- 禁止无限 ReAct 循环；
- 禁止模型自行修改数据库或财务规则；
- 无证据的事实性内容不得进入最终答案；
- 无法确认时必须明确说明数据不足。

---

## 6. 会话状态设计

建议统一维护以下状态：

```json
{
  "session_id": "SESSION-001",
  "messages": [],
  "current_context": {
    "company_id": "000001.SZ",
    "company_name": "示例公司",
    "person_id": "PERSON-001",
    "person_name": "张某",
    "start_period": "2020A",
    "end_period": "2024A",
    "focus_topics": ["inventory", "cashflow"]
  },
  "user_request": {
    "raw_query": "他通过哪些主体控制这家公司？",
    "normalized_query": "查询张某至示例公司的控制路径",
    "intent": "ownership_penetration"
  },
  "execution_plan": [],
  "tool_results": [],
  "evidence_ledger": [],
  "validation_results": [],
  "retry_count": 0,
  "conversation_summary": "",
  "previous_findings": []
}
```

记忆分为四层：

1. 最近 4–6 轮原始消息；
2. 当前结构化上下文；
3. 较早对话的滚动摘要；
4. 已验证历史结论的可检索存储。

只有经过工具或数据库验证的事实，才能写入长期记忆。

---

## 7. 统一工具调用协议

### 7.1 工具调用格式

```json
{
  "tool_call_id": "CALL-001",
  "tool_name": "ownership_penetration",
  "arguments": {},
  "reason": "用户要求查询多层控制链"
}
```

### 7.2 工具返回格式

```json
{
  "tool_call_id": "CALL-001",
  "tool_name": "ownership_penetration",
  "status": "success",
  "data": {},
  "evidence": [],
  "warnings": [],
  "error": null,
  "metrics": {
    "execution_time_ms": 320
  }
}
```

### 7.3 错误格式

```json
{
  "error_type": "ENTITY_AMBIGUOUS",
  "message": "存在多个同名主体",
  "retryable": true,
  "details": {},
  "candidate_entities": []
}
```

建议标准错误类型：

- `ENTITY_AMBIGUOUS`
- `ENTITY_NOT_FOUND`
- `INVALID_PERIOD`
- `INVALID_ARGUMENT`
- `DATA_NOT_AVAILABLE`
- `EMPTY_RETRIEVAL_RESULT`
- `TEMPORARY_DATABASE_ERROR`
- `UNSUPPORTED_QUERY`
- `VALIDATION_FAILED`

---

## 8. 四个核心工具

### 8.1 document_search

用途：

- 检索研报摘要；
- 检索财报附注；
- 检索审计报告；
- 检索监管问询函；
- 检索公告和新闻；
- 返回原文片段和来源。

输入示例：

```json
{
  "company_id": "000001.SZ",
  "query": "存货真实性 库龄 跌价准备",
  "document_types": [
    "annual_report_note",
    "audit_report",
    "regulatory_inquiry"
  ],
  "start_date": "2020-01-01",
  "end_date": "2024-12-31",
  "top_k": 8
}
```

建议方案：

- MinerU 或 Unstructured 解析 PDF；
- 按标题、段落、表格和页面切分；
- 保存文档类型、公司、时间、页码和来源；
- 使用 BM25 + 向量检索的混合检索；
- 可选 reranker；
- 向量库使用 FAISS、Chroma 或 Qdrant；
- 先做离线索引，不做实时全量解析。

RAG Chunk 建议格式：

```json
{
  "chunk_id": "DOC-001-C008",
  "document_id": "DOC-001",
  "company_id": "000001.SZ",
  "document_type": "regulatory_inquiry",
  "title": "关于存货事项的问询",
  "publish_date": "2024-05-12",
  "page": 6,
  "section": "问题二",
  "text": "……",
  "source_path": "……"
}
```

### 8.2 ownership_penetration

用途：

- 查询股东和实际控制人；
- 查询多层持股或控制路径；
- 计算间接持股比例；
- 展示关系有效日期；
- 返回每一跳证据。

输入示例：

```json
{
  "source_entity_id": "PERSON-001",
  "target_entity_id": "000001.SZ",
  "as_of_date": "2024-12-31",
  "max_depth": 5,
  "relation_types": ["OWNS", "CONTROLS"]
}
```

节点格式：

```json
{
  "entity_id": "COMPANY-001",
  "name": "示例投资有限公司",
  "entity_type": "COMPANY",
  "aliases": ["示例投资"]
}
```

关系格式：

```json
{
  "edge_id": "EDGE-001",
  "source_entity_id": "PERSON-001",
  "target_entity_id": "COMPANY-001",
  "relation_type": "OWNS",
  "ratio": 0.8,
  "valid_from": "2022-01-01",
  "valid_to": null,
  "evidence_id": "DOC-001-P12"
}
```

实现要求：

- 图路径由 Neo4j 查询；
- 间接持股比例由程序计算；
- 纯持股路径按各层比例相乘；
- 控制关系与持股关系必须区分；
- 每一跳必须绑定证据；
- LLM 不得自行生成路径或计算比例。

### 8.3 event_timeline

用途：

- 从公告、新闻、监管文本中抽取事件；
- 聚合同一公司同类事件；
- 去除重复新闻；
- 生成事件时间线；
- 将舆情节点与股权变更节点对齐。

事件记录格式：

```json
{
  "event_id": "EVENT-001",
  "company_id": "000001.SZ",
  "event_type": "controller_change",
  "event_date": "2024-05-10",
  "entities": ["A公司", "张某", "B集团"],
  "summary": "公司披露实际控制人拟发生变更",
  "source_document_ids": ["NEWS-001", "ANN-002"]
}
```

轻量实现方案：

1. 使用 LLM 将文本抽取为结构化事件；
2. 按公司、事件类型和时间窗口筛选候选；
3. 使用 Embedding 相似度和实体重叠聚类；
4. 使用 LLM 生成事件簇标题和摘要；
5. 将图谱关系生效日期插入时间线。

不需要训练聚类模型，也不需要复杂因果推理。

### 8.4 financial_risk_analysis

用途：

- 标准化财务科目；
- 计算财务指标；
- 执行跨科目勾稽规则；
- 输出风险信号、风险等级、数据对比和证据。

输入示例：

```json
{
  "company_id": "000001.SZ",
  "periods": ["2020A", "2021A", "2022A", "2023A", "2024A"],
  "risk_dimensions": [
    "revenue_quality",
    "inventory",
    "cashflow",
    "profitability",
    "solvency"
  ]
}
```

财务长表格式：

```json
{
  "company_id": "000001.SZ",
  "report_period": "2024A",
  "statement_scope": "CONSOLIDATED",
  "statement_type": "BALANCE_SHEET",
  "item_code": "INVENTORY",
  "item_name_raw": "存货",
  "value_raw": 1250,
  "unit_raw": "万元",
  "value_cny": 12500000,
  "source_document_id": "FS-2024",
  "source_page": 18
}
```

规则配置示例：

```json
{
  "rule_id": "FIN-INV-001",
  "name": "存货增长与营收增长背离",
  "required_metrics": [
    "inventory_growth",
    "revenue_growth",
    "inventory_turnover_change"
  ],
  "conditions": [
    "inventory_growth - revenue_growth >= 0.30",
    "inventory_turnover_change <= -0.20"
  ],
  "minimum_triggered_conditions": 2,
  "severity": "high",
  "weight": 15
}
```

建议第一版完成 12–15 条规则，覆盖：

- 利润与经营现金流背离；
- 存货与营收增速背离；
- 应收账款与营收增速背离；
- 存货周转下降；
- 毛利率异常；
- 非经常性损益依赖；
- 销售、管理、研发和财务费用异常；
- 短期偿债能力不足；
- 商誉或资产减值风险；
- 关联交易异常；
- 非标审计意见；
- 监管问询风险。

规则阈值应同时参考：

- 企业自身历史；
- 同行业分位数；
- 绝对业务阈值。

---

## 9. Evidence Ledger

所有工具返回的证据统一存入证据账本。

```json
{
  "evidence_id": "EVID-001",
  "evidence_type": "financial_statement",
  "source": {
    "document_id": "ANNUAL-REPORT-2024",
    "company_id": "000001.SZ",
    "document_type": "annual_report",
    "page": 86
  },
  "fact": {
    "item_code": "INVENTORY",
    "period": "2024A",
    "value": 1680000000,
    "unit": "CNY"
  },
  "support_level": "direct",
  "used_by": ["FIN-INV-001"]
}
```

要求：

- 最终答案中的关键数字必须绑定证据；
- 图谱路径每一跳必须绑定证据；
- 风险判断必须绑定规则触发记录；
- 事件节点必须绑定来源文档；
- 无法绑定证据的事实性语句不得输出；
- LLM 只能解释结构化结果，不得自行补数字。

---

## 10. 路由策略

采用三层路由：

1. 规则初筛；
2. LLM 生成执行计划；
3. 程序校验计划。

关键词示例：

- 实控人、股东、持股、控制链、穿透 → `ownership_penetration`
- 利润、现金流、存货、应收、毛利率 → `financial_risk_analysis`
- 问询函、审计报告、附注、研报原文 → `document_search`
- 时间线、什么时候、事件经过、舆情发展 → `event_timeline`

复杂问题允许组合调用，例如：

```text
“存货为什么异常，监管机构是否关注？”
→ financial_risk_analysis
→ document_search
```

```text
“控制权如何形成，后来发生了什么？”
→ ownership_penetration
→ event_timeline
```

---

## 11. 输入、输出和答案校验

### 工具执行前

检查：

- 公司和人物是否存在；
- 参数是否完整；
- 时间范围是否合法；
- 最大图深度是否合法；
- 财务数据是否至少覆盖两个期间；
- 文档类型是否支持；
- 是否存在重复调用。

### 工具执行后

检查：

- 图谱路径每一跳是否真实存在；
- 持股比例是否在 0–1；
- 间接持股比例是否可复算；
- 财务公式是否可复算；
- 单位和报表口径是否一致；
- 规则触发条件是否满足；
- RAG 文档是否属于目标公司和时间范围；
- 检索证据是否支持结论。

### 最终答案

检查：

- 是否出现结构化结果中不存在的数字；
- 是否把风险信号写成造假事实；
- 是否混淆公司、人物和年份；
- 是否缺少证据；
- 是否省略重要限制条件。

---

## 12. 评测 Harness

评测不能只看最终回答，应分层评测：

1. Context Accuracy；
2. Intent Accuracy；
3. Tool Precision / Recall；
4. Tool Execution Accuracy；
5. Evidence Accuracy；
6. Final Answer Accuracy；
7. Self-correction Success Rate；
8. End-to-End Latency。

评测数据采用 JSONL。

路由样本：

```json
{
  "case_id": "ROUTE-001",
  "conversation": [
    {"role": "user", "content": "分析一下A公司的财务情况"},
    {"role": "assistant", "content": "……"},
    {"role": "user", "content": "那它的实际控制人是谁？"}
  ],
  "expected": {
    "resolved_company_id": "000001.SZ",
    "intent": "controller_lookup",
    "expected_tools": ["ownership_penetration"],
    "must_not_call": ["financial_risk_analysis"]
  }
}
```

股权样本：

```json
{
  "case_id": "GRAPH-001",
  "query": "张某通过哪些主体间接持有A公司？",
  "expected": {
    "path": [
      "PERSON-001",
      "COMPANY-021",
      "FUND-006",
      "000001.SZ"
    ],
    "path_ratio": 0.144
  }
}
```

财务样本：

```json
{
  "case_id": "FIN-001",
  "company_id": "000001.SZ",
  "period": "2024A",
  "labels": ["inventory_risk", "cashflow_quality_risk"],
  "evidence": [
    "存货同比增长72%",
    "营收同比增长11%",
    "存货周转率下降28%"
  ]
}
```

建议最小评测集：

- 150–300 条工具路由问题；
- 50–100 条多轮记忆问题；
- 50–100 条已核验股权路径；
- 50–100 个公司年度财务风险样本；
- 30–50 条事件时间线样本。

---

## 13. 推荐技术栈

| 模块 | 推荐 |
|---|---|
| 主工作流 | LangGraph |
| API | FastAPI |
| Web Demo | Streamlit 或 Gradio |
| 大模型 | Qwen、DeepSeek 或兼容 Function Calling 的 API |
| Schema | Pydantic / JSON Schema |
| 文档解析 | MinerU 或 Unstructured |
| RAG | LlamaIndex 或 LangChain |
| 向量库 | FAISS、Chroma 或 Qdrant |
| 关键词检索 | BM25 |
| 图数据库 | Neo4j |
| 财务计算 | Pandas / NumPy |
| 关系数据库 | PostgreSQL，MVP 可用 SQLite |
| 会话缓存 | Redis 可选，MVP 可不用 |
| Trace | LangSmith 或自建结构化日志 |
| 部署 | 本地 CLI 与 FastAPI，配套环境变量和启动说明 |
| 测试 | Pytest |

第一版不使用：

- 多 Agent 框架；
- 神经网络训练；
- 图神经网络；
- Drools；
- Kafka；
- Kubernetes；
- 分布式任务队列；
- 全量 MCP 化；
- 企业级权限系统。

---

## 14. 推荐项目目录

```text
financial-agent/
├── app/
│   ├── api/
│   └── web/
├── harness/
│   ├── graph/
│   ├── state/
│   ├── routing/
│   ├── guards/
│   ├── recovery/
│   ├── evidence/
│   └── tracing/
├── tools/
│   ├── document_search/
│   ├── ownership_graph/
│   ├── event_timeline/
│   └── financial_risk/
├── data_pipeline/
│   ├── document_parser/
│   ├── financial_normalizer/
│   ├── graph_builder/
│   └── event_extractor/
├── schemas/
│   ├── agent_state/
│   ├── tool_calls/
│   ├── tool_results/
│   ├── evidence/
│   └── evaluation/
├── prompts/
├── evaluation/
│   ├── datasets/
│   ├── metrics/
│   ├── runners/
│   └── reports/
├── tests/
└── deployment/
```

---

## 15. 开发顺序

### 阶段 1：定义契约

先完成：

- Agent State；
- Tool Call Schema；
- Tool Result Schema；
- Error Schema；
- Evidence Schema；
- Evaluation Schema。

此阶段不要先写复杂 Agent。

### 阶段 2：数据管道

完成：

- 财务科目标准化；
- PDF 和文本解析；
- RAG Chunk 构建；
- 向量索引；
- 股权节点和关系导入；
- 事件结构化抽取。

### 阶段 3：四个独立工具

要求每个工具均支持：

```text
固定 JSON 输入
→ 独立执行
→ 固定 JSON 输出
→ 单元测试
```

工具独立可用后，再接 Agent。

### 阶段 4：最小 Harness

完成：

```text
单轮问题
→ 意图识别
→ 调用单个工具
→ 校验结果
→ 生成答案
```

### 阶段 5：组合调用

支持：

- 财务工具 + 文档检索；
- 图谱工具 + 事件时间线；
- 图谱工具 + 财务工具。

### 阶段 6：记忆、自纠错和证据校验

完成：

- 10 轮以上对话；
- 指代消解；
- 实体歧义修复；
- 一次自动重试；
- Evidence Ledger；
- Trace 回放；
- 离线评测。

### 阶段 7：Web Demo 和答辩功能

展示：

- 对话过程；
- 工具调用轨迹；
- 股权穿透图；
- 事件时间线；
- 财务风险卡片；
- 证据来源；
- 自纠错案例；
- 评测指标。

---

## 16. MVP 验收标准

MVP 必须满足：

- 一个 LangGraph 主工作流；
- 四个结构化工具；
- 一个统一 Agent State；
- 一个统一 Tool Result Schema；
- 一套输入和输出业务校验；
- 一次受控自动纠错；
- 一套 Evidence Ledger；
- 一套 Trace 日志；
- 一套 JSONL 离线评测；
- 支持 10 轮以上连续对话；
- 支持大于 3 层股权路径；
- 支持 12 条以上财务规则；
- 支持事件聚类和时间线；
- 支持 Web 或 API 演示；
- 所有关键结论可追溯。

---

## 17. 比赛优化建议

本节用于把开发方案进一步贴合“研究生金融科技创新大赛项目14”的评审重点。当前方案已经覆盖题目三条主线，但为了提高可落地性、答辩说服力和演示稳定性，建议做以下收敛与增强。

### 17.1 项目叙事建议

建议将项目名称和答辩主线固定为：

```text
FinTrace：面向 A 股投研的证据驱动型 Agentic AI 问答系统
```

核心卖点不要泛泛讲“多智能体”或“金融大模型”，而要突出三点：

1. 证据驱动：所有事实、数字、股权路径和风险信号都绑定 evidence_id；
2. 可复算：股权比例、财务指标和规则触发由确定性程序计算；
3. 可评测：从路由、工具执行、证据命中到最终答案分层评测。

答辩时建议使用一句话定义系统：

```text
FinTrace 不是让大模型直接判断财务造假，而是让 Agent 调用可验证工具完成检索、图推理和财务勾稽，再由大模型生成带证据的投研问答解释。
```

### 17.2 与官方题目的差距补强

官方题目特别强调 ToC 场景、0.5M Tokens 长上下文、多智能体协同、动态知识图谱和财报反欺诈。比赛项目不一定要完整实现企业级版本，但需要说明取舍。

建议在方案和答辩中明确：

- ToC 场景：Web Demo 的问题模板应更像个人投资者提问，例如“这家公司财报有没有异常信号？”“实控人到底怎么控制它？”“最近这件事会不会影响风险判断？”；
- 0.5M Tokens：MVP 不追求把全部历史消息塞进模型，而采用“短期原文 + 结构化状态 + 滚动摘要 + 已验证事实库”的分层记忆；
- 多智能体协同：工程实现采用一个主 Agent 加多个工具，答辩中表述为“Agentic 工具化协同”，避免复杂多 Agent 带来的不稳定；
- 动态知识图谱：MVP 先做离线构建和增量导入接口，演示时突出“按 as_of_date 查询历史有效关系”；
- 反欺诈：输出统一使用“风险信号”“异常线索”“需进一步核查”，不要直接宣称“造假结论”。

### 17.3 MVP 数据范围建议

第一版数据范围必须小而闭环，建议选择：

- 20–50 家 A 股上市公司；
- 每家公司连续 5 年财务三表；
- 年报、审计意见、监管问询函、公告和少量新闻；
- 十大股东、控股股东、实际控制人变更记录；
- 所属申万一级行业和基础市值/价格字段。

如果比赛数据集已给定，应优先使用比赛数据，不额外追求实时行情。外部公开数据只能作为补充证据，且必须记录 source、抓取时间和文档版本。

### 17.4 财务规则优先级

财务模块建议先实现高解释性规则，而不是过早做复杂综合评分。第一批规则按优先级实现：

1. 净利润增长但经营现金流净额恶化；
2. 存货增速显著高于营收增速；
3. 应收账款增速显著高于营收增速；
4. 存货周转率连续下降；
5. 毛利率显著偏离自身历史或行业中位数；
6. 非经常性损益占净利润比例过高；
7. 经营现金流净额/净利润长期低于阈值；
8. 短期借款和一年内到期债务压力上升；
9. 商誉或资产减值损失异常；
10. 关联交易金额或占比异常；
11. 审计意见为非标准无保留意见；
12. 出现监管问询、问询回复或立案调查相关文本。

每条规则都应输出：

```json
{
  "rule_id": "FIN-CFO-001",
  "triggered": true,
  "severity": "medium",
  "metrics": {},
  "thresholds": {},
  "evidence_ids": [],
  "explanation": "只能解释风险信号，不直接认定造假"
}
```

### 17.5 股权穿透实现建议

股权模块建议区分三类结果：

- 持股路径：按比例相乘计算间接持股；
- 控制路径：按控制关系、协议控制、实际控制人关系展示，不简单相乘；
- 关联路径：法定代表人、董监高、关联方、基金管理人等弱关系，只作为风险提示。

输出时建议同时展示：

- 最短路径；
- 持股比例最高路径；
- 证据最完整路径；
- 查询日期 as_of_date 下有效的路径。

这样可以避免“找到一条路径就结束”的薄弱感，也更贴近官方题目中的“多链路穿透”要求。

### 17.6 事件时间线实现建议

事件时间线可以做轻量但要可解释。建议事件类型先固定为：

- `controller_change`：实控人或控股股东变更；
- `share_pledge`：股权质押；
- `regulatory_inquiry`：监管问询；
- `audit_opinion`：审计意见变化；
- `financial_restated`：财务更正或追溯调整；
- `major_litigation`：重大诉讼仲裁；
- `risk_warning`：风险警示或退市风险。

事件聚类不要追求复杂因果推理。可采用“公司 + 事件类型 + 时间窗口 + 文本相似度 + 关键实体重合”的规则化聚合，再用 LLM 生成摘要。

### 17.7 长上下文演示设计

为了证明“10 轮以上连续对话”能力，建议准备固定演示脚本：

1. 用户先问 A 公司近五年财务风险；
2. 继续追问“主要异常来自哪里”；
3. 追问“监管有没有关注”；
4. 切换问“它的实控人是谁”；
5. 追问“他通过哪些主体控制”；
6. 追问“这些主体后来有没有变化”；
7. 再问“把财务异常和股权事件放到一条时间线上”；
8. 追问“哪些结论证据最弱”；
9. 追问“如果只看 2023 年，风险是否变化”；
10. 追问“生成一份给普通投资者看的摘要”。

这套脚本能同时展示记忆、指代消解、工具路由、组合调用、证据追溯和口径切换。

### 17.8 Web Demo 必备界面

Web Demo 不建议只做聊天框。至少应有四个区域：

- 左侧：对话窗口；
- 中部：答案正文和风险卡片；
- 右侧：工具调用 Trace、证据列表和可点击来源；
- 下方或独立页签：股权路径图、事件时间线、财务指标趋势图。

演示重点是“看得见的可信过程”，不是页面花哨程度。每次回答最好显示：

- 调用了哪些工具；
- 使用了哪些 evidence_id；
- 哪些数据不足；
- 是否发生自动纠错；
- 最终结论置信等级。

### 17.9 评测与答辩指标建议

除了官方指标，建议额外准备一页内部评测表：

| 能力 | 指标 | 最小目标 |
|---|---|---|
| 指代消解 | 多轮上下文实体解析准确率 | ≥ 90% |
| 工具路由 | Top-1 工具选择准确率 | ≥ 92% |
| 证据引用 | 关键结论 evidence 覆盖率 | 100% |
| 股权穿透 | 路径每跳准确率 | ≥ 90% |
| 财务规则 | 规则触发可复算率 | 100% |
| 报告生成 | 禁止无证据数字 | 0 次 |
| 性能 | 单次常规问答延迟 | ≤ 10 秒 |

其中“证据覆盖率”和“可复算率”是很好的答辩亮点，因为它们比单纯模型效果更容易让评委信服。

### 17.10 风险与降级策略

比赛开发中最容易失控的部分是数据清洗、图谱构建和长上下文。建议预先设计降级路线：

- Neo4j 部署不稳定时，MVP 可用 NetworkX 或 SQLite 关系表完成路径查询；
- 向量检索质量不足时，保留 BM25 关键词检索作为兜底；
- 事件聚类效果不足时，先按事件类型和时间窗口规则聚合；
- 长上下文成本过高时，使用结构化记忆和摘要，不依赖超长窗口模型；
- 财务标签样本不足时，先做规则命中评测，再补少量人工标注样本计算 F1；
- LLM 输出不稳定时，改为固定 JSON 结构生成，再渲染为自然语言。

### 17.11 建议补充交付物

最终交付建议包含：

- 项目源码和部署说明；
- 技术白皮书；
- 答辩 PPT；
- 演示脚本和演示数据说明；
- 评测 JSONL 数据集；
- 评测报告；
- 规则库说明文档；
- 数据字典；
- Evidence Ledger 样例；
- Trace 回放样例。

这些材料能显著提升项目的完整度，也方便团队分工。

### 17.12 复杂度削减建议

当前方案中有些内容容易被理解为“必须做得很完整”，但从比赛交付角度看，它们不是第一优先级。建议按下面方式降级，避免把 MVP 做成企业级平台。

| 内容 | 当前容易被误解的复杂度 | 建议处理 |
|---|---|---|
| 0.5M Tokens 长上下文 | 误以为必须依赖超长窗口模型完整塞入历史对话 | 不作为工程硬依赖。MVP 用结构化记忆、滚动摘要和已验证事实库模拟长周期记忆能力 |
| 多 Agent 协同 | 误以为必须实现多个自治 Agent 互相协作 | 不做复杂多 Agent。采用一个主 Agent 调度四个确定性工具即可 |
| 动态知识图谱 | 误以为必须实时从全网新闻和公告抽取并更新 | MVP 做离线图谱构建 + 手动或批处理增量导入接口 |
| 实时行情/API | 误以为必须接入大量实时金融数据接口 | 比赛数据优先。行情和宏观数据作为可选补充，不影响主链路 |
| 事件因果推理 | 误以为需要判断事件之间的真实因果关系 | 只做时序对齐和事件簇摘要，不宣称因果推断 |
| 财报反欺诈 | 误以为系统要判断公司是否造假 | 只输出风险信号、异常线索和需核查事项 |
| 行业分位数 | 误以为必须覆盖全市场行业基准 | MVP 可用比赛数据内同行样本计算近似分位数；样本不足时只使用自身历史阈值 |
| Web Demo | 误以为要做完整投研平台 | 只做能展示问答、工具轨迹、证据、股权图、时间线和风险卡片的演示台 |
| Trace 系统 | 误以为必须接 LangSmith 或复杂观测平台 | MVP 使用本地 JSONL trace 文件即可 |
| 数据库体系 | 误以为 PostgreSQL、Neo4j、向量库都必须生产级部署 | MVP 可组合 SQLite + NetworkX + FAISS，本地稳定优先 |

建议把“必须完成”和“可选增强”明确区分：

必须完成：

- 统一 Schema；
- 四个工具的固定输入输出；
- 财务规则可复算；
- 股权路径可复算；
- 证据账本；
- Trace 日志；
- Web 或 API 演示；
- 基础评测集。

可选增强：

- 真实超长窗口模型；
- 多 Agent 协作；
- 实时数据接入；
- 大规模动态图谱；
- reranker；
- Redis；
- LangSmith；

### 17.13 技术实现明确化

下面补充若干容易写得抽象的技术点，作为实际开发时的默认实现方案。

#### 17.13.1 长上下文记忆如何实现

不依赖模型“记住所有内容”，而采用四层存储：

1. `messages_recent`：保存最近 4–6 轮原始对话；
2. `current_context`：保存当前公司、人物、期间、指标、意图等结构化字段；
3. `conversation_summary`：每 4–6 轮滚动更新一次摘要；
4. `verified_facts`：只保存带 evidence_id 的历史结论。

每轮对话流程：

```text
读取最近消息
→ 读取 current_context
→ 检索 verified_facts
→ 解析当前问题中的实体、时间和指代
→ 更新 current_context
→ 执行工具
→ 将通过校验的事实写入 verified_facts
```

指代消解规则：

- “它/这家公司/该公司”优先指向 `current_context.company_id`；
- “他/她/这个人”优先指向 `current_context.person_id`；
- 如果当前上下文中同时存在多个候选主体，返回 `ENTITY_AMBIGUOUS`，不让模型猜。

#### 17.13.2 工具路由如何实现

路由采用确定性规则优先，LLM 只做补充。

第一层：关键词和实体类型规则。

```text
出现“实控人、股东、持股、穿透、控制链” → ownership_penetration
出现“利润、现金流、存货、应收、毛利率、偿债” → financial_risk_analysis
出现“问询函、审计报告、附注、原文、依据” → document_search
出现“时间线、经过、什么时候、舆情、事件” → event_timeline
```

第二层：LLM 输出结构化执行计划。

第三层：程序校验计划：

- 工具名必须在白名单中；
- 参数必须通过 Pydantic 校验；
- 不能重复调用相同工具和相同参数；
- 组合调用最多 2 个主要工具；
- 每个工具最多重试 1 次。

#### 17.13.3 document_search 如何实现

MVP 默认实现：

1. 离线读取 PDF、DOCX、TXT 或 CSV 文本；
2. 按文档标题、页码、段落和表格切 chunk；
3. 每个 chunk 写入 `documents` 表和 `chunks` 表；
4. 同时建立 BM25 索引和向量索引；
5. 查询时先 BM25 召回 50 条、向量召回 50 条；
6. 使用加权分数合并去重；
7. 返回 top_k，并保留页码、文档类型、发布日期和 source_path。

建议混合分数：

```text
final_score = 0.55 * bm25_score_norm + 0.45 * embedding_score_norm
```

如果暂时没有 embedding，可以只用 BM25，但接口保持不变。

#### 17.13.4 ownership_penetration 如何实现

MVP 可以先不用 Neo4j，使用 SQLite 边表 + NetworkX：

`entities` 表：

```text
entity_id, name, entity_type, aliases
```

`relations` 表：

```text
edge_id, source_entity_id, target_entity_id, relation_type, ratio,
valid_from, valid_to, evidence_id
```

路径查询：

```text
按 as_of_date 过滤有效边
→ 按 relation_types 过滤关系类型
→ 使用 all_simple_paths 查询 source 到 target 的路径
→ 限制 max_depth <= 5
→ 对 OWNS 路径计算 ratio 连乘
→ 对 CONTROLS 路径只展示控制链，不计算间接持股比例
→ 每一跳检查 evidence_id
```

Neo4j 可作为增强版本，不应阻塞 MVP。

#### 17.13.5 event_timeline 如何实现

MVP 不做复杂事件抽取平台，采用“规则 + LLM 结构化抽取 + 聚合”：

1. document_search 召回候选公告、新闻、问询函；
2. LLM 按固定 JSON Schema 抽取事件；
3. 程序校验 event_type、event_date、company_id 和 source_document_ids；
4. 按 `company_id + event_type + 30 天窗口` 合并候选事件；
5. 如果标题相似度或实体重合度较高，则归为同一事件簇；
6. 按 event_date 排序输出时间线。

事件簇合并规则建议：

```text
same_company = true
same_event_type = true
date_distance <= 30 days
title_similarity >= 0.75 或 entity_overlap >= 0.5
```

#### 17.13.6 financial_risk_analysis 如何实现

财务数据统一转成长表后再计算指标。不要直接让 LLM 读表判断。

指标计算流程：

```text
读取连续 5 年财务长表
→ 单位统一为 CNY
→ 按 company_id、period、statement_scope 聚合
→ 计算基础指标
→ 计算同比和趋势
→ 执行规则库
→ 生成 RiskSignal 列表
→ 绑定 evidence_id 和 rule_id
```

建议先实现这些基础指标：

- `revenue_growth`
- `net_profit_growth`
- `operating_cashflow_growth`
- `cfo_to_net_profit`
- `inventory_growth`
- `receivable_growth`
- `inventory_turnover`
- `gross_margin`
- `expense_ratio`
- `debt_to_asset`
- `current_ratio`
- `non_recurring_profit_ratio`

规则文件建议使用 YAML 或 JSON，不写死在 Agent Prompt 中。LLM 只解释规则结果，不参与公式计算。

#### 17.13.7 Evidence Ledger 如何落地

证据账本可以先用一个本地 JSONL 文件或 SQLite 表实现。

每条证据必须包含：

```text
evidence_id
source_document_id
source_type
company_id
publish_date 或 report_period
page 或 row_id
fact_type
fact_value
source_path
created_at
```

最终答案生成前做硬校验：

- 出现数字时，必须能在 answer_object 中找到对应 evidence_id；
- 出现股权路径时，每条 edge 必须有 evidence_id；
- 出现财务风险判断时，必须有 rule_id 和 evidence_ids；
- 出现事件结论时，必须有 source_document_ids。

校验不通过时，不直接输出答案，而是生成“证据不足”的降级回答。

#### 17.13.8 Trace 如何落地

MVP 每轮对话写一条 JSONL trace：

```json
{
  "trace_id": "TRACE-001",
  "session_id": "SESSION-001",
  "user_query": "分析A公司的存货风险",
  "resolved_context": {},
  "plan": [],
  "tool_calls": [],
  "tool_results_summary": [],
  "evidence_ids": [],
  "validation": [],
  "final_answer": "……",
  "latency_ms": 0
}
```

Web Demo 直接读取 trace 并展示，不需要复杂监控系统。

#### 17.13.9 Web Demo 如何最小实现

推荐使用 Streamlit，原因是开发快、展示数据表和图形方便。

最小页面：

```text
st.chat_input
st.chat_message
st.expander("工具调用")
st.expander("证据来源")
st.dataframe("财务指标")
st.graphviz_chart 或 pyvis 展示股权路径
st.timeline 或 plotly 展示事件时间线
```

如果时间紧，优先保证：

1. 对话可用；
2. 工具调用轨迹可见；
3. 证据可见；
4. 财务风险卡片可见。

股权图和时间线可以先用表格展示，再做可视化增强。

---

## 18. Codex 执行要求

1. 先阅读本文件，再输出技术拆解和实施顺序。
2. 不要一次性生成整个项目。
3. 按“Schema → 数据管道 → 独立工具 → Harness → 评测 → Web”顺序开发。
4. 每完成一个模块，必须同时提供：
   - 数据结构；
   - 接口说明；
   - 单元测试；
   - 示例输入输出；
   - 错误处理；
   - README。
5. 所有工具均应可以脱离 Agent 独立测试。
6. 所有金额、比例、时间和财务指标必须使用确定性程序计算。
7. 所有 LLM 输出必须通过 Pydantic 或 JSON Schema 验证。
8. 所有关键事实必须绑定 evidence_id。
9. 禁止无限循环和无限重试。
10. 禁止为了“先进”引入不必要框架。
11. 优先保证正确性、可解释性、可测试性和 Demo 稳定性。
12. 发生设计冲突时，优先选择更简单、可复现、可评测的方案。

---

## 19. 首个开发任务

Codex 首先只完成以下内容，不直接实现业务逻辑：

1. 输出项目技术拆解；
2. 创建推荐目录结构；
3. 定义以下 Pydantic Schema：
   - AgentState；
   - ToolCall；
   - ToolResult；
   - ToolError；
   - Evidence；
   - ExecutionPlan；
   - EvaluationCase；
4. 为四个工具创建接口占位和示例输入输出；
5. 创建最小 LangGraph 工作流骨架；
6. 创建 Pytest 测试骨架；
7. 创建 `.env.example`、本地部署说明和主 README；
8. 等待 Schema 和目录结构确认后，再开始数据和工具实现。

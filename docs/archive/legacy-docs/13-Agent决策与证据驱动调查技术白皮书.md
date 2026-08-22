# FinTrace Agent 决策与证据驱动调查技术白皮书

> 版本：v1.0  
> 日期：2026-08-19  
> 定位：本文聚焦 FinTrace 在线 Agent 的请求理解、可回答性判断、工具路由、证据管理与有界自主调查架构。项目整体比赛目标、数据边界和评测口径分别以 [竞赛要求与验收口径](00-竞赛要求与验收口径.md)、[系统目标能力与数据边界](01-系统目标能力与数据边界.md) 和 [评测规范](04-评测规范.md) 为准；运行时工具接口以代码和测试为准。

---

## 1. 文档目标

FinTrace 的核心目标不是构建一个“让大模型自由调用工具”的通用聊天 Agent，而是构建一个面向 A 股投研场景的 **Evidence-driven Financial Investigation Agent**：

1. 先理解用户在问谁、什么时间、什么任务；
2. 判断当前系统是否具备处理该请求的能力；
3. 对简单、唯一、参数完整的事实型请求走确定性快速路径；
4. 对复杂研判请求由 LLM 决定下一步最有价值的调查动作；
5. 工具内部的检索、过滤、计算、图搜索和风险规则保持确定性；
6. 所有关键事实进入统一 Evidence Ledger；
7. 只有证据足够时才生成相应强度的结论；
8. 证据不足时继续有限调查，或明确输出部分回答、证据不足、能力不支持等状态；
9. 全流程可追踪、可回放、可评测。

本文是对历史工程规格（见 `archive/FINANCIAL_AGENT_PROJECT_SPEC.md`）中 Agent Harness 方案的进一步收敛。旧规格中的 LangGraph、统一工具接口、证据账本、校验与 Trace 继续保留；主要升级点是将“一次性完整 Tool Plan”调整为：

**请求解析 + 可回答性判断 + Direct Fast Path + LLM Initial Action + Evidence-driven Bounded Investigation**

---

## 2. 核心设计原则

### 2.1 确定的事情交给程序，不确定的事情交给 LLM

FinTrace 中不同职责必须明确分离：

- **程序负责**：实体别名匹配、时间标准化、能力边界、参数合法性、财务计算、股权图遍历、文档检索、事件筛选、Evidence ID、重试边界、停止条件；
- **LLM 负责**：复杂自然语言理解、复杂任务的下一步调查动作、证据缺口归纳、最终解释与自然语言组织；
- **LLM 不负责**：凭记忆补财务数字、自己计算财务公式、自己决定数据截止日、凭语言推理执行股权穿透、无证据认定因果关系。

可概括为：

```text
LLM decides WHAT to investigate.
Program determines WHAT is true.
Evidence constrains WHAT can be claimed.
```

### 2.2 LangGraph 是编排骨架，ReAct 是局部调查机制

FinTrace 不采用“纯固定工作流”或“纯无限 ReAct”二选一，而采用混合架构：

- LangGraph 负责整个状态机、条件分支、错误处理、循环上限和 Trace；
- 简单任务走确定性 Direct Route；
- 复杂任务进入有界的 Evidence-driven Investigation Loop；
- ReAct 仅用于“根据已有观察决定下一步工具”，不用于替代工具内部算法。

更准确的架构名称是：

> **LangGraph Deterministic Harness + Direct Fast Path + Evidence-guided Bounded Investigation**

### 2.3 高 Tool Precision 优先于高规则覆盖率

Deterministic Gate 的目标不是尽量覆盖所有问题，而是只拦截“几乎没有歧义”的请求。宁可将复杂问题交给 LLM Planner，也不能为追求规则覆盖率而错误调用工具。

推荐目标：

- Direct Gate Precision：尽量接近 98% 以上；
- Direct Gate Coverage：30%～50% 即可接受；
- 总体 Tool Call Precision：对齐比赛指标，目标不低于 92%。

---

## 3. 状态体系与术语对齐

### 3.1 评测标签与运行态分离

评测标签与在线运行状态不应混用。

离线评测的可回答性标签包括：

- `supported`
- `partially_supported`
- `clarification_needed`
- `unsupported`
- `unsafe_prediction`

在线 Agent 的 `answer_status` 用于描述本轮真实执行结果：

- `answered`：已获得充分证据并完成回答；
- `partially_answered`：复合问题中只有部分内容具备证据；
- `clarification_required`：缺少公司、日期、比较对象或其他必要条件，无法唯一处理；
- `unsupported`：当前系统没有所需工具、数据源或权限；
- `insufficient_evidence`：系统具备对应能力，但本次没有获得足够证据；
- `failed`：工具、模型、网络或程序执行失败。

关键区分：

```text
clarification_required = 用户输入条件不足
unsupported            = 系统能力不存在
insufficient_evidence   = 能力存在，但本次证据不足
failed                  = 执行故障
```

### 3.2 Pre-Answerability 与 Post-Answerability 分离

可回答性不应只判断一次。

**Pre-Answerability** 在工具执行前判断：

> 系统理论上能否处理这个请求？

主要检查：

- 请求是否已正确解析；
- 必要主体、时间、比较对象等参数是否完整；
- 是否存在匹配 Capability；
- Capability 是否在当前版本实际启用；
- 是否涉及实时行情、用户账户、不可保证的未来预测等能力边界。

**Post-Answerability / Evidence Sufficiency** 在工具执行后判断：

> 本轮实际获得的证据是否足以回答？

主要检查：

- ToolResult 是否成功；
- 数据是否命中；
- Evidence 是否存在；
- 用户请求的关键方面是否被覆盖；
- 是否仍存在可通过现有工具补足的 Evidence Gap。

---

## 4. 总体架构

```text
User Query
    ↓
1. Load Session / Memory
    ↓
2. Request Resolution
   Entity / Time / Task / Metric / Constraints
    ↓
3. Pre-Answerability
    ├─ clarification_required → Ask / END
    ├─ unsupported            → Refuse / END
    └─ routeable
          ↓
4. Deterministic Direct Gate
    ├─ direct        → build direct ToolCall
    └─ investigation→ LLM Initial Action Planner
          ↓
5. Validate Action
          ↓
6. Execute Deterministic Tool
          ↓
7. Evidence Ledger
          ↓
8. Evidence Sufficiency
    ├─ sufficient → Final Answer
    ├─ partial    → Partial Answer + limitations
    ├─ gap 可补   → Decide Next Action → Tool → Evidence → Review
    └─ gap 不可补 → insufficient_evidence
          ↓
9. Final Answer Generation
          ↓
10. Memory Write + Trace
```

---

## 5. Session Memory 与上下文恢复

MVP 使用：

```text
SQLite + Pydantic State
```

建议结构：

```python
class SessionContext(BaseModel):
    session_id: str
    active_entities: list[str] = []
    active_people: list[str] = []
    active_periods: list[str] = []
    active_metrics: list[str] = []
    active_topic: str | None = None
    comparison_targets: list[str] = []
    recent_messages: list[dict] = []
    conversation_summary: str = ""
    verified_findings: list[dict] = []
```

推荐四层记忆：

1. 最近 4～8 轮原始消息；
2. 当前结构化 `CurrentContext`；
3. 较早历史的滚动摘要；
4. 经过工具证据验证的 `verified_findings`。

只有经过 Tool Evidence 支撑的事实才能进入长期 Verified Finding。用户主观表述和 LLM 自行推断不得作为“已验证事实”写入长期记忆。

长历史可扩展为：

```text
SQLite / PostgreSQL + FAISS
```

FAISS 只用于检索旧的 verified facts / summaries，而不是把全部历史原样塞回 Prompt。

---

## 6. Request Resolution

Request Resolution 不选工具，只将自然语言转为结构化任务描述：

> 谁（Entity）+ 什么时间（Time）+ 要做什么（Task）+ 有什么约束（Constraint）

建议结构：

```python
class ParsedRequest(BaseModel):
    raw_query: str
    entities: list[str] = []
    people: list[str] = []
    periods: list[str] = []
    task_family: str | None = None
    metrics: list[str] = []
    document_types: list[str] = []
    event_types: list[str] = []
    comparison_type: str | None = None
    requires_explanation: bool = False
    requires_investigation: bool = False
    requires_realtime: bool = False
    requires_prediction: bool = False
    unresolved_slots: list[str] = []
```

例如：

```text
贵州茅台 2023 和 2024 年净利润变化多少？
```

解析为：

```json
{
  "entities": ["600519.SH"],
  "periods": ["2023-12-31", "2024-12-31"],
  "task_family": "financial_metric_compare",
  "metrics": ["NET_PROFIT_PARENT"],
  "comparison_type": "cross_period",
  "requires_investigation": false
}
```

此阶段仍然不应直接写入 `financial_analysis`。用户需求与工具实现保持解耦。

---

## 7. Entity Resolution

推荐四级策略：

```text
1. 证券代码 Regex
2. entity_alias_index
3. Session Context 指代继承
4. LLM 仅用于真正的语义歧义消解
```

公司名称、简称与代码统一通过内部 `entity_alias_index` 解析。建议 SQLite 表：

```text
alias | entity_id | entity_type | canonical_name
```

多轮指代如“它、这家公司、该公司、前面那个公司”，若 `CurrentContext` 中只有一个唯一实体，可确定性继承；若存在多个合理主体，则返回 `clarification_required`。

**禁止默认公司。** 实体解析失败时必须返回 `None` / unresolved，禁止填入示例公司或 `000001.SZ`。

---

## 8. Time Resolution

主要使用：

```text
Regex + 中文时间规则 + knowledge_cutoff
```

示例：

```text
2024 年         → 2024-12-31
2024 年一季度   → 2024-03-31
2024 年半年报   → 2024-06-30
2024 年三季度   → 2024-09-30
```

相对时间如“今天、当前、最新、去年”必须基于工作流注入的 `knowledge_cutoff` 和当前数据覆盖转换，不允许 Planner 自己生成或修改截止日期。

`knowledge_cutoff` 只注入 Capability Registry 中声明支持该约束的工具。文档检索将它解释为最晚披露日期，并使用 `min(end_date, knowledge_cutoff)` 作为实际检索上界；因此用户给出的查询区间不能绕过系统截止日。财务与股东工具继续按各自的披露日期执行防前视选择。

必须持续区分：

- `report_periods`：财务数据归属期间；
- `as_of_date`：股权快照观察时点；
- `start_date / end_date`：事件或文档时间范围；
- `knowledge_cutoff`：当前系统允许使用的信息最晚披露日。

---

## 9. Task Recognition

简单任务规则优先：

```text
“净利润是多少”           → financial_metric_query
“2023 和 2024 营收变化”  → financial_metric_compare
“十大股东是谁”           → ownership_snapshot
“找年报/公告原文”         → document_retrieval
“监管处罚时间线”          → event_timeline
```

复杂任务使用 Qwen Structured Output + Pydantic Validation，例如：

```json
{
  "task_family": "financial_investigation",
  "requires_investigation": true,
  "focus_topics": ["profit_quality", "cashflow"]
}
```

LLM 此时负责结构化意图理解，不负责直接生成完整工具计划。

---

## 10. Capability Registry 与 Pre-Answerability

在线 Agent 不应靠 LLM“猜自己会不会”。所有可调用能力由静态 Registry 描述。

推荐使用 YAML + Pydantic：

```yaml
financial_metric_query:
  implemented: true
  tool: financial_analysis
  operation: metric_query
  required_slots:
    - company_ids
    - metric_codes
    - report_periods

financial_metric_compare:
  implemented: true
  tool: financial_analysis
  operation: metric_compare

ownership_snapshot:
  implemented: true
  tool: ownership_analysis
  operation: holding_query

ownership_penetration:
  implemented: false

realtime_market_price:
  implemented: false

user_portfolio:
  implemented: false
```

`implemented` 必须反映当前真实代码能力，而不是目标接口。

推荐判断顺序：

```python
if capability_not_supported:
    return UNSUPPORTED

if required_user_slots_missing_or_ambiguous:
    return CLARIFICATION_REQUIRED

if unsafe_deterministic_prediction:
    return SAFE_DOWNGRADE_OR_UNSUPPORTED

return ROUTEABLE
```

Pre-Answerability 主要判断**能力是否存在**，不应查询具体事实值。

若能力存在但实际调用后无数据：

```text
Pre-Answerability → routeable
Tool execution    → no data
Post-Answerability→ insufficient_evidence
```

可选优化：维护轻量 `data_coverage_manifest`，只用于提前拦截明显超出数据范围的请求。

---

## 11. Deterministic Direct Gate

Direct Gate 不是第二个复杂 Rule Planner。它只判断：

> 当前请求是否已经明确到可以不调用 Planner LLM，直接构造唯一合法 ToolCall？

只有同时满足以下条件才走 Direct：

```text
唯一 Capability
AND
唯一 Tool
AND
唯一 Operation
AND
必要参数完整
AND
不存在未解析指代
AND
不需要复杂解释或多步调查
AND
不存在跨工具组合需求
```

典型 Direct 请求：

```text
贵州茅台 2024 年营业收入是多少？
→ financial_analysis.metric_query

贵州茅台 2023 和 2024 年净利润变化多少？
→ financial_analysis.metric_compare

贵州茅台十大股东是谁？
→ ownership_analysis.holding_query

查贵州茅台年报中关于存货跌价准备的内容。
→ document_search.search
```

必须交给 Planner 的请求：

```text
为什么利润增长但现金流下降？
结合财报和监管问询分析存货风险。
这家公司最近有什么值得警惕的地方？
```

若同时存在多个合理 Direct Matcher，Gate 不做冲突消解，直接 `defer_to_llm`。

---

## 12. LLM Initial Action Planner

不建议复杂任务一开始生成完整：

```text
[tool_1, tool_2, tool_3, tool_4]
```

推荐每次只输出一个“下一动作”：

```python
class AgentAction(BaseModel):
    action: Literal["call_tool", "finish", "clarify", "unsupported"]
    capability: str | None = None
    tool_name: str | None = None
    operation: str | None = None
    arguments: dict = {}
    reason: str
    expected_evidence: str | None = None
```

Planner 输入：

```text
ParsedRequest
CurrentContext
Candidate Capabilities
Current Evidence
Previous Tool Calls
Current Evidence Gaps
```

输出示例：

```json
{
  "action": "call_tool",
  "capability": "financial_metric_compare",
  "tool_name": "financial_analysis",
  "operation": "metric_compare",
  "arguments": {},
  "reason": "先验证利润与经营现金流是否存在显著背离",
  "expected_evidence": "多期净利润与经营现金流序列"
}
```

`reason` 仅记录可审计的简短决策理由，不保存自由形式私有思维链。

Planner 不应始终看到全部工具。Request Resolution + Capability Registry 可先收缩候选能力，以降低无关调用。

---

## 13. Action Validator

所有 LLM Action 在执行前必须通过确定性校验。

技术实现：

```text
Pydantic Schema
+
Capability Registry
+
Tool Operation Rules
+
Business Constraints
```

检查：

- Tool 是否存在；
- Operation 是否在当前版本启用；
- 必填参数是否齐全；
- 参数类型是否正确；
- `metric_compare` 是否存在“多公司 + 多期间”造成比较维度不唯一；
- 报告期、观察时点与截止日是否混用；
- Planner 是否试图修改 `knowledge_cutoff`；
- 是否重复执行完全相同且没有信息增益的调用。

验证失败后不得直接调用工具，应进入一次受控 Argument Repair 或 Replan。

---

## 14. 确定性工具层

Agent 只决定“查什么”，工具决定“怎么算、怎么搜、怎么遍历”。

### 14.1 `financial_analysis`

```text
Normalized Financial Data
→ SQLite Index
→ Python Deterministic Calculation
```

职责：

- `metric_query`
- `metric_compare`
- 目标态 `risk_scan`

Agent 不自行计算增长率、CAGR、财务比率；风险规则只输出 `derived_signal`，不得直接认定财务造假。

### 14.2 `ownership_analysis`

普通快照和比较使用 SQLite Query。

有限股权穿透目标态：

```text
Ownership Graph
→ bounded BFS / DFS
→ path ratio calculation
```

穿透深度与路径数必须设置 `max_depth`、`max_paths`。禁止使用 Agent-level ReAct 一层一层询问股东来模拟图搜索。

### 14.3 `document_search`

保持：

```text
Metadata Filter
+ BM25
+ Embedding Retrieval
+ FAISS
+ RRF / Rerank
```

LLM 主要根据 Evidence Gap 动态调整查询词，而不是接管底层检索算法。

### 14.4 `event_timeline`

```text
SQLite / normalized event table
→ filter
→ deduplicate
→ time ordering
→ rule-based clustering
```

事件抽取尽量离线完成，在线工具只执行检索、筛选、聚类与证据组装。

---

## 15. Evidence Ledger

所有可引用事实必须进入统一 Evidence Ledger。

建议至少维护：

```text
Evidence
Claim
EvidenceGap
```

### Evidence

```python
class Evidence(BaseModel):
    evidence_id: str
    source_type: str
    source_id: str
    entity_ids: list[str]
    as_of_date: str | None = None
    content: dict
    provenance: dict
```

### Claim

```python
class Claim(BaseModel):
    claim_id: str
    text: str
    status: Literal["verified", "partial", "unsupported"]
    evidence_ids: list[str]
```

### EvidenceGap

```python
class EvidenceGap(BaseModel):
    gap_id: str
    description: str
    candidate_capabilities: list[str]
    resolved: bool = False
```

Agent 的循环对象应从“自由思考”变为“填补明确 Evidence Gap”。

---

## 16. Evidence Sufficiency / Post-Answerability

简单事实题使用程序判断：对应指标 ToolResult 成功且存在 Evidence，即可 `answered`。

复合或解释题使用：

```text
Deterministic checks + Structured LLM Evaluator
```

Evaluator 只能判断“覆盖了什么、还缺什么”，不得创造新事实。

状态映射：

```text
全部关键方面有证据
→ answered

复合问题部分关键方面有证据
→ partially_answered

能力存在，但无足够证据且无法继续补充
→ insufficient_evidence
```

---

## 17. Evidence-driven Bounded Investigation

复杂任务循环：

```text
Evidence Review
    ↓
Evidence Gap
    ↓
Decide Next Action
    ↓
Validate
    ↓
Tool
    ↓
Observation / Evidence
    ↓
Update Ledger
    ↓
Evidence Review
```

建议默认边界：

```text
max_steps = 5
max_total_tool_calls = 6
max_calls_per_tool = 2
max_repair_per_action = 1
```

停止条件：

- 证据已经充分；
- Evidence Gap 无对应 Capability；
- 工具明确返回无数据；
- 连续 2 轮没有新增有效 Evidence；
- 重复相同调用；
- 达到 `max_steps`；
- 达到 `max_total_tool_calls`；
- 发生不可恢复错误。

---

## 18. Self-correction 与 Retry 分离

```text
TEMPORARY_ERROR
→ same-call retry once

INVALID_ARGUMENT
→ repair arguments once

WRONG_TOOL / capability mismatch
→ replan next action

NO_DATA
→ 尝试其他合法 capability，或停止

INSUFFICIENT_EVIDENCE
→ 寻找下一条可补充证据路径

UNSUPPORTED
→ stop
```

Trace 应记录：

```text
original_action
error_type
repair_action
repair_reason
repair_result
```

---

## 19. Final Answer Generation

最终生成模型只接收：

```text
User Query
Resolved Context
Answer Status
Verified Claims
Supporting Evidence
Limitations
```

约束：

1. 只能引用提供的 Evidence；
2. 数字必须来自 ToolResult / Verified Claim；
3. `derived_signal` 只能表述为风险线索；
4. 时间相近或事件聚类不能自动表述为因果关系；
5. 证据不足必须明确披露；
6. `partially_answered` 不得包装成完整结论；
7. 不得使用模型常识补齐当前数据源没有的事实。

---

## 20. Trace 与可观测性

每轮建议写入 JSONL：

```json
{
  "session_id": "...",
  "turn_id": 1,
  "query": "...",
  "resolved_context": {},
  "parsed_request": {},
  "pre_answerability": {},
  "routing_mode": "direct",
  "planner_actions": [],
  "tool_calls": [],
  "tool_results": [],
  "evidence_added": [],
  "evidence_gaps": [],
  "repairs": [],
  "termination_reason": "evidence_sufficient",
  "answer_status": "answered"
}
```

重点统计：

- Entity Resolution Accuracy；
- Condition Carryover Accuracy；
- Answerability Accuracy；
- Direct Gate Precision / Coverage；
- Tool Call Precision / Recall；
- Unnecessary Tool Call Rate；
- Self-correction Success Rate；
- Evidence Coverage；
- Investigation Step Count；
- Token / Tool Cost；
- Turn Latency；
- False Answer Rate；
- False Refusal Rate。

---

## 21. LangGraph 目标节点设计

```text
START
  ↓
load_session
  ↓
resolve_request
  ↓
check_pre_answerability
  ├─ clarification → build_clarification → persist → END
  ├─ unsupported   → build_refusal       → persist → END
  └─ routeable
        ↓
route_mode
  ├─ direct → build_direct_action
  └─ investigation → plan_next_action
        ↓
validate_action
  ├─ repairable → repair_action → validate_action
  └─ valid
        ↓
execute_one_tool
        ↓
validate_tool_result
        ↓
merge_evidence
        ↓
review_evidence
  ├─ continue → plan_next_action
  ├─ sufficient
  ├─ partial
  └─ insufficient
        ↓
generate_answer
        ↓
persist_session
        ↓
write_trace
        ↓
END
```

`execute_one_tool` 是 Investigation Mode 的关键：复杂任务每一轮只执行一个主要 ToolCall，再基于观察决定下一步。

---

## 22. AgentState 推荐扩展字段

```python
class AgentState(BaseModel):
    messages: list = []
    current_context: dict = {}
    tool_results: list = []
    evidence_ledger: list = []
    previous_findings: list = []

    parsed_request: dict | None = None
    pre_answerability: dict | None = None

    routing_mode: Literal["direct", "investigation"] | None = None
    candidate_capabilities: list[str] = []

    current_action: dict | None = None
    step_count: int = 0
    max_steps: int = 5
    total_tool_calls: int = 0
    tool_call_history: list = []

    claims: list = []
    evidence_gaps: list = []
    evidence_sufficient: bool = False
    no_new_evidence_rounds: int = 0

    repair_count: int = 0
    failed_actions: list = []
    termination_reason: str | None = None
    answer_status: str | None = None
```

---

## 23. 与当前代码的最小迁移方案

原则：**不推翻现有 LangGraph 和工具层，只替换路由与复杂任务执行方式。**

继续保留：

- `StateGraph`；
- `AgentState`；
- Tool Registry / `execute_tool`；
- `ToolResult`；
- Evidence 数据结构；
- 可复用的 plan/result 校验逻辑；
- answer generation；
- tracing；
- 四类公共工具。

目标 `harness/routing/`：

```text
harness/routing/
├── entities.py
├── time_resolver.py
├── request_parser.py
├── capability_registry.py
├── answerability.py
├── direct_gate.py
├── planner.py
└── action_validator.py
```

`planner.py` 从：

```text
query → 完整 ExecutionPlan(tool_calls[])
```

迁移为：

```text
ParsedRequest + Context + Evidence
→ AgentAction（一次一个 next action）
```

旧 Rule Planner 可保留作为：

- benchmark baseline；
- Direct Gate 的部分 matcher；
- LLM 调用失败时的有限 fallback。

复杂 Investigation Mode 将 `execute_tools_node` 改为 `execute_one_tool_node`，执行后回到 `review_evidence`。

---

## 24. 推荐实施顺序

### Phase 1：入口稳定化

1. 去除默认公司 `000001.SZ`；
2. Session Context 持久化；
3. Entity / Time Resolution；
4. `ParsedRequest`；
5. Capability Registry；
6. Pre-Answerability；
7. Deterministic Direct Gate。

### Phase 2：复杂任务自适应

1. `AgentAction` schema；
2. LLM Initial / Next Action Planner；
3. `execute_one_tool`；
4. Evidence Gap；
5. Evidence Sufficiency；
6. Bounded Investigation Loop。

### Phase 3：自纠错与证据质量

1. Argument Repair；
2. Wrong-tool Replan；
3. No-data 降级；
4. Claim–Evidence Mapping；
5. Final Answer Verification；
6. Trace 指标完善。

### Phase 4：目标工具能力

- `financial_analysis.risk_scan`；
- `ownership_analysis.penetration`；
- 更完整的事件数据与聚类；
- 长历史 verified facts 检索。

---

## 25. 建议的消融实验

### A. Rule-only Router

```text
规则识别 → 固定 Tool Plan
```

### B. LLM-only Planner / ReAct

```text
所有问题 → LLM Planner → 动态 Tool Calling
```

### C. Hybrid FinTrace（目标方案）

```text
Request Resolution
→ Pre-Answerability
→ Direct Gate
→ LLM Bounded Investigation
→ Evidence Sufficiency
```

比较：

- Tool Call Precision；
- Necessary Tool Recall；
- Answer Accuracy；
- Complex-query Success Rate；
- Self-correction Success Rate；
- Average Tool Calls / Turn；
- Average Tokens / Turn；
- P95 Latency；
- False Answer / False Refusal；
- Evidence Coverage。

---

## 26. 非目标与明确禁止项

当前阶段不建议：

- 多 Agent 协作；
- 无限 ReAct；
- 让 LLM 自己做财务计算；
- 让 LLM 自己执行股权图遍历；
- 用 `document_search` 兜底所有无法分类的问题；
- 实体不明时默认示例公司；
- 将目标接口误当成已实现能力；
- 把 Tool warning 当成完整证据；
- 无 Evidence 的模型常识进入 Verified Finding；
- 为比赛项目引入不必要的分布式基础设施。

---

## 27. 最终架构摘要

FinTrace 的在线 Agent 可归纳为三个核心 Gate 和一个有界调查循环。

### Gate A：Do we understand the request?

```text
Memory
→ Entity
→ Time
→ Task
→ Constraints
```

### Gate B：Can FinTrace handle it?

```text
Parsed Request
→ Capability Registry
→ Required Slots
→ Pre-Answerability
```

### Gate C：Do we need agentic reasoning?

```text
Unique capability + unique operation + complete args
→ Direct Tool Call

否则
→ LLM Planner
```

### Investigation Loop：Do we have enough evidence?

```text
Tool
→ Evidence
→ Evidence Gap
→ Next Action
→ Tool
→ ...
→ sufficient / partial / insufficient
```

最终主线：

```text
User
→ Memory
→ Request Resolution
→ Pre-Answerability
→ Direct Gate / LLM Planner
→ Validated Tool Action
→ Deterministic Tool
→ Evidence Ledger
→ Evidence Sufficiency
→ Bounded Investigation（必要时）
→ Grounded Final Answer
→ Memory + Trace
```

这套架构的本质不是让 LLM 获得更多自由，而是让 LLM 只在真正需要语义判断的地方发挥作用，同时通过确定性能力边界、工具算法、证据账本和循环上限保证金融问答的可靠性、可解释性与可评测性。

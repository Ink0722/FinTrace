# FinTrace 在线 Agent、记忆、路由与证据工作流

> 文档性质：当前实现说明。审查时以 `harness/graph/workflow.py` 的图拓扑、`schemas/agent_state.py` 的状态字段和 `tests/test_workflow.py` 为最终依据。

## 代码审查索引

| 审查对象 | 实现文件 | 主要测试 |
| --- | --- | --- |
| LangGraph 拓扑与持久化时机 | `harness/graph/workflow.py`、`harness/graph/conditions.py` | `tests/test_workflow.py`、`tests/test_langgraph_conditions.py` |
| 节点状态变更 | `harness/graph/nodes.py` | `tests/test_workflow.py`、`tests/test_skills_integration.py` |
| 请求、实体与期间解析 | `harness/routing/request_parser.py`、`harness/routing/entities.py`、`harness/routing/time_resolver.py`、`harness/routing/financial_period_resolver.py` | `tests/test_routing.py` |
| 能力、直连、规划和修复 | `harness/routing/capability_registry.py`、`harness/routing/direct_gate.py`、`harness/routing/planner.py`、`harness/routing/action_validator.py` | `tests/test_agent_modules.py`、`tests/test_routing.py` |
| Evidence Ledger 与工具校验 | `harness/evidence/`、`harness/guards/validation.py` | `tests/test_agent_modules.py`、`tests/test_workflow.py` |
| Prompt 与 LLM Skill | `harness/prompts.py`、`harness/skills.py`、`prompts/` | `tests/test_prompt_loading.py`、`tests/test_skills_integration.py`、`tests/test_llm.py` |
| 会话、Trace 与 SSE | `harness/memory/`、`harness/tracing/`、`harness/streaming.py` | `tests/test_local_users.py`、`tests/test_evaluation_log.py`、`tests/test_streaming_workflow.py` |

## 总体模型

FinTrace 是“确定性 Harness + 受限 LLM 调查”的证据驱动 Agent：LLM 决定下一项待调查动作，程序决定可计算事实，证据决定可主张结论。系统不采用无限 ReAct，也不以多个自由对话 Agent 代替工具、规则和状态机。

```text
load context -> resolve request -> pre-answerability
  -> clarification / refusal
  -> direct action or bounded investigation
  -> action validation -> one tool execution -> result validation
  -> evidence merge -> evidence review -> answer -> persist context and trace
```

## 当前已实现并验证的运行拓扑

当前入口是 `harness.graph.workflow.run_agent(query, session_id)`，内部由 LangGraph `StateGraph` 编排 16 个节点。CLI 和 FastAPI 最终都调用这个入口。下图依据当前 `harness/graph/workflow.py`、`harness/graph/conditions.py` 和实际 Trace 绘制，不表示尚未实现的目标能力。

```mermaid
flowchart TD
    START([用户问题]) --> LS[load_session<br/>加载会话与结构化上下文]
    LS --> RR[resolve_request<br/>实体、时间、任务与约束解析]
    RR --> PA[check_pre_answerability<br/>Gate A/B 可回答性判断]

    PA -->|需要补充必要条件| BC[build_clarification<br/>生成澄清问题]
    PA -->|数据或能力不支持| BR[build_refusal<br/>生成边界说明]
    PA -->|可以路由| RM[route_mode<br/>Gate C 路径分流]
    BC --> PS[persist_session<br/>保存会话]
    BR --> PS

    RM -->|Direct：参数完整且动作唯一| VA[validate_action<br/>校验能力、参数、实体、截止日、重复与预算]
    RM -->|Investigation：需要动态取证| PN[plan_next_action<br/>LLM 每轮只规划一个动作]

    PN -->|call_tool| VA
    PN -->|clarify| BC
    PN -->|unsupported| BR
    PN -->|finish 或预算耗尽| GA[generate_answer<br/>基于证据生成结构化回答]

    VA -->|合法| ET[execute_one_tool<br/>执行一个确定性工具]
    VA -->|可最小修复一次| RA[repair_action<br/>规则或 LLM 修复动作]
    VA -->|需重新规划| PN
    RA --> VA

    ET --> VT[validate_tool_result<br/>校验状态、错误与返回契约]
    VT --> ME[merge_evidence<br/>按 evidence_id 合并证据]
    ME --> RE[review_evidence<br/>确定性检查加 LLM Evidence Reviewer]

    RE -->|仍有可解决缺口且预算允许| PN
    RE -->|证据充分、不可重试失败、无新增证据或达到上限| GA

    GA -->|LLM 成功| PS
    GA -->|LLM 失败| SE[structured_error<br/>返回结构化错误，不编造答案]
    SE --> PS
    PS --> END([LangGraph 本轮结束])
    END -. run_agent 返回后 .-> TRACE[写入统一 SQLite<br/>节点、工具、证据、缺口、模型调用与耗时]
```

两条主路径的实际含义：

- **Direct Fast Path**：请求解析后能够构造唯一合法动作，不调用 Planner；工具结果仍必须经过结果校验、Evidence Ledger、证据审查和 Final Answer Skill。
- **Bounded Investigation**：Planner 每轮只产生一个 `AgentAction`，执行和审查后决定继续调查或回答；受 `max_steps`、`max_total_tool_calls`、无新增证据、不可重试错误及文档搜索次数等条件约束，不会无限 ReAct。
- **Clarification / Refusal**：缺少唯一必要条件时澄清，超出数据与能力边界时拒绝；两者均不应调用无关工具。
- **Structured Error**：最终回答模型失败时进入结构化错误节点，不使用确定性模板冒充模型回答。

### 当前验证基线

| 验证层 | 输入或命令 | 结果 |
| --- | --- | --- |
| 完整自动化回归 | `F:\conda_envs\FinTrace\python.exe -m pytest -q --basetemp=.tmp_tests` | `225 passed`（2026-08-24） |
| 历史会话恢复冒烟 | `GET /users/{user_id}/sessions/{session_id}` | 已从 SQLite 恢复回答、工具、证据、节点和 LLM 记录 |
| 前端契约验证 | `npm run build` | Next.js 生产构建通过（2026-08-24） |

该基线证明代码路径能够端到端运行，不证明竞赛准确率已经达标。真实模型冒烟受模型、网络和数据命中影响，应把具体运行写入 `08-统一评测清单与实施记录.md`，不能用单次成功替代冻结评测。

## 会话与记忆

当前 `SessionStore` 持久化四类字段：最近消息、结构化 `current_context`、`conversation_summary` 和 `verified_findings`。结构化上下文保存公司、人物、比较对象、日期、指标和任务；模型猜测、失败结果和未经核验的数字不得进入 `verified_findings`。

当前每次恢复最近 8 条消息，并由请求解析结合结构化上下文处理指代。0.5M Token 累计历史下的分级记忆检索仍是竞赛目标，仓库当前没有能够证明该指标的长上下文检索器或正式压力报告；审查时不得把 SQLite 已持久化等同于 0.5M 召回已实现。

## 请求解析与可回答性

请求解析依次产出：原始问题、显式与继承的实体候选、时间锚点/范围、任务族、指标/主题、约束和歧义。实体消歧必须返回可审计候选，不能在公司名称不唯一时默认选择。相对时间应相对会话时间或明确锚点解析。

`PreAnswerability` 的代码状态只有三种：

- `routeable`：存在可用能力且必要参数完整，或可以进入调查模式继续取证；
- `clarification_required`：缺失会导致主体、任务或比较维度无法唯一确定的必要参数；
- `unsupported`：系统没有对应数据或能力。实时行情、用户账户和无证据预测由任务族映射到此状态。

在线回答状态另行记录 `answered`、`partially_answered`、`clarification_required`、`unsupported`、`insufficient_evidence`、`failed`，不得与离线评测标签混用。

`clarification_required` 只用于缺少的信息会导致主体、任务或比较维度无法唯一确定、因而不能可靠执行的情况。系统已经能够按确定性口径执行但覆盖不完整时，应执行并返回 `partially_answered`；工具运行后仍无核心证据时使用 `insufficient_evidence`，不能用澄清代替数据不足。

## 工具规划与校验

简单、参数完整、操作唯一的事实请求走确定性 Direct Gate。其余请求进入有界调查循环：每轮只规划一个 `AgentAction`，包含工具、操作、参数、目的和预期证据。动作校验器负责能力状态、参数、实体、时间、截止日、重复调用、预算和操作组合；失败时仅允许一次最小修复或重新规划。

工具层处理数值计算、日期过滤、图路径、文本检索和事件排序。LLM 不得自行计算财务公式、编造引用、以语言推断控制链，或绕过数据截止日。

综合财务风险调查先由 `financial_analysis.risk_scan` 返回v2逐期间观察值、状态、阈值和输入证据，再用 `event_timeline` 检查问询、处罚、更正和审计事件；只有用户需要原因、金额、解释或整改细节时，才将文档类型限定为 `announcement` 调用 `document_search`。`research_analysis` 仅在用户询问机构观点或 Reviewer 明确提出观点证据缺口时使用。`insufficient_data` 与 `not_applicable` 均不得表述为低风险；阈值未完成金标校准前不生成综合风险分。

风险期间由确定性解析器在Gate B前完成。一个用户目标期间扩展为截至该目标的全部同口径历史；未指定期间时优先采用截止日前全部可用年度，没有年度数据时选择数据最多的一组同口径中报或季报；多个明确期间保持原样。不同累计口径不会混合比较。只有一个可用期间时仍执行点时规则，跨期规则和连续性规则返回 `insufficient_data`，最终回答按覆盖情况降级。Action Validator要求工具参数与解析结果逐项一致，阻止LLM Planner写入示例年份或猜测年份。

补充核验工具返回 `DATA_NOT_AVAILABLE` 时表示当前数据源未命中，应记录为证据缺口并继续或降级回答；如果已有财务证据，不得因为事件或公告补充查询为空而抹掉整个风险分析结果。

事件查询先由 `event_timeline` 返回结构化节点、阶段、日期精度和公告证据。`event_cluster` 仅在共享文号或标题主题相似度达到阈值时合并同类型节点，并返回 `match_reasons`；跨类型关系只允许基于共享文号生成。需要处罚金额、事件原因或正文细节时，Agent继续调用 `document_search`，不得将标题摘要扩写成正文事实。空结果诊断可用于调整类型或时间条件，但“索引未命中”不能改写成“现实中从未发生”。

研报问题采用相同的分层模式。询问“机构如何评价、给出什么评级/预测/风险提示”时，先调用 `research_analysis.view_query` 获取带机构、日期、观点类型和来源定位的结构化观点；询问“为何得出该判断、原文如何论证、具体上下文是什么”时，Planner先取观点，再将研报类型过滤传给 `document_search`。若用户从一开始就只要原文片段，可直接检索。研报观点、研报引用事实和系统独立核实事实在 Evidence Ledger 中保持不同认识论状态，不得相互升级。

因此事件与研报共享同一决策骨架，但证据性质不同：公告标题事件是可追溯的事件节点候选；研报记录证明的是“某机构曾作出某项陈述”。二者的结构化工具负责高精度筛选，`document_search` 负责补充原文，不负责取代事件分类或观点归属。

## Evidence Ledger 与回答

Evidence Ledger 将每个工具结果规范为带 `evidence_id` 的事实单元，记录来源、对象、时间、原始定位、计算输入/公式、质量标记和限制。Evidence Reviewer 检查每个用户子问题是否已有充分且相互一致的证据；若否，明确证据缺口并在预算内继续调查或降级回答。

最终回答按事实、推论、机构观点和限制分层表达：事实必须能回指证据；推论必须陈述依据与不确定性；研报观点必须标明归属；风险信号不得写为造假定论。答案生成失败返回结构化错误而非模板化伪答案。

## Prompt、Trace 与治理

Prompt 正文、版本和装配唯一以 `prompts/` 为准。全局策略与当前 Skill 组合为系统提示，运行时上下文独立注入；能力注册表、工具 Schema、数据截止日和预算由程序动态提供，禁止硬编码进 Prompt。

每次模型调用记录 prompt ID/version、模型、温度、输入哈希、输出 schema 版本和延迟。每轮 Trace 记录解析结果、候选能力、动作历史、校验/修复、工具摘要、证据、缺口、终止原因和版本信息；不得记录或展示不可验证的私有推理链。

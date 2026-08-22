# 00｜Prompt Manifest 与依赖规范

## 对应文件

`prompts/00_prompt_manifest.md`

## 目的

定义 Prompt 文件的依赖关系、运行时组装方式、输入输出契约和版本管理规则。此文件本身不直接发送给模型。

## Prompt 组装规则

```python
def assemble_prompt(skill_name, dynamic_context):
    return {
        "system": load("01_global_policy.md") + "\n\n" + load(f"{skill_name}.md"),
        "context": dynamic_context,
    }
```

禁止：

- 在 `03_next_action_planner.md` 内复制 `01_global_policy.md` 全文；
- 在 `06_final_answer.md` 内复制 Evidence Reviewer 规则；
- 将 Capability Registry 固化到 Prompt；
- 将当前 Tool 实现状态硬编码到多个 md 文件。

## 依赖清单

```yaml
skills:
  request_parser:
    file: 02_request_parser.md
    prompt_dependencies:
      - 01_global_policy.md
    runtime_dependencies:
      - raw_query
      - recent_context
      - current_context
      - deterministic_entity_candidates
      - deterministic_time_candidates
    output: ParsedRequest

  next_action_planner:
    file: 03_next_action_planner.md
    prompt_dependencies:
      - 01_global_policy.md
    runtime_dependencies:
      - ParsedRequest
      - CurrentContext
      - CandidateCapabilities
      - EvidenceLedger
      - EvidenceGaps
      - ToolCallHistory
      - RemainingBudget
    output: AgentAction

  evidence_reviewer:
    file: 04_evidence_reviewer.md
    prompt_dependencies:
      - 01_global_policy.md
    runtime_dependencies:
      - ParsedRequest
      - VerifiedClaims
      - EvidenceLedger
      - ToolCallHistory
    output: EvidenceReview

  action_repair:
    file: 05_action_repair.md
    prompt_dependencies:
      - 01_global_policy.md
    runtime_dependencies:
      - FailedAction
      - ValidatorError
      - CapabilityDefinition
      - ParsedRequest
    output: ActionRepairResult

  final_answer:
    file: 06_final_answer.md
    prompt_dependencies:
      - 01_global_policy.md
    runtime_dependencies:
      - raw_query
      - ResolvedContext
      - AnswerStatus
      - VerifiedClaims
      - SupportingEvidence
      - Limitations
    output: FinalAnswer

  memory_summarizer:
    file: 07_memory_summarizer.md
    optional: true
    prompt_dependencies:
      - 01_global_policy.md
    runtime_dependencies:
      - finalized_turn
      - previous_context
      - verified_findings
    output: MemoryUpdate

  search_query_rewriter:
    file: 08_search_query_rewriter.md
    optional: true
    prompt_dependencies:
      - 01_global_policy.md
    runtime_dependencies:
      - EvidenceGap
      - ResolvedContext
      - DocumentSearchCapability
    output: SearchQuerySpec
```

## 依赖不是文本 include

`02 → 03` 的依赖表示 Planner 消费 `ParsedRequest`，**不表示 `03_next_action_planner.md` 要包含 `02_request_parser.md` 的正文**。

正确关系：

```
02 Request Parser
→ ParsedRequest 数据对象
→ 03 Next Action Planner
```

而不是：

```
03 Prompt = 01全文 + 02全文 + 03全文
```

## 程序侧动态依赖

以下对象必须来自程序，不作为 Prompt 文件：

```
CapabilityRegistry
ToolSchema
MetricRegistry
EntityAliasIndex
knowledge_cutoff
ToolBudget
ValidatorRules
ImplementedOperations
```

## 建议版本头

每个 md 文件开头保留：

```yaml
prompt_id: fintrace.<name>
version: 1.0.0
depends_on:
  - fintrace.global_policy@1.x
input_schema: <schema>
output_schema: <schema>
```

## Trace 要求

每次 LLM 调用至少记录：

```
prompt_id
prompt_version
model_name
temperature
input_hash
output_schema_version
latency_ms
```

Prompt 修改必须能够通过评测集进行 A/B Test，不建议直接覆盖线上版本而不保留版本号。

## Prompt 语言规范

FinTrace 当前 Prompt 统一采用：

> **中文语义规则 + 英文工程协议**
> 

具体约定：

- Role、Goal、Decision Rules、Constraints、Few-shot、Counterexample：使用中文；
- `prompt_id`、文件名、Pydantic Class、Input/Output Schema、JSON Key、Capability、Tool、Operation、Error Code、状态枚举：保持英文；
- 不在同一 Prompt 中进行中英双语重复翻译；
- Runtime 注入对象保持代码侧原始英文标识，不为 Prompt 单独建立中文别名；
- 当前语言版本统一标记：`language: zh-CN`；
- 本轮中文化不改变 Schema 和 Skill 职责，版本由 `1.0.0` 升级为 `1.1.0`。

推荐示例：

```
你是 FinTrace 的 Next Action Planner。
你的任务是根据当前 Evidence Gap，只选择一个最优的下一动作。

如果当前 Evidence 已足以回答用户核心问题，返回 `finish`。
如果必须调用工具，只能从 `candidate_capabilities` 中选择。
```

不推荐：

```
只能基于证据回答。
Only answer based on evidence.
```

原因：中英双语重复会增加 Token、维护成本和规则漂移风险。
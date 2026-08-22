---
prompt_id: fintrace.prompt_manifest
version: 1.2.0
language: zh-CN
depends_on: []
output_schema: null
---

# Prompt Manifest 与依赖规范

本文件不发送给模型。它定义 `prompts/` 目录下各 Skill 的职责、依赖与运行时数据契约，供加载校验与版本管理使用。

## 组装规则

System Prompt = `01_global_policy.md` + 当前 Skill Prompt。
用户输入 = Runtime Context（结构化 JSON），独立于 Prompt 文件。

禁止：

- Skill Prompt 之间互相复制全文；
- 将 Capability Registry、Tool Schema、`knowledge_cutoff`、Tool Budget、implemented 状态固化进 Prompt 文件。

## Skill 清单

| skill | 文件 | 核心 | 输出 Schema |
| --- | --- | --- | --- |
| request_parser | 02_request_parser.md | 是 | ParsedRequest |
| next_action_planner | 03_next_action_planner.md | 是 | AgentAction |
| evidence_reviewer | 04_evidence_reviewer.md | 是 | EvidenceReview |
| action_repair | 05_action_repair.md | 是 | ActionRepairResult |
| final_answer | 06_final_answer.md | 是 | FinalAnswer |

当前 `02` 至 `06` Skill 的 `depends_on` 均为 `fintrace.global_policy@1.x`。Capability 名称、implemented 状态、必填参数与参数上限仍由 Runtime Registry / Tool Schema 注入；Prompt 只维护决策与证据边界。

## 运行时数据契约

| skill | runtime_dependencies |
| --- | --- |
| request_parser | raw_query, recent_context, current_context, deterministic_entity_candidates, deterministic_time_candidates |
| next_action_planner | ParsedRequest, CurrentContext, CandidateCapabilities, EvidenceLedger, EvidenceGaps, ToolCallHistory, RemainingBudget |
| evidence_reviewer | ParsedRequest, VerifiedClaims, EvidenceLedger, ToolCallHistory, AvailableCapabilities |
| action_repair | FailedAction, ValidatorError, CapabilityDefinition, ToolSchema, ParsedRequest, RepairBudget |
| final_answer | raw_query, ResolvedContext, AnswerStatus, VerifiedClaims, SupportingEvidence, Limitations |

## Trace 要求

每次 LLM 调用记录：`prompt_id`、`prompt_version`、`model_name`、`temperature`、`input_hash`、`output_schema_version`、`latency_ms`。

## 版本策略

- patch：措辞优化，不改变决策边界；
- minor：增加字段、规则或 few-shot，保持兼容；
- major：修改输入/输出 Schema 或 Skill 职责边界。

修改 `01_global_policy.md` 视为高影响变更，需全 Skill 回归。

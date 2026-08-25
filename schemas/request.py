"""Data contracts for the online Agent request/action/evidence pipeline (docs/13, docs/11)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TaskFamily = Literal[
    "financial_metric_query",
    "financial_metric_compare",
    "financial_investigation",
    "ownership_snapshot",
    "ownership_compare",
    "ownership_penetration",
    "document_retrieval",
    "event_query",
    "event_investigation",
    "research_view_query",
    "research_investigation",
    "realtime_market_query",
    "user_account_query",
    "prediction_request",
    "general_financial_explanation",
    "unknown",
]


class EntityCandidate(BaseModel):
    """An entity term found in the query with its resolution outcome."""

    term: str
    company_ids: list[str] = Field(default_factory=list)
    status: Literal["resolved", "ambiguous", "not_found"] = "not_found"


class ParsedRequest(BaseModel):
    """Structured user request (Gate A). Describes the need, never picks a tool."""

    raw_query: str
    entities: list[str] = Field(default_factory=list)  # canonical company ids (windcodes)
    entity_candidates: list[EntityCandidate] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)  # holder/person names (unresolved terms)
    periods: list[str] = Field(default_factory=list)  # report periods, ISO dates
    requested_periods: list[str] = Field(default_factory=list)  # periods explicitly stated by the user
    target_period: str | None = None
    period_type: str | None = None
    period_resolution_mode: Literal[
        "not_required", "explicit", "history_until_target", "all_available_fy",
        "all_available_comparable", "latest_available", "latest_two_comparable", "data_unavailable"
    ] = "not_required"
    as_of_dates: list[str] = Field(default_factory=list)  # ownership observation points
    start_date: str | None = None
    end_date: str | None = None
    time_mode: Literal["unspecified", "explicit", "today", "latest", "recent"] = "unspecified"
    task_family: TaskFamily = "unknown"
    metrics: list[str] = Field(default_factory=list)
    focus_topics: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    research_claim_types: list[str] = Field(default_factory=list)
    institutions: list[str] = Field(default_factory=list)
    comparison_type: Literal["cross_period", "cross_entity", "none", "ambiguous"] = "none"
    requires_explanation: bool = False
    requires_investigation: bool = False
    requires_realtime: bool = False
    requires_prediction: bool = False
    capability_gaps: list[str] = Field(default_factory=list)
    unresolved_references: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    parsed_by: Literal["rule", "llm"] = "rule"


class AgentAction(BaseModel):
    """One next action. The planner (ReAct) emits exactly one per round."""

    action: Literal["call_tool", "finish", "clarify", "unsupported"]
    capability: str | None = None
    tool_name: str | None = None
    operation: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    target_gap_id: str | None = None
    reason: str = ""
    expected_evidence: str | None = None


class ActionRepairResult(BaseModel):
    """Outcome of the action-repair skill (05)."""

    status: Literal["repaired", "replan_required", "clarification_required", "non_repairable"]
    repaired_action: AgentAction | None = None
    reason: str = ""


class EvidenceGap(BaseModel):
    gap_id: str
    description: str
    priority: Literal["high", "medium", "low"] = "medium"
    candidate_capabilities: list[str] = Field(default_factory=list)
    resolvable: bool = True


class CoveredAspect(BaseModel):
    aspect: str
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceReview(BaseModel):
    status: Literal["sufficient", "continue", "partial", "insufficient"]
    covered_aspects: list[CoveredAspect] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    reason: str = ""


class FinalAnswer(BaseModel):
    answer: str
    used_evidence_ids: list[str] = Field(default_factory=list)
    limitations_disclosed: list[str] = Field(default_factory=list)


class PreAnswerability(BaseModel):
    status: Literal["routeable", "routeable_with_gaps", "clarification_required", "unsupported"]
    capability: str | None = None
    reason: str = ""
    missing_slots: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class CapabilityDescriptor(BaseModel):
    name: str
    implemented: bool
    tool: str | None = None
    operation: str | None = None
    required_slots: list[str] = Field(default_factory=list)
    supports_knowledge_cutoff: bool = False
    description: str = ""


class LlmCallRecord(BaseModel):
    prompt_id: str
    prompt_version: str
    model: str
    temperature: float = 0.0
    input_hash: str
    output_schema: str
    latency_ms: int = 0
    status: Literal["success", "recovered", "failed"] = "success"
    attempt_count: int = 1
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    response_chars: int = 0
    error_type: str | None = None
    error_message: str | None = None


class ToolCallEntry(BaseModel):
    """Audit entry per executed tool call in the investigation loop."""

    tool_name: str
    operation: str | None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    action_reason: str = ""

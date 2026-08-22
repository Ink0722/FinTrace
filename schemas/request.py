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
    as_of_dates: list[str] = Field(default_factory=list)  # ownership observation points
    start_date: str | None = None
    end_date: str | None = None
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
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceReview(BaseModel):
    status: Literal["sufficient", "continue", "partial", "insufficient"]
    covered_aspects: list[CoveredAspect] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    reason: str = ""


class Claim(BaseModel):
    claim_id: str
    text: str
    status: Literal["verified", "partial", "unsupported"] = "verified"
    evidence_ids: list[str] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    answer: str
    used_claim_ids: list[str] = Field(default_factory=list)
    used_evidence_ids: list[str] = Field(default_factory=list)
    limitations_disclosed: list[str] = Field(default_factory=list)


class PreAnswerability(BaseModel):
    status: Literal["routeable", "clarification_required", "unsupported"]
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


class ToolCallEntry(BaseModel):
    """Audit entry per executed tool call in the investigation loop."""

    tool_name: str
    operation: str | None
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    action_reason: str = ""

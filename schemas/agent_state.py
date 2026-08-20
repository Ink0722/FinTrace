from typing import Any

from pydantic import BaseModel, Field

from schemas.evidence import Evidence
from schemas.request import AgentAction, EvidenceGap, LlmCallRecord, ParsedRequest, PreAnswerability, ToolCallEntry
from schemas.tool_calls import ExecutionPlan
from schemas.tool_results import ToolResult


class Message(BaseModel):
    role: str
    content: str


class CurrentContext(BaseModel):
    company_ids: list[str] = Field(default_factory=list)
    company_names: list[str] = Field(default_factory=list)
    person_id: str | None = None
    person_name: str | None = None
    report_periods: list[str] = Field(default_factory=list)
    focus_topics: list[str] = Field(default_factory=list)
    active_topic: str | None = None
    comparison_targets: list[str] = Field(default_factory=list)


class UserRequest(BaseModel):
    raw_query: str
    normalized_query: str | None = None
    intent: str | None = None


class AgentState(BaseModel):
    session_id: str
    messages: list[Message] = Field(default_factory=list)
    current_context: CurrentContext = Field(default_factory=CurrentContext)
    user_request: UserRequest
    execution_plan: ExecutionPlan | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    evidence_ledger: list[Evidence] = Field(default_factory=list)
    validation_results: list[dict[str, Any]] = Field(default_factory=list)
    retry_count: int = 0
    conversation_summary: str = ""
    previous_findings: list[dict[str, Any]] = Field(default_factory=list)
    final_answer: str | None = None
    workflow_status: str = "running"
    next_action: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    llm_status: str | None = None
    executed_nodes: list[str] = Field(default_factory=list)

    # --- Gate A/B/C pipeline (docs/13 §22) ---
    knowledge_cutoff: str | None = None
    parsed_request: ParsedRequest | None = None
    pre_answerability: PreAnswerability | None = None
    routing_mode: str | None = None  # direct | investigation
    candidate_capabilities: list[str] = Field(default_factory=list)

    current_action: AgentAction | None = None
    step_count: int = 0
    max_steps: int = 5
    total_tool_calls: int = 0
    max_total_tool_calls: int = 6
    tool_call_history: list[ToolCallEntry] = Field(default_factory=list)

    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    evidence_sufficient: bool = False
    review_status: str | None = None  # sufficient | continue | partial | insufficient
    no_new_evidence_rounds: int = 0

    repair_count: int = 0
    failed_actions: list[dict[str, Any]] = Field(default_factory=list)
    termination_reason: str | None = None
    answer_status: str | None = None  # answered | partially_answered | clarification_required | unsupported | insufficient_evidence | failed

    llm_calls: list[LlmCallRecord] = Field(default_factory=list)

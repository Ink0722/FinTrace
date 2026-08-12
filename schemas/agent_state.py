from typing import Any

from pydantic import BaseModel, Field

from schemas.evidence import Evidence
from schemas.tool_calls import ExecutionPlan
from schemas.tool_results import ToolResult


class Message(BaseModel):
    role: str
    content: str


class CurrentContext(BaseModel):
    company_id: str | None = None
    company_name: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    start_period: str | None = None
    end_period: str | None = None
    focus_topics: list[str] = Field(default_factory=list)


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

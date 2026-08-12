from typing import Any

from pydantic import BaseModel, Field

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.evidence import Evidence


class ToolError(BaseModel):
    error_type: ErrorType
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    candidate_entities: list[dict[str, Any]] = Field(default_factory=list)


class ToolMetrics(BaseModel):
    execution_time_ms: int = 0


class ToolResult(BaseModel):
    tool_call_id: str
    tool_name: ToolName
    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ToolError | None = None
    metrics: ToolMetrics = Field(default_factory=ToolMetrics)

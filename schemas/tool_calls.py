from typing import Any

from pydantic import BaseModel, Field

from schemas.enums import ToolName


class ToolCall(BaseModel):
    tool_call_id: str
    tool_name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str


class ExecutionPlan(BaseModel):
    plan_id: str
    user_intent: str
    tool_calls: list[ToolCall] = Field(default_factory=list)

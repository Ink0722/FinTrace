from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class MemoryUpdate(BaseModel):
    """Validated output of the rolling conversation summarizer."""

    summary: str
    open_questions: list[str] = Field(default_factory=list)


class VerifiedFinding(BaseModel):
    """Compact, evidence-bound fact retained as a long-term memory hint."""

    finding_id: str
    company_id: str | None = None
    topic: str
    fact: dict[str, Any]
    evidence_ids: list[str]
    source_turn_id: int
    source_tool: str | None = None

    @model_validator(mode="after")
    def require_evidence(self) -> "VerifiedFinding":
        if not self.evidence_ids:
            raise ValueError("verified finding requires at least one evidence_id")
        return self

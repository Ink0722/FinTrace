from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceSource(BaseModel):
    document_id: str | None = None
    company_id: str | None = None
    document_type: str | None = None
    page: int | None = None
    row_id: str | None = None
    source_path: str | None = None


class Evidence(BaseModel):
    evidence_id: str
    evidence_type: str
    source: EvidenceSource
    fact: dict[str, Any]
    support_level: Literal["direct", "derived", "weak"] = "direct"
    used_by: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

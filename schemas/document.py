from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    company_id: str
    document_type: str
    title: str
    publish_date: date
    page: int | None = None
    section: str | None = None
    text: str
    source_path: str | None = None


class DocumentSearchHit(BaseModel):
    chunk: DocumentChunk
    score: float
    evidence_id: str
    retrieval: dict[str, Any] = Field(default_factory=dict)

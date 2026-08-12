from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


EventType = Literal[
    "controller_change",
    "share_pledge",
    "regulatory_inquiry",
    "audit_opinion",
    "financial_restated",
    "major_litigation",
    "risk_warning",
]


class EventRecord(BaseModel):
    event_id: str
    company_id: str
    event_type: EventType
    event_date: date
    entities: list[str] = Field(default_factory=list)
    title: str
    summary: str
    source_document_ids: list[str] = Field(default_factory=list)
    evidence_id: str | None = None
    source_path: str | None = None
    page: int | None = None


class EventCluster(BaseModel):
    cluster_id: str
    company_id: str
    event_type: EventType
    start_date: date
    end_date: date
    title: str
    summary: str
    events: list[EventRecord] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

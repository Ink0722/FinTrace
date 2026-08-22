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
    "regulatory_penalty",
    "risk_warning",
]

EventStage = Literal[
    "initial",
    "progress",
    "response",
    "remediation",
    "resolution",
    "correction",
    "unknown",
]

DatePrecision = Literal["exact", "inferred", "announcement_only"]


class EventRecord(BaseModel):
    event_id: str
    company_id: str
    event_type: EventType
    event_date: date
    announcement_date: date | None = None
    effective_date: date | None = None
    date_precision: DatePrecision = "announcement_only"
    event_stage: EventStage = "unknown"
    entities: list[str] = Field(default_factory=list)
    agencies: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    topic_signature: str = ""
    title: str
    summary: str
    source_document_ids: list[str] = Field(default_factory=list)
    evidence_id: str | None = None
    source_path: str | None = None
    page: int | None = None
    extraction_method: str = "structured_record"
    quality_flags: list[str] = Field(default_factory=list)


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
    match_reasons: list[str] = Field(default_factory=list)


class EventRelation(BaseModel):
    source_event_id: str
    target_event_id: str
    relation_type: Literal["FOLLOWED_BY", "RESPONDS_TO", "REMEDIATES", "RESOLVES", "CORRECTS"]
    evidence_basis: Literal["shared_reference_id"]
    shared_reference_ids: list[str] = Field(default_factory=list)

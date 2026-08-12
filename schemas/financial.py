from typing import Literal

from pydantic import BaseModel, Field


class FinancialRecord(BaseModel):
    company_id: str
    report_period: str
    statement_scope: str = "CONSOLIDATED"
    statement_type: str
    item_code: str
    item_name_raw: str
    value_raw: float
    unit_raw: str = "CNY"
    value_cny: float
    source_document_id: str
    source_page: int | None = None
    evidence_id: str | None = None
    source_path: str | None = None


class FinancialMetric(BaseModel):
    company_id: str
    report_period: str
    metric_code: str
    value: float | None
    evidence_ids: list[str] = Field(default_factory=list)


class RiskSignal(BaseModel):
    rule_id: str
    name: str
    triggered: bool
    severity: Literal["low", "medium", "high"]
    score: int
    metrics: dict[str, float | None] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str

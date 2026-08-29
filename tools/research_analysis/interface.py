from __future__ import annotations

import sqlite3
import time
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.evidence import Evidence, EvidenceSource
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.research_analysis.config import ResearchAnalysisConfig
from tools.research_analysis.repository import ResearchRepository, validate_snapshot


CLAIM_TYPES = {
    "cited_fact", "analyst_judgment", "earnings_forecast",
    "investment_rating", "risk_opinion",
}
INDEX_RECOVERY_HINT = "Upload the prebuilt research_views.sqlite to the configured index path."


class ResearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["view_query"]
    query: str | None = None
    company_ids: list[str] = Field(min_length=1, max_length=10)
    start_date: date | None = None
    end_date: date | None = None
    institutions: list[str] | None = None
    claim_types: list[str] | None = None
    topics: list[str] | None = None
    knowledge_cutoff: date | None = None
    limit: int = Field(default=20, ge=1, le=200)

    @field_validator("company_ids")
    @classmethod
    def normalize_companies(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
        if not normalized:
            raise ValueError("company_ids must contain at least one valid identifier")
        return normalized

    @field_validator("claim_types")
    @classmethod
    def validate_claim_types(cls, values: list[str] | None) -> list[str] | None:
        unknown = sorted(set(values or []) - CLAIM_TYPES)
        if unknown:
            raise ValueError(f"unsupported claim_types: {unknown}")
        return values


def research_analysis(call: ToolCall) -> ToolResult:
    started = time.perf_counter()
    try:
        arguments = ResearchArguments.model_validate(call.arguments)
        if arguments.start_date and arguments.end_date and arguments.start_date > arguments.end_date:
            raise ValueError("start_date must be on or before end_date")
        config = ResearchAnalysisConfig.from_env()
    except (ValidationError, TypeError, ValueError) as exc:
        return failed(call, started, ErrorType.INVALID_ARGUMENT, f"Invalid research_analysis arguments or configuration: {exc}")
    repository = ResearchRepository(config.index_path)
    if not repository.available():
        return failed(call, started, ErrorType.DATA_NOT_AVAILABLE, f"Research index not found: {config.index_path}", details={"recovery_hint": INDEX_RECOVERY_HINT})
    snapshot_errors = validate_snapshot(config.index_path, config.research_path, config.chunks_path)
    if snapshot_errors:
        return failed(call, started, ErrorType.DATA_NOT_AVAILABLE, "Research index is stale or incomplete.", details={"errors": snapshot_errors, "recovery_hint": INDEX_RECOVERY_HINT})
    try:
        claims = repository.query_claims(
            company_ids=arguments.company_ids, start_date=arguments.start_date,
            end_date=arguments.end_date, institutions=arguments.institutions,
            claim_types=arguments.claim_types, topics=arguments.topics,
            knowledge_cutoff=arguments.knowledge_cutoff, limit=arguments.limit,
        )
    except sqlite3.Error as exc:
        return failed(call, started, ErrorType.TEMPORARY_DATABASE_ERROR, f"Research index query failed: {type(exc).__name__}: {exc}", retryable=isinstance(exc, sqlite3.OperationalError))
    if not claims:
        return failed(call, started, ErrorType.DATA_NOT_AVAILABLE, "No research views matched the requested filters and cutoff.", details={"company_ids": arguments.company_ids})
    evidence = [claim_evidence(item, call.tool_call_id) for item in claims]
    return ToolResult(
        tool_call_id=call.tool_call_id, tool_name=ToolName.RESEARCH_ANALYSIS,
        status=ToolStatus.SUCCESS,
        data={
            "operation": "view_query", "company_ids": arguments.company_ids,
            "claims": claims, "claim_count": len(claims),
            "claim_type_counts": count_types(claims),
            "data_source": "sqlite_research_views",
        },
        evidence=evidence,
        warnings=[
            "Research claims prove what an institution stated; they do not independently prove the underlying company facts."
        ],
        metrics=ToolMetrics(execution_time_ms=elapsed(started)),
    )


def claim_evidence(item: dict, used_by: str) -> Evidence:
    return Evidence(
        evidence_id=f"EVID-{item['claim_id']}",
        evidence_type=f"research_{item['claim_type']}",
        source=EvidenceSource(
            document_id=item["source_document_id"], company_id=item["company_id"],
            document_type="research_report", row_id=item.get("chunk_id"),
        ),
        fact={
            "claim_id": item["claim_id"], "claim_type": item["claim_type"],
            "institution": item["institution"], "publish_date": item["publish_date"],
            "topic": item.get("topic"), "stance": item["stance"],
            "claim_text": item["claim_text"], "source_span": item["source_span"],
            "epistemic_status": "attributed_research_view",
        },
        support_level="direct", used_by=[used_by],
    )


def count_types(claims: list[dict]) -> dict[str, int]:
    values: dict[str, int] = {}
    for item in claims:
        values[item["claim_type"]] = values.get(item["claim_type"], 0) + 1
    return values


def failed(call, started, error_type, message, *, retryable=False, details=None):
    return ToolResult(
        tool_call_id=call.tool_call_id, tool_name=ToolName.RESEARCH_ANALYSIS,
        status=ToolStatus.FAILED, data={}, evidence=[], warnings=[],
        error=ToolError(error_type=error_type, message=message, retryable=retryable, details=details or {}),
        metrics=ToolMetrics(execution_time_ms=elapsed(started)),
    )


def elapsed(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))

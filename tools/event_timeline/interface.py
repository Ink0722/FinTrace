from __future__ import annotations

import sqlite3
import time
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.event_timeline.config import EventTimelineConfig
from tools.event_timeline.repository import EventRepository, validate_event_index_snapshot
from tools.event_timeline.timeline import cluster_events, evidence_from_clusters
from tools.event_timeline.validation import SUPPORTED_EVENT_TYPES, validate_events


class EventTimelineArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["event_query", "event_cluster"]
    entity_ids: list[str] = Field(min_length=1, max_length=1)
    start_date: date | None = None
    end_date: date | None = None
    event_types: list[str] | None = None
    keywords: list[str] | None = None
    limit: int = Field(default=100, ge=1, le=500)
    window_days: int = Field(default=30, ge=1, le=365)
    knowledge_cutoff: date | None = None
    query: str | None = None

    @field_validator("entity_ids")
    @classmethod
    def normalize_entities(cls, values: list[str]) -> list[str]:
        return [value.strip().upper() for value in values]

    @field_validator("event_types")
    @classmethod
    def validate_event_types(cls, values: list[str] | None) -> list[str] | None:
        unknown = sorted(set(values or []) - SUPPORTED_EVENT_TYPES)
        if unknown:
            raise ValueError(f"unsupported event_types: {unknown}")
        return values

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip() for value in values if value.strip()]
        return normalized or None

    @model_validator(mode="after")
    def validate_dates(self) -> "EventTimelineArguments":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if self.operation == "event_query" and "window_days" in self.model_fields_set:
            raise ValueError("window_days is only valid for event_cluster")
        return self


def event_timeline(call: ToolCall) -> ToolResult:
    started = time.perf_counter()
    try:
        arguments = EventTimelineArguments.model_validate(call.arguments)
        config = EventTimelineConfig.from_env()
    except (ValidationError, TypeError, ValueError) as exc:
        return _failed(call, started, ErrorType.INVALID_ARGUMENT, f"Invalid event_timeline arguments or configuration: {exc}", details={"arguments": call.arguments})

    repository = EventRepository(config.index_path)
    if not repository.available():
        return _failed(call, started, ErrorType.DATA_NOT_AVAILABLE, f"Event index not found: {config.index_path}", details={"build_command": "python -m data_pipeline.events.build_index"})
    snapshot_errors = validate_event_index_snapshot(config.index_path, config.normalized_dir)
    if snapshot_errors:
        return _failed(call, started, ErrorType.DATA_NOT_AVAILABLE, "Event index is stale or incomplete; rebuild it from normalized announcements.", details={"errors": snapshot_errors, "build_command": "python -m data_pipeline.events.build_index"})
    try:
        events = repository.query_events(
            company_id=arguments.entity_ids[0], event_types=arguments.event_types,
            start_date=arguments.start_date, end_date=arguments.end_date,
            keywords=arguments.keywords, knowledge_cutoff=arguments.knowledge_cutoff,
            limit=arguments.limit,
        )
    except sqlite3.Error as exc:
        return _failed(call, started, ErrorType.TEMPORARY_DATABASE_ERROR, f"Event index query failed: {type(exc).__name__}: {exc}", retryable=isinstance(exc, sqlite3.OperationalError))
    if not events:
        return _failed(call, started, ErrorType.DATA_NOT_AVAILABLE, "No event records matched the requested company, filters and cutoff.", details={"company_id": arguments.entity_ids[0]})

    validation = validate_events(events)
    if validation.errors:
        return _failed(call, started, ErrorType.VALIDATION_FAILED, "Event records validation failed.", details={"errors": validation.errors})
    clusters = cluster_events(events, window_days=arguments.window_days) if arguments.operation == "event_cluster" else []
    evidence = evidence_from_clusters(clusters or cluster_events(events, window_days=1), used_by=call.tool_call_id)
    data = {
        "operation": arguments.operation,
        "entity_ids": arguments.entity_ids,
        "event_types": arguments.event_types,
        "events": [event.model_dump(mode="json") for event in events],
        "clusters": [cluster.model_dump(mode="json") for cluster in clusters],
        "summary": summarize_events(events, clusters),
        "knowledge_cutoff": arguments.knowledge_cutoff.isoformat() if arguments.knowledge_cutoff else None,
        "data_source": "sqlite_announcement_events",
        "message": f"event_timeline {arguments.operation} completed",
    }
    warnings = list(validation.warnings)
    if arguments.knowledge_cutoff is None:
        warnings.append("knowledge_cutoff was not provided; results use all announcements in the frozen event index.")
    if arguments.operation == "event_cluster":
        warnings.append("Event clusters express temporal and topical relatedness, not causality.")
    return ToolResult(tool_call_id=call.tool_call_id, tool_name=ToolName.EVENT_TIMELINE, status=ToolStatus.SUCCESS, data=data, evidence=evidence, warnings=list(dict.fromkeys(warnings)), metrics=ToolMetrics(execution_time_ms=_elapsed_ms(started)))


def summarize_events(events, clusters) -> dict:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    return {"event_count": len(events), "cluster_count": len(clusters), "event_type_counts": counts, "date_range": [events[0].event_date.isoformat(), events[-1].event_date.isoformat()] if events else None}


def _failed(call, started, error_type, message, *, retryable=False, details=None):
    return ToolResult(tool_call_id=call.tool_call_id, tool_name=ToolName.EVENT_TIMELINE, status=ToolStatus.FAILED, data={}, evidence=[], warnings=[], error=ToolError(error_type=error_type, message=message, retryable=retryable, details=details or {}), metrics=ToolMetrics(execution_time_ms=_elapsed_ms(started)))


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))

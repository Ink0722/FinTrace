from datetime import date

from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolMetrics, ToolResult
from tools.event_timeline.data_source import load_event_dataset
from tools.event_timeline.timeline import cluster_events, evidence_from_clusters, filter_events
from tools.event_timeline.validation import validate_events


def event_timeline(call: ToolCall) -> ToolResult:
    entity_ids = call.arguments.get("entity_ids")
    if not isinstance(entity_ids, list) or len(entity_ids) != 1 or not isinstance(entity_ids[0], str):
        return failed_result(
            call=call,
            error_type=ErrorType.INVALID_ARGUMENT,
            message="event_timeline currently requires entity_ids with exactly one company.",
            details={"entity_ids": entity_ids},
            warnings=[],
        )
    company_id = entity_ids[0]
    event_types = call.arguments.get("event_types")
    start_date = date.fromisoformat(call.arguments["start_date"]) if call.arguments.get("start_date") else None
    end_date = date.fromisoformat(call.arguments["end_date"]) if call.arguments.get("end_date") else None

    try:
        dataset = load_event_dataset(company_id=company_id)
    except Exception as exc:
        return failed_result(
            call=call,
            error_type=ErrorType.VALIDATION_FAILED,
            message="Event records could not be loaded.",
            details={"error": f"{type(exc).__name__}: {exc}"},
            warnings=[],
        )

    warnings = list(dataset.warnings)
    if dataset.strict and not dataset.events:
        return failed_result(
            call=call,
            error_type=ErrorType.DATA_NOT_AVAILABLE,
            message=f"No event records found for company_id={company_id}.",
            details={"company_id": company_id, "data_source": dataset.source_name},
            warnings=warnings,
        )

    validation = validate_events(dataset.events)
    warnings.extend(validation.warnings)
    if validation.errors:
        return failed_result(
            call=call,
            error_type=ErrorType.VALIDATION_FAILED,
            message="Event records validation failed.",
            details={"errors": validation.errors},
            warnings=warnings,
        )

    events = filter_events(
        dataset.events,
        company_id=company_id,
        event_types=event_types,
        start_date=start_date,
        end_date=end_date,
    )
    clusters = cluster_events(events)
    evidence = evidence_from_clusters(clusters, used_by=call.tool_call_id)
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.EVENT_TIMELINE,
        status=ToolStatus.SUCCESS,
        data={
            "entity_ids": entity_ids,
            "data_source": dataset.source_name,
            "event_types": event_types,
            "events": [event.model_dump() for event in events],
            "clusters": [cluster.model_dump() for cluster in clusters],
            "summary": summarize_events(events, clusters),
            "message": f"event_timeline executed with {dataset.source_name} event records",
        },
        evidence=evidence,
        warnings=warnings,
        metrics=ToolMetrics(execution_time_ms=0),
    )


def summarize_events(events, clusters) -> dict:
    type_counts: dict[str, int] = {}
    for event in events:
        type_counts[event.event_type] = type_counts.get(event.event_type, 0) + 1
    return {
        "event_count": len(events),
        "cluster_count": len(clusters),
        "event_type_counts": type_counts,
        "date_range": [events[0].event_date.isoformat(), events[-1].event_date.isoformat()] if events else None,
    }


def failed_result(call: ToolCall, error_type: ErrorType, message: str, details: dict, warnings: list[str]) -> ToolResult:
    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=ToolName.EVENT_TIMELINE,
        status=ToolStatus.FAILED,
        data={},
        evidence=[],
        warnings=warnings,
        error=ToolError(error_type=error_type, message=message, retryable=False, details=details),
        metrics=ToolMetrics(execution_time_ms=0),
    )

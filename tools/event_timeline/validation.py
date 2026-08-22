from dataclasses import dataclass, field

from schemas.event import EventRecord


SUPPORTED_EVENT_TYPES = {
    "regulatory_inquiry",
    "audit_opinion",
    "controller_change",
    "share_pledge",
    "financial_restated",
    "major_litigation",
    "regulatory_penalty",
    "risk_warning",
}


@dataclass
class EventValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_events(events: list[EventRecord]) -> EventValidationResult:
    result = EventValidationResult()
    seen: set[str] = set()
    announcement_only_count = 0
    for event in events:
        if not event.event_id:
            result.errors.append("Event missing event_id")
        elif event.event_id in seen:
            result.errors.append(f"Duplicate event_id: {event.event_id}")
        seen.add(event.event_id)
        if not event.company_id:
            result.errors.append(f"Event {event.event_id} missing company_id")
        if event.event_type not in SUPPORTED_EVENT_TYPES:
            result.warnings.append(f"Event {event.event_id} uses unsupported event_type: {event.event_type}")
        if not event.title and not event.summary:
            result.errors.append(f"Event {event.event_id} missing both title and summary")
        if event.date_precision == "announcement_only":
            announcement_only_count += 1
        if not event.evidence_id:
            result.warnings.append(f"Event {event.event_id} missing evidence_id; generated fallback evidence_id will be used")
        if not event.source_document_ids and not event.source_path:
            result.warnings.append(f"Event {event.event_id} missing source document/path")
    if announcement_only_count:
        result.warnings.append(
            f"{announcement_only_count} event(s) use announcement dates because exact event dates were not extracted"
        )
    return result

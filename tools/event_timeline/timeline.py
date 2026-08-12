from datetime import date

from schemas.event import EventCluster, EventRecord
from schemas.evidence import Evidence, EvidenceSource


def filter_events(
    events: list[EventRecord],
    company_id: str | None,
    event_types: list[str] | None,
    start_date: date | None,
    end_date: date | None,
) -> list[EventRecord]:
    allowed_types = set(event_types or [])
    filtered: list[EventRecord] = []
    for event in events:
        if company_id and event.company_id != company_id:
            continue
        if allowed_types and event.event_type not in allowed_types:
            continue
        if start_date and event.event_date < start_date:
            continue
        if end_date and event.event_date > end_date:
            continue
        filtered.append(event)
    return sorted(filtered, key=lambda item: item.event_date)


def entity_overlap(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def cluster_events(events: list[EventRecord], window_days: int = 30) -> list[EventCluster]:
    clusters: list[EventCluster] = []
    for event in events:
        matched: EventCluster | None = None
        for cluster in clusters:
            date_distance = abs((event.event_date - cluster.end_date).days)
            if (
                cluster.company_id == event.company_id
                and cluster.event_type == event.event_type
                and date_distance <= window_days
                and entity_overlap(event.entities, cluster.events[-1].entities) >= 0.3
            ):
                matched = cluster
                break

        if matched:
            matched.events.append(event)
            matched.end_date = max(matched.end_date, event.event_date)
            matched.summary = "；".join(item.summary for item in matched.events)
            matched.source_document_ids = sorted(
                {doc_id for item in matched.events for doc_id in item.source_document_ids}
            )
            matched.evidence_ids = [event_evidence_id(item) for item in matched.events]
        else:
            clusters.append(
                EventCluster(
                    cluster_id=f"CLUSTER-{len(clusters) + 1:03d}",
                    company_id=event.company_id,
                    event_type=event.event_type,
                    start_date=event.event_date,
                    end_date=event.event_date,
                    title=event.title,
                    summary=event.summary,
                    events=[event],
                    source_document_ids=list(event.source_document_ids),
                    evidence_ids=[event_evidence_id(event)],
                )
            )
    return clusters


def evidence_from_clusters(clusters: list[EventCluster], used_by: str) -> list[Evidence]:
    evidence: list[Evidence] = []
    for cluster in clusters:
        for event in cluster.events:
            document_id = event.source_document_ids[0] if event.source_document_ids else None
            evidence.append(
                Evidence(
                    evidence_id=event_evidence_id(event),
                    evidence_type="event_source",
                    source=EvidenceSource(
                        document_id=document_id,
                        company_id=event.company_id,
                        document_type=event.event_type,
                        page=event.page,
                        source_path=event.source_path,
                    ),
                    fact={
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "event_date": event.event_date.isoformat(),
                        "title": event.title,
                        "summary": event.summary,
                        "entities": event.entities,
                    },
                    used_by=[used_by],
                )
            )
    deduped: dict[str, Evidence] = {}
    for item in evidence:
        deduped[item.evidence_id] = item
    return list(deduped.values())


def event_evidence_id(event: EventRecord) -> str:
    if event.evidence_id:
        return event.evidence_id
    if event.source_document_ids:
        return f"EVID-{event.source_document_ids[0]}"
    return f"EVID-{event.event_id}"

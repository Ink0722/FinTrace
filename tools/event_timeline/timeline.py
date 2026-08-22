import hashlib
from datetime import date

from schemas.event import EventCluster, EventRecord, EventRelation
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


def topic_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_grams = {left[index:index + 2] for index in range(max(1, len(left) - 1))}
    right_grams = {right[index:index + 2] for index in range(max(1, len(right) - 1))}
    union = left_grams | right_grams
    return len(left_grams & right_grams) / len(union) if union else 0.0


def shared_reference_ids(left: EventRecord, right: EventRecord) -> list[str]:
    return sorted(set(left.reference_ids) & set(right.reference_ids))


def cluster_events(events: list[EventRecord], window_days: int = 30) -> list[EventCluster]:
    clusters: list[EventCluster] = []
    for event in events:
        matched: EventCluster | None = None
        for cluster in clusters:
            date_distance = abs((event.event_date - cluster.end_date).days)
            previous = cluster.events[-1]
            references = shared_reference_ids(event, previous)
            similarity = topic_similarity(event.topic_signature, previous.topic_signature)
            if (
                cluster.company_id == event.company_id
                and cluster.event_type == event.event_type
                and date_distance <= window_days
                and (bool(references) or similarity >= 0.45)
            ):
                matched = cluster
                reason = "shared_reference_id" if references else f"topic_similarity={similarity:.3f}"
                break

        if matched:
            matched.events.append(event)
            matched.end_date = max(matched.end_date, event.event_date)
            matched.summary = "；".join(item.summary for item in matched.events)
            matched.source_document_ids = sorted(
                {doc_id for item in matched.events for doc_id in item.source_document_ids}
            )
            matched.evidence_ids = [event_evidence_id(item) for item in matched.events]
            matched.match_reasons.append(reason)
        else:
            clusters.append(
                EventCluster(
                    cluster_id="CLUSTER-" + hashlib.sha256(
                        f"{event.company_id}|{event.event_type}|{event.event_id}".encode("utf-8")
                    ).hexdigest()[:16].upper(),
                    company_id=event.company_id,
                    event_type=event.event_type,
                    start_date=event.event_date,
                    end_date=event.event_date,
                    title=event.title,
                    summary=event.summary,
                    events=[event],
                    source_document_ids=list(event.source_document_ids),
                    evidence_ids=[event_evidence_id(event)],
                    match_reasons=["cluster_seed"],
                )
            )
    return clusters


def build_event_relations(events: list[EventRecord]) -> list[EventRelation]:
    relations: list[EventRelation] = []
    ordered = sorted(events, key=lambda item: (item.event_date, item.event_id))
    for index, target in enumerate(ordered):
        for source in reversed(ordered[:index]):
            if source.company_id != target.company_id:
                continue
            references = shared_reference_ids(source, target)
            if not references:
                continue
            relation_type = {
                "response": "RESPONDS_TO",
                "remediation": "REMEDIATES",
                "resolution": "RESOLVES",
                "correction": "CORRECTS",
            }.get(target.event_stage, "FOLLOWED_BY")
            is_backward_relation = relation_type != "FOLLOWED_BY"
            relations.append(EventRelation(
                source_event_id=target.event_id if is_backward_relation else source.event_id,
                target_event_id=source.event_id if is_backward_relation else target.event_id,
                relation_type=relation_type,
                evidence_basis="shared_reference_id",
                shared_reference_ids=references,
            ))
            break
    return relations


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
                        "announcement_date": event.announcement_date.isoformat() if event.announcement_date else None,
                        "effective_date": event.effective_date.isoformat() if event.effective_date else None,
                        "date_precision": event.date_precision,
                        "event_stage": event.event_stage,
                        "title": event.title,
                        "summary": event.summary,
                        "entities": event.entities,
                        "agencies": event.agencies,
                        "reference_ids": event.reference_ids,
                        "extraction_method": event.extraction_method,
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

from schemas.evidence import Evidence


def merge_evidence(existing: list[Evidence], incoming: list[Evidence]) -> list[Evidence]:
    by_id = {item.evidence_id: item for item in existing}
    for item in incoming:
        by_id[item.evidence_id] = item
    return list(by_id.values())

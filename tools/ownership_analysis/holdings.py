from __future__ import annotations

from dataclasses import replace

from tools.ownership_analysis.repository import HolderRecord


RANK_SOURCE = "calculated_by_holding_ratio"


def rank_holders(records: list[HolderRecord]) -> list[HolderRecord]:
    """Attach competition ranking by holding ratio (ties share the same rank)."""
    ordered = sorted(records, key=lambda record: (-record.holding_ratio, record.holder_name))
    ranked: list[HolderRecord] = []
    previous_ratio: float | None = None
    previous_rank = 0
    for index, record in enumerate(ordered, start=1):
        if previous_ratio is not None and record.holding_ratio == previous_ratio:
            rank = previous_rank
        else:
            rank = index
        ranked.append(replace(record, calculated_rank=rank))
        previous_ratio = record.holding_ratio
        previous_rank = rank
    return ranked


def duplicate_holder_flags(records: list[HolderRecord]) -> list[str]:
    seen: set[str] = set()
    for record in records:
        if record.holder_entity_id in seen:
            return ["duplicate_holder_in_snapshot"]
        seen.add(record.holder_entity_id)
    return []


def concentration(records: list[HolderRecord]) -> dict:
    """Snapshot-level concentration; always computed on the full effective snapshot."""
    ratios = sorted((record.holding_ratio for record in records), reverse=True)

    def top_sum(n: int) -> float:
        return round(sum(ratios[:n]), 6)

    return {
        "holder_count": len(records),
        "top1_ratio_sum": top_sum(1),
        "top3_ratio_sum": top_sum(3),
        "top5_ratio_sum": top_sum(5),
        "top10_ratio_sum": top_sum(10),
        "corporate_ratio_sum": round(
            sum(record.holding_ratio for record in records if record.holder_category == "COMPANY"), 6
        ),
        "person_ratio_sum": round(
            sum(record.holding_ratio for record in records if record.holder_category == "PERSON"), 6
        ),
        "rank_source": RANK_SOURCE,
    }


def holder_to_dict(record: HolderRecord) -> dict:
    return {
        "calculated_rank": record.calculated_rank,
        "holder_entity_id": record.holder_entity_id,
        "holder_name": record.holder_name,
        "holder_category": record.holder_category,
        "holding_quantity": record.holding_quantity,
        "holding_ratio": record.holding_ratio,
        "holding_ratio_raw_pct": record.holding_ratio_raw,
        "restricted_quantity": record.restricted_quantity,
        "share_category_code": record.share_category_code,
        "share_category_name": record.share_category_name,
        "quality_flags": list(record.quality_flags),
        "evidence_id": record.evidence_id,
    }


def compare_snapshots(
    start_records: list[HolderRecord], end_records: list[HolderRecord]
) -> dict:
    """Deterministically diff two effective snapshots of one company."""
    start_by_entity = {record.holder_entity_id: record for record in start_records}
    end_by_entity = {record.holder_entity_id: record for record in end_records}

    entered = [
        holder_to_dict(record)
        for record in end_records
        if record.holder_entity_id not in start_by_entity
    ]
    exited = [
        holder_to_dict(record)
        for record in start_records
        if record.holder_entity_id not in end_by_entity
    ]
    increased: list[dict] = []
    decreased: list[dict] = []
    unchanged_count = 0
    for entity_id, start_record in start_by_entity.items():
        end_record = end_by_entity.get(entity_id)
        if end_record is None:
            continue
        quantity_change = _delta(end_record.holding_quantity, start_record.holding_quantity)
        ratio_change_raw = round(end_record.holding_ratio_raw - start_record.holding_ratio_raw, 6)
        if quantity_change == 0 and ratio_change_raw == 0:
            unchanged_count += 1
            continue
        entry = {
            "holder_entity_id": entity_id,
            "holder_name": end_record.holder_name,
            "holder_category": end_record.holder_category,
            "start": _side_dict(start_record),
            "end": _side_dict(end_record),
            "quantity_change": quantity_change,
            "ratio_change_raw_pct": ratio_change_raw,
        }
        if quantity_change > 0 or ratio_change_raw > 0:
            increased.append(entry)
        else:
            decreased.append(entry)
    return {
        "entered": entered,
        "exited": exited,
        "increased": increased,
        "decreased": decreased,
        "unchanged_count": unchanged_count,
    }


def _side_dict(record: HolderRecord) -> dict:
    return {
        "holding_quantity": record.holding_quantity,
        "holding_ratio": record.holding_ratio,
        "holding_ratio_raw_pct": record.holding_ratio_raw,
        "calculated_rank": record.calculated_rank,
        "evidence_id": record.evidence_id,
    }


def _delta(end: float | None, start: float | None) -> float | None:
    if end is None or start is None:
        return None
    return end - start

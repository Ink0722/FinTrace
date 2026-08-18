from schemas.evidence import Evidence, EvidenceSource
from tools.ownership_analysis.repository import HolderRecord


def build_holding_evidence(
    records: list[HolderRecord], used_by: str, source_path: str
) -> list[Evidence]:
    return [
        Evidence(
            evidence_id=record.evidence_id,
            evidence_type="shareholder_holding",
            source=EvidenceSource(
                company_id=record.target_company_id,
                row_id=record.record_id,
                source_path=str(source_path),
            ),
            fact={
                "holder_name": record.holder_name,
                "holder_entity_id": record.holder_entity_id,
                "target_company_id": record.target_company_id,
                "holding_quantity": record.holding_quantity,
                "holding_ratio": record.holding_ratio,
                "holding_ratio_raw_pct": record.holding_ratio_raw,
                "holder_end_date": record.holder_end_date,
                "announcement_date": record.announcement_date,
                "share_category_name": record.share_category_name,
                "holder_category": record.holder_category,
            },
            used_by=[used_by],
        )
        for record in records
    ]

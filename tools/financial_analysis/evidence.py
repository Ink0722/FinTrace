from schemas.evidence import Evidence, EvidenceSource
from tools.financial_analysis.repository import FinancialMetricRecord


def build_financial_evidence(
    records: list[FinancialMetricRecord], used_by: str
) -> list[Evidence]:
    return [
        Evidence(
            evidence_id=record.evidence_id,
            evidence_type="financial_statement_metric",
            source=EvidenceSource(
                document_id=record.source_object_id,
                company_id=record.company_id,
                document_type=record.statement_name,
                row_id=record.source_object_id,
                source_path=record.source_table,
            ),
            fact={
                "company_id": record.company_id,
                "report_period": record.report_period,
                "metric_code": record.metric_code,
                "value": record.value,
                "currency": record.currency,
                "value_nature": record.value_nature,
                "announcement_date": record.announcement_date,
                "source_column": record.source_column,
                "mapping_version": record.mapping_version,
            },
            used_by=[used_by],
        )
        for record in records
    ]

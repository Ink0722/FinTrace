from dataclasses import dataclass, field

from schemas.financial import FinancialRecord


REQUIRED_ITEM_CODES = {"REVENUE", "NET_PROFIT", "OPERATING_CASHFLOW", "INVENTORY"}


@dataclass
class FinancialValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_financial_records(records: list[FinancialRecord]) -> FinancialValidationResult:
    result = FinancialValidationResult()
    seen: set[tuple[str, str, str]] = set()
    periods: dict[str, set[str]] = {}
    for record in records:
        key = (record.company_id, record.report_period, record.item_code)
        if key in seen:
            result.warnings.append(f"Duplicate financial record: {key}")
        seen.add(key)
        if not record.report_period:
            result.errors.append(f"Record {record.item_code} missing report_period")
        if not record.item_code:
            result.errors.append(f"Record in period {record.report_period} missing item_code")
        if record.value_cny is None:
            result.errors.append(f"Record {record.report_period}/{record.item_code} missing value_cny")
        if not record.source_document_id:
            result.warnings.append(f"Record {record.report_period}/{record.item_code} missing source_document_id")
        if not record.evidence_id:
            result.warnings.append(f"Record {record.report_period}/{record.item_code} missing evidence_id; generated fallback evidence_id will be used")
        periods.setdefault(record.report_period, set()).add(record.item_code)

    for period, item_codes in periods.items():
        missing = sorted(REQUIRED_ITEM_CODES - item_codes)
        if missing:
            result.warnings.append(f"Period {period} missing recommended item codes: {missing}")
    return result

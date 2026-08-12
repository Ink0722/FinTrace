import csv
from dataclasses import dataclass
from pathlib import Path

from schemas.financial import FinancialRecord


@dataclass
class CsvFinancialDataSource:
    records_path: Path
    name: str = "csv"

    def load_records(self, company_id: str) -> list[FinancialRecord]:
        with self.records_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            records: list[FinancialRecord] = []
            for row in rows:
                if (row.get("company_id") or "").strip() != company_id:
                    continue
                records.append(
                    FinancialRecord(
                        company_id=company_id,
                        report_period=(row.get("report_period") or "").strip(),
                        statement_scope=(row.get("statement_scope") or "CONSOLIDATED").strip() or "CONSOLIDATED",
                        statement_type=(row.get("statement_type") or "").strip(),
                        item_code=normalize_item_code(row.get("metric_code") or row.get("item_code")),
                        item_name_raw=(row.get("metric_name") or row.get("item_name_raw") or "").strip(),
                        value_raw=parse_float(row.get("value") or row.get("value_raw")),
                        unit_raw=(row.get("unit") or row.get("unit_raw") or "CNY").strip() or "CNY",
                        value_cny=parse_float(row.get("value_cny") or row.get("value") or row.get("value_raw")),
                        source_document_id=(row.get("source_doc_id") or row.get("source_document_id") or "").strip(),
                        source_page=parse_int(row.get("page") or row.get("source_page")),
                        evidence_id=(row.get("evidence_id") or "").strip() or None,
                        source_path=(row.get("source_path") or "").strip() or None,
                    )
                )
            return records


def normalize_item_code(value: str | None) -> str:
    return (value or "").strip().upper()


def parse_float(value: str | None) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError("Financial value is required.")
    return float(str(value).replace(",", "").strip())


def parse_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value).strip()))

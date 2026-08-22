from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tools.financial_analysis.metric_catalog import MAPPING_VERSION, SOURCE_FILES


@dataclass(frozen=True)
class FinancialMetricRecord:
    company_id: str
    report_period: str
    period_type: str
    statement_name: str
    statement_type_raw: str
    metric_code: str
    metric_name: str
    value: float
    currency: str
    value_nature: str
    announcement_date: str
    source_table: str
    source_column: str
    source_object_id: str
    company_type_code: str | None
    evidence_id: str
    mapping_version: str
    quality_flags: tuple[str, ...]

    def as_dict(self) -> dict:
        value = dict(self.__dict__)
        value["quality_flags"] = list(self.quality_flags)
        return value


class FinancialRepository:
    def __init__(self, index_path: Path):
        self.index_path = index_path

    def available(self) -> bool:
        return self.index_path.is_file()

    def query_metrics(
        self,
        *,
        company_ids: list[str],
        report_periods: list[str],
        metric_codes: list[str],
        statement_types: list[str] | None = None,
        currency: str = "CNY",
        knowledge_cutoff: date | None = None,
    ) -> list[FinancialMetricRecord]:
        clauses = [
            f"company_id IN ({_placeholders(company_ids)})",
            f"report_period IN ({_placeholders(report_periods)})",
            f"metric_code IN ({_placeholders(metric_codes)})",
            "currency = ?",
        ]
        params: list[str] = [*company_ids, *report_periods, *metric_codes, currency]
        if statement_types:
            clauses.append(f"statement_name IN ({_placeholders(statement_types)})")
            params.extend(statement_types)
        if knowledge_cutoff:
            clauses.append("announcement_date <= ?")
            params.append(knowledge_cutoff.isoformat())
        sql = f"""
            SELECT company_id, report_period, period_type, statement_name,
                   statement_type_raw, metric_code, metric_name, value, currency,
                   value_nature, announcement_date, source_table, source_column,
                   source_object_id, company_type_code, evidence_id,
                   mapping_version, quality_flags_json
            FROM financial_metrics
            WHERE {' AND '.join(clauses)}
            ORDER BY metric_code, report_period, company_id
        """
        with sqlite3.connect(self.index_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_available_periods(
        self,
        *,
        company_id: str,
        knowledge_cutoff: date | None = None,
    ) -> list[tuple[str, str]]:
        clauses = ["company_id = ?"]
        params = [company_id]
        if knowledge_cutoff:
            clauses.append("announcement_date <= ?")
            params.append(knowledge_cutoff.isoformat())
        sql = f"""
            SELECT report_period, period_type
            FROM financial_metrics
            WHERE {' AND '.join(clauses)}
            GROUP BY report_period, period_type
            ORDER BY report_period
        """
        with sqlite3.connect(self.index_path) as connection:
            return [(str(row[0]), str(row[1])) for row in connection.execute(sql, params).fetchall()]


def validate_index_snapshot(index_path: Path, normalized_dir: Path) -> list[str]:
    manifest_path = index_path.with_name("manifest.json")
    if not manifest_path.is_file():
        return [f"Financial index manifest not found: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Financial index manifest is invalid: {type(exc).__name__}: {exc}"]
    errors: list[str] = []
    if manifest.get("mapping_version") != MAPPING_VERSION:
        errors.append(
            f"mapping version mismatch: index={manifest.get('mapping_version')}, expected={MAPPING_VERSION}"
        )
    manifest_sources = manifest.get("sources")
    if not isinstance(manifest_sources, dict):
        return [*errors, "Financial index manifest has no sources object."]
    for statement_name, filename in SOURCE_FILES.items():
        source_path = normalized_dir / filename
        recorded = manifest_sources.get(statement_name)
        if not source_path.is_file():
            errors.append(f"normalized source not found: {source_path}")
            continue
        if not isinstance(recorded, dict):
            errors.append(f"manifest source missing: {statement_name}")
            continue
        stat = source_path.stat()
        if int(recorded.get("size", -1)) != stat.st_size:
            errors.append(f"normalized source size changed: {source_path}")
        if int(recorded.get("mtime_ns", -1)) != stat.st_mtime_ns:
            errors.append(f"normalized source modification time changed: {source_path}")
    return errors


def _placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def _row_to_record(row: sqlite3.Row) -> FinancialMetricRecord:
    return FinancialMetricRecord(
        company_id=row["company_id"],
        report_period=row["report_period"],
        period_type=row["period_type"],
        statement_name=row["statement_name"],
        statement_type_raw=row["statement_type_raw"],
        metric_code=row["metric_code"],
        metric_name=row["metric_name"],
        value=float(row["value"]),
        currency=row["currency"],
        value_nature=row["value_nature"],
        announcement_date=row["announcement_date"],
        source_table=row["source_table"],
        source_column=row["source_column"],
        source_object_id=row["source_object_id"],
        company_type_code=row["company_type_code"],
        evidence_id=row["evidence_id"],
        mapping_version=row["mapping_version"],
        quality_flags=tuple(json.loads(row["quality_flags_json"])),
    )

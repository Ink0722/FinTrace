from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from tools.financial_analysis.config import FinancialAnalysisConfig
from tools.financial_analysis.metric_catalog import (
    MAPPING_VERSION,
    METRIC_DEFINITIONS,
    SOURCE_FILES,
    period_type,
)


SCHEMA_SQL = """
PRAGMA synchronous = NORMAL;

CREATE TABLE financial_metrics (
    company_id TEXT NOT NULL,
    report_period TEXT NOT NULL,
    period_type TEXT NOT NULL,
    statement_name TEXT NOT NULL,
    statement_type_raw TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    currency TEXT NOT NULL,
    value_nature TEXT NOT NULL,
    announcement_date TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_column TEXT NOT NULL,
    source_object_id TEXT NOT NULL,
    company_type_code TEXT,
    evidence_id TEXT NOT NULL PRIMARY KEY,
    mapping_version TEXT NOT NULL,
    quality_flags_json TEXT NOT NULL
);

CREATE INDEX idx_financial_company_period_metric
ON financial_metrics(company_id, report_period, metric_code);

CREATE INDEX idx_financial_announcement
ON financial_metrics(company_id, announcement_date);

CREATE INDEX idx_financial_metric_period
ON financial_metrics(metric_code, report_period);
"""


def build_parser() -> argparse.ArgumentParser:
    config = FinancialAnalysisConfig.from_env()
    parser = argparse.ArgumentParser(description="Build the FinTrace financial metric index.")
    parser.add_argument("--normalized-dir", type=Path, default=config.normalized_dir)
    parser.add_argument("--output", type=Path, default=config.index_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_financial_index(args.normalized_dir, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_financial_index(normalized_dir: Path, output_path: Path) -> dict:
    started = time.perf_counter()
    sources = {name: normalized_dir / filename for name, filename in SOURCE_FILES.items()}
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing normalized financial files: {missing}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)
    definitions_by_statement = {
        statement: [item for item in METRIC_DEFINITIONS if item.statement_name == statement]
        for statement in SOURCE_FILES
    }
    source_rows: dict[str, int] = {}
    metric_rows: dict[str, int] = {}
    try:
        with closing(sqlite3.connect(temporary_path)) as connection:
            connection.executescript(SCHEMA_SQL)
            for statement_name, source_path in sources.items():
                source_count = 0
                metric_count = 0
                batch: list[tuple] = []
                with source_path.open("r", encoding="utf-8-sig") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        source_count += 1
                        row = json.loads(line)
                        common = _common_values(row, statement_name, source_path, line_number)
                        for definition in definitions_by_statement[statement_name]:
                            value = row.get(definition.source_column)
                            if value is None:
                                continue
                            batch.append(_metric_row(common, definition, float(value)))
                            metric_count += 1
                        if len(batch) >= 5000:
                            _insert_rows(connection, batch)
                            batch.clear()
                if batch:
                    _insert_rows(connection, batch)
                source_rows[statement_name] = source_count
                metric_rows[statement_name] = metric_count
            connection.commit()
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    manifest = {
        "status": "complete",
        "mapping_version": MAPPING_VERSION,
        "index_path": str(output_path),
        "source_rows": source_rows,
        "metric_rows": metric_rows,
        "total_metric_rows": sum(metric_rows.values()),
        "sources": {
            name: {
                "path": str(path),
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "sha256": _sha256(path),
            }
            for name, path in sources.items()
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    manifest_path = output_path.with_name("manifest.json")
    _write_json_atomic(manifest_path, manifest)
    return manifest


def _common_values(row: dict, statement_name: str, source_path: Path, line_number: int) -> dict:
    company_id = str(row.get("s_info_windcode") or "").strip()
    report_period = str(row.get("report_period") or "").strip()
    object_id = str(row.get("object_id") or "").strip()
    announcement_date = str(row.get("actual_ann_dt") or row.get("ann_dt") or "").strip()
    if not company_id or not report_period or not object_id or not announcement_date:
        raise ValueError(f"Missing financial key field in {source_path}:{line_number}")
    flags: list[str] = []
    wind_code = str(row.get("wind_code") or "").strip()
    if wind_code and wind_code != company_id:
        flags.append("wind_code_mismatch")
    if period_type(report_period) == "NON_STANDARD":
        flags.append("non_standard_period")
    return {
        "company_id": company_id,
        "report_period": report_period,
        "period_type": period_type(report_period),
        "statement_name": statement_name,
        "statement_type_raw": str(row.get("statement_type") or "").strip(),
        "currency": str(row.get("crncy_code") or "CNY").strip() or "CNY",
        "announcement_date": announcement_date,
        "source_table": str(source_path),
        "source_object_id": object_id,
        "company_type_code": str(row.get("comp_type_code") or "").strip() or None,
        "quality_flags": flags,
    }


def _metric_row(common: dict, definition, value: float) -> tuple:
    evidence_key = f"{common['source_object_id']}|{definition.metric_code}|{MAPPING_VERSION}"
    evidence_id = "EVID-FIN-" + hashlib.sha256(evidence_key.encode("utf-8")).hexdigest()[:24].upper()
    return (
        common["company_id"],
        common["report_period"],
        common["period_type"],
        common["statement_name"],
        common["statement_type_raw"],
        definition.metric_code,
        definition.name,
        value,
        common["currency"],
        definition.value_nature,
        common["announcement_date"],
        common["source_table"],
        definition.source_column,
        common["source_object_id"],
        common["company_type_code"],
        evidence_id,
        MAPPING_VERSION,
        json.dumps(common["quality_flags"], ensure_ascii=False, separators=(",", ":")),
    )


def _insert_rows(connection: sqlite3.Connection, rows: list[tuple]) -> None:
    connection.executemany(
        """
        INSERT INTO financial_metrics VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())

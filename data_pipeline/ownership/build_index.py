from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from tools.ownership_analysis.config import OWNERSHIP_MAPPING_VERSION, SHAREHOLDERS_FILENAME, OwnershipAnalysisConfig


SCHEMA_SQL = """
PRAGMA synchronous = NORMAL;

CREATE TABLE holder_records (
    record_id TEXT NOT NULL PRIMARY KEY,
    target_company_id TEXT NOT NULL,
    announcement_date TEXT NOT NULL,
    holder_end_date TEXT NOT NULL,
    report_period TEXT,
    holder_entity_id TEXT NOT NULL,
    holder_name TEXT NOT NULL,
    holder_aname TEXT,
    holder_category TEXT NOT NULL,
    holding_quantity INTEGER,
    holding_ratio REAL NOT NULL,
    holding_ratio_raw REAL NOT NULL,
    restricted_quantity INTEGER,
    share_category_code TEXT,
    share_category_name TEXT,
    memo TEXT,
    quality_flags_json TEXT NOT NULL,
    evidence_id TEXT NOT NULL
);

CREATE INDEX idx_holder_records_snapshot
ON holder_records(target_company_id, announcement_date, holder_end_date);

CREATE INDEX idx_holder_records_entity
ON holder_records(holder_entity_id);

CREATE TABLE holder_entities (
    holder_entity_id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    compcode TEXT,
    identity_quality TEXT NOT NULL,
    record_count INTEGER NOT NULL
);

CREATE INDEX idx_holder_entities_name ON holder_entities(name);
"""


class SkipRow(Exception):
    """Raised when one source row cannot be imported; the row is counted and skipped."""


@dataclass(frozen=True)
class ParsedRecord:
    row: tuple
    entity: dict


def build_parser() -> argparse.ArgumentParser:
    config = OwnershipAnalysisConfig.from_env()
    parser = argparse.ArgumentParser(description="Build the FinTrace shareholder holdings index.")
    parser.add_argument("--normalized-dir", type=Path, default=config.normalized_dir)
    parser.add_argument("--output", type=Path, default=config.index_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_ownership_index(args.normalized_dir, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_ownership_index(normalized_dir: Path, output_path: Path) -> dict:
    started = time.perf_counter()
    source_path = normalized_dir / SHAREHOLDERS_FILENAME
    if not source_path.is_file():
        raise FileNotFoundError(f"Missing normalized shareholder file: {source_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)

    stats: dict = {"parsed": 0, "skipped": 0, "skip_reasons": {}}
    entities: dict[str, dict] = {}
    try:
        with closing(sqlite3.connect(temporary_path)) as connection:
            connection.executescript(SCHEMA_SQL)
            batch: list[tuple] = []
            with source_path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        record = _parse_record(json.loads(line))
                    except json.JSONDecodeError:
                        _count_skip(stats, "invalid_json")
                        continue
                    except SkipRow as exc:
                        _count_skip(stats, str(exc))
                        continue
                    stats["parsed"] += 1
                    batch.append(record.row)
                    _accumulate_entity(entities, record.entity)
                    if len(batch) >= 5000:
                        _insert_records(connection, batch)
                        batch.clear()
            if batch:
                _insert_records(connection, batch)
            _insert_entities(connection, entities)
            connection.commit()
            inserted_rows = connection.execute("SELECT COUNT(*) FROM holder_records").fetchone()[0]
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    manifest = {
        "status": "complete",
        "mapping_version": OWNERSHIP_MAPPING_VERSION,
        "index_path": str(output_path),
        "source": {
            "path": str(source_path),
            "size": source_path.stat().st_size,
            "mtime_ns": source_path.stat().st_mtime_ns,
            "sha256": _sha256(source_path),
        },
        "rows": {
            "parsed": stats["parsed"],
            "inserted": inserted_rows,
            "duplicates_ignored": stats["parsed"] - inserted_rows,
            "skipped": stats["skipped"],
            "skip_reasons": stats["skip_reasons"],
        },
        "entities": {
            "total": len(entities),
            "resolved": sum(1 for item in entities.values() if item["identity_quality"] == "resolved"),
            "unresolved": sum(1 for item in entities.values() if item["identity_quality"] == "unresolved"),
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    manifest_path = output_path.with_name("manifest.json")
    _write_json_atomic(manifest_path, manifest)
    return manifest


def _parse_record(row: dict) -> ParsedRecord:
    company = str(row.get("s_info_windcode") or "").strip()
    announcement_date = str(row.get("ann_dt") or "").strip()
    holder_end_date = str(row.get("s_holder_enddate") or "").strip()
    holder_name = str(row.get("s_holder_name") or "").strip()
    if not company:
        raise SkipRow("missing_target_company")
    if not _is_iso_date(announcement_date):
        raise SkipRow("invalid_announcement_date")
    if not _is_iso_date(holder_end_date):
        raise SkipRow("invalid_holder_end_date")
    if not holder_name:
        raise SkipRow("missing_holder_name")
    try:
        pct_raw = float(row.get("s_holder_pct"))
    except (TypeError, ValueError):
        raise SkipRow("invalid_holding_ratio") from None
    if not 0 < pct_raw <= 100:
        raise SkipRow("invalid_holding_ratio")

    flags: list[str] = []
    raw_category = str(row.get("s_holder_holdercategory") or "").strip()
    if raw_category == "1":
        holder_category = "PERSON"
    elif raw_category == "2":
        holder_category = "COMPANY"
    else:
        holder_category = "COMPANY"
        flags.append("missing_holder_category")

    compcode = str(row.get("s_info_compcode") or "").strip() or None
    if compcode is None:
        flags.append("missing_compcode")

    quantity = _parse_int(row.get("s_holder_quantity"))
    if quantity is None:
        flags.append("missing_quantity")
    restricted_quantity = _parse_int(row.get("s_holder_restrictedquantity"))
    share_category_code = str(row.get("s_holder_sharecategory") or "").strip() or None
    share_category_name = str(row.get("s_holder_sharecategoryname") or "").strip() or None
    report_period = str(row.get("report_period") or "").strip() or None
    if report_period is None:
        flags.append("missing_report_period")
    holder_aname = str(row.get("s_holder_aname") or "").strip() or None
    memo = str(row.get("s_holder_memo") or "").strip() or None
    if announcement_date < holder_end_date:
        flags.append("announcement_before_holder_end")

    if compcode:
        holder_entity_id = f"{holder_category}:{compcode}"
        identity_quality = "resolved"
    else:
        digest = hashlib.sha1(holder_name.encode("utf-8")).hexdigest()[:12].upper()
        holder_entity_id = f"{holder_category}_UNRESOLVED:{digest}:{company}"
        identity_quality = "unresolved"

    hash_key = f"{company}|{announcement_date}|{holder_end_date}|{holder_name}|{share_category_code}|{quantity}|{pct_raw}|{compcode}"
    digest = hashlib.sha256(hash_key.encode("utf-8")).hexdigest()[:24].upper()
    record_id = f"REC-OWN-{digest}"
    evidence_id = f"EVID-OWN-{digest}"

    return ParsedRecord(
        row=(
            record_id,
            company,
            announcement_date,
            holder_end_date,
            report_period,
            holder_entity_id,
            holder_name,
            holder_aname,
            holder_category,
            quantity,
            pct_raw / 100,
            pct_raw,
            restricted_quantity,
            share_category_code,
            share_category_name,
            memo,
            json.dumps(flags, ensure_ascii=False, separators=(",", ":")),
            evidence_id,
        ),
        entity={
            "holder_entity_id": holder_entity_id,
            "name": holder_name,
            "entity_type": holder_category,
            "compcode": compcode,
            "identity_quality": identity_quality,
        },
    )


def _accumulate_entity(entities: dict[str, dict], entity: dict) -> None:
    existing = entities.get(entity["holder_entity_id"])
    if existing is None:
        entities[entity["holder_entity_id"]] = {**entity, "record_count": 1}
        return
    existing["record_count"] += 1
    if entity["compcode"]:
        existing["compcode"] = entity["compcode"]


def _insert_records(connection: sqlite3.Connection, rows: list[tuple]) -> None:
    connection.executemany(
        """
        INSERT OR IGNORE INTO holder_records VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows,
    )


def _insert_entities(connection: sqlite3.Connection, entities: dict[str, dict]) -> None:
    rows = [
        (
            item["holder_entity_id"],
            item["name"],
            item["entity_type"],
            item["compcode"],
            item["identity_quality"],
            item["record_count"],
        )
        for item in entities.values()
    ]
    connection.executemany(
        "INSERT INTO holder_entities VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def _count_skip(stats: dict, reason: str) -> None:
    stats["skipped"] += 1
    stats["skip_reasons"][reason] = stats["skip_reasons"].get(reason, 0) + 1


def _is_iso_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    try:
        time.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _parse_int(value) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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

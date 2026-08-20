from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_INDEX_PATH = PROJECT_ROOT / "data" / "indexes" / "entity_alias" / "company_aliases.sqlite"

ALIAS_SCHEMA_SQL = """
PRAGMA synchronous = NORMAL;

CREATE TABLE companies (
    company_id TEXT NOT NULL PRIMARY KEY,
    canonical_name TEXT,
    sources TEXT NOT NULL
);

CREATE TABLE aliases (
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    company_id TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE INDEX idx_aliases_normalized ON aliases(normalized_alias);
CREATE INDEX idx_aliases_company ON aliases(company_id);
"""

SOURCES_SPEC = {
    "research_reports": ("data/normalized/research_reports.jsonl", "research"),
    "announcements": ("data/normalized/announcements.jsonl", "announcement"),
    "financial_metrics": ("data/indexes/financial_analysis/financial_metrics.sqlite", "financial"),
    "ownership_holdings": ("data/indexes/ownership_analysis/ownership_holdings.sqlite", "ownership"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the FinTrace company alias index.")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output", type=Path, default=DEFAULT_INDEX_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_alias_index(args.data_root, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_alias_index(data_root: Path, output_path: Path) -> dict:
    started = time.perf_counter()
    companies: dict[str, dict] = {}
    aliases: dict[tuple[str, str], tuple[str, str]] = {}

    report = _load_research_reports(data_root / "normalized" / "research_reports.jsonl", companies, aliases)
    report.update(_load_announcements(data_root / "normalized" / "announcements.jsonl", companies, aliases))
    report.update(_load_company_ids_from_sqlite(data_root / "indexes" / "financial_analysis" / "financial_metrics.sqlite", "financial", companies))
    report.update(_load_company_ids_from_sqlite(data_root / "indexes" / "ownership_analysis" / "ownership_holdings.sqlite", "ownership", companies))

    if not companies:
        raise FileNotFoundError(f"No company sources found under {data_root}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(temporary_path)) as connection:
            connection.executescript(ALIAS_SCHEMA_SQL)
            connection.executemany(
                "INSERT INTO companies VALUES (?, ?, ?)",
                [
                    (company_id, item["canonical_name"], ",".join(sorted(item["sources"])))
                    for company_id, item in companies.items()
                ],
            )
            connection.executemany(
                "INSERT INTO aliases VALUES (?, ?, ?, ?)",
                [
                    (alias, normalize_alias(alias), company_id, source)
                    for (alias, company_id), source in aliases.items()
                ],
            )
            connection.commit()
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return {
        "status": "complete",
        "index_path": str(output_path),
        "companies": len(companies),
        "aliases": len(aliases),
        "sources": report,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def _load_research_reports(path: Path, companies: dict, aliases: dict) -> dict:
    if not path.is_file():
        return {"research_reports": "skipped_missing"}
    count = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            company_id = canonical_company_id(str(row.get("sec_code") or "").strip())
            name = str(row.get("sec_name") or "").strip()
            if not company_id or not name:
                continue
            _register(companies, aliases, company_id, name, "research")
            count += 1
    return {"research_reports": count}


def canonical_company_id(code: str) -> str:
    """Research sec_code comes bare; infer the exchange suffix by the standard A-share rule."""
    code = code.strip().upper()
    if not code:
        return ""
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "9")):
        return f"{code}.BJ"
    return code


def _load_announcements(path: Path, companies: dict, aliases: dict) -> dict:
    """Company short names usually prefix announcement titles before ':' or '：'."""
    if not path.is_file():
        return {"announcements": "skipped_missing"}
    count = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            company_id = str(row.get("s_info_windcode") or "").strip()
            title = str(row.get("n_info_title") or "")
            if not company_id:
                continue
            _register_company(companies, company_id, None, "announcement")
            prefix = title.split(":", 1)[0].split("：", 1)[0].strip()
            if 2 <= len(prefix) <= 12 and any("一" <= ch <= "鿿" for ch in prefix):
                _register_alias(aliases, prefix, company_id, "announcement_title")
                count += 1
    return {"announcement_title_aliases": count}


def _load_company_ids_from_sqlite(path: Path, source: str, companies: dict) -> dict:
    if not path.is_file():
        return {source: "skipped_missing"}
    with closing(sqlite3.connect(path)) as connection:
        if source == "financial":
            rows = connection.execute("SELECT DISTINCT company_id FROM financial_metrics").fetchall()
        else:
            rows = connection.execute("SELECT DISTINCT target_company_id FROM holder_records").fetchall()
    for (company_id,) in rows:
        _register_company(companies, str(company_id), None, source)
    return {source: len(rows)}


def _register(companies: dict, aliases: dict, company_id: str, name: str, source: str) -> None:
    _register_company(companies, company_id, name, source)
    _register_alias(aliases, name, company_id, source)


def _register_company(companies: dict, company_id: str, name: str | None, source: str) -> None:
    entry = companies.setdefault(company_id, {"canonical_name": None, "sources": set()})
    entry["sources"].add(source)
    if name and not entry["canonical_name"]:
        entry["canonical_name"] = name


def _register_alias(aliases: dict, alias: str, company_id: str, source: str) -> None:
    aliases.setdefault((alias, company_id), source)


def normalize_alias(value: str) -> str:
    return "".join(value.split()).upper()


if __name__ == "__main__":
    raise SystemExit(main())

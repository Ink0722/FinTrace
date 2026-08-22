from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

from data_pipeline.entity_alias.build_index import canonical_company_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NORMALIZED = PROJECT_ROOT / "data" / "normalized"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "processed" / "entity_resolution" / "company_universe.jsonl"
UNIVERSE_VERSION = "company-universe-v1"
_A_SHARE_CODE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the union of company codes observed by FinTrace.")
    parser.add_argument("--normalized-dir", type=Path, default=DEFAULT_NORMALIZED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_company_universe(args.normalized_dir, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_company_universe(normalized_dir: Path, output_path: Path) -> dict:
    started = time.perf_counter()
    companies: dict[str, dict] = {}
    source_stats: dict[str, int | str] = {}
    source_paths: list[Path] = []

    specs = [
        ("shareholders", normalized_dir / "shareholders.jsonl", "s_info_windcode", "ann_dt", None),
        ("research_reports", normalized_dir / "research_reports.jsonl", "sec_code", "publish_date", "sec_name"),
        ("announcements", normalized_dir / "announcements.jsonl", "s_info_windcode", "ann_dt", None),
        ("balance_sheets", normalized_dir / "balance_sheets.jsonl", "s_info_windcode", "actual_ann_dt", None),
        ("cashflows", normalized_dir / "cashflows.jsonl", "s_info_windcode", "actual_ann_dt", None),
        ("income_statements", normalized_dir / "income_statements.jsonl", "s_info_windcode", "actual_ann_dt", None),
    ]
    for source, path, code_field, date_field, name_field in specs:
        if not path.is_file():
            source_stats[source] = "skipped_missing"
            continue
        source_paths.append(path)
        source_stats[source] = _consume_source(
            companies,
            path,
            source=source,
            code_field=code_field,
            date_field=date_field,
            name_field=name_field,
        )

    if not companies:
        raise FileNotFoundError(f"No company-code sources found under {normalized_dir}")

    rows = []
    for company_id in sorted(companies):
        item = companies[company_id]
        code_type = classify_company_code(company_id)
        rows.append(
            {
                "company_id": company_id,
                "code_type": code_type,
                "profile_eligible": code_type == "a_share",
                "sources": sorted(item["sources"]),
                "security_names": sorted(item["names"]),
                "first_observed_date": min(item["dates"]) if item["dates"] else None,
                "last_observed_date": max(item["dates"]) if item["dates"] else None,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, output_path)

    type_counts: dict[str, int] = {}
    for row in rows:
        type_counts[row["code_type"]] = type_counts.get(row["code_type"], 0) + 1
    manifest = {
        "status": "complete",
        "universe_version": UNIVERSE_VERSION,
        "output_path": str(output_path),
        "company_count": len(rows),
        "profile_eligible_count": sum(row["profile_eligible"] for row in rows),
        "code_types": type_counts,
        "source_rows": source_stats,
        "sources": {path.name: _fingerprint(path) for path in source_paths},
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    _write_json_atomic(output_path.with_name("company_universe_manifest.json"), manifest)
    return manifest


def classify_company_code(company_id: str) -> str:
    if not _A_SHARE_CODE.fullmatch(company_id):
        return "nonstandard"
    code, exchange = company_id.split(".", 1)
    valid_prefix = (
        (exchange == "SH" and code.startswith("6"))
        or (exchange == "SZ" and code.startswith(("0", "3")))
        or (exchange == "BJ" and code.startswith(("4", "8", "9")))
    )
    return "a_share" if valid_prefix else "exchange_mismatch"


def load_company_universe(path: Path, *, eligible_only: bool = False) -> dict[str, dict]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Company universe not found: {path}. Build it with: "
            "python -m data_pipeline.entity_resolution.build_company_universe"
        )
    companies: dict[str, dict] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            company_id = canonical_company_id(str(row.get("company_id") or ""))
            if not company_id or (eligible_only and not row.get("profile_eligible")):
                continue
            companies[company_id] = row
    return companies


def _consume_source(
    companies: dict[str, dict],
    path: Path,
    *,
    source: str,
    code_field: str,
    date_field: str,
    name_field: str | None,
) -> int:
    count = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            company_id = canonical_company_id(str(row.get(code_field) or ""))
            if not company_id:
                continue
            item = companies.setdefault(company_id, {"sources": set(), "names": set(), "dates": set()})
            item["sources"].add(source)
            observed_date = str(row.get(date_field) or row.get("ann_dt") or "").strip()
            if _is_iso_date(observed_date):
                item["dates"].add(observed_date)
            if name_field:
                name = str(row.get(name_field) or "").strip()
                if name:
                    item["names"].add(name)
            count += 1
    return count


def _is_iso_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def _fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "size": path.stat().st_size, "sha256": digest.hexdigest()}


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())

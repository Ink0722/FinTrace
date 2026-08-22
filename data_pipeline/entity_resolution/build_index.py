from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from data_pipeline.entity_alias.build_index import canonical_company_id
from data_pipeline.entity_resolution.build_company_universe import (
    DEFAULT_OUTPUT as DEFAULT_UNIVERSE,
    UNIVERSE_VERSION,
    load_company_universe,
)
from data_pipeline.entity_resolution.normalize import legal_core_name, normalize_name


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "indexes" / "entity_resolution" / "entity_master.sqlite"
DEFAULT_PROFILES = PROJECT_ROOT / "data" / "source" / "company_profiles" / "akshare_company_profiles.jsonl"
SCHEMA_VERSION = "entity-master-v1"

SCHEMA_SQL = """
PRAGMA synchronous = NORMAL;

CREATE TABLE entities (
    entity_id TEXT NOT NULL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    resolution_status TEXT NOT NULL
);

CREATE TABLE entity_aliases (
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY(entity_id, alias, source)
);
CREATE INDEX idx_entity_aliases_normalized ON entity_aliases(normalized_alias);

CREATE TABLE entity_identifiers (
    entity_id TEXT NOT NULL,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY(identifier_type, identifier_value, source)
);
CREATE INDEX idx_entity_identifiers_entity ON entity_identifiers(entity_id);

CREATE TABLE entity_links (
    source_entity_id TEXT NOT NULL,
    canonical_entity_id TEXT NOT NULL,
    link_type TEXT NOT NULL,
    match_method TEXT NOT NULL,
    confidence REAL NOT NULL,
    review_status TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(source_entity_id, canonical_entity_id, link_type)
);

CREATE TABLE match_candidates (
    source_entity_id TEXT NOT NULL,
    candidate_entity_id TEXT NOT NULL,
    match_method TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    review_status TEXT NOT NULL,
    PRIMARY KEY(source_entity_id, candidate_entity_id, match_method)
);
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the FinTrace canonical entity index.")
    parser.add_argument("--normalized-dir", type=Path, default=PROJECT_ROOT / "data" / "normalized")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--company-profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--company-universe", type=Path, default=DEFAULT_UNIVERSE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_entity_index(
        args.normalized_dir, args.output, args.company_profiles, args.company_universe
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_entity_index(
    normalized_dir: Path,
    output_path: Path,
    company_profiles_path: Path | None = None,
    company_universe_path: Path = DEFAULT_UNIVERSE,
) -> dict:
    started = time.perf_counter()
    shareholder_path = normalized_dir / "shareholders.jsonl"
    for path in (shareholder_path, company_universe_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing entity-resolution source: {path}")

    listed = _load_listed_companies(company_universe_path)
    profile_count = _merge_company_profiles(listed, company_profiles_path)
    holders = _load_holders(shareholder_path)
    links, candidates = _resolve(holders, listed)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.executescript(SCHEMA_SQL)
            _write_entities(connection, listed, holders)
            connection.executemany(
                "INSERT INTO entity_links VALUES (?, ?, ?, ?, ?, ?, ?)", links
            )
            connection.executemany(
                "INSERT INTO match_candidates VALUES (?, ?, ?, ?, ?, ?)", candidates
            )
            connection.commit()
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)

    manifest = {
        "status": "complete",
        "schema_version": SCHEMA_VERSION,
        "index_path": str(output_path),
        "sources": {
            "shareholders": _fingerprint(shareholder_path),
            "company_universe": _fingerprint(company_universe_path),
        },
        "company_universe_version": UNIVERSE_VERSION,
        "listed_companies": len(listed),
        "company_profiles_loaded": profile_count,
        "holder_entities": len(holders),
        "confirmed_links": len(links),
        "review_candidates": len(candidates),
        "match_methods": _method_counts(links),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    if company_profiles_path and company_profiles_path.is_file():
        manifest["sources"]["company_profiles"] = _fingerprint(company_profiles_path)
    _write_json_atomic(output_path.with_name("manifest.json"), manifest)
    return manifest


def _load_listed_companies(path: Path) -> dict[str, dict]:
    companies: dict[str, dict] = {}
    for company_id, row in load_company_universe(path).items():
        names = {str(name).strip() for name in row.get("security_names", []) if str(name).strip()}
        companies[company_id] = {
            "name": sorted(names)[0] if names else company_id,
            "aliases": set(names),
            "research_aliases": set(names),
            "profile_aliases": set(),
            "resolution_status": "named" if names else "code_only",
        }
    return companies


def _merge_company_profiles(companies: dict[str, dict], path: Path | None) -> int:
    if path is None or not path.is_file():
        return 0
    latest_success: dict[str, dict] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            company_id = canonical_company_id(str(row.get("company_id") or ""))
            if company_id in companies and row.get("fetch_status") == "success":
                latest_success[company_id] = row
    for company_id, row in latest_success.items():
        item = companies[company_id]
        legal_name = str(row.get("legal_name") or "").strip()
        security_name = str(row.get("security_name") or "").strip()
        former_names = row.get("former_names") if isinstance(row.get("former_names"), list) else []
        source = str(row.get("source") or "akshare.company_profile").strip()
        if legal_name:
            item["name"] = legal_name
            item["resolution_status"] = "profiled"
            item["aliases"].add(legal_name)
            item["profile_aliases"].add((legal_name, "LEGAL_NAME", source))
        if security_name:
            item["aliases"].add(security_name)
            item["profile_aliases"].add((security_name, "SECURITY_NAME", source))
        for former_name in former_names:
            former_name = str(former_name).strip()
            if former_name:
                item["aliases"].add(former_name)
                item["profile_aliases"].add((former_name, "FORMER_SECURITY_NAME", source))
    return len(latest_success)


def _load_holders(path: Path) -> dict[str, dict]:
    holders: dict[str, dict] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for row_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("s_holder_holdercategory") or "") != "2":
                continue
            name = str(row.get("s_holder_name") or "").strip()
            if not name:
                continue
            compcode = str(row.get("s_info_compcode") or "").strip()
            if compcode:
                entity_id = f"COMPANY:{compcode}"
                quality = "source_identifier"
            else:
                target = str(row.get("s_info_windcode") or "").strip()
                digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12].upper()
                entity_id = f"COMPANY_UNRESOLVED:{digest}:{target}"
                quality = "source_scoped_name"
            item = holders.setdefault(
                entity_id,
                {"name": name, "aliases": set(), "compcode": compcode or None, "quality": quality},
            )
            item["aliases"].add(name)
            alternate = str(row.get("s_holder_aname") or "").strip()
            if alternate:
                item["aliases"].add(alternate)
    return holders


def _resolve(holders: dict[str, dict], listed: dict[str, dict]) -> tuple[list[tuple], list[tuple]]:
    legal_index: dict[str, set[str]] = defaultdict(set)
    exact_index: dict[str, set[str]] = defaultdict(set)
    core_index: dict[str, set[str]] = defaultdict(set)
    for company_id, item in listed.items():
        for alias, alias_type, _source in item.get("profile_aliases", set()):
            if alias_type == "LEGAL_NAME":
                legal_index[normalize_name(alias)].add(company_id)
        for alias in item["aliases"]:
            exact_index[normalize_name(alias)].add(company_id)
            core_index[legal_core_name(alias)].add(company_id)

    holder_ids_by_core: dict[str, set[str]] = defaultdict(set)
    for holder_id, item in holders.items():
        for alias in item["aliases"]:
            core = legal_core_name(alias)
            if core:
                holder_ids_by_core[core].add(holder_id)

    links: list[tuple] = []
    candidates: list[tuple] = []
    for holder_id, item in holders.items():
        legal_matches = set().union(
            *(legal_index.get(normalize_name(alias), set()) for alias in item["aliases"])
        )
        if len(legal_matches) == 1:
            company_id = next(iter(legal_matches))
            links.append(
                _link_row(holder_id, company_id, "exact_legal_name", 1.0, item, listed[company_id])
            )
            continue
        if len(legal_matches) > 1:
            for company_id in sorted(legal_matches):
                candidates.append(
                    (
                        holder_id,
                        company_id,
                        "ambiguous_legal_name",
                        0.7,
                        "Legal name maps to multiple listed companies",
                        "pending",
                    )
                )
            continue
        exact = set().union(*(exact_index.get(normalize_name(alias), set()) for alias in item["aliases"]))
        if len(exact) == 1:
            company_id = next(iter(exact))
            links.append(_link_row(holder_id, company_id, "exact_normalized_name", 1.0, item, listed[company_id]))
            continue
        if len(exact) > 1:
            for company_id in sorted(exact):
                candidates.append((holder_id, company_id, "ambiguous_exact_name", 0.6, "Exact name maps to multiple listed companies", "pending"))
            continue
        holder_cores = {legal_core_name(alias) for alias in item["aliases"] if legal_core_name(alias)}
        unique_core_matches = {
            next(iter(core_index[core]))
            for core in holder_cores
            if len(core_index.get(core, set())) == 1 and len(holder_ids_by_core[core]) == 1
        }
        if len(unique_core_matches) == 1:
            company_id = next(iter(unique_core_matches))
            links.append(
                _link_row(
                    holder_id,
                    company_id,
                    "unique_legal_core",
                    0.95,
                    item,
                    listed[company_id],
                )
            )
        else:
            core_matches = set().union(*(core_index.get(core, set()) for core in holder_cores))
            for company_id in sorted(core_matches):
                candidates.append(
                    (
                        holder_id,
                        company_id,
                        "ambiguous_legal_core",
                        0.5,
                        "Legal core maps to multiple entities; manual confirmation required",
                        "pending",
                    )
                )
    return links, candidates


def _link_row(holder_id: str, company_id: str, method: str, confidence: float, holder: dict, company: dict) -> tuple:
    evidence = {"holder_name": holder["name"], "listed_name": company["name"]}
    return (holder_id, company_id, "SAME_LEGAL_ENTITY", method, confidence, "auto_confirmed", json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))


def _write_entities(connection: sqlite3.Connection, listed: dict[str, dict], holders: dict[str, dict]) -> None:
    entity_rows, alias_rows, identifier_rows = [], [], []
    for company_id, item in listed.items():
        entity_rows.append(
            (company_id, "LISTED_COMPANY", item["name"], item["resolution_status"])
        )
        identifier_rows.append((company_id, "STOCK_CODE", company_id, "company_universe"))
        alias_rows.extend(
            (company_id, alias, normalize_name(alias), "SECURITY_NAME", "research_reports")
            for alias in item.get("research_aliases", set())
        )
        alias_rows.extend(
            (company_id, alias, normalize_name(alias), alias_type, source)
            for alias, alias_type, source in item.get("profile_aliases", set())
        )
    for holder_id, item in holders.items():
        entity_rows.append((holder_id, "COMPANY_HOLDER", item["name"], item["quality"]))
        if item["compcode"]:
            identifier_rows.append((holder_id, "SOURCE_COMPCODE", item["compcode"], "shareholders"))
        alias_rows.extend((holder_id, alias, normalize_name(alias), "DISCLOSED_NAME", "shareholders") for alias in item["aliases"])
    connection.executemany("INSERT INTO entities VALUES (?, ?, ?, ?)", entity_rows)
    connection.executemany("INSERT INTO entity_aliases VALUES (?, ?, ?, ?, ?)", alias_rows)
    connection.executemany("INSERT INTO entity_identifiers VALUES (?, ?, ?, ?)", identifier_rows)


def _method_counts(links: list[tuple]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in links:
        counts[row[3]] = counts.get(row[3], 0) + 1
    return counts


def _fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns, "sha256": digest.hexdigest()}


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())

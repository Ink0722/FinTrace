from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from data_pipeline.entity_resolution.normalize import legal_core_name, normalize_name


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENTITY_INDEX = PROJECT_ROOT / "data" / "indexes" / "entity_resolution" / "entity_master.sqlite"
DEFAULT_CANDIDATES = PROJECT_ROOT / "data" / "processed" / "entity_resolution" / "unlinked_match_candidates.jsonl"
DEFAULT_CLASSIFICATIONS = PROJECT_ROOT / "data" / "processed" / "entity_resolution" / "unlinked_holder_classification.jsonl"
AUDIT_VERSION = "unlinked-entity-audit-v1"

STOP_BIGRAMS = {
    "公司", "有限", "股份", "集团", "投资", "管理", "控股", "企业", "发展", "中国",
}
VEHICLE_MARKERS = (
    "证券投资基金",
    "资产管理计划",
    "集合资产管理",
    "企业年金计划",
    "信托计划",
    "合伙企业",
    "香港中央结算",
    "基金管理有限公司",
    "Investment Fund",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit unlinked company holders against listed-company aliases.")
    parser.add_argument("--entity-index", type=Path, default=DEFAULT_ENTITY_INDEX)
    parser.add_argument("--candidates-output", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--classifications-output", type=Path, default=DEFAULT_CLASSIFICATIONS)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.62)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = audit_unlinked_entities(
        args.entity_index,
        args.candidates_output,
        args.classifications_output,
        top_k=args.top_k,
        min_score=args.min_score,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def audit_unlinked_entities(
    entity_index: Path,
    candidates_output: Path,
    classifications_output: Path,
    *,
    top_k: int = 3,
    min_score: float = 0.62,
) -> dict:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if not 0 <= min_score <= 1:
        raise ValueError("min_score must be between 0 and 1")
    if not entity_index.is_file():
        raise FileNotFoundError(f"Entity index not found: {entity_index}")

    started = time.perf_counter()
    holders, companies = _load_entities(entity_index)
    alias_index = _build_alias_index(companies)
    candidate_rows: list[dict] = []
    classification_rows: list[dict] = []
    class_counts: Counter[str] = Counter()
    score_bands: Counter[str] = Counter()

    for holder_id in sorted(holders):
        holder = holders[holder_id]
        ranked = _rank_candidates(holder["aliases"], companies, alias_index, top_k, min_score)
        classification, reason = _classify(holder["aliases"], ranked)
        class_counts[classification] += 1
        classification_rows.append(
            {
                "holder_entity_id": holder_id,
                "holder_names": sorted(holder["aliases"]),
                "preliminary_class": classification,
                "reason": reason,
                "candidate_count": len(ranked),
                "top_candidate_company_id": ranked[0]["candidate_company_id"] if ranked else None,
                "top_score": ranked[0]["score"] if ranked else None,
                "review_status": "pending",
                "audit_version": AUDIT_VERSION,
            }
        )
        for rank, candidate in enumerate(ranked, start=1):
            candidate_rows.append(
                {
                    "holder_entity_id": holder_id,
                    "holder_names": sorted(holder["aliases"]),
                    "candidate_company_id": candidate["candidate_company_id"],
                    "candidate_name": candidate["candidate_name"],
                    "matched_holder_name": candidate["matched_holder_name"],
                    "matched_company_alias": candidate["matched_company_alias"],
                    "score": candidate["score"],
                    "sequence_score": candidate["sequence_score"],
                    "bigram_score": candidate["bigram_score"],
                    "core_containment": candidate["core_containment"],
                    "normalized_exact": candidate["normalized_exact"],
                    "legal_core_equal": candidate["legal_core_equal"],
                    "rank": rank,
                    "preliminary_class": classification,
                    "review_status": "pending",
                    "audit_version": AUDIT_VERSION,
                }
            )
            if rank == 1:
                score_bands[_score_band(candidate["score"])] += 1

    _write_jsonl_atomic(candidates_output, candidate_rows)
    _write_jsonl_atomic(classifications_output, classification_rows)
    report = {
        "status": "complete",
        "audit_version": AUDIT_VERSION,
        "entity_index": str(entity_index),
        "unlinked_company_holders": len(holders),
        "listed_companies": len(companies),
        "candidate_rows": len(candidate_rows),
        "holders_with_candidates": sum(bool(row["candidate_count"]) for row in classification_rows),
        "classification_counts": dict(sorted(class_counts.items())),
        "top_score_bands": dict(sorted(score_bands.items())),
        "parameters": {"top_k": top_k, "min_score": min_score},
        "candidates_output": str(candidates_output),
        "classifications_output": str(classifications_output),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    _write_json_atomic(candidates_output.with_name("unlinked_audit_report.json"), report)
    return report


def _load_entities(entity_index: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    with sqlite3.connect(entity_index) as connection:
        linked = {row[0] for row in connection.execute("SELECT DISTINCT source_entity_id FROM entity_links")}
        holders = {
            row[0]: {"name": row[1], "aliases": set()}
            for row in connection.execute(
                "SELECT entity_id, canonical_name FROM entities WHERE entity_type='COMPANY_HOLDER'"
            )
            if row[0] not in linked
        }
        companies = {
            row[0]: {"name": row[1], "aliases": set()}
            for row in connection.execute(
                "SELECT entity_id, canonical_name FROM entities WHERE entity_type='LISTED_COMPANY'"
            )
        }
        for entity_id, alias in connection.execute("SELECT entity_id, alias FROM entity_aliases"):
            if entity_id in holders:
                holders[entity_id]["aliases"].add(str(alias))
            elif entity_id in companies:
                companies[entity_id]["aliases"].add(str(alias))
    for entity_id, item in holders.items():
        item["aliases"].add(item["name"])
    for entity_id, item in companies.items():
        item["aliases"].add(item["name"])
    return holders, companies


def _build_alias_index(companies: dict[str, dict]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for company_id, item in companies.items():
        for alias in item["aliases"]:
            for gram in _bigrams(normalize_name(alias)):
                if gram not in STOP_BIGRAMS:
                    index[gram].add(company_id)
    return index


def _rank_candidates(
    holder_aliases: set[str],
    companies: dict[str, dict],
    alias_index: dict[str, set[str]],
    top_k: int,
    min_score: float,
) -> list[dict]:
    overlap: Counter[str] = Counter()
    for alias in holder_aliases:
        for gram in _bigrams(normalize_name(alias)):
            if gram in STOP_BIGRAMS:
                continue
            overlap.update(alias_index.get(gram, ()))
    candidate_ids = [company_id for company_id, _ in overlap.most_common(80)]
    ranked = []
    for company_id in candidate_ids:
        best = _best_alias_pair(holder_aliases, companies[company_id]["aliases"])
        if best["score"] < min_score:
            continue
        ranked.append(
            {
                "candidate_company_id": company_id,
                "candidate_name": companies[company_id]["name"],
                **best,
            }
        )
    ranked.sort(key=lambda row: (-row["score"], row["candidate_company_id"]))
    return ranked[:top_k]


def _best_alias_pair(holder_aliases: set[str], company_aliases: set[str]) -> dict:
    best: dict | None = None
    for holder_name in holder_aliases:
        left = normalize_name(holder_name)
        if not left:
            continue
        for company_alias in company_aliases:
            right = normalize_name(company_alias)
            if not right:
                continue
            sequence = SequenceMatcher(None, left, right, autojunk=False).ratio()
            bigram = _dice(_bigrams(left), _bigrams(right))
            left_core, right_core = legal_core_name(left), legal_core_name(right)
            containment = bool(
                min(len(left_core), len(right_core)) >= 4
                and (left_core in right_core or right_core in left_core)
            )
            score = 0.55 * sequence + 0.45 * bigram
            normalized_exact = left == right
            legal_core_equal = bool(left_core and left_core == right_core)
            if normalized_exact:
                score = 1.0
            elif containment:
                score = max(score, 0.88 + 0.08 * min(len(left_core), len(right_core)) / max(len(left_core), len(right_core)))
            row = {
                "matched_holder_name": holder_name,
                "matched_company_alias": company_alias,
                "score": round(min(score, 1.0), 6),
                "sequence_score": round(sequence, 6),
                "bigram_score": round(bigram, 6),
                "core_containment": containment,
                "normalized_exact": normalized_exact,
                "legal_core_equal": legal_core_equal,
            }
            if best is None or row["score"] > best["score"]:
                best = row
    return best or {
        "matched_holder_name": "",
        "matched_company_alias": "",
        "score": 0.0,
        "sequence_score": 0.0,
        "bigram_score": 0.0,
        "core_containment": False,
        "normalized_exact": False,
        "legal_core_equal": False,
    }


def _classify(holder_aliases: set[str], ranked: list[dict]) -> tuple[str, str]:
    joined = "|".join(holder_aliases)
    if any(marker in joined for marker in VEHICLE_MARKERS):
        return "institution_or_vehicle", "Holder name identifies a fund, plan, partnership or settlement vehicle."
    if not ranked:
        return "insufficient_candidate", "No listed-company alias reached the exploratory score threshold."
    top = ranked[0]
    margin = top["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0)
    if top["normalized_exact"] and margin >= 0.03:
        return "likely_same_entity", "High name similarity with a separated top candidate; human verification required."
    if top["legal_core_equal"]:
        return "legal_core_collision", "Legal cores match but legal forms differ; this may be a parent, subsidiary or renamed entity."
    if top["score"] >= 0.78:
        return "likely_related_entity", "Names share a substantial core but may denote parent, subsidiary or affiliate."
    return "uncertain", "A weak candidate exists but does not support an identity or relationship conclusion."


def _bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _dice(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2 * len(left.intersection(right)) / (len(left) + len(right))


def _score_band(score: float) -> str:
    lower = math.floor(score * 20) / 20
    return f"{lower:.2f}-{min(lower + 0.05, 1.0):.2f}"


def _write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())

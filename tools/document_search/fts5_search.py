from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from schemas.document import DocumentSearchHit
from tools.document_search.kb_loader import load_kb_chunks_by_ids
from tools.document_search.search import BM25_TOKENIZER_VERSION, tokenize


@dataclass(frozen=True)
class LexicalSearchOutcome:
    hits: list[DocumentSearchHit]
    search_time_ms: int = 0
    candidate_count: int = 0
    strategy: str = "fts5_global"


def bm25_index_available(index_path: Path) -> bool:
    return index_path.is_file()


def validate_bm25_index_snapshot(index_path: Path, kb_path: Path) -> list[str]:
    """Validate a copied KB/BM25 pair without relying on file timestamps."""
    manifest_path = index_path.with_name("bm25_manifest.json")
    if not manifest_path.is_file():
        return [f"BM25 index manifest not found: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"BM25 index manifest is invalid: {type(exc).__name__}: {exc}"]
    errors: list[str] = []
    if manifest.get("tokenizer_version") != BM25_TOKENIZER_VERSION:
        errors.append(
            f"tokenizer version mismatch: index={manifest.get('tokenizer_version')}, "
            f"expected={BM25_TOKENIZER_VERSION}"
        )
    recorded = manifest.get("kb")
    if not isinstance(recorded, dict):
        return [*errors, "BM25 index manifest has no kb object."]
    if not kb_path.is_file():
        errors.append(f"knowledge base not found: {kb_path}")
        return errors
    stat = kb_path.stat()
    if int(recorded.get("size", -1)) != stat.st_size:
        errors.append(f"knowledge base size changed: {kb_path}")
    expected_chunks = manifest.get("chunk_count")
    if expected_chunks is not None:
        try:
            with sqlite3.connect(kb_path) as connection:
                actual_chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if int(expected_chunks) != int(actual_chunks):
                errors.append(
                    f"knowledge base chunk count changed: expected={expected_chunks}, "
                    f"actual={actual_chunks}"
                )
        except sqlite3.Error as exc:
            errors.append(f"knowledge base metadata check failed: {type(exc).__name__}: {exc}")
    return errors


def build_match_expression(query: str) -> str | None:
    """FTS5 OR expression over the same bigram tokens as the offline build."""
    terms = tokenize(query)
    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms)


def fts5_search(
    *,
    query: str,
    index_path: Path,
    kb_path: Path,
    top_k: int,
    company_id: str | None = None,
    document_types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> LexicalSearchOutcome:
    started = time.perf_counter()
    expression = build_match_expression(query)
    if expression is None:
        return LexicalSearchOutcome(hits=[], search_time_ms=_elapsed_ms(started), candidate_count=0)

    filter_clauses, params = _filter_clauses(
        company_id, document_types, start_date, end_date, prefix="m."
    )
    filter_sql = f"AND {' AND '.join(filter_clauses)}" if filter_clauses else ""
    strategy = "fts5_filtered" if filter_clauses else "fts5_global"

    with sqlite3.connect(index_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT m.chunk_id AS chunk_id, bm25(bm25_chunks) AS rank_score
            FROM bm25_chunks JOIN chunk_meta m ON m.rowid_ref = bm25_chunks.rowid
            WHERE bm25_chunks MATCH ? {filter_sql}
            ORDER BY rank_score
            LIMIT ?
            """,
            [expression, *params, top_k],
        ).fetchall()
        candidate_count = _candidate_count(connection, company_id, document_types, start_date, end_date)

    if not rows:
        return LexicalSearchOutcome(
            hits=[], search_time_ms=_elapsed_ms(started), candidate_count=candidate_count, strategy=strategy
        )

    chunk_ids = [row["chunk_id"] for row in rows]
    chunk_map = load_kb_chunks_by_ids(chunk_ids, kb_path=kb_path)
    # bm25() is negative with "smaller is better"; flip and normalize like the old scorer.
    raw_scores = [-float(row["rank_score"]) for row in rows]
    max_raw = max(raw_scores)
    hits: list[DocumentSearchHit] = []
    for chunk_id, raw in zip(chunk_ids, raw_scores):
        chunk = chunk_map.get(chunk_id)
        if chunk is None:
            continue
        normalized = round(float(raw / max_raw), 6) if max_raw else 0.0
        hits.append(
            DocumentSearchHit(
                chunk=chunk,
                score=normalized,
                evidence_id=f"EVID-{chunk.chunk_id}",
                retrieval={
                    "source": "bm25",
                    "matched_by": ["bm25"],
                    "bm25_score": normalized,
                    "vector_score": None,
                    "final_score": normalized,
                    "lexical_strategy": strategy,
                },
            )
        )
    return LexicalSearchOutcome(
        hits=hits,
        search_time_ms=_elapsed_ms(started),
        candidate_count=candidate_count,
        strategy=strategy,
    )


def _filter_clauses(
    company_id: str | None,
    document_types: list[str] | None,
    start_date: date | None,
    end_date: date | None,
    *,
    prefix: str,
) -> tuple[list[str], list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if company_id:
        clauses.append(f"{prefix}company_id = ?")
        params.append(company_id)
    if document_types:
        placeholders = ",".join("?" for _ in document_types)
        clauses.append(f"{prefix}document_type IN ({placeholders})")
        params.extend(document_types)
    if start_date:
        clauses.append(f"{prefix}published_date >= ?")
        params.append(start_date.isoformat())
    if end_date:
        clauses.append(f"{prefix}published_date <= ?")
        params.append(end_date.isoformat())
    return clauses, params


def _candidate_count(
    connection: sqlite3.Connection,
    company_id: str | None,
    document_types: list[str] | None,
    start_date: date | None,
    end_date: date | None,
) -> int:
    clauses, params = _filter_clauses(company_id, document_types, start_date, end_date, prefix="")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    row = connection.execute(f"SELECT COUNT(*) FROM chunk_meta {where_sql}", params).fetchone()
    return int(row[0]) if row else 0


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))

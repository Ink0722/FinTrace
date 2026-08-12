import json
from datetime import date
from pathlib import Path

import faiss
import numpy as np

from knowledge_base.embeddings.client import build_embedding_client
from schemas.document import DocumentChunk, DocumentSearchHit
from tools.document_search.kb_loader import load_kb_chunks_by_ids


def vector_index_available(kb_dir: Path) -> bool:
    return (kb_dir / "vector.faiss").exists() and (kb_dir / "vector_ids.json").exists()


def vector_search(
    *,
    query: str,
    kb_dir: Path,
    kb_path: Path,
    top_k: int,
    company_id: str | None = None,
    document_types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    pool_k: int | None = None,
) -> list[DocumentSearchHit]:
    if not vector_index_available(kb_dir):
        return []
    index = faiss.read_index(str(kb_dir / "vector.faiss"))
    vector_ids = json.loads((kb_dir / "vector_ids.json").read_text(encoding="utf-8"))
    query_vector = build_embedding_client().embed_query(query).reshape(1, -1)
    query_vector = np.ascontiguousarray(query_vector, dtype="float32")
    search_k = min(pool_k or max(top_k * 5, 50), len(vector_ids))
    scores, row_ids = index.search(query_vector, search_k)
    candidates: list[tuple[str, float]] = []
    for row_id, score in zip(row_ids[0], scores[0]):
        if row_id < 0 or row_id >= len(vector_ids):
            continue
        candidates.append((vector_ids[int(row_id)], float(score)))
    chunk_map = load_kb_chunks_by_ids([chunk_id for chunk_id, _ in candidates], kb_path=kb_path)
    hits: list[DocumentSearchHit] = []
    for chunk_id, score in candidates:
        chunk = chunk_map.get(chunk_id)
        if not chunk or not chunk_matches(chunk, company_id, document_types, start_date, end_date):
            continue
        hits.append(
            DocumentSearchHit(
                chunk=chunk,
                score=round(max(0.0, min(1.0, score)), 6),
                evidence_id=f"EVID-{chunk.chunk_id}",
                retrieval={
                    "source": "vector",
                    "matched_by": ["vector"],
                    "bm25_score": None,
                    "vector_score": round(max(0.0, min(1.0, score)), 6),
                    "final_score": round(max(0.0, min(1.0, score)), 6),
                },
            )
        )
        if len(hits) >= top_k:
            break
    return hits


def chunk_matches(
    chunk: DocumentChunk,
    company_id: str | None,
    document_types: list[str] | None,
    start_date: date | None,
    end_date: date | None,
) -> bool:
    if company_id and chunk.company_id != company_id:
        return False
    if document_types and chunk.document_type not in set(document_types):
        return False
    if start_date and chunk.publish_date < start_date:
        return False
    if end_date and chunk.publish_date > end_date:
        return False
    return True


def merge_hybrid_hits(bm25_hits: list[DocumentSearchHit], vector_hits: list[DocumentSearchHit], top_k: int) -> list[DocumentSearchHit]:
    merged: dict[str, dict] = {}
    for hit in bm25_hits:
        entry = merged.setdefault(hit.chunk.chunk_id, {"hit": hit, "bm25_score": None, "vector_score": None})
        entry["bm25_score"] = max(entry["bm25_score"] or 0.0, hit.score)
    for hit in vector_hits:
        entry = merged.setdefault(hit.chunk.chunk_id, {"hit": hit, "bm25_score": None, "vector_score": None})
        entry["vector_score"] = max(entry["vector_score"] or 0.0, hit.score)

    ranked = sorted(merged.values(), key=hybrid_score, reverse=True)[:top_k]
    hits: list[DocumentSearchHit] = []
    for entry in ranked:
        bm25_score = entry["bm25_score"]
        vector_score = entry["vector_score"]
        final_score = round(hybrid_score(entry), 6)
        matched_by = []
        if bm25_score is not None:
            matched_by.append("bm25")
        if vector_score is not None:
            matched_by.append("vector")
        hits.append(
            entry["hit"].model_copy(
                update={
                    "score": final_score,
                    "retrieval": {
                        "source": "hybrid",
                        "matched_by": matched_by,
                        "bm25_score": bm25_score,
                        "vector_score": vector_score,
                        "final_score": final_score,
                    },
                }
            )
        )
    return hits


def hybrid_score(entry: dict) -> float:
    bm25_score = entry["bm25_score"] or 0.0
    vector_score = entry["vector_score"] or 0.0
    return bm25_score * 0.55 + vector_score * 0.45

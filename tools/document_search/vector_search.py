from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import faiss
import numpy as np

from data_pipeline.documents.embedding_client import build_embedding_client
from schemas.document import DocumentSearchHit
from tools.document_search.kb_loader import load_kb_chunks_by_ids


@dataclass(frozen=True)
class VectorSearchOutcome:
    hits: list[DocumentSearchHit]
    embedding_time_ms: int = 0
    search_time_ms: int = 0
    strategy: str = "none"
    candidate_count: int = 0


@dataclass(frozen=True)
class VectorData:
    vector_ids: tuple[str, ...]
    id_to_row: dict[str, int]
    embeddings: np.ndarray


_VECTOR_DATA_CACHE: dict[tuple[str, int, str, int], VectorData] = {}
_VECTOR_DATA_LOCK = threading.Lock()


def vector_index_available(kb_dir: Path) -> bool:
    required = ("vector.faiss", "vector_ids.json", "embeddings.npy")
    return all((kb_dir / name).is_file() for name in required)


def vector_coverage_warning(kb_dir: Path) -> str | None:
    manifest_path = kb_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not manifest.get("partial_index"):
        return None
    chunk_count = int(manifest.get("chunk_count") or 0)
    vector_count = int(manifest.get("vector_count") or 0)
    excluded = int(manifest.get("excluded_vector_count") or max(0, chunk_count - vector_count))
    coverage = float(manifest.get("vector_coverage") or 0.0) * 100
    return (
        f"Vector index coverage is {coverage:.4f}% ({vector_count}/{chunk_count}); "
        f"{excluded} chunks have recorded embedding failures and remain available to BM25."
    )


def vector_search(
    *,
    query: str,
    kb_dir: Path,
    kb_path: Path,
    top_k: int,
    allowed_chunk_ids: set[str] | None = None,
    exact_batch_size: int = 4096,
) -> VectorSearchOutcome:
    if not vector_index_available(kb_dir):
        return VectorSearchOutcome(hits=[])

    vector_data = load_vector_data(kb_dir)
    client = build_embedding_client()
    validate_embedding_config(kb_dir, client)

    if allowed_chunk_ids is not None:
        candidate_rows = np.fromiter(
            (
                vector_data.id_to_row[chunk_id]
                for chunk_id in allowed_chunk_ids
                if chunk_id in vector_data.id_to_row
            ),
            dtype=np.int64,
        )
        if len(candidate_rows) == 0:
            return VectorSearchOutcome(hits=[], strategy="filtered_exact", candidate_count=0)
    else:
        candidate_rows = None

    embedding_started = time.perf_counter()
    query_vector = client.embed_query(query).reshape(-1)
    embedding_time_ms = _elapsed_ms(embedding_started)

    search_started = time.perf_counter()
    if candidate_rows is not None:
        ranked = _search_filtered_exact(
            vector_data.embeddings,
            candidate_rows,
            query_vector,
            top_k=top_k,
            batch_size=exact_batch_size,
        )
        strategy = "filtered_exact"
        candidate_count = len(candidate_rows)
    else:
        index = load_faiss_index(kb_dir)
        query_matrix = np.ascontiguousarray(query_vector.reshape(1, -1), dtype="float32")
        scores, row_ids = index.search(query_matrix, min(top_k, len(vector_data.vector_ids)))
        ranked = [
            (int(row_id), float(score))
            for row_id, score in zip(row_ids[0], scores[0])
            if 0 <= row_id < len(vector_data.vector_ids)
        ]
        strategy = "faiss_global"
        candidate_count = len(vector_data.vector_ids)
    search_time_ms = _elapsed_ms(search_started)

    candidates = [(vector_data.vector_ids[row_id], score) for row_id, score in ranked]
    chunk_map = load_kb_chunks_by_ids([item[0] for item in candidates], kb_path=kb_path)
    hits: list[DocumentSearchHit] = []
    for chunk_id, score in candidates:
        chunk = chunk_map.get(chunk_id)
        if chunk is None:
            continue
        rounded_score = round(score, 6)
        hits.append(
            DocumentSearchHit(
                chunk=chunk,
                score=rounded_score,
                evidence_id=f"EVID-{chunk.chunk_id}",
                retrieval={
                    "source": "vector",
                    "matched_by": ["vector"],
                    "bm25_score": None,
                    "vector_score": rounded_score,
                    "final_score": rounded_score,
                    "vector_strategy": strategy,
                },
            )
        )
    return VectorSearchOutcome(
        hits=hits,
        embedding_time_ms=embedding_time_ms,
        search_time_ms=search_time_ms,
        strategy=strategy,
        candidate_count=candidate_count,
    )


def load_vector_data(kb_dir: Path) -> VectorData:
    ids_path = kb_dir / "vector_ids.json"
    embeddings_path = kb_dir / "embeddings.npy"
    key = (
        str(ids_path.resolve()),
        ids_path.stat().st_mtime_ns,
        str(embeddings_path.resolve()),
        embeddings_path.stat().st_mtime_ns,
    )
    with _VECTOR_DATA_LOCK:
        cached = _VECTOR_DATA_CACHE.get(key)
        if cached is not None:
            return cached
        _close_vector_data_cache()
        vector_ids = tuple(json.loads(ids_path.read_text(encoding="utf-8")))
        embeddings = np.load(embeddings_path, mmap_mode="r")
        if embeddings.ndim != 2 or embeddings.shape[0] != len(vector_ids):
            _close_memmap(embeddings)
            raise RuntimeError(
                f"Embedding count mismatch: embeddings={embeddings.shape}, ids={len(vector_ids)}."
            )
        data = VectorData(
            vector_ids=vector_ids,
            id_to_row={chunk_id: row for row, chunk_id in enumerate(vector_ids)},
            embeddings=embeddings,
        )
        _VECTOR_DATA_CACHE[key] = data
        return data


def load_faiss_index(kb_dir: Path):
    path = kb_dir / "vector.faiss"
    index = _load_faiss_index_cached(str(path.resolve()), path.stat().st_mtime_ns)
    vector_count = len(load_vector_data(kb_dir).vector_ids)
    if index.ntotal != vector_count:
        raise RuntimeError(f"Vector index count mismatch: faiss={index.ntotal}, ids={vector_count}.")
    return index


@lru_cache(maxsize=2)
def _load_faiss_index_cached(path: str, mtime_ns: int):
    del mtime_ns
    return faiss.read_index(path)


def clear_vector_cache() -> None:
    with _VECTOR_DATA_LOCK:
        _close_vector_data_cache()
        _load_faiss_index_cached.cache_clear()


def _close_vector_data_cache() -> None:
    for data in _VECTOR_DATA_CACHE.values():
        _close_memmap(data.embeddings)
    _VECTOR_DATA_CACHE.clear()


def _close_memmap(value: np.ndarray) -> None:
    mmap_handle = getattr(value, "_mmap", None)
    if mmap_handle is not None:
        mmap_handle.close()


def _search_filtered_exact(
    embeddings: np.ndarray,
    candidate_rows: np.ndarray,
    query_vector: np.ndarray,
    *,
    top_k: int,
    batch_size: int,
) -> list[tuple[int, float]]:
    scores = np.empty(len(candidate_rows), dtype="float32")
    for start in range(0, len(candidate_rows), batch_size):
        rows = candidate_rows[start : start + batch_size]
        matrix = np.asarray(embeddings[rows], dtype="float32")
        scores[start : start + len(rows)] = matrix @ query_vector
    count = min(top_k, len(candidate_rows))
    if count == len(candidate_rows):
        positions = np.argsort(scores)[::-1]
    else:
        positions = np.argpartition(scores, -count)[-count:]
        positions = positions[np.argsort(scores[positions])[::-1]]
    return [(int(candidate_rows[pos]), float(scores[pos])) for pos in positions]


def validate_embedding_config(kb_dir: Path, client) -> None:
    manifest_path = kb_dir / "manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    embedding = manifest.get("embedding")
    if not isinstance(embedding, dict):
        return
    expected_model = embedding.get("model")
    expected_dimension = embedding.get("dimension")
    expected_api_mode = embedding.get("api_mode")
    if expected_model and expected_model != client.model:
        raise RuntimeError(
            f"Query embedding model {client.model!r} does not match index model {expected_model!r}."
        )
    if expected_dimension and int(expected_dimension) != int(client.dimension):
        raise RuntimeError(
            f"Query embedding dimension {client.dimension} does not match index dimension {expected_dimension}."
        )
    client_api_mode = getattr(client, "api_mode", None)
    if expected_api_mode and client_api_mode and expected_api_mode != client_api_mode:
        raise RuntimeError(
            f"Query embedding API mode {client_api_mode!r} does not match index mode {expected_api_mode!r}."
        )


def merge_hybrid_hits(
    bm25_hits: list[DocumentSearchHit],
    vector_hits: list[DocumentSearchHit],
    top_k: int,
    *,
    rrf_k: int = 60,
    max_chunks_per_document: int = 3,
) -> list[DocumentSearchHit]:
    merged: dict[str, dict] = {}
    for rank, hit in enumerate(bm25_hits, start=1):
        entry = merged.setdefault(
            hit.chunk.chunk_id,
            {"hit": hit, "bm25_score": None, "vector_score": None, "rrf_score": 0.0},
        )
        entry["bm25_score"] = hit.score
        entry["rrf_score"] += 1.0 / (rrf_k + rank)
    for rank, hit in enumerate(vector_hits, start=1):
        entry = merged.setdefault(
            hit.chunk.chunk_id,
            {"hit": hit, "bm25_score": None, "vector_score": None, "rrf_score": 0.0},
        )
        entry["vector_score"] = hit.score
        entry["rrf_score"] += 1.0 / (rrf_k + rank)

    max_rrf = 2.0 / (rrf_k + 1)
    ranked = sorted(merged.values(), key=lambda item: item["rrf_score"], reverse=True)
    hits: list[DocumentSearchHit] = []
    per_document: dict[str, int] = {}
    for entry in ranked:
        hit = entry["hit"]
        document_id = hit.chunk.document_id
        if per_document.get(document_id, 0) >= max_chunks_per_document:
            continue
        per_document[document_id] = per_document.get(document_id, 0) + 1
        final_score = round(entry["rrf_score"] / max_rrf, 6)
        matched_by = []
        if entry["bm25_score"] is not None:
            matched_by.append("bm25")
        if entry["vector_score"] is not None:
            matched_by.append("vector")
        hits.append(
            hit.model_copy(
                update={
                    "score": final_score,
                    "retrieval": {
                        "source": "hybrid",
                        "matched_by": matched_by,
                        "bm25_score": entry["bm25_score"],
                        "vector_score": entry["vector_score"],
                        "final_score": final_score,
                        "fusion": "rrf",
                    },
                }
            )
        )
        if len(hits) >= top_k:
            break
    return hits


def limit_chunks_per_document(
    hits: list[DocumentSearchHit], top_k: int, max_chunks_per_document: int
) -> list[DocumentSearchHit]:
    limited: list[DocumentSearchHit] = []
    per_document: dict[str, int] = {}
    for hit in hits:
        document_id = hit.chunk.document_id
        if per_document.get(document_id, 0) >= max_chunks_per_document:
            continue
        per_document[document_id] = per_document.get(document_id, 0) + 1
        limited.append(hit)
        if len(limited) >= top_k:
            break
    return limited


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))

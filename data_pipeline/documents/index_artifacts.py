from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np


def normalize_embedding_file(path: Path, *, rows_per_batch: int = 10_000) -> None:
    embeddings = np.lib.format.open_memmap(path, mode="r+")
    if embeddings.ndim != 2:
        raise RuntimeError(f"Embedding matrix must be two-dimensional, got {embeddings.shape}.")
    for start in range(0, embeddings.shape[0], rows_per_batch):
        batch = np.asarray(embeddings[start : start + rows_per_batch], dtype="float32")
        if not np.isfinite(batch).all():
            raise RuntimeError(f"Embedding matrix contains non-finite values near row {start}.")
        norms = np.linalg.norm(batch, axis=1, keepdims=True)
        if np.any(norms == 0):
            row = start + int(np.flatnonzero(norms.ravel() == 0)[0])
            raise RuntimeError(f"Embedding row {row} has zero norm.")
        embeddings[start : start + len(batch)] = batch / norms
    embeddings.flush()
    close_memmap(embeddings)


def build_faiss_index(
    embeddings_path: Path,
    output_path: Path,
    dimension: int,
    *,
    rows_per_batch: int = 10_000,
) -> None:
    embeddings = np.load(embeddings_path, mmap_mode="r")
    if embeddings.ndim != 2 or embeddings.shape[1] != dimension:
        raise RuntimeError(
            f"Embedding shape mismatch: expected (*, {dimension}), got {embeddings.shape}."
        )
    index = faiss.IndexFlatIP(dimension)
    for start in range(0, embeddings.shape[0], rows_per_batch):
        batch = np.ascontiguousarray(
            embeddings[start : start + rows_per_batch], dtype="float32"
        )
        index.add(batch)
    if index.ntotal != embeddings.shape[0]:
        raise RuntimeError(
            f"FAISS row count mismatch: expected {embeddings.shape[0]}, got {index.ntotal}."
        )
    faiss.write_index(index, str(output_path))
    close_memmap(embeddings)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def close_memmap(value: np.ndarray) -> None:
    mmap_handle = getattr(value, "_mmap", None)
    if mmap_handle is not None:
        mmap_handle.close()

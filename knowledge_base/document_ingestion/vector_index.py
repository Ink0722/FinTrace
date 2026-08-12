import json
import sqlite3
from pathlib import Path

import faiss
import numpy as np

from knowledge_base.embeddings.client import EmbeddingClient


def load_chunk_texts(db_path: Path) -> tuple[list[str], list[str]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT chunk_id, title, section_title, text
            FROM chunks
            ORDER BY doc_id ASC, chunk_index ASC
            """
        ).fetchall()
    chunk_ids = [row[0] for row in rows]
    texts = [f"{row[1]} {row[2] or ''}\n{row[3]}" for row in rows]
    return chunk_ids, texts


def build_vector_index(db_path: Path, kb_dir: Path, client: EmbeddingClient) -> dict:
    chunk_ids, texts = load_chunk_texts(db_path)
    if not texts:
        return {"enabled": False, "reason": "no_chunks"}
    embeddings = client.embed_documents(texts)
    if embeddings.ndim != 2:
        raise RuntimeError("Embedding client must return a 2D matrix.")
    dim = int(embeddings.shape[1])
    index = faiss.IndexFlatIP(dim)
    index.add(np.ascontiguousarray(embeddings, dtype="float32"))
    kb_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(kb_dir / "vector.faiss"))
    (kb_dir / "vector_ids.json").write_text(json.dumps(chunk_ids, ensure_ascii=False, indent=2), encoding="utf-8")
    np.save(kb_dir / "embeddings.npy", embeddings)
    return {
        "enabled": True,
        "provider": type(client).__name__,
        "model": getattr(client, "model", None),
        "dimension": dim,
        "chunk_count": len(chunk_ids),
        "vector_index_path": str(kb_dir / "vector.faiss"),
        "vector_ids_path": str(kb_dir / "vector_ids.json"),
    }

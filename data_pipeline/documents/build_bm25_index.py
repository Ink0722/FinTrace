from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from tools.document_search.config import DocumentSearchConfig
from tools.document_search.search import BM25_TOKENIZER_VERSION, tokenize


SCHEMA_SQL = """
PRAGMA synchronous = NORMAL;

CREATE VIRTUAL TABLE bm25_chunks USING fts5(text, content='');

CREATE TABLE chunk_meta (
    rowid_ref INTEGER PRIMARY KEY,
    chunk_id TEXT NOT NULL UNIQUE,
    doc_id TEXT NOT NULL,
    company_id TEXT,
    document_type TEXT,
    published_date TEXT
);

CREATE INDEX idx_chunk_meta_company ON chunk_meta(company_id);
CREATE INDEX idx_chunk_meta_type ON chunk_meta(document_type);
CREATE INDEX idx_chunk_meta_date ON chunk_meta(published_date);
"""

BATCH_SIZE = 2000


def build_parser() -> argparse.ArgumentParser:
    config = DocumentSearchConfig.from_env()
    parser = argparse.ArgumentParser(description="Build the FinTrace BM25 FTS5 lexical index.")
    parser.add_argument("--kb-path", type=Path, default=config.kb_path)
    parser.add_argument("--output", type=Path, default=config.bm25_index_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_bm25_index(args.kb_path, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_bm25_index(kb_path: Path, output_path: Path) -> dict:
    started = time.perf_counter()
    if not kb_path.is_file():
        raise FileNotFoundError(f"Document knowledge base not found: {kb_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)

    chunk_count = 0
    integrity_check = "skipped"
    try:
        with closing(sqlite3.connect(kb_path)) as source:
            source.row_factory = sqlite3.Row
            cursor = source.execute(
                """
                SELECT chunk_id, doc_id, company_id, document_type, published_date,
                       title, section_title, text
                FROM chunks
                ORDER BY chunk_id
                """
            )
            with closing(sqlite3.connect(temporary_path)) as target:
                target.executescript(SCHEMA_SQL)
                batch_lexical: list[tuple] = []
                batch_meta: list[tuple] = []
                rowid = 0
                while True:
                    rows = cursor.fetchmany(BATCH_SIZE)
                    if not rows:
                        break
                    for row in rows:
                        rowid += 1
                        chunk_count += 1
                        lexical_text = " ".join(
                            tokenize(f"{row['title'] or ''} {row['section_title'] or ''} {row['text'] or ''}")
                        )
                        batch_lexical.append((rowid, lexical_text))
                        batch_meta.append(
                            (
                                rowid,
                                row["chunk_id"],
                                row["doc_id"],
                                row["company_id"],
                                row["document_type"],
                                row["published_date"],
                            )
                        )
                    _insert_batches(target, batch_lexical, batch_meta)
                    batch_lexical.clear()
                    batch_meta.clear()
                target.commit()
                indexed_rows = target.execute("SELECT COUNT(*) FROM chunk_meta").fetchone()[0]
                if indexed_rows != chunk_count:
                    raise RuntimeError(
                        f"BM25 index row mismatch: meta={indexed_rows}, expected={chunk_count}."
                    )
                try:
                    target.execute("INSERT INTO bm25_chunks(bm25_chunks) VALUES('integrity-check')")
                    target.commit()
                    integrity_check = "passed"
                except sqlite3.OperationalError:
                    integrity_check = "skipped_contentless_unsupported"
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    manifest = {
        "status": "complete",
        "tokenizer_version": BM25_TOKENIZER_VERSION,
        "bm25_index_path": str(output_path),
        "kb": {
            "path": str(kb_path),
            "size": kb_path.stat().st_size,
            "mtime_ns": kb_path.stat().st_mtime_ns,
            "sha256": _sha256(kb_path),
        },
        "chunk_count": chunk_count,
        "index_size_bytes": output_path.stat().st_size,
        "integrity_check": integrity_check,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    manifest_path = output_path.with_name("bm25_manifest.json")
    _write_json_atomic(manifest_path, manifest)
    return manifest


def _insert_batches(target: sqlite3.Connection, batch_lexical: list[tuple], batch_meta: list[tuple]) -> None:
    target.executemany("INSERT INTO bm25_chunks(rowid, text) VALUES (?, ?)", batch_lexical)
    target.executemany(
        "INSERT INTO chunk_meta VALUES (?, ?, ?, ?, ?, ?)",
        batch_meta,
    )


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

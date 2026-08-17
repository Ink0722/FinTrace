from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from data_pipeline.documents.embedding_text import DocumentMetadata, EmbeddingRecord


CORPUS_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE documents (
    doc_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    title TEXT NOT NULL,
    published_date TEXT NOT NULL,
    publisher TEXT,
    tags_json TEXT NOT NULL,
    source_file TEXT
);

CREATE TABLE chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    title TEXT NOT NULL,
    published_date TEXT NOT NULL,
    publisher TEXT,
    tags_json TEXT NOT NULL,
    chunk_version TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    section_title TEXT,
    char_start INTEGER NOT NULL,
    text TEXT NOT NULL,
    source_file TEXT,
    vector_row INTEGER NOT NULL UNIQUE,
    FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);

CREATE INDEX idx_chunks_company_type_date
ON chunks(company_id, document_type, published_date);

CREATE INDEX idx_chunks_document
ON chunks(doc_id, chunk_index);

CREATE INDEX idx_chunks_publisher
ON chunks(publisher);
"""


def build_corpus_store(
    path: Path,
    documents: dict[str, DocumentMetadata],
    records: Iterable[EmbeddingRecord],
    *,
    insert_batch_size: int = 1000,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(CORPUS_SCHEMA_SQL)
        conn.executemany(
            """
            INSERT INTO documents (
                doc_id, company_id, document_type, title, published_date,
                publisher, tags_json, source_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    document.document_id,
                    document.company_id,
                    document.document_type,
                    document.title,
                    document.published_date,
                    document.publisher,
                    json.dumps(document.tags, ensure_ascii=False, separators=(",", ":")),
                    document.source_ref,
                )
                for document in documents.values()
            ),
        )
        rows: list[tuple] = []
        count = 0
        for record in records:
            document = record.document
            rows.append(
                (
                    record.chunk_id,
                    record.document_id,
                    document.company_id,
                    document.document_type,
                    document.title,
                    document.published_date,
                    document.publisher,
                    json.dumps(document.tags, ensure_ascii=False, separators=(",", ":")),
                    record.chunk_version,
                    record.chunk_index,
                    record.section_title,
                    record.char_start,
                    record.text,
                    document.source_ref,
                    record.vector_row,
                )
            )
            if len(rows) >= insert_batch_size:
                insert_chunk_rows(conn, rows)
                count += len(rows)
                rows.clear()
        if rows:
            insert_chunk_rows(conn, rows)
            count += len(rows)
        conn.commit()
    return count


def insert_chunk_rows(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO chunks (
            chunk_id, doc_id, company_id, document_type, title, published_date,
            publisher, tags_json, chunk_version, chunk_index, section_title,
            char_start, text, source_file, vector_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def load_vector_ids(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "SELECT chunk_id FROM chunks ORDER BY vector_row ASC"
        ).fetchall()
    return [str(row[0]) for row in rows]


def validate_corpus_store(path: Path, expected_chunks: int) -> None:
    with closing(sqlite3.connect(path)) as conn:
        document_count = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        chunk_count = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        vector_rows = int(conn.execute("SELECT COUNT(DISTINCT vector_row) FROM chunks").fetchone()[0])
    if document_count <= 0:
        raise RuntimeError("Corpus SQLite contains no documents.")
    if chunk_count != expected_chunks or vector_rows != expected_chunks:
        raise RuntimeError(
            "Corpus SQLite count mismatch: "
            f"expected={expected_chunks}, chunks={chunk_count}, vector_rows={vector_rows}."
        )

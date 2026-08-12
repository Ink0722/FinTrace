import sqlite3
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    title TEXT NOT NULL,
    published_date TEXT NOT NULL,
    source_file TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    parser TEXT NOT NULL,
    parse_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    title TEXT NOT NULL,
    published_date TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    section_title TEXT,
    text TEXT NOT NULL,
    source_file TEXT NOT NULL,
    FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_company_type_date
ON chunks(company_id, document_type, published_date);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def clear_store(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM documents")
    conn.commit()


def document_unchanged(conn: sqlite3.Connection, source_file: str, file_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM documents WHERE source_file = ? AND file_hash = ? LIMIT 1",
        (source_file, file_hash),
    ).fetchone()
    return row is not None


def delete_document_chunks(conn: sqlite3.Connection, doc_id: str) -> None:
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))


def insert_document(conn: sqlite3.Connection, document: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO documents (
            doc_id, company_id, document_type, title, published_date,
            source_file, file_hash, parser, parse_status
        )
        VALUES (:doc_id, :company_id, :document_type, :title, :published_date,
            :source_file, :file_hash, :parser, :parse_status)
        """,
        document,
    )


def insert_chunks(conn: sqlite3.Connection, chunks: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO chunks (
            chunk_id, doc_id, company_id, document_type, title, published_date,
            chunk_index, page_start, page_end, section_title, text, source_file
        )
        VALUES (
            :chunk_id, :doc_id, :company_id, :document_type, :title, :published_date,
            :chunk_index, :page_start, :page_end, :section_title, :text, :source_file
        )
        """,
        chunks,
    )

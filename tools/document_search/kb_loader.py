import sqlite3
from datetime import date
from pathlib import Path

from schemas.document import DocumentChunk
from tools.document_search.config import DocumentSearchConfig


def resolve_kb_path() -> Path:
    return DocumentSearchConfig.from_env().kb_path


def knowledge_base_available(kb_path: Path | None = None) -> bool:
    path = kb_path or resolve_kb_path()
    return path.exists() and path.is_file()


def load_kb_chunks(
    *,
    company_id: str | None = None,
    document_types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    kb_path: Path | None = None,
) -> list[DocumentChunk]:
    path = kb_path or resolve_kb_path()
    if not knowledge_base_available(path):
        return []

    clauses: list[str] = []
    params: list[str] = []
    if company_id:
        clauses.append("company_id = ?")
        params.append(company_id)
    if document_types:
        placeholders = ",".join("?" for _ in document_types)
        clauses.append(f"document_type IN ({placeholders})")
        params.extend(document_types)
    if start_date:
        clauses.append("published_date >= ?")
        params.append(start_date.isoformat())
    if end_date:
        clauses.append("published_date <= ?")
        params.append(end_date.isoformat())

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        SELECT chunk_id, doc_id, company_id, document_type, title, published_date,
               page_start, section_title, text, source_file
        FROM chunks
        {where_sql}
        ORDER BY published_date DESC, chunk_index ASC
    """
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [row_to_chunk(row) for row in rows]


def load_kb_chunks_by_ids(chunk_ids: list[str], kb_path: Path | None = None) -> dict[str, DocumentChunk]:
    if not chunk_ids:
        return {}
    path = kb_path or resolve_kb_path()
    if not knowledge_base_available(path):
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    sql = f"""
        SELECT chunk_id, doc_id, company_id, document_type, title, published_date,
               page_start, section_title, text, source_file
        FROM chunks
        WHERE chunk_id IN ({placeholders})
    """
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, chunk_ids).fetchall()
    return {row["chunk_id"]: row_to_chunk(row) for row in rows}


def load_filtered_chunk_ids(
    *,
    company_id: str | None = None,
    document_types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    kb_path: Path | None = None,
) -> set[str]:
    path = kb_path or resolve_kb_path()
    if not knowledge_base_available(path):
        return set()
    clauses, params = _filter_sql(company_id, document_types, start_date, end_date)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    with sqlite3.connect(path) as conn:
        rows = conn.execute(f"SELECT chunk_id FROM chunks {where_sql}", params).fetchall()
    return {str(row[0]) for row in rows}


def _filter_sql(
    company_id: str | None,
    document_types: list[str] | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[list[str], list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if company_id:
        clauses.append("company_id = ?")
        params.append(company_id)
    if document_types:
        placeholders = ",".join("?" for _ in document_types)
        clauses.append(f"document_type IN ({placeholders})")
        params.extend(document_types)
    if start_date:
        clauses.append("published_date >= ?")
        params.append(start_date.isoformat())
    if end_date:
        clauses.append("published_date <= ?")
        params.append(end_date.isoformat())
    return clauses, params


def row_to_chunk(row: sqlite3.Row) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=row["chunk_id"],
        document_id=row["doc_id"],
        company_id=row["company_id"],
        document_type=row["document_type"],
        title=row["title"],
        publish_date=date.fromisoformat(row["published_date"]),
        page=row["page_start"],
        section=row["section_title"],
        text=row["text"],
        source_path=row["source_file"],
    )

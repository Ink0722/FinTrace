import argparse
import hashlib
import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from knowledge_base.document_ingestion.chunker import chunk_pages, stable_doc_id
from knowledge_base.document_ingestion.kb_store import (
    clear_store,
    connect,
    delete_document_chunks,
    document_unchanged,
    insert_chunks,
    insert_document,
)
from knowledge_base.document_ingestion.parsers import parse_document
from knowledge_base.document_ingestion.vector_index import build_vector_index
from knowledge_base.embeddings.client import build_embedding_client


SUPPORTED_SUFFIXES = {".txt", ".md", ".docx", ".pdf"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build FinTrace local document knowledge base")
    parser.add_argument("--raw-dir", default="data/raw_documents", help="Directory containing source documents")
    parser.add_argument("--kb-dir", default="data/knowledge_base", help="Directory to write SQLite/FAISS files")
    parser.add_argument("--max-chars", type=int, default=900, help="Maximum characters per chunk")
    parser.add_argument("--overlap-chars", type=int, default=120, help="Overlap characters for long chunks")
    parser.add_argument("--append", action="store_true", help="Append to existing SQLite store instead of rebuilding")
    parser.add_argument("--skip-unchanged", action="store_true", help="Skip source files with unchanged SHA-256 hash")
    parser.add_argument("--build-vector", action="store_true", help="Build FAISS vector index after SQLite ingestion")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw_dir = Path(args.raw_dir)
    kb_dir = Path(args.kb_dir)
    db_path = kb_dir / "fintrace_kb.sqlite"
    conn = connect(db_path)
    if not args.append and not args.skip_unchanged:
        clear_store(conn)

    files = [path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES]
    doc_count = 0
    chunk_count = 0
    failures: list[dict] = []
    document_reports: list[dict] = []
    skipped_count = 0
    for path in files:
        started_at = time.perf_counter()
        try:
            metadata = parse_metadata(path)
            doc_id = stable_doc_id(path, metadata["company_id"], metadata["published_date"], metadata["document_type"])
            file_hash = file_sha256(path)
            if args.skip_unchanged and document_unchanged(conn, str(path), file_hash):
                skipped_count += 1
                document_reports.append(
                    {
                        "source_file": str(path),
                        "company_id": metadata["company_id"],
                        "document_type": metadata["document_type"],
                        "published_date": metadata["published_date"],
                        "parse_status": "skipped_unchanged",
                        "page_count": 0,
                        "text_char_count": 0,
                        "chunk_count": 0,
                        "table_count": 0,
                        "section_count": 0,
                        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                        "warnings": [],
                    }
                )
                continue
            pages = parse_document(path)
            chunks = chunk_pages(pages, doc_id=doc_id, max_chars=args.max_chars, overlap_chars=args.overlap_chars)
            document = {
                "doc_id": doc_id,
                "company_id": metadata["company_id"],
                "document_type": metadata["document_type"],
                "title": metadata["title"],
                "published_date": metadata["published_date"],
                "source_file": str(path),
                "file_hash": file_hash,
                "parser": path.suffix.lower().lstrip("."),
                "parse_status": "success",
            }
            insert_document(conn, document)
            delete_document_chunks(conn, doc_id)
            insert_chunks(conn, [attach_chunk_metadata(chunk, document) for chunk in chunks])
            conn.commit()
            doc_count += 1
            chunk_count += len(chunks)
            document_reports.append(
                build_document_report(
                    path=path,
                    metadata=metadata,
                    pages=pages,
                    chunks=chunks,
                    status="success",
                    warnings=build_parse_warnings(pages, chunks),
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
                )
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failures.append({"file": str(path), "error": error})
            document_reports.append(
                {
                    "source_file": str(path),
                    "parse_status": "failed",
                    "warnings": [],
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                    "error": error,
                }
            )

    embedding_info = None
    if args.build_vector:
        try:
            embedding_info = build_vector_index(db_path, kb_dir, build_embedding_client())
        except Exception as exc:
            embedding_info = {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}
            failures.append({"file": "__vector_index__", "error": embedding_info["error"]})

    write_manifest(kb_dir, db_path, raw_dir, doc_count, chunk_count, failures, embedding_info)
    write_parse_report(kb_dir, raw_dir, document_reports, failures, skipped_count)
    conn.close()
    print(f"Built knowledge base: documents={doc_count}, chunks={chunk_count}, failures={len(failures)}")
    return 1 if failures and not doc_count else 0


def parse_metadata(path: Path) -> dict[str, str]:
    parts = path.stem.split("_", 2)
    if len(parts) >= 3:
        company_id, published_date, document_type = parts
    else:
        company_id = path.parent.parent.name if path.parent.parent != path.parent else "000001.SZ"
        document_type = path.parent.name
        published_date = "1970-01-01"
    return {
        "company_id": company_id.upper(),
        "published_date": published_date,
        "document_type": document_type,
        "title": path.stem,
    }


def attach_chunk_metadata(chunk: dict, document: dict) -> dict:
    return {
        **chunk,
        "doc_id": document["doc_id"],
        "company_id": document["company_id"],
        "document_type": document["document_type"],
        "title": document["title"],
        "published_date": document["published_date"],
        "section_title": chunk.get("section_title"),
        "source_file": document["source_file"],
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(
    kb_dir: Path,
    db_path: Path,
    raw_dir: Path,
    doc_count: int,
    chunk_count: int,
    failures: list[dict],
    embedding_info: dict | None,
) -> None:
    kb_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "raw_dir": str(raw_dir),
        "sqlite_path": str(db_path),
        "vector_index_path": str(kb_dir / "vector.faiss"),
        "vector_ids_path": str(kb_dir / "vector_ids.json"),
        "document_count": doc_count,
        "chunk_count": chunk_count,
        "failures": failures,
        "embedding": embedding_info,
    }
    (kb_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parse_warnings(pages: list[dict], chunks: list[dict]) -> list[str]:
    warnings: list[str] = []
    text_char_count = sum(len(str(page.get("text") or "")) for page in pages)
    if not pages or text_char_count == 0:
        warnings.append("empty_text")
    elif text_char_count < 100:
        warnings.append("low_text_volume_may_need_ocr")
    if not chunks:
        warnings.append("no_chunks")
    return warnings


def build_document_report(
    path: Path,
    metadata: dict,
    pages: list[dict],
    chunks: list[dict],
    status: str,
    warnings: list[str],
    duration_ms: float,
) -> dict:
    return {
        "source_file": str(path),
        "company_id": metadata["company_id"],
        "document_type": metadata["document_type"],
        "published_date": metadata["published_date"],
        "parse_status": status,
        "page_count": len(pages),
        "text_char_count": sum(len(str(page.get("text") or "")) for page in pages),
        "chunk_count": len(chunks),
        "table_count": sum(int(page.get("table_count") or 0) for page in pages),
        "section_count": len({chunk.get("section_title") for chunk in chunks if chunk.get("section_title")}),
        "duration_ms": duration_ms,
        "warnings": warnings,
    }


def write_parse_report(kb_dir: Path, raw_dir: Path, documents: list[dict], failures: list[dict], skipped_count: int) -> None:
    summary = {
        "document_count": len(documents),
        "chunk_count": sum(document.get("chunk_count", 0) for document in documents),
        "failed_count": len(failures),
        "skipped_count": skipped_count,
        "table_count": sum(document.get("table_count", 0) for document in documents),
        "duration_ms": round(sum(document.get("duration_ms", 0) for document in documents), 3),
        "empty_text_count": sum(1 for document in documents if "empty_text" in document.get("warnings", [])),
        "needs_ocr_count": sum(1 for document in documents if "low_text_volume_may_need_ocr" in document.get("warnings", [])),
    }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "raw_dir": str(raw_dir),
        "summary": summary,
        "documents": documents,
        "failures": failures,
    }
    (kb_dir / "parse_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

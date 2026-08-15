from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from data_pipeline.text.chunker import ChunkingConfig, chunk_text
from data_pipeline.text.document_builder import display_path, length_summary, write_json_atomic


CHUNK_KEYS = {
    "chunk_version",
    "chunk_id",
    "document_id",
    "chunk_index",
    "section_title",
    "char_start",
    "text",
}
SUPPORTED_DOCUMENT_TYPES = {"announcement", "research_report"}


class ChunkBuildError(RuntimeError):
    pass


def build_chunks(
    *,
    data_dir: Path,
    documents_path: Path | None = None,
    output_path: Path | None = None,
    report_path: Path | None = None,
    manifest_path: Path | None = None,
    version: str = "chunks-v1",
    config: ChunkingConfig | None = None,
) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    documents_path = (documents_path or data_dir / "text_corpus" / "documents.jsonl").resolve()
    output_path = (output_path or data_dir / "text_corpus" / "chunks.jsonl").resolve()
    report_path = (report_path or data_dir / "text_corpus" / "chunk_quality.json").resolve()
    manifest_path = (manifest_path or data_dir / "text_corpus" / "chunk_manifest.json").resolve()
    config = config or ChunkingConfig()
    if not documents_path.is_file():
        raise FileNotFoundError(f"Required Document corpus does not exist: {documents_path}")
    if not version.strip():
        raise ValueError("Chunk version cannot be empty")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_name(f"{output_path.name}.tmp")
    source_hasher = hashlib.sha256()
    output_hasher = hashlib.sha256()
    seen_document_ids: set[str] = set()
    seen_chunk_ids: set[str] = set()
    document_counts: Counter[str] = Counter()
    chunk_counts: Counter[str] = Counter()
    splitter_stats: Counter[str] = Counter()
    lengths: list[int] = []
    lengths_by_type: dict[str, list[int]] = defaultdict(list)
    source_text_chars = 0

    try:
        with temporary_output.open("w", encoding="utf-8", newline="\n") as output:
            for document, raw_line in iter_documents(documents_path):
                source_hasher.update(raw_line.encode("utf-8"))
                document_id, document_type, text = validate_document(document)
                if document_id in seen_document_ids:
                    raise ChunkBuildError(f"Duplicate document_id: {document_id}")
                seen_document_ids.add(document_id)
                document_counts[document_type] += 1
                source_text_chars += len(text)

                result = chunk_text(text, document_type=document_type, config=config)
                if not result.pieces:
                    raise ChunkBuildError(f"Document produced no chunks: {document_id}")
                validate_coverage(document_id, text, result.pieces, config=config)
                splitter_stats.update(result.stats)

                for chunk_index, piece in enumerate(result.pieces, start=1):
                    chunk_id = f"{document_id}-C{chunk_index:04d}"
                    if chunk_id in seen_chunk_ids:
                        raise ChunkBuildError(f"Duplicate chunk_id: {chunk_id}")
                    seen_chunk_ids.add(chunk_id)
                    chunk = {
                        "chunk_version": version,
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "chunk_index": chunk_index,
                        "section_title": piece.section_title,
                        "char_start": piece.char_start,
                        "text": piece.text,
                    }
                    if set(chunk) != CHUNK_KEYS:
                        raise ChunkBuildError(f"Unexpected Chunk schema for {chunk_id}")
                    encoded = (json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                        "utf-8"
                    )
                    output.write(encoded.decode("utf-8"))
                    output_hasher.update(encoded)
                    length = len(piece.text)
                    lengths.append(length)
                    lengths_by_type[document_type].append(length)
                    chunk_counts[document_type] += 1
        os.replace(temporary_output, output_path)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise

    created_at = datetime.now().astimezone().isoformat()
    report = {
        "created_at": created_at,
        "input": display_path(documents_path),
        "output": display_path(output_path),
        "chunk_version": version,
        "config": asdict(config),
        "total_documents": sum(document_counts.values()),
        "total_chunks": sum(chunk_counts.values()),
        "documents_by_type": dict(sorted(document_counts.items())),
        "chunks_by_type": dict(sorted(chunk_counts.items())),
        "chunk_length_chars": length_summary(lengths),
        "chunk_length_chars_by_type": {
            key: length_summary(value) for key, value in sorted(lengths_by_type.items())
        },
        "short_chunks": sum(length < config.min_chars for length in lengths),
        "chunks_at_most_30_chars": sum(length <= 30 for length in lengths),
        "chunks_at_most_80_chars": sum(length <= 80 for length in lengths),
        "chunks_over_hard_max": sum(length > config.hard_max_chars for length in lengths),
        "chunks_with_section_title": splitter_stats["chunks_with_section_title"],
        "splitter": dict(sorted(splitter_stats.items())),
        "source_text_chars": source_text_chars,
        "duplicate_chunk_ids": 0,
        "empty_chunks": 0,
        "coverage_failures": 0,
    }
    manifest = {
        "chunk_version": version,
        "created_at": created_at,
        "source_documents": display_path(documents_path),
        "source_documents_sha256": source_hasher.hexdigest(),
        "chunks": display_path(output_path),
        "chunks_sha256": output_hasher.hexdigest(),
        "schema_fields": [
            "chunk_version",
            "chunk_id",
            "document_id",
            "chunk_index",
            "section_title",
            "char_start",
            "text",
        ],
        "config": asdict(config),
        "total_documents": report["total_documents"],
        "total_chunks": report["total_chunks"],
    }
    write_json_atomic(report_path, report)
    write_json_atomic(manifest_path, manifest)
    return report


def iter_documents(path: Path) -> Iterator[tuple[dict[str, Any], str]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ChunkBuildError(f"Invalid JSON in {path} at line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ChunkBuildError(f"Expected object in {path} at line {line_number}")
            yield value, line


def validate_document(document: dict[str, Any]) -> tuple[str, str, str]:
    document_id = str(document.get("document_id") or "").strip()
    document_type = str(document.get("document_type") or "").strip()
    text = document.get("text")
    if not document_id:
        raise ChunkBuildError("Document is missing document_id")
    if document_type not in SUPPORTED_DOCUMENT_TYPES:
        raise ChunkBuildError(f"Unsupported document_type for {document_id}: {document_type}")
    if not isinstance(text, str) or not text.strip():
        raise ChunkBuildError(f"Document has empty text: {document_id}")
    return document_id, document_type, text


def validate_coverage(document_id: str, text: str, pieces, *, config: ChunkingConfig) -> None:
    previous_end = 0
    reconstructed: list[str] = []
    for piece in pieces:
        if not piece.text:
            raise ChunkBuildError(f"Empty chunk in document: {document_id}")
        end = piece.char_start + len(piece.text)
        if piece.char_start < previous_end:
            raise ChunkBuildError(f"Overlapping chunks in document: {document_id}")
        if text[piece.char_start:end] != piece.text:
            raise ChunkBuildError(f"Invalid char_start in document: {document_id}")
        if len(piece.text) > config.hard_max_chars:
            raise ChunkBuildError(f"Chunk exceeds hard max in document: {document_id}")
        reconstructed.append(piece.text)
        previous_end = end
    if remove_whitespace("".join(reconstructed)) != remove_whitespace(text):
        raise ChunkBuildError(f"Chunk coverage mismatch in document: {document_id}")


def remove_whitespace(value: str) -> str:
    return "".join(value.split())

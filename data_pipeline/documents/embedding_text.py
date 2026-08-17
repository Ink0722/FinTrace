from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


EMBEDDING_TEXT_VERSION = "embedding-text-v1"
DOCUMENT_TYPE_LABELS = {
    "announcement": "公告",
    "research_report": "研报摘要",
}
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ASCII_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


@dataclass(frozen=True)
class DocumentMetadata:
    document_id: str
    document_type: str
    company_id: str
    title: str
    published_date: str
    publisher: str | None
    tags: tuple[str, ...]
    source_ref: str | None


@dataclass(frozen=True)
class EmbeddingRecord:
    vector_row: int
    chunk_id: str
    document_id: str
    chunk_version: str
    chunk_index: int
    section_title: str | None
    char_start: int
    text: str
    embedding_text: str
    document: DocumentMetadata


def load_document_metadata(path: Path) -> dict[str, DocumentMetadata]:
    documents: dict[str, DocumentMetadata] = {}
    for line_number, value in iter_jsonl(path):
        document_id = required_string(value, "document_id", path, line_number)
        if document_id in documents:
            raise ValueError(f"Duplicate document_id in {path} at line {line_number}: {document_id}")
        tags = value.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError(f"tags must be a list in {path} at line {line_number}")
        documents[document_id] = DocumentMetadata(
            document_id=document_id,
            document_type=required_string(value, "document_type", path, line_number),
            company_id=required_string(value, "company_id", path, line_number),
            title=required_string(value, "title", path, line_number),
            published_date=required_string(value, "published_date", path, line_number),
            publisher=optional_string(value.get("publisher")),
            tags=tuple(unique_strings(tags)),
            source_ref=optional_string(value.get("source_ref")),
        )
    return documents


def iter_embedding_records(
    chunks_path: Path,
    documents: dict[str, DocumentMetadata],
) -> Iterator[EmbeddingRecord]:
    seen_chunk_ids: set[str] = set()
    for vector_row, (line_number, value) in enumerate(iter_jsonl(chunks_path)):
        chunk_id = required_string(value, "chunk_id", chunks_path, line_number)
        if chunk_id in seen_chunk_ids:
            raise ValueError(f"Duplicate chunk_id in {chunks_path} at line {line_number}: {chunk_id}")
        seen_chunk_ids.add(chunk_id)
        document_id = required_string(value, "document_id", chunks_path, line_number)
        document = documents.get(document_id)
        if document is None:
            raise ValueError(
                f"Unknown document_id in {chunks_path} at line {line_number}: {document_id}"
            )
        text = required_string(value, "text", chunks_path, line_number)
        section_title = optional_string(value.get("section_title"))
        yield EmbeddingRecord(
            vector_row=vector_row,
            chunk_id=chunk_id,
            document_id=document_id,
            chunk_version=required_string(value, "chunk_version", chunks_path, line_number),
            chunk_index=required_int(value, "chunk_index", chunks_path, line_number),
            section_title=section_title,
            char_start=required_int(value, "char_start", chunks_path, line_number),
            text=text,
            embedding_text=format_embedding_text(document, section_title, text),
            document=document,
        )


def format_embedding_text(
    document: DocumentMetadata,
    section_title: str | None,
    text: str,
) -> str:
    lines = [
        f"文档类型：{DOCUMENT_TYPE_LABELS.get(document.document_type, document.document_type)}",
        f"证券代码：{document.company_id}",
        f"标题：{document.title}",
        f"发布日期：{document.published_date}",
    ]
    if document.publisher:
        lines.append(f"发布机构：{document.publisher}")
    if document.tags:
        lines.append(f"标签：{'；'.join(document.tags)}")
    if section_title:
        lines.append(f"章节：{section_title}")
    lines.extend(["正文：", text])
    return "\n".join(lines)


def estimate_embedding_corpus(
    chunks_path: Path,
    documents: dict[str, DocumentMetadata],
) -> dict[str, Any]:
    chunk_count = 0
    raw_text_chars = 0
    embedding_chars = 0
    max_embedding_chars = 0
    counts_by_type: Counter[str] = Counter()
    chars_by_type: Counter[str] = Counter()
    cjk_chars = 0
    ascii_alnum_chars = 0
    nonspace_other_chars = 0
    for record in iter_embedding_records(chunks_path, documents):
        chunk_count += 1
        raw_text_chars += len(record.text)
        current_chars = len(record.embedding_text)
        embedding_chars += current_chars
        max_embedding_chars = max(max_embedding_chars, current_chars)
        counts_by_type[record.document.document_type] += 1
        chars_by_type[record.document.document_type] += current_chars
        cjk = len(CJK_RE.findall(record.embedding_text))
        ascii_alnum = len(ASCII_ALNUM_RE.findall(record.embedding_text))
        nonspace = sum(1 for char in record.embedding_text if not char.isspace())
        cjk_chars += cjk
        ascii_alnum_chars += ascii_alnum
        nonspace_other_chars += max(0, nonspace - cjk - ascii_alnum)

    token_estimate = estimate_token_range(cjk_chars, ascii_alnum_chars, nonspace_other_chars)
    return {
        "embedding_text_version": EMBEDDING_TEXT_VERSION,
        "document_count": len(documents),
        "chunk_count": chunk_count,
        "raw_chunk_text_chars": raw_text_chars,
        "embedding_text_chars": embedding_chars,
        "metadata_added_chars": embedding_chars - raw_text_chars,
        "max_embedding_text_chars": max_embedding_chars,
        "chunks_by_document_type": dict(sorted(counts_by_type.items())),
        "embedding_chars_by_document_type": dict(sorted(chars_by_type.items())),
        "character_classes": {
            "cjk": cjk_chars,
            "ascii_alnum": ascii_alnum_chars,
            "other_nonspace": nonspace_other_chars,
        },
        "estimated_tokens": token_estimate,
    }


def estimate_token_range(cjk: int, ascii_alnum: int, other: int) -> dict[str, int | str]:
    low = math.ceil(cjk * 0.50 + ascii_alnum / 4.5 + other * 0.35)
    midpoint = math.ceil(cjk * 0.70 + ascii_alnum / 3.5 + other * 0.60)
    high = math.ceil(cjk * 1.00 + ascii_alnum / 2.5 + other * 1.00)
    return {
        "low": low,
        "midpoint": midpoint,
        "high": high,
        "method": "character-class estimate; actual billing uses the Qwen tokenizer and API usage",
    }


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} at line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected object in {path} at line {line_number}")
            yield line_number, value


def required_string(value: dict[str, Any], key: str, path: Path, line_number: int) -> str:
    result = optional_string(value.get(key))
    if result is None:
        raise ValueError(f"Missing {key} in {path} at line {line_number}")
    return result


def required_int(value: dict[str, Any], key: str, path: Path, line_number: int) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int):
        raise ValueError(f"Invalid {key} in {path} at line {line_number}")
    return result


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def unique_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = optional_string(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result

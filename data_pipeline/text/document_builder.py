from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from data_pipeline.text.cleaner import clean_tags, clean_text, remove_leading_title_lines


A_SHARE_SUFFIXES = {"XSHG": ".SH", "XSHE": ".SZ", "XBEI": ".BJ"}
COMPANY_ID_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")


class DocumentBuildError(RuntimeError):
    pass


def build_documents(
    *,
    data_dir: Path,
    output_path: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    output_path = (output_path or data_dir / "text_corpus" / "documents.jsonl").resolve()
    report_path = (report_path or data_dir / "text_corpus" / "document_quality.json").resolve()
    announcement_path = data_dir / "jsonl" / "announcements.jsonl"
    research_path = data_dir / "jsonl" / "research_reports.jsonl"
    require_file(announcement_path)
    require_file(research_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_name(f"{output_path.name}.tmp")
    seen_ids: set[str] = set()
    lengths: list[int] = []
    input_counts: Counter[str] = Counter()
    output_counts: Counter[str] = Counter()
    cleaning_counts: Counter[str] = Counter()
    skipped: dict[str, Counter[str]] = defaultdict(Counter)
    skipped_examples: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    def reject(document_type: str, reason: str, record_id: object) -> None:
        skipped[document_type][reason] += 1
        examples = skipped_examples[document_type][reason]
        if len(examples) < 20:
            examples.append(str(record_id or "<missing>"))

    try:
        with temporary_output.open("w", encoding="utf-8", newline="\n") as output:
            for record in iter_jsonl(announcement_path):
                input_counts["announcement"] += 1
                document, reason, removed_title_lines = announcement_document(record, data_dir=data_dir)
                if document is None:
                    reject("announcement", reason or "invalid_record", record.get("id"))
                    continue
                if removed_title_lines:
                    cleaning_counts["announcement_documents_cleaned"] += 1
                    cleaning_counts["announcement_leading_title_lines_removed"] += removed_title_lines
                write_document(output, document, seen_ids, lengths)
                output_counts["announcement"] += 1

            for record in iter_jsonl(research_path):
                input_counts["research_report"] += 1
                document, reason = research_document(record, source_path=research_path)
                if document is None:
                    reject("research_report", reason or "invalid_record", record.get("report_id"))
                    continue
                write_document(output, document, seen_ids, lengths)
                output_counts["research_report"] += 1
        os.replace(temporary_output, output_path)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise

    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "inputs": {
            "announcements": display_path(announcement_path),
            "research_reports": display_path(research_path),
        },
        "output": display_path(output_path),
        "datasets": {
            "announcement": dataset_report(
                input_counts["announcement"],
                output_counts["announcement"],
                skipped["announcement"],
                skipped_examples["announcement"],
            ),
            "research_report": dataset_report(
                input_counts["research_report"],
                output_counts["research_report"],
                skipped["research_report"],
                skipped_examples["research_report"],
            ),
        },
        "total_documents": sum(output_counts.values()),
        "duplicate_document_ids": 0,
        "empty_texts": 0,
        "cleaning": {
            "announcement_documents_cleaned": cleaning_counts[
                "announcement_documents_cleaned"
            ],
            "announcement_leading_title_lines_removed": cleaning_counts[
                "announcement_leading_title_lines_removed"
            ],
        },
        "text_length_chars": length_summary(lengths),
    }
    write_json_atomic(report_path, report)
    return report


def announcement_document(
    record: dict[str, Any], *, data_dir: Path
) -> tuple[dict[str, Any] | None, str | None, int]:
    status = clean_text(record.get("download_status"))
    if status != "success":
        return None, status or "download_not_successful", 0

    record_id = clean_text(record.get("id"))
    company_id = clean_text(record.get("s_info_windcode")).upper()
    title = clean_text(record.get("n_info_title"))
    published_date = normalize_iso_date(record.get("ann_dt"))
    raw_document_path = clean_text(record.get("document_path"))
    if not record_id:
        return None, "missing_id", 0
    if not COMPANY_ID_RE.fullmatch(company_id):
        return None, "invalid_company_id", 0
    if not title:
        return None, "missing_title", 0
    if not published_date:
        return None, "invalid_published_date", 0
    if not raw_document_path:
        return None, "missing_document_path", 0

    document_path = resolve_document_path(raw_document_path, data_dir=data_dir)
    if not document_path.is_file():
        return None, "missing_document_file", 0
    try:
        text = clean_text(read_text(document_path))
    except (OSError, UnicodeError):
        return None, "unreadable_document_file", 0
    if not text:
        return None, "empty_text", 0
    text, removed_title_lines = remove_leading_title_lines(text, title)

    return {
        "document_id": f"ANN-{record_id}",
        "document_type": "announcement",
        "company_id": company_id,
        "title": title,
        "published_date": published_date,
        "tags": clean_tags(record.get("category_names")),
        "text": text,
        "source_ref": path_reference(raw_document_path),
    }, None, removed_title_lines


def research_document(
    record: dict[str, Any], *, source_path: Path
) -> tuple[dict[str, Any] | None, str | None]:
    report_id = clean_text(record.get("report_id"))
    exchange_code = clean_text(record.get("exchange_code")).upper()
    suffix = A_SHARE_SUFFIXES.get(exchange_code)
    if not report_id:
        return None, "missing_report_id"
    if suffix is None:
        return None, "unsupported_exchange"

    security_code = clean_text(record.get("sec_code"))
    if not re.fullmatch(r"\d{6}", security_code):
        return None, "invalid_security_code"
    company_id = f"{security_code}{suffix}"
    title = clean_text(record.get("title"))
    published_date = normalize_iso_date(record.get("publish_date"))
    publisher = clean_text(record.get("org_name"))
    text = clean_text(record.get("abstract"))
    if not title:
        return None, "missing_title"
    if not published_date:
        return None, "invalid_published_date"
    if not publisher:
        return None, "missing_publisher"
    if not text:
        return None, "empty_text"

    tag_values = [
        record.get("report_sub_type"),
        record.get("rating_org"),
        record.get("rating_change"),
    ]
    if record.get("first_cover") == 1:
        tag_values.append("首次覆盖")
    return {
        "document_id": f"RR-{report_id}",
        "document_type": "research_report",
        "company_id": company_id,
        "title": title,
        "published_date": published_date,
        "publisher": publisher,
        "tags": clean_tags(tag_values),
        "text": text,
        "source_ref": f"{display_path(source_path)}#{report_id}",
    }, None


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DocumentBuildError(f"Invalid JSON in {path} at line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise DocumentBuildError(f"Expected object in {path} at line {line_number}")
            yield value


def write_document(output, document: dict[str, Any], seen_ids: set[str], lengths: list[int]) -> None:
    document_id = document["document_id"]
    if document_id in seen_ids:
        raise DocumentBuildError(f"Duplicate document_id: {document_id}")
    seen_ids.add(document_id)
    lengths.append(len(document["text"]))
    output.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n")


def resolve_document_path(raw_path: str, *, data_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    candidates = [Path.cwd() / path, data_dir.parent / path, data_dir / path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return candidates[0].resolve()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030")


def normalize_iso_date(value: object) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def path_reference(value: str) -> str:
    return value.replace("\\", "/")


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def dataset_report(
    input_count: int,
    output_count: int,
    skipped: Counter[str],
    examples: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        "input": input_count,
        "output": output_count,
        "skipped": dict(sorted(skipped.items())),
        "skipped_examples": {key: examples[key] for key in sorted(examples)},
    }


def length_summary(lengths: list[int]) -> dict[str, float | int]:
    if not lengths:
        return {"min": 0, "p50": 0, "p95": 0, "max": 0, "mean": 0.0}
    ordered = sorted(lengths)
    return {
        "min": ordered[0],
        "p50": percentile(ordered, 0.50),
        "p95": percentile(ordered, 0.95),
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 1),
    }


def percentile(ordered: list[int], fraction: float) -> int:
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Required input does not exist: {path}")


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)

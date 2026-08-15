"""Finalize exclusion of announcements whose attachments have no text layer."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Preprocessed data root")
    parser.add_argument("--dry-run", action="store_true", help="Validate targets without changing files")
    return parser


def is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.resolve()
    jsonl_path = data_dir / "jsonl" / "announcements.jsonl"
    documents_dir = (data_dir / "documents" / "announcements").resolve()
    if not jsonl_path.is_file():
        raise FileNotFoundError(f"Announcement JSONL does not exist: {jsonl_path}")

    records: list[dict] = []
    targets: list[tuple[dict, Path | None]] = []
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            records.append(record)
            if record.get("download_status") != "content_unavailable":
                continue
            error = str(record.get("download_error") or "")
            if "too little extractable text" not in error:
                raise RuntimeError(
                    f"Refusing to exclude non-text-layer-unrelated record {record.get('id')}: {error}"
                )
            raw_path = record.get("document_path")
            document_path = Path(str(raw_path)).resolve() if raw_path else None
            if document_path and not is_within(document_path, documents_dir):
                raise RuntimeError(f"Refusing to remove file outside announcement directory: {document_path}")
            targets.append((record, document_path))

    existing_files = [path for _, path in targets if path and path.is_file()]
    print(
        f"Validated exclusions: records={len(targets)}, existing_text_files={len(existing_files)}",
        flush=True,
    )
    if args.dry_run:
        return 0

    excluded_at = datetime.now().astimezone().isoformat()
    manifest_rows: list[dict] = []
    for record, document_path in targets:
        previous_path = str(record.get("document_path") or "")
        if document_path and document_path.is_file():
            document_path.unlink()
        record["download_status"] = "excluded_no_text_layer"
        record["repair_status"] = "excluded"
        record["index_document"] = False
        record["excluded_reason"] = "attachment_has_no_extractable_text_layer"
        record["excluded_at"] = excluded_at
        record["excluded_document_path"] = previous_path or None
        record["document_path"] = None
        manifest_rows.append(
            {
                "id": record.get("id"),
                "stock_code": record.get("s_info_windcode"),
                "announcement_date": record.get("ann_dt"),
                "title": record.get("n_info_title"),
                "excluded_reason": record["excluded_reason"],
                "removed_document_path": previous_path,
                "html_url": (record.get("source_urls") or {}).get("html"),
                "attachment_url": (record.get("source_urls") or {}).get("pdf"),
            }
        )

    temporary_jsonl = jsonl_path.with_suffix(".jsonl.tmp")
    with temporary_jsonl.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary_jsonl, jsonl_path)

    manifest_path = data_dir / "announcement_exclusions.csv"
    temporary_manifest = manifest_path.with_suffix(".csv.tmp")
    fieldnames = [
        "id",
        "stock_code",
        "announcement_date",
        "title",
        "excluded_reason",
        "removed_document_path",
        "html_url",
        "attachment_url",
    ]
    with temporary_manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest_rows)
    os.replace(temporary_manifest, manifest_path)

    report_path = data_dir / "announcement_exclusion_report.json"
    temporary_report = report_path.with_suffix(".json.tmp")
    report = {
        "finished_at": excluded_at,
        "excluded_records": len(targets),
        "removed_text_files": len(existing_files),
        "announcement_jsonl": str(jsonl_path),
        "manifest": str(manifest_path),
    }
    temporary_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_report, report_path)
    print(f"Finalized exclusions: {report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

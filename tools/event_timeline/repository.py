from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from schemas.event import EventRecord
from tools.event_timeline.config import ANNOUNCEMENTS_FILENAME, EVENT_MAPPING_VERSION


class EventRepository:
    def __init__(self, index_path: Path):
        self.index_path = index_path

    def available(self) -> bool:
        return self.index_path.is_file()

    def query_events(self, *, company_id: str, event_types: list[str] | None, start_date: date | None, end_date: date | None, keywords: list[str] | None, knowledge_cutoff: date | None, limit: int) -> list[EventRecord]:
        clauses = ["company_id = ?"]
        params: list[object] = [company_id]
        if event_types:
            clauses.append(f"event_type IN ({','.join('?' for _ in event_types)})")
            params.extend(event_types)
        if start_date:
            clauses.append("event_date >= ?")
            params.append(start_date.isoformat())
        if end_date:
            clauses.append("event_date <= ?")
            params.append(end_date.isoformat())
        if knowledge_cutoff:
            clauses.append("announcement_date <= ?")
            params.append(knowledge_cutoff.isoformat())
        if keywords:
            keyword_clauses = []
            for keyword in keywords:
                keyword_clauses.append("(title LIKE ? OR summary LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%"])
            clauses.append("(" + " OR ".join(keyword_clauses) + ")")
        params.append(limit)
        sql = f"""
            SELECT event_id, company_id, event_type, event_date, announcement_date,
                   title, summary, entities_json, source_document_id, evidence_id,
                   source_path, extraction_method, quality_flags_json
            FROM events
            WHERE {' AND '.join(clauses)}
            ORDER BY event_date ASC, event_id ASC
            LIMIT ?
        """
        with sqlite3.connect(self.index_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_event(row) for row in rows]


def validate_event_index_snapshot(index_path: Path, normalized_dir: Path) -> list[str]:
    manifest_path = index_path.with_name("manifest.json")
    if not manifest_path.is_file():
        return [f"Event index manifest not found: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Event index manifest is invalid: {type(exc).__name__}: {exc}"]
    errors = []
    if manifest.get("mapping_version") != EVENT_MAPPING_VERSION:
        errors.append(f"mapping version mismatch: index={manifest.get('mapping_version')}, expected={EVENT_MAPPING_VERSION}")
    source_path = normalized_dir / ANNOUNCEMENTS_FILENAME
    recorded = manifest.get("source")
    if not source_path.is_file():
        return [*errors, f"normalized source not found: {source_path}"]
    if not isinstance(recorded, dict):
        return [*errors, "Event index manifest has no source object."]
    stat = source_path.stat()
    if int(recorded.get("size", -1)) != stat.st_size:
        errors.append(f"normalized source size changed: {source_path}")
    if int(recorded.get("mtime_ns", -1)) != stat.st_mtime_ns:
        errors.append(f"normalized source modification time changed: {source_path}")
    return errors


def _row_to_event(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=row["event_id"], company_id=row["company_id"], event_type=row["event_type"],
        event_date=date.fromisoformat(row["event_date"]), announcement_date=date.fromisoformat(row["announcement_date"]),
        entities=json.loads(row["entities_json"]), title=row["title"], summary=row["summary"],
        source_document_ids=[row["source_document_id"]], evidence_id=row["evidence_id"], source_path=row["source_path"],
        extraction_method=row["extraction_method"], quality_flags=json.loads(row["quality_flags_json"]),
    )


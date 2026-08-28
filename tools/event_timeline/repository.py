from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from schemas.event import EventRecord
from tools.event_timeline.config import EVENT_MAPPING_VERSION


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
                   effective_date, date_precision, event_stage, title, summary,
                   entities_json, agencies_json, reference_ids_json, topic_signature,
                   source_document_id, evidence_id,
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

    def diagnose_no_match(
        self, *, company_id: str, event_types: list[str] | None,
        start_date: date | None, end_date: date | None,
        keywords: list[str] | None, knowledge_cutoff: date | None,
    ) -> dict:
        with sqlite3.connect(self.index_path) as connection:
            total, first_date, last_date = connection.execute(
                "SELECT COUNT(1), MIN(event_date), MAX(event_date) FROM events WHERE company_id = ?",
                (company_id,),
            ).fetchone()
            available_types = [row[0] for row in connection.execute(
                "SELECT DISTINCT event_type FROM events WHERE company_id = ? ORDER BY event_type",
                (company_id,),
            )]
            matched_without_cutoff = self._count_matches(
                connection, company_id=company_id, event_types=event_types,
                start_date=start_date, end_date=end_date, keywords=keywords,
                knowledge_cutoff=None,
            )
        if total == 0:
            reason = "company_not_present_in_event_index"
        elif event_types and not set(event_types).intersection(available_types):
            reason = "event_type_not_available_for_company"
        elif knowledge_cutoff and matched_without_cutoff > 0:
            reason = "all_matches_after_knowledge_cutoff"
        else:
            reason = "date_or_keyword_filters_not_matched"
        return {
            "reason": reason,
            "company_id": company_id,
            "company_event_count": total,
            "available_event_types": available_types,
            "available_date_range": [first_date, last_date] if first_date else None,
            "matched_without_knowledge_cutoff": matched_without_cutoff,
        }

    @staticmethod
    def _count_matches(connection, *, company_id, event_types, start_date, end_date, keywords, knowledge_cutoff) -> int:
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
        return connection.execute(
            f"SELECT COUNT(1) FROM events WHERE {' AND '.join(clauses)}", params
        ).fetchone()[0]


def validate_event_index_snapshot(index_path: Path, normalized_dir: Path) -> list[str]:
    del normalized_dir  # The deployed SQLite index is self-contained.
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
    recorded = manifest.get("source")
    if not isinstance(recorded, dict):
        return [*errors, "Event index manifest has no source object."]
    return errors


def _row_to_event(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=row["event_id"], company_id=row["company_id"], event_type=row["event_type"],
        event_date=date.fromisoformat(row["event_date"]), announcement_date=date.fromisoformat(row["announcement_date"]),
        effective_date=date.fromisoformat(row["effective_date"]) if row["effective_date"] else None,
        date_precision=row["date_precision"], event_stage=row["event_stage"],
        entities=json.loads(row["entities_json"]), agencies=json.loads(row["agencies_json"]),
        reference_ids=json.loads(row["reference_ids_json"]), topic_signature=row["topic_signature"],
        title=row["title"], summary=row["summary"],
        source_document_ids=[row["source_document_id"]], evidence_id=row["evidence_id"], source_path=row["source_path"],
        extraction_method=row["extraction_method"], quality_flags=json.loads(row["quality_flags_json"]),
    )

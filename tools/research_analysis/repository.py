from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from tools.research_analysis.config import RESEARCH_MAPPING_VERSION


class ResearchRepository:
    def __init__(self, index_path: Path):
        self.index_path = index_path

    def available(self) -> bool:
        return self.index_path.is_file()

    def query_claims(
        self, *, company_ids: list[str], start_date: date | None, end_date: date | None,
        institutions: list[str] | None, claim_types: list[str] | None,
        topics: list[str] | None, knowledge_cutoff: date | None, limit: int,
    ) -> list[dict]:
        clauses = [f"c.company_id IN ({','.join('?' for _ in company_ids)})"]
        params: list[object] = list(company_ids)
        if start_date:
            clauses.append("c.publish_date >= ?")
            params.append(start_date.isoformat())
        if end_date:
            clauses.append("c.publish_date <= ?")
            params.append(end_date.isoformat())
        if knowledge_cutoff:
            clauses.append("c.publish_date <= ?")
            params.append(knowledge_cutoff.isoformat())
        if institutions:
            clauses.append(f"c.institution IN ({','.join('?' for _ in institutions)})")
            params.extend(institutions)
        if claim_types:
            clauses.append(f"c.claim_type IN ({','.join('?' for _ in claim_types)})")
            params.extend(claim_types)
        if topics:
            topic_clauses = []
            for topic in topics:
                topic_clauses.append("(c.topic LIKE ? OR c.claim_text LIKE ?)")
                params.extend([f"%{topic}%", f"%{topic}%"])
            clauses.append("(" + " OR ".join(topic_clauses) + ")")
        params.append(limit)
        sql = f"""
            SELECT c.*, v.title AS report_title, v.authors_json, v.rating,
                   v.rating_change, v.target_price, v.report_sub_type
            FROM research_claims c
            JOIN research_views v ON v.view_id = c.view_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.publish_date DESC, c.claim_id ASC
            LIMIT ?
        """
        with sqlite3.connect(self.index_path) as connection:
            connection.row_factory = sqlite3.Row
            return [row_to_dict(row) for row in connection.execute(sql, params).fetchall()]


def row_to_dict(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["authors"] = json.loads(value.pop("authors_json"))
    value["quality_flags"] = json.loads(value.pop("quality_flags_json"))
    return value


def validate_snapshot(index_path: Path, research_path: Path, chunks_path: Path) -> list[str]:
    manifest_path = index_path.with_name("manifest.json")
    if not manifest_path.is_file():
        return [f"Research index manifest not found: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Research index manifest is invalid: {type(exc).__name__}: {exc}"]
    errors = []
    if manifest.get("mapping_version") != RESEARCH_MAPPING_VERSION:
        errors.append(f"mapping version mismatch: index={manifest.get('mapping_version')}, expected={RESEARCH_MAPPING_VERSION}")
    for name, path in (("research_reports", research_path), ("chunks", chunks_path)):
        recorded = (manifest.get("sources") or {}).get(name)
        if not path.is_file():
            errors.append(f"source not found: {path}")
        elif not isinstance(recorded, dict):
            errors.append(f"manifest source missing: {name}")
        else:
            stat = path.stat()
            if recorded.get("size") != stat.st_size or recorded.get("mtime_ns") != stat.st_mtime_ns:
                errors.append(f"source changed: {path}")
    return errors

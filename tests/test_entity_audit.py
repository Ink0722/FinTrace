import json
import sqlite3
from pathlib import Path

from data_pipeline.entity_resolution.audit_unlinked import audit_unlinked_entities


def test_unlinked_audit_ranks_candidates_and_excludes_confirmed_links(tmp_path: Path) -> None:
    index = tmp_path / "entity.sqlite"
    _build_index(index)
    candidates = tmp_path / "candidates.jsonl"
    classifications = tmp_path / "classifications.jsonl"

    report = audit_unlinked_entities(index, candidates, classifications, top_k=2, min_score=0.5)

    candidate_rows = _read_jsonl(candidates)
    classification_rows = _read_jsonl(classifications)
    assert report["unlinked_company_holders"] == 2
    assert {row["holder_entity_id"] for row in classification_rows} == {
        "COMPANY:UNLINKED",
        "COMPANY:VEHICLE",
    }
    unlinked = next(row for row in candidate_rows if row["holder_entity_id"] == "COMPANY:UNLINKED")
    assert unlinked["candidate_company_id"] == "000001.SZ"
    vehicle = next(row for row in classification_rows if row["holder_entity_id"] == "COMPANY:VEHICLE")
    assert vehicle["preliminary_class"] == "institution_or_vehicle"
    assert all(row["holder_entity_id"] != "COMPANY:LINKED" for row in candidate_rows)


def _build_index(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT,
                canonical_name TEXT,
                resolution_status TEXT
            );
            CREATE TABLE entity_aliases (
                entity_id TEXT,
                alias TEXT,
                normalized_alias TEXT,
                alias_type TEXT,
                source TEXT
            );
            CREATE TABLE entity_links (
                source_entity_id TEXT,
                canonical_entity_id TEXT,
                link_type TEXT,
                match_method TEXT,
                confidence REAL,
                review_status TEXT,
                evidence_json TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO entities VALUES (?, ?, ?, ?)",
            [
                ("000001.SZ", "LISTED_COMPANY", "Alpha Technology Co Ltd", "profiled"),
                ("000002.SZ", "LISTED_COMPANY", "Beta Industry Co Ltd", "profiled"),
                ("COMPANY:UNLINKED", "COMPANY_HOLDER", "Alpha Technologies Co Ltd", "source_identifier"),
                ("COMPANY:VEHICLE", "COMPANY_HOLDER", "Example Securities Investment Fund", "source_identifier"),
                ("COMPANY:LINKED", "COMPANY_HOLDER", "Beta Industry Co Ltd", "source_identifier"),
            ],
        )
        connection.executemany(
            "INSERT INTO entity_aliases VALUES (?, ?, ?, ?, ?)",
            [
                ("000001.SZ", "Alpha Technology", "", "LEGAL_NAME", "test"),
                ("000002.SZ", "Beta Industry", "", "LEGAL_NAME", "test"),
                ("COMPANY:UNLINKED", "Alpha Technologies", "", "DISCLOSED_NAME", "test"),
                ("COMPANY:VEHICLE", "Example Securities Investment Fund", "", "DISCLOSED_NAME", "test"),
            ],
        )
        connection.execute(
            "INSERT INTO entity_links VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("COMPANY:LINKED", "000002.SZ", "SAME_LEGAL_ENTITY", "test", 1.0, "confirmed", "{}"),
        )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

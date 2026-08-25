"""SQLite persistence for evaluation batches and case execution state."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from harness.tracing.store import connect


def ensure_schema() -> None:
    with connect() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS evaluation_batches (
                batch_id TEXT PRIMARY KEY,
                dataset_path TEXT NOT NULL,
                dataset_sha256 TEXT NOT NULL,
                evaluation_user_id TEXT NOT NULL,
                knowledge_cutoff TEXT NOT NULL,
                agent_version TEXT,
                status TEXT NOT NULL DEFAULT 'prepared',
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS evaluation_cases (
                batch_id TEXT NOT NULL REFERENCES evaluation_batches(batch_id) ON DELETE CASCADE,
                case_id TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                agent_session_id TEXT NOT NULL,
                expected_turn_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                annotation_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                run_id TEXT,
                started_at TEXT,
                completed_at TEXT,
                error_message TEXT,
                PRIMARY KEY (batch_id, case_id),
                UNIQUE (batch_id, source_session_id, expected_turn_id)
            );
            CREATE INDEX IF NOT EXISTS idx_eval_cases_batch_status
                ON evaluation_cases(batch_id, status);
        """)


def create_batch(batch: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    ensure_schema()
    with connect() as connection:
        connection.execute("""
            INSERT INTO evaluation_batches (
                batch_id, dataset_path, dataset_sha256, evaluation_user_id,
                knowledge_cutoff, agent_version, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?)
        """, (
            batch["batch_id"], batch["dataset_path"], batch["dataset_sha256"],
            batch["evaluation_user_id"], batch["knowledge_cutoff"],
            batch.get("agent_version"), batch["created_at"],
        ))
        connection.executemany("""
            INSERT INTO evaluation_cases (
                batch_id, case_id, source_session_id, agent_session_id,
                expected_turn_id, question, annotation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [(
            batch["batch_id"], case["case_id"], case["source_session_id"],
            case["agent_session_id"], case["expected_turn_id"], case["question"],
            json.dumps(case["annotation"], ensure_ascii=False),
        ) for case in cases])


def get_batch(batch_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM evaluation_batches WHERE batch_id = ?", (batch_id,),
        ).fetchone()
        return dict(row) if row else None


def list_cases(
    batch_id: str, *, session_id: str | None = None, include_failed: bool = False,
    max_cases: int | None = None,
) -> list[dict[str, Any]]:
    statuses = ["pending", "failed"] if include_failed else ["pending"]
    marks = ",".join("?" for _ in statuses)
    clauses = ["batch_id = ?", f"status IN ({marks})"]
    params: list[Any] = [batch_id, *statuses]
    if session_id is not None:
        clauses.append("source_session_id = ?")
        params.append(session_id)
    limit = " LIMIT ?" if max_cases is not None else ""
    if max_cases is not None:
        params.append(max_cases)
    with connect() as connection:
        rows = connection.execute(f"""
            SELECT * FROM evaluation_cases WHERE {' AND '.join(clauses)}
            ORDER BY CAST(source_session_id AS INTEGER), expected_turn_id{limit}
        """, params).fetchall()
        return [dict(row) for row in rows]


def reset_interrupted(batch_id: str) -> int:
    with connect() as connection:
        cursor = connection.execute("""
            UPDATE evaluation_cases SET status = 'pending', error_message = 'Reset after interrupted run'
            WHERE batch_id = ? AND status = 'running'
        """, (batch_id,))
        return cursor.rowcount


def mark_batch_running(batch_id: str) -> None:
    now = datetime.now(UTC).isoformat()
    with connect() as connection:
        connection.execute("""
            UPDATE evaluation_batches SET status = 'running', started_at = COALESCE(started_at, ?),
                completed_at = NULL WHERE batch_id = ?
        """, (now, batch_id))


def prior_turns_completed(
    batch_id: str, source_session_id: str, expected_turn_id: int,
) -> bool:
    with connect() as connection:
        count = connection.execute("""
            SELECT COUNT(*) FROM evaluation_cases
            WHERE batch_id = ? AND source_session_id = ? AND expected_turn_id < ?
              AND status != 'completed'
        """, (batch_id, source_session_id, expected_turn_id)).fetchone()[0]
        return count == 0


def mark_case_running(batch_id: str, case_id: str) -> None:
    with connect() as connection:
        connection.execute("""
            UPDATE evaluation_cases SET status = 'running', attempt_count = attempt_count + 1,
                started_at = ?, completed_at = NULL, error_message = NULL
            WHERE batch_id = ? AND case_id = ?
        """, (datetime.now(UTC).isoformat(), batch_id, case_id))


def mark_case_completed(batch_id: str, case_id: str, run_id: str) -> None:
    with connect() as connection:
        connection.execute("""
            UPDATE evaluation_cases SET status = 'completed', run_id = ?, completed_at = ?,
                error_message = NULL WHERE batch_id = ? AND case_id = ?
        """, (run_id, datetime.now(UTC).isoformat(), batch_id, case_id))


def mark_case_failed(batch_id: str, case_id: str, message: str, run_id: str | None = None) -> None:
    with connect() as connection:
        connection.execute("""
            UPDATE evaluation_cases SET status = 'failed', run_id = ?, completed_at = ?, error_message = ?
            WHERE batch_id = ? AND case_id = ?
        """, (run_id, datetime.now(UTC).isoformat(), message[:2000], batch_id, case_id))


def refresh_batch_status(batch_id: str, *, update: bool = True) -> dict[str, Any]:
    with connect() as connection:
        counts = {row["status"]: row["count"] for row in connection.execute("""
            SELECT status, COUNT(*) AS count FROM evaluation_cases
            WHERE batch_id = ? GROUP BY status
        """, (batch_id,))}
        remaining = sum(counts.get(name, 0) for name in ("pending", "running", "failed"))
        if remaining == 0:
            status = "completed"
        elif counts.get("running"):
            status = "running"
        elif counts.get("failed"):
            status = "failed"
        else:
            status = "partial"
        if update:
            connection.execute(
                "UPDATE evaluation_batches SET status = ?, completed_at = ? WHERE batch_id = ?",
                (status, datetime.now(UTC).isoformat() if status == "completed" else None, batch_id),
            )
        batch = dict(connection.execute(
            "SELECT * FROM evaluation_batches WHERE batch_id = ?", (batch_id,),
        ).fetchone())
    return {**batch, "case_statuses": counts, "total_cases": sum(counts.values())}

from pathlib import Path
import sqlite3

from harness.memory.session_store import SessionStore
from harness.migrate_runtime_db import migrate
from harness.tracing.store import connect, import_payload


def test_legacy_databases_merge_without_losing_rows(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions.sqlite"
    observability = tmp_path / "observability.sqlite3"
    target = tmp_path / "runtime" / "fintrace.sqlite3"

    store = SessionStore(path=sessions)
    from schemas.agent_state import CurrentContext, Message
    store.save(
        "SESSION-OLD", current_context=CurrentContext(company_ids=["600519.SH"]),
        conversation_summary="summary", verified_findings=[],
        recent_messages=[Message(role="user", content="question")], turn_count=1,
    )
    import_payload({
        "run_id": "RUN-OLD", "trace_id": "TRACE-OLD", "session_id": "SESSION-OLD",
        "turn_id": 1, "query": "question", "answer": "answer", "latency_ms": 5,
    }, path=observability)

    result = migrate(sessions, observability, target)

    assert result["status"] == "completed"
    assert result["integrity_check"] == "ok"
    assert result["foreign_key_errors"] == 0
    assert sessions.exists() and observability.exists()
    assert SessionStore(path=target).load("SESSION-OLD")["turn_count"] == 1
    with connect(readonly=True, path=target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_migration_maps_legacy_session_columns_by_name(tmp_path: Path) -> None:
    sessions = tmp_path / "legacy-sessions.sqlite"
    observability = tmp_path / "observability.sqlite3"
    target = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(sessions) as connection:
        connection.executescript("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                current_context TEXT NOT NULL,
                conversation_summary TEXT NOT NULL,
                verified_findings TEXT NOT NULL,
                recent_messages TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                turn_count INTEGER NOT NULL
            );
            INSERT INTO sessions VALUES (
                'LEGACY', '{}', '', '[]', '[]', '2026-08-22T19:00:47+00:00', 7
            );
        """)
    import_payload({
        "run_id": "RUN-LEGACY", "session_id": "LEGACY", "turn_id": 7,
        "query": "question", "answer": "answer",
    }, path=observability)

    migrate(sessions, observability, target)

    loaded = SessionStore(path=target).load("LEGACY")
    assert loaded["turn_count"] == 7
    with connect(readonly=True, path=target) as connection:
        row = connection.execute(
            "SELECT turn_count, updated_at FROM sessions WHERE session_id = 'LEGACY'"
        ).fetchone()
        assert row["turn_count"] == 7
        assert row["updated_at"] == "2026-08-22T19:00:47+00:00"


def test_session_store_repairs_previously_swapped_rows(tmp_path: Path) -> None:
    database = tmp_path / "swapped.sqlite3"
    store = SessionStore(path=database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO sessions(session_id, turn_count, updated_at) VALUES (?, ?, ?)",
            ("SWAPPED", "2026-08-22T19:00:47.299961+00:00", "7"),
        )

    repaired = SessionStore(path=database).load("SWAPPED")

    assert repaired["turn_count"] == 7
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT turn_count, updated_at FROM sessions WHERE session_id = 'SWAPPED'"
        ).fetchone()
        assert row == (7, "2026-08-22T19:00:47.299961+00:00")

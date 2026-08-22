from pathlib import Path

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

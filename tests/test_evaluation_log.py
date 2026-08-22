from app.api.main import run_detail, runs
from harness.graph.workflow import run_agent
from harness.tracing.store import get_run, list_runs


def test_observability_store_records_complete_turns(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "observability.sqlite3"
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(database_path))
    monkeypatch.setenv("FINTRACE_EVAL_LOG_ENABLED", "true")

    first = run_agent("600519.SH 2024年营业收入是多少", session_id="TEST-EVAL-TURNS")
    second = run_agent("这家公司十大股东是谁", session_id="TEST-EVAL-TURNS")

    records = list_runs(session_id="TEST-EVAL-TURNS")
    assert first.turn_id == 1
    assert second.turn_id == 2
    assert sorted(record["turn_id"] for record in records) == [1, 2]
    assert records[0]["session_id"] == "TEST-EVAL-TURNS"
    detail = get_run(records[0]["run_id"])
    assert detail is not None
    assert detail["trace_id"] == records[0]["trace_id"]
    assert isinstance(detail["tool_calls"], list)
    assert isinstance(detail["evidence"], list)
    assert isinstance(detail["workflow_events"], list)
    assert "answer" in detail
    api_list = runs(
        session_id="TEST-EVAL-TURNS", answer_status=None, limit=50, offset=0,
    )
    assert len(api_list["items"]) == 2
    assert run_detail(records[0]["run_id"])["run_id"] == records[0]["run_id"]


def test_observability_log_can_be_disabled(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "disabled.sqlite3"
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(database_path))
    monkeypatch.setenv("FINTRACE_EVAL_LOG_ENABLED", "false")
    run_agent("600519.SH 2024年营业收入是多少", session_id="TEST-EVAL-DISABLED")
    assert database_path.exists()  # Session memory still uses the unified runtime database.
    import sqlite3
    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_runs'"
        ).fetchone()
        assert table is None or connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0

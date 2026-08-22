import json

from harness.tracing.migrate_jsonl import migrate
from harness.tracing.export_jsonl import export
from harness.tracing.store import get_run, list_runs


def test_legacy_logs_are_merged_and_preserved(monkeypatch, tmp_path) -> None:
    traces = tmp_path / "traces.jsonl"
    turns = tmp_path / "turns.jsonl"
    database = tmp_path / "observability.sqlite3"
    trace_rows = [
        {"created_at": "2026-01-01T00:00:00+00:00", "run_id": "RUN-1", "trace_id": "TRACE-1",
         "session_id": "S-1", "turn_id": 1, "user_query": "q1", "planner_actions": [],
         "tool_results_summary": [], "executed_nodes": ["resolve_request"]},
        {"created_at": "2025-01-01T00:00:00+00:00", "session_id": "OLD", "user_query": "legacy"},
    ]
    turn_rows = [{"run_id": "RUN-1", "trace_id": "TRACE-1", "session_id": "S-1",
                  "turn_id": 1, "query": "q1", "answer": "answer", "latency_ms": 10}]
    traces.write_text("\n".join(json.dumps(item) for item in trace_rows) + "\n", encoding="utf-8")
    turns.write_text("\n".join(json.dumps(item) for item in turn_rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(database))

    result = migrate(traces_path=traces, turns_path=turns, database_path=database)

    assert result["imported_runs"] == 2
    assert result["matched_rows"] == 1
    assert result["legacy_trace_rows"] == 1
    records = list_runs(limit=10)
    assert len(records) == 2
    assert get_run("RUN-1")["answer"] == "answer"
    assert any(item["run_id"].startswith("LEGACY-") for item in records)
    output = tmp_path / "export.jsonl"
    assert export(output) == 2
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2

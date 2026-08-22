import json

from harness.graph.workflow import run_agent


def test_evaluation_log_records_turns_and_links_trace(monkeypatch, tmp_path) -> None:
    eval_path = tmp_path / "agent_turns.jsonl"
    trace_path = tmp_path / "traces.jsonl"
    monkeypatch.setenv("FINTRACE_EVAL_LOG_PATH", str(eval_path))
    monkeypatch.setenv("TRACE_PATH", str(trace_path))
    monkeypatch.setenv("FINTRACE_EVAL_LOG_ENABLED", "true")

    first = run_agent("600519.SH 2024年营业收入是多少", session_id="TEST-EVAL-TURNS")
    second = run_agent("这家公司十大股东是谁", session_id="TEST-EVAL-TURNS")

    records = [json.loads(line) for line in eval_path.read_text(encoding="utf-8").splitlines()]
    traces = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert first.turn_id == 1
    assert second.turn_id == 2
    assert [record["turn_id"] for record in records] == [1, 2]
    assert records[0]["session_id"] == "TEST-EVAL-TURNS"
    assert records[0]["run_id"] == traces[0]["run_id"]
    assert records[0]["trace_id"] == traces[0]["trace_id"]
    assert isinstance(records[0]["tool_calls"], list)
    assert "answer" in records[0]


def test_evaluation_log_can_be_disabled(monkeypatch, tmp_path) -> None:
    eval_path = tmp_path / "disabled.jsonl"
    monkeypatch.setenv("FINTRACE_EVAL_LOG_PATH", str(eval_path))
    monkeypatch.setenv("FINTRACE_EVAL_LOG_ENABLED", "false")
    run_agent("600519.SH 2024年营业收入是多少", session_id="TEST-EVAL-DISABLED")
    assert not eval_path.exists()

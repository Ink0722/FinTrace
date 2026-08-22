from harness.graph.workflow import stream_agent


def test_stream_agent_emits_graph_events_and_completion(monkeypatch) -> None:
    monkeypatch.setenv("FINTRACE_EVAL_LOG_ENABLED", "true")
    events: list[tuple[str, dict]] = []

    state = stream_agent(
        "600519.SH 2024年营业收入是多少",
        "TEST-STREAM-WORKFLOW",
        lambda event, payload: events.append((event, payload)),
    )

    names = [event for event, _ in events]
    assert names[0] == "turn.started"
    assert "request.resolved" in names
    assert "route.selected" in names
    assert "tool.started" in names
    assert "tool.completed" in names
    assert names[-1] == "turn.completed"
    assert events[-1][1]["state"]["session_id"] == "TEST-STREAM-WORKFLOW"
    assert state.session_id == "TEST-STREAM-WORKFLOW"

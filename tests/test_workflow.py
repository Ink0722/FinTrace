from harness.graph.workflow import run_agent


def test_workflow_direct_metric_query() -> None:
    state = run_agent("600519.SH 2024年营业收入是多少", session_id="TEST-WF-1")
    assert state.routing_mode == "direct"
    assert state.tool_call_history and state.tool_call_history[0].tool_name == "financial_analysis"
    assert state.tool_call_history[0].status == "success"
    assert state.evidence_ledger


def test_workflow_investigation_runs_bounded_queue() -> None:
    state = run_agent("结合公告分析600519.SH的存货风险", session_id="TEST-WF-2")
    assert state.routing_mode == "investigation"
    assert 1 <= state.total_tool_calls <= state.max_total_tool_calls
    assert state.tool_call_history

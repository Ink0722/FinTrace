from harness.graph.workflow import run_agent


def test_workflow_runs_with_placeholder_tool() -> None:
    state = run_agent("分析一下示例公司的财务风险", session_id="TEST-SESSION")
    assert state.execution_plan is not None
    assert len(state.tool_results) >= 1

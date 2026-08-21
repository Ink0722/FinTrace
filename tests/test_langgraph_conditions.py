from schemas.agent_state import AgentState
from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolResult
from harness.graph.workflow import run_agent


def test_missing_company_clarifies_instead_of_guessing() -> None:
    state = run_agent("净利润是多少", session_id="TEST-GATE-1")
    assert state.answer_status == "clarification_required"
    assert state.workflow_status == "clarification_required"
    assert "上市公司" in state.final_answer
    assert state.tool_call_history == []
    assert "build_clarification" in state.executed_nodes


def test_realtime_request_refused() -> None:
    state = run_agent("600519.SH 现在股价多少", session_id="TEST-GATE-2")
    assert state.answer_status == "unsupported"
    assert "行情" in state.final_answer
    assert state.tool_call_history == []


def test_direct_path_skips_planner() -> None:
    state = run_agent("600519.SH 2024年营业收入是多少", session_id="TEST-GATE-3")
    assert state.routing_mode == "direct"
    assert "plan_next_action" not in state.executed_nodes
    assert state.total_tool_calls == 1


def test_investigation_loop_respects_budget(monkeypatch) -> None:
    def always_failing_tool(call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name,
            status=ToolStatus.FAILED,
            error=ToolError(error_type=ErrorType.DATA_NOT_AVAILABLE, message="no data", retryable=False),
        )

    monkeypatch.setattr("harness.graph.nodes.execute_tool", always_failing_tool)
    state = run_agent("结合公告分析600519.SH的存货风险", session_id="TEST-GATE-4")
    assert state.routing_mode == "investigation"
    assert state.total_tool_calls <= state.max_total_tool_calls
    assert state.step_count <= state.max_steps + 1
    assert state.termination_reason in {"non_retryable_tool_failure", "no_new_evidence", "budget_exhausted", None}
    # Without a configured LLM the turn ends in the structured-error branch.
    assert state.answer_status in {"insufficient_evidence", "failed"}


def test_pronoun_inherits_from_session_context() -> None:
    first = run_agent("600519.SH 2024年营业收入是多少", session_id="TEST-GATE-5")
    assert first.parsed_request is not None and first.parsed_request.entities == ["600519.SH"]
    second = run_agent("这家公司十大股东是谁", session_id="TEST-GATE-5")
    assert second.parsed_request is not None
    assert second.parsed_request.entities == ["600519.SH"]
    assert second.tool_call_history and second.tool_call_history[0].tool_name == "ownership_analysis"


def test_state_supports_new_pipeline_fields() -> None:
    state = AgentState.model_validate(
        {
            "session_id": "S",
            "user_request": {"raw_query": "q"},
            "routing_mode": "investigation",
            "step_count": 2,
            "max_steps": 5,
            "answer_status": None,
        }
    )
    assert state.routing_mode == "investigation"
    assert state.step_count == 2

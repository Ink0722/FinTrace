from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ExecutionPlan, ToolCall
from schemas.tool_results import ToolError, ToolResult
from harness.graph.workflow import run_agent
from harness.guards.validation import validate_plan


def test_plan_invalid_routes_to_structured_error(monkeypatch) -> None:
    def invalid_plan(query: str) -> ExecutionPlan:
        call = ToolCall(
            tool_call_id="CALL-001",
            tool_name=ToolName.DOCUMENT_SEARCH,
            arguments={"query": query},
            reason="duplicate for test",
        )
        return ExecutionPlan(plan_id="PLAN-BAD", user_intent="document_search", tool_calls=[call, call])

    monkeypatch.setattr("harness.graph.nodes.route_query", invalid_plan)
    state = run_agent("test invalid plan", session_id="TEST-PLAN-INVALID")
    assert state.workflow_status == "plan_invalid"
    assert "PLAN_VALIDATION_FAILED" in state.final_answer
    assert "execute_tools" not in state.executed_nodes


def test_retryable_tool_failure_retries_once(monkeypatch) -> None:
    calls = {"count": 0}

    def flaky_tool(call: ToolCall) -> ToolResult:
        calls["count"] += 1
        if calls["count"] == 1:
            return ToolResult(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                status=ToolStatus.FAILED,
                error=ToolError(
                    error_type=ErrorType.TEMPORARY_DATABASE_ERROR,
                    message="temporary failure",
                    retryable=True,
                ),
            )
        return ToolResult(tool_call_id=call.tool_call_id, tool_name=call.tool_name, status=ToolStatus.SUCCESS)

    monkeypatch.setattr("harness.graph.nodes.execute_tool", flaky_tool)
    state = run_agent("test retry", session_id="TEST-RETRY")
    assert calls["count"] == 2
    assert state.retry_count == 1
    assert "retry_tools" in state.executed_nodes
    assert state.tool_results[-1].status == ToolStatus.SUCCESS


def test_evidence_insufficient_adds_warning(monkeypatch) -> None:
    def empty_document_tool(call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=ToolName.DOCUMENT_SEARCH,
            status=ToolStatus.SUCCESS,
            data={"hits": []},
        )

    monkeypatch.setattr("harness.graph.nodes.execute_tool", empty_document_tool)
    state = run_agent("test evidence", session_id="TEST-EVIDENCE")
    assert "evidence_warning" in state.executed_nodes
    assert any("evidence_id" in warning or "document_search" in warning for warning in state.warnings)


def test_plan_validation_rejects_deprecated_scalar_collection_parameters() -> None:
    plan = ExecutionPlan(
        plan_id="PLAN-OLD-ARGS",
        user_intent="financial_analysis",
        tool_calls=[
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
                arguments={"company_id": "000001.SZ", "report_period": "2022-12-31"},
                reason="test deprecated arguments",
            )
        ],
    )
    errors = validate_plan(plan)
    assert "deprecated scalar parameter: financial_risk_analysis.company_id" in errors
    assert "deprecated scalar parameter: financial_risk_analysis.report_period" in errors

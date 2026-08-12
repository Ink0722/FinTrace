from harness.answering import build_structured_error_answer, generate_answer_with_status
from harness.evidence.ledger import merge_evidence
from harness.guards.validation import validate_plan, validate_tool_result
from harness.routing.router import route_query
from schemas.agent_state import AgentState
from schemas.tool_results import ToolResult
from tools.registry import execute_tool


def route_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("route")
    query = state.user_request.normalized_query or state.user_request.raw_query
    state.execution_plan = route_query(query)
    state.user_request.intent = state.execution_plan.user_intent
    return state


def validate_plan_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("validate_plan")
    errors = validate_plan(state.execution_plan) if state.execution_plan else ["missing execution plan"]
    state.validation_results.append({"stage": "validate_plan", "errors": errors})
    return state


def plan_error_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("plan_error")
    state.workflow_status = "plan_invalid"
    for error in _latest_errors(state, "validate_plan"):
        state.errors.append(
            {
                "stage": "validate_plan",
                "error_type": "PLAN_VALIDATION_FAILED",
                "message": error,
                "retryable": False,
            }
        )
    return state


def execute_tools_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("execute_tools")
    if not state.execution_plan or _latest_errors(state, "validate_plan"):
        return state
    for call in state.execution_plan.tool_calls:
        result: ToolResult = execute_tool(call)
        state.tool_results.append(result)
        state.evidence_ledger = merge_evidence(state.evidence_ledger, result.evidence)
    return state


def retry_tools_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("retry_tools")
    if not state.execution_plan:
        return state
    failed_retryable_ids = {
        result.tool_call_id
        for result in state.tool_results
        if result.status.value != "success" and result.error and result.error.retryable
    }
    if not failed_retryable_ids:
        return state

    state.retry_count += 1
    for call in state.execution_plan.tool_calls:
        if call.tool_call_id not in failed_retryable_ids:
            continue
        result: ToolResult = execute_tool(call)
        state.tool_results = [old for old in state.tool_results if old.tool_call_id != call.tool_call_id]
        state.tool_results.append(result)
        state.evidence_ledger = merge_evidence(state.evidence_ledger, result.evidence)
    return state


def validate_tool_results_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("validate_tool_results")
    for result in state.tool_results:
        errors = validate_tool_result(result)
        if result.status.value != "success":
            message = result.error.message if result.error else "tool returned non-success status"
            errors.append(message)
            state.errors.append(
                {
                    "stage": "validate_tool_results",
                    "tool_call_id": result.tool_call_id,
                    "error_type": result.error.error_type.value if result.error else "TOOL_FAILED",
                    "message": message,
                    "retryable": result.error.retryable if result.error else False,
                }
            )
        state.validation_results.append({"stage": "validate_tool_result", "tool_call_id": result.tool_call_id, "errors": errors})
    return state


def tool_error_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("tool_error")
    state.workflow_status = "tool_failed"
    return state


def check_evidence_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("check_evidence")
    warnings: list[str] = []
    if state.tool_results and not state.evidence_ledger:
        warnings.append("工具已执行，但没有返回任何 evidence_id。")
    for result in state.tool_results:
        if result.tool_name.value == "document_search" and not result.data.get("hits"):
            warnings.append("document_search 未召回原文片段。")
        if result.tool_name.value == "ownership_penetration":
            for path in result.data.get("paths", []):
                for hop in path.get("hops", []):
                    if not hop.get("evidence_id"):
                        warnings.append("ownership_penetration 存在缺少 evidence_id 的路径跳。")
        if result.tool_name.value == "financial_risk_analysis":
            for signal in result.data.get("risk_signals", []):
                if signal.get("triggered") and not signal.get("evidence_ids"):
                    warnings.append(f"financial_risk_analysis 规则 {signal.get('rule_id')} 触发但缺少 evidence_ids。")
    for warning in warnings:
        if warning not in state.warnings:
            state.warnings.append(warning)
    state.next_action = "evidence_warning" if warnings else None
    return state


def evidence_warning_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("evidence_warning")
    if state.workflow_status == "running":
        state.workflow_status = "evidence_warning"
    return state


def generate_answer_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("generate_answer")
    answer, status, error = generate_answer_with_status(state)
    state.final_answer = answer
    state.llm_status = status
    if error:
        state.errors.append(error)
        state.workflow_status = "llm_failed"
    return state


def structured_error_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("structured_error")
    state.final_answer = build_structured_error_answer(state)
    if state.workflow_status == "running":
        state.workflow_status = "failed"
    return state


def _latest_errors(state: AgentState, stage: str) -> list[str]:
    for item in reversed(state.validation_results):
        if item.get("stage") == stage:
            return item.get("errors", [])
    return []

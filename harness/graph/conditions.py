from schemas.agent_state import AgentState


def after_plan_validation(state: AgentState) -> str:
    if _latest_errors(state, "validate_plan"):
        return "plan_error"
    return "execute_tools"


def after_tool_validation(state: AgentState) -> str:
    failed_results = [result for result in state.tool_results if result.status.value != "success"]
    if not failed_results:
        return "check_evidence"

    retryable = [result for result in failed_results if result.error and result.error.retryable]
    if retryable and state.retry_count < 1:
        return "retry_tools"
    return "tool_error"


def after_evidence_check(state: AgentState) -> str:
    if state.next_action == "evidence_warning":
        return "evidence_warning"
    return "generate_answer"


def after_answer_generation(state: AgentState) -> str:
    if state.llm_status == "failed":
        return "structured_error"
    return "__end__"


def _latest_errors(state: AgentState, stage: str) -> list[str]:
    for item in reversed(state.validation_results):
        if item.get("stage") == stage:
            return item.get("errors", [])
    return []

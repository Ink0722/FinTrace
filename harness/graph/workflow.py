import sys
import time

from langgraph.graph import END, START, StateGraph

from harness.graph.conditions import after_answer_generation, after_evidence_check, after_plan_validation, after_tool_validation
from harness.graph.nodes import (
    check_evidence_node,
    evidence_warning_node,
    execute_tools_node,
    generate_answer_node,
    plan_error_node,
    route_node,
    retry_tools_node,
    structured_error_node,
    tool_error_node,
    validate_plan_node,
    validate_tool_results_node,
)
from harness.tracing.jsonl import write_trace
from schemas.agent_state import AgentState, Message, UserRequest


def build_graph():
    """Compile the auditable Agent control flow used by CLI and FastAPI."""
    graph = StateGraph(AgentState)
    graph.add_node("route", route_node)
    graph.add_node("validate_plan", validate_plan_node)
    graph.add_node("plan_error", plan_error_node)
    graph.add_node("execute_tools", execute_tools_node)
    graph.add_node("retry_tools", retry_tools_node)
    graph.add_node("validate_tool_results", validate_tool_results_node)
    graph.add_node("tool_error", tool_error_node)
    graph.add_node("check_evidence", check_evidence_node)
    graph.add_node("evidence_warning", evidence_warning_node)
    graph.add_node("generate_answer", generate_answer_node)
    graph.add_node("structured_error", structured_error_node)

    graph.add_edge(START, "route")
    graph.add_edge("route", "validate_plan")
    graph.add_conditional_edges(
        "validate_plan",
        after_plan_validation,
        {"execute_tools": "execute_tools", "plan_error": "plan_error"},
    )
    graph.add_edge("plan_error", "structured_error")
    graph.add_edge("execute_tools", "validate_tool_results")
    graph.add_conditional_edges(
        "validate_tool_results",
        after_tool_validation,
        {"check_evidence": "check_evidence", "retry_tools": "retry_tools", "tool_error": "tool_error"},
    )
    graph.add_edge("retry_tools", "validate_tool_results")
    graph.add_edge("tool_error", "structured_error")
    graph.add_conditional_edges(
        "check_evidence",
        after_evidence_check,
        {"evidence_warning": "evidence_warning", "generate_answer": "generate_answer"},
    )
    graph.add_edge("evidence_warning", "generate_answer")
    graph.add_conditional_edges(
        "generate_answer",
        after_answer_generation,
        {"structured_error": "structured_error", "__end__": END},
    )
    graph.add_edge("structured_error", END)
    return graph.compile()


COMPILED_GRAPH = build_graph()


def run_agent(query: str, session_id: str = "SESSION-001") -> AgentState:
    """Run one user query through LangGraph and persist an execution trace."""
    started = time.perf_counter()
    state = AgentState(
        session_id=session_id,
        messages=[Message(role="user", content=query)],
        user_request=UserRequest(raw_query=query, normalized_query=query),
    )
    state = AgentState.model_validate(COMPILED_GRAPH.invoke(state))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    write_trace(
        {
            "session_id": session_id,
            "user_query": query,
            "plan": state.execution_plan.model_dump() if state.execution_plan else None,
            "tool_results_summary": [result.model_dump() for result in state.tool_results],
            "evidence_ids": [e.evidence_id for e in state.evidence_ledger],
            "validation": state.validation_results,
            "final_answer": state.final_answer,
            "workflow_status": state.workflow_status,
            "llm_status": state.llm_status,
            "errors": state.errors,
            "warnings": state.warnings,
            "executed_nodes": state.executed_nodes,
            "latency_ms": elapsed_ms,
        }
    )
    return state


if __name__ == "__main__":
    user_query = " ".join(sys.argv[1:]) or "分析一下示例公司的财务风险"
    print(run_agent(user_query).model_dump_json(indent=2))

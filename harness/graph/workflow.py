"""Agent workflow: Direct Fast Path + Evidence-driven Bounded Investigation (docs/13 §21)."""
import os
import sys
import time
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from harness.graph.conditions import (
    after_answer_generation,
    after_plan_next_action,
    after_pre_answerability,
    after_review_evidence,
    after_route_mode,
    after_validate_action,
)
from harness.graph.nodes import (
    build_clarification_node,
    build_refusal_node,
    check_pre_answerability_node,
    execute_one_tool_node,
    generate_answer_node,
    load_session_node,
    merge_evidence_node,
    persist_session_node,
    plan_next_action_node,
    repair_action_node,
    resolve_request_node,
    review_evidence_node,
    route_mode_node,
    structured_error_node,
    validate_action_node,
    validate_tool_result_node,
)
from harness.tracing.jsonl import write_trace
from schemas.agent_state import AgentState, Message, UserRequest


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("load_session", load_session_node)
    graph.add_node("resolve_request", resolve_request_node)
    graph.add_node("check_pre_answerability", check_pre_answerability_node)
    graph.add_node("build_clarification", build_clarification_node)
    graph.add_node("build_refusal", build_refusal_node)
    graph.add_node("route_mode", route_mode_node)
    graph.add_node("plan_next_action", plan_next_action_node)
    graph.add_node("validate_action", validate_action_node)
    graph.add_node("repair_action", repair_action_node)
    graph.add_node("execute_one_tool", execute_one_tool_node)
    graph.add_node("validate_tool_result", validate_tool_result_node)
    graph.add_node("merge_evidence", merge_evidence_node)
    graph.add_node("review_evidence", review_evidence_node)
    graph.add_node("generate_answer", generate_answer_node)
    graph.add_node("persist_session", persist_session_node)
    graph.add_node("structured_error", structured_error_node)

    graph.add_edge(START, "load_session")
    graph.add_edge("load_session", "resolve_request")
    graph.add_edge("resolve_request", "check_pre_answerability")
    graph.add_conditional_edges(
        "check_pre_answerability",
        after_pre_answerability,
        {"clarify": "build_clarification", "refuse": "build_refusal", "route": "route_mode"},
    )
    graph.add_edge("build_clarification", "persist_session")
    graph.add_edge("build_refusal", "persist_session")
    graph.add_conditional_edges(
        "route_mode",
        after_route_mode,
        {"direct": "validate_action", "investigation": "plan_next_action"},
    )
    graph.add_conditional_edges(
        "plan_next_action",
        after_plan_next_action,
        {
            "validate": "validate_action",
            "clarify": "build_clarification",
            "refuse": "build_refusal",
            "answer": "generate_answer",
        },
    )
    graph.add_conditional_edges(
        "validate_action",
        after_validate_action,
        {"execute": "execute_one_tool", "repair": "repair_action", "replan": "plan_next_action"},
    )
    graph.add_edge("repair_action", "validate_action")
    graph.add_edge("execute_one_tool", "validate_tool_result")
    graph.add_edge("validate_tool_result", "merge_evidence")
    graph.add_edge("merge_evidence", "review_evidence")
    graph.add_conditional_edges(
        "review_evidence",
        after_review_evidence,
        {"plan": "plan_next_action", "answer": "generate_answer"},
    )
    graph.add_conditional_edges(
        "generate_answer",
        after_answer_generation,
        {"error": "structured_error", "end": "persist_session"},
    )
    graph.add_edge("persist_session", END)
    graph.add_edge("structured_error", "persist_session")
    return graph.compile()


COMPILED_GRAPH = build_graph()


def knowledge_cutoff_from_env() -> str | None:
    raw = (os.getenv("FINTRACE_KNOWLEDGE_CUTOFF") or "").strip()
    return raw or None


def run_agent(query: str, session_id: str = "SESSION-001") -> AgentState:
    """Run one user query through the Direct/Investigation graph and persist a trace."""
    started = time.perf_counter()
    state = AgentState(
        session_id=session_id,
        messages=[Message(role="user", content=query)],
        user_request=UserRequest(raw_query=query, normalized_query=query),
        knowledge_cutoff=knowledge_cutoff_from_env(),
    )
    state = AgentState.model_validate(COMPILED_GRAPH.invoke(state))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    write_trace(
        {
            "session_id": session_id,
            "user_query": query,
            "parsed_request": state.parsed_request.model_dump() if state.parsed_request else None,
            "pre_answerability": state.pre_answerability.model_dump() if state.pre_answerability else None,
            "routing_mode": state.routing_mode,
            "candidate_capabilities": state.candidate_capabilities,
            "planner_actions": [entry.model_dump() for entry in state.tool_call_history],
            "tool_results_summary": [result.model_dump() for result in state.tool_results],
            "evidence_ids": [e.evidence_id for e in state.evidence_ledger],
            "evidence_gaps": [gap.model_dump() for gap in state.evidence_gaps],
            "repairs": [item for item in state.validation_results if item.get("stage") == "validate_action" and item.get("errors")],
            "failed_actions": state.failed_actions,
            "llm_calls": [record.model_dump() for record in state.llm_calls],
            "validation": state.validation_results,
            "final_answer": state.final_answer,
            "answer_status": state.answer_status,
            "termination_reason": state.termination_reason,
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

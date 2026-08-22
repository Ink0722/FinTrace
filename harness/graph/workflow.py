"""Agent workflow: Direct Fast Path + Evidence-driven Bounded Investigation (docs/13 §21)."""
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4
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
from harness.tracing.store import persist_run
from harness.streaming import reset_emitter, set_emitter
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
    _persist_state(state, started=started)
    return state


def stream_agent(
    query: str, session_id: str, emit: Callable[[str, dict[str, Any]], None],
) -> AgentState:
    """Execute the graph while emitting auditable node and answer-token events."""
    started = time.perf_counter()
    state = AgentState(
        session_id=session_id,
        messages=[Message(role="user", content=query)],
        user_request=UserRequest(raw_query=query, normalized_query=query),
        knowledge_cutoff=knowledge_cutoff_from_env(),
    )
    run_id = f"RUN-{uuid4().hex.upper()}"
    trace_id = f"TRACE-{uuid4().hex.upper()}"
    emit("turn.started", {"run_id": run_id, "trace_id": trace_id, "session_id": session_id})
    token = set_emitter(emit)
    evidence_seen: set[str] = set()
    try:
        for update in COMPILED_GRAPH.stream(state, stream_mode="updates"):
            for node_name, raw_update in update.items():
                state = _merge_stream_update(state, raw_update)
                _emit_node_event(emit, node_name, state, evidence_seen)
    finally:
        reset_emitter(token)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _persist_state(state, started=started, run_id=run_id, trace_id=trace_id, elapsed_ms=elapsed_ms)
    emit("turn.completed", {
        "run_id": run_id, "trace_id": trace_id, "latency_ms": elapsed_ms,
        "state": state.model_dump(mode="json"),
    })
    return state


def _merge_stream_update(state: AgentState, update: Any) -> AgentState:
    if isinstance(update, AgentState):
        return update
    if isinstance(update, dict):
        return AgentState.model_validate({**state.model_dump(), **update})
    return state


def _emit_node_event(
    emit: Callable[[str, dict[str, Any]], None], node: str, state: AgentState,
    evidence_seen: set[str],
) -> None:
    if node == "resolve_request" and state.parsed_request:
        emit("request.resolved", state.parsed_request.model_dump(mode="json"))
    elif node == "route_mode":
        emit("route.selected", {"mode": state.routing_mode, "capabilities": state.candidate_capabilities})
    elif node == "validate_action" and state.current_action and state.current_action.action == "call_tool":
        emit("tool.started", state.current_action.model_dump(mode="json"))
    elif node == "execute_one_tool" and state.tool_results:
        emit("tool.completed", {
            "call": state.tool_call_history[-1].model_dump(mode="json") if state.tool_call_history else {},
            "result": state.tool_results[-1].model_dump(mode="json"),
        })
    elif node == "merge_evidence":
        added = [item for item in state.evidence_ledger if item.evidence_id not in evidence_seen]
        evidence_seen.update(item.evidence_id for item in added)
        if added:
            emit("evidence.added", {"items": [item.model_dump(mode="json") for item in added]})
    elif node == "generate_answer":
        emit("answer.completed", {"status": state.answer_status})
    emit("workflow.node", {"node": node, "status": "completed"})


def _persist_state(
    state: AgentState, *, started: float, run_id: str | None = None,
    trace_id: str | None = None, elapsed_ms: int | None = None,
) -> None:
    elapsed_ms = elapsed_ms if elapsed_ms is not None else int((time.perf_counter() - started) * 1000)
    run_id = run_id or f"RUN-{uuid4().hex.upper()}"
    trace_id = trace_id or f"TRACE-{uuid4().hex.upper()}"
    try:
        persist_run(state, run_id=run_id, trace_id=trace_id, latency_ms=elapsed_ms)
    except (OSError, ValueError, sqlite3.Error) as exc:
        state.warnings.append(f"Observability log write failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    user_query = " ".join(sys.argv[1:]) or "分析一下示例公司的财务风险"
    print(run_agent(user_query).model_dump_json(indent=2))

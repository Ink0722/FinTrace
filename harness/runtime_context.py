"""Runtime context builders: structured payloads fed to each prompt skill (docs/11 §五)."""
from __future__ import annotations

from schemas.agent_state import AgentState
from schemas.evidence import Evidence

from harness.routing.capability_registry import get_capability
from tools.argument_validation import tool_argument_schema

MAX_EVIDENCE_ITEMS = 12
MAX_DOCUMENT_CHUNK_ITEMS = 4
MAX_CHUNK_TEXT_CHARS = 500
CAPABILITY_GAP_MESSAGES = {
    "realtime_market_data_unavailable": "当前数据不包含实时或历史行情，无法评价股价表现。",
    "deterministic_investment_recommendation_unavailable": "系统不提供确定性的买入、卖出或涨跌预测结论。",
    "user_account_operation_unavailable": "系统不连接个人账户，无法办理开户、权限或账户资料操作。",
}


def evidence_summary(state: AgentState, limit: int = MAX_EVIDENCE_ITEMS) -> list[dict]:
    summary: list[dict] = []
    for item in _select_evidence_items(state.evidence_ledger, limit=limit):
        fact = dict(item.fact)
        text = fact.get("text")
        if isinstance(text, str) and len(text) > MAX_CHUNK_TEXT_CHARS:
            fact["text"] = text[:MAX_CHUNK_TEXT_CHARS] + "…"
        summary.append(
            {
                "evidence_id": item.evidence_id,
                "evidence_type": item.evidence_type,
                "company_id": item.source.company_id,
                "document_type": item.source.document_type,
                "support_level": item.support_level,
                "fact": fact,
            }
        )
    return summary


def _select_evidence_items(items: list[Evidence], *, limit: int) -> list[Evidence]:
    """Reserve runtime context for structured evidence while bounding document text."""
    document_items = [item for item in items if item.evidence_type == "document_chunk"]
    structured_items = [item for item in items if item.evidence_type != "document_chunk"]
    selected_documents = document_items[:MAX_DOCUMENT_CHUNK_ITEMS]
    selected_structured = structured_items[: max(0, limit - len(selected_documents))]
    selected_ids = {id(item) for item in [*selected_structured, *selected_documents]}
    return [item for item in items if id(item) in selected_ids][:limit]


def capability_descriptors(names: list[str]) -> list[dict]:
    descriptors = []
    for name in names:
        capability = get_capability(name)
        if capability is not None:
            descriptors.append(capability.model_dump())
    return descriptors


def budget(state: AgentState) -> dict:
    return {
        "steps": f"{state.step_count}/{state.max_steps}",
        "tool_calls": f"{state.total_tool_calls}/{state.max_total_tool_calls}",
        "no_new_evidence_rounds": state.no_new_evidence_rounds,
    }


def resolved_request_context(state: AgentState) -> dict:
    """Return session context constrained to entities resolved for this turn."""
    context = state.current_context.model_dump()
    if state.parsed_request is None:
        return context

    company_names = dict(zip(state.current_context.company_ids, state.current_context.company_names))
    context["company_ids"] = list(state.parsed_request.entities)
    context["company_names"] = [
        company_names[company_id]
        for company_id in state.parsed_request.entities
        if company_id in company_names
    ]
    return context


def planner_runtime(state: AgentState) -> dict:
    return {
        "raw_query": state.user_request.raw_query,
        "parsed_request": state.parsed_request.model_dump() if state.parsed_request else None,
        "resolved_context": resolved_request_context(state),
        "conversation_summary": state.conversation_summary,
        "memory_hints": state.relevant_memories,
        "candidate_capabilities": capability_descriptors(state.candidate_capabilities),
        "current_evidence": evidence_summary(state),
        "evidence_gaps": [gap.model_dump() for gap in state.evidence_gaps],
        "tool_call_history": [entry.model_dump() for entry in state.tool_call_history],
        "remaining_budget": budget(state),
        "knowledge_cutoff": state.knowledge_cutoff,
    }


def reviewer_runtime(state: AgentState) -> dict:
    return {
        "raw_query": state.user_request.raw_query,
        "parsed_request": state.parsed_request.model_dump() if state.parsed_request else None,
        "resolved_context": resolved_request_context(state),
        "evidence_ledger": evidence_summary(state),
        "tool_call_history": [entry.model_dump() for entry in state.tool_call_history],
        "available_capabilities": capability_descriptors(state.candidate_capabilities),
    }


def repair_runtime(state: AgentState, errors: list[str]) -> dict:
    action = state.current_action
    capability = get_capability(action.capability) if action else None
    return {
        "raw_query": state.user_request.raw_query,
        "parsed_request": state.parsed_request.model_dump() if state.parsed_request else None,
        "resolved_context": resolved_request_context(state),
        "failed_action": action.model_dump() if action else None,
        "validator_error": errors,
        "capability_definition": capability.model_dump() if capability else None,
        "tool_schema": tool_argument_schema(action.tool_name, action.operation) if action else {},
        "repair_budget": {"used": state.repair_count, "max": 1},
    }


def final_answer_runtime(state: AgentState) -> dict:
    gaps = [gap.description for gap in state.evidence_gaps]
    capability_gaps = [
        CAPABILITY_GAP_MESSAGES.get(gap, gap)
        for gap in (state.parsed_request.capability_gaps if state.parsed_request else [])
    ]
    clarification = (
        state.pre_answerability.clarification_question
        if state.pre_answerability and state.pre_answerability.status == "routeable_with_gaps"
        else None
    )
    return {
        "raw_query": state.user_request.raw_query,
        "resolved_context": resolved_request_context(state),
        "answer_status": state.answer_status,
        "supporting_evidence": evidence_summary(state),
        "limitations": list(dict.fromkeys([*capability_gaps, *gaps, *state.warnings]))[:10],
        "clarification_question": clarification,
    }

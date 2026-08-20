"""Runtime context builders: structured payloads fed to each prompt skill (docs/11 §五)."""
from __future__ import annotations

from schemas.agent_state import AgentState
from schemas.evidence import Evidence

from harness.routing.capability_registry import get_capability

MAX_EVIDENCE_ITEMS = 12
MAX_CHUNK_TEXT_CHARS = 500


def evidence_summary(state: AgentState, limit: int = MAX_EVIDENCE_ITEMS) -> list[dict]:
    summary: list[dict] = []
    for item in state.evidence_ledger[:limit]:
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
                "fact": fact,
            }
        )
    return summary


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


def planner_runtime(state: AgentState) -> dict:
    return {
        "raw_query": state.user_request.raw_query,
        "parsed_request": state.parsed_request.model_dump() if state.parsed_request else None,
        "resolved_context": state.current_context.model_dump(),
        "candidate_capabilities": capability_descriptors(state.candidate_capabilities),
        "current_evidence": evidence_summary(state),
        "verified_claims": [],
        "evidence_gaps": [gap.model_dump() for gap in state.evidence_gaps],
        "tool_call_history": [entry.model_dump() for entry in state.tool_call_history],
        "remaining_budget": budget(state),
        "knowledge_cutoff": state.knowledge_cutoff,
    }


def reviewer_runtime(state: AgentState) -> dict:
    return {
        "raw_query": state.user_request.raw_query,
        "parsed_request": state.parsed_request.model_dump() if state.parsed_request else None,
        "resolved_context": state.current_context.model_dump(),
        "verified_claims": [],
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
        "resolved_context": state.current_context.model_dump(),
        "failed_action": action.model_dump() if action else None,
        "validator_error": errors,
        "capability_definition": capability.model_dump() if capability else None,
        "tool_schema": {},
        "repair_budget": {"used": state.repair_count, "max": 1},
    }


def final_answer_runtime(state: AgentState) -> dict:
    gaps = [gap.description for gap in state.evidence_gaps]
    return {
        "raw_query": state.user_request.raw_query,
        "resolved_context": state.current_context.model_dump(),
        "answer_status": state.answer_status,
        "verified_claims": [],
        "supporting_evidence": evidence_summary(state),
        "limitations": list(dict.fromkeys([*gaps, *state.warnings]))[:10],
    }

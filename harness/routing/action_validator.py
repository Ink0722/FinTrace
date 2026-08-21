"""Deterministic validation of every AgentAction before execution (docs/13 §13)."""
from __future__ import annotations

import re

from schemas.agent_state import AgentState
from schemas.request import AgentAction
from tools.entity_resolver import EntityResolver

from harness.routing.capability_registry import get_capability

WINDCODE = re.compile(r"^\d{6}\.(SZ|SH|BJ)$", flags=re.IGNORECASE)


def validate_action(action: AgentAction, state: AgentState, resolver: EntityResolver | None = None) -> list[str]:
    errors: list[str] = []
    if action.action != "call_tool":
        if action.action not in {"finish", "clarify", "unsupported"}:
            errors.append(f"unknown action type: {action.action}")
        return errors

    capability = get_capability(action.capability or "")
    if capability is None:
        errors.append(f"unknown capability: {action.capability}")
        return errors
    if not capability.implemented:
        errors.append(f"capability not implemented: {action.capability}")
        return errors
    if action.tool_name != capability.tool or action.operation != capability.operation:
        errors.append(
            f"tool/operation mismatch: expected {capability.tool}.{capability.operation}, "
            f"got {action.tool_name}.{action.operation}"
        )
        return errors

    arguments = action.arguments
    if "knowledge_cutoff" in arguments:
        errors.append("planner must not set knowledge_cutoff; it is injected by the workflow")

    if action.capability == "document_retrieval" and state.parsed_request:
        if not arguments.get("company_ids") and len(state.parsed_request.entities) == 1:
            arguments["company_ids"] = list(state.parsed_request.entities)
        if not arguments.get("document_types") and state.parsed_request.document_types:
            arguments["document_types"] = list(state.parsed_request.document_types)

    company_ids = arguments.get("company_ids")
    if company_ids is not None:
        if not isinstance(company_ids, list) or not company_ids:
            errors.append("company_ids must be a non-empty list")
        else:
            normalized: list[str] = []
            for term in company_ids:
                canonical = _canonical_company(term, resolver)
                if canonical is None:
                    errors.append(f"company_ids contains an unresolvable entity: {term}")
                elif canonical not in normalized:
                    normalized.append(canonical)
            if normalized and len(normalized) == len(company_ids):
                arguments["company_ids"] = normalized

    if action.capability == "financial_metric_compare":
        n_companies = len(arguments.get("company_ids") or [])
        n_periods = len(arguments.get("report_periods") or [])
        if n_companies > 1 and n_periods > 1:
            errors.append("metric_compare forbids multiple companies AND multiple periods (ambiguous dimension)")
        if n_companies < 1 or n_periods < 1:
            errors.append("metric_compare requires company_ids and report_periods")

    for slot in capability.required_slots:
        if slot == "company_ids_or_holder_ids":
            if not arguments.get("company_ids") and not arguments.get("holder_ids"):
                errors.append("holding_query requires company_ids or holder_ids")
        elif slot == "query":
            if not str(arguments.get("query") or "").strip():
                errors.append("document retrieval requires a non-empty query")
        elif slot == "entity_ids":
            if not arguments.get("entity_ids"):
                errors.append("event_query requires entity_ids")
        elif not arguments.get(slot):
            errors.append(f"missing required argument: {slot}")

    if _is_duplicate(action, state):
        errors.append("duplicate tool call with identical arguments and no information gain")
    return errors


def repair_action(action: AgentAction, errors: list[str], state: AgentState) -> AgentAction | None:
    """Deterministic minimal repair for the common fixable cases; None means replan."""
    arguments = dict(action.arguments)
    repaired: bool = False

    if "planner must not set knowledge_cutoff" in " ".join(errors):
        arguments.pop("knowledge_cutoff", None)
        repaired = True

    if any("unresolvable entity" in error for error in errors) and state.parsed_request:
        known = state.parsed_request.entities
        candidate = [company for company in known if company not in arguments.get("company_ids", [])]
        if arguments.get("company_ids") and candidate:
            arguments["company_ids"] = [arguments["company_ids"][0]]
            repaired = True

    if not repaired:
        return None
    return action.model_copy(update={"arguments": arguments})


def _canonical_company(term: str, resolver: EntityResolver | None) -> str | None:
    if WINDCODE.match(str(term)):
        return str(term).upper()
    if resolver is None:
        return None
    resolution = resolver.resolve_company(str(term))
    if resolution.status == "resolved" and resolution.company_id:
        return resolution.company_id
    return None


def _is_duplicate(action: AgentAction, state: AgentState) -> bool:
    fingerprint = _action_fingerprint(action.arguments)
    for entry in state.tool_call_history:
        if entry.tool_name != action.tool_name or entry.operation != action.operation:
            continue
        if _action_fingerprint(entry.arguments) == fingerprint:
            return True
    return False


def _action_fingerprint(arguments: dict) -> dict:
    fingerprint = {key: value for key, value in arguments.items() if key not in {"reason", "operation", "knowledge_cutoff"}}
    query = fingerprint.get("query")
    if isinstance(query, str):
        fingerprint["query"] = " ".join(query.lower().split())
    return fingerprint

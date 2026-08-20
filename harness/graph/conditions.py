"""Conditional edge functions for the new Agent graph."""


def after_pre_answerability(state) -> str:
    status = state.pre_answerability.status if state.pre_answerability else "routeable"
    if status == "clarification_required":
        return "clarify"
    if status == "unsupported":
        return "refuse"
    return "route"


def after_route_mode(state) -> str:
    return "direct" if state.routing_mode == "direct" else "investigation"


def after_plan_next_action(state) -> str:
    next_action = getattr(state, "next_action", "validate")
    if next_action == "clarify":
        return "clarify"
    if next_action == "refuse":
        return "refuse"
    if next_action == "answer":
        return "answer"
    return "validate"


def after_validate_action(state) -> str:
    next_action = getattr(state, "next_action", "replan")
    if next_action == "execute":
        return "execute"
    if next_action in {"repair", "validate"}:  # repair needed, or a repaired action needs re-validation
        return "repair"
    return "replan"


def after_review_evidence(state) -> str:
    return "answer" if getattr(state, "next_action", "answer") == "answer" else "plan"


def after_answer_generation(state) -> str:
    if state.llm_status == "failed":
        return "error"
    return "end"

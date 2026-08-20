"""LangGraph nodes for the Direct Fast Path + Bounded Investigation architecture (docs/13 §21)."""
from __future__ import annotations

from schemas.agent_state import AgentState, CurrentContext, Message
from schemas.request import AgentAction, ToolCallEntry
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolResult
from tools.entity_resolver import EntityResolver
from tools.registry import execute_tool

from harness.answering import build_structured_error_answer, generate_answer_with_status
from harness.evidence.ledger import merge_evidence
from harness.evidence.review import review_evidence
from harness.guards.validation import validate_tool_result
from harness.memory.session_store import SessionStore
from harness.runtime_context import final_answer_runtime, planner_runtime, repair_runtime, reviewer_runtime
from harness.routing.action_validator import repair_action, validate_action
from harness.routing.answerability import check_answerability, is_investigation
from harness.routing.capability_registry import candidate_capabilities
from harness.routing.direct_gate import build_direct_action
from harness.routing.planner import plan_next_action
from harness.routing.request_parser import parse_request
from harness.skills import run_skill
from schemas.enums import ToolName
from schemas.request import AgentAction, EvidenceReview, PreAnswerability


def load_session_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("load_session")
    store = SessionStore()
    loaded = store.load(state.session_id)
    if loaded["current_context"]:
        state.current_context = CurrentContext(**loaded["current_context"])
    if not state.conversation_summary:
        state.conversation_summary = loaded["conversation_summary"]
    state.previous_findings = loaded["verified_findings"]
    history = [Message(**item) for item in loaded["recent_messages"]]
    state.messages = [*history, *state.messages]
    return state


def resolve_request_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("resolve_request")
    query = state.user_request.raw_query
    resolver = EntityResolver()

    def llm_fallback(raw_query: str, current_context, rule_parsed):
        from harness.llm import QwenClient
        from harness.skills import run_skill

        client = QwenClient()
        if not client.enabled:
            return None
        runtime = {
            "raw_query": raw_query,
            "current_context": current_context.model_dump() if current_context else {},
            "deterministic_entity_candidates": [item.model_dump() for item in rule_parsed.entity_candidates],
            "deterministic_time_candidates": {
                "periods": rule_parsed.periods,
                "as_of_dates": rule_parsed.as_of_dates,
                "start_date": rule_parsed.start_date,
                "end_date": rule_parsed.end_date,
                "unresolved": rule_parsed.unresolved_references,
            },
        }
        output, record = run_skill("request_parser", runtime, client=client)
        state.llm_calls.append(record)
        return output

    parsed = parse_request(
        query,
        current_context=state.current_context,
        resolver=resolver,
        knowledge_cutoff=state.knowledge_cutoff,
        llm_fallback=llm_fallback,
    )
    state.parsed_request = parsed
    state.user_request.intent = parsed.task_family

    # Carryover: later turns can resolve pronouns against this context.
    if parsed.entities:
        state.current_context.company_ids = parsed.entities[-3:]
        state.current_context.company_names = [
            name for name in (resolver.company_name(company) for company in parsed.entities[-3:]) if name
        ]
    if parsed.periods:
        state.current_context.report_periods = parsed.periods[-4:]
    if parsed.focus_topics:
        state.current_context.focus_topics = parsed.focus_topics
    if parsed.task_family != "unknown":
        state.current_context.active_topic = parsed.task_family
    return state


def check_pre_answerability_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("check_pre_answerability")
    state.pre_answerability = check_answerability(state.parsed_request)
    state.candidate_capabilities = candidate_capabilities(state.parsed_request.task_family)
    if state.pre_answerability.status == "routeable" and is_investigation(state.parsed_request):
        # Investigation adapts across evidence types: offer the broad implemented set,
        # otherwise the planner cannot switch to documents/events when a slot is missing.
        state.candidate_capabilities = candidate_capabilities("financial_investigation")
        for extra in ("ownership_snapshot", "ownership_compare"):
            if extra not in state.candidate_capabilities:
                state.candidate_capabilities.append(extra)
    state.next_action = state.pre_answerability.status  # routeable | clarification_required | unsupported
    return state


def build_clarification_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("build_clarification")
    pre = state.pre_answerability
    state.answer_status = "clarification_required"
    state.workflow_status = "clarification_required"
    state.termination_reason = "clarification_required"
    state.final_answer = pre.clarification_question or "请补充必要条件后重试。"
    return state


def build_refusal_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("build_refusal")
    pre = state.pre_answerability
    state.answer_status = "unsupported"
    state.workflow_status = "unsupported"
    state.termination_reason = "unsupported"
    state.final_answer = f"当前系统无法处理该请求：{pre.reason}"
    return state


def route_mode_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("route_mode")
    parsed = state.parsed_request
    if is_investigation(parsed):
        state.routing_mode = "investigation"
        state.next_action = "plan"
        return state
    action = build_direct_action(parsed)
    if action is None:
        state.routing_mode = "investigation"
        state.next_action = "plan"
        return state
    state.routing_mode = "direct"
    state.current_action = action
    state.next_action = "validate"
    return state


def plan_next_action_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("plan_next_action")
    state.step_count += 1
    if state.total_tool_calls >= state.max_total_tool_calls or state.step_count > state.max_steps:
        state.termination_reason = state.termination_reason or "budget_exhausted"
        state.current_action = AgentAction(action="finish", reason="调查预算已耗尽，基于现有证据收束。")
    else:
        state.current_action = _plan_via_llm_or_rules(state)
    action = state.current_action
    if action.action == "clarify":
        state.pre_answerability = state.pre_answerability.model_copy(
            update={
                "status": "clarification_required",
                "clarification_question": action.reason or "请补充必要条件。",
            }
        )
        state.next_action = "clarify"
    elif action.action == "unsupported":
        state.pre_answerability = state.pre_answerability.model_copy(
            update={"status": "unsupported", "reason": action.reason or "超出能力边界。"}
        )
        state.next_action = "refuse"
    elif action.action == "finish":
        state.next_action = "answer"
    else:
        state.next_action = "validate"
    return state


def _run_skill_with_breaker(state: AgentState, skill: str, runtime: dict):
    """Skip further calls to a skill that already failed this turn (e.g. API down)."""
    from harness.prompts import SKILL_REGISTRY, load_prompt

    prompt_id = load_prompt(SKILL_REGISTRY[skill][0]).prompt_id
    if any(record.prompt_id == prompt_id and record.status == "failed" for record in state.llm_calls):
        return None, None
    return run_skill(skill, runtime)


def _plan_via_llm_or_rules(state: AgentState) -> AgentAction:
    """LLM skill (03) picks the next action; the rule queue is the degraded fallback."""
    if state.routing_mode != "investigation":
        return plan_next_action(state)
    action, record = _run_skill_with_breaker(state, "next_action_planner", planner_runtime(state))
    if record is not None:
        state.llm_calls.append(record)
    if isinstance(action, AgentAction) and action.action in {"call_tool", "finish", "clarify", "unsupported"}:
        return action
    if record is not None and record.status != "failed":
        state.warnings.append("LLM planner 输出非法，已回退规则调查队列。")
    if "LLM planner 不可用，使用规则调查队列。" not in state.warnings:
        state.warnings.append("LLM planner 不可用，使用规则调查队列。")
    return plan_next_action(state)


def _latest_validation_errors(state: AgentState) -> list[str]:
    for item in reversed(state.validation_results):
        if item.get("stage") == "validate_action":
            return item.get("errors", [])
    return []


def validate_action_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("validate_action")
    action = state.current_action
    errors = validate_action(action, state, resolver=EntityResolver())
    state.validation_results.append({"stage": "validate_action", "errors": errors})
    if not errors:
        state.next_action = "execute"
    elif state.repair_count < 1:
        state.next_action = "repair"
    else:
        state.failed_actions.append(
            {"action": action.model_dump() if action else None, "errors": errors, "reason": "validation_failed"}
        )
        state.next_action = "replan"
    return state


def repair_action_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("repair_action")
    state.repair_count += 1
    action = state.current_action
    errors = _latest_validation_errors(state)

    repaired = repair_action(action, errors, state) if action else None
    if repaired is None and action is not None:
        result, record = run_skill("action_repair", repair_runtime(state, errors))
        if record is not None:
            state.llm_calls.append(record)
        if result is not None and result.status == "repaired" and result.repaired_action is not None:
            repaired = result.repaired_action
        elif result is not None and result.status == "clarification_required":
            base = state.pre_answerability or PreAnswerability(status="clarification_required")
            state.pre_answerability = base.model_copy(
                update={"status": "clarification_required", "clarification_question": result.reason or "请补充必要条件。"}
            )
            state.next_action = "clarify"
            return state

    if repaired is not None:
        state.current_action = repaired
        state.next_action = "validate"
        return state
    state.failed_actions.append(
        {"action": action.model_dump() if action else None, "errors": errors, "reason": "repair_failed"}
    )
    state.next_action = "replan"
    return state


def execute_one_tool_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("execute_one_tool")
    action = state.current_action
    arguments = dict(action.arguments or {})
    # financial/ownership tools require `operation` in arguments; document/event forbid it.
    if action.tool_name in {"financial_analysis", "ownership_analysis"} and action.operation:
        arguments.setdefault("operation", action.operation)
    if state.knowledge_cutoff:
        arguments.setdefault("knowledge_cutoff", state.knowledge_cutoff)
    call = ToolCall(
        tool_call_id=f"CALL-{state.total_tool_calls + 1:03d}",
        tool_name=ToolName(action.tool_name),
        arguments=arguments,
        reason=action.reason or "",
    )
    evidence_before = len(state.evidence_ledger)
    result: ToolResult = execute_tool(call)
    state.tool_results.append(result)
    state.evidence_ledger = merge_evidence(state.evidence_ledger, result.evidence)
    state.total_tool_calls += 1
    state.tool_call_history.append(
        ToolCallEntry(
            tool_name=call.tool_name.value,
            operation=action.operation,
            arguments={key: value for key, value in arguments.items() if key not in {"query", "knowledge_cutoff"}},
            status=result.status.value,
            evidence_ids=[item.evidence_id for item in result.evidence],
            action_reason=action.reason or "",
        )
    )
    if len(state.evidence_ledger) > evidence_before:
        state.no_new_evidence_rounds = 0
    else:
        state.no_new_evidence_rounds += 1
    return state


def validate_tool_result_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("validate_tool_result")
    if state.tool_results:
        result = state.tool_results[-1]
        errors = validate_tool_result(result)
        if result.status.value != "success":
            errors.append(result.error.message if result.error else "tool returned non-success status")
        state.validation_results.append(
            {"stage": "validate_tool_result", "tool_call_id": result.tool_call_id, "errors": errors}
        )
    return state


def merge_evidence_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("merge_evidence")
    # Evidence was merged incrementally in execute_one_tool_node; this node is the audit point.
    return state


def review_evidence_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("review_evidence")
    deterministic = review_evidence(state)
    review = deterministic
    llm_decided = False
    if state.routing_mode == "investigation":
        llm_review, record = _run_skill_with_breaker(state, "evidence_reviewer", reviewer_runtime(state))
        if record is not None:
            state.llm_calls.append(record)
        if isinstance(llm_review, EvidenceReview):
            llm_decided = True
            llm_descriptions = {gap.description for gap in llm_review.evidence_gaps}
            merged_gaps = [
                *llm_review.evidence_gaps,
                *[gap for gap in deterministic.evidence_gaps if gap.description not in llm_descriptions],
            ]
            review = EvidenceReview(
                status=llm_review.status,
                covered_aspects=llm_review.covered_aspects,
                evidence_gaps=merged_gaps,
                reason=llm_review.reason,
            )
    state.evidence_gaps = review.evidence_gaps
    state.evidence_sufficient = review.status == "sufficient"
    state.review_status = review.status
    action = state.current_action
    if state.routing_mode == "direct":
        state.next_action = "answer"
    elif action is not None and action.action == "finish":
        state.termination_reason = state.termination_reason or "planner_finish"
        state.next_action = "answer"
    elif llm_decided and review.status in {"sufficient", "partial", "insufficient"}:
        state.next_action = "answer"
    elif state.no_new_evidence_rounds >= 2:
        state.termination_reason = state.termination_reason or "no_new_evidence"
        state.next_action = "answer"
    elif state.total_tool_calls >= state.max_total_tool_calls:
        state.termination_reason = state.termination_reason or "budget_exhausted"
        state.next_action = "answer"
    else:
        state.next_action = "plan"
    return state


def generate_answer_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("generate_answer")
    if state.answer_status not in {"clarification_required", "unsupported"}:
        state.answer_status = _answer_status_from_review_state(state)

    result, record = _run_skill_with_breaker(state, "final_answer", final_answer_runtime(state))
    if record is not None:
        state.llm_calls.append(record)
    if result is not None:
        state.final_answer = result.model_dump_json(ensure_ascii=False)
        state.llm_status = "success"
        state.workflow_status = state.answer_status or "completed"
        return state

    answer, status, error = generate_answer_with_status(state)
    state.final_answer = answer
    state.llm_status = status
    if error:
        state.errors.append(error)
        state.workflow_status = "llm_failed"
    else:
        state.workflow_status = state.answer_status or "completed"
    return state


def _answer_status_from_review_state(state: AgentState) -> str:
    status = state.review_status
    if status == "sufficient":
        return "answered"
    if status == "partial":
        return "partially_answered"
    if state.evidence_ledger:
        return "partially_answered"
    return "insufficient_evidence"


def persist_session_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("persist_session")
    store = SessionStore()
    store.save(
        state.session_id,
        current_context=state.current_context,
        conversation_summary=state.conversation_summary,
        verified_findings=state.previous_findings,
        recent_messages=state.messages,
    )
    return state


def structured_error_node(state: AgentState) -> AgentState:
    state.executed_nodes.append("structured_error")
    state.final_answer = build_structured_error_answer(state)
    if state.llm_status == "failed" or state.answer_status is None:
        state.answer_status = "failed"
    if state.workflow_status == "running":
        state.workflow_status = "failed"
    return state

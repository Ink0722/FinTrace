"""Lightweight rolling memory built on the existing session JSON fields."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from schemas.agent_state import AgentState, Message
from schemas.memory import MemoryUpdate, VerifiedFinding
from schemas.request import LlmCallRecord, ParsedRequest

RECENT_MESSAGE_LIMIT = 12
RECENT_MESSAGE_RETAIN_AFTER_SUMMARY = 8
SUMMARY_INTERVAL_TURNS = 6
SUMMARY_CHAR_THRESHOLD = 12_000
MAX_SUMMARY_CHARS = 2_000
MAX_VERIFIED_FINDINGS = 100
MAX_RELEVANT_FINDINGS = 8

SkillRunner = Callable[[str, dict[str, Any]], tuple[BaseModel | None, LlmCallRecord]]


def prepare_session_memory(state: AgentState, *, skill_runner: SkillRunner | None = None) -> None:
    """Append the answer, compact old messages, and persist evidence-bound facts in state."""
    _append_assistant_answer(state)
    state.previous_findings = update_verified_findings(
        state.previous_findings,
        state.evidence_ledger,
        parsed=state.parsed_request,
        tool_results=state.tool_results,
        turn_id=state.turn_id,
    )
    state.relevant_memories = select_relevant_findings(state.previous_findings, state.parsed_request)

    if not _should_summarize(state):
        state.messages = state.messages[-RECENT_MESSAGE_LIMIT:]
        return

    messages_to_compress = state.messages[:-RECENT_MESSAGE_RETAIN_AFTER_SUMMARY]
    if not messages_to_compress:
        state.messages = state.messages[-RECENT_MESSAGE_LIMIT:]
        return

    if any(record.status == "failed" for record in state.llm_calls):
        state.warnings.append("本轮已有模型调用失败，已延后会话摘要更新。")
        state.messages = state.messages[-RECENT_MESSAGE_LIMIT:]
        return

    if skill_runner is None:
        from harness.skills import run_skill

        skill_runner = run_skill
    output, record = skill_runner(
        "memory_summarizer",
        {
            "previous_summary": state.conversation_summary,
            "messages_to_compress": [item.model_dump() for item in messages_to_compress],
            "current_context": state.current_context.model_dump(),
            "verified_findings": state.relevant_memories[:MAX_RELEVANT_FINDINGS],
        },
    )
    state.llm_calls.append(record)
    if isinstance(output, MemoryUpdate):
        state.conversation_summary = _format_summary(output)
        state.messages = state.messages[-RECENT_MESSAGE_RETAIN_AFTER_SUMMARY:]
        return

    state.warnings.append("会话摘要更新失败，已保留近期消息，不影响本轮回答。")
    state.messages = state.messages[-RECENT_MESSAGE_LIMIT:]


def update_verified_findings(
    existing: list[dict[str, Any]],
    evidence_items: list,
    *,
    parsed: ParsedRequest | None,
    tool_results: list,
    turn_id: int,
) -> list[dict[str, Any]]:
    """Merge compact evidence facts without trusting generated answer text."""
    merged: dict[str, dict[str, Any]] = {}
    for item in existing:
        key = str(item.get("finding_id") or _legacy_key(item))
        merged[key] = item

    tool_by_evidence: dict[str, str] = {}
    for result in tool_results:
        for evidence in result.evidence:
            tool_by_evidence[evidence.evidence_id] = result.tool_name.value

    fallback_company = parsed.entities[0] if parsed and len(parsed.entities) == 1 else None
    for evidence in evidence_items:
        if evidence.support_level == "weak":
            continue
        evidence_id = str(evidence.evidence_id or "").strip()
        if not evidence_id:
            continue
        fact = _compact_value(evidence.fact)
        company_id = evidence.source.company_id or fallback_company
        topic = _topic_from_evidence(evidence.evidence_type)
        finding_id = _finding_id(company_id, topic, fact)
        finding = VerifiedFinding(
            finding_id=finding_id,
            company_id=company_id,
            topic=topic,
            fact=fact,
            evidence_ids=[evidence_id],
            source_turn_id=turn_id,
            source_tool=tool_by_evidence.get(evidence_id),
        )
        previous = merged.get(finding_id)
        if previous:
            old_ids = previous.get("evidence_ids") if isinstance(previous.get("evidence_ids"), list) else []
            finding.evidence_ids = list(dict.fromkeys([*old_ids, evidence_id]))[-10:]
            merged.pop(finding_id, None)
        merged[finding_id] = finding.model_dump(mode="json")
    return list(merged.values())[-MAX_VERIFIED_FINDINGS:]


def select_relevant_findings(
    findings: list[dict[str, Any]], parsed: ParsedRequest | None,
    *, limit: int = MAX_RELEVANT_FINDINGS,
) -> list[dict[str, Any]]:
    """Select by exact company metadata and task topic; no semantic index is required."""
    if not parsed or not parsed.entities:
        return []
    companies = set(parsed.entities)
    preferred_topic = _topic_from_task(parsed.task_family)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for raw in findings:
        try:
            finding = VerifiedFinding.model_validate(raw)
        except (TypeError, ValueError):
            continue
        if finding.company_id not in companies:
            continue
        topic_score = 1 if preferred_topic and finding.topic == preferred_topic else 0
        scored.append((topic_score, finding.source_turn_id, finding.model_dump(mode="json")))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:limit]]


def _append_assistant_answer(state: AgentState) -> None:
    if not state.final_answer:
        return
    content = _answer_text(state.final_answer)
    if state.messages and state.messages[-1].role == "assistant" and state.messages[-1].content == content:
        return
    state.messages.append(Message(role="assistant", content=content))


def _answer_text(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw[:4_000]
    if isinstance(payload, dict) and payload.get("answer"):
        return str(payload["answer"])[:4_000]
    return raw[:4_000]


def _should_summarize(state: AgentState) -> bool:
    if len(state.messages) <= RECENT_MESSAGE_RETAIN_AFTER_SUMMARY:
        return False
    char_count = sum(len(item.content) for item in state.messages)
    return (
        state.turn_id % SUMMARY_INTERVAL_TURNS == 0
        or len(state.messages) > RECENT_MESSAGE_LIMIT
        or char_count > SUMMARY_CHAR_THRESHOLD
    )


def _format_summary(update: MemoryUpdate) -> str:
    summary = update.summary.strip()
    if update.open_questions:
        summary = f"{summary}\n未完成事项：{'；'.join(update.open_questions)}".strip()
    return summary[:MAX_SUMMARY_CHARS]


def _compact_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return str(value)[:300]
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple)):
        return [_compact_value(item, depth=depth + 1) for item in value[:10]]
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:300]


def _finding_id(company_id: str | None, topic: str, fact: dict[str, Any]) -> str:
    payload = json.dumps(
        {"company_id": company_id, "topic": topic, "fact": fact},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return "MEM-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20].upper()


def _legacy_key(item: dict[str, Any]) -> str:
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return "LEGACY-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20].upper()


def _topic_from_evidence(evidence_type: str) -> str:
    if evidence_type.startswith("financial_"):
        return "financial"
    if evidence_type.startswith("shareholder_"):
        return "ownership"
    if evidence_type.startswith("event_"):
        return "event"
    if evidence_type.startswith("research_"):
        return "research"
    if evidence_type.startswith("document_"):
        return "document"
    return "other"


def _topic_from_task(task_family: str) -> str | None:
    if task_family.startswith("financial_"):
        return "financial"
    if task_family.startswith("ownership_"):
        return "ownership"
    if task_family.startswith("event_"):
        return "event"
    if task_family.startswith("research_"):
        return "research"
    if task_family == "document_retrieval":
        return "document"
    return None

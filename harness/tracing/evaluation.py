"""Compact per-turn records for offline evaluation and replay."""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv

from schemas.agent_state import AgentState


load_dotenv()

_PROCESS_LOCK = threading.Lock()


def write_evaluation_turn(state: AgentState, *, run_id: str, trace_id: str, latency_ms: int) -> None:
    if not _enabled():
        return
    path = Path(os.getenv("FINTRACE_EVAL_LOG_PATH", "./evaluation/runs/agent_turns.jsonl"))
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "trace_id": trace_id,
        "session_id": state.session_id,
        "turn_id": state.turn_id,
        "query": state.user_request.raw_query,
        "resolved_context": _resolved_context(state),
        "routing_mode": state.routing_mode,
        "answer_status": state.answer_status,
        "answer": _answer_payload(state.final_answer),
        "tool_calls": [_tool_call_summary(item.model_dump()) for item in state.tool_call_history],
        "evidence_ids": [item.evidence_id for item in state.evidence_ledger],
        "limitations": _limitations(state),
        "errors": state.errors,
        "warnings": state.warnings,
        "termination_reason": state.termination_reason,
        "workflow_status": state.workflow_status,
        "llm_status": state.llm_status,
        "latency_ms": latency_ms,
    }
    _append_jsonl(path, payload)


def _resolved_context(state: AgentState) -> dict[str, Any]:
    parsed = state.parsed_request
    return {
        "company_ids": list(parsed.entities if parsed else state.current_context.company_ids),
        "report_periods": list(parsed.periods if parsed else state.current_context.report_periods),
        "task_family": parsed.task_family if parsed else None,
        "focus_topics": list(parsed.focus_topics if parsed else state.current_context.focus_topics),
        "document_types": list(parsed.document_types if parsed else []),
        "knowledge_cutoff": state.knowledge_cutoff,
    }


def _tool_call_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_name": item.get("tool_name"),
        "operation": item.get("operation"),
        "arguments": item.get("arguments") or {},
        "status": item.get("status"),
        "evidence_ids": item.get("evidence_ids") or [],
        "reason": item.get("action_reason") or "",
    }


def _answer_payload(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    return str(parsed.get("answer") or raw) if isinstance(parsed, dict) else raw


def _limitations(state: AgentState) -> list[str]:
    limitations = [gap.description for gap in state.evidence_gaps]
    if state.final_answer:
        try:
            parsed = json.loads(state.final_answer)
            if isinstance(parsed, dict):
                limitations.extend(parsed.get("limitations_disclosed") or parsed.get("limitations") or [])
        except (json.JSONDecodeError, TypeError):
            pass
    return list(dict.fromkeys(str(item) for item in limitations if item))


def _enabled() -> bool:
    return os.getenv("FINTRACE_EVAL_LOG_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    with _PROCESS_LOCK, _file_lock(path.with_suffix(path.suffix + ".lock")):
        with path.open("a", encoding="utf-8") as file:
            file.write(line)
            file.flush()


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0)
        if lock_file.read(1) == b"":
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

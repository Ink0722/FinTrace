"""One-time migration of legacy Trace and evaluation JSONL files to SQLite."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from harness.tracing.store import import_payload, observability_path


def migrate(
    *, traces_path: Path, turns_path: Path, database_path: Path,
) -> dict[str, Any]:
    traces, trace_errors = _read_jsonl(traces_path)
    turns, turn_errors = _read_jsonl(turns_path)
    turns_by_run = {item["run_id"]: item for _, item in turns if item.get("run_id")}
    imported_ids: set[str] = set()
    matched = legacy = 0

    for line_number, trace in traces:
        run_id = trace.get("run_id")
        turn = turns_by_run.get(run_id) if run_id else None
        if run_id:
            matched += int(turn is not None)
        else:
            run_id = _legacy_id(trace, line_number)
            legacy += 1
        payload = _merge(trace, turn or {}, run_id=run_id, legacy=turn is None)
        import_payload(payload, path=database_path)
        imported_ids.add(run_id)

    for _, turn in turns:
        run_id = turn.get("run_id")
        if not run_id or run_id in imported_ids:
            continue
        import_payload(_merge({}, turn, run_id=run_id, legacy=False), path=database_path)
        imported_ids.add(run_id)

    return {
        "status": "completed" if not trace_errors and not turn_errors else "completed_with_errors",
        "database_path": str(database_path),
        "trace_rows": len(traces),
        "turn_rows": len(turns),
        "matched_rows": matched,
        "legacy_trace_rows": legacy,
        "imported_runs": len(imported_ids),
        "invalid_trace_rows": trace_errors,
        "invalid_turn_rows": turn_errors,
    }


def _merge(trace: dict, turn: dict, *, run_id: str, legacy: bool) -> dict:
    tool_results = trace.get("tool_results_summary") or []
    evidence = trace.get("evidence") or [
        evidence
        for result in tool_results
        for evidence in (result.get("evidence") or [])
    ]
    parsed = trace.get("parsed_request") or (turn.get("resolved_context") if turn else None)
    final_answer = trace.get("final_answer")
    return {
        "run_id": run_id,
        "trace_id": trace.get("trace_id") or turn.get("trace_id") or f"TRACE-{run_id}",
        "session_id": trace.get("session_id") or turn.get("session_id") or "LEGACY",
        "turn_id": trace.get("turn_id") or turn.get("turn_id"),
        "created_at": trace.get("created_at") or turn.get("created_at"),
        "query": trace.get("user_query") or turn.get("query") or "",
        "parsed_request": parsed,
        "current_context": trace.get("current_context"),
        "knowledge_cutoff": (parsed or {}).get("knowledge_cutoff") if isinstance(parsed, dict) else None,
        "routing_mode": trace.get("routing_mode") or turn.get("routing_mode"),
        "answer_status": trace.get("answer_status") or turn.get("answer_status"),
        "answer": turn.get("answer") or _answer_text(final_answer),
        "final_answer": final_answer,
        "termination_reason": trace.get("termination_reason") or turn.get("termination_reason"),
        "workflow_status": trace.get("workflow_status") or turn.get("workflow_status"),
        "llm_status": trace.get("llm_status") or turn.get("llm_status"),
        "warnings": trace.get("warnings") or turn.get("warnings") or [],
        "errors": trace.get("errors") or turn.get("errors") or [],
        "latency_ms": trace.get("latency_ms") or turn.get("latency_ms") or 0,
        "tool_calls": trace.get("planner_actions") or (trace.get("plan") or {}).get("tool_calls") or turn.get("tool_calls") or [],
        "tool_results": tool_results,
        "evidence": evidence,
        "evidence_gaps": trace.get("evidence_gaps") or [],
        "validation": trace.get("validation") or [],
        "failed_actions": trace.get("failed_actions") or [],
        "llm_calls": trace.get("llm_calls") or [],
        "executed_nodes": trace.get("executed_nodes") or [],
        "legacy": legacy,
    }


def _read_jsonl(path: Path) -> tuple[list[tuple[int, dict]], list[int]]:
    if not path.exists():
        return [], []
    rows, errors = [], []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append((line_number, value))
                else:
                    errors.append(line_number)
            except json.JSONDecodeError:
                errors.append(line_number)
    return rows, errors


def _legacy_id(trace: dict, line_number: int) -> str:
    identity = json.dumps({
        "created_at": trace.get("created_at"),
        "session_id": trace.get("session_id"),
        "query": trace.get("user_query"),
        "line": line_number,
    }, ensure_ascii=False, sort_keys=True)
    return f"LEGACY-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24].upper()}"


def _answer_text(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return str(parsed.get("answer") or raw) if isinstance(parsed, dict) else raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, default=Path("evaluation/reports/traces.jsonl"))
    parser.add_argument("--turns", type=Path, default=Path("evaluation/runs/agent_turns.jsonl"))
    parser.add_argument("--database", type=Path, default=observability_path())
    args = parser.parse_args()
    print(json.dumps(migrate(
        traces_path=args.traces, turns_path=args.turns, database_path=args.database,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

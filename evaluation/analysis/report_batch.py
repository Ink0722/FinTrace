"""Export one completed evaluation batch into review sheets and report metrics."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import statistics
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from harness.runtime_db import PROJECT_ROOT
from harness.tracing.store import connect


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "evaluation" / "results"
ANSWER_MANUAL_FIELDS = (
    "human_label", "llm_label", "adjudicated_label", "final_correct",
    "required_facts", "necessary_fact_count", "hit_fact_count", "reviewer_notes",
)
TOOL_MANUAL_FIELDS = (
    "manual_call_needed", "manual_tool_correct", "manual_operation_correct",
    "manual_parameters_correct", "manual_is_redundant", "final_call_correct",
    "required_financial_calls", "covered_financial_calls",
    "required_ownership_calls", "covered_ownership_calls",
    "required_event_calls", "covered_event_calls",
    "required_document_research_calls", "covered_document_research_calls",
    "reviewer_notes",
)
ERROR_MANUAL_FIELDS = (
    "manual_is_runtime_error", "manual_error_category", "manual_correctly_handled",
    "reviewer_notes",
)


@dataclass(frozen=True)
class BatchContext:
    batch: dict[str, Any]
    cases: list[dict[str, Any]]
    runs: dict[str, dict[str, Any]]
    tools: dict[str, list[dict[str, Any]]]
    evidence: dict[str, list[dict[str, Any]]]
    llm_calls: dict[str, list[dict[str, Any]]]
    attempts: dict[tuple[str, int], list[dict[str, Any]]]


def prepare_batch(batch_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    """Create review sheets without changing the runtime database."""
    context = load_batch(batch_id)
    output_dir = output_root / batch_id
    output_dir.mkdir(parents=True, exist_ok=True)

    answer_rows = build_answer_rows(context)
    tool_rows = build_tool_rows(context)
    error_rows = build_error_rows(context)
    runtime_rows = build_runtime_rows(context)

    _write_review_csv(
        output_dir / "answer_review.csv", answer_rows, ANSWER_MANUAL_FIELDS,
        key_fields=("case_id",),
    )
    _write_review_csv(
        output_dir / "tool_review.csv", tool_rows, TOOL_MANUAL_FIELDS,
        key_fields=("case_id", "call_sequence"),
    )
    _write_review_csv(
        output_dir / "error_review.csv", error_rows, ERROR_MANUAL_FIELDS,
        key_fields=("case_id",),
    )
    _write_csv(output_dir / "runtime_metrics.csv", runtime_rows)

    summary = build_run_summary(context, runtime_rows, error_rows)
    _write_json(output_dir / "run_summary.json", summary)
    metrics = aggregate_reviews(output_dir, context=context)
    return {
        "status": "prepared",
        "batch_id": batch_id,
        "output_dir": str(output_dir),
        "case_count": len(context.cases),
        "files": sorted(path.name for path in output_dir.iterdir() if path.is_file()),
        "annotation_coverage": metrics["annotation_coverage"],
    }


def aggregate_batch(batch_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    """Aggregate review sheets after human/LLM labels have been filled."""
    context = load_batch(batch_id)
    output_dir = output_root / batch_id
    required = (
        "answer_review.csv", "tool_review.csv", "error_review.csv", "runtime_metrics.csv",
    )
    missing = [name for name in required if not (output_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing review files: {missing}. Run the prepare command first."
        )
    metrics = aggregate_reviews(output_dir, context=context)
    return {
        "status": "aggregated",
        "batch_id": batch_id,
        "output_dir": str(output_dir),
        "annotation_coverage": metrics["annotation_coverage"],
    }


def load_batch(batch_id: str) -> BatchContext:
    with connect(readonly=True) as connection:
        batch_row = connection.execute(
            "SELECT * FROM evaluation_batches WHERE batch_id = ?", (batch_id,),
        ).fetchone()
        if batch_row is None:
            raise ValueError(f"Unknown evaluation batch: {batch_id}")
        batch = dict(batch_row)
        cases = [dict(row) for row in connection.execute(
            """
            SELECT ec.*, ar.run_id AS final_run_id, ar.trace_id, ar.user_id,
                   ar.created_at AS run_created_at, ar.answer, ar.final_answer_raw,
                   ar.answer_status, ar.routing_mode, ar.termination_reason,
                   ar.workflow_status, ar.llm_status, ar.latency_ms,
                   ar.knowledge_cutoff AS run_knowledge_cutoff,
                   ar.parsed_request_json, ar.current_context_json,
                   ar.warnings_json, ar.errors_json, ar.evidence_gaps_json,
                   ar.failed_actions_json
            FROM evaluation_cases ec
            LEFT JOIN agent_runs ar ON ar.run_id = ec.run_id
            WHERE ec.batch_id = ?
            ORDER BY CAST(ec.source_session_id AS INTEGER), ec.expected_turn_id
            """,
            (batch_id,),
        )]
        run_ids = [str(row["final_run_id"]) for row in cases if row.get("final_run_id")]
        runs = {str(row["final_run_id"]): row for row in cases if row.get("final_run_id")}
        tools = _load_children(connection, "tool_executions", run_ids)
        evidence = _load_children(connection, "evidence_records", run_ids)
        llm_calls = _load_children(connection, "llm_executions", run_ids)

        original_user = str(batch["evaluation_user_id"])
        attempt_rows = [dict(row) for row in connection.execute(
            """
            SELECT run_id, session_id, turn_id, created_at, answer_status,
                   workflow_status, llm_status, errors_json, failed_actions_json
            FROM agent_runs
            WHERE user_id = ?
            ORDER BY session_id, turn_id, created_at, run_id
            """,
            (original_user,),
        )]
        attempt_ids = [str(row["run_id"]) for row in attempt_rows]
        attempt_tools = _load_children(connection, "tool_executions", attempt_ids)
        attempt_llm = _load_children(connection, "llm_executions", attempt_ids)

    attempts: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in attempt_rows:
        row["tools"] = attempt_tools.get(str(row["run_id"]), [])
        row["llm_calls"] = attempt_llm.get(str(row["run_id"]), [])
        attempts[(str(row["session_id"]), int(row["turn_id"] or 0))].append(row)
    return BatchContext(batch, cases, runs, tools, evidence, llm_calls, dict(attempts))


def build_answer_rows(context: BatchContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in context.cases:
        annotation = _loads(case.get("annotation_json"), {})
        parsed = _loads(case.get("parsed_request_json"), {})
        final_raw = _loads(case.get("final_answer_raw"), {})
        run_id = str(case.get("final_run_id") or "")
        evidence_ids = [str(item.get("evidence_id") or "") for item in context.evidence.get(run_id, [])]
        used_ids = final_raw.get("used_evidence_ids", []) if isinstance(final_raw, dict) else []
        rows.append({
            "case_id": case["case_id"],
            "source_session_id": case["source_session_id"],
            "turn_id": case["expected_turn_id"],
            "run_id": run_id,
            "question": case["question"],
            "dataset_answerability": annotation.get("answerability", ""),
            "required_entities": _json_cell(annotation.get("required_entities") or []),
            "required_date": annotation.get("required_date") or "",
            "required_chunk_ids": _json_cell(annotation.get("required_chunk_ids") or []),
            "parsed_entities": _json_cell(parsed.get("entities") or []),
            "parsed_periods": _json_cell(parsed.get("periods") or parsed.get("requested_periods") or []),
            "task_family": parsed.get("task_family") or "",
            "answer_status": case.get("answer_status") or "",
            "answer": case.get("answer") or "",
            "available_evidence_ids": _json_cell(evidence_ids),
            "used_evidence_ids": _json_cell(used_ids),
            "human_label": "",
            "llm_label": "",
            "adjudicated_label": "",
            "final_correct": "",
            "required_facts": "",
            "necessary_fact_count": "",
            "hit_fact_count": "",
            "reviewer_notes": "",
        })
    return rows


def build_tool_rows(context: BatchContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in context.cases:
        annotation = _loads(case.get("annotation_json"), {})
        valid_tools = annotation.get("valid_tools") or []
        run_id = str(case.get("final_run_id") or "")
        actual = context.tools.get(run_id, [])
        records = actual or [{"sequence": ""}]
        for call in records:
            tool_name = str(call.get("tool_name") or "")
            operation = str(call.get("operation") or "")
            qualified = f"{tool_name}.{operation}" if tool_name and operation else ""
            rows.append({
                "case_id": case["case_id"],
                "source_session_id": case["source_session_id"],
                "turn_id": case["expected_turn_id"],
                "run_id": run_id,
                "question": case["question"],
                "dataset_answerability": annotation.get("answerability", ""),
                "annotated_acceptable_tools": _json_cell(valid_tools),
                "call_sequence": call.get("sequence", ""),
                "tool_name": tool_name,
                "operation": operation,
                "qualified_operation": qualified,
                "reason": call.get("reason") or "",
                "arguments": _canonical_json(call.get("arguments_json"), {}),
                "status": call.get("status") or "",
                "duration_ms": call.get("duration_ms", ""),
                "auto_matches_acceptable_tool": (
                    "yes" if qualified and qualified in valid_tools
                    else "no" if qualified else "not_called"
                ),
                "manual_call_needed": "",
                "manual_tool_correct": "",
                "manual_operation_correct": "",
                "manual_parameters_correct": "",
                "manual_is_redundant": "",
                "final_call_correct": "",
                "required_financial_calls": "",
                "covered_financial_calls": "",
                "required_ownership_calls": "",
                "covered_ownership_calls": "",
                "required_event_calls": "",
                "covered_event_calls": "",
                "required_document_research_calls": "",
                "covered_document_research_calls": "",
                "reviewer_notes": "",
            })
    return rows


def build_error_rows(context: BatchContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in context.cases:
        session_id = str(case.get("agent_session_id") or "")
        turn_id = int(case.get("expected_turn_id") or 0)
        attempts = context.attempts.get((session_id, turn_id), [])
        candidates: list[dict[str, str]] = []
        for attempt in attempts:
            candidates.extend(_attempt_errors(attempt))
        categories = sorted({item["category"] for item in candidates})
        final_attempts = [
            attempt for attempt in attempts
            if str(attempt.get("run_id") or "") == str(case.get("final_run_id") or "")
        ]
        final_candidates = [
            item for attempt in final_attempts for item in _attempt_errors(attempt)
        ]
        final_categories = sorted({item["category"] for item in final_candidates})
        details = [f"[{item['category']}] {item['detail']}" for item in candidates]
        final_failed = (case.get("answer_status") == "failed" or case.get("workflow_status") in {"failed", "llm_failed"})
        all_categories_handled = bool(categories) and not final_failed and set(categories).issubset(final_categories)
        rows.append({
            "case_id": case["case_id"],
            "source_session_id": case["source_session_id"],
            "turn_id": turn_id,
            "question": case["question"],
            "final_run_id": case.get("final_run_id") or "",
            "attempt_count": case.get("attempt_count") or len(attempts),
            "recorded_attempts": len(attempts),
            "automatic_error_candidate": "yes" if candidates else "no",
            "automatic_error_categories": ";".join(categories),
            "automatic_error_details": _json_cell(details),
            "final_run_error_categories": ";".join(final_categories),
            "final_answer_status": case.get("answer_status") or "",
            "final_workflow_status": case.get("workflow_status") or "",
            "automatic_recovery_result": (
                "in_run_recovered" if all_categories_handled
                else "recovered_by_later_execution" if candidates and not final_failed and not final_categories
                else "partially_handled" if candidates and not final_failed and final_categories
                else "not_recovered" if candidates else "no_detected_error"
            ),
            "automatic_correctly_handled": (
                "yes" if all_categories_handled else "no" if candidates else ""
            ),
            "manual_is_runtime_error": "",
            "manual_error_category": "",
            "manual_correctly_handled": "",
            "reviewer_notes": "",
        })
    return rows


def build_runtime_rows(context: BatchContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in context.cases:
        run_id = str(case.get("final_run_id") or "")
        tools = context.tools.get(run_id, [])
        llm_calls = context.llm_calls.get(run_id, [])
        prompt_tokens = completion_tokens = llm_latency_ms = 0
        for call in llm_calls:
            payload = _loads(call.get("payload_json"), {})
            prompt_tokens += _integer(payload.get("prompt_tokens"))
            completion_tokens += _integer(payload.get("completion_tokens"))
            llm_latency_ms += _integer(payload.get("latency_ms"))
        scenario = _scenario(case, len(tools))
        input_cost, output_cost = _token_cost(prompt_tokens, completion_tokens)
        rows.append({
            "case_id": case["case_id"],
            "source_session_id": case["source_session_id"],
            "turn_id": case["expected_turn_id"],
            "run_id": run_id,
            "answer_status": case.get("answer_status") or "",
            "routing_mode": case.get("routing_mode") or "none",
            "scenario": scenario,
            "latency_ms": _integer(case.get("latency_ms")),
            "tool_call_count": len(tools),
            "tool_duration_ms": sum(_integer(item.get("duration_ms")) for item in tools),
            "llm_call_count": len(llm_calls),
            "llm_latency_ms": llm_latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated_cost": "" if input_cost is None else _decimal(input_cost + output_cost),
        })
    return rows


def build_run_summary(
    context: BatchContext,
    runtime_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    models = Counter()
    prompt_versions = Counter()
    for calls in context.llm_calls.values():
        for call in calls:
            model = str(call.get("model") or "")
            if model:
                models[model] += 1
            key = f"{call.get('prompt_id') or 'unknown'}@{call.get('prompt_version') or 'unknown'}"
            prompt_versions[key] += 1
    return {
        "batch": context.batch,
        "generated_from": "runtime/fintrace.sqlite3 (read-only)",
        "git_commit": _git_commit(),
        "python_version": platform.python_version(),
        "case_count": len(context.cases),
        "session_count": len({str(item["source_session_id"]) for item in context.cases}),
        "case_statuses": dict(Counter(str(item.get("status") or "unknown") for item in context.cases)),
        "answer_statuses": dict(Counter(str(item.get("answer_status") or "unknown") for item in context.cases)),
        "routing_modes": dict(Counter(str(item.get("routing_mode") or "none") for item in context.cases)),
        "scenarios": dict(Counter(str(item["scenario"]) for item in runtime_rows)),
        "models": dict(models),
        "prompt_versions": dict(prompt_versions),
        "final_run_count": len(runtime_rows),
        "original_attempt_count": sum(len(value) for value in context.attempts.values()),
        "automatic_error_candidate_turns": sum(
            row["automatic_error_candidate"] == "yes" for row in error_rows
        ),
        "token_totals": {
            "prompt": sum(_integer(row["prompt_tokens"]) for row in runtime_rows),
            "completion": sum(_integer(row["completion_tokens"]) for row in runtime_rows),
            "total": sum(_integer(row["total_tokens"]) for row in runtime_rows),
        },
        "cost_configuration": {
            "input_per_million": os.getenv("FINTRACE_QWEN_INPUT_PRICE_PER_MILLION") or None,
            "output_per_million": os.getenv("FINTRACE_QWEN_OUTPUT_PRICE_PER_MILLION") or None,
            "note": "Cost remains blank unless both environment variables are configured.",
        },
    }


def aggregate_reviews(output_dir: Path, *, context: BatchContext) -> dict[str, Any]:
    answers = _read_csv(output_dir / "answer_review.csv")
    tools = _read_csv(output_dir / "tool_review.csv")
    errors = _read_csv(output_dir / "error_review.csv")
    runtime = _read_csv(output_dir / "runtime_metrics.csv")

    metrics = {
        "batch_id": context.batch["batch_id"],
        "validation": _validate_review_rows(answers, tools, errors),
        "annotation_coverage": _annotation_coverage(answers, tools, errors),
        "answer_quality": _answer_metrics(answers),
        "tool_quality": _tool_metrics(tools),
        "error_handling": _error_metrics(errors),
        "runtime": _runtime_metrics(runtime, tools, answers),
        "specialty_experiments": _specialty_experiments(output_dir),
    }
    _write_json(output_dir / "table_metrics.json", metrics)
    (output_dir / "whitepaper_tables.md").write_text(
        _whitepaper_tables(metrics), encoding="utf-8",
    )
    return metrics


def _answer_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    labels = [_truth(row.get("final_correct")) for row in rows]
    reviewed = [value for value in labels if value is not None]
    fact_rows = [
        row for row in rows
        if _optional_int(row.get("necessary_fact_count")) is not None
        and _optional_int(row.get("hit_fact_count")) is not None
    ]
    reference = sum(_optional_int(row["necessary_fact_count"]) or 0 for row in fact_rows)
    hits = sum(_optional_int(row["hit_fact_count"]) or 0 for row in fact_rows)
    return {
        "total_turns": len(rows),
        "reviewed_turns": len(reviewed),
        "correct_turns": sum(value is True for value in reviewed),
        "answer_accuracy": _ratio(sum(value is True for value in reviewed), len(reviewed)),
        "fact_reviewed_turns": len(fact_rows),
        "reference_facts": reference,
        "hit_facts": hits,
        "key_fact_recall": _ratio(hits, reference),
    }


def _tool_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    domains: dict[str, list[dict[str, str]]] = defaultdict(list)
    actual = [row for row in rows if row.get("call_sequence")]
    for row in actual:
        domains[_tool_domain(row.get("tool_name", ""))].append(row)

    results: dict[str, Any] = {}
    for domain, items in sorted(domains.items()):
        call_labels = [_truth(item.get("final_call_correct")) for item in items]
        parameter_labels = [_truth(item.get("manual_parameters_correct")) for item in items]
        reviewed_calls = [value for value in call_labels if value is not None]
        reviewed_parameters = [value for value in parameter_labels if value is not None]
        required, covered = _tool_recall_counts(rows, domain)
        results[domain] = {
            "logical_calls": len(items),
            "reviewed_calls": len(reviewed_calls),
            "correct_calls": sum(value is True for value in reviewed_calls),
            "precision": _ratio(sum(value is True for value in reviewed_calls), len(reviewed_calls)),
            "required_calls": required,
            "correct_required_calls": covered,
            "recall": _ratio(covered, required),
            "reviewed_parameters": len(reviewed_parameters),
            "correct_parameters": sum(value is True for value in reviewed_parameters),
            "parameter_accuracy": _ratio(
                sum(value is True for value in reviewed_parameters), len(reviewed_parameters)
            ),
        }
    all_labels = [_truth(item.get("final_call_correct")) for item in actual]
    reviewed = [value for value in all_labels if value is not None]
    results["overall"] = {
        "logical_calls": len(actual),
        "reviewed_calls": len(reviewed),
        "correct_calls": sum(value is True for value in reviewed),
        "precision": _ratio(sum(value is True for value in reviewed), len(reviewed)),
    }
    return results


def _error_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    error_rows = [row for row in rows if row.get("automatic_error_candidate") == "yes"]
    handled = [
        row for row in error_rows if _truth(row.get("automatic_correctly_handled")) is True
    ]
    categories: dict[str, dict[str, Any]] = {}
    for category in ("tool_execution", "action_or_parameter", "llm_output", "network_or_infrastructure"):
        selected = [
            row for row in error_rows
            if category in _split_categories(row.get("automatic_error_categories", ""))
        ]
        correct = [
            row for row in selected
            if category in _split_categories(row.get("final_run_error_categories", ""))
            and row.get("final_answer_status") != "failed"
            and row.get("final_workflow_status") not in {"failed", "llm_failed"}
        ]
        categories[category] = {
            "error_turns": len(selected),
            "correctly_handled_turns": len(correct),
            "handling_success_rate": _ratio(len(correct), len(selected)),
        }
    total = len(rows)
    normal = total - len(error_rows)
    return {
        "total_turns": total,
        "reviewed_turns": total,
        "error_turns": len(error_rows),
        "correctly_handled_turns": len(handled),
        "error_rate": _ratio(len(error_rows), total),
        "handling_success_rate": _ratio(len(handled), len(error_rows)),
        "reliable_run_rate": _ratio(normal + len(handled), total),
        "categories": categories,
    }


def _runtime_metrics(
    rows: list[dict[str, str]], tool_rows: list[dict[str, str]],
    answer_rows: list[dict[str, str]],
) -> dict[str, Any]:
    by_scenario: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_scenario[row.get("scenario") or "other"].append(row)
    scenarios = {name: _runtime_group(items) for name, items in sorted(by_scenario.items())}

    by_tool: dict[str, list[float]] = defaultdict(list)
    for row in tool_rows:
        if not row.get("call_sequence"):
            continue
        key = row.get("qualified_operation") or row.get("tool_name") or "unknown"
        by_tool[key].append(float(row.get("duration_ms") or 0))
    return {
        "overall": _runtime_group(rows),
        "scenarios": scenarios,
        "token_bands": _token_band_metrics(rows, answer_rows),
        "tool_durations": {
            name: _duration_summary(values) for name, values in sorted(by_tool.items())
        },
    }


def _token_band_metrics(
    runtime_rows: list[dict[str, str]], answer_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    answers = {row.get("case_id", ""): row for row in answer_rows}
    bands = (
        ("10K", 0, 10_000),
        ("50K", 10_000, 50_000),
        ("100K", 50_000, 100_000),
        ("250K", 100_000, 250_000),
        ("500K", 250_000, 500_000),
    )
    results: list[dict[str, Any]] = []
    for index, (label, lower, upper) in enumerate(bands):
        selected = [
            row for row in runtime_rows
            if (lower <= _integer(row.get("total_tokens")) <= upper if index == 0
                else lower < _integer(row.get("total_tokens")) <= upper)
        ]
        answer_labels = [
            _truth(answers.get(row.get("case_id", ""), {}).get("final_correct"))
            for row in selected
        ]
        reviewed = [value for value in answer_labels if value is not None]
        fact_rows = [
            answers.get(row.get("case_id", ""), {}) for row in selected
            if _optional_int(answers.get(row.get("case_id", ""), {}).get("necessary_fact_count")) is not None
            and _optional_int(answers.get(row.get("case_id", ""), {}).get("hit_fact_count")) is not None
        ]
        reference = sum(_optional_int(row.get("necessary_fact_count")) or 0 for row in fact_rows)
        hits = sum(_optional_int(row.get("hit_fact_count")) or 0 for row in fact_rows)
        latency = _duration_summary([float(row.get("latency_ms") or 0) for row in selected])
        results.append({
            "label": label,
            "lower_exclusive": lower if index else None,
            "upper_inclusive": upper,
            "session_count": len({row.get("source_session_id", "") for row in selected}),
            "turn_count": len(selected),
            "reviewed_answers": len(reviewed),
            "answer_accuracy": _ratio(sum(value is True for value in reviewed), len(reviewed)),
            "reference_facts": reference,
            "hit_facts": hits,
            "key_fact_recall": _ratio(hits, reference),
            "p95_latency_ms": latency["p95_ms"],
        })
    return results


def _runtime_group(rows: list[dict[str, str]]) -> dict[str, Any]:
    latencies = [float(row.get("latency_ms") or 0) for row in rows]
    tokens = [float(row.get("total_tokens") or 0) for row in rows]
    costs = [float(row["estimated_cost"]) for row in rows if row.get("estimated_cost")]
    summary = _duration_summary(latencies)
    return {
        "sample_count": len(rows),
        "p50_latency_ms": summary["p50_ms"],
        "p95_latency_ms": summary["p95_ms"],
        "max_latency_ms": summary["max_ms"],
        "average_tokens": round(statistics.fmean(tokens), 2) if tokens else None,
        "average_cost": round(statistics.fmean(costs), 6) if costs else None,
    }


def _annotation_coverage(
    answers: list[dict[str, str]], tools: list[dict[str, str]], errors: list[dict[str, str]],
) -> dict[str, Any]:
    actual_tools = [row for row in tools if row.get("call_sequence")]
    return {
        "answer_labels": _count_truth_labels(answers, "final_correct"),
        "fact_labels": sum(
            _optional_int(row.get("necessary_fact_count")) is not None
            and _optional_int(row.get("hit_fact_count")) is not None
            for row in answers
        ),
        "tool_call_labels": _count_truth_labels(actual_tools, "final_call_correct"),
        "tool_parameter_labels": _count_truth_labels(actual_tools, "manual_parameters_correct"),
        "error_turn_labels": _count_truth_labels(errors, "manual_is_runtime_error"),
        "error_handling_labels": sum(
            _truth(row.get("manual_is_runtime_error")) is True
            and _truth(row.get("manual_correctly_handled")) is not None
            for row in errors
        ),
        "automatically_detected_error_turns": sum(
            row.get("automatic_error_candidate") == "yes" for row in errors
        ),
        "automatically_handled_error_turns": sum(
            _truth(row.get("automatic_correctly_handled")) is True for row in errors
        ),
    }


def _whitepaper_tables(metrics: dict[str, Any]) -> str:
    tool = metrics["tool_quality"]
    error = metrics["error_handling"]
    runtime = metrics["runtime"]
    specialty = metrics.get("specialty_experiments") or {}
    performance_result, performance_conclusion = _specialty_performance_cells(specialty)
    ownership_strict = specialty.get("ownership_strict_accuracy") or {}
    ownership_gt_3 = (ownership_strict.get("groups") or {}).get("depth_gt_3") or {}
    ownership_accuracy = ownership_gt_3.get("strict_accuracy")
    event_quality = specialty.get("event_quality") or {}
    event_recall = event_quality.get("key_event_recall")
    event_pairwise_precision = event_quality.get("cluster_pairwise_precision")
    event_pairwise_recall = event_quality.get("cluster_pairwise_recall")
    event_pairwise_f1 = event_quality.get("cluster_pairwise_f1")
    event_breaks = event_quality.get("severe_temporal_break_count")
    event_result = (
        f"关键节点召回率{_percent(event_recall)}；聚类成对精确率"
        f"{_percent(event_pairwise_precision)}、召回率{_percent(event_pairwise_recall)}、"
        f"F1 {_percent(event_pairwise_f1)}；"
        f"严重时序断裂{event_breaks}次"
        if event_recall is not None and event_breaks is not None
        else "待专项实验"
    )
    event_conclusion = (
        "达到"
        if event_recall is not None
        and float(event_recall) >= 0.85
        and int(event_breaks or 0) == 0
        else "待评测" if event_recall is None else "未达到"
    )
    lines = [
        "# 第五章实验结果候选表格",
        "",
        "> 本文件由运行记录和评审工作表生成。`待标注`不应替换为估算值。",
        "",
        "## Token分档结果",
        "",
        "| Token档位 | 会话数 | 轮次数 | 回答准确率 | 关键事实召回率 | 端到端$P_{95}$延迟 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in runtime["token_bands"]:
        lines.append(
            f"| {item['label']} | {item['session_count']} | {item['turn_count']} "
            f"| {_percent(item['answer_accuracy']) if item['turn_count'] else '不适用'} "
            f"| {_percent(item['key_fact_recall']) if item['turn_count'] else '不适用'} "
            f"| {_seconds(item['p95_latency_ms']) if item['turn_count'] else '不适用'} |"
        )
    lines.extend([
        "",
        "> 本批次单轮最高总Token为116,283。系统通过文档Chunk检索、近期消息、长期摘要、已验证事实记忆和受限证据集合控制上下文规模，因此未形成0.5M Tokens实际输入；该结果不替代专项极限输入测试。",
        "",
        "## 工具调用结果",
        "",
        "| 工具领域 | 逻辑调用数 | 工具调用精确率 | 工具召回率 | 参数准确率 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    labels = (
        ("financial", "财务工具"), ("ownership", "股权工具"),
        ("event", "事件工具"), ("document_and_research", "文档与研报工具"),
    )
    for key, label in labels:
        item = tool.get(key, {})
        lines.append(
            f"| {label} | {item.get('logical_calls', 0)} | {_percent(item.get('precision'))} "
            f"| {_percent(item.get('recall'))} | {_percent(item.get('parameter_accuracy'))} |"
        )
    lines.extend([
        "",
        "## 错误处置结果",
        "",
        "| 错误类型 | 错误轮次数 | 正确处置轮次数 | 错误处置成功率 |",
        "| --- | ---: | ---: | ---: |",
    ])
    error_labels = (
        ("tool_execution", "工具执行错误"),
        ("action_or_parameter", "动作或参数校验错误"),
        ("llm_output", "大模型输出错误"),
        ("network_or_infrastructure", "网络与基础设施错误"),
    )
    for key, label in error_labels:
        item = error["categories"][key]
        reviewed = error["reviewed_turns"] > 0
        lines.append(
            f"| {label} | {item['error_turns'] if reviewed else '待标注'} "
            f"| {item['correctly_handled_turns'] if reviewed else '待标注'} "
            f"| {_percent(item['handling_success_rate'])} |"
        )
    lines.append(
        f"| 总体 | {error['error_turns'] if error['reviewed_turns'] else '待标注'} "
        f"| {error['correctly_handled_turns'] if error['reviewed_turns'] else '待标注'} "
        f"| {_percent(error['handling_success_rate'])} |"
    )
    lines.extend([
        "",
        "## 端到端效率",
        "",
        "| 场景 | 样本数 | $P_{50}$延迟 | $P_{95}$延迟 | 最大延迟 | 平均Token | 平均成本 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    scenario_labels = (
        ("deterministic_direct", "确定性直连"),
        ("bounded_investigation", "有界调查"),
        ("multi_tool_investigation", "多工具调查"),
        ("boundary_or_insufficient", "证据不足或拒答"),
    )
    for key, label in scenario_labels:
        item = runtime["scenarios"].get(key, {})
        lines.append(
            f"| {label} | {item.get('sample_count', 0)} | {_seconds(item.get('p50_latency_ms'))} "
            f"| {_seconds(item.get('p95_latency_ms'))} | {_seconds(item.get('max_latency_ms'))} "
            f"| {_number(item.get('average_tokens'))} | {_number(item.get('average_cost'))} |"
        )
    lines.extend([
        "",
        "## 竞赛验收汇总",
        "",
        "| 竞赛技术要求 | 实验结果 | 验收目标 | 验收结论 |",
        "| --- | ---: | ---: | --- |",
        "| 0.5M Tokens、10轮以上多轮问答的回答准确率 | 待专项实验 | ≥90% | 待评测 |",
        "| 0.5M Tokens、10轮以上多轮问答的关键事实召回率 | 待专项实验 | ≥90% | 待评测 |",
        f"| 外部工具调用精确率 | {_percent(tool['overall'].get('precision'))} | ≥92% | {_conclusion(tool['overall'].get('precision'), 0.92)} |",
        f"| 轨迹可检测错误的自动处置率（自纠错成功率） | {_percent(error.get('handling_success_rate'))} | ≥80% | {_conclusion(error.get('handling_success_rate'), 0.80)} |",
        f"| 深度大于3层的股权穿透完整严格准确率 | {_percent(ownership_accuracy)} | ≥85% | {_conclusion(ownership_accuracy, 0.85)} |",
        f"| 事件关键节点召回率及因果、时序一致性 | {event_result} | 召回率≥85%，无明显逻辑断裂 | {event_conclusion} |",
        f"| 股权穿透与事件脉络工具执行时间 | {performance_result} | ≤5秒 | {performance_conclusion} |",
        "| 财务风险预警微平均F1 | 待专项实验 | ≥85% | 待评测 |",
        "| 财务排雷报告专家盲评优秀率 | 待专家盲评 | ≥80% | 待评测 |",
        "",
    ])
    return "\n".join(lines)


def _load_children(connection, table: str, run_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not run_ids:
        return {}
    for start in range(0, len(run_ids), 500):
        batch = run_ids[start:start + 500]
        marks = ",".join("?" for _ in batch)
        for row in connection.execute(
            f"SELECT * FROM {table} WHERE run_id IN ({marks}) ORDER BY run_id, sequence", batch,
        ):
            item = dict(row)
            result[str(item["run_id"])].append(item)
    return dict(result)


def _attempt_errors(attempt: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for raw in _loads(attempt.get("errors_json"), []) or []:
        detail = raw if isinstance(raw, str) else " | ".join(
            str(raw.get(key) or "") for key in ("stage", "error_type", "message")
        ).strip(" |")
        findings.append({"category": _error_category(detail), "detail": detail})
    for raw in _loads(attempt.get("failed_actions_json"), []) or []:
        detail = _json_cell(raw)
        findings.append({"category": "action_or_parameter", "detail": detail})
    for tool in attempt.get("tools", []):
        if str(tool.get("status") or "").lower() in {"failed", "error", "invalid"}:
            detail = f"{tool.get('tool_name')}.{tool.get('operation')}: {tool.get('status')}"
            findings.append({"category": _error_category(detail), "detail": detail})
    for call in attempt.get("llm_calls", []):
        if str(call.get("status") or "").lower() not in {"", "success", "completed"}:
            payload = _loads(call.get("payload_json"), {})
            detail = " | ".join(str(value) for value in (
                call.get("prompt_id"), call.get("status"), payload.get("error_type"),
                payload.get("error_message"),
            ) if value)
            findings.append({"category": _error_category(detail), "detail": detail})
    unique = {(item["category"], item["detail"]): item for item in findings}
    return list(unique.values())


def _error_category(detail: str) -> str:
    value = detail.lower()
    if any(token in value for token in (
        "timeout", "proxy", "connection", "network", "http", "ssl", "rate limit", "readtimeout",
    )):
        return "network_or_infrastructure"
    if any(token in value for token in ("argument", "validation", "validate", "action_repair", "invalid_action")):
        return "action_or_parameter"
    if any(token in value for token in ("tool", "execute_one_tool", "data_not_available")):
        return "tool_execution"
    return "llm_output"


def _scenario(case: dict[str, Any], tool_count: int) -> str:
    status = str(case.get("answer_status") or "")
    mode = str(case.get("routing_mode") or "")
    if status in {"unsupported", "clarification_required", "insufficient_evidence"}:
        return "boundary_or_insufficient"
    if mode == "direct":
        return "deterministic_direct"
    if mode == "investigation" and tool_count >= 2:
        return "multi_tool_investigation"
    if mode == "investigation":
        return "bounded_investigation"
    return "other"


def _tool_domain(tool_name: str) -> str:
    if tool_name == "financial_analysis":
        return "financial"
    if tool_name == "ownership_analysis":
        return "ownership"
    if tool_name == "event_timeline":
        return "event"
    if tool_name in {"document_search", "research_analysis"}:
        return "document_and_research"
    return "other"


def _tool_recall_counts(rows: list[dict[str, str]], domain: str) -> tuple[int, int]:
    required = covered = 0
    seen: set[str] = set()
    field_domain = "document_research" if domain == "document_and_research" else domain
    required_field = f"required_{field_domain}_calls"
    covered_field = f"covered_{field_domain}_calls"
    for row in rows:
        case_id = row.get("case_id", "")
        if case_id in seen:
            continue
        seen.add(case_id)
        required += _optional_int(row.get(required_field)) or 0
        covered += _optional_int(row.get(covered_field)) or 0
    return required, covered


def _validate_review_rows(
    answers: list[dict[str, str]], tools: list[dict[str, str]], errors: list[dict[str, str]],
) -> dict[str, Any]:
    issues: list[str] = []
    for row in answers:
        necessary = _optional_int(row.get("necessary_fact_count"))
        hits = _optional_int(row.get("hit_fact_count"))
        if necessary is not None and hits is not None and (necessary < 0 or hits < 0 or hits > necessary):
            issues.append(f"{row.get('case_id')}: fact counts must satisfy 0 <= hit <= necessary")
    domains = ("financial", "ownership", "event", "document_research")
    seen: set[str] = set()
    for row in tools:
        case_id = row.get("case_id", "")
        if case_id in seen:
            continue
        seen.add(case_id)
        for domain in domains:
            required = _optional_int(row.get(f"required_{domain}_calls"))
            covered = _optional_int(row.get(f"covered_{domain}_calls"))
            if required is not None and covered is not None and (
                required < 0 or covered < 0 or covered > required
            ):
                issues.append(
                    f"{case_id}: {domain} counts must satisfy 0 <= covered <= required"
                )
    for row in errors:
        is_error = _truth(row.get("manual_is_runtime_error"))
        handled = _truth(row.get("manual_correctly_handled"))
        if is_error is False and handled is True:
            issues.append(f"{row.get('case_id')}: a non-error turn cannot be marked handled")
    return {"issue_count": len(issues), "issues": issues[:200]}


def _duration_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"sample_count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(values)
    return {
        "sample_count": len(values),
        "p50_ms": round(_percentile(ordered, 0.50), 2),
        "p95_ms": round(_percentile(ordered, 0.95), 2),
        "max_ms": round(max(ordered), 2),
    }


def _specialty_experiments(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "specialty_tool_benchmark.json"
    result: dict[str, Any] = {
        "long_context_0_5m": None,
        "ownership_penetration": None,
        "event_timeline": None,
        "financial_risk": None,
        "expert_review": None,
        "note": "Unfilled items require separate reference labels or controlled experiments.",
    }
    if not path.is_file():
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result

    ownership = payload.get("ownership_penetration")
    ownership_depth_3 = payload.get("ownership_penetration_depth_3")
    event_query = payload.get("event_query")
    event_cluster = payload.get("event_cluster")
    competition = payload.get("competition_result")
    if isinstance(ownership, dict):
        result["ownership_penetration"] = _without_cases(ownership)
    if isinstance(ownership_depth_3, dict):
        result["ownership_penetration_depth_3"] = _without_cases(ownership_depth_3)
    if isinstance(event_query, dict) or isinstance(event_cluster, dict):
        result["event_timeline"] = {
            "event_query": _without_cases(event_query) if isinstance(event_query, dict) else None,
            "event_cluster": _without_cases(event_cluster) if isinstance(event_cluster, dict) else None,
        }
    if isinstance(competition, dict):
        result["tool_performance_acceptance"] = competition
    strict_path = output_dir / "ownership_strict_summary.json"
    strict_payload = None
    if strict_path.is_file():
        try:
            strict_payload = json.loads(strict_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            strict_payload = None
    if isinstance(strict_payload, dict):
        result["ownership_strict_accuracy"] = strict_payload
    event_quality_path = output_dir / "event_quality_summary.json"
    if event_quality_path.is_file():
        try:
            event_quality_payload = json.loads(
                event_quality_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            event_quality_payload = None
        if isinstance(event_quality_payload, dict):
            result["event_quality"] = event_quality_payload
    result["source"] = path.name
    return result


def _without_cases(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "cases"}


def _percentile(ordered: list[float], quantile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _token_cost(prompt_tokens: int, completion_tokens: int) -> tuple[float | None, float]:
    try:
        input_price = float(os.getenv("FINTRACE_QWEN_INPUT_PRICE_PER_MILLION", ""))
        output_price = float(os.getenv("FINTRACE_QWEN_OUTPUT_PRICE_PER_MILLION", ""))
    except ValueError:
        return None, 0.0
    return prompt_tokens * input_price / 1_000_000, completion_tokens * output_price / 1_000_000


def _write_review_csv(
    path: Path, rows: list[dict[str, Any]], manual_fields: Iterable[str],
    *, key_fields: tuple[str, ...],
) -> None:
    previous: dict[tuple[str, ...], dict[str, str]] = {}
    if path.exists():
        for row in _read_csv(path):
            previous[tuple(row.get(field, "") for field in key_fields)] = row
    for row in rows:
        old = previous.get(tuple(str(row.get(field, "")) for field in key_fields), {})
        for field in manual_fields:
            if old.get(field, "").strip():
                row[field] = old[field]
    _write_csv(path, rows)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write an empty worksheet: {path.name}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _canonical_json(value: Any, default: Any) -> str:
    return _json_cell(_loads(value, default))


def _json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _truth(value: Any) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"yes", "true", "1", "correct", "pass", "是", "正确", "通过"}:
        return True
    if normalized in {"no", "false", "0", "incorrect", "fail", "否", "错误", "不通过"}:
        return False
    return None


def _count_truth_labels(rows: list[dict[str, str]], field: str) -> int:
    return sum(_truth(row.get(field)) is not None for row in rows)


def _split_categories(value: str) -> set[str]:
    return {item.strip() for item in value.replace(",", ";").split(";") if item.strip()}


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _decimal(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _percent(value: Any) -> str:
    return "待标注" if value is None else f"{float(value):.2%}"


def _seconds(value: Any) -> str:
    return "-" if value is None else f"{float(value) / 1000:.2f}s"


def _specialty_performance_cells(specialty: dict[str, Any]) -> tuple[str, str]:
    acceptance = specialty.get("tool_performance_acceptance") or {}
    ownership_ms = acceptance.get("ownership_penetration_p95_ms")
    event_ms = acceptance.get("event_timeline_p95_ms")
    threshold_ms = acceptance.get("threshold_ms", 5000)
    if ownership_ms is None or event_ms is None:
        return "待受控性能实验", "待评测"
    result = f"{float(ownership_ms) / 1000:.3f}s；{float(event_ms) / 1000:.3f}s"
    passed = float(ownership_ms) <= float(threshold_ms) and float(event_ms) <= float(threshold_ms)
    return result, "达到" if passed else "未达到"


def _number(value: Any) -> str:
    return "-" if value is None else f"{float(value):.2f}"


def _conclusion(value: Any, target: float) -> str:
    if value is None:
        return "待评测"
    return "达到" if float(value) >= target else "未达到"


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
            check=True, capture_output=True, text=True,
        ).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "aggregate"):
        command = commands.add_parser(name)
        command.add_argument("--batch-id", required=True)
        command.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_batch(args.batch_id, args.output_root)
    else:
        result = aggregate_batch(args.batch_id, args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

import csv
import json
from datetime import UTC, datetime

from evaluation.analysis.report_batch import aggregate_batch, prepare_batch
from evaluation.runner import repository
from harness.tracing.store import import_payload
from harness.tracing.users import ensure_user


def _seed_batch(tmp_path):
    dataset = tmp_path / "questions.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    batch_id = "EVAL-TEST-ANALYSIS"
    user_id = "USER-EVAL-TEST-ANALYSIS"
    ensure_user(user_id, "Evaluation analysis")
    repository.create_batch({
        "batch_id": batch_id,
        "dataset_path": str(dataset),
        "dataset_sha256": "abc",
        "evaluation_user_id": user_id,
        "knowledge_cutoff": "2026-05-28",
        "agent_version": "test",
        "created_at": datetime.now(UTC).isoformat(),
    }, [{
        "case_id": "SESSION-001-TURN-001",
        "source_session_id": "1",
        "agent_session_id": f"{batch_id}-SESSION-001",
        "expected_turn_id": 1,
        "question": "测试问题",
        "annotation": {
            "answerability": "answerable",
            "required_entities": ["600519.SH"],
            "required_date": None,
            "valid_tools": ["financial_analysis.metric_query"],
            "required_chunk_ids": [],
        },
    }])
    import_payload({
        "run_id": "RUN-ANALYSIS",
        "trace_id": "TRACE-ANALYSIS",
        "user_id": user_id,
        "session_id": f"{batch_id}-SESSION-001",
        "turn_id": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "query": "测试问题",
        "answer": "测试答案",
        "final_answer_raw": json.dumps({
            "answer": "测试答案", "used_evidence_ids": ["EV-1"],
        }, ensure_ascii=False),
        "answer_status": "answered",
        "routing_mode": "direct",
        "workflow_status": "completed",
        "knowledge_cutoff": "2026-05-28",
        "parsed_request": {"entities": ["600519.SH"], "task_family": "financial_query"},
        "tool_calls": [{
            "tool_call_id": "TC-1",
            "tool_name": "financial_analysis",
            "operation": "metric_query",
            "arguments": {"company_ids": ["600519.SH"]},
            "reason": "query metric",
        }],
        "tool_results": [{
            "status": "success",
            "metrics": {"execution_time_ms": 12},
            "evidence": [],
        }],
        "evidence": [{
            "evidence_id": "EV-1",
            "evidence_type": "financial_metric",
            "support_level": "strong",
            "source": {"company_id": "600519.SH"},
            "fact": {"value": 1},
        }],
        "llm_calls": [{
            "prompt_id": "fintrace.final_answer",
            "prompt_version": "1.0",
            "model": "qwen-test",
            "status": "success",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "latency_ms": 30,
        }],
        "latency_ms": 100,
    })
    repository.mark_case_completed(batch_id, "SESSION-001-TURN-001", "RUN-ANALYSIS")
    repository.refresh_batch_status(batch_id)
    return batch_id


def test_prepare_exports_review_sheets_and_runtime_metrics(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    batch_id = _seed_batch(tmp_path)
    output_root = tmp_path / "results"

    result = prepare_batch(batch_id, output_root)

    assert result["case_count"] == 1
    output = output_root / batch_id
    assert (output / "run_summary.json").exists()
    assert (output / "table_metrics.json").exists()
    assert (output / "whitepaper_tables.md").exists()
    with (output / "answer_review.csv").open(encoding="utf-8-sig", newline="") as handle:
        answer = next(csv.DictReader(handle))
    assert answer["question"] == "测试问题"
    assert answer["used_evidence_ids"] == '["EV-1"]'
    with (output / "tool_review.csv").open(encoding="utf-8-sig", newline="") as handle:
        tool = next(csv.DictReader(handle))
    assert tool["qualified_operation"] == "financial_analysis.metric_query"
    assert tool["auto_matches_acceptable_tool"] == "yes"


def test_aggregate_uses_only_completed_manual_labels(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    batch_id = _seed_batch(tmp_path)
    output_root = tmp_path / "results"
    prepare_batch(batch_id, output_root)
    output = output_root / batch_id

    _edit_first_row(output / "answer_review.csv", {
        "final_correct": "yes", "necessary_fact_count": "2", "hit_fact_count": "1",
    })
    _edit_first_row(output / "tool_review.csv", {
        "final_call_correct": "yes", "manual_parameters_correct": "yes",
        "required_financial_calls": "1", "covered_financial_calls": "1",
    })

    aggregate_batch(batch_id, output_root)
    metrics = json.loads((output / "table_metrics.json").read_text(encoding="utf-8"))
    assert metrics["answer_quality"]["answer_accuracy"] == 1.0
    assert metrics["answer_quality"]["key_fact_recall"] == 0.5
    assert metrics["tool_quality"]["overall"]["precision"] == 1.0
    assert metrics["tool_quality"]["financial"]["recall"] == 1.0
    assert metrics["error_handling"]["error_rate"] == 0.0
    assert metrics["runtime"]["token_bands"][0]["turn_count"] == 1


def test_prepare_preserves_existing_manual_labels(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINTRACE_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    batch_id = _seed_batch(tmp_path)
    output_root = tmp_path / "results"
    prepare_batch(batch_id, output_root)
    answer_path = output_root / batch_id / "answer_review.csv"
    _edit_first_row(answer_path, {"final_correct": "yes", "reviewer_notes": "checked"})

    prepare_batch(batch_id, output_root)

    with answer_path.open(encoding="utf-8-sig", newline="") as handle:
        answer = next(csv.DictReader(handle))
    assert answer["final_correct"] == "yes"
    assert answer["reviewer_notes"] == "checked"


def _edit_first_row(path, changes):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = reader.fieldnames
    rows[0].update(changes)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

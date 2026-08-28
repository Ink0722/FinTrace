import json
import sqlite3

from evaluation.analysis.report_batch import (
    _specialty_experiments,
    _specialty_performance_cells,
)
from evaluation.analysis.specialty_tool_benchmark import (
    select_event_cases,
    select_ownership_cases,
    summarize_results,
)


def test_select_ownership_cases_balances_depths(tmp_path) -> None:
    index_path = tmp_path / "ownership.sqlite"
    with sqlite3.connect(index_path) as connection:
        connection.executescript(
            """
            CREATE TABLE holder_records (
                target_company_id TEXT, holder_end_date TEXT,
                announcement_date TEXT, holder_entity_id TEXT
            );
            CREATE TABLE holder_company_links (
                holder_entity_id TEXT, company_id TEXT
            );
            """
        )
        for index in range(6):
            connection.execute(
                "INSERT INTO holder_company_links VALUES (?, ?)",
                (f"H{index}", f"C{index}"),
            )
            connection.execute(
                "INSERT INTO holder_records VALUES (?, ?, ?, ?)",
                (f"C{index + 1}", "2025-12-31", "2026-03-01", f"H{index}"),
            )

    cases = select_ownership_cases(index_path, cutoff="2026-05-28", sample_size=3)

    assert [case["expected_depth"] for case in cases] == [4, 5, 6]
    assert all(case["source_entity_id"] == "C0" for case in cases)

    depth_3_cases = select_ownership_cases(
        index_path, cutoff="2026-05-28", sample_size=1, depths=(3,)
    )
    assert [case["expected_depth"] for case in depth_3_cases] == [3]


def test_select_event_cases_prefers_larger_event_sets(tmp_path) -> None:
    index_path = tmp_path / "events.sqlite"
    with sqlite3.connect(index_path) as connection:
        connection.execute(
            """
            CREATE TABLE events (
                company_id TEXT, event_date TEXT, announcement_date TEXT
            )
            """
        )
        for company_id, count in (("C1", 2), ("C2", 4), ("C3", 3), ("C4", 1)):
            for day in range(1, count + 1):
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?)",
                    (company_id, f"2026-01-{day:02d}", f"2026-01-{day:02d}"),
                )

    cases = select_event_cases(index_path, cutoff="2026-05-28", sample_size=3)

    assert [case["company_id"] for case in cases] == ["C2", "C3", "C1"]
    assert [case["available_event_count"] for case in cases] == [4, 3, 2]


def test_summarize_results_reports_latency_distribution() -> None:
    results = [
        {"status": "success", "execution_time_ms": value}
        for value in (1, 2, 3, 4, 5)
    ]

    summary = summarize_results(results)

    assert summary == {
        "sample_count": 5,
        "success_count": 5,
        "failure_count": 0,
        "p50_ms": 3.0,
        "p95_ms": 4.8,
        "max_ms": 5.0,
    }


def test_specialty_results_are_loaded_without_case_details(tmp_path) -> None:
    payload = {
        "ownership_penetration_depth_3": {
            "sample_count": 30, "p95_ms": 200, "cases": [{"id": 0}]
        },
        "ownership_penetration": {"sample_count": 30, "p95_ms": 300, "cases": [{"id": 1}]},
        "event_query": {"sample_count": 30, "p95_ms": 7, "cases": [{"id": 2}]},
        "event_cluster": {"sample_count": 30, "p95_ms": 6, "cases": [{"id": 3}]},
        "competition_result": {"threshold_ms": 5000},
    }
    (tmp_path / "specialty_tool_benchmark.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    strict_summary = {
        "groups": {
            "depth_3": {"sample_count": 30, "strict_accuracy": 1.0},
            "depth_gt_3": {"sample_count": 30, "strict_accuracy": 0.9},
        }
    }
    (tmp_path / "ownership_strict_summary.json").write_text(
        json.dumps(strict_summary), encoding="utf-8"
    )
    event_quality = {
        "key_event_recall": 1.0,
        "cluster_pairwise_f1": 0.48,
        "severe_temporal_break_count": 0,
    }
    (tmp_path / "event_quality_summary.json").write_text(
        json.dumps(event_quality), encoding="utf-8"
    )

    result = _specialty_experiments(tmp_path)

    assert result["ownership_penetration"] == {"sample_count": 30, "p95_ms": 300}
    assert result["ownership_penetration_depth_3"] == {
        "sample_count": 30,
        "p95_ms": 200,
    }
    assert result["event_timeline"]["event_cluster"] == {
        "sample_count": 30,
        "p95_ms": 6,
    }
    assert result["tool_performance_acceptance"] == {"threshold_ms": 5000}
    assert result["ownership_strict_accuracy"] == strict_summary
    assert result["event_quality"] == event_quality


def test_specialty_performance_uses_both_tool_thresholds() -> None:
    result, conclusion = _specialty_performance_cells(
        {
            "tool_performance_acceptance": {
                "ownership_penetration_p95_ms": 333.2,
                "event_timeline_p95_ms": 7.1,
                "threshold_ms": 5000,
            }
        }
    )

    assert result == "0.333s；0.007s"
    assert conclusion == "达到"

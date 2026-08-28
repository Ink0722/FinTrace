from __future__ import annotations

import pytest

from evaluation.analysis.financial_quality_review import (
    score_report_reviews,
    score_risk_reviews,
    summarize_report_scores,
    summarize_risk_scores,
    validate_report_reviews,
    validate_risk_reviews,
)


def _risk_input(rule_id: str = "RULE-1") -> dict:
    return {
        "case_id": "CASE-1",
        "rule_id": rule_id,
        "review_packet_id": "PACKET-RISK-1",
        "metrics": [
            {"metric_key": "REVENUE@2024-12-31", "value": 100},
            {"metric_key": "REVENUE@2025-12-31", "value": 120},
        ],
    }


def _risk_review(label: str = "positive", rule_id: str = "RULE-1") -> dict:
    return {
        "case_id": "CASE-1",
        "rule_id": rule_id,
        "review_packet_id": "PACKET-RISK-1",
        "reference_label": label,
        "supporting_metric_keys": ["REVENUE@2024-12-31"],
        "reason": "calculated against the threshold",
    }


def test_validate_risk_review_rejects_unknown_metric_key() -> None:
    review = _risk_review()
    review["supporting_metric_keys"] = ["MISSING@2024-12-31"]

    with pytest.raises(ValueError, match="unknown supporting_metric_keys"):
        validate_risk_reviews([_risk_input()], [review])


def test_risk_scoring_counts_system_not_evaluable_as_false_negative() -> None:
    reviews = validate_risk_reviews([_risk_input()], [_risk_review("positive")])
    rows = score_risk_reviews(reviews, {("CASE-1", "RULE-1"): "not_evaluable"})
    summary = summarize_risk_scores(rows)["micro"]

    assert summary["fn"] == 1
    assert summary["recall"] == 0.0
    assert summary["system_not_evaluable_count"] == 1


def test_not_evaluable_reference_is_excluded_from_binary_metrics() -> None:
    reviews = validate_risk_reviews(
        [_risk_input()], [_risk_review("not_evaluable")]
    )
    rows = score_risk_reviews(reviews, {("CASE-1", "RULE-1"): "positive"})
    summary = summarize_risk_scores(rows)["micro"]

    assert summary["scored_count"] == 0
    assert summary["excluded_not_evaluable_count"] == 1
    assert summary["tp"] == summary["fp"] == summary["fn"] == 0


def _report_input() -> dict:
    return {
        "case_id": "CASE-1",
        "review_packet_id": "PACKET-REPORT-1",
        "final_report": "report",
    }


def _report_review() -> dict:
    return {
        "case_id": "CASE-1",
        "review_packet_id": "PACKET-REPORT-1",
        "scores": {
            "data_and_citations": 4,
            "logical_consistency": 5,
            "financial_professionalism": 4,
            "completeness_and_usability": 4,
        },
        "veto_errors": [],
        "review_reason": "accurate and complete",
    }


def test_report_excellent_requires_all_dimensions_and_no_veto() -> None:
    valid = validate_report_reviews([_report_input()], [_report_review()])
    rows = score_report_reviews(valid)
    assert rows[0]["excellent"] == "yes"
    assert summarize_report_scores(rows)["excellent_rate"] == 1.0

    review = _report_review()
    review["veto_errors"] = ["fraud_overstatement"]
    valid = validate_report_reviews([_report_input()], [review])
    assert score_report_reviews(valid)[0]["excellent"] == "no"


def test_report_review_rejects_non_integer_score_and_unknown_veto() -> None:
    review = _report_review()
    review["scores"]["logical_consistency"] = 4.5
    with pytest.raises(ValueError, match="logical_consistency"):
        validate_report_reviews([_report_input()], [review])

    review = _report_review()
    review["veto_errors"] = ["not_a_veto"]
    with pytest.raises(ValueError, match="unknown veto_errors"):
        validate_report_reviews([_report_input()], [review])


def test_reviews_reject_stale_packet_ids() -> None:
    risk_review = _risk_review()
    risk_review["review_packet_id"] = "OLD"
    with pytest.raises(ValueError, match="review_packet_id"):
        validate_risk_reviews([_risk_input()], [risk_review])

    report_review = _report_review()
    report_review["review_packet_id"] = "OLD"
    with pytest.raises(ValueError, match="review_packet_id"):
        validate_report_reviews([_report_input()], [report_review])

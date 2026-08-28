import pytest

from evaluation.analysis.event_quality_review import (
    _deterministic_checks,
    score_case,
    validate_review,
)


def _case() -> dict:
    return {
        "case_id": "EVENT-QUALITY-001",
        "company_id": "600001.SH",
        "events": [
            {
                "event_id": "E1",
                "event_date": "2025-01-01",
                "announcement_date": "2025-01-01",
            },
            {
                "event_id": "E2",
                "event_date": "2025-01-02",
                "announcement_date": "2025-01-02",
            },
            {
                "event_id": "E3",
                "event_date": "2025-02-01",
                "announcement_date": "2025-02-01",
            },
        ],
        "system_clusters": [
            {"cluster_id": "C1", "event_ids": ["E1", "E2"]},
            {"cluster_id": "C2", "event_ids": ["E3"]},
        ],
        "system_relations": [
            {
                "source_event_id": "E2",
                "target_event_id": "E1",
                "relation_type": "RESPONDS_TO",
            }
        ],
        "deterministic_checks": {
            "event_ids_unique": True,
            "all_events_clustered_once": True,
            "temporal_violation_count": 0,
            "cutoff_violation_count": 0,
        },
    }


def _review() -> dict:
    return {
        "case_id": "EVENT-QUALITY-001",
        "company_id": "600001.SH",
        "key_event_ids": ["E1", "E3"],
        "reference_clusters": [
            {
                "reference_cluster_id": "R1",
                "event_ids": ["E1", "E2"],
                "reason": "same inquiry",
            },
            {
                "reference_cluster_id": "R2",
                "event_ids": ["E3"],
                "reason": "separate event",
            },
        ],
        "relation_reviews": [
            {
                "source_event_id": "E2",
                "target_event_id": "E1",
                "relation_type": "RESPONDS_TO",
                "verdict": "supported",
                "reason": "explicit response",
            }
        ],
        "severe_temporal_break": False,
        "review_notes": "checked",
    }


def test_valid_review_scores_key_events_clusters_and_relations() -> None:
    case = _case()
    review = validate_review(case, _review())

    score = score_case(case, review)

    assert score["key_event_recall"] == 1.0
    assert score["pairwise_precision"] == 1.0
    assert score["pairwise_recall"] == 1.0
    assert score["pairwise_f1"] == 1.0
    assert score["supported_relation_count"] == 1


def test_review_rejects_duplicate_or_incomplete_cluster_coverage() -> None:
    case = _case()
    review = _review()
    review["reference_clusters"][1]["event_ids"] = ["E2"]

    with pytest.raises(ValueError, match="multiple reference clusters"):
        validate_review(case, review)


def test_review_rejects_missing_relation_review() -> None:
    review = _review()
    review["relation_reviews"] = []

    with pytest.raises(ValueError, match="relation review coverage mismatch"):
        validate_review(_case(), review)


def test_deterministic_checks_find_order_cutoff_and_relation_direction_errors() -> None:
    events = [
        {"event_id": "E2", "event_date": "2025-01-02", "announcement_date": "2026-06-01"},
        {"event_id": "E1", "event_date": "2025-01-01", "announcement_date": "2025-01-01"},
    ]
    clusters = [{"cluster_id": "C1", "event_ids": ["E2", "E1"]}]
    relations = [
        {
            "source_event_id": "E2",
            "target_event_id": "E1",
            "relation_type": "FOLLOWED_BY",
        }
    ]

    checks = _deterministic_checks(
        events, clusters, relations, cutoff="2026-05-28"
    )

    assert checks["all_events_clustered_once"] is True
    assert checks["temporal_violation_count"] == 2
    assert checks["cutoff_violation_count"] == 1

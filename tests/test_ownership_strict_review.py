from copy import deepcopy

from evaluation.analysis.ownership_strict_review import (
    _deduplicate_review_file,
    _normalise_review,
    enumerate_reference_paths,
    score_paths,
)


def _edge(source: str, target: str, ratio: float, index: int) -> dict:
    return {
        "edge_id": f"R{index}",
        "source_entity_id": source,
        "source_name": source,
        "target_entity_id": target,
        "target_name": target,
        "relation_type": "OWNS",
        "holding_ratio": ratio,
        "holder_end_date": "2025-12-31",
        "announcement_date": "2026-03-01",
        "evidence_id": f"E{index}",
        "holder_name": source,
        "bridge_match_method": "exact_name",
    }


def _reference_path() -> dict:
    graph = {
        "A": [_edge("A", "B", 0.5, 1)],
        "B": [_edge("B", "C", 0.4, 2)],
        "C": [_edge("C", "D", 0.3, 3)],
    }
    return enumerate_reference_paths(
        graph,
        {item: item for item in "ABCD"},
        source_entity_id="A",
        target_entity_id="D",
        max_depth=3,
    )[0]


def test_reference_path_is_independently_enumerated() -> None:
    path = _reference_path()

    assert path["depth"] == 3
    assert [item["entity_id"] for item in path["nodes"]] == ["A", "B", "C", "D"]
    assert path["path_ratio"] == 0.06


def test_strict_score_passes_complete_matching_path() -> None:
    reference = _reference_path()
    actual = deepcopy(reference)
    for edge in actual["edges"]:
        edge.pop("holder_name")
        edge.pop("bridge_match_method")

    score = score_paths(
        {"status": "success", "expected_depth": 3}, [reference], [actual]
    )

    assert score["deterministic_pass"] is True
    assert score["failure_reasons"] == []


def test_strict_score_rejects_ratio_or_missing_path() -> None:
    reference = _reference_path()
    wrong_ratio = deepcopy(reference)
    wrong_ratio["edges"][1]["holding_ratio"] = 0.41

    ratio_score = score_paths(
        {"status": "success", "expected_depth": 3}, [reference], [wrong_ratio]
    )
    missing_score = score_paths(
        {"status": "success", "expected_depth": 3}, [reference], []
    )

    assert ratio_score["deterministic_pass"] is False
    assert "edge_ratios_correct" in ratio_score["failure_reasons"]
    assert missing_score["deterministic_pass"] is False
    assert "path_count_correct" in missing_score["failure_reasons"]


def test_llm_review_requires_every_semantic_check() -> None:
    case = {"case_id": "OWN-STRICT-D3-001"}
    record = {
        "entity_identity_consistent": True,
        "relation_semantics_correct": True,
        "source_support_consistent": True,
        "no_unsupported_relations": False,
        "llm_review_pass": True,
        "confidence": 0.9,
        "reason": "存在额外关系。",
    }

    result = _normalise_review(record, case)

    assert result["llm_review_pass"] is False


def test_duplicate_reviews_are_atomically_collapsed(tmp_path) -> None:
    path = tmp_path / "reviews.jsonl"
    path.write_text(
        '{"case_id":"A","evaluation_error":"timeout"}\n'
        '{"case_id":"A","llm_review_pass":true}\n'
        '{"case_id":"B","llm_review_pass":true}\n',
        encoding="utf-8",
    )

    records = _deduplicate_review_file(path)

    assert len(records) == 2
    assert records[0] == {"case_id": "A", "llm_review_pass": True}
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2

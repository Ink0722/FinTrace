import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from data_pipeline.events.build_index import build_event_index, classify_event
from schemas.enums import ToolName
from schemas.tool_calls import ToolCall
from tools.event_timeline.interface import event_timeline


@pytest.fixture
def event_index(monkeypatch):
    root = Path(".tmp_tests") / f"events_{uuid4().hex}"
    normalized = root / "normalized"
    index_path = root / "indexes" / "events.sqlite"
    normalized.mkdir(parents=True)
    rows = [
        _announcement("DOC-1", "000777.SZ", "2023-05-12", "关于收到年报问询函的公告"),
        _announcement("DOC-2", "000777.SZ", "2023-05-20", "关于回复年报问询函的公告"),
        _announcement("DOC-3", "000777.SZ", "2024-01-10", "关于收到行政处罚决定书的公告"),
        _announcement("DOC-4", "000888.SZ", "2023-05-15", "关于股份质押的公告"),
        _announcement("DOC-5", "000777.SZ", "2024-02-10", "日常经营情况公告"),
    ]
    source = normalized / "announcements.jsonl"
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    manifest = build_event_index(normalized, index_path)
    monkeypatch.setenv("FINTRACE_EVENT_NORMALIZED_DIR", str(normalized))
    monkeypatch.setenv("FINTRACE_EVENT_INDEX_PATH", str(index_path))
    try:
        yield index_path, manifest
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_event_index_classifies_supported_announcements(event_index) -> None:
    _, manifest = event_index
    assert manifest["stats"]["inserted_rows"] == 4
    assert manifest["stats"]["unclassified"] == 1
    assert classify_event("收到审计意见", []) == "audit_opinion"


def test_event_query_returns_ordered_events_and_evidence(event_index) -> None:
    result = event_timeline(_call({"operation": "event_query", "entity_ids": ["000777.SZ"], "event_types": ["regulatory_inquiry"]}))
    assert result.status.value == "success"
    assert [item["event_date"] for item in result.data["events"]] == ["2023-05-12", "2023-05-20"]
    assert result.data["clusters"] == []
    assert len(result.evidence) == 2
    assert all(item.fact["extraction_method"] == "announcement_title_rule" for item in result.evidence)


def test_event_cluster_groups_related_events_without_causality_claim(event_index) -> None:
    result = event_timeline(_call({"operation": "event_cluster", "entity_ids": ["000777.SZ"], "event_types": ["regulatory_inquiry"], "window_days": 30}))
    assert result.status.value == "success"
    assert len(result.data["clusters"]) == 1
    assert len(result.data["clusters"][0]["events"]) == 2
    assert any("not causality" in warning for warning in result.warnings)


def test_event_query_respects_knowledge_cutoff(event_index) -> None:
    result = event_timeline(_call({"operation": "event_query", "entity_ids": ["000777.SZ"], "knowledge_cutoff": "2023-12-31"}))
    assert result.status.value == "success"
    assert all(item["announcement_date"] <= "2023-12-31" for item in result.data["events"])


def test_event_query_empty_result_is_not_evidence_of_no_event(event_index) -> None:
    result = event_timeline(_call({"operation": "event_query", "entity_ids": ["000777.SZ"], "event_types": ["share_pledge"]}))
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"


def test_event_query_rejects_cluster_only_argument(event_index) -> None:
    result = event_timeline(_call({"operation": "event_query", "entity_ids": ["000777.SZ"], "window_days": 30}))
    assert result.status.value == "failed"
    assert result.error.error_type.value == "INVALID_ARGUMENT"


def test_missing_event_index_returns_build_instruction(monkeypatch) -> None:
    monkeypatch.setenv("FINTRACE_EVENT_INDEX_PATH", str(Path(".tmp_tests") / "missing-events.sqlite"))
    result = event_timeline(_call({"operation": "event_query", "entity_ids": ["000777.SZ"]}))
    assert result.status.value == "failed"
    assert "data_pipeline.events.build_index" in result.error.details["build_command"]


def _call(arguments: dict) -> ToolCall:
    return ToolCall(tool_call_id="CALL-EVENT-001", tool_name=ToolName.EVENT_TIMELINE, arguments=arguments, reason="test")


def _announcement(document_id: str, company_id: str, announcement_date: str, title: str) -> dict:
    return {"id": document_id, "object_id": "{" + document_id + "}", "s_info_windcode": company_id, "ann_dt": announcement_date, "n_info_title": title, "category_names": [], "document_path": f"data/source/announcements/{document_id}.txt"}

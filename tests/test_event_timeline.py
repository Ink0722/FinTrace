from datetime import date
from pathlib import Path
import shutil
from uuid import uuid4

from schemas.enums import ToolName
from schemas.tool_calls import ToolCall
from tools.event_timeline.interface import event_timeline
from tools.event_timeline.sample_data import load_sample_events
from tools.event_timeline.timeline import cluster_events, filter_events


def test_filter_events_by_company_and_type() -> None:
    events = filter_events(
        load_sample_events(),
        company_id="000001.SZ",
        event_types=["regulatory_inquiry"],
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
    )
    assert len(events) == 2
    assert all(event.event_type == "regulatory_inquiry" for event in events)


def test_cluster_events_merges_related_inquiries() -> None:
    events = filter_events(load_sample_events(), "000001.SZ", ["regulatory_inquiry"], None, None)
    clusters = cluster_events(events)
    assert len(clusters) == 1
    assert len(clusters[0].events) == 2


def test_event_timeline_tool_returns_evidence() -> None:
    result = event_timeline(
        ToolCall(
            tool_call_id="CALL-001",
            tool_name=ToolName.EVENT_TIMELINE,
            arguments={"company_id": "000001.SZ", "event_types": ["regulatory_inquiry"]},
            reason="test",
        )
    )
    assert result.status.value == "success"
    assert result.data["clusters"]
    assert result.evidence


def test_event_timeline_prefers_csv_data(monkeypatch) -> None:
    test_root = write_events_csv(
        [
            "event_id,company_id,event_date,event_type,title,description,entities,source_doc_id,source_path,page,evidence_id",
            "EVT-CSV-001,000777.SZ,2023-05-12,regulatory_inquiry,年报问询函,交易所要求说明存货跌价准备,000777.SZ;交易所,DOC-EVT-001,data/raw/inquiry.pdf,2,EVID-EVT-001",
            "EVT-CSV-002,000777.SZ,2023-05-20,regulatory_inquiry,问询回复,公司回复存货跌价准备问题,000777.SZ;交易所,DOC-EVT-002,data/raw/reply.pdf,3,EVID-EVT-002",
        ]
    )
    try:
        monkeypatch.setenv("EVENT_DATA_SOURCE", "csv")
        monkeypatch.setenv("EVENTS_PATH", str(test_root / "events.csv"))
        result = event_timeline(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.EVENT_TIMELINE,
                arguments={"company_id": "000777.SZ", "event_types": ["regulatory_inquiry"]},
                reason="test",
            )
        )
        assert result.status.value == "success"
        assert result.data["data_source"] == "csv"
        assert result.data["summary"]["event_count"] == 2
        assert len(result.data["clusters"]) == 1
        assert {item.evidence_id for item in result.evidence} == {"EVID-EVT-001", "EVID-EVT-002"}
        assert result.evidence[0].source.source_path in {"data/raw/inquiry.pdf", "data/raw/reply.pdf"}
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_event_csv_company_without_records_returns_error(monkeypatch) -> None:
    test_root = write_events_csv(
        [
            "event_id,company_id,event_date,event_type,title,description,entities,source_doc_id,evidence_id",
            "EVT-CSV-001,000777.SZ,2023-05-12,regulatory_inquiry,年报问询函,交易所要求说明存货跌价准备,000777.SZ,DOC-EVT-001,EVID-EVT-001",
        ]
    )
    try:
        monkeypatch.setenv("EVENT_DATA_SOURCE", "csv")
        monkeypatch.setenv("EVENTS_PATH", str(test_root / "events.csv"))
        result = event_timeline(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.EVENT_TIMELINE,
                arguments={"company_id": "000888.SZ"},
                reason="test",
            )
        )
        assert result.status.value == "failed"
        assert result.error.error_type.value == "DATA_NOT_AVAILABLE"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_event_csv_validation_error(monkeypatch) -> None:
    test_root = write_events_csv(
        [
            "event_id,company_id,event_date,event_type,title,description,entities,source_doc_id,evidence_id",
            ",000777.SZ,2023-05-12,regulatory_inquiry,,,000777.SZ,DOC-EVT-001,EVID-EVT-001",
        ]
    )
    try:
        monkeypatch.setenv("EVENT_DATA_SOURCE", "csv")
        monkeypatch.setenv("EVENTS_PATH", str(test_root / "events.csv"))
        result = event_timeline(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.EVENT_TIMELINE,
                arguments={"company_id": "000777.SZ"},
                reason="test",
            )
        )
        assert result.status.value == "failed"
        assert result.error.error_type.value == "VALIDATION_FAILED"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def write_events_csv(lines: list[str]) -> Path:
    test_root = Path(".tmp_tests") / f"events_csv_{uuid4().hex}"
    test_root.mkdir(parents=True, exist_ok=True)
    (test_root / "events.csv").write_text("\n".join(lines), encoding="utf-8")
    return test_root

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from data_pipeline.research_views.build_index import build_research_index, extract_claims, split_risks
from schemas.enums import ToolName
from schemas.tool_calls import ToolCall
from tools.research_analysis.interface import research_analysis


@pytest.fixture
def research_index(monkeypatch):
    root = Path(".tmp_tests") / f"research_{uuid4().hex}"
    source = root / "research_reports.jsonl"
    chunks = root / "chunks_v2.jsonl"
    index_path = root / "indexes" / "research_views.sqlite"
    root.mkdir(parents=True)
    rows = [
        report("R-1", "000777", "XSHE", "2024-04-01", "甲证券", "买入", "维持"),
        report("R-2", "000777", "XSHE", "2025-04-01", "乙证券", "增持", "上调"),
    ]
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    chunk_rows = []
    for row in rows:
        chunk_rows.append({
            "chunk_version": "chunks-v2", "chunk_id": f"RR-{row['report_id']}-C0001",
            "document_id": f"RR-{row['report_id']}", "chunk_index": 1,
            "section_title": None, "char_start": 0, "text": row["abstract"],
        })
    chunks.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in chunk_rows), encoding="utf-8")
    manifest = build_research_index(source, chunks, index_path)
    monkeypatch.setenv("FINTRACE_RESEARCH_SOURCE_PATH", str(source))
    monkeypatch.setenv("FINTRACE_RESEARCH_CHUNKS_PATH", str(chunks))
    monkeypatch.setenv("FINTRACE_RESEARCH_INDEX_PATH", str(index_path))
    try:
        yield manifest
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_extract_claims_keeps_opinions_attributed() -> None:
    claims = extract_claims(report("R-1", "000777", "XSHE", "2024-04-01", "甲证券", "买入", "维持"))
    assert {item["claim_type"] for item in claims} == {
        "investment_rating", "earnings_forecast", "risk_opinion",
        "analyst_judgment", "cited_fact",
    }
    assert all(item["source_span"] for item in claims)


def test_split_risks_drops_non_explanatory_fragments() -> None:
    assert split_risks("风险；需求不及预期；原材料价格上涨") == ["需求不及预期", "原材料价格上涨"]


def test_build_index_maps_claims_to_chunks(research_index) -> None:
    assert research_index["stats"]["view_rows"] == 2
    assert research_index["stats"]["claim_rows"] == 12
    assert research_index["stats"]["claims_with_chunk"] == 8


def test_view_query_filters_claim_type_and_cutoff(research_index) -> None:
    result = research_analysis(call({
        "operation": "view_query", "company_ids": ["000777.SZ"],
        "claim_types": ["risk_opinion"], "knowledge_cutoff": "2024-12-31",
    }))
    assert result.status.value == "success"
    assert result.data["claim_count"] == 2
    assert all(item["institution"] == "甲证券" for item in result.data["claims"])
    assert result.evidence[0].evidence_type == "research_risk_opinion"
    assert result.evidence[0].fact["epistemic_status"] == "attributed_research_view"
    assert result.evidence[0].source.row_id == "RR-R-1-C0001"


def test_view_query_empty_does_not_invent_opinion(research_index) -> None:
    result = research_analysis(call({
        "operation": "view_query", "company_ids": ["600519.SH"],
    }))
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"


def test_view_query_rejects_blank_company_ids(research_index) -> None:
    result = research_analysis(call({"operation": "view_query", "company_ids": [" "]}))
    assert result.status.value == "failed"
    assert result.error.error_type.value == "INVALID_ARGUMENT"


def call(arguments: dict) -> ToolCall:
    return ToolCall(
        tool_call_id="CALL-RESEARCH-001", tool_name=ToolName.RESEARCH_ANALYSIS,
        arguments=arguments, reason="test",
    )


def report(report_id, code, exchange, publish_date, institution, rating, change):
    return {
        "report_id": report_id, "sec_code": code, "exchange_code": exchange,
        "publish_date": publish_date, "org_name": institution, "author": "张三,李四",
        "title": f"示例公司：经营改善支撑业绩增长",
        "report_type": "公司研究", "report_sub_type": "业绩点评",
        "rating_org": rating, "rating_change": change,
        "abstract": (
            "事件：公司发布年度报告，实现营业收入增长。"
            "盈利预测与投资评级：预计2025年归母净利润为10亿元，维持“买入”评级。"
            "风险提示：需求不及预期、行业竞争加剧。"
        ),
    }

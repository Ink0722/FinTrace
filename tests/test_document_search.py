from datetime import date
import json
from pathlib import Path
import shutil
from uuid import uuid4

from schemas.enums import ToolName
from schemas.tool_calls import ToolCall
from knowledge_base.document_ingestion.build_kb import main as build_kb_main
from tools.document_search.interface import document_search
from tools.document_search.sample_data import load_sample_chunks
from tools.document_search.search import bm25_search, filter_chunks


def test_filter_chunks_by_company_and_type() -> None:
    chunks = filter_chunks(
        load_sample_chunks(),
        company_id="000001.SZ",
        document_types=["regulatory_inquiry"],
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
    )
    assert len(chunks) == 1
    assert chunks[0].document_id == "INQUIRY-2023"


def test_bm25_search_returns_relevant_chunk() -> None:
    hits = bm25_search(load_sample_chunks(), query="存货 跌价准备 问询", top_k=3)
    assert hits
    assert hits[0].chunk.company_id == "000001.SZ"
    assert "存货" in hits[0].chunk.text


def test_document_search_tool_returns_evidence() -> None:
    result = document_search(
        ToolCall(
            tool_call_id="CALL-001",
            tool_name=ToolName.DOCUMENT_SEARCH,
            arguments={
                "company_id": "000001.SZ",
                "query": "存货 跌价准备 问询",
                "document_types": ["annual_report_note", "audit_report", "regulatory_inquiry"],
                "top_k": 3,
            },
            reason="test",
        )
    )
    assert result.status.value == "success"
    assert result.data["hits"]
    assert result.evidence


def test_document_search_prefers_local_knowledge_base(monkeypatch) -> None:
    kb_path = Path("data/knowledge_base/fintrace_kb.sqlite")
    monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_path))
    result = document_search(
        ToolCall(
            tool_call_id="CALL-001",
            tool_name=ToolName.DOCUMENT_SEARCH,
            arguments={
                "company_id": "000001.SZ",
                "query": "知识库专用词",
                "top_k": 3,
            },
            reason="test",
        )
    )
    assert result.status.value == "success"


def test_build_kb_and_search_txt_document(monkeypatch) -> None:
    test_root = Path(".tmp_tests") / f"document_kb_{uuid4().hex}"
    raw_dir = test_root / "raw_documents/test_company/inquiry_letter"
    kb_dir = test_root / "knowledge_base"
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_file = raw_dir / "000001.SZ_2023-05-12_inquiry_letter.txt"
    source_file.write_text("监管问询函要求公司说明存货跌价准备是否充分。", encoding="utf-8")
    try:
        exit_code = build_kb_main(["--raw-dir", str(test_root / "raw_documents/test_company"), "--kb-dir", str(kb_dir)])
        assert exit_code == 0
        monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_dir / "fintrace_kb.sqlite"))
        result = document_search(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.DOCUMENT_SEARCH,
                arguments={
                    "company_id": "000001.SZ",
                    "query": "存货 跌价准备 问询函",
                    "document_types": ["inquiry_letter"],
                    "top_k": 3,
                },
                reason="test",
            )
        )
        assert result.data["source"] == "knowledge_base"
        assert result.data["hits"]
        assert result.data["retrieval_debug"]["returned_hit_count"] == len(result.data["hits"])
        assert result.data["hits"][0]["retrieval"]["matched_by"]
        assert result.evidence[0].source.source_path.endswith("000001.SZ_2023-05-12_inquiry_letter.txt")
        assert result.evidence[0].fact["retrieval"]["final_score"] > 0
        assert (kb_dir / "parse_report.json").exists()
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_build_vector_index_and_hybrid_search_txt_document(monkeypatch) -> None:
    test_root = Path(".tmp_tests") / f"document_vector_kb_{uuid4().hex}"
    raw_dir = test_root / "raw_documents/test_company/inquiry_letter"
    kb_dir = test_root / "knowledge_base"
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_file = raw_dir / "000001.SZ_2023-05-12_inquiry_letter.txt"
    source_file.write_text("inventory impairment inquiry letter inventory impairment", encoding="utf-8")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    try:
        exit_code = build_kb_main(
            [
                "--raw-dir",
                str(test_root / "raw_documents/test_company"),
                "--kb-dir",
                str(kb_dir),
                "--build-vector",
            ]
        )
        assert exit_code == 0
        assert (kb_dir / "vector.faiss").exists()
        assert (kb_dir / "vector_ids.json").exists()
        monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_dir / "fintrace_kb.sqlite"))
        result = document_search(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.DOCUMENT_SEARCH,
                arguments={
                    "company_id": "000001.SZ",
                    "query": "inventory impairment",
                    "document_types": ["inquiry_letter"],
                    "top_k": 3,
                    "mode": "hybrid",
                },
                reason="test",
            )
        )
        assert result.data["source"] == "knowledge_base"
        assert result.data["mode"] == "hybrid"
        assert result.data["hits"]
        assert result.data["retrieval_debug"]["vector_available"] is True
        assert result.data["hits"][0]["retrieval"]["source"] == "hybrid"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_section_aware_chunking_writes_parse_report(monkeypatch) -> None:
    test_root = Path(".tmp_tests") / f"document_section_kb_{uuid4().hex}"
    raw_dir = test_root / "raw_documents/test_company/inquiry_letter"
    kb_dir = test_root / "knowledge_base"
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_file = raw_dir / "000001.SZ_2023-05-12_inquiry_letter.txt"
    source_file.write_text("问题一：关于存货跌价准备\n\n请公司说明存货跌价准备是否充分。", encoding="utf-8")
    try:
        exit_code = build_kb_main(["--raw-dir", str(test_root / "raw_documents/test_company"), "--kb-dir", str(kb_dir)])
        assert exit_code == 0
        report = json.loads((kb_dir / "parse_report.json").read_text(encoding="utf-8"))
        assert report["summary"]["document_count"] == 1
        assert report["documents"][0]["section_count"] == 1
        monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_dir / "fintrace_kb.sqlite"))
        result = document_search(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.DOCUMENT_SEARCH,
                arguments={"company_id": "000001.SZ", "query": "存货 跌价准备", "document_types": ["inquiry_letter"]},
                reason="test",
            )
        )
        assert result.data["hits"][0]["chunk"]["section"] == "问题一：关于存货跌价准备"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_docx_table_is_ingested_as_searchable_text(monkeypatch) -> None:
    import docx

    test_root = Path(".tmp_tests") / f"document_docx_table_kb_{uuid4().hex}"
    raw_dir = test_root / "raw_documents/test_company/annual_report"
    kb_dir = test_root / "knowledge_base"
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_file = raw_dir / "000001.SZ_2023-04-30_annual_report.docx"
    document = docx.Document()
    document.add_paragraph("年度报告附注")
    table = document.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "期末余额"
    table.cell(0, 2).text = "跌价准备"
    table.cell(1, 0).text = "库存商品"
    table.cell(1, 1).text = "210"
    table.cell(1, 2).text = "15"
    document.save(str(source_file))
    try:
        exit_code = build_kb_main(["--raw-dir", str(test_root / "raw_documents/test_company"), "--kb-dir", str(kb_dir)])
        assert exit_code == 0
        report = json.loads((kb_dir / "parse_report.json").read_text(encoding="utf-8"))
        assert report["summary"]["table_count"] == 1
        monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_dir / "fintrace_kb.sqlite"))
        result = document_search(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.DOCUMENT_SEARCH,
                arguments={"company_id": "000001.SZ", "query": "库存商品 跌价准备", "document_types": ["annual_report"]},
                reason="test",
            )
        )
        assert result.data["hits"]
        assert "库存商品" in result.data["hits"][0]["chunk"]["text"]
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_build_kb_skip_unchanged_reports_skipped_files() -> None:
    test_root = Path(".tmp_tests") / f"document_skip_kb_{uuid4().hex}"
    raw_dir = test_root / "raw_documents/test_company/inquiry_letter"
    kb_dir = test_root / "knowledge_base"
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_file = raw_dir / "000001.SZ_2023-05-12_inquiry_letter.txt"
    source_file.write_text("监管问询函要求公司说明存货跌价准备是否充分。", encoding="utf-8")
    try:
        first_exit = build_kb_main(["--raw-dir", str(test_root / "raw_documents/test_company"), "--kb-dir", str(kb_dir)])
        second_exit = build_kb_main(
            [
                "--raw-dir",
                str(test_root / "raw_documents/test_company"),
                "--kb-dir",
                str(kb_dir),
                "--skip-unchanged",
            ]
        )
        assert first_exit == 0
        assert second_exit == 0
        report = json.loads((kb_dir / "parse_report.json").read_text(encoding="utf-8"))
        assert report["summary"]["skipped_count"] == 1
        assert report["documents"][0]["parse_status"] == "skipped_unchanged"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)

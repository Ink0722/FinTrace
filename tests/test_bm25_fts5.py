from datetime import date
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from data_pipeline.documents.build_bm25_index import build_bm25_index
from data_pipeline.documents.build_file_index import main as build_kb_main
from schemas.enums import ToolName
from schemas.tool_calls import ToolCall
from tools.document_search.fts5_search import (
    build_match_expression,
    fts5_search,
    validate_bm25_index_snapshot,
)
from tools.document_search.interface import document_search


@pytest.fixture
def fts5_index():
    test_root, kb_path, index_path = _make_index()
    try:
        yield kb_path, index_path
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_build_match_expression_quotes_terms() -> None:
    assert build_match_expression("存货跌价") == '"存货" OR "货跌" OR "跌价"'
    assert build_match_expression("   ") is None
    assert build_match_expression("！！！") is None


def test_fts5_search_with_filters(fts5_index) -> None:
    kb_path, index_path = fts5_index
    outcome = fts5_search(
        query="存货跌价准备",
        index_path=index_path,
        kb_path=kb_path,
        top_k=5,
        company_id="000001.SZ",
    )
    assert outcome.hits
    assert outcome.strategy == "fts5_filtered"
    assert outcome.candidate_count == 2  # two chunks for 000001.SZ
    top = outcome.hits[0]
    assert top.chunk.company_id == "000001.SZ"
    assert "存货" in top.chunk.text
    assert top.score == 1.0  # best hit normalizes to 1.0
    assert top.retrieval["matched_by"] == ["bm25"]
    assert top.retrieval["lexical_strategy"] == "fts5_filtered"

    typed = fts5_search(
        query="盈利预测",
        index_path=index_path,
        kb_path=kb_path,
        top_k=5,
        document_types=["research_report"],
    )
    assert typed.hits and all(hit.chunk.document_type == "research_report" for hit in typed.hits)

    dated = fts5_search(
        query="存货跌价准备",
        index_path=index_path,
        kb_path=kb_path,
        top_k=5,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
    )
    assert dated.hits == []  # both chunks are from 2023

    no_match = fts5_search(
        query="完全不存在的内容",
        index_path=index_path,
        kb_path=kb_path,
        top_k=5,
    )
    assert no_match.hits == []
    assert no_match.strategy == "fts5_global"


def test_bm25_manifest_and_validation(fts5_index) -> None:
    kb_path, index_path = fts5_index
    manifest_path = index_path.with_name("bm25_manifest.json")
    import json

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunk_count"] == 3
    assert manifest["tokenizer_version"] == "bm25-bigram-v1"
    assert manifest["integrity_check"] == "passed"
    assert validate_bm25_index_snapshot(index_path, kb_path) == []

    manifest["tokenizer_version"] = "old-tokenizer"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    errors = validate_bm25_index_snapshot(index_path, kb_path)
    assert any("tokenizer version mismatch" in error for error in errors)


def test_document_search_uses_fts5_lexical_strategy(fts5_index, monkeypatch) -> None:
    kb_path, index_path = fts5_index
    monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_path))
    result = document_search(
        _call({"query": "存货跌价准备", "company_ids": ["000001.SZ"], "mode": "bm25", "top_k": 3})
    )
    assert result.status.value == "success"
    assert result.data["retrieval_debug"]["lexical_strategy"] == "fts5_filtered"
    assert result.data["retrieval_debug"]["candidate_chunk_count"] == 2
    assert result.data["hits"]
    assert result.evidence


def test_document_search_requires_bm25_index(fts5_index, monkeypatch) -> None:
    kb_path, index_path = fts5_index
    monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_path))
    monkeypatch.setenv("FINTRACE_BM25_INDEX_PATH", str(index_path.parent / "missing.sqlite"))
    result = document_search(_call({"query": "存货", "mode": "bm25"}))
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"
    assert result.error.details["build_command"] == "python -m data_pipeline.documents.build_bm25_index"


def test_document_search_rejects_stale_bm25_index(fts5_index, monkeypatch) -> None:
    import json

    kb_path, index_path = fts5_index
    manifest_path = index_path.with_name("bm25_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["kb"]["mtime_ns"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_path))
    result = document_search(_call({"query": "存货", "mode": "hybrid"}))
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"
    assert "stale or incomplete" in result.error.message


def test_vector_mode_does_not_require_bm25_index(fts5_index, monkeypatch) -> None:
    kb_path, index_path = fts5_index
    monkeypatch.setenv("FINTRACE_KB_PATH", str(kb_path))
    monkeypatch.setenv("FINTRACE_BM25_INDEX_PATH", str(index_path.parent / "missing.sqlite"))
    result = document_search(_call({"query": "存货", "mode": "vector"}))
    # No vector artifacts in this fixture: vector mode reports its own unavailable error
    # instead of the BM25 one, proving vector search does not depend on the lexical index.
    assert result.status.value == "failed"
    assert "Vector index artifacts" in result.error.message


def _call(arguments: dict) -> ToolCall:
    return ToolCall(
        tool_call_id="CALL-001",
        tool_name=ToolName.DOCUMENT_SEARCH,
        arguments=arguments,
        reason="test",
    )


def _make_index():
    test_root = Path(".tmp_tests") / f"bm25_fts5_{uuid4().hex}"
    raw_dir = test_root / "raw_documents"
    kb_dir = test_root / "knowledge_base"
    (raw_dir / "000001.SZ").mkdir(parents=True)
    (raw_dir / "600519.SH").mkdir(parents=True)
    (raw_dir / "000001.SZ" / "000001.SZ_2023-05-12_inquiry_letter.txt").write_text(
        "监管问询函要求公司说明存货跌价准备计提是否充分，并说明相关会计估计变更原因。", encoding="utf-8"
    )
    (raw_dir / "000001.SZ" / "000001.SZ_2023-06-01_annual_report.txt").write_text(
        "年报附注披露存货余额与跌价准备明细，公司存货跌价准备计提政策保持一致。", encoding="utf-8"
    )
    (raw_dir / "600519.SH" / "600519.SH_2023-07-01_research_report.txt").write_text(
        "研报摘要：维持盈利预测，目标价上调，关注主营业务增长与存货管理效率。", encoding="utf-8"
    )
    kb_path = kb_dir / "fintrace_kb.sqlite"
    index_path = kb_dir / "bm25_index.sqlite"
    assert build_kb_main(["--raw-dir", str(raw_dir), "--kb-dir", str(kb_dir)]) == 0
    build_bm25_index(kb_path, index_path)
    return test_root, kb_path, index_path

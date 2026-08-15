import json
import re
import shutil
import uuid
from pathlib import Path

import pytest

from data_pipeline.text.chunk_builder import (
    CHUNK_KEYS,
    ChunkBuildError,
    build_chunks,
)
from data_pipeline.text.chunker import ChunkingConfig, chunk_text


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


@pytest.fixture
def workspace_tmp_path() -> Path:
    path = Path("tests") / "test_artifacts" / f"chunk_builder_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_chunking_config_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(target_chars=100, min_chars=200)

    with pytest.raises(ValueError):
        ChunkingConfig(soft_max_chars=1300, hard_max_chars=1200)


def test_split_document_inherits_heading_hierarchy_and_source_offsets() -> None:
    text = (
        "没有标题的导语。\n\n"
        "一、监管情况\n"
        "这是监管情况正文。\n\n"
        "（一）收入确认\n"
        "这是收入确认正文。"
    )

    result = chunk_text(
        text,
        document_type="announcement",
        config=ChunkingConfig(min_chars=1),
    )

    assert [piece.section_title for piece in result.pieces] == [
        None,
        "一、监管情况",
        "一、监管情况 / （一）收入确认",
    ]
    for piece in result.pieces:
        assert text[piece.char_start : piece.char_start + len(piece.text)] == piece.text
    assert _compact("".join(piece.text for piece in result.pieces)) == _compact(text)


def test_split_document_does_not_generate_heading_for_ordinary_paragraph() -> None:
    text = "这是一段普通正文，不应被识别为标题。\n\n这是第二段普通正文。"

    result = chunk_text(
        text,
        document_type="announcement",
        config=ChunkingConfig(min_chars=1),
    )

    assert result.pieces
    assert all(piece.section_title is None for piece in result.pieces)


def test_split_document_recognizes_research_report_inline_sections() -> None:
    text = (
        "事件：公司发布年度报告，营业收入保持增长。\n\n"
        "投资要点：主营业务改善，现金流仍需持续观察。\n\n"
        "风险提示：行业需求波动，项目进度不及预期。"
    )

    result = chunk_text(
        text,
        document_type="research_report",
        config=ChunkingConfig(min_chars=1),
    )

    assert [piece.section_title for piece in result.pieces] == [
        "事件",
        "投资要点",
        "风险提示",
    ]


def test_split_document_attaches_title_only_parent_to_child_section() -> None:
    text = "一、整改情况\n\n（一）收入确认\n公司已经完成相关整改。"

    result = chunk_text(
        text,
        document_type="announcement",
        config=ChunkingConfig(min_chars=1),
    )

    assert len(result.pieces) == 1
    assert result.pieces[0].text == text
    assert result.pieces[0].section_title == "一、整改情况 / （一）收入确认"


def test_split_document_does_not_treat_decimal_or_list_sentence_as_heading() -> None:
    text = "经营数据如下。\n\n1.86\n\n3、对相关责任人处以罚款；"

    result = chunk_text(
        text,
        document_type="announcement",
        config=ChunkingConfig(min_chars=1),
    )

    assert len(result.pieces) == 1
    assert result.pieces[0].section_title is None


def test_split_document_attaches_short_prefix_to_first_section() -> None:
    text = "要点\n\n风险提示：行业需求可能波动。"

    result = chunk_text(text, document_type="research_report")

    assert len(result.pieces) == 1
    assert result.pieces[0].text == text
    assert result.pieces[0].section_title == "风险提示"


def test_split_document_merges_research_heading_fragment_forward() -> None:
    text = "主要观点：? 事件：公司发布2024年年度报告，营业收入保持增长。"

    result = chunk_text(
        text,
        document_type="research_report",
        config=ChunkingConfig(min_chars=1),
    )

    assert len(result.pieces) == 1
    assert result.pieces[0].text == text
    assert result.pieces[0].section_title == "事件"
    assert result.stats["structural_fragments_merged"] == 1


def test_split_document_merges_number_only_research_heading_fragment() -> None:
    text = "投资建议：1）盈利预测：预计公司明年归母净利润增长。"

    result = chunk_text(
        text,
        document_type="research_report",
        config=ChunkingConfig(min_chars=1),
    )

    assert len(result.pieces) == 1
    assert result.pieces[0].text == text
    assert result.pieces[0].section_title == "盈利预测"


def test_split_document_does_not_match_heading_inside_compound_word() -> None:
    text = "投资要点：相关事件：公司发布年度报告，归母净利润保持增长。"

    result = chunk_text(
        text,
        document_type="research_report",
        config=ChunkingConfig(min_chars=1),
    )

    assert len(result.pieces) == 1
    assert result.pieces[0].text == text
    assert result.pieces[0].section_title == "投资要点"


@pytest.mark.parametrize(
    "text",
    [
        "二、涉诉金额\n本金25亿元及利息等。",
        "3、整改负责人\n副总经理：牛丹丹",
        "风险提示：补贴政策风险，垃圾量不及预期。",
    ],
)
def test_split_document_keeps_short_complete_fact(text: str) -> None:
    result = chunk_text(
        text,
        document_type="announcement",
        config=ChunkingConfig(min_chars=1),
    )

    assert len(result.pieces) == 1
    assert result.pieces[0].text == text


def test_split_document_merges_short_paragraphs_within_same_section() -> None:
    text = "甲" * 100 + "\n\n" + "乙" * 150

    result = chunk_text(text, document_type="announcement")

    assert len(result.pieces) == 1
    assert result.pieces[0].text == text


def test_split_document_splits_oversized_paragraph_without_losing_text() -> None:
    text = "".join(f"第{i}项经营情况保持稳定。" for i in range(180))
    config = ChunkingConfig(target_chars=300, min_chars=100, soft_max_chars=450, hard_max_chars=500)

    result = chunk_text(text, document_type="announcement", config=config)

    assert len(result.pieces) > 1
    assert all(len(piece.text) <= config.hard_max_chars for piece in result.pieces)
    assert "".join(piece.text for piece in result.pieces) == text
    assert [piece.char_start for piece in result.pieces] == sorted(
        piece.char_start for piece in result.pieces
    )


def test_build_chunks_writes_minimal_schema_and_quality_artifacts(
    workspace_tmp_path: Path,
) -> None:
    data_root = workspace_tmp_path / "data"
    data_dir = data_root / "text_corpus"
    documents_path = data_dir / "documents.jsonl"
    text = "一、经营情况\n公司营业收入增长。\n\n二、风险提示\n原材料价格可能波动。"
    _write_jsonl(
        documents_path,
        [
            {
                "document_id": "ANN-0001",
                "document_type": "announcement",
                "stock_code": "000001.SZ",
                "title": "测试公告",
                "date": "2024-01-01",
                "publisher": "测试公司",
                "text": text,
            }
        ],
    )

    summary = build_chunks(data_dir=data_root, config=ChunkingConfig(min_chars=1))

    chunks_path = data_dir / "chunks.jsonl"
    chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines()]
    assert summary["total_documents"] == 1
    assert summary["total_chunks"] == 2
    assert all(set(chunk) == CHUNK_KEYS for chunk in chunks)
    assert [chunk["chunk_id"] for chunk in chunks] == ["ANN-0001-C0001", "ANN-0001-C0002"]
    assert chunks[0]["chunk_index"] == 1
    assert chunks[0]["chunk_version"] == "chunks-v1"
    assert text[chunks[0]["char_start"] : chunks[0]["char_start"] + len(chunks[0]["text"])] == chunks[0]["text"]

    report = json.loads((data_dir / "chunk_quality.json").read_text(encoding="utf-8"))
    manifest = json.loads((data_dir / "chunk_manifest.json").read_text(encoding="utf-8"))
    assert report["coverage_failures"] == 0
    assert report["chunks_over_hard_max"] == 0
    assert report["total_documents"] == 1
    assert set(manifest["schema_fields"]) == CHUNK_KEYS
    assert manifest["config"]["target_chars"] == 600
    assert manifest["source_documents_sha256"]
    assert manifest["chunks_sha256"]


def test_build_chunks_failure_does_not_replace_existing_output(workspace_tmp_path: Path) -> None:
    data_root = workspace_tmp_path / "data"
    data_dir = data_root / "text_corpus"
    documents_path = data_dir / "documents.jsonl"
    output_path = data_dir / "chunks.jsonl"
    duplicate = {
        "document_id": "ANN-DUPLICATE",
        "document_type": "announcement",
        "stock_code": "000001.SZ",
        "title": "重复文档",
        "date": "2024-01-01",
        "publisher": "测试公司",
        "text": "正文内容。",
    }
    _write_jsonl(documents_path, [duplicate, duplicate])
    output_path.write_text("existing output\n", encoding="utf-8")

    with pytest.raises(ChunkBuildError, match="Duplicate document_id"):
        build_chunks(data_dir=data_root)

    assert output_path.read_text(encoding="utf-8") == "existing output\n"
    assert not output_path.with_suffix(".jsonl.tmp").exists()

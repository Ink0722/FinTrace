from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from data_pipeline.text.cleaner import clean_text, remove_leading_title_lines
from data_pipeline.text.document_builder import DocumentBuildError, build_documents


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def sample_announcement(text_path: Path, **overrides) -> dict:
    record = {
        "id": "1001",
        "s_info_windcode": "603439.SH",
        "ann_dt": "2026-05-26",
        "n_info_title": "公司处罚情况公告",
        "category_names": ["违纪违规", "违纪违规", "个股其他公告"],
        "document_path": text_path.as_posix(),
        "download_status": "success",
    }
    record.update(overrides)
    return record


def sample_research(**overrides) -> dict:
    record = {
        "report_id": "5971493",
        "sec_code": "601033",
        "exchange_code": "XSHG",
        "title": "2024年报点评",
        "publish_date": "2025-04-01",
        "org_name": "东吴证券",
        "report_sub_type": "业绩点评",
        "rating_org": "买入",
        "rating_change": "维持",
        "first_cover": 0,
        "abstract": "公司实现营业收入增长。\r\n\r\n  风险提示。",
    }
    record.update(overrides)
    return record


def test_clean_text_only_normalizes_whitespace() -> None:
    assert clean_text("\ufeff 第一段\t内容\r\n\r\n\r\n第二段\x00") == "第一段 内容\n\n第二段"


@pytest.mark.parametrize(
    ("body", "title", "expected", "removed"),
    [
        ("监管公告\n监管公告\n证券代码：000001\n正文", "监管公告", "证券代码：000001\n正文", 2),
        ("监管公告。\n正文", "监管公告", "正文", 1),
        ("公司：整改公告\n正文", "公司:整改公告", "正文", 1),
        ("引言\n监管公告\n正文", "监管公告", "引言\n监管公告\n正文", 0),
        ("监管公告摘要\n正文", "监管公告", "监管公告摘要\n正文", 0),
        ("监管公告", "监管公告", "监管公告", 0),
    ],
)
def test_remove_leading_title_lines_is_conservative(
    body: str, title: str, expected: str, removed: int
) -> None:
    assert remove_leading_title_lines(body, title) == (expected, removed)


def test_build_documents_maps_sources_and_reports_exclusions() -> None:
    data_dir = Path("tests/.text_document_builder_data")
    try:
        announcement_text = data_dir / "documents" / "announcements" / "1001.txt"
        announcement_text.parent.mkdir(parents=True)
        announcement_text.write_text("公告正文\r\n\r\n  第二段", encoding="utf-8")

        write_jsonl(
            data_dir / "jsonl" / "announcements.jsonl",
            [
                sample_announcement(announcement_text),
                sample_announcement(
                    announcement_text,
                    id="1002",
                    download_status="excluded_no_text_layer",
                    document_path=None,
                ),
            ],
        )
        write_jsonl(
            data_dir / "jsonl" / "research_reports.jsonl",
            [
                sample_research(),
                sample_research(report_id="5971494", sec_code="920088", exchange_code="XBEI", first_cover=1),
                sample_research(report_id="5971495", sec_code="00001", exchange_code="XKRX"),
            ],
        )

        report = build_documents(data_dir=data_dir)
        documents = read_jsonl(data_dir / "text_corpus" / "documents.jsonl")

        assert report["total_documents"] == 3
        assert report["datasets"]["announcement"]["skipped"] == {"excluded_no_text_layer": 1}
        assert report["datasets"]["research_report"]["skipped"] == {"unsupported_exchange": 1}
        assert report["cleaning"] == {
            "announcement_documents_cleaned": 0,
            "announcement_leading_title_lines_removed": 0,
        }

        announcement = documents[0]
        assert announcement == {
            "document_id": "ANN-1001",
            "document_type": "announcement",
            "company_id": "603439.SH",
            "title": "公司处罚情况公告",
            "published_date": "2026-05-26",
            "tags": ["违纪违规", "个股其他公告"],
            "text": "公告正文\n\n第二段",
            "source_ref": announcement_text.as_posix(),
        }
        assert "publisher" not in announcement

        research = documents[1]
        assert research["document_id"] == "RR-5971493"
        assert research["company_id"] == "601033.SH"
        assert research["publisher"] == "东吴证券"
        assert research["tags"] == ["业绩点评", "买入", "维持"]
        assert research["text"] == "公司实现营业收入增长。\n\n风险提示。"
        assert research["source_ref"].endswith("data/jsonl/research_reports.jsonl#5971493")

        first_cover = documents[2]
        assert first_cover["company_id"] == "920088.BJ"
        assert first_cover["tags"] == ["业绩点评", "买入", "维持", "首次覆盖"]
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_duplicate_document_id_fails_without_replacing_existing_output() -> None:
    data_dir = Path("tests/.text_document_duplicate_data")
    try:
        announcement_text = data_dir / "documents" / "announcements" / "1001.txt"
        announcement_text.parent.mkdir(parents=True)
        announcement_text.write_text("公告正文", encoding="utf-8")
        duplicate = sample_announcement(announcement_text)
        write_jsonl(data_dir / "jsonl" / "announcements.jsonl", [duplicate, duplicate])
        write_jsonl(data_dir / "jsonl" / "research_reports.jsonl", [])
        output = data_dir / "text_corpus" / "documents.jsonl"
        output.parent.mkdir(parents=True)
        output.write_text("existing\n", encoding="utf-8")

        with pytest.raises(DocumentBuildError, match="Duplicate document_id"):
            build_documents(data_dir=data_dir)

        assert output.read_text(encoding="utf-8") == "existing\n"
        assert not output.with_name("documents.jsonl.tmp").exists()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)

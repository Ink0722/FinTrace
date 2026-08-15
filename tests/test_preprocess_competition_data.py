import json
import shutil
import zipfile
from pathlib import Path

from data_pipeline.competition.preprocess import (
    VisibleTextParser,
    detect_document_format,
    download_announcement,
    is_html_metadata_only,
    is_timeout_failure,
    iter_xlsx_records,
    normalize_date,
    normalize_record,
    repair_announcements,
)


def test_normalize_dates_from_both_source_formats():
    assert normalize_date("20240102") == "2024-01-02"
    assert normalize_date("46168") == "2026-05-26"
    assert normalize_date("2025-03-31") == "2025-03-31"


def test_normalize_record_preserves_codes_and_types_numbers():
    record = normalize_record(
        {
            "s_info_windcode": "000001.SZ",
            "ann_dt": "20240102",
            "statement_type": "408006000",
            "amount": "123.4500",
            "missing": "",
        }
    )
    assert record == {
        "s_info_windcode": "000001.SZ",
        "ann_dt": "2024-01-02",
        "statement_type": "408006000",
        "amount": 123.45,
        "missing": None,
    }


def test_html_parser_ignores_scripts_and_keeps_block_boundaries():
    parser = VisibleTextParser()
    parser.feed(
        "<html><script>ignore()</script><body><h1>公告标题</h1>"
        "<p>第一段&nbsp;正文</p><p>第二段</p></body></html>"
    )
    assert parser.text() == "公告标题\n第一段 正文\n第二段"


def test_metadata_only_html_detection_requires_an_attachment():
    shell = "公告标题\n公告日期：\n2026-04-04\n附件:\n公告正文.pdf (107316)"
    assert is_html_metadata_only(shell)
    assert not is_html_metadata_only("公告正文" * 100)
    assert not is_html_metadata_only("一份很短但没有附件的公告")


def test_document_format_detection_uses_magic_before_url_suffix():
    assert detect_document_format(b"%PDF-1.7", "text/plain", "report.doc") == "pdf"
    assert (
        detect_document_format(bytes.fromhex("D0CF11E0A1B11AE1"), "", "report.pdf")
        == "doc"
    )
    assert detect_document_format(b"<html></html>", "text/html", "report.pdf") == "html"


def test_timeout_failure_filter_excludes_parse_failures_and_successes():
    assert is_timeout_failure(
        {
            "download_status": "content_unavailable",
            "download_error": "attachment: TimeoutError: Download exceeded 60 seconds",
        }
    )
    assert not is_timeout_failure(
        {
            "download_status": "content_unavailable",
            "download_error": "Downloaded PDF contained too little extractable text",
        }
    )
    assert not is_timeout_failure(
        {"download_status": "success", "download_error": "Previous request timed out"}
    )


def test_timeout_only_repair_does_not_select_non_timeout_failures():
    data_dir = Path("tests/.timeout_retry_data")
    announcements = data_dir / "jsonl/announcements.jsonl"
    record = {
        "id": "scan-only",
        "download_status": "content_unavailable",
        "download_error": "Downloaded PDF contained too little extractable text",
        "document_path": "tests/missing.txt",
    }
    try:
        announcements.parent.mkdir(parents=True)
        announcements.write_text(json.dumps(record) + "\n", encoding="utf-8")
        report = repair_announcements(
            data_dir=data_dir, workers=1, timeout=1, retries=0, timeout_only=True
        )
        assert report["selected"] == 0
        assert report["remaining_timeouts"] == 0
        assert (data_dir / "announcement_timeout_remaining.csv").is_file()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_streaming_xlsx_reader_supports_shared_and_sparse_cells():
    workbook = Path("tests/.preprocess_sample.xlsx")
    shared_strings = """<?xml version="1.0" encoding="UTF-8"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <si><t>id</t></si><si><t>note</t></si><si><t>title</t></si><si><t>公告</t></si>
    </sst>"""
    worksheet = """<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>
        <row r="2"><c r="A2"><v>7</v></c><c r="C2" t="s"><v>3</v></c></row>
      </sheetData>
    </worksheet>"""
    try:
        with zipfile.ZipFile(workbook, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", shared_strings)
            archive.writestr("xl/worksheets/sheet1.xml", worksheet)

        headers, records = iter_xlsx_records(workbook)
        assert headers == ["id", "note", "title"]
        assert list(records) == [{"id": "7", "note": None, "title": "公告"}]
    finally:
        workbook.unlink(missing_ok=True)


def test_announcement_download_falls_back_from_html_to_pdf(monkeypatch):
    output = Path("tests/.announcement.txt")
    calls = []

    def fake_fetch(url, expected_format, timeout, retries=2):
        calls.append((url, expected_format, timeout))
        if expected_format == "html":
            raise ValueError("HTML has no article text")
        return "公告正文" * 30, "pdf"

    monkeypatch.setattr("data_pipeline.competition.preprocess.fetch_text", fake_fetch)
    record = {
        "id": "1",
        "source_urls": {"html": "https://example.test/1.html", "pdf": "https://example.test/1.pdf"},
        "document_path": output.as_posix(),
        "download_status": "not_requested",
        "download_error": None,
        "selected_url": None,
        "selected_format": None,
    }
    try:
        result = download_announcement(record, Path("data"), timeout=7, overwrite=True)
        assert [item[1] for item in calls] == ["html", "attachment"]
        assert result["selected_url"] == "https://example.test/1.pdf"
        assert result["selected_format"] == "pdf"
        assert result["download_status"] == "success"
        assert output.read_text(encoding="utf-8").startswith("公告正文")
    finally:
        output.unlink(missing_ok=True)

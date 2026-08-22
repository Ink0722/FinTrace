import json
from pathlib import Path

from data_pipeline.entity_resolution.build_company_universe import (
    build_company_universe,
    classify_company_code,
    load_company_universe,
)


def test_company_universe_uses_union_of_available_sources(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    _write_jsonl(
        normalized / "shareholders.jsonl",
        [{"s_info_windcode": "000001.SZ", "ann_dt": "2024-01-02"}],
    )
    _write_jsonl(
        normalized / "research_reports.jsonl",
        [{"sec_code": "600030", "sec_name": "CITIC Securities", "publish_date": "2025-04-01"}],
    )
    _write_jsonl(
        normalized / "announcements.jsonl",
        [{"s_info_windcode": "430001.BJ", "ann_dt": "2026-05-01"}],
    )
    output = tmp_path / "company_universe.jsonl"

    manifest = build_company_universe(normalized, output)
    companies = load_company_universe(output)

    assert manifest["company_count"] == 3
    assert set(companies) == {"000001.SZ", "600030.SH", "430001.BJ"}
    assert companies["600030.SH"]["security_names"] == ["CITIC Securities"]
    assert companies["000001.SZ"]["sources"] == ["shareholders"]


def test_company_code_classification() -> None:
    assert classify_company_code("600030.SH") == "a_share"
    assert classify_company_code("000001.SZ") == "a_share"
    assert classify_company_code("430001.BJ") == "a_share"
    assert classify_company_code("000001.SH") == "exchange_mismatch"
    assert classify_company_code("U11") == "nonstandard"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

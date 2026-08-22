import json
import sqlite3
from pathlib import Path

from data_pipeline.entity_resolution.build_index import build_entity_index
from data_pipeline.entity_resolution.build_company_universe import build_company_universe
from data_pipeline.entity_resolution.normalize import legal_core_name, normalize_name


def test_name_normalization_is_conservative() -> None:
    assert normalize_name("甲方（中国） 有限公司") == "甲方中国有限公司"
    assert legal_core_name("甲方（中国）有限公司") == "甲方中国"


def test_build_entity_index_confirms_only_unique_name_links(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    _write_jsonl(
        normalized / "research_reports.jsonl",
        [
            {"sec_code": "000001", "sec_name": "甲方科技股份有限公司"},
            {"sec_code": "000002", "sec_name": "乙方科技"},
        ],
    )
    _write_jsonl(
        normalized / "shareholders.jsonl",
        [
            _holder("000003.SZ", "甲方科技股份有限公司", "C001"),
            _holder("000003.SZ", "乙方科技有限公司", "C002"),
            _holder("000003.SZ", "张三", "P001", category="1"),
        ],
    )
    output = tmp_path / "entity_master.sqlite"

    universe = tmp_path / "company_universe.jsonl"
    build_company_universe(normalized, universe)
    manifest = build_entity_index(normalized, output, None, universe)

    assert manifest["confirmed_links"] == 2
    assert manifest["review_candidates"] == 0
    with sqlite3.connect(output) as connection:
        links = connection.execute(
            "SELECT source_entity_id, canonical_entity_id, match_method FROM entity_links ORDER BY source_entity_id"
        ).fetchall()
        people = connection.execute(
            "SELECT COUNT(*) FROM entities WHERE entity_id = 'PERSON:P001'"
        ).fetchone()[0]
    assert links == [
        ("COMPANY:C001", "000001.SZ", "exact_normalized_name"),
        ("COMPANY:C002", "000002.SZ", "unique_legal_core"),
    ]
    assert people == 0


def test_ambiguous_legal_core_is_not_promoted(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    _write_jsonl(
        normalized / "research_reports.jsonl",
        [
            {"sec_code": "000001", "sec_name": "同名科技"},
            {"sec_code": "000002", "sec_name": "同名科技有限公司"},
        ],
    )
    _write_jsonl(
        normalized / "shareholders.jsonl",
        [_holder("000003.SZ", "同名科技股份有限公司", "C001")],
    )
    output = tmp_path / "entity_master.sqlite"

    universe = tmp_path / "company_universe.jsonl"
    build_company_universe(normalized, universe)
    manifest = build_entity_index(normalized, output, None, universe)

    assert manifest["confirmed_links"] == 0
    assert manifest["review_candidates"] == 2


def test_akshare_legal_name_bridges_nonmatching_security_name(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    _write_jsonl(
        normalized / "research_reports.jsonl",
        [{"sec_code": "000001", "sec_name": "中航产融"}],
    )
    _write_jsonl(
        normalized / "shareholders.jsonl",
        [_holder("000002.SZ", "中航工业产融控股股份有限公司", "C001")],
    )
    profiles = tmp_path / "profiles.jsonl"
    _write_jsonl(
        profiles,
        [
            {
                "company_id": "000001.SZ",
                "legal_name": "中航工业产融控股股份有限公司",
                "security_name": "中航产融",
                "former_names": [],
                "fetch_status": "success",
            }
        ],
    )
    output = tmp_path / "entity_master.sqlite"

    universe = tmp_path / "company_universe.jsonl"
    build_company_universe(normalized, universe)
    manifest = build_entity_index(normalized, output, profiles, universe)

    assert manifest["company_profiles_loaded"] == 1
    assert manifest["match_methods"] == {"exact_legal_name": 1}


def _holder(target: str, name: str, compcode: str, *, category: str = "2") -> dict:
    return {
        "s_info_windcode": target,
        "s_holder_holdercategory": category,
        "s_holder_name": name,
        "s_holder_aname": name,
        "s_info_compcode": compcode,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

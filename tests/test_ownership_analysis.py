import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from data_pipeline.ownership.build_index import build_ownership_index
from schemas.enums import ToolName
from schemas.tool_calls import ToolCall
from tools.ownership_analysis.interface import ownership_analysis


@pytest.fixture
def ownership_index(monkeypatch):
    root = Path(".tmp_tests") / f"ownership_analysis_{uuid4().hex}"
    normalized = root / "normalized"
    index_path = root / "indexes" / "ownership_holdings.sqlite"
    normalized.mkdir(parents=True)
    (normalized / "shareholders.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in _dataset()),
        encoding="utf-8",
    )
    manifest = build_ownership_index(normalized, index_path)
    monkeypatch.setenv("FINTRACE_OWNERSHIP_NORMALIZED_DIR", str(normalized))
    monkeypatch.setenv("FINTRACE_OWNERSHIP_INDEX_PATH", str(index_path))
    try:
        yield index_path, manifest
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_ownership_index_manifest(ownership_index) -> None:
    _, manifest = ownership_index
    rows = manifest["rows"]
    # 22 valid rows + 1 exact duplicate -> parsed 23, inserted 22, duplicates 1.
    assert rows["parsed"] == 23
    assert rows["inserted"] == 22
    assert rows["duplicates_ignored"] == 1
    assert rows["skipped"] == 1
    assert rows["skip_reasons"] == {"missing_holder_name": 1}
    entities = manifest["entities"]
    assert entities["resolved"] == 3  # PERSON:P0001, PERSON:P0003, COMPANY:C0001
    assert entities["unresolved"] == 13  # 李四 + 11 extended-roster holders + 赵六
    assert manifest["mapping_version"] == "ownership-holdings-v1"


def test_holding_query_latest_snapshot_forward(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_query",
                "company_ids": ["000001.SZ"],
            }
        )
    )
    assert result.status.value == "success"
    assert result.data["direction"] == "company_to_holders"
    company = result.data["companies"][0]
    assert company["snapshot"]["holder_end_date"] == "2025-06-30"
    assert company["snapshot"]["announcement_date"] == "2025-07-25"
    holders = company["holders"]
    assert [holder["holder_name"] for holder in holders] == ["张三", "甲投资有限公司", "王五"]
    assert [holder["calculated_rank"] for holder in holders] == [1, 2, 3]
    assert company["concentration"]["top1_ratio_sum"] == pytest.approx(0.135)
    assert company["concentration"]["top3_ratio_sum"] == pytest.approx(0.36)
    assert result.evidence and result.evidence[0].evidence_id.startswith("EVID-OWN-")
    assert result.evidence[0].source.row_id.startswith("REC-OWN-")
    assert any("as_of_date was not provided" in warning for warning in result.warnings)


def test_holding_query_as_of_date_avoids_lookahead(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_query",
                "company_ids": ["000001.SZ"],
                "as_of_date": "2025-06-30",
            }
        )
    )
    assert result.status.value == "success"
    company = result.data["companies"][0]
    # The 2025-06-30 snapshot was announced on 2025-07-25, after the observation date.
    assert company["snapshot"]["holder_end_date"] == "2024-12-31"
    assert {holder["holder_name"] for holder in company["holders"]} == {"张三", "甲投资有限公司", "王五"}


def test_holding_query_future_company_not_visible(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_query",
                "company_ids": ["000004.SZ"],
                "as_of_date": "2025-06-30",
            }
        )
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"


def test_holding_query_reverse_by_holder_name(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_query",
                "holder_ids": ["张三"],
                "as_of_date": "2025-06-30",
            }
        )
    )
    assert result.status.value == "success"
    assert result.data["direction"] == "holder_to_companies"
    companies = result.data["companies"]
    assert [company["company_id"] for company in companies] == ["000001.SZ", "000002.SZ"]
    first = companies[0]["holdings"][0]
    assert first["holder_name"] == "张三"
    assert first["calculated_rank"] == 1
    assert first["holding_ratio_raw_pct"] == pytest.approx(14.0)


def test_holding_query_cross_filter(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_query",
                "company_ids": ["000001.SZ"],
                "holder_ids": ["李四"],
                "as_of_date": "2024-08-01",
            }
        )
    )
    assert result.status.value == "success"
    assert result.data["direction"] == "cross_filter"
    company = result.data["companies"][0]
    assert company["snapshot"]["holder_end_date"] == "2024-06-30"
    assert [holder["holder_name"] for holder in company["holders"]] == ["李四"]
    # Concentration is computed on the full effective snapshot, not the filtered view.
    assert company["concentration"]["holder_count"] == 3
    assert company["concentration"]["top3_ratio_sum"] == pytest.approx(0.35)


def test_holding_query_holder_types_filter(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_query",
                "company_ids": ["000001.SZ"],
                "holder_types": ["PERSON"],
            }
        )
    )
    assert result.status.value == "success"
    holders = result.data["companies"][0]["holders"]
    assert {holder["holder_name"] for holder in holders} == {"张三", "王五"}
    assert result.data["companies"][0]["concentration"]["holder_count"] == 3


def test_holding_query_requires_company_or_holder(ownership_index) -> None:
    result = ownership_analysis(_call({"operation": "holding_query"}))
    assert result.status.value == "failed"
    assert result.error.error_type.value == "INVALID_ARGUMENT"


def test_holding_query_unknown_company(ownership_index) -> None:
    result = ownership_analysis(
        _call({"operation": "holding_query", "company_ids": ["999999.SZ"]})
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"


def test_penetration_is_explicitly_unsupported(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "penetration",
                "source_entity_id": "PERSON-001",
                "target_entity_id": "000001.SZ",
                "as_of_date": "2024-12-31",
            }
        )
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "UNSUPPORTED_QUERY"


def test_holding_compare_entered_exited_and_changes(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_compare",
                "company_ids": ["000001.SZ"],
                "start_date": "2024-07-25",
                "end_date": "2025-02-01",
            }
        )
    )
    assert result.status.value == "success"
    assert result.data["start"]["snapshot"]["holder_end_date"] == "2024-06-30"
    assert result.data["end"]["snapshot"]["holder_end_date"] == "2024-12-31"
    assert [entry["holder_name"] for entry in result.data["entered"]] == ["王五"]
    assert [entry["holder_name"] for entry in result.data["exited"]] == ["李四"]
    increased = {entry["holder_name"]: entry for entry in result.data["increased"]}
    decreased = {entry["holder_name"]: entry for entry in result.data["decreased"]}
    assert increased["甲投资有限公司"]["ratio_change_raw_pct"] == pytest.approx(1.0)
    assert increased["甲投资有限公司"]["quantity_change"] == 100
    assert decreased["张三"]["ratio_change_raw_pct"] == pytest.approx(-1.0)
    assert decreased["张三"]["quantity_change"] == -100
    assert result.data["unchanged_count"] == 0
    assert any("退出主要股东名单" in warning for warning in result.warnings)
    assert result.evidence


def test_holding_compare_change_threshold(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_compare",
                "company_ids": ["000001.SZ"],
                "start_date": "2025-02-01",
                "end_date": "2025-08-01",
                "change_threshold": 0.6,
            }
        )
    )
    assert result.status.value == "success"
    # Both ±0.5pp changes fall below the 0.6pp threshold and are suppressed.
    assert result.data["increased"] == []
    assert result.data["decreased"] == []
    assert result.data["unchanged_count"] == 1  # 甲投资有限公司 13.0 -> 13.0
    assert result.data["below_threshold_count"] == 2
    assert any("below change_threshold" in warning for warning in result.warnings)
    # Without the threshold the ±0.5pp changes are listed as well.
    unfiltered = ownership_analysis(
        _call(
            {
                "operation": "holding_compare",
                "company_ids": ["000001.SZ"],
                "start_date": "2025-02-01",
                "end_date": "2025-08-01",
            }
        )
    )
    assert [entry["holder_name"] for entry in unfiltered.data["increased"]] == ["王五"]
    assert unfiltered.data["increased"][0]["ratio_change_raw_pct"] == pytest.approx(0.5)
    assert [entry["holder_name"] for entry in unfiltered.data["decreased"]] == ["张三"]


def test_holding_compare_boundary_missing(ownership_index) -> None:
    result = ownership_analysis(
        _call(
            {
                "operation": "holding_compare",
                "company_ids": ["000001.SZ"],
                "start_date": "2020-01-01",
                "end_date": "2025-01-01",
            }
        )
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"
    assert result.error.details["missing_boundaries"] == ["start_date"]


def test_holding_compare_rejects_invalid_shape(ownership_index) -> None:
    reversed_dates = ownership_analysis(
        _call(
            {
                "operation": "holding_compare",
                "company_ids": ["000001.SZ"],
                "start_date": "2025-01-01",
                "end_date": "2024-01-01",
            }
        )
    )
    assert reversed_dates.status.value == "failed"
    assert reversed_dates.error.error_type.value == "INVALID_ARGUMENT"
    multiple_companies = ownership_analysis(
        _call(
            {
                "operation": "holding_compare",
                "company_ids": ["000001.SZ", "000002.SZ"],
                "start_date": "2024-07-25",
                "end_date": "2025-02-01",
            }
        )
    )
    assert multiple_companies.status.value == "failed"
    assert multiple_companies.error.error_type.value == "INVALID_ARGUMENT"


def test_missing_index_returns_build_instruction(monkeypatch) -> None:
    missing = Path(".tmp_tests") / "missing-ownership-index.sqlite"
    monkeypatch.setenv("FINTRACE_OWNERSHIP_INDEX_PATH", str(missing))
    result = ownership_analysis(
        _call({"operation": "holding_query", "company_ids": ["000001.SZ"]})
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"
    assert result.error.details["build_command"] == "python -m data_pipeline.ownership.build_index"


def test_stale_index_requires_rebuild(ownership_index, monkeypatch) -> None:
    index_path, _ = ownership_index
    manifest_path = index_path.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mapping_version"] = "old-version"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = ownership_analysis(
        _call({"operation": "holding_query", "company_ids": ["000001.SZ"]})
    )
    assert result.status.value == "failed"
    assert "stale or incomplete" in result.error.message


def test_snapshot_quality_flags_surfaced(ownership_index) -> None:
    result = ownership_analysis(
        _call({"operation": "holding_query", "company_ids": ["000003.SZ"]})
    )
    assert result.status.value == "success"
    company = result.data["companies"][0]
    assert company["snapshot"]["snapshot_scope"] == "extended_roster"
    assert "snapshot_more_than_ten" in company["snapshot"]["quality_flags"]
    assert "snapshot_ratio_sum_over_100" in company["snapshot"]["quality_flags"]
    assert any("extended_roster" in warning for warning in result.warnings)
    assert any("超过 100%" in warning for warning in result.warnings)


def test_limitations_attached(ownership_index) -> None:
    result = ownership_analysis(
        _call({"operation": "holding_query", "company_ids": ["000001.SZ"]})
    )
    assert any("主要股东披露数据" in item for item in result.data["limitations"])
    assert any("rank_source" in item for item in result.data["limitations"])


def _call(arguments: dict) -> ToolCall:
    return ToolCall(
        tool_call_id="CALL-001",
        tool_name=ToolName.OWNERSHIP_ANALYSIS,
        arguments=arguments,
        reason="test",
    )


def _row(
    windcode: str,
    ann_dt: str,
    end_date: str,
    category: str,
    name: str,
    quantity: int,
    pct: float,
    *,
    compcode: str | None = None,
) -> dict:
    return {
        "s_info_windcode": windcode,
        "ann_dt": ann_dt,
        "s_holder_enddate": end_date,
        "s_holder_holdercategory": category,
        "s_holder_name": name,
        "s_holder_quantity": quantity,
        "s_holder_pct": pct,
        "s_holder_sharecategory": "1014",
        "s_holder_restrictedquantity": 0,
        "s_holder_aname": name,
        "s_holder_sequence": None,
        "s_holder_sharecategoryname": "A股流通股",
        "s_holder_memo": None,
        "s_info_compcode": compcode,
        "report_period": None,
        "s_holder_nat": None,
    }


def _dataset() -> list[dict]:
    rows: list[dict] = [
        # 000001.SZ snapshot 1: end 2024-06-30, announced 2024-07-20.
        _row("000001.SZ", "2024-07-20", "2024-06-30", "1", "张三", 1500, 15.0, compcode="P0001"),
        _row("000001.SZ", "2024-07-20", "2024-06-30", "1", "李四", 800, 8.0),
        _row("000001.SZ", "2024-07-20", "2024-06-30", "2", "甲投资有限公司", 1200, 12.0, compcode="C0001"),
        # 000001.SZ snapshot 2: end 2024-12-31, announced 2025-01-25.
        _row("000001.SZ", "2025-01-25", "2024-12-31", "1", "张三", 1400, 14.0, compcode="P0001"),
        _row("000001.SZ", "2025-01-25", "2024-12-31", "1", "王五", 900, 9.0, compcode="P0003"),
        _row("000001.SZ", "2025-01-25", "2024-12-31", "2", "甲投资有限公司", 1300, 13.0, compcode="C0001"),
        # 000001.SZ snapshot 3: end 2025-06-30, announced 2025-07-25.
        _row("000001.SZ", "2025-07-25", "2025-06-30", "1", "张三", 1350, 13.5, compcode="P0001"),
        _row("000001.SZ", "2025-07-25", "2025-06-30", "1", "王五", 950, 9.5, compcode="P0003"),
        _row("000001.SZ", "2025-07-25", "2025-06-30", "2", "甲投资有限公司", 1300, 13.0, compcode="C0001"),
        # 000002.SZ: 张三 also appears here with the same resolved compcode.
        _row("000002.SZ", "2025-01-30", "2024-12-31", "1", "张三", 500, 5.0, compcode="P0001"),
        # 000003.SZ: 11 holders each at 9.5% -> extended_roster and ratio sum 104.5%.
        *[
            _row("000003.SZ", "2025-02-01", "2024-12-31", "1", f"股东{index:02d}", 950, 9.5)
            for index in range(1, 12)
        ],
        # 000004.SZ: only disclosed far in the future.
        _row("000004.SZ", "2026-01-05", "2025-12-31", "1", "赵六", 1000, 10.0),
    ]
    # One exact duplicate (folded at import) and one invalid row (skipped).
    rows.append(rows[0])
    rows.append(_row("000001.SZ", "2025-07-25", "2025-06-30", "1", "", 100, 1.0))
    return rows

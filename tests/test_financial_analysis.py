import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from data_pipeline.financial.build_index import build_financial_index
from harness.routing.financial_period_resolver import resolve_financial_periods
from schemas.request import ParsedRequest
from schemas.enums import ToolName
from schemas.tool_calls import ToolCall
from tools.financial_analysis.interface import financial_analysis
from tools.financial_analysis.risk_catalog import RISK_RULES
from tools.financial_analysis.risk_rules import evaluate_rule


@pytest.fixture
def financial_index(monkeypatch):
    root = Path(".tmp_tests") / f"financial_analysis_{uuid4().hex}"
    normalized = root / "normalized"
    index_path = root / "indexes" / "financial_metrics.sqlite"
    normalized.mkdir(parents=True)
    _write_jsonl(
        normalized / "balance_sheets.jsonl",
        [
            _row("OBJ-BS-1", "000001.SZ", "2023-12-31", inventories=100, acct_rcv=50, tot_assets=1000),
            _row("OBJ-BS-2", "000001.SZ", "2024-12-31", inventories=150, acct_rcv=55, tot_assets=1200),
            _row("OBJ-BS-3", "000002.SZ", "2024-12-31", inventories=200, tot_assets=1500),
        ],
    )
    _write_jsonl(
        normalized / "income_statements.jsonl",
        [
            _row("OBJ-IS-1", "000001.SZ", "2023-12-31", oper_rev=500, net_profit_excl_min_int_inc=50),
            _row("OBJ-IS-2", "000001.SZ", "2024-12-31", oper_rev=600, net_profit_excl_min_int_inc=60),
            _row("OBJ-IS-3", "000002.SZ", "2024-12-31", oper_rev=800, net_profit_excl_min_int_inc=70),
            _row("OBJ-IS-4", "000001.SZ", "2025-03-31", oper_rev=160, net_profit_excl_min_int_inc=15),
        ],
    )
    _write_jsonl(
        normalized / "cashflows.jsonl",
        [
            _row("OBJ-CF-1", "000001.SZ", "2023-12-31", net_cash_flows_oper_act=40),
            _row("OBJ-CF-2", "000001.SZ", "2024-12-31", net_cash_flows_oper_act=20),
            _row("OBJ-CF-3", "000002.SZ", "2024-12-31", net_cash_flows_oper_act=65),
        ],
    )
    manifest = build_financial_index(normalized, index_path)
    monkeypatch.setenv("FINTRACE_FINANCIAL_NORMALIZED_DIR", str(normalized))
    monkeypatch.setenv("FINTRACE_FINANCIAL_INDEX_PATH", str(index_path))
    try:
        yield index_path, manifest
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_financial_index_from_normalized_jsonl(financial_index) -> None:
    index_path, manifest = financial_index
    assert index_path.is_file()
    assert manifest["source_rows"]["income_statement"] == 4
    assert manifest["total_metric_rows"] > 0


def test_metric_query_returns_values_and_evidence(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "metric_query",
                "company_ids": ["000001.SZ"],
                "metric_codes": ["REVENUE", "NET_PROFIT_PARENT"],
                "report_periods": ["2024-12-31"],
                "knowledge_cutoff": "2025-04-30",
            }
        )
    )
    assert result.status.value == "success"
    assert {item["metric_code"] for item in result.data["values"]} == {
        "REVENUE",
        "NET_PROFIT_PARENT",
    }
    assert result.evidence
    assert result.evidence[0].source.row_id


def test_metric_query_respects_knowledge_cutoff(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "metric_query",
                "company_ids": ["000001.SZ"],
                "metric_codes": ["REVENUE"],
                "report_periods": ["2024-12-31"],
                "knowledge_cutoff": "2024-01-01",
            }
        )
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "DATA_NOT_AVAILABLE"


def test_metric_compare_across_periods(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "metric_compare",
                "company_ids": ["000001.SZ"],
                "metric_codes": ["REVENUE"],
                "report_periods": ["2023-12-31", "2024-12-31"],
                "comparison_method": "both",
            }
        )
    )
    comparison = result.data["comparisons"][0]
    assert result.status.value == "success"
    assert result.data["comparison_dimension"] == "period"
    assert comparison["adjacent_changes"][0]["change_amount"] == 100
    assert comparison["adjacent_changes"][0]["change_rate"] == pytest.approx(0.2)


def test_metric_compare_across_companies(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "metric_compare",
                "company_ids": ["000001.SZ", "000002.SZ"],
                "metric_codes": ["INVENTORY"],
                "report_periods": ["2024-12-31"],
            }
        )
    )
    comparison = result.data["comparisons"][0]
    assert result.status.value == "success"
    assert comparison["ranking"] == ["000002.SZ", "000001.SZ"]
    assert comparison["max_min_spread"]["change_amount"] == 50


def test_metric_compare_rejects_ambiguous_dimensions(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "metric_compare",
                "company_ids": ["000001.SZ", "000002.SZ"],
                "metric_codes": ["TOTAL_ASSETS"],
                "report_periods": ["2023-12-31", "2024-12-31"],
            }
        )
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "INVALID_ARGUMENT"


def test_metric_compare_rejects_mixed_ytd_period_types(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "metric_compare",
                "company_ids": ["000001.SZ"],
                "metric_codes": ["REVENUE"],
                "report_periods": ["2024-12-31", "2025-03-31"],
            }
        )
    )
    assert result.status.value == "failed"
    assert "matching period types" in result.error.message


def test_risk_scan_returns_triggered_not_triggered_and_skipped_rules(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "risk_scan",
                "company_ids": ["000001.SZ"],
                "report_periods": ["2023-12-31", "2024-12-31"],
            }
        )
    )
    assert result.status.value == "success"
    by_rule = {item["rule_id"]: item for item in result.data["signals"]}
    assert by_rule["CASH_PROFIT_DIVERGENCE"]["status"] == "triggered"
    assert by_rule["INVENTORY_REVENUE_DIVERGENCE"]["status"] == "triggered"
    assert by_rule["RECEIVABLE_REVENUE_DIVERGENCE"]["status"] == "not_triggered"
    assert by_rule["LIQUIDITY_PRESSURE"]["status"] == "insufficient_data"
    assert result.data["rule_version"] == "financial-risk-rules-v2"
    assert result.data["coverage"]["evaluated_rule_count"] == 4
    assert len(result.data["signals"]) == 8
    assert result.data["threshold_calibration"]["status"] == "uncalibrated"
    assert result.data["overall_score"] is None
    assert result.data["scoring_status"] == "disabled_until_calibrated"
    assert result.evidence


def test_period_resolver_expands_single_target_to_available_history(financial_index) -> None:
    parsed = ParsedRequest(
        raw_query="分析000001.SZ在2024年的金融风险", entities=["000001.SZ"],
        periods=["2024-12-31"], requested_periods=["2024-12-31"],
        task_family="financial_investigation",
    )
    resolved = resolve_financial_periods(parsed, "2025-04-30")
    assert resolved.requested_periods == ["2024-12-31"]
    assert resolved.periods == ["2023-12-31", "2024-12-31"]
    assert resolved.target_period == "2024-12-31"
    assert resolved.period_resolution_mode == "history_until_target"


def test_period_resolver_uses_all_available_fy_when_unspecified(financial_index) -> None:
    parsed = ParsedRequest(
        raw_query="分析000001.SZ的金融风险", entities=["000001.SZ"],
        task_family="financial_investigation",
    )
    resolved = resolve_financial_periods(parsed, "2025-04-30")
    assert resolved.requested_periods == []
    assert resolved.periods == ["2023-12-31", "2024-12-31"]
    assert resolved.period_resolution_mode == "all_available_fy"


def test_period_resolver_uses_latest_available_period_for_metric_query(financial_index) -> None:
    parsed = ParsedRequest(
        raw_query="000001.SZ最新营业收入", entities=["000001.SZ"], metrics=["REVENUE"],
        task_family="financial_metric_query", time_mode="latest", end_date="2025-04-30",
    )
    resolved = resolve_financial_periods(parsed, "2025-04-30")
    assert resolved.periods == ["2025-03-31"]
    assert resolved.target_period == "2025-03-31"
    assert resolved.period_resolution_mode == "latest_available"


def test_period_resolver_uses_latest_two_comparable_periods_for_compare(financial_index) -> None:
    parsed = ParsedRequest(
        raw_query="000001.SZ营业收入最近有什么变化", entities=["000001.SZ"], metrics=["REVENUE"],
        task_family="financial_metric_compare", time_mode="recent", end_date="2025-04-30",
    )
    resolved = resolve_financial_periods(parsed, "2025-04-30")
    assert resolved.periods == ["2023-12-31", "2024-12-31"]
    assert resolved.period_resolution_mode == "latest_two_comparable"


def test_risk_scan_allows_single_period_and_degrades_pair_rules(financial_index) -> None:
    result = financial_analysis(_call({
        "operation": "risk_scan", "company_ids": ["000002.SZ"],
        "report_periods": ["2024-12-31"], "requested_periods": [],
        "target_period": "2024-12-31", "period_resolution_mode": "all_available_fy",
    }))
    assert result.status.value == "success"
    by_rule = {item["rule_id"]: item for item in result.data["signals"]}
    assert by_rule["CASH_PROFIT_DIVERGENCE"]["status"] == "insufficient_data"
    assert by_rule["NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE"]["status"] == "insufficient_data"
    skipped = {item["rule_id"]: item for item in result.data["rules_skipped"]}
    assert skipped["NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE"]["reason"] == "minimum_periods_not_met"
    assert result.data["target_period"] == "2024-12-31"


def test_risk_v2_evaluates_each_adjacent_period_pair() -> None:
    series = {
        "INVENTORY": _series({"2022-12-31": 100, "2023-12-31": 180, "2024-12-31": 190}),
        "REVENUE": _series({"2022-12-31": 100, "2023-12-31": 110, "2024-12-31": 150}),
    }
    result = evaluate_rule(
        RISK_RULES["INVENTORY_REVENUE_DIVERGENCE"],
        series,
        ["2022-12-31", "2023-12-31", "2024-12-31"],
    )
    assert [item["status"] for item in result["observations"]] == ["triggered", "not_triggered"]


def test_risk_v2_marks_non_positive_profit_not_applicable() -> None:
    series = {
        "NET_PROFIT_PARENT": _series({"2023-12-31": -10, "2024-12-31": 5}),
        "OPERATING_CASHFLOW": _series({"2023-12-31": 20, "2024-12-31": 1}),
    }
    result = evaluate_rule(
        RISK_RULES["CASH_PROFIT_DIVERGENCE"], series, ["2023-12-31", "2024-12-31"]
    )
    assert result["status"] == "not_applicable"
    assert result["observations"][0]["not_applicable_reason"] == "non_positive_profit"


def test_risk_v2_keeps_usable_pairs_when_one_period_is_missing() -> None:
    series = {
        "ACCOUNTS_RECEIVABLE": _series({"2022-12-31": 100, "2023-12-31": 150}),
        "REVENUE": _series({"2022-12-31": 100, "2023-12-31": 110, "2024-12-31": 120}),
    }
    result = evaluate_rule(
        RISK_RULES["RECEIVABLE_REVENUE_DIVERGENCE"],
        series,
        ["2022-12-31", "2023-12-31", "2024-12-31"],
    )
    assert result["status"] == "triggered"
    assert result["observations"][1]["status"] == "insufficient_data"
    assert result["missing_inputs"]


def test_risk_v2_detects_persistent_negative_cashflow() -> None:
    series = {"OPERATING_CASHFLOW": _series({
        "2022-12-31": 10, "2023-12-31": -2, "2024-12-31": -3,
    })}
    result = evaluate_rule(
        RISK_RULES["NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE"],
        series,
        ["2022-12-31", "2023-12-31", "2024-12-31"],
    )
    assert result["status"] == "triggered"
    assert result["calculated_values"]["longest_negative_run"] == 2


def test_risk_v2_sales_cash_and_leverage_rules() -> None:
    periods = ["2023-12-31", "2024-12-31"]
    sales = evaluate_rule(RISK_RULES["SALES_CASH_REVENUE_DIVERGENCE"], {
        "CASH_RECEIVED_FROM_SALES": _series({periods[0]: 100, periods[1]: 60}),
        "REVENUE": _series({periods[0]: 100, periods[1]: 100}),
    }, periods)
    leverage = evaluate_rule(RISK_RULES["LEVERAGE_PRESSURE"], {
        "TOTAL_LIABILITIES": _series({periods[0]: 60, periods[1]: 80}),
        "TOTAL_ASSETS": _series({periods[0]: 100, periods[1]: 100}),
    }, periods)
    assert sales["status"] == "triggered"
    assert leverage["status"] == "triggered"


def test_risk_scan_can_select_rules(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "risk_scan",
                "company_ids": ["000001.SZ"],
                "report_periods": ["2023-12-31", "2024-12-31"],
                "rule_ids": ["CASH_PROFIT_DIVERGENCE"],
            }
        )
    )
    assert result.status.value == "success"
    assert [item["rule_id"] for item in result.data["signals"]] == ["CASH_PROFIT_DIVERGENCE"]


def test_risk_scan_rejects_noncomparable_periods(financial_index) -> None:
    result = financial_analysis(
        _call(
            {
                "operation": "risk_scan",
                "company_ids": ["000001.SZ"],
                "report_periods": ["2024-12-31", "2025-03-31"],
            }
        )
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "INVALID_ARGUMENT"
    assert "same period type" in result.error.message


def test_missing_financial_index_returns_build_instruction(monkeypatch) -> None:
    missing = Path(".tmp_tests") / "missing-financial-index.sqlite"
    monkeypatch.setenv("FINTRACE_FINANCIAL_INDEX_PATH", str(missing))
    result = financial_analysis(
        _call(
            {
                "operation": "metric_query",
                "company_ids": ["000001.SZ"],
                "metric_codes": ["REVENUE"],
                "report_periods": ["2024-12-31"],
            }
        )
    )
    assert result.status.value == "failed"
    assert "data_pipeline.financial.build_index" in result.error.details["build_command"]


def test_stale_financial_index_requires_rebuild(financial_index) -> None:
    index_path, _ = financial_index
    manifest_path = index_path.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mapping_version"] = "old-version"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = financial_analysis(
        _call(
            {
                "operation": "metric_query",
                "company_ids": ["000001.SZ"],
                "metric_codes": ["REVENUE"],
                "report_periods": ["2024-12-31"],
            }
        )
    )
    assert result.status.value == "failed"
    assert "stale or incomplete" in result.error.message


def _call(arguments: dict) -> ToolCall:
    return ToolCall(
        tool_call_id="CALL-001",
        tool_name=ToolName.FINANCIAL_ANALYSIS,
        arguments=arguments,
        reason="test",
    )


def _series(values: dict[str, float]) -> dict[str, dict]:
    return {
        period: {"value": value, "evidence_id": f"E-{period}"}
        for period, value in values.items()
    }


def _row(object_id: str, company_id: str, report_period: str, **metrics) -> dict:
    return {
        "object_id": object_id,
        "s_info_windcode": company_id,
        "wind_code": company_id,
        "ann_dt": "2025-03-31",
        "actual_ann_dt": "2025-03-31",
        "report_period": report_period,
        "statement_type": "408006000",
        "crncy_code": "CNY",
        "comp_type_code": "1",
        **metrics,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

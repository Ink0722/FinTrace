from pathlib import Path
import shutil
from uuid import uuid4

from schemas.tool_calls import ToolCall
from schemas.enums import ToolName
from tools.financial_risk.interface import financial_risk_analysis
from tools.financial_risk.metrics import calculate_metrics, latest_metric_map
from tools.financial_risk.rules import run_rules
from tools.financial_risk.sample_data import load_sample_financial_records


def test_financial_metrics_are_calculated() -> None:
    records = load_sample_financial_records()
    metrics = latest_metric_map(calculate_metrics(records))
    assert metrics["inventory_growth"].value is not None
    assert metrics["cfo_to_net_profit"].value is not None


def test_financial_rules_trigger_sample_risks() -> None:
    records = load_sample_financial_records()
    signals = run_rules(latest_metric_map(calculate_metrics(records)))
    triggered = {signal.rule_id for signal in signals if signal.triggered}
    assert "FIN-CFO-001" in triggered
    assert "FIN-INV-001" in triggered


def test_financial_risk_tool_returns_evidence() -> None:
    result = financial_risk_analysis(
        ToolCall(
            tool_call_id="CALL-001",
            tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
            arguments={"company_ids": ["000001.SZ"]},
            reason="test",
        )
    )
    assert result.status.value == "success"
    assert result.data["risk_score"] > 0
    assert result.evidence


def test_financial_risk_prefers_csv_data(monkeypatch) -> None:
    test_root = write_financial_csv(
        [
            "company_id,company_name,report_period,statement_type,metric_code,metric_name,value,unit,currency,source_doc_id,source_path,page,evidence_id",
            "000777.SZ,CSV公司,2021-12-31,income_statement,REVENUE,营业收入,100,CNY,CNY,DOC-FIN-2021,data/raw/annual.pdf,80,EVID-FIN-001",
            "000777.SZ,CSV公司,2021-12-31,income_statement,NET_PROFIT,净利润,10,CNY,CNY,DOC-FIN-2021,data/raw/annual.pdf,81,EVID-FIN-002",
            "000777.SZ,CSV公司,2021-12-31,cashflow_statement,OPERATING_CASHFLOW,经营现金流,12,CNY,CNY,DOC-FIN-2021,data/raw/annual.pdf,82,EVID-FIN-003",
            "000777.SZ,CSV公司,2021-12-31,balance_sheet,INVENTORY,存货,20,CNY,CNY,DOC-FIN-2021,data/raw/annual.pdf,83,EVID-FIN-004",
            "000777.SZ,CSV公司,2021-12-31,balance_sheet,ACCOUNTS_RECEIVABLE,应收账款,15,CNY,CNY,DOC-FIN-2021,data/raw/annual.pdf,84,EVID-FIN-005",
            "000777.SZ,CSV公司,2021-12-31,income_statement,GROSS_PROFIT,毛利,30,CNY,CNY,DOC-FIN-2021,data/raw/annual.pdf,85,EVID-FIN-006",
            "000777.SZ,CSV公司,2021-12-31,income_statement,NON_RECURRING_PROFIT,非经常性损益,1,CNY,CNY,DOC-FIN-2021,data/raw/annual.pdf,86,EVID-FIN-007",
            "000777.SZ,CSV公司,2022-12-31,income_statement,REVENUE,营业收入,120,CNY,CNY,DOC-FIN-2022,data/raw/annual.pdf,80,EVID-FIN-008",
            "000777.SZ,CSV公司,2022-12-31,income_statement,NET_PROFIT,净利润,20,CNY,CNY,DOC-FIN-2022,data/raw/annual.pdf,81,EVID-FIN-009",
            "000777.SZ,CSV公司,2022-12-31,cashflow_statement,OPERATING_CASHFLOW,经营现金流,5,CNY,CNY,DOC-FIN-2022,data/raw/annual.pdf,82,EVID-FIN-010",
            "000777.SZ,CSV公司,2022-12-31,balance_sheet,INVENTORY,存货,60,CNY,CNY,DOC-FIN-2022,data/raw/annual.pdf,83,EVID-FIN-011",
            "000777.SZ,CSV公司,2022-12-31,balance_sheet,ACCOUNTS_RECEIVABLE,应收账款,35,CNY,CNY,DOC-FIN-2022,data/raw/annual.pdf,84,EVID-FIN-012",
            "000777.SZ,CSV公司,2022-12-31,income_statement,GROSS_PROFIT,毛利,36,CNY,CNY,DOC-FIN-2022,data/raw/annual.pdf,85,EVID-FIN-013",
            "000777.SZ,CSV公司,2022-12-31,income_statement,NON_RECURRING_PROFIT,非经常性损益,8,CNY,CNY,DOC-FIN-2022,data/raw/annual.pdf,86,EVID-FIN-014",
        ]
    )
    try:
        monkeypatch.setenv("FINANCIAL_DATA_SOURCE", "csv")
        monkeypatch.setenv("FINANCIAL_RECORDS_PATH", str(test_root / "financial_records.csv"))
        result = financial_risk_analysis(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
                arguments={"company_ids": ["000777.SZ"]},
                reason="test",
            )
        )
        assert result.status.value == "success"
        assert result.data["data_source"] == "csv"
        assert "EVID-FIN-011" in {item.evidence_id for item in result.evidence}
        assert result.evidence[0].source.source_path == "data/raw/annual.pdf"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_financial_csv_company_without_records_returns_error(monkeypatch) -> None:
    test_root = write_financial_csv(
        [
            "company_id,report_period,statement_type,metric_code,metric_name,value,source_doc_id,evidence_id",
            "000777.SZ,2022-12-31,income_statement,REVENUE,营业收入,120,DOC-FIN,EVID-FIN-001",
        ]
    )
    try:
        monkeypatch.setenv("FINANCIAL_DATA_SOURCE", "csv")
        monkeypatch.setenv("FINANCIAL_RECORDS_PATH", str(test_root / "financial_records.csv"))
        result = financial_risk_analysis(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
                arguments={"company_ids": ["000888.SZ"]},
                reason="test",
            )
        )
        assert result.status.value == "failed"
        assert result.error.error_type.value == "DATA_NOT_AVAILABLE"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_financial_csv_validation_error(monkeypatch) -> None:
    test_root = write_financial_csv(
        [
            "company_id,report_period,statement_type,metric_code,metric_name,value,source_doc_id,evidence_id",
            "000777.SZ,,income_statement,REVENUE,营业收入,120,DOC-FIN,EVID-FIN-001",
        ]
    )
    try:
        monkeypatch.setenv("FINANCIAL_DATA_SOURCE", "csv")
        monkeypatch.setenv("FINANCIAL_RECORDS_PATH", str(test_root / "financial_records.csv"))
        result = financial_risk_analysis(
            ToolCall(
                tool_call_id="CALL-001",
                tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
                arguments={"company_ids": ["000777.SZ"]},
                reason="test",
            )
        )
        assert result.status.value == "failed"
        assert result.error.error_type.value == "VALIDATION_FAILED"
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_financial_tool_uses_plural_target_period_and_prior_history(monkeypatch) -> None:
    monkeypatch.setenv("FINANCIAL_DATA_SOURCE", "sample")
    result = financial_risk_analysis(
        ToolCall(
            tool_call_id="CALL-001",
            tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
            arguments={"company_ids": ["000001.SZ"], "report_periods": ["2022-12-31"]},
            reason="test",
        )
    )
    assert result.status.value == "success"
    assert result.data["company_ids"] == ["000001.SZ"]
    assert result.data["report_periods"] == ["2022-12-31"]
    assert result.data["triggered_rule_ids"]


def test_financial_tool_rejects_multiple_companies_in_current_implementation(monkeypatch) -> None:
    monkeypatch.setenv("FINANCIAL_DATA_SOURCE", "sample")
    result = financial_risk_analysis(
        ToolCall(
            tool_call_id="CALL-001",
            tool_name=ToolName.FINANCIAL_RISK_ANALYSIS,
            arguments={"company_ids": ["000001.SZ", "000002.SZ"]},
            reason="test",
        )
    )
    assert result.status.value == "failed"
    assert result.error.error_type.value == "INVALID_ARGUMENT"


def write_financial_csv(lines: list[str]) -> Path:
    test_root = Path(".tmp_tests") / f"financial_csv_{uuid4().hex}"
    test_root.mkdir(parents=True, exist_ok=True)
    (test_root / "financial_records.csv").write_text("\n".join(lines), encoding="utf-8")
    return test_root

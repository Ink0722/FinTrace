from __future__ import annotations

from tools.financial_analysis.repository import FinancialMetricRecord
from tools.financial_analysis.risk_catalog import RISK_RULE_VERSION, RiskRuleDefinition
from tools.financial_analysis.risk_rules import evaluate_rule


def run_risk_scan(*, company_id: str, periods: list[str], records: list[FinancialMetricRecord], rules: list[RiskRuleDefinition]) -> dict:
    ordered_periods = sorted(periods)
    series: dict[str, dict[str, dict]] = {}
    for record in records:
        series.setdefault(record.metric_code, {})[record.report_period] = {
            "value": record.value,
            "evidence_id": record.evidence_id,
        }
    signals = [evaluate_rule(rule, series, ordered_periods) for rule in rules]
    evaluated = [item["rule_id"] for item in signals if item["status"] != "insufficient_data"]
    skipped = [
        {"rule_id": item["rule_id"], "reason": "missing_inputs", "missing_inputs": item["missing_inputs"]}
        for item in signals
        if item["status"] == "insufficient_data"
    ]
    return {
        "operation": "risk_scan",
        "company_id": company_id,
        "periods_used": ordered_periods,
        "signals": signals,
        "triggered_signals": [item for item in signals if item["status"] == "triggered"],
        "rules_evaluated": evaluated,
        "rules_skipped": skipped,
        "coverage": {"requested_rule_count": len(rules), "evaluated_rule_count": len(evaluated), "coverage_rate": len(evaluated) / len(rules) if rules else 0.0},
        "rule_version": RISK_RULE_VERSION,
    }


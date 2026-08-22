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
    evaluated = [item["rule_id"] for item in signals if item["status"] in {"triggered", "not_triggered"}]
    skipped = []
    for item in signals:
        if item["status"] != "insufficient_data":
            continue
        minimum_periods_not_met = any(
            observation.get("insufficient_reason") == "minimum_periods_not_met"
            for observation in item["observations"]
        ) or not item["observations"]
        skipped.append({
            "rule_id": item["rule_id"],
            "reason": "minimum_periods_not_met" if minimum_periods_not_met else "missing_inputs",
            "missing_inputs": item["missing_inputs"],
        })
    not_applicable = [
        {"rule_id": item["rule_id"], "reason": "rule_not_applicable", "observations": item["observations"]}
        for item in signals
        if item["status"] == "not_applicable"
    ]
    return {
        "operation": "risk_scan",
        "company_id": company_id,
        "periods_used": ordered_periods,
        "signals": signals,
        "triggered_signals": [item for item in signals if item["status"] == "triggered"],
        "rules_evaluated": evaluated,
        "rules_skipped": skipped,
        "rules_not_applicable": not_applicable,
        "coverage": {
            "requested_rule_count": len(rules),
            "evaluated_rule_count": len(evaluated),
            "not_applicable_rule_count": len(not_applicable),
            "insufficient_data_rule_count": len(skipped),
            "coverage_rate": len(evaluated) / len(rules) if rules else 0.0,
        },
        "rule_version": RISK_RULE_VERSION,
        "threshold_calibration": {
            "status": "uncalibrated",
            "basis": "expert_initial",
            "reason": "No frozen industry taxonomy or independent financial-risk gold set is currently available.",
        },
        "overall_score": None,
        "scoring_status": "disabled_until_calibrated",
    }

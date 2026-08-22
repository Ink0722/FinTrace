from __future__ import annotations

from collections.abc import Callable

from tools.financial_analysis.risk_catalog import RiskRuleDefinition


MetricSeries = dict[str, dict[str, dict]]


def evaluate_rule(rule: RiskRuleDefinition, series: MetricSeries, periods: list[str]) -> dict:
    missing = [
        {"metric_code": metric, "report_period": period}
        for metric in rule.required_metrics
        for period in periods
        if metric not in series or period not in series[metric]
    ]
    if missing:
        return _base_result(rule, "insufficient_data", [], {}, missing)

    evaluator = _EVALUATORS[rule.rule_id]
    calculated, triggered, observations = evaluator(series, periods, rule.thresholds)
    evidence_ids = [series[metric][period]["evidence_id"] for metric in rule.required_metrics for period in periods]
    result = _base_result(rule, "triggered" if triggered else "not_triggered", evidence_ids, calculated, [])
    result["observations"] = observations
    result["severity"] = _severity(rule.rule_id, calculated) if triggered else None
    return result


def _base_result(rule, status, evidence_ids, calculated, missing):
    return {
        "signal_id": f"SIGNAL-{rule.rule_id}",
        "rule_id": rule.rule_id,
        "name": rule.name,
        "topic": rule.topic,
        "status": status,
        "severity": None,
        "observations": [],
        "formula": rule.formula,
        "thresholds": rule.thresholds,
        "calculated_values": calculated,
        "missing_inputs": missing,
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
    }


def _growth(previous: float, current: float) -> float | None:
    return None if previous == 0 else (current - previous) / abs(previous)


def _ratio(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _cash_profit(series, periods, thresholds):
    first, last = periods[0], periods[-1]
    profit_growth = _growth(series["NET_PROFIT_PARENT"][first]["value"], series["NET_PROFIT_PARENT"][last]["value"])
    cash_growth = _growth(series["OPERATING_CASHFLOW"][first]["value"], series["OPERATING_CASHFLOW"][last]["value"])
    cash_profit = _ratio(series["OPERATING_CASHFLOW"][last]["value"], series["NET_PROFIT_PARENT"][last]["value"])
    divergence = profit_growth is not None and cash_growth is not None and profit_growth > 0 and cash_growth <= thresholds["cashflow_growth_max"]
    low_coverage = cash_profit is not None and cash_profit < thresholds["cashflow_to_profit_max"]
    values = {"profit_growth": profit_growth, "cashflow_growth": cash_growth, "cashflow_to_profit": cash_profit}
    return values, divergence or low_coverage, [{"from": first, "to": last, **values}]


def _growth_divergence(metric: str) -> Callable:
    def evaluate(series, periods, thresholds):
        first, last = periods[0], periods[-1]
        asset_growth = _growth(series[metric][first]["value"], series[metric][last]["value"])
        revenue_growth = _growth(series["REVENUE"][first]["value"], series["REVENUE"][last]["value"])
        gap = None if asset_growth is None or revenue_growth is None else asset_growth - revenue_growth
        triggered = asset_growth is not None and asset_growth > 0 and gap is not None and round(gap, 12) >= thresholds["growth_gap_min"]
        values = {f"{metric.lower()}_growth": asset_growth, "revenue_growth": revenue_growth, "growth_gap": gap}
        return values, triggered, [{"from": first, "to": last, **values}]
    return evaluate


def _liquidity(series, periods, thresholds):
    observations = []
    triggered = False
    for period in periods:
        current_ratio = _ratio(series["CURRENT_ASSETS"][period]["value"], series["CURRENT_LIABILITIES"][period]["value"])
        cash_coverage = _ratio(series["MONETARY_CAPITAL"][period]["value"], series["CURRENT_LIABILITIES"][period]["value"])
        hit = (current_ratio is not None and current_ratio < thresholds["current_ratio_min"]) or (cash_coverage is not None and cash_coverage < thresholds["cash_coverage_min"])
        triggered = triggered or hit
        observations.append({"report_period": period, "current_ratio": current_ratio, "cash_to_current_liabilities": cash_coverage, "triggered": hit})
    return {"period_observations": observations}, triggered, observations


def _margin(series, periods, thresholds):
    first, last = periods[0], periods[-1]
    def margins(period):
        revenue = series["REVENUE"][period]["value"]
        return _ratio(revenue - series["OPERATING_COST"][period]["value"], revenue), _ratio(series["OPERATING_PROFIT"][period]["value"], revenue)
    first_gross, first_operating = margins(first)
    last_gross, last_operating = margins(last)
    gross_change = None if first_gross is None or last_gross is None else last_gross - first_gross
    operating_change = None if first_operating is None or last_operating is None else last_operating - first_operating
    threshold = thresholds["margin_change_min"]
    triggered = (gross_change is not None and abs(gross_change) >= threshold) or (operating_change is not None and abs(operating_change) >= threshold)
    values = {"gross_margin_change": gross_change, "operating_margin_change": operating_change, "from_gross_margin": first_gross, "to_gross_margin": last_gross, "from_operating_margin": first_operating, "to_operating_margin": last_operating}
    return values, triggered, [{"from": first, "to": last, **values}]


def _severity(rule_id: str, values: dict) -> str:
    if rule_id in {"RECEIVABLE_REVENUE_DIVERGENCE", "INVENTORY_REVENUE_DIVERGENCE"}:
        return "high" if (values.get("growth_gap") or 0) >= 0.6 else "medium"
    if rule_id == "CASH_PROFIT_DIVERGENCE":
        return "high" if (values.get("cashflow_to_profit") is not None and values["cashflow_to_profit"] < 0) else "medium"
    return "medium"


_EVALUATORS = {
    "CASH_PROFIT_DIVERGENCE": _cash_profit,
    "RECEIVABLE_REVENUE_DIVERGENCE": _growth_divergence("ACCOUNTS_RECEIVABLE"),
    "INVENTORY_REVENUE_DIVERGENCE": _growth_divergence("INVENTORY"),
    "LIQUIDITY_PRESSURE": _liquidity,
    "MARGIN_VOLATILITY": _margin,
}

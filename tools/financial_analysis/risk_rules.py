from __future__ import annotations

from collections.abc import Callable

from tools.financial_analysis.risk_catalog import RiskRuleDefinition


MetricSeries = dict[str, dict[str, dict]]


def evaluate_rule(rule: RiskRuleDefinition, series: MetricSeries, periods: list[str]) -> dict:
    calculated, observations = _EVALUATORS[rule.rule_id](series, periods, rule.thresholds)
    statuses = [item["status"] for item in observations]
    if "triggered" in statuses:
        status = "triggered"
    elif "not_triggered" in statuses:
        status = "not_triggered"
    elif "not_applicable" in statuses:
        status = "not_applicable"
    else:
        status = "insufficient_data"

    missing_inputs = _unique_dicts(
        missing for observation in observations for missing in observation.get("missing_inputs", [])
    )
    evidence_ids = list(dict.fromkeys(
        evidence_id for observation in observations for evidence_id in observation.get("evidence_ids", [])
    ))
    calculated.update({
        "observation_count": len(observations),
        "applicable_observation_count": sum(value in {"triggered", "not_triggered"} for value in statuses),
        "triggered_observation_count": statuses.count("triggered"),
        "max_consecutive_triggered": _max_consecutive(statuses, "triggered"),
    })
    return {
        "signal_id": f"SIGNAL-{rule.rule_id}", "rule_id": rule.rule_id,
        "name": rule.name, "topic": rule.topic, "status": status,
        "severity": _severity(rule.rule_id, calculated, observations) if status == "triggered" else None,
        "observations": observations, "formula": rule.formula, "thresholds": rule.thresholds,
        "threshold_basis": rule.threshold_basis, "calibration_status": rule.calibration_status,
        "calculated_values": calculated, "missing_inputs": missing_inputs, "evidence_ids": evidence_ids,
    }


def _pair_growth_divergence(metric: str) -> Callable:
    def evaluate(series, periods, thresholds):
        observations = []
        for previous, current in zip(periods, periods[1:]):
            values, missing = _values(series, (metric, "REVENUE"), (previous, current))
            if missing:
                observations.append(_observation(previous, current, "insufficient_data", missing=missing))
                continue
            previous_asset, previous_revenue = values[(metric, previous)], values[("REVENUE", previous)]
            if previous_asset <= 0 or previous_revenue <= 0:
                observations.append(_observation(previous, current, "not_applicable", reason="non_positive_growth_base"))
                continue
            asset_growth = _growth(previous_asset, values[(metric, current)])
            revenue_growth = _growth(previous_revenue, values[("REVENUE", current)])
            gap = asset_growth - revenue_growth
            hit = asset_growth > 0 and round(gap, 12) >= thresholds["growth_gap_min"]
            observations.append(_observation(
                previous, current, _status(hit),
                evidence=_evidence(series, (metric, "REVENUE"), (previous, current)),
                values={f"{metric.lower()}_growth": asset_growth, "revenue_growth": revenue_growth, "growth_gap": gap},
            ))
        return {"pair_observations": observations}, observations
    return evaluate


def _cash_profit(series, periods, thresholds):
    observations = []
    metrics = ("NET_PROFIT_PARENT", "OPERATING_CASHFLOW")
    for previous, current in zip(periods, periods[1:]):
        values, missing = _values(series, metrics, (previous, current))
        if missing:
            observations.append(_observation(previous, current, "insufficient_data", missing=missing))
            continue
        previous_profit = values[("NET_PROFIT_PARENT", previous)]
        current_profit = values[("NET_PROFIT_PARENT", current)]
        if previous_profit <= 0 or current_profit <= 0:
            observations.append(_observation(previous, current, "not_applicable", reason="non_positive_profit"))
            continue
        profit_growth = _growth(previous_profit, current_profit)
        cash_growth = _growth(values[("OPERATING_CASHFLOW", previous)], values[("OPERATING_CASHFLOW", current)])
        cash_profit = values[("OPERATING_CASHFLOW", current)] / current_profit
        divergence = cash_growth is not None and profit_growth > 0 and cash_growth <= thresholds["cashflow_growth_max"]
        low_coverage = cash_profit < thresholds["cashflow_to_profit_max"]
        observations.append(_observation(
            previous, current, _status(divergence or low_coverage),
            evidence=_evidence(series, metrics, (previous, current)),
            values={"profit_growth": profit_growth, "cashflow_growth": cash_growth, "cashflow_to_profit": cash_profit},
        ))
    return {"pair_observations": observations}, observations


def _liquidity(series, periods, thresholds):
    metrics = ("CURRENT_ASSETS", "CURRENT_LIABILITIES", "MONETARY_CAPITAL")
    observations = []
    for period in periods:
        values, missing = _values(series, metrics, (period,))
        if missing:
            observations.append(_point_observation(period, "insufficient_data", missing=missing))
            continue
        liabilities = values[("CURRENT_LIABILITIES", period)]
        if liabilities <= 0:
            observations.append(_point_observation(period, "not_applicable", reason="non_positive_current_liabilities"))
            continue
        current_ratio = values[("CURRENT_ASSETS", period)] / liabilities
        cash_coverage = values[("MONETARY_CAPITAL", period)] / liabilities
        hit = current_ratio < thresholds["current_ratio_min"] or cash_coverage < thresholds["cash_coverage_min"]
        observations.append(_point_observation(
            period, _status(hit), evidence=_evidence(series, metrics, (period,)),
            values={"current_ratio": current_ratio, "cash_to_current_liabilities": cash_coverage},
        ))
    return {"period_observations": observations}, observations


def _margin(series, periods, thresholds):
    metrics = ("REVENUE", "OPERATING_COST", "OPERATING_PROFIT")
    observations = []
    for previous, current in zip(periods, periods[1:]):
        values, missing = _values(series, metrics, (previous, current))
        if missing:
            observations.append(_observation(previous, current, "insufficient_data", missing=missing))
            continue
        if values[("REVENUE", previous)] <= 0 or values[("REVENUE", current)] <= 0:
            observations.append(_observation(previous, current, "not_applicable", reason="non_positive_revenue"))
            continue
        def margins(period):
            revenue = values[("REVENUE", period)]
            return (revenue - values[("OPERATING_COST", period)]) / revenue, values[("OPERATING_PROFIT", period)] / revenue
        previous_gross, previous_operating = margins(previous)
        current_gross, current_operating = margins(current)
        gross_change = current_gross - previous_gross
        operating_change = current_operating - previous_operating
        hit = abs(gross_change) >= thresholds["margin_change_min"] or abs(operating_change) >= thresholds["margin_change_min"]
        observations.append(_observation(
            previous, current, _status(hit), evidence=_evidence(series, metrics, (previous, current)),
            values={"gross_margin_change": gross_change, "operating_margin_change": operating_change,
                    "from_gross_margin": previous_gross, "to_gross_margin": current_gross,
                    "from_operating_margin": previous_operating, "to_operating_margin": current_operating},
        ))
    return {"pair_observations": observations}, observations


def _negative_cashflow(series, periods, thresholds):
    metric = "OPERATING_CASHFLOW"
    observations, runs, negative_run = [], [], []
    for period in periods:
        values, missing = _values(series, (metric,), (period,))
        if missing:
            if negative_run:
                runs.append(negative_run)
                negative_run = []
            observations.append(_point_observation(period, "insufficient_data", missing=missing))
            continue
        value = values[(metric, period)]
        observations.append(_point_observation(
            period, "not_triggered", evidence=_evidence(series, (metric,), (period,)),
            values={"operating_cashflow": value, "negative": value < 0},
        ))
        if value < 0:
            negative_run.append(len(observations) - 1)
        elif negative_run:
            runs.append(negative_run)
            negative_run = []
    if negative_run:
        runs.append(negative_run)
    minimum = int(thresholds["consecutive_periods_min"])
    for run in runs:
        if len(run) >= minimum:
            for index in run:
                observations[index]["status"] = "triggered"
    return {"longest_negative_run": max((len(run) for run in runs), default=0)}, observations


def _sales_cash(series, periods, thresholds):
    metrics = ("CASH_RECEIVED_FROM_SALES", "REVENUE")
    observations, previous_ratio = [], None
    for period in periods:
        values, missing = _values(series, metrics, (period,))
        if missing:
            observations.append(_point_observation(period, "insufficient_data", missing=missing))
            previous_ratio = None
            continue
        revenue = values[("REVENUE", period)]
        if revenue <= 0:
            observations.append(_point_observation(period, "not_applicable", reason="non_positive_revenue"))
            previous_ratio = None
            continue
        ratio = values[("CASH_RECEIVED_FROM_SALES", period)] / revenue
        ratio_change = None if previous_ratio is None else ratio - previous_ratio
        hit = ratio < thresholds["cash_to_revenue_min"] or (ratio_change is not None and ratio_change <= thresholds["ratio_change_max"])
        observations.append(_point_observation(
            period, _status(hit), evidence=_evidence(series, metrics, (period,)),
            values={"sales_cash_to_revenue": ratio, "ratio_change": ratio_change},
        ))
        previous_ratio = ratio
    return {"period_observations": observations}, observations


def _leverage(series, periods, thresholds):
    metrics = ("TOTAL_LIABILITIES", "TOTAL_ASSETS")
    observations, previous_ratio = [], None
    for period in periods:
        values, missing = _values(series, metrics, (period,))
        if missing:
            observations.append(_point_observation(period, "insufficient_data", missing=missing))
            previous_ratio = None
            continue
        assets = values[("TOTAL_ASSETS", period)]
        if assets <= 0:
            observations.append(_point_observation(period, "not_applicable", reason="non_positive_total_assets"))
            previous_ratio = None
            continue
        ratio = values[("TOTAL_LIABILITIES", period)] / assets
        increase = None if previous_ratio is None else ratio - previous_ratio
        hit = ratio >= thresholds["debt_ratio_max"] or (increase is not None and increase >= thresholds["debt_ratio_increase_max"])
        observations.append(_point_observation(
            period, _status(hit), evidence=_evidence(series, metrics, (period,)),
            values={"debt_to_assets": ratio, "debt_ratio_change": increase},
        ))
        previous_ratio = ratio
    return {"period_observations": observations}, observations


def _values(series, metrics, periods):
    values, missing = {}, []
    for metric in metrics:
        for period in periods:
            record = series.get(metric, {}).get(period)
            if record is None:
                missing.append({"metric_code": metric, "report_period": period})
            else:
                values[(metric, period)] = record["value"]
    return values, missing


def _evidence(series, metrics, periods):
    return list(dict.fromkeys(
        series[metric][period]["evidence_id"] for metric in metrics for period in periods
        if metric in series and period in series[metric]
    ))


def _observation(previous, current, status, *, evidence=None, values=None, missing=None, reason=None):
    return {"from": previous, "to": current, "status": status, **(values or {}),
            "evidence_ids": evidence or [], "missing_inputs": missing or [],
            **({"not_applicable_reason": reason} if reason else {})}


def _point_observation(period, status, *, evidence=None, values=None, missing=None, reason=None):
    return {"report_period": period, "status": status, **(values or {}),
            "evidence_ids": evidence or [], "missing_inputs": missing or [],
            **({"not_applicable_reason": reason} if reason else {})}


def _growth(previous: float, current: float) -> float | None:
    return None if previous == 0 else (current - previous) / abs(previous)


def _status(triggered: bool) -> str:
    return "triggered" if triggered else "not_triggered"


def _max_consecutive(values: list[str], target: str) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value == target else 0
        longest = max(longest, current)
    return longest


def _unique_dicts(values):
    result, seen = [], set()
    for value in values:
        key = tuple(sorted(value.items()))
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _severity(rule_id: str, calculated: dict, observations: list[dict]) -> str:
    if calculated.get("max_consecutive_triggered", 0) >= 2:
        return "high"
    triggered = [item for item in observations if item["status"] == "triggered"]
    if rule_id in {"RECEIVABLE_REVENUE_DIVERGENCE", "INVENTORY_REVENUE_DIVERGENCE"}:
        return "high" if any((item.get("growth_gap") or 0) >= 0.6 for item in triggered) else "medium"
    if rule_id == "CASH_PROFIT_DIVERGENCE":
        return "high" if any(item.get("cashflow_to_profit", 1) < 0 for item in triggered) else "medium"
    if rule_id == "LIQUIDITY_PRESSURE":
        return "high" if any(item.get("current_ratio", 1) < 0.7 or item.get("cash_to_current_liabilities", 1) < 0.1 for item in triggered) else "medium"
    if rule_id == "MARGIN_VOLATILITY":
        return "high" if any(max(abs(item.get("gross_margin_change", 0)), abs(item.get("operating_margin_change", 0))) >= 0.2 for item in triggered) else "medium"
    if rule_id == "NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE":
        return "high" if calculated.get("longest_negative_run", 0) >= 3 else "medium"
    if rule_id == "SALES_CASH_REVENUE_DIVERGENCE":
        return "high" if any(item.get("sales_cash_to_revenue", 1) < 0.6 or (item.get("ratio_change") or 0) <= -0.4 for item in triggered) else "medium"
    if rule_id == "LEVERAGE_PRESSURE":
        return "high" if any(item.get("debt_to_assets", 0) >= 0.85 for item in triggered) else "medium"
    return "medium"


_EVALUATORS = {
    "CASH_PROFIT_DIVERGENCE": _cash_profit,
    "RECEIVABLE_REVENUE_DIVERGENCE": _pair_growth_divergence("ACCOUNTS_RECEIVABLE"),
    "INVENTORY_REVENUE_DIVERGENCE": _pair_growth_divergence("INVENTORY"),
    "LIQUIDITY_PRESSURE": _liquidity,
    "MARGIN_VOLATILITY": _margin,
    "NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE": _negative_cashflow,
    "SALES_CASH_REVENUE_DIVERGENCE": _sales_cash,
    "LEVERAGE_PRESSURE": _leverage,
}

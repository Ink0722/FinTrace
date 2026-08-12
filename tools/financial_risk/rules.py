from schemas.financial import FinancialMetric, RiskSignal


def _metric_value(metrics: dict[str, FinancialMetric], code: str) -> float | None:
    metric = metrics.get(code)
    return metric.value if metric else None


def _metric_evidence(metrics: dict[str, FinancialMetric], *codes: str) -> list[str]:
    evidence_ids: list[str] = []
    for code in codes:
        metric = metrics.get(code)
        if metric:
            evidence_ids.extend(metric.evidence_ids)
    return sorted(set(evidence_ids))


def run_rules(metrics: dict[str, FinancialMetric]) -> list[RiskSignal]:
    signals: list[RiskSignal] = []

    cfo_to_profit = _metric_value(metrics, "cfo_to_net_profit")
    profit_growth = _metric_value(metrics, "net_profit_growth")
    cfo_growth = _metric_value(metrics, "operating_cashflow_growth")
    triggered = (
        cfo_to_profit is not None
        and profit_growth is not None
        and cfo_growth is not None
        and profit_growth > 0.10
        and cfo_growth < 0
        and cfo_to_profit < 0.6
    )
    signals.append(
        RiskSignal(
            rule_id="FIN-CFO-001",
            name="净利润增长但经营现金流背离",
            triggered=triggered,
            severity="high" if triggered else "medium",
            score=20 if triggered else 0,
            metrics={
                "cfo_to_net_profit": cfo_to_profit,
                "net_profit_growth": profit_growth,
                "operating_cashflow_growth": cfo_growth,
            },
            thresholds={"net_profit_growth_min": 0.10, "operating_cashflow_growth_max": 0.0, "cfo_to_net_profit_max": 0.6},
            evidence_ids=_metric_evidence(metrics, "cfo_to_net_profit", "net_profit_growth", "operating_cashflow_growth"),
            explanation="净利润增长而经营现金流转弱，可能提示利润质量风险，需结合回款、收入确认和应收项目进一步核查。",
        )
    )

    inventory_growth = _metric_value(metrics, "inventory_growth")
    revenue_growth = _metric_value(metrics, "revenue_growth")
    turnover_change = _metric_value(metrics, "inventory_turnover_change")
    triggered = (
        inventory_growth is not None
        and revenue_growth is not None
        and turnover_change is not None
        and inventory_growth - revenue_growth >= 0.30
        and turnover_change <= -0.20
    )
    signals.append(
        RiskSignal(
            rule_id="FIN-INV-001",
            name="存货增长与营收增长背离",
            triggered=triggered,
            severity="high" if triggered else "medium",
            score=18 if triggered else 0,
            metrics={
                "inventory_growth": inventory_growth,
                "revenue_growth": revenue_growth,
                "inventory_turnover_change": turnover_change,
            },
            thresholds={"inventory_minus_revenue_growth_min": 0.30, "inventory_turnover_change_max": -0.20},
            evidence_ids=_metric_evidence(metrics, "inventory_growth", "revenue_growth", "inventory_turnover_change"),
            explanation="存货增速显著高于营收且周转下降，可能提示存货积压、跌价准备不足或收入交付节奏异常。",
        )
    )

    receivable_growth = _metric_value(metrics, "receivable_growth")
    triggered = (
        receivable_growth is not None
        and revenue_growth is not None
        and receivable_growth - revenue_growth >= 0.25
    )
    signals.append(
        RiskSignal(
            rule_id="FIN-AR-001",
            name="应收账款增长与营收增长背离",
            triggered=triggered,
            severity="medium" if triggered else "low",
            score=12 if triggered else 0,
            metrics={"receivable_growth": receivable_growth, "revenue_growth": revenue_growth},
            thresholds={"receivable_minus_revenue_growth_min": 0.25},
            evidence_ids=_metric_evidence(metrics, "receivable_growth", "revenue_growth"),
            explanation="应收账款增速明显高于收入增速，可能提示回款质量下降或收入确认质量风险。",
        )
    )

    non_recurring_ratio = _metric_value(metrics, "non_recurring_profit_ratio")
    triggered = non_recurring_ratio is not None and non_recurring_ratio >= 0.30
    signals.append(
        RiskSignal(
            rule_id="FIN-NR-001",
            name="非经常性损益依赖",
            triggered=triggered,
            severity="medium" if triggered else "low",
            score=10 if triggered else 0,
            metrics={"non_recurring_profit_ratio": non_recurring_ratio},
            thresholds={"non_recurring_profit_ratio_min": 0.30},
            evidence_ids=_metric_evidence(metrics, "non_recurring_profit_ratio"),
            explanation="非经常性损益占净利润比例较高时，需关注利润可持续性。",
        )
    )

    return signals

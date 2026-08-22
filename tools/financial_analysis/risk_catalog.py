from __future__ import annotations

from dataclasses import dataclass


RISK_RULE_VERSION = "financial-risk-rules-v2"


@dataclass(frozen=True)
class RiskRuleDefinition:
    rule_id: str
    name: str
    topic: str
    required_metrics: tuple[str, ...]
    formula: str
    thresholds: dict[str, float]
    threshold_basis: str = "expert_initial"
    calibration_status: str = "uncalibrated"


RISK_RULES: dict[str, RiskRuleDefinition] = {
    rule.rule_id: rule
    for rule in (
        RiskRuleDefinition(
            rule_id="CASH_PROFIT_DIVERGENCE",
            name="利润与经营现金流背离",
            topic="earnings_quality",
            required_metrics=("NET_PROFIT_PARENT", "OPERATING_CASHFLOW"),
            formula="for each adjacent comparable period: profit_growth > 0 and cashflow_growth <= -0.2, or positive_profit and cashflow_to_profit < 0.5",
            thresholds={"cashflow_growth_max": -0.2, "cashflow_to_profit_max": 0.5},
        ),
        RiskRuleDefinition(
            rule_id="RECEIVABLE_REVENUE_DIVERGENCE",
            name="应收账款与收入背离",
            topic="asset_quality",
            required_metrics=("ACCOUNTS_RECEIVABLE", "REVENUE"),
            formula="for each adjacent comparable period: receivable_growth - revenue_growth >= 0.3 and receivable_growth > 0",
            thresholds={"growth_gap_min": 0.3},
        ),
        RiskRuleDefinition(
            rule_id="INVENTORY_REVENUE_DIVERGENCE",
            name="存货与收入背离",
            topic="asset_quality",
            required_metrics=("INVENTORY", "REVENUE"),
            formula="for each adjacent comparable period: inventory_growth - revenue_growth >= 0.3 and inventory_growth > 0",
            thresholds={"growth_gap_min": 0.3},
        ),
        RiskRuleDefinition(
            rule_id="LIQUIDITY_PRESSURE",
            name="短期偿债压力",
            topic="solvency",
            required_metrics=("CURRENT_ASSETS", "CURRENT_LIABILITIES", "MONETARY_CAPITAL"),
            formula="for each report period: current_ratio < 1.0 or cash_to_current_liabilities < 0.2",
            thresholds={"current_ratio_min": 1.0, "cash_coverage_min": 0.2},
        ),
        RiskRuleDefinition(
            rule_id="MARGIN_VOLATILITY",
            name="成本利润率异常变化",
            topic="profitability",
            required_metrics=("REVENUE", "OPERATING_COST", "OPERATING_PROFIT"),
            formula="for each adjacent comparable period: abs(gross_margin_change) >= 0.1 or abs(operating_margin_change) >= 0.1",
            thresholds={"margin_change_min": 0.1},
        ),
        RiskRuleDefinition(
            rule_id="NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE",
            name="经营现金流持续为负",
            topic="earnings_quality",
            required_metrics=("OPERATING_CASHFLOW",),
            formula="operating_cashflow < 0 for at least 2 consecutive requested periods",
            thresholds={"consecutive_periods_min": 2},
        ),
        RiskRuleDefinition(
            rule_id="SALES_CASH_REVENUE_DIVERGENCE",
            name="销售收现与收入背离",
            topic="earnings_quality",
            required_metrics=("CASH_RECEIVED_FROM_SALES", "REVENUE"),
            formula="for each adjacent comparable period: sales_cash_to_revenue < 0.8 or ratio_change <= -0.2",
            thresholds={"cash_to_revenue_min": 0.8, "ratio_change_max": -0.2},
        ),
        RiskRuleDefinition(
            rule_id="LEVERAGE_PRESSURE",
            name="资产负债率压力",
            topic="solvency",
            required_metrics=("TOTAL_LIABILITIES", "TOTAL_ASSETS"),
            formula="for each report period: debt_to_assets >= 0.7, or adjacent increase >= 0.1",
            thresholds={"debt_ratio_max": 0.7, "debt_ratio_increase_max": 0.1},
        ),
    )
}


def select_rules(rule_ids: list[str] | None, focus_topics: list[str] | None) -> list[RiskRuleDefinition]:
    if rule_ids:
        return [RISK_RULES[rule_id] for rule_id in rule_ids]
    topics = set(focus_topics or [])
    return [rule for rule in RISK_RULES.values() if not topics or rule.topic in topics]

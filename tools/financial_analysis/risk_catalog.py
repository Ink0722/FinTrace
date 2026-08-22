from __future__ import annotations

from dataclasses import dataclass


RISK_RULE_VERSION = "financial-risk-rules-v1"


@dataclass(frozen=True)
class RiskRuleDefinition:
    rule_id: str
    name: str
    topic: str
    required_metrics: tuple[str, ...]
    formula: str
    thresholds: dict[str, float]


RISK_RULES: dict[str, RiskRuleDefinition] = {
    rule.rule_id: rule
    for rule in (
        RiskRuleDefinition(
            rule_id="CASH_PROFIT_DIVERGENCE",
            name="利润与经营现金流背离",
            topic="earnings_quality",
            required_metrics=("NET_PROFIT_PARENT", "OPERATING_CASHFLOW"),
            formula="profit_growth > 0 and cashflow_growth <= -0.2, or cashflow_to_profit < 0.5",
            thresholds={"cashflow_growth_max": -0.2, "cashflow_to_profit_max": 0.5},
        ),
        RiskRuleDefinition(
            rule_id="RECEIVABLE_REVENUE_DIVERGENCE",
            name="应收账款与收入背离",
            topic="asset_quality",
            required_metrics=("ACCOUNTS_RECEIVABLE", "REVENUE"),
            formula="receivable_growth - revenue_growth >= 0.3 and receivable_growth > 0",
            thresholds={"growth_gap_min": 0.3},
        ),
        RiskRuleDefinition(
            rule_id="INVENTORY_REVENUE_DIVERGENCE",
            name="存货与收入背离",
            topic="asset_quality",
            required_metrics=("INVENTORY", "REVENUE"),
            formula="inventory_growth - revenue_growth >= 0.3 and inventory_growth > 0",
            thresholds={"growth_gap_min": 0.3},
        ),
        RiskRuleDefinition(
            rule_id="LIQUIDITY_PRESSURE",
            name="短期偿债压力",
            topic="solvency",
            required_metrics=("CURRENT_ASSETS", "CURRENT_LIABILITIES", "MONETARY_CAPITAL"),
            formula="current_ratio < 1.0 or cash_to_current_liabilities < 0.2",
            thresholds={"current_ratio_min": 1.0, "cash_coverage_min": 0.2},
        ),
        RiskRuleDefinition(
            rule_id="MARGIN_VOLATILITY",
            name="成本利润率异常变化",
            topic="profitability",
            required_metrics=("REVENUE", "OPERATING_COST", "OPERATING_PROFIT"),
            formula="abs(gross_margin_change) >= 0.1 or abs(operating_margin_change) >= 0.1",
            thresholds={"margin_change_min": 0.1},
        ),
    )
}


def select_rules(rule_ids: list[str] | None, focus_topics: list[str] | None) -> list[RiskRuleDefinition]:
    if rule_ids:
        return [RISK_RULES[rule_id] for rule_id in rule_ids]
    topics = set(focus_topics or [])
    return [rule for rule in RISK_RULES.values() if not topics or rule.topic in topics]

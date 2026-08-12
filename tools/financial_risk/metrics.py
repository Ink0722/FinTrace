from collections import defaultdict

from schemas.evidence import Evidence, EvidenceSource
from schemas.financial import FinancialMetric, FinancialRecord


def build_financial_table(records: list[FinancialRecord]) -> dict[tuple[str, str], FinancialRecord]:
    return {(record.report_period, record.item_code): record for record in records}


def evidence_from_records(records: list[FinancialRecord]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for record in records:
        evidence.append(
            Evidence(
                evidence_id=financial_evidence_id(record),
                evidence_type="financial_statement",
                source=EvidenceSource(
                    document_id=record.source_document_id,
                    company_id=record.company_id,
                    document_type="annual_report",
                    page=record.source_page,
                    source_path=record.source_path,
                ),
                fact={
                    "item_code": record.item_code,
                    "period": record.report_period,
                    "value": record.value_cny,
                    "unit": "CNY",
                },
            )
        )
    return evidence


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / abs(previous)


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def calculate_metrics(records: list[FinancialRecord]) -> list[FinancialMetric]:
    table = build_financial_table(records)
    periods = sorted({record.report_period for record in records})
    company_id = records[0].company_id if records else ""
    metrics: list[FinancialMetric] = []

    def value(period: str, item_code: str) -> float | None:
        record = table.get((period, item_code))
        return record.value_cny if record else None

    def evid(period: str, *item_codes: str) -> list[str]:
        ids = []
        for item_code in item_codes:
            if (period, item_code) in table:
                ids.append(financial_evidence_id(table[(period, item_code)]))
        return ids

    previous_period: str | None = None
    turnover_history: dict[str, float | None] = {}
    for period in periods:
        revenue = value(period, "REVENUE")
        net_profit = value(period, "NET_PROFIT")
        cfo = value(period, "OPERATING_CASHFLOW")
        inventory = value(period, "INVENTORY")
        receivable = value(period, "ACCOUNTS_RECEIVABLE")
        gross_profit = value(period, "GROSS_PROFIT")
        non_recurring = value(period, "NON_RECURRING_PROFIT")

        metric_values = {
            "cfo_to_net_profit": ratio(cfo, net_profit),
            "inventory_turnover": ratio(revenue, inventory),
            "gross_margin": ratio(gross_profit, revenue),
            "non_recurring_profit_ratio": ratio(non_recurring, net_profit),
        }

        if previous_period:
            metric_values.update(
                {
                    "revenue_growth": pct_change(revenue, value(previous_period, "REVENUE")),
                    "net_profit_growth": pct_change(net_profit, value(previous_period, "NET_PROFIT")),
                    "operating_cashflow_growth": pct_change(cfo, value(previous_period, "OPERATING_CASHFLOW")),
                    "inventory_growth": pct_change(inventory, value(previous_period, "INVENTORY")),
                    "receivable_growth": pct_change(receivable, value(previous_period, "ACCOUNTS_RECEIVABLE")),
                    "inventory_turnover_change": pct_change(
                        metric_values["inventory_turnover"], turnover_history.get(previous_period)
                    ),
                }
            )

        turnover_history[period] = metric_values["inventory_turnover"]
        for metric_code, metric_value in metric_values.items():
            metrics.append(
                FinancialMetric(
                    company_id=company_id,
                    report_period=period,
                    metric_code=metric_code,
                    value=metric_value,
                    evidence_ids=evid(
                        period,
                        "REVENUE",
                        "NET_PROFIT",
                        "OPERATING_CASHFLOW",
                        "INVENTORY",
                        "ACCOUNTS_RECEIVABLE",
                        "GROSS_PROFIT",
                        "NON_RECURRING_PROFIT",
                    ),
                )
            )
        previous_period = period
    return metrics


def latest_metric_map(metrics: list[FinancialMetric]) -> dict[str, FinancialMetric]:
    grouped: dict[str, list[FinancialMetric]] = defaultdict(list)
    for metric in metrics:
        grouped[metric.metric_code].append(metric)
    return {code: sorted(items, key=lambda item: item.report_period)[-1] for code, items in grouped.items()}


def financial_evidence_id(record: FinancialRecord) -> str:
    return record.evidence_id or f"EVID-{record.source_document_id}-{record.item_code}"

from __future__ import annotations

from tools.financial_analysis.metric_catalog import METRIC_CATALOG
from tools.financial_analysis.repository import FinancialMetricRecord


def build_metric_query_result(
    records: list[FinancialMetricRecord],
    *,
    company_ids: list[str],
    report_periods: list[str],
    metric_codes: list[str],
) -> tuple[list[dict], list[dict]]:
    missing = find_missing_combinations(
        records,
        company_ids=company_ids,
        report_periods=report_periods,
        metric_codes=metric_codes,
    )
    values = []
    for record in records:
        value = record.as_dict()
        value["metric_name"] = METRIC_CATALOG[record.metric_code].name
        values.append(value)
    return values, missing


def find_missing_combinations(
    records: list[FinancialMetricRecord],
    *,
    company_ids: list[str],
    report_periods: list[str],
    metric_codes: list[str],
) -> list[dict]:
    found = {(record.company_id, record.report_period, record.metric_code) for record in records}
    return [
        {
            "company_id": company_id,
            "report_period": report_period,
            "metric_code": metric_code,
        }
        for company_id in company_ids
        for report_period in report_periods
        for metric_code in metric_codes
        if (company_id, report_period, metric_code) not in found
    ]

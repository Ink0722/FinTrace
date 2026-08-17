from __future__ import annotations

from collections import defaultdict

from tools.financial_analysis.repository import FinancialMetricRecord


def compare_periods(
    records: list[FinancialMetricRecord],
    *,
    company_id: str,
    report_periods: list[str],
    metric_codes: list[str],
    comparison_method: str,
) -> tuple[list[dict], list[str]]:
    by_key = {(item.metric_code, item.report_period): item for item in records}
    warnings: list[str] = []
    comparisons: list[dict] = []
    ordered_periods = sorted(report_periods)
    for metric_code in metric_codes:
        points = [
            _point(by_key.get((metric_code, period)), period)
            for period in ordered_periods
        ]
        changes = []
        for previous, current in zip(points, points[1:]):
            change = _change(previous, current, comparison_method)
            if (
                comparison_method in {"percent", "both"}
                and change.get("change_rate") is None
                and previous["value"] == 0
            ):
                warnings.append(
                    f"{metric_code} at {previous['report_period']} is zero; percentage change is undefined."
                )
            changes.append(change)
        cumulative = _change(points[0], points[-1], comparison_method)
        comparisons.append(
            {
                "comparison_dimension": "period",
                "company_id": company_id,
                "metric_code": metric_code,
                "points": points,
                "adjacent_changes": changes,
                "cumulative_change": cumulative,
            }
        )
    return comparisons, list(dict.fromkeys(warnings))


def compare_companies(
    records: list[FinancialMetricRecord],
    *,
    company_ids: list[str],
    report_period: str,
    metric_codes: list[str],
    comparison_method: str,
) -> tuple[list[dict], list[str]]:
    grouped: dict[str, dict[str, FinancialMetricRecord]] = defaultdict(dict)
    for record in records:
        grouped[record.metric_code][record.company_id] = record
    comparisons: list[dict] = []
    warnings: list[str] = []
    for metric_code in metric_codes:
        points = [
            _company_point(grouped[metric_code].get(company_id), company_id)
            for company_id in company_ids
        ]
        ranked = sorted(
            (point for point in points if point["value"] is not None),
            key=lambda point: point["value"],
            reverse=True,
        )
        spread = None
        if len(ranked) >= 2:
            spread = _change(ranked[-1], ranked[0], comparison_method)
        company_types = {
            point["company_type_code"]
            for point in points
            if point["company_type_code"] is not None
        }
        if len(company_types) > 1:
            warnings.append(
                f"{metric_code} comparison includes different company_type_code values: {sorted(company_types)}."
            )
        comparisons.append(
            {
                "comparison_dimension": "company",
                "report_period": report_period,
                "metric_code": metric_code,
                "points": points,
                "ranking": [point["company_id"] for point in ranked],
                "max_min_spread": spread,
            }
        )
    return comparisons, warnings


def _point(record: FinancialMetricRecord | None, report_period: str) -> dict:
    if record is None:
        return {"report_period": report_period, "value": None, "evidence_id": None}
    return {
        "report_period": report_period,
        "period_type": record.period_type,
        "value": record.value,
        "currency": record.currency,
        "value_nature": record.value_nature,
        "evidence_id": record.evidence_id,
    }


def _company_point(record: FinancialMetricRecord | None, company_id: str) -> dict:
    if record is None:
        return {
            "company_id": company_id,
            "value": None,
            "company_type_code": None,
            "evidence_id": None,
        }
    return {
        "company_id": company_id,
        "value": record.value,
        "currency": record.currency,
        "company_type_code": record.company_type_code,
        "evidence_id": record.evidence_id,
    }


def _change(previous: dict, current: dict, comparison_method: str) -> dict:
    previous_value = previous.get("value")
    current_value = current.get("value")
    result = {
        "from": previous.get("report_period") or previous.get("company_id"),
        "to": current.get("report_period") or current.get("company_id"),
    }
    if previous_value is None or current_value is None:
        if comparison_method in {"absolute", "both"}:
            result["change_amount"] = None
        if comparison_method in {"percent", "both"}:
            result["change_rate"] = None
        return result
    if comparison_method in {"absolute", "both"}:
        result["change_amount"] = current_value - previous_value
    if comparison_method in {"percent", "both"}:
        result["change_rate"] = (
            None if previous_value == 0 else (current_value - previous_value) / abs(previous_value)
        )
    return result

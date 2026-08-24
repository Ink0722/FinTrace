from __future__ import annotations

import sqlite3
from datetime import date

from schemas.request import ParsedRequest
from tools.financial_analysis.config import FinancialAnalysisConfig
from tools.financial_analysis.metric_catalog import period_type
from tools.financial_analysis.repository import FinancialRepository


def resolve_financial_periods(parsed: ParsedRequest, knowledge_cutoff: str | None) -> ParsedRequest:
    """Resolve deterministic comparable periods for a financial risk investigation."""
    financial_families = {"financial_investigation", "financial_metric_query", "financial_metric_compare"}
    if parsed.task_family not in financial_families or not parsed.entities:
        return parsed
    if parsed.task_family == "financial_investigation" and len(parsed.entities) != 1:
        return parsed

    requested = list(parsed.periods)
    if parsed.task_family != "financial_investigation" and requested:
        return parsed.model_copy(update={
            "requested_periods": requested,
            "target_period": requested[-1],
            "period_type": period_type(requested[-1]),
            "period_resolution_mode": "explicit",
        })
    if len(requested) >= 2:
        return parsed.model_copy(update={
            "requested_periods": requested,
            "target_period": requested[-1],
            "period_type": period_type(requested[-1]),
            "period_resolution_mode": "explicit",
        })

    try:
        cutoff = date.fromisoformat(knowledge_cutoff) if knowledge_cutoff else None
        config = FinancialAnalysisConfig.from_env()
        repository = FinancialRepository(config.index_path)
        available_by_company = {
            company_id: repository.list_available_periods(company_id=company_id, knowledge_cutoff=cutoff)
            for company_id in parsed.entities
        } if repository.available() else {}
    except (OSError, ValueError, sqlite3.Error):
        available_by_company = {}

    available = available_by_company.get(parsed.entities[0], [])

    if parsed.task_family != "financial_investigation":
        sets = [set(items) for items in available_by_company.values() if items]
        common = sorted(set.intersection(*sets)) if len(sets) == len(parsed.entities) and sets else []
        if parsed.task_family == "financial_metric_compare" and len(parsed.entities) == 1:
            groups = {
                kind: sorted(value for value, value_kind in common if value_kind == kind)
                for kind in {value_kind for _, value_kind in common}
            }
            candidates = [values[-2:] for values in groups.values() if len(values) >= 2]
            if candidates:
                selected = max(candidates, key=lambda values: values[-1])
                return parsed.model_copy(update={
                    "periods": selected,
                    "requested_periods": [],
                    "target_period": selected[-1],
                    "period_type": period_type(selected[-1]),
                    "period_resolution_mode": "latest_two_comparable",
                })
        if common:
            latest = common[-1]
            return parsed.model_copy(update={
                "periods": [latest[0]],
                "requested_periods": [],
                "target_period": latest[0],
                "period_type": latest[1],
                "period_resolution_mode": "latest_available",
            })
        return parsed.model_copy(update={"period_resolution_mode": "data_unavailable"})

    if requested:
        target = requested[0]
        target_type = period_type(target)
        historical = [value for value, kind in available if kind == target_type and value <= target]
        resolved = sorted(set([*historical, target]))
        return parsed.model_copy(update={
            "periods": resolved,
            "requested_periods": requested,
            "target_period": target,
            "period_type": target_type,
            "period_resolution_mode": "history_until_target",
        })

    annual = [value for value, kind in available if kind == "FY"]
    if annual:
        return parsed.model_copy(update={
            "periods": annual,
            "requested_periods": [],
            "target_period": annual[-1],
            "period_type": "FY",
            "period_resolution_mode": "all_available_fy",
        })

    # Some companies only expose interim statements. Keep one comparable
    # period type instead of mixing Q1/H1/Q3 figures with incompatible scopes.
    groups = {
        kind: [value for value, value_kind in available if value_kind == kind]
        for kind in {value_kind for _, value_kind in available}
    }
    groups = {kind: values for kind, values in groups.items() if values}
    if groups:
        priority = {"Q3_YTD": 3, "H1": 2, "Q1": 1}
        selected_type, selected = max(
            groups.items(), key=lambda item: (len(item[1]), priority.get(item[0], 0))
        )
        selected = sorted(selected)
        return parsed.model_copy(update={
            "periods": selected,
            "requested_periods": [],
            "target_period": selected[-1],
            "period_type": selected_type,
            "period_resolution_mode": "all_available_comparable",
        })
    return parsed.model_copy(update={
        "requested_periods": [],
        "period_resolution_mode": "data_unavailable",
    })

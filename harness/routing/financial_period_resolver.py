from __future__ import annotations

import sqlite3
from datetime import date

from schemas.request import ParsedRequest
from tools.financial_analysis.config import FinancialAnalysisConfig
from tools.financial_analysis.metric_catalog import period_type
from tools.financial_analysis.repository import FinancialRepository


def resolve_financial_periods(parsed: ParsedRequest, knowledge_cutoff: str | None) -> ParsedRequest:
    """Resolve deterministic comparable periods for a financial risk investigation."""
    if parsed.task_family != "financial_investigation" or len(parsed.entities) != 1:
        return parsed

    requested = list(parsed.periods)
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
        available = repository.list_available_periods(
            company_id=parsed.entities[0], knowledge_cutoff=cutoff,
        ) if repository.available() else []
    except (OSError, ValueError, sqlite3.Error):
        available = []

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

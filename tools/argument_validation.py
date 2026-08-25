"""Shared Pydantic preflight for every implemented tool operation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from tools.document_search.interface import DocumentSearchArguments
from tools.event_timeline.interface import EventTimelineArguments
from tools.financial_analysis.interface import FinancialAnalysisArguments, RiskScanArguments
from tools.ownership_analysis.interface import OwnershipAnalysisArguments, PenetrationArguments
from tools.research_analysis.interface import ResearchArguments


ARGUMENT_MODELS: dict[tuple[str, str], type[BaseModel]] = {
    ("document_search", "search"): DocumentSearchArguments,
    ("financial_analysis", "metric_query"): FinancialAnalysisArguments,
    ("financial_analysis", "metric_compare"): FinancialAnalysisArguments,
    ("financial_analysis", "risk_scan"): RiskScanArguments,
    ("ownership_analysis", "holding_query"): OwnershipAnalysisArguments,
    ("ownership_analysis", "holding_compare"): OwnershipAnalysisArguments,
    ("ownership_analysis", "penetration"): PenetrationArguments,
    ("event_timeline", "event_query"): EventTimelineArguments,
    ("event_timeline", "event_cluster"): EventTimelineArguments,
    ("research_analysis", "view_query"): ResearchArguments,
}


@dataclass(frozen=True)
class ArgumentPreflight:
    normalized_arguments: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


def validate_tool_arguments(
    tool_name: str,
    operation: str,
    arguments: dict[str, Any],
) -> ArgumentPreflight:
    model = ARGUMENT_MODELS.get((str(tool_name), str(operation)))
    if model is None:
        return ArgumentPreflight(errors=[f"no argument schema registered for {tool_name}.{operation}"])
    try:
        parsed = model.model_validate(arguments)
    except ValidationError as exc:
        return ArgumentPreflight(errors=_format_errors(exc))
    return ArgumentPreflight(normalized_arguments=parsed.model_dump(mode="json", exclude_unset=True))


def tool_argument_schema(tool_name: str | None, operation: str | None) -> dict[str, Any]:
    model = ARGUMENT_MODELS.get((str(tool_name), str(operation)))
    return model.model_json_schema() if model is not None else {}


def allowed_argument_fields(tool_name: str | None, operation: str | None) -> set[str]:
    model = ARGUMENT_MODELS.get((str(tool_name), str(operation)))
    return set(model.model_fields) if model is not None else set()


def _format_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for item in exc.errors(include_url=False):
        location = ".".join(str(value) for value in item.get("loc") or ()) or "arguments"
        errors.append(f"tool schema {location}: {item.get('msg', 'invalid value')}")
    return errors

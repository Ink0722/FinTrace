from harness.routing.capability_registry import implemented_operations
from tools.argument_validation import ARGUMENT_MODELS, validate_tool_arguments


def test_every_implemented_operation_has_an_argument_schema() -> None:
    assert set(ARGUMENT_MODELS) == implemented_operations()


def test_tool_argument_preflight_normalizes_effective_arguments() -> None:
    result = validate_tool_arguments(
        "event_timeline",
        "event_query",
        {
            "operation": "event_query",
            "entity_ids": ["600519.sh"],
            "end_date": "2024-12-31",
            "knowledge_cutoff": "2026-05-28",
        },
    )

    assert result.errors == []
    assert result.normalized_arguments == {
        "operation": "event_query",
        "entity_ids": ["600519.SH"],
        "end_date": "2024-12-31",
        "knowledge_cutoff": "2026-05-28",
    }


def test_tool_argument_preflight_rejects_extra_fields_before_execution() -> None:
    result = validate_tool_arguments(
        "event_timeline",
        "event_query",
        {
            "operation": "event_query",
            "entity_ids": ["600519.SH"],
            "time_mode": "latest",
        },
    )

    assert any("time_mode" in error and "extra inputs" in error.lower() for error in result.errors)


def test_financial_preflight_rejects_incomplete_metric_query() -> None:
    result = validate_tool_arguments(
        "financial_analysis",
        "metric_query",
        {"operation": "metric_query", "company_ids": ["600519.SH"]},
    )

    assert any("metric_codes" in error for error in result.errors)
    assert any("report_periods" in error for error in result.errors)

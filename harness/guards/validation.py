from schemas.tool_calls import ExecutionPlan
from schemas.tool_results import ToolResult


def validate_plan(plan: ExecutionPlan) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    deprecated_scalar_parameters = {
        "company_id",
        "report_period",
        "comparison_report_periods",
        "start_report_period",
        "end_report_period",
        "target_company_id",
    }
    collection_parameters = {"company_ids", "report_periods", "entity_ids", "metric_codes"}
    for call in plan.tool_calls:
        key = (call.tool_name.value, repr(sorted(call.arguments.items())))
        if key in seen:
            errors.append(f"duplicate tool call: {call.tool_name.value}")
        seen.add(key)
        for parameter in deprecated_scalar_parameters.intersection(call.arguments):
            errors.append(f"deprecated scalar parameter: {call.tool_name.value}.{parameter}")
        for parameter in collection_parameters.intersection(call.arguments):
            if not isinstance(call.arguments[parameter], list):
                errors.append(f"collection parameter must be a list: {call.tool_name.value}.{parameter}")
    if len(plan.tool_calls) > 2:
        errors.append("MVP supports at most two tool calls per turn")
    return errors


def validate_tool_result(result: ToolResult) -> list[str]:
    errors: list[str] = []
    if result.status.value == "success" and result.error is not None:
        errors.append("successful result must not include error")
    return errors

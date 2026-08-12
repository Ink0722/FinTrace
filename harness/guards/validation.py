from schemas.tool_calls import ExecutionPlan
from schemas.tool_results import ToolResult


def validate_plan(plan: ExecutionPlan) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for call in plan.tool_calls:
        key = (call.tool_name.value, repr(sorted(call.arguments.items())))
        if key in seen:
            errors.append(f"duplicate tool call: {call.tool_name.value}")
        seen.add(key)
    if len(plan.tool_calls) > 2:
        errors.append("MVP supports at most two tool calls per turn")
    return errors


def validate_tool_result(result: ToolResult) -> list[str]:
    errors: list[str] = []
    if result.status.value == "success" and result.error is not None:
        errors.append("successful result must not include error")
    return errors

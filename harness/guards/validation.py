from schemas.tool_results import ToolResult


def validate_tool_result(result: ToolResult) -> list[str]:
    errors: list[str] = []
    if result.status.value == "success" and result.error is not None:
        errors.append("successful result must not include error")
    return errors

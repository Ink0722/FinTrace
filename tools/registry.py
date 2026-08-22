from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolResult
from tools.document_search.interface import document_search
from tools.event_timeline.interface import event_timeline
from tools.financial_analysis.interface import financial_analysis
from tools.ownership_analysis.interface import ownership_analysis
from tools.research_analysis.interface import research_analysis


def execute_tool(call: ToolCall) -> ToolResult:
    """Dispatch a validated ToolCall to the concrete tool implementation."""
    if call.tool_name == ToolName.DOCUMENT_SEARCH:
        return document_search(call)
    if call.tool_name == ToolName.OWNERSHIP_ANALYSIS:
        return ownership_analysis(call)
    if call.tool_name == ToolName.EVENT_TIMELINE:
        return event_timeline(call)
    if call.tool_name == ToolName.FINANCIAL_ANALYSIS:
        return financial_analysis(call)
    if call.tool_name == ToolName.RESEARCH_ANALYSIS:
        return research_analysis(call)

    return ToolResult(
        tool_call_id=call.tool_call_id,
        tool_name=call.tool_name,
        status=ToolStatus.FAILED,
        error=ToolError(
            error_type=ErrorType.UNSUPPORTED_QUERY,
            message=f"Unsupported tool: {call.tool_name}",
            retryable=False,
        ),
    )

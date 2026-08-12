from schemas.agent_state import AgentState, UserRequest
from schemas.enums import ToolName
from schemas.tool_calls import ToolCall


def test_agent_state_minimal() -> None:
    state = AgentState(session_id="S1", user_request=UserRequest(raw_query="分析A公司"))
    assert state.session_id == "S1"
    assert state.user_request.raw_query == "分析A公司"


def test_tool_call_schema() -> None:
    call = ToolCall(
        tool_call_id="CALL-001",
        tool_name=ToolName.DOCUMENT_SEARCH,
        arguments={"query": "问询函"},
        reason="test",
    )
    assert call.tool_name == ToolName.DOCUMENT_SEARCH

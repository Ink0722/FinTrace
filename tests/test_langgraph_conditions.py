from schemas.agent_state import AgentState, CurrentContext
from schemas.enums import ErrorType, ToolName, ToolStatus
from schemas.tool_calls import ToolCall
from schemas.tool_results import ToolError, ToolResult
from harness.graph.nodes import resolve_request_node, review_evidence_node
from harness.graph.workflow import run_agent
from harness.memory.session_store import SessionStore
from schemas.evidence import Evidence
from schemas.request import AgentAction, ParsedRequest, ToolCallEntry


def test_missing_company_clarifies_instead_of_guessing() -> None:
    state = run_agent("净利润是多少", session_id="TEST-GATE-1")
    assert state.answer_status == "clarification_required"
    assert state.workflow_status == "clarification_required"
    assert "上市公司" in state.final_answer
    assert state.tool_call_history == []
    assert "build_clarification" in state.executed_nodes


def test_realtime_request_refused() -> None:
    state = run_agent("600519.SH 现在股价多少", session_id="TEST-GATE-2")
    assert state.answer_status == "unsupported"
    assert "行情" in state.final_answer
    assert state.tool_call_history == []


def test_obvious_market_boundary_skips_all_llm_calls() -> None:
    state = run_agent("今天有哪些龙虎榜和主力资金净流入股票", session_id="TEST-GATE-MARKET")
    assert state.answer_status == "unsupported"
    assert state.tool_call_history == []
    assert state.llm_calls == []


def test_market_technical_boundary_skips_tools_and_llm_calls() -> None:
    for index, question in enumerate((
        "武汉凡谷金叉",
        "万方发展主力控仓比例和主力成本是多少",
        "近1月横盘且筹码高度集中的股票有哪些",
    )):
        state = run_agent(question, session_id=f"TEST-GATE-TECHNICAL-{index}")
        assert state.answer_status == "unsupported"
        assert state.tool_call_history == []
        assert state.llm_calls == []


def test_direct_path_skips_planner() -> None:
    state = run_agent("600519.SH 2024年营业收入是多少", session_id="TEST-GATE-3")
    assert state.routing_mode == "direct"
    assert "plan_next_action" not in state.executed_nodes
    assert state.total_tool_calls == 1


def test_investigation_loop_respects_budget(monkeypatch) -> None:
    def always_failing_tool(call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name,
            status=ToolStatus.FAILED,
            error=ToolError(error_type=ErrorType.DATA_NOT_AVAILABLE, message="no data", retryable=False),
        )

    monkeypatch.setattr("harness.graph.nodes.execute_tool", always_failing_tool)
    state = run_agent("结合公告分析600519.SH的存货风险", session_id="TEST-GATE-4")
    assert state.routing_mode == "investigation"
    assert state.total_tool_calls <= state.max_total_tool_calls
    assert state.step_count <= state.max_steps + 1
    assert state.termination_reason in {"no_new_evidence", "budget_exhausted", None}
    # Without a configured LLM the turn ends in the structured-error branch.
    assert state.answer_status in {"insufficient_evidence", "failed"}


def test_pronoun_inherits_from_session_context() -> None:
    SessionStore().save(
        "TEST-GATE-5",
        current_context=CurrentContext(company_ids=["600519.SH"]),
        conversation_summary="",
        verified_findings=[],
        recent_messages=[],
        turn_count=1,
    )
    second = run_agent("这家公司十大股东是谁", session_id="TEST-GATE-5")
    assert second.turn_id == 2
    assert second.parsed_request is not None
    assert second.parsed_request.entities == ["600519.SH"]
    assert second.tool_call_history and second.tool_call_history[0].tool_name == "ownership_analysis"


def test_explicit_industry_topic_clears_previous_company_context() -> None:
    state = AgentState(
        session_id="TEST-TOPIC-SWITCH",
        current_context=CurrentContext(company_ids=["600519.SH"], company_names=["贵州茅台"]),
        user_request={"raw_query": "新能源汽车上下游情况"},
    )

    resolved = resolve_request_node(state)

    assert resolved.parsed_request is not None
    assert resolved.parsed_request.entities == []
    assert resolved.current_context.company_ids == []
    assert resolved.current_context.company_names == []


def test_explicit_company_replaces_previous_company_context() -> None:
    state = AgentState(
        session_id="TEST-COMPANY-SWITCH",
        current_context=CurrentContext(company_ids=["600519.SH"], company_names=["贵州茅台"]),
        user_request={"raw_query": "平安银行2024年营业收入是多少"},
    )

    resolved = resolve_request_node(state)

    assert resolved.parsed_request is not None
    assert resolved.parsed_request.entities == ["000001.SZ"]
    assert resolved.current_context.company_ids == ["000001.SZ"]
    assert resolved.current_context.company_names == ["平安银行"]


def test_persisted_session_keeps_user_and_assistant_messages() -> None:
    run_agent("净利润是多少", session_id="TEST-MESSAGE-PAIR")
    loaded = SessionStore().load("TEST-MESSAGE-PAIR")
    roles = [item["role"] for item in loaded["recent_messages"]]
    assert roles[-2:] == ["user", "assistant"]


def test_state_supports_new_pipeline_fields() -> None:
    state = AgentState.model_validate(
        {
            "session_id": "S",
            "user_request": {"raw_query": "q"},
            "routing_mode": "investigation",
            "step_count": 2,
            "max_steps": 5,
            "answer_status": None,
        }
    )
    assert state.routing_mode == "investigation"
    assert state.step_count == 2


def test_company_only_overview_requires_two_evidence_sources(monkeypatch) -> None:
    monkeypatch.setattr("harness.graph.nodes._run_skill_with_breaker", lambda *args, **kwargs: (None, None))
    state = AgentState(
        session_id="OVERVIEW",
        user_request={"raw_query": "贵州茅台"},
        parsed_request=ParsedRequest(raw_query="贵州茅台", entities=["600519.SH"], task_family="unknown"),
        routing_mode="investigation",
        current_action=AgentAction(action="call_tool", tool_name="event_timeline", operation="event_query"),
        total_tool_calls=1,
        tool_call_history=[ToolCallEntry(tool_name="event_timeline", operation="event_query", status="success")],
        tool_results=[_evidence_result(ToolName.EVENT_TIMELINE, "EVENT-E1")],
    )
    review_evidence_node(state)
    assert state.next_action == "plan"

    state.current_action = AgentAction(action="call_tool", tool_name="research_analysis", operation="view_query")
    state.total_tool_calls = 2
    state.tool_call_history.append(
        ToolCallEntry(tool_name="research_analysis", operation="view_query", status="success")
    )
    state.tool_results.append(_evidence_result(ToolName.RESEARCH_ANALYSIS, "RESEARCH-E1"))
    review_evidence_node(state)
    assert state.next_action == "answer"
    assert state.termination_reason == "company_overview_coverage_reached"


def test_company_overview_uses_deterministic_planning_and_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "harness.graph.nodes._run_skill_with_breaker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("overview must not call planner/reviewer")),
    )
    state = AgentState(
        session_id="OVERVIEW-FAST",
        user_request={"raw_query": "贵州茅台"},
        parsed_request=ParsedRequest(raw_query="贵州茅台", entities=["600519.SH"], task_family="unknown"),
        routing_mode="investigation",
    )

    from harness.graph.nodes import plan_next_action_node

    plan_next_action_node(state)
    assert state.current_action is not None
    assert state.current_action.action == "call_tool"

    state.current_action = AgentAction(action="call_tool", tool_name="event_timeline", operation="event_query")
    state.total_tool_calls = 1
    state.tool_call_history = [ToolCallEntry(tool_name="event_timeline", operation="event_query", status="success")]
    state.tool_results = [_evidence_result(ToolName.EVENT_TIMELINE, "EVENT-FAST")]
    review_evidence_node(state)
    assert state.next_action == "plan"


def test_company_overview_does_not_count_document_detail_as_second_aspect(monkeypatch) -> None:
    monkeypatch.setattr("harness.graph.nodes._run_skill_with_breaker", lambda *args, **kwargs: (None, None))
    state = AgentState(
        session_id="OVERVIEW-DOCUMENT",
        user_request={"raw_query": "东吴证券"},
        parsed_request=ParsedRequest(raw_query="东吴证券", entities=["601555.SH"], task_family="unknown"),
        routing_mode="investigation",
        current_action=AgentAction(action="call_tool", tool_name="document_search", operation="search"),
        total_tool_calls=2,
        tool_call_history=[
                ToolCallEntry(
                    tool_name="event_timeline",
                operation="event_query",
                status="success",
            ),
                ToolCallEntry(
                    tool_name="document_search",
                operation="search",
                status="success",
            ),
        ],
        tool_results=[
            _evidence_result(ToolName.EVENT_TIMELINE, "EVENT-E1"),
            _evidence_result(ToolName.DOCUMENT_SEARCH, "DOCUMENT-E1"),
        ],
    )

    review_evidence_node(state)

    assert state.next_action == "plan"
    assert state.termination_reason is None


def test_no_data_result_skips_llm_evidence_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "harness.graph.nodes._run_skill_with_breaker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("empty evidence must not call reviewer")),
    )
    state = AgentState(
        session_id="NO-DATA-FAST",
        user_request={"raw_query": "分析600519.SH的风险"},
        parsed_request=ParsedRequest(
            raw_query="分析600519.SH的风险",
            entities=["600519.SH"],
            task_family="financial_investigation",
        ),
        routing_mode="investigation",
        current_action=AgentAction(action="call_tool", tool_name="financial_analysis", operation="risk_scan"),
        total_tool_calls=1,
        tool_results=[
            ToolResult(
                tool_call_id="CALL-NO-DATA",
                tool_name=ToolName.FINANCIAL_ANALYSIS,
                status=ToolStatus.FAILED,
                error=ToolError(
                    error_type=ErrorType.DATA_NOT_AVAILABLE,
                    message="no matching records",
                    retryable=False,
                ),
            )
        ],
    )

    review_evidence_node(state)

    assert state.llm_calls == []
    assert state.next_action == "plan"


def _evidence_result(tool_name: ToolName, evidence_id: str) -> ToolResult:
    return ToolResult(
        tool_call_id=f"CALL-{evidence_id}",
        tool_name=tool_name,
        status=ToolStatus.SUCCESS,
        data={"operation": "test"},
        evidence=[Evidence(evidence_id=evidence_id, evidence_type="test", source={}, fact={"summary": "test evidence"})],
    )

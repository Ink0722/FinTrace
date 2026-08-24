from __future__ import annotations

from harness.memory.manager import prepare_session_memory, select_relevant_findings, update_verified_findings
from harness.runtime_context import planner_runtime
from schemas.agent_state import AgentState, Message, UserRequest
from schemas.enums import ToolName, ToolStatus
from schemas.evidence import Evidence
from schemas.memory import MemoryUpdate, VerifiedFinding
from schemas.request import LlmCallRecord, ParsedRequest
from schemas.tool_results import ToolResult


def _record(status: str = "success") -> LlmCallRecord:
    return LlmCallRecord(
        prompt_id="fintrace.memory_summarizer",
        prompt_version="1.0.0",
        model="test-model",
        input_hash="abc",
        output_schema="MemoryUpdate",
        status=status,
    )


def _state(messages: list[Message], *, turn_id: int = 1) -> AgentState:
    return AgentState(
        session_id="MEMORY-TEST",
        turn_id=turn_id,
        messages=messages,
        user_request=UserRequest(raw_query="600519.SH营业收入"),
        parsed_request=ParsedRequest(
            raw_query="600519.SH营业收入",
            entities=["600519.SH"],
            task_family="financial_metric_query",
        ),
        final_answer='{"answer":"本轮回答","used_evidence_ids":[]}',
    )


def test_memory_summary_compacts_old_messages_and_keeps_answer() -> None:
    messages = [Message(role="user" if i % 2 == 0 else "assistant", content=f"消息{i}") for i in range(10)]
    state = _state(messages, turn_id=6)

    def runner(skill, runtime):
        assert skill == "memory_summarizer"
        assert runtime["messages_to_compress"]
        return MemoryUpdate(summary="较早对话摘要", open_questions=["继续分析现金流"]), _record()

    prepare_session_memory(state, skill_runner=runner)

    assert len(state.messages) == 8
    assert state.messages[-1] == Message(role="assistant", content="本轮回答")
    assert state.conversation_summary == "较早对话摘要\n未完成事项：继续分析现金流"
    assert state.llm_calls[-1].prompt_id == "fintrace.memory_summarizer"


def test_memory_summary_failure_keeps_recent_messages_without_failing_turn() -> None:
    messages = [Message(role="user", content=f"消息{i}") for i in range(12)]
    state = _state(messages, turn_id=7)

    prepare_session_memory(state, skill_runner=lambda *args: (None, _record("failed")))

    assert len(state.messages) == 12
    assert state.messages[-1].role == "assistant"
    assert state.final_answer is not None
    assert any("摘要更新失败" in warning for warning in state.warnings)


def test_memory_summary_is_deferred_after_an_earlier_llm_failure() -> None:
    state = _state([Message(role="user", content=f"消息{i}") for i in range(12)], turn_id=6)
    state.llm_calls.append(_record("failed"))
    called = False

    def runner(*args):
        nonlocal called
        called = True
        return MemoryUpdate(summary="不应执行"), _record()

    prepare_session_memory(state, skill_runner=runner)

    assert called is False
    assert len(state.messages) == 12
    assert any("延后会话摘要" in warning for warning in state.warnings)


def test_verified_findings_are_derived_from_evidence_and_deduplicated() -> None:
    evidence = Evidence(
        evidence_id="FIN-E1",
        evidence_type="financial_statement_metric",
        source={"company_id": "600519.SH"},
        fact={"metric_code": "REVENUE", "report_period": "2024-12-31", "value": 100},
    )
    result = ToolResult(
        tool_call_id="CALL-1",
        tool_name=ToolName.FINANCIAL_ANALYSIS,
        status=ToolStatus.SUCCESS,
        evidence=[evidence],
    )
    parsed = ParsedRequest(raw_query="q", entities=["600519.SH"], task_family="financial_metric_query")

    once = update_verified_findings([], [evidence], parsed=parsed, tool_results=[result], turn_id=3)
    twice = update_verified_findings(once, [evidence], parsed=parsed, tool_results=[result], turn_id=4)

    assert len(twice) == 1
    finding = VerifiedFinding.model_validate(twice[0])
    assert finding.evidence_ids == ["FIN-E1"]
    assert finding.company_id == "600519.SH"
    assert finding.source_tool == "financial_analysis"


def test_relevant_findings_require_matching_company_and_prefer_task_topic() -> None:
    findings = [
        _finding("F1", "600519.SH", "event", 4),
        _finding("F2", "600519.SH", "financial", 2),
        _finding("F3", "601919.SH", "financial", 9),
    ]
    parsed = ParsedRequest(raw_query="q", entities=["600519.SH"], task_family="financial_metric_query")

    selected = select_relevant_findings(findings, parsed)

    assert [item["finding_id"] for item in selected] == ["F2", "F1"]
    state = _state([Message(role="user", content="q")])
    state.relevant_memories = selected
    runtime = planner_runtime(state)
    assert runtime["memory_hints"] == selected
    assert runtime["conversation_summary"] == ""


def _finding(finding_id: str, company_id: str, topic: str, turn: int) -> dict:
    return VerifiedFinding(
        finding_id=finding_id,
        company_id=company_id,
        topic=topic,
        fact={"summary": finding_id},
        evidence_ids=[f"E-{finding_id}"],
        source_turn_id=turn,
    ).model_dump(mode="json")

"""Unit tests for the new routing / memory / evidence modules."""
from schemas.agent_state import AgentState, CurrentContext, Message, UserRequest
from schemas.enums import ToolName, ToolStatus
from schemas.evidence import Evidence
from schemas.request import AgentAction, ParsedRequest, ToolCallEntry
from schemas.tool_results import ToolResult
from tools.entity_resolver import EntityResolver

from harness.evidence.review import answer_status_from_review, review_evidence
from harness.memory.session_store import SessionStore
from harness.routing.action_validator import validate_action
from harness.routing.entities import extract_entities
from harness.routing.time_resolver import resolve_time


def make_state(parsed: ParsedRequest | None = None) -> AgentState:
    return AgentState(
        session_id="TEST-MOD",
        user_request=UserRequest(raw_query="测试"),
        parsed_request=parsed,
    )


# --- time resolver ---

def test_resolve_time_periods_and_relative() -> None:
    time = resolve_time("2024年一季度和2024年半年报")
    assert time.periods == ["2024-03-31", "2024-06-30"]
    assert resolve_time("2023年").periods == ["2023-12-31"]
    assert resolve_time("最新数据").unresolved == ["latest"]
    assert resolve_time("去年的业绩").unresolved == ["last_year"]
    assert resolve_time("去年的业绩", knowledge_cutoff="2025-06-30").periods == ["2024-12-31"]
    assert resolve_time("2022年以来的处罚").start_date == "2022-01-01"


# --- entity resolver + extraction ---

def test_resolver_name_code_and_ambiguous() -> None:
    resolver = EntityResolver()
    assert resolver.resolve_company("中远海控").status == "resolved"
    assert resolver.resolve_company("中远海控").company_id == "601919.SH"
    assert resolver.resolve_company("600519.SH").company_id == "600519.SH"
    assert resolver.resolve_company("600519").company_id == "600519.SH"
    assert resolver.resolve_company("不存在的公司XYZ").status == "not_found"


def test_extract_entities_pronoun_inheritance_single_candidate() -> None:
    resolver = EntityResolver()
    context = CurrentContext(company_ids=["600519.SH"])
    extraction = extract_entities("这家公司十大股东是谁", resolver, context)
    assert extraction.company_ids == ["600519.SH"]
    assert extraction.inherited


def test_extract_entities_no_pronoun_with_multiple_context_companies() -> None:
    resolver = EntityResolver()
    context = CurrentContext(company_ids=["600519.SH", "601919.SH"])
    extraction = extract_entities("这家公司十大股东是谁", resolver, context)
    assert extraction.company_ids == []


def test_extract_entities_demo_alias_still_works() -> None:
    resolver = EntityResolver()
    extraction = extract_entities("分析示例公司的存货风险", resolver)
    assert extraction.company_ids == ["000001.SZ"]


# --- action validator ---

def test_validate_action_rejects_cutoff_tampering_and_duplicates() -> None:
    parsed = ParsedRequest(raw_query="q", entities=["600519.SH"], metrics=["REVENUE"], periods=["2024-12-31"])
    state = make_state(parsed)
    action = AgentAction(
        action="call_tool",
        capability="financial_metric_query",
        tool_name="financial_analysis",
        operation="metric_query",
        arguments={
            "operation": "metric_query",
            "company_ids": ["600519.SH"],
            "metric_codes": ["REVENUE"],
            "report_periods": ["2024-12-31"],
            "knowledge_cutoff": "2030-01-01",
        },
        reason="test",
    )
    errors = validate_action(action, state, resolver=EntityResolver())
    assert any("knowledge_cutoff" in error for error in errors)


def test_validate_action_rejects_ambiguous_compare_dimension() -> None:
    state = make_state()
    action = AgentAction(
        action="call_tool",
        capability="financial_metric_compare",
        tool_name="financial_analysis",
        operation="metric_compare",
        arguments={
            "operation": "metric_compare",
            "company_ids": ["600519.SH", "601919.SH"],
            "metric_codes": ["REVENUE"],
            "report_periods": ["2023-12-31", "2024-12-31"],
        },
        reason="test",
    )
    errors = validate_action(action, state, resolver=EntityResolver())
    assert any("ambiguous dimension" in error for error in errors)


def test_validate_action_normalizes_company_names() -> None:
    state = make_state()
    action = AgentAction(
        action="call_tool",
        capability="ownership_snapshot",
        tool_name="ownership_analysis",
        operation="holding_query",
        arguments={"operation": "holding_query", "company_ids": ["中远海控"]},
        reason="test",
    )
    errors = validate_action(action, state, resolver=EntityResolver())
    assert errors == []
    assert action.arguments["company_ids"] == ["601919.SH"]


def test_document_queries_use_query_in_duplicate_fingerprint() -> None:
    state = make_state()
    state.tool_call_history = [
        ToolCallEntry(
            tool_name="document_search",
            operation="search",
            arguments={"query": "inventory impairment"},
            status="failed",
        )
    ]
    changed = AgentAction(
        action="call_tool",
        capability="document_retrieval",
        tool_name="document_search",
        operation="search",
        arguments={"query": "inventory composition"},
    )
    duplicate = changed.model_copy(update={"arguments": {"query": "  INVENTORY   IMPAIRMENT "}})
    assert validate_action(changed, state, resolver=EntityResolver()) == []
    assert any("duplicate tool call" in error for error in validate_action(duplicate, state, resolver=EntityResolver()))


def test_document_action_inherits_resolved_filters() -> None:
    parsed = ParsedRequest(
        raw_query="search announcement",
        entities=["600519.SH"],
        document_types=["announcement"],
        task_family="document_retrieval",
    )
    state = make_state(parsed)
    action = AgentAction(
        action="call_tool",
        capability="document_retrieval",
        tool_name="document_search",
        operation="search",
        arguments={"query": "inventory"},
    )
    assert validate_action(action, state, resolver=EntityResolver()) == []
    assert action.arguments["company_ids"] == ["600519.SH"]
    assert action.arguments["document_types"] == ["announcement"]


# --- evidence review ---

def _result(status: str, evidence: list[Evidence]) -> ToolResult:
    return ToolResult(
        tool_call_id="CALL-001",
        tool_name=ToolName.FINANCIAL_ANALYSIS,
        status=ToolStatus.SUCCESS if status == "success" else ToolStatus.FAILED,
        evidence=evidence,
    )


def test_review_evidence_statuses() -> None:
    evidence = [Evidence(evidence_id="E1", evidence_type="t", source={}, fact={})]
    state = make_state()
    state.tool_results = [_result("success", evidence)]
    assert review_evidence(state).status == "sufficient"
    assert answer_status_from_review(review_evidence(state)) == "answered"

    state.tool_results = [_result("success", evidence), _result("failed", [])]
    assert review_evidence(state).status == "partial"

    state.tool_results = [_result("success", [])]
    assert review_evidence(state).status == "insufficient"

    state.tool_results = []
    assert review_evidence(state).status == "insufficient"


# --- session store ---

def test_session_store_roundtrip() -> None:
    store = SessionStore()
    store.save(
        "TEST-SESSION",
        current_context=CurrentContext(company_ids=["600519.SH"], report_periods=["2024-12-31"]),
        conversation_summary="第一轮",
        verified_findings=[{"claim": "营收上升", "evidence_ids": ["E1"]}],
        recent_messages=[Message(role="user", content="问"), Message(role="assistant", content="答")],
    )
    loaded = store.load("TEST-SESSION")
    assert loaded["current_context"]["company_ids"] == ["600519.SH"]
    assert loaded["verified_findings"][0]["claim"] == "营收上升"
    assert len(loaded["recent_messages"]) == 2
    assert store.load("MISSING")["current_context"] == {}

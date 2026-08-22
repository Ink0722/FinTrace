from schemas.agent_state import CurrentContext
from schemas.request import ParsedRequest

from harness.routing.answerability import check_answerability, is_investigation
from harness.routing.capability_registry import CAPABILITIES, candidate_capabilities, implemented_operations
from harness.routing.direct_gate import build_direct_action
from harness.routing.entities import extract_document_types
from harness.routing.request_parser import parse_request
from tools.entity_resolver import EntityResolver

RESOLVER = EntityResolver()


def test_parse_resolves_company_name_via_alias_index() -> None:
    parsed = parse_request("中远海控2023年和2024年净利润变化多少", resolver=RESOLVER)
    assert parsed.entities == ["601919.SH"]
    assert parsed.task_family == "financial_metric_compare"
    assert parsed.metrics == ["NET_PROFIT_PARENT"]
    assert parsed.periods == ["2023-12-31", "2024-12-31"]
    assert parsed.comparison_type == "cross_period"
    assert not parsed.requires_investigation


def test_parse_never_defaults_to_sample_company() -> None:
    parsed = parse_request("净利润是多少", resolver=RESOLVER)
    assert parsed.entities == []
    assert "company_ids" in parsed.missing_slots


def test_parse_windcode_adjacent_to_chinese() -> None:
    parsed = parse_request("600519.SH的存货是多少", resolver=RESOLVER)
    assert parsed.entities == ["600519.SH"]
    assert parsed.metrics == ["INVENTORY"]


def test_parse_quarter_and_half_year_periods() -> None:
    parsed = parse_request("600519.SH 2024年一季度存货和2024年半年报存货", resolver=RESOLVER)
    assert parsed.periods == ["2024-03-31", "2024-06-30"]


def test_parse_flags_investigation_and_explanation() -> None:
    parsed = parse_request("结合公告分析600519.SH的存货风险", resolver=RESOLVER)
    assert parsed.requires_investigation
    assert parsed.task_family in {"financial_metric_query", "financial_investigation"}


def test_parse_realtime_and_prediction_families() -> None:
    assert parse_request("股价现在多少", resolver=RESOLVER).requires_realtime
    assert parse_request("明年会涨吗", resolver=RESOLVER).requires_prediction


def test_document_types_use_kb_vocabulary() -> None:
    assert extract_document_types("监管问询函有没有关注存货跌价准备") == ["announcement"]
    assert extract_document_types("研报怎么看盈利预测") == ["research_report"]
    parsed = parse_request("查600519.SH关于存货的年报内容", resolver=RESOLVER)
    assert parsed.document_types == ["announcement"]


def test_answerability_unsupported_realtime() -> None:
    parsed = parse_request("600519.SH 股价多少", resolver=RESOLVER)
    pre = check_answerability(parsed)
    assert pre.status == "unsupported"


def test_answerability_clarifies_missing_slots() -> None:
    parsed = parse_request("净利润是多少", resolver=RESOLVER)
    pre = check_answerability(parsed)
    assert pre.status == "clarification_required"
    assert "company_ids" in pre.missing_slots
    assert pre.clarification_question


def test_answerability_routeable_complete_request() -> None:
    parsed = parse_request("600519.SH 2024年营业收入是多少", resolver=RESOLVER)
    pre = check_answerability(parsed)
    assert pre.status == "routeable"


def test_investigation_requests_skip_hard_slot_requirements() -> None:
    parsed = parse_request("结合公告分析600519.SH的存货风险", resolver=RESOLVER)
    assert parsed.requires_investigation
    assert check_answerability(parsed).status == "routeable"
    assert is_investigation(parsed)


def test_direct_gate_builds_unique_metric_query() -> None:
    parsed = parse_request("600519.SH 2024年营业收入是多少", resolver=RESOLVER)
    action = build_direct_action(parsed)
    assert action is not None and action.action == "call_tool"
    assert action.tool_name == "financial_analysis"
    assert action.arguments["company_ids"] == ["600519.SH"]
    assert action.arguments["operation"] == "metric_query"


def test_direct_gate_defers_ambiguous_comparison() -> None:
    parsed = ParsedRequest(
        raw_query="比较甲乙公司2023和2024年净利润",
        entities=["600519.SH", "601919.SH"],
        periods=["2023-12-31", "2024-12-31"],
        metrics=["NET_PROFIT_PARENT"],
        task_family="financial_metric_compare",
    )
    assert build_direct_action(parsed) is None
    assert is_investigation(parsed) is False  # single capability, complete slots -> Gate C says direct-capable but ambiguous dimension defers


def test_direct_gate_ownership_snapshot() -> None:
    parsed = parse_request("600519.SH十大股东是谁", resolver=RESOLVER)
    action = build_direct_action(parsed)
    assert action is not None
    assert action.tool_name == "ownership_analysis"
    assert action.arguments["company_ids"] == ["600519.SH"]


def test_capability_registry_reflects_real_implementation() -> None:
    assert CAPABILITIES["financial_risk_scan"].implemented is True
    assert CAPABILITIES["ownership_penetration"].implemented is True
    assert ("financial_analysis", "metric_query") in implemented_operations()
    assert ("financial_analysis", "risk_scan") in implemented_operations()
    assert ("ownership_analysis", "penetration") in implemented_operations()
    assert CAPABILITIES["document_retrieval"].supports_knowledge_cutoff is True
    assert candidate_capabilities("financial_investigation") == [
        "financial_risk_scan",
        "financial_metric_query",
        "financial_metric_compare",
        "document_retrieval",
        "event_query",
    ]

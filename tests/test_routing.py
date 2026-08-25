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


def test_llm_entity_merge_deduplicates_canonical_company_id() -> None:
    parsed = parse_request(
        "四川九洲",
        resolver=RESOLVER,
        llm_fallback=lambda *_: ParsedRequest(raw_query="四川九洲", entities=["四川九洲"]),
    )
    assert parsed.entities == ["000801.SZ"]


def test_parse_never_defaults_to_sample_company() -> None:
    parsed = parse_request("净利润是多少", resolver=RESOLVER)
    assert parsed.entities == []
    assert "company_ids" in parsed.missing_slots


def test_unknown_explicit_company_is_not_treated_as_a_broad_topic() -> None:
    parsed = parse_request("新光制药属于哪个细分板块", resolver=RESOLVER)
    pre = check_answerability(parsed)

    assert parsed.entities == []
    assert any(item.term == "新光制药" and item.status == "not_found" for item in parsed.entity_candidates)
    assert pre.status == "clarification_required"
    assert "新光制药" in (pre.clarification_question or "")


def test_industry_topic_routes_to_broad_document_retrieval() -> None:
    parsed = parse_request("化学制药板块有哪些公司", resolver=RESOLVER)
    pre = check_answerability(parsed)
    action = build_direct_action(parsed)

    assert parsed.entities == []
    assert parsed.entity_candidates == []
    assert parsed.task_family == "document_retrieval"
    assert pre.status == "routeable"
    assert action is not None and action.tool_name == "document_search"
    assert "company_ids" not in action.arguments


def test_broad_market_and_industry_news_route_without_company() -> None:
    questions = (
        "近期科技产业有哪些新动态？",
        "市场动态方面有什么新消息？",
        "今天市场有哪些事件需要关注",
        "今天有什么热点需要关注？",
        "今天市场有什么利空消息？",
    )
    for question in questions:
        parsed = parse_request(question, resolver=RESOLVER, knowledge_cutoff="2026-05-28")
        assert parsed.entities == []
        assert parsed.entity_candidates == []
        assert parsed.task_family == "document_retrieval"
        assert check_answerability(parsed).status == "routeable"


def test_recent_market_performance_is_realtime_but_financial_performance_is_not() -> None:
    market = parse_request("证券最近表现如何", resolver=RESOLVER)
    overall = parse_request("今天市场整体表现如何？", resolver=RESOLVER)
    financial = parse_request("贵州茅台近期财务表现如何", resolver=RESOLVER)

    assert market.task_family == "realtime_market_query"
    assert overall.task_family == "realtime_market_query"
    assert financial.task_family != "realtime_market_query"


def test_financial_report_list_routes_to_announcement_search() -> None:
    parsed = parse_request("海能达最新的财务报告有哪些", resolver=RESOLVER)
    action = build_direct_action(parsed)

    assert parsed.task_family == "document_retrieval"
    assert parsed.document_types == ["announcement"]
    assert action is not None and action.tool_name == "document_search"


def test_research_report_content_routes_to_research_investigation() -> None:
    parsed = parse_request("海能达的研究报告内容是什么", resolver=RESOLVER)

    assert parsed.task_family == "research_investigation"
    assert parsed.entities == ["002583.SZ"]


def test_company_news_routes_to_event_investigation() -> None:
    parsed = parse_request("海能达近期有哪些重要消息", resolver=RESOLVER)
    news = parse_request("东吴证券近期有哪些新闻值得关注", resolver=RESOLVER)

    assert parsed.task_family == "event_investigation"
    assert parsed.entities == ["002583.SZ"]
    assert news.task_family == "event_investigation"
    assert news.entities == ["601555.SH"]


def test_market_performance_and_buy_advice_use_existing_boundaries() -> None:
    market = parse_request("格林美今天表现怎么样", resolver=RESOLVER, knowledge_cutoff="2026-05-28")
    advice = parse_request("贵州茅台还能继续买吗", resolver=RESOLVER)

    assert market.task_family == "realtime_market_query"
    assert check_answerability(market).status == "unsupported"
    assert advice.task_family == "prediction_request"
    assert check_answerability(advice).status == "unsupported"


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


def test_financial_risk_without_periods_does_not_require_user_clarification() -> None:
    parsed = parse_request("分析一下600519.SH的金融风险", resolver=RESOLVER)
    assert parsed.task_family == "financial_investigation"
    assert parsed.parsed_by == "rule"
    assert parsed.periods == []
    assert parsed.focus_topics == []
    pre = check_answerability(parsed)
    assert pre.status == "routeable"


def test_risk_focus_topics_use_rule_catalog_vocabulary() -> None:
    inventory = parse_request("分析600519.SH 2023年和2024年的存货风险", resolver=RESOLVER)
    cashflow = parse_request("分析600519.SH 2023年和2024年的现金流风险", resolver=RESOLVER)
    assert inventory.focus_topics == ["asset_quality"]
    assert cashflow.focus_topics == ["earnings_quality"]


def test_parse_realtime_and_prediction_families() -> None:
    assert parse_request("股价现在多少", resolver=RESOLVER).requires_realtime
    assert parse_request("明年会涨吗", resolver=RESOLVER).requires_prediction


def test_market_and_watchlist_phrases_are_rejected_as_realtime_requests() -> None:
    questions = (
        "今天市场上有哪些涨停的股票",
        "今天跑赢大盘了吗",
        "今天自选股涨最多？",
        "跌最狠的是哪一只",
    )
    for question in questions:
        parsed = parse_request(question, resolver=RESOLVER, knowledge_cutoff="2026-05-28")
        assert parsed.task_family == "realtime_market_query"
        assert parsed.requires_realtime
        assert check_answerability(parsed).status == "unsupported"


def test_mixed_supported_and_realtime_request_keeps_supported_task() -> None:
    parsed = parse_request("600519.SH最新营业收入和现在股价是多少", resolver=RESOLVER, knowledge_cutoff="2026-05-28")
    assert parsed.task_family == "financial_metric_query"
    assert parsed.time_mode == "latest"
    assert parsed.end_date == "2026-05-28"
    assert parsed.capability_gaps == ["realtime_market_data_unavailable"]
    assert check_answerability(parsed).status == "routeable_with_gaps"


def test_mixed_supported_and_account_request_keeps_supported_task() -> None:
    parsed = parse_request("600519.SH营业收入是多少，怎么修改银行卡", resolver=RESOLVER)
    assert parsed.task_family == "financial_metric_query"
    assert parsed.capability_gaps == ["user_account_operation_unavailable"]
    assert check_answerability(parsed).status == "routeable_with_gaps"


def test_pure_account_request_remains_unsupported() -> None:
    parsed = parse_request("如何修改银行卡", resolver=RESOLVER)
    assert parsed.task_family == "user_account_query"
    assert parsed.entities == []
    assert check_answerability(parsed).status == "unsupported"


def test_entity_only_request_is_explored_instead_of_immediately_clarified() -> None:
    parsed = parse_request("贵州茅台", resolver=RESOLVER)
    assert parsed.entities == ["600519.SH"]
    assert parsed.task_family == "unknown"
    pre = check_answerability(parsed)
    assert pre.status == "routeable"
    assert is_investigation(parsed)


def test_securities_company_is_not_an_institution_when_it_is_the_target() -> None:
    company = parse_request("东吴证券", resolver=RESOLVER)
    risk = parse_request("分析东吴证券的财务风险", resolver=RESOLVER)

    assert company.entities == ["601555.SH"]
    assert company.institutions == []
    assert risk.entities == ["601555.SH"]
    assert risk.institutions == []


def test_research_publisher_is_separated_from_target_company() -> None:
    parsed = parse_request("东吴证券如何评价贵州茅台", resolver=RESOLVER)

    assert parsed.entities == ["600519.SH"]
    assert parsed.institutions == ["东吴证券"]
    assert parsed.task_family == "research_view_query"
    assert not parsed.requires_explanation
    assert not parsed.requires_investigation
    assert build_direct_action(parsed).tool_name == "research_analysis"


def test_research_questions_route_by_structured_view_vs_source_text() -> None:
    view = parse_request("机构如何看待601033.SH的盈利前景", resolver=RESOLVER)
    assert view.task_family == "research_view_query"
    assert view.research_claim_types == ["analyst_judgment"]
    assert build_direct_action(view).tool_name == "research_analysis"

    detail = parse_request("机构为什么看好601033.SH，具体依据是什么", resolver=RESOLVER)
    assert detail.task_family == "research_investigation"
    assert is_investigation(detail)

    source = parse_request("查找601033.SH研报原文", resolver=RESOLVER)
    assert source.task_family == "document_retrieval"


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
    assert ("research_analysis", "view_query") in implemented_operations()
    assert CAPABILITIES["document_retrieval"].supports_knowledge_cutoff is True
    assert candidate_capabilities("financial_investigation") == [
        "financial_risk_scan",
        "financial_metric_query",
        "financial_metric_compare",
        "document_retrieval",
        "event_query",
        "research_view_query",
    ]

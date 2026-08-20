"""Request Resolution (Gate A): query -> ParsedRequest. Rules first, LLM skill 02 as fallback."""
from __future__ import annotations

from schemas.agent_state import CurrentContext
from schemas.request import EntityCandidate, ParsedRequest
from tools.entity_resolver import EntityResolver

from harness.routing.entities import (
    extract_document_types,
    extract_entities,
    extract_event_types,
    extract_focus_topics,
)
from harness.routing.time_resolver import resolve_time

METRIC_KEYWORDS = (
    ("INVENTORY", ("存货", "库存")),
    ("ACCOUNTS_RECEIVABLE", ("应收", "回款")),
    ("OPERATING_CASHFLOW", ("经营现金流", "现金流")),
    ("NET_PROFIT_PARENT", ("归母净利润", "净利润", "利润")),
    ("REVENUE", ("营收", "营业收入", "收入")),
    ("OPERATING_COST", ("营业成本", "成本")),
    ("TOTAL_ASSETS", ("总资产", "资产规模")),
    ("TOTAL_LIABILITIES", ("总负债", "负债")),
    ("CURRENT_LIABILITIES", ("流动负债", "偿债")),
    ("CURRENT_ASSETS", ("流动资产",)),
    ("MONETARY_CAPITAL", ("货币资金", "现金余额")),
    ("OPERATING_PROFIT", ("营业利润",)),
    ("R_AND_D_EXPENSE", ("研发费用", "研发投入")),
)
DEFAULT_METRICS = ["REVENUE", "NET_PROFIT_PARENT", "OPERATING_CASHFLOW"]

EXPLANATION_MARKERS = ("为什么", "原因", "如何", "怎么看", "意味着", "是否异常", "合理吗", "背后")
INVESTIGATION_MARKERS = ("分析", "调查", "排查", "诊断", "综合", "尽调", "风险画像", "结合", "并评估", "研判")
COMPARE_MARKERS = ("变化", "比较", "对比", "同比增长", "同比下降", "趋势", "涨了", "跌了", "增长多少", "下降多少", "变动")
REALTIME_MARKERS = ("股价", "市值", "行情", "涨跌幅", "K线", "盘口", "实时", "今日净值", "现价")
PREDICTION_MARKERS = ("会涨", "会跌", "能涨", "目标价是多少", "值不值得买", "买入建议", "预测未来", "明年会")


def parse_request(
    query: str,
    *,
    current_context: CurrentContext | None = None,
    resolver: EntityResolver | None = None,
    knowledge_cutoff: str | None = None,
    llm_fallback=None,
) -> ParsedRequest:
    """Build ParsedRequest deterministically; `llm_fallback` (run_skill) refines unknown families."""
    resolver = resolver or EntityResolver()
    extraction = extract_entities(query, resolver, current_context)
    time = resolve_time(query, knowledge_cutoff)

    parsed = ParsedRequest(
        raw_query=query,
        entities=extraction.company_ids,
        entity_candidates=[
            EntityCandidate(term=item["term"], company_ids=[c["company_id"] for c in item["candidates"] if c.get("company_id")], status="ambiguous")
            for item in extraction.ambiguous
        ],
        people=extraction.holder_names,
        periods=time.periods,
        as_of_dates=time.as_of_dates,
        start_date=time.start_date,
        end_date=time.end_date,
        metrics=_infer_metrics(query),
        focus_topics=extract_focus_topics(query),
        document_types=extract_document_types(query),
        event_types=extract_event_types(query),
        requires_explanation=any(marker in query for marker in EXPLANATION_MARKERS),
        requires_realtime=any(marker in query for marker in REALTIME_MARKERS),
        requires_prediction=any(marker in query for marker in PREDICTION_MARKERS),
        parsed_by="rule",
    )
    if extraction.unresolved_terms:
        parsed.unresolved_references.extend(extraction.unresolved_terms)
    if time.unresolved:
        parsed.unresolved_references.extend(time.unresolved)

    parsed.task_family = _infer_task_family(parsed, query)
    parsed.comparison_type = _infer_comparison(parsed, query)
    parsed.requires_investigation = parsed.requires_explanation or any(
        marker in query for marker in INVESTIGATION_MARKERS
    ) or parsed.task_family in {"financial_investigation", "event_investigation"}

    if parsed.task_family == "unknown" and llm_fallback is not None:
        llm_parsed = llm_fallback(query, current_context, parsed)
        if llm_parsed is not None:
            _merge_llm_result(parsed, llm_parsed, resolver)

    if parsed.entities and not parsed.periods and parsed.task_family in {
        "financial_metric_query",
        "financial_metric_compare",
    }:
        parsed.missing_slots.append("report_periods")
    if not parsed.entities and parsed.task_family in {
        "financial_metric_query",
        "financial_metric_compare",
        "ownership_snapshot",
        "ownership_compare",
        "event_query",
    }:
        parsed.missing_slots.append("company_ids")
    parsed.missing_slots = list(dict.fromkeys(parsed.missing_slots))
    return parsed


def _infer_task_family(parsed: ParsedRequest, query: str) -> str:
    if parsed.requires_realtime:
        return "realtime_market_query"
    if parsed.requires_prediction:
        return "prediction_request"

    has_ownership = any(word in query for word in ("股东", "持股", "十大", "实控人", "减持", "增持", "质押"))
    has_document = any(word in query for word in ("公告", "问询函", "年报", "研报", "原文", "披露", "检索", "查找", "找出"))
    has_event = any(word in query for word in ("事件", "时间线", "处罚", "立案", "违规记录", "警示函", "经过"))
    has_metric = bool(parsed.metrics)

    if "穿透" in query:
        return "ownership_penetration"
    if has_ownership and any(word in query for word in ("变化", "减持", "增持", "进入", "退出", "比较")):
        return "ownership_compare"
    if has_ownership and not has_document:
        return "ownership_snapshot"
    if has_event and (parsed.requires_explanation or "调查" in query or "梳理" in query):
        return "event_investigation"
    if has_event:
        return "event_query"
    if has_document and not has_metric:
        return "document_retrieval"
    if has_metric and any(marker in query for marker in COMPARE_MARKERS):
        return "financial_metric_compare"
    if has_metric:
        return "financial_metric_query"
    if parsed.requires_explanation and not has_document:
        return "financial_investigation"
    if has_document:
        return "document_retrieval"
    return "unknown"


def _infer_comparison(parsed: ParsedRequest, query: str) -> str:
    if any(marker in query for marker in COMPARE_MARKERS) or parsed.task_family in {
        "financial_metric_compare",
        "ownership_compare",
    }:
        if len(parsed.entities) > 1 and len(parsed.periods) <= 1:
            return "cross_entity"
        if len(parsed.periods) > 1:
            return "cross_period"
        return "ambiguous"
    return "none"


def _infer_metrics(query: str) -> list[str]:
    selected = [code for code, keywords in METRIC_KEYWORDS if any(keyword in query for keyword in keywords)]
    return selected or []


def _merge_llm_result(parsed: ParsedRequest, llm_parsed: ParsedRequest, resolver: EntityResolver) -> None:
    """LLM output wins for semantics; deterministic entity resolution stays authoritative."""
    parsed.task_family = llm_parsed.task_family if llm_parsed.task_family != "unknown" else parsed.task_family
    parsed.metrics = list(dict.fromkeys(parsed.metrics + llm_parsed.metrics))
    parsed.focus_topics = list(dict.fromkeys(parsed.focus_topics + llm_parsed.focus_topics))
    parsed.document_types = list(dict.fromkeys(parsed.document_types + llm_parsed.document_types))
    parsed.event_types = list(dict.fromkeys(parsed.event_types + llm_parsed.event_types))
    parsed.requires_explanation = parsed.requires_explanation or llm_parsed.requires_explanation
    parsed.requires_investigation = parsed.requires_investigation or llm_parsed.requires_investigation
    parsed.requires_realtime = parsed.requires_realtime or llm_parsed.requires_realtime
    parsed.requires_prediction = parsed.requires_prediction or llm_parsed.requires_prediction
    parsed.comparison_type = llm_parsed.comparison_type if parsed.comparison_type == "none" else parsed.comparison_type
    parsed.unresolved_references = list(
        dict.fromkeys(parsed.unresolved_references + llm_parsed.unresolved_references)
    )
    parsed.missing_slots = list(dict.fromkeys(parsed.missing_slots + llm_parsed.missing_slots))
    parsed.parsed_by = "llm"
    # Entities from the LLM are still re-resolved through the alias index; never trusted blindly.
    for term in llm_parsed.entities:
        if term in parsed.entities:
            continue
        resolution = resolver.resolve_company(term)
        if resolution.status == "resolved" and resolution.company_id:
            parsed.entities.append(resolution.company_id)

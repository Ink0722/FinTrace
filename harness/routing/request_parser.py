"""Request Resolution (Gate A): query -> ParsedRequest. Rules first, LLM skill 02 as fallback."""
from __future__ import annotations

import re

from schemas.agent_state import CurrentContext
from schemas.request import EntityCandidate, ParsedRequest
from tools.entity_resolver import EntityResolver

from harness.routing.entities import (
    extract_document_types,
    extract_entities,
    extract_event_types,
    extract_focus_topics,
    is_industry_topic_query,
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
MARKET_FLOW_MARKERS = (
    "主力资金", "主力控盘", "主力控仓", "主力成本", "主力持仓", "主力增仓", "增仓占比",
    "资金流向", "资金流入", "资金流出", "资金净流入", "资金净流出", "融资余额", "融券余额",
)
MARKET_TECHNICAL_MARKERS = (
    "走势", "金叉", "死叉", "横盘", "放量", "缩量", "量价齐升", "均线", "压力位", "支撑位",
    "筹码", "形态", "自由流通市值",
)
REALTIME_MARKERS = (
    "股价", "市值", "行情", "涨跌幅", "K线", "盘口", "实时", "今日净值", "现价",
    "涨停", "跌停", "大盘", "自选股", "涨最多", "跌最狠", "龙虎榜", "大宗交易",
    "强势股", "连板", "换手率", "动态市盈率", "市净率", "成交量", "成交额", "振幅", "波动率",
    *MARKET_FLOW_MARKERS,
    *MARKET_TECHNICAL_MARKERS,
)
NON_MARKET_TREND_PHRASES = (
    "经营走势", "业绩走势", "营收走势", "利润走势", "现金流走势", "业务形态", "经营形态",
)
PREDICTION_MARKERS = (
    "会涨", "会跌", "能涨", "目标价是多少", "值不值得买", "买入建议", "预测未来", "明年会",
    "还能买吗", "能买吗", "可以买", "该不该买", "要不要买", "要不要卖",
)
USER_ACCOUNT_MARKERS = ("银行卡", "账户权限", "交易权限", "开户", "销户", "修改密码", "持仓账户")
RESEARCH_MARKERS = ("机构", "券商", "分析师", "研报", "研究报告", "机构研究", "评级", "目标价", "盈利预测", "风险提示")
RESEARCH_DETAIL_MARKERS = ("原文", "出处", "依据", "理由", "为什么", "详细", "具体怎么说", "内容")
FINANCIAL_RISK_MARKERS = ("金融风险", "财务风险", "风险评估", "风险分析", "财务排雷", "风险扫描")
COMPANY_NEWS_MARKERS = ("最新动态", "近期动态", "市场动态", "重要消息", "近期新闻", "最新消息")
INSTITUTION_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,12}(?:证券|研究所|基金))")
VALID_FOCUS_TOPICS = {"asset_quality", "earnings_quality", "profitability", "solvency"}
FOCUS_TOPIC_ALIASES = {
    "inventory": "asset_quality", "receivable": "asset_quality",
    "cashflow": "earnings_quality", "profit_quality": "earnings_quality",
    "gross_margin": "profitability",
}


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
    institutions, institution_company_ids = _infer_institutions(query, resolver)
    target_company_ids = [
        company_id for company_id in extraction.company_ids
        if company_id not in institution_company_ids
    ]

    parsed = ParsedRequest(
        raw_query=query,
        entities=target_company_ids,
        entity_candidates=[
            EntityCandidate(term=item["term"], company_ids=[c["company_id"] for c in item["candidates"] if c.get("company_id")], status="ambiguous")
            for item in extraction.ambiguous
        ] + [EntityCandidate(term=term, status="not_found") for term in extraction.unresolved_terms],
        people=extraction.holder_names,
        periods=time.periods,
        requested_periods=time.periods,
        as_of_dates=time.as_of_dates,
        start_date=time.start_date,
        end_date=time.end_date,
        time_mode=time.mode,
        metrics=_infer_metrics(query),
        focus_topics=extract_focus_topics(query),
        document_types=extract_document_types(query),
        event_types=extract_event_types(query),
        research_claim_types=_infer_research_claim_types(query),
        institutions=institutions,
        requires_explanation=any(marker in query for marker in EXPLANATION_MARKERS),
        requires_realtime=_requires_realtime_data(query),
        requires_prediction=_requires_prediction(query),
        parsed_by="rule",
    )
    if extraction.unresolved_terms:
        parsed.unresolved_references.extend(extraction.unresolved_terms)
    if time.unresolved:
        parsed.unresolved_references.extend(time.unresolved)

    parsed.task_family = _infer_task_family(parsed, query)
    if parsed.task_family == "research_view_query":
        # “如何评价/怎么看” asks for the attributed view itself. Only explicit
        # requests for reasons or source context should enter investigation mode.
        parsed.requires_explanation = False
    if parsed.task_family == "user_account_query":
        # Account terms such as “银行卡” may contain company-name substrings.
        # They are irrelevant to this task and must not pollute financial context.
        parsed.entities = []
        parsed.entity_candidates = []
        parsed.people = []
    parsed.capability_gaps = _infer_capability_gaps(parsed)
    parsed.comparison_type = _infer_comparison(parsed, query)
    parsed.requires_investigation = parsed.requires_explanation or any(
        marker in query for marker in INVESTIGATION_MARKERS
    ) or parsed.task_family in {"financial_investigation", "event_investigation"}

    if parsed.task_family == "unknown" and llm_fallback is not None:
        llm_parsed = llm_fallback(query, current_context, parsed)
        if llm_parsed is not None:
            _merge_llm_result(parsed, llm_parsed, resolver)
            parsed.institutions = [
                institution for institution in parsed.institutions
                if _has_institution_role(query, institution)
            ]

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
        "research_view_query",
    }:
        parsed.missing_slots.append("company_ids")
    parsed.missing_slots = list(dict.fromkeys(parsed.missing_slots))
    return parsed


def _infer_task_family(parsed: ParsedRequest, query: str) -> str:
    has_user_account = any(marker in query for marker in USER_ACCOUNT_MARKERS)
    has_research = bool(parsed.institutions) or any(marker in query for marker in RESEARCH_MARKERS)
    has_ownership = any(word in query for word in ("股东", "持股", "十大", "实控人", "减持", "增持", "质押"))
    has_document = bool(parsed.document_types) or any(
        word in query for word in ("公告", "问询函", "财务报告", "季度报告", "研报", "研究报告", "原文", "披露", "检索", "查找", "找出")
    )
    has_event = any(word in query for word in ("事件", "时间线", "处罚", "立案", "违规记录", "警示函", "经过"))
    has_metric = bool(parsed.metrics)
    has_financial_risk = any(marker in query for marker in FINANCIAL_RISK_MARKERS)
    has_supported_domain = has_research or has_ownership or has_document or has_event or has_metric or has_financial_risk

    if has_user_account and not has_supported_domain:
        return "user_account_query"
    if parsed.requires_realtime and not has_supported_domain:
        return "realtime_market_query"
    if parsed.requires_prediction and not has_supported_domain:
        return "prediction_request"
    if is_industry_topic_query(query) and not parsed.entities and not parsed.unresolved_references:
        return "document_retrieval"
    if has_research and any(marker in query for marker in RESEARCH_DETAIL_MARKERS):
        if any(marker in query for marker in ("找", "查找", "检索")) and "观点" not in query:
            return "document_retrieval"
        return "research_investigation"
    if has_research:
        return "research_view_query"
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
    if parsed.entities and _is_company_news_query(query):
        return "event_investigation"
    if has_financial_risk:
        return "financial_investigation"
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
    if parsed.requires_realtime:
        return "realtime_market_query"
    if parsed.requires_prediction:
        return "prediction_request"
    return "unknown"


def _requires_realtime_data(query: str) -> bool:
    matched = [marker for marker in REALTIME_MARKERS if marker in query]
    if matched and not (
        set(matched).issubset({"走势", "形态"})
        and any(phrase in query for phrase in NON_MARKET_TREND_PHRASES)
    ):
        return True
    if any(marker in query for marker in ("经营表现", "财务表现", "业绩表现")):
        return False
    return bool(re.search(
        r"(?:今天|今日|近期|最近).{0,10}(?:走势|涨跌|涨幅|跌幅|市场表现|表现(?:怎么样|如何))",
        query,
    ))


def _is_company_news_query(query: str) -> bool:
    if any(marker in query for marker in COMPANY_NEWS_MARKERS):
        return True
    return bool(re.search(r"(?:近期|最近|最新).{0,8}(?:动态|消息|新闻)", query))


def _requires_prediction(query: str) -> bool:
    if any(marker in query for marker in PREDICTION_MARKERS):
        return True
    return bool(re.search(r"(?:还能|是否|可不可以|能不能).{0,6}(?:买|卖)(?:吗|呢|？|\?)?", query))


def _infer_capability_gaps(parsed: ParsedRequest) -> list[str]:
    gaps: list[str] = []
    if parsed.requires_realtime and parsed.task_family != "realtime_market_query":
        gaps.append("realtime_market_data_unavailable")
    if parsed.requires_prediction and parsed.task_family != "prediction_request":
        gaps.append("deterministic_investment_recommendation_unavailable")
    if any(marker in parsed.raw_query for marker in USER_ACCOUNT_MARKERS) and parsed.task_family != "user_account_query":
        gaps.append("user_account_operation_unavailable")
    return gaps


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
    selected = []
    for code, keywords in METRIC_KEYWORDS:
        if not any(keyword in query for keyword in keywords):
            continue
        if code == "OPERATING_COST" and "主力成本" in query and not any(
            phrase in query for phrase in ("营业成本", "经营成本")
        ):
            continue
        selected.append(code)
    return selected or []


def _infer_research_claim_types(query: str) -> list[str]:
    values = []
    mapping = (
        ("investment_rating", ("评级", "买入", "增持", "减持", "目标价")),
        ("earnings_forecast", ("盈利预测", "利润预测", "业绩预测", "预期")),
        ("risk_opinion", ("风险提示", "担忧", "风险")),
        ("analyst_judgment", ("怎么看", "观点", "认为", "评价", "如何看待")),
    )
    for claim_type, markers in mapping:
        if any(marker in query for marker in markers):
            values.append(claim_type)
    return values


def _infer_institutions(query: str, resolver: EntityResolver) -> tuple[list[str], set[str]]:
    """Separate a research publisher from a listed company that is the query target."""
    institutions: list[str] = []
    company_ids: set[str] = set()
    for name in dict.fromkeys(INSTITUTION_PATTERN.findall(query)):
        if not _has_institution_role(query, name):
            continue
        institutions.append(name)
        resolution = resolver.resolve_company(name)
        if resolution.status == "resolved" and resolution.company_id:
            company_ids.add(resolution.company_id)
    return institutions, company_ids


def _has_institution_role(query: str, name: str) -> bool:
    escaped = re.escape(name)
    patterns = (
        rf"(?:根据|按照|引用|来自)\s*{escaped}",
        rf"{escaped}(?:发布|出具|撰写|覆盖|发表)?(?:的)?(?:研报|研究报告|观点|预测|判断|分析师观点)",
        rf"{escaped}(?:认为|指出|预计|给予|维持|上调|下调|如何评价|如何看待|怎么看|对)",
    )
    return any(re.search(pattern, query) for pattern in patterns)


def _merge_llm_result(parsed: ParsedRequest, llm_parsed: ParsedRequest, resolver: EntityResolver) -> None:
    """LLM output wins for semantics; deterministic entity resolution stays authoritative."""
    parsed.task_family = llm_parsed.task_family if llm_parsed.task_family != "unknown" else parsed.task_family
    parsed.metrics = list(dict.fromkeys(parsed.metrics + llm_parsed.metrics))
    parsed.focus_topics = _normalize_focus_topics(parsed.focus_topics + llm_parsed.focus_topics)
    parsed.document_types = list(dict.fromkeys(parsed.document_types + llm_parsed.document_types))
    parsed.event_types = list(dict.fromkeys(parsed.event_types + llm_parsed.event_types))
    parsed.research_claim_types = list(dict.fromkeys(parsed.research_claim_types + llm_parsed.research_claim_types))
    parsed.institutions = list(dict.fromkeys(parsed.institutions + llm_parsed.institutions))
    parsed.requires_explanation = parsed.requires_explanation or llm_parsed.requires_explanation
    parsed.requires_investigation = parsed.requires_investigation or llm_parsed.requires_investigation
    parsed.requires_realtime = parsed.requires_realtime or llm_parsed.requires_realtime
    parsed.requires_prediction = parsed.requires_prediction or llm_parsed.requires_prediction
    parsed.capability_gaps = list(dict.fromkeys(parsed.capability_gaps + llm_parsed.capability_gaps))
    parsed.comparison_type = llm_parsed.comparison_type if parsed.comparison_type == "none" else parsed.comparison_type
    parsed.unresolved_references = list(
        dict.fromkeys(parsed.unresolved_references + llm_parsed.unresolved_references)
    )
    parsed.missing_slots = list(dict.fromkeys(parsed.missing_slots + llm_parsed.missing_slots))
    parsed.parsed_by = "llm"
    # Entities from the LLM are still re-resolved through the alias index; never trusted blindly.
    for term in llm_parsed.entities:
        resolution = resolver.resolve_company(term)
        if (
            resolution.status == "resolved"
            and resolution.company_id
            and resolution.company_id not in parsed.entities
        ):
            parsed.entities.append(resolution.company_id)
        elif resolution.status == "not_found":
            if not any(item.term == term for item in parsed.entity_candidates):
                parsed.entity_candidates.append(EntityCandidate(term=term, status="not_found"))
            if term not in parsed.unresolved_references:
                parsed.unresolved_references.append(term)
    parsed.entities = list(dict.fromkeys(parsed.entities))


def _normalize_focus_topics(values: list[str]) -> list[str]:
    normalized = [FOCUS_TOPIC_ALIASES.get(value, value) for value in values]
    return list(dict.fromkeys(value for value in normalized if value in VALID_FOCUS_TOPICS))

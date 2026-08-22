"""Entity extraction with alias-index resolution. No default company (docs/13 §7)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from schemas.agent_state import CurrentContext
from tools.entity_resolver import EntityResolver

# Explicit demo aliases kept for the sample scenario; never used as a silent fallback.
DEMO_COMPANY_ALIASES = {"示例公司": "000001.SZ"}
PRONOUN_COMPANY = ("这家公司", "该公司", "本公司", "标的公司", "这家企业")

# KB-valid document vocabulary (C2): fine-grained intent stays in the query text.
DOCUMENT_TYPE_KEYWORDS = {
    "announcement": ("问询函", "监管问询", "年报", "年度报告", "审计报告", "审计意见", "公告", "原文", "披露", "违规", "处罚"),
    "research_report": ("研报", "研究报告", "机构观点"),
}
EVENT_TYPE_KEYWORDS = {
    "regulatory_inquiry": ("问询函", "监管问询"),
    "regulatory_penalty": ("处罚", "监管处罚", "立案", "警示函"),
    "controller_change": ("控制权", "实控人变更", "股权变更"),
    "share_pledge": ("质押",),
    "major_litigation": ("诉讼",),
    "risk_warning": ("退市风险", "风险警示", "违规", "立案调查"),
    "public_opinion": ("舆情", "新闻"),
}
FOCUS_TOPIC_KEYWORDS = {
    "asset_quality": ("存货", "跌价准备", "库存", "应收", "应收账款"),
    "earnings_quality": ("现金流", "经营现金流", "回款", "利润质量", "净利润", "非经常性损益"),
    "profitability": ("毛利率", "毛利", "盈利能力"),
    "solvency": ("偿债", "负债", "债务"),
}


@dataclass
class EntityExtraction:
    company_ids: list[str] = field(default_factory=list)  # resolved canonical ids
    ambiguous: list[dict] = field(default_factory=list)  # {term, candidates}
    unresolved_terms: list[str] = field(default_factory=list)  # raw names we could not resolve
    inherited: bool = False  # company came from session context pronoun inheritance
    holder_names: list[str] = field(default_factory=list)


def extract_entities(
    query: str,
    resolver: EntityResolver,
    current_context: CurrentContext | None = None,
) -> EntityExtraction:
    result = EntityExtraction()
    seen: set[str] = set()

    def add_company(company_id: str) -> None:
        if company_id not in seen:
            seen.add(company_id)
            result.company_ids.append(company_id)

    for alias, company_id in DEMO_COMPANY_ALIASES.items():
        if alias in query:
            add_company(company_id)

    for term in resolver.find_company_names_in_text(query):
        resolution = resolver.resolve_company(term)
        if resolution.status == "resolved" and resolution.company_id:
            add_company(resolution.company_id)
        elif resolution.status == "ambiguous":
            result.ambiguous.append({"term": term, "candidates": resolution.candidates})
        else:
            result.unresolved_terms.append(term)

    for code in re.findall(r"(?<![0-9A-Za-z.])(\d{6}\.(?:SZ|SH|BJ|sz|sh|bj))(?![0-9A-Za-z.])", query):
        resolution = resolver.resolve_company(code)
        if resolution.status == "resolved" and resolution.company_id:
            add_company(resolution.company_id)
        else:
            result.unresolved_terms.append(code.upper())

    for bare in set(re.findall(r"(?<![\d.])(\d{6})(?![\d.])", query)):
        resolution = resolver.resolve_company(bare)
        if resolution.status == "resolved" and resolution.company_id:
            add_company(resolution.company_id)
        elif resolution.status == "ambiguous":
            result.ambiguous.append({"term": bare, "candidates": resolution.candidates})

    if not result.company_ids and not result.ambiguous and not result.unresolved_terms:
        if any(pronoun in query for pronoun in PRONOUN_COMPANY) and current_context and len(
            set(current_context.company_ids)
        ) == 1:
            add_company(current_context.company_ids[0])
            result.inherited = True

    result.holder_names = extract_holder_names(query)
    return result


def extract_holder_names(query: str) -> list[str]:
    """Holder/person names near ownership verbs; LLM parser refines these in complex cases."""
    names: list[str] = []
    for match in re.finditer(r"股东\s*([一-鿿A-Za-z0-9（）()·]{2,30}?)(?:\s)*(?:持有|持股|减持|增持|是|持有多少)", query):
        name = match.group(1).strip("的 了")
        if 2 <= len(name) <= 30:
            names.append(name)
    for match in re.finditer(r"([一-鿿·]{2,4})(?:持有|减持|增持)(?:了|多少)?", query):
        name = match.group(1)
        if 2 <= len(name) <= 4 and name not in ("公司持有", "股东持"):
            names.append(name)
    return list(dict.fromkeys(names))


def extract_document_types(query: str) -> list[str]:
    return _extract_by_keywords(query, DOCUMENT_TYPE_KEYWORDS)


def extract_event_types(query: str) -> list[str]:
    return _extract_by_keywords(query, EVENT_TYPE_KEYWORDS)


def extract_focus_topics(query: str) -> list[str]:
    return _extract_by_keywords(query, FOCUS_TOPIC_KEYWORDS)


def _extract_by_keywords(query: str, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    values: list[str] = []
    for value, keywords in mapping.items():
        if any(keyword in query for keyword in keywords):
            values.append(value)
    return values

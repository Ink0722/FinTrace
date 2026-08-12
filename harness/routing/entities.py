import re


COMPANY_ALIASES = {
    "示例公司": "000001.SZ",
    "这家公司": "000001.SZ",
    "该公司": "000001.SZ",
}

FOCUS_TOPIC_KEYWORDS = {
    "inventory": ("存货", "跌价准备", "库存"),
    "cashflow": ("现金流", "经营现金流", "回款"),
    "receivable": ("应收", "应收账款"),
    "profit_quality": ("利润质量", "净利润", "非经常性损益"),
    "gross_margin": ("毛利率", "毛利"),
    "solvency": ("偿债", "负债", "债务"),
}

DOCUMENT_TYPE_KEYWORDS = {
    "inquiry_letter": ("问询函", "监管问询"),
    "annual_report": ("年报", "年度报告"),
    "audit_report": ("审计报告", "审计意见"),
    "research_report": ("研报",),
    "announcement": ("公告", "原文", "披露"),
}

EVENT_TYPE_KEYWORDS = {
    "regulatory_inquiry": ("问询函", "监管问询"),
    "regulatory_penalty": ("处罚", "监管处罚", "立案"),
    "control_change": ("控制权", "实控人变更", "股权变更"),
    "public_opinion": ("舆情", "新闻"),
}


def extract_company_id(query: str) -> str:
    stock_code = re.search(r"\b\d{6}\.(?:SZ|SH|BJ)\b", query, flags=re.IGNORECASE)
    if stock_code:
        return stock_code.group(0).upper()
    bare_code = re.search(r"\b\d{6}\b", query)
    if bare_code:
        return f"{bare_code.group(0)}.SZ"
    for alias, company_id in COMPANY_ALIASES.items():
        if alias in query:
            return company_id
    return "000001.SZ"


def extract_period(query: str) -> str | None:
    year = re.search(r"(20\d{2})\s*(?:年|A)?", query)
    if not year:
        return None
    return f"{year.group(1)}A"


def extract_focus_topics(query: str) -> list[str]:
    return _extract_by_keywords(query, FOCUS_TOPIC_KEYWORDS)


def extract_document_types(query: str) -> list[str]:
    return _extract_by_keywords(query, DOCUMENT_TYPE_KEYWORDS)


def extract_event_types(query: str) -> list[str]:
    return _extract_by_keywords(query, EVENT_TYPE_KEYWORDS)


def _extract_by_keywords(query: str, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    values: list[str] = []
    for value, keywords in mapping.items():
        if any(keyword in query for keyword in keywords):
            values.append(value)
    return values

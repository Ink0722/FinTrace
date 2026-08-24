"""Deterministic Chinese time expression resolution (docs/13 §8)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

QUARTER_SUFFIXES = {
    "一季报": "03-31", "一季度": "03-31", "一季": "03-31", "q1": "03-31",
    "半年报": "06-30", "中报": "06-30", "上半年": "06-30", "h1": "06-30", "半年": "06-30",
    "三季报": "09-30", "三季度": "09-30", "三季": "09-30", "q3": "09-30",
    "年报": "12-31", "年度": "12-31", "年末": "12-31", "年底": "12-31", "全年": "12-31", "fy": "12-31",
}
PERIOD_KEYWORD = re.compile(
    r"(20\d{2})\s*年?\s*(一季报|一季度|一季|半年报|中报|上半年|三季报|三季度|三季|年报|年度|年末|年底|全年|Q1|q1|H1|h1|Q3|q3|FY|fy)?"
)
BARE_YEAR = re.compile(r"(20\d{2})\s*年?")
EXPLICIT_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
SINCE_PATTERN = re.compile(r"(20\d{2})\s*年(?:以来|之后|后)")
MONTH_END = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:末|底)?")

RELATIVE_LATEST = ("最新", "当前", "现在", "目前", "最近")
RELATIVE_TODAY = ("今天", "今日", "当天")
RELATIVE_RECENT = ("近期", "近来", "最近")
RELATIVE_LAST_YEAR = ("去年", "上一年", "上年")


@dataclass
class TimeResolution:
    periods: list[str] = field(default_factory=list)  # report periods (financial)
    as_of_dates: list[str] = field(default_factory=list)  # ownership observation points
    start_date: str | None = None
    end_date: str | None = None
    mode: str = "unspecified"
    unresolved: list[str] = field(default_factory=list)  # relative expressions we could not normalize


def resolve_time(query: str, knowledge_cutoff: str | None = None) -> TimeResolution:
    result = TimeResolution()

    for match in PERIOD_KEYWORD.finditer(query):
        year = match.group(1)
        keyword = (match.group(2) or "").strip()
        if not keyword and not re.search(rf"{year}\s*年", query):
            # bare year captured by the optional group's prefix; skip noise like codes
            continue
        if not keyword:
            result.periods.append(f"{year}-12-31")  # "2024年" defaults to annual report
        else:
            suffix = QUARTER_SUFFIXES.get(keyword) or QUARTER_SUFFIXES.get(keyword.lower())
            if suffix:
                result.periods.append(f"{year}-{suffix}")

    for iso in EXPLICIT_DATE.findall(query):
        if iso not in result.periods:
            result.as_of_dates.append(iso)

    for year, month in MONTH_END.findall(query):
        result.as_of_dates.append(_month_end(int(year), int(month)))

    since = SINCE_PATTERN.search(query)
    if since:
        result.start_date = f"{since.group(1)}-01-01"

    seen_periods = []
    for period in result.periods:
        if period not in seen_periods:
            seen_periods.append(period)
    result.periods = sorted(seen_periods)
    result.as_of_dates = sorted(set(result.as_of_dates))

    has_explicit_time = bool(result.periods or result.as_of_dates or result.start_date or result.end_date)
    if has_explicit_time:
        result.mode = "explicit"
    if not has_explicit_time and any(marker in query for marker in RELATIVE_TODAY):
        result.mode = "today"
        if knowledge_cutoff:
            result.start_date = knowledge_cutoff
            result.end_date = knowledge_cutoff
        else:
            result.unresolved.append("today")
    elif not has_explicit_time and any(marker in query for marker in RELATIVE_RECENT):
        result.mode = "recent"
        if knowledge_cutoff:
            result.end_date = knowledge_cutoff
    elif not has_explicit_time and any(marker in query for marker in RELATIVE_LATEST):
        result.mode = "latest"
        if knowledge_cutoff:
            result.end_date = knowledge_cutoff
    if any(marker in query for marker in RELATIVE_LAST_YEAR):
        resolved = _last_year(knowledge_cutoff)
        if resolved:
            if resolved not in result.periods:
                result.periods.append(resolved)
            result.periods.sort()
            result.mode = "explicit"
        else:
            result.unresolved.append("last_year")
    return result


def _month_end(year: int, month: int) -> str:
    if month == 12:
        return f"{year}-12-31"
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).isoformat()


def _last_year(knowledge_cutoff: str | None) -> str | None:
    if not knowledge_cutoff:
        return None
    try:
        cutoff = date.fromisoformat(knowledge_cutoff)
    except ValueError:
        return None
    return f"{cutoff.year - 1}-12-31"

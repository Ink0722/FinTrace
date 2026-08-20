"""Deterministic Direct Gate (Gate C): build the single obvious ToolCall without an LLM (docs/13 §11)."""
from __future__ import annotations

from schemas.request import AgentAction, ParsedRequest

DEFAULT_TOP_K = 8


def build_direct_action(parsed: ParsedRequest) -> AgentAction | None:
    """Return the unique legal action, or None to defer to the investigation planner."""
    family = parsed.task_family
    if family == "financial_metric_query" and parsed.entities and parsed.metrics and parsed.periods:
        return AgentAction(
            action="call_tool",
            capability="financial_metric_query",
            tool_name="financial_analysis",
            operation="metric_query",
            arguments={
                "query": parsed.raw_query,
                "operation": "metric_query",
                "company_ids": parsed.entities,
                "metric_codes": parsed.metrics,
                "report_periods": parsed.periods,
            },
            reason="唯一的指标查询能力且参数完整，直接执行。",
            expected_evidence="指定公司与报告期的指标值及来源证据",
        )
    if family == "financial_metric_compare" and parsed.entities and parsed.metrics:
        if len(parsed.entities) == 1 and len(parsed.periods) >= 2:
            return _compare_action(parsed, parsed.entities, parsed.periods, dimension="period")
        if len(parsed.entities) >= 2 and len(parsed.periods) == 1:
            return _compare_action(parsed, parsed.entities, parsed.periods, dimension="company")
        return None  # ambiguous comparison dimension -> defer
    if family == "ownership_snapshot" and (parsed.entities or parsed.people):
        arguments: dict = {"query": parsed.raw_query, "operation": "holding_query", "top_n": 10}
        if parsed.entities:
            arguments["company_ids"] = parsed.entities
        if parsed.people:
            arguments["holder_ids"] = parsed.people
        if parsed.as_of_dates:
            arguments["as_of_date"] = parsed.as_of_dates[-1]
        return AgentAction(
            action="call_tool",
            capability="ownership_snapshot",
            tool_name="ownership_analysis",
            operation="holding_query",
            arguments=arguments,
            reason="唯一的股东快照能力且主体明确，直接执行。",
            expected_evidence="主要股东名单、持股比例与快照元信息",
        )
    if family == "ownership_compare" and len(parsed.entities) == 1:
        start, end = _compare_boundary(parsed)
        if start and end:
            return AgentAction(
                action="call_tool",
                capability="ownership_compare",
                tool_name="ownership_analysis",
                operation="holding_compare",
                arguments={
                    "query": parsed.raw_query,
                    "operation": "holding_compare",
                    "company_ids": parsed.entities,
                    "start_date": start,
                    "end_date": end,
                },
                reason="单一公司且两个观察时点明确，直接执行比较。",
                expected_evidence="两个快照的股东进入、退出与增减持变化",
            )
    if family == "document_retrieval":
        arguments = {"query": parsed.raw_query, "top_k": DEFAULT_TOP_K}
        if parsed.entities:
            arguments["company_ids"] = parsed.entities
        if parsed.document_types:
            arguments["document_types"] = parsed.document_types
        if parsed.start_date:
            arguments["start_date"] = parsed.start_date
        if parsed.end_date:
            arguments["end_date"] = parsed.end_date
        return AgentAction(
            action="call_tool",
            capability="document_retrieval",
            tool_name="document_search",
            operation="search",
            arguments=arguments,
            reason="唯一的文档检索能力，直接执行。",
            expected_evidence="与查询相关的公告或研报 Chunk",
        )
    if family == "event_query" and len(parsed.entities) == 1:
        arguments = {
            "query": parsed.raw_query,
            "scope": "entity",
            "entity_ids": parsed.entities,
        }
        if parsed.event_types:
            arguments["event_types"] = parsed.event_types
        if parsed.start_date or parsed.end_date:
            arguments["start_date"] = parsed.start_date
            arguments["end_date"] = parsed.end_date
        return AgentAction(
            action="call_tool",
            capability="event_query",
            tool_name="event_timeline",
            operation="event_query",
            arguments=arguments,
            reason="单一公司的事件查询，直接执行。",
            expected_evidence="按时间排序的事件节点及证据",
        )
    return None


def _compare_action(parsed: ParsedRequest, company_ids: list[str], periods: list[str], *, dimension: str) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="financial_metric_compare",
        tool_name="financial_analysis",
        operation="metric_compare",
        arguments={
            "query": parsed.raw_query,
            "operation": "metric_compare",
            "company_ids": company_ids,
            "metric_codes": parsed.metrics,
            "report_periods": periods,
            "comparison_method": "both",
        },
        reason=f"比较维度唯一（{dimension}），参数完整，直接执行。",
        expected_evidence="同口径指标的序列、差异或排序",
    )


def _compare_boundary(parsed: ParsedRequest) -> tuple[str | None, str | None]:
    if parsed.start_date and parsed.end_date:
        return parsed.start_date, parsed.end_date
    if len(parsed.as_of_dates) >= 2:
        return parsed.as_of_dates[0], parsed.as_of_dates[-1]
    if len(parsed.periods) >= 2:
        return parsed.periods[0], parsed.periods[-1]
    return None, None

"""Next-action planner. Phase 1: deterministic queue derived from ParsedRequest.

Phase 2 replaces the rule brain with the LLM skill (prompts/03_next_action_planner.md);
the AgentAction contract and this module's public API stay unchanged.
"""
from __future__ import annotations

from schemas.agent_state import AgentState
from schemas.request import AgentAction, ParsedRequest

from harness.routing.direct_gate import DEFAULT_TOP_K, _compare_boundary


def plan_next_action(state: AgentState) -> AgentAction:
    """Emit exactly one action per call, skipping already-executed queue items."""
    parsed = state.parsed_request
    if parsed is None:
        return AgentAction(action="finish", reason="缺少解析结果，终止调查。")

    executed_keys = {(entry.tool_name, entry.operation) for entry in state.tool_call_history}
    for action in _action_queue(parsed):
        if action.action != "call_tool":
            continue
        key = (action.tool_name, action.operation)
        if key in executed_keys and not _arguments_gain(action, state):
            continue
        return action
    return AgentAction(action="finish", reason="规则调查队列已执行完毕。", expected_evidence=None)


def _action_queue(parsed: ParsedRequest) -> list[AgentAction]:
    queue: list[AgentAction] = []
    family = parsed.task_family
    entities = parsed.entities

    if family in {"financial_investigation", "financial_metric_query", "financial_metric_compare", "unknown"}:
        if entities and parsed.metrics and parsed.periods:
            queue.append(_metric_query_action(parsed))
        if entities and parsed.metrics and len(parsed.periods) >= 2 and len(entities) == 1:
            queue.append(_metric_compare_action(parsed))
        if entities:
            queue.append(_document_action(parsed))
            if parsed.document_types:
                queue.append(_document_action(parsed, drop_type_filter=True))
            queue.append(_event_action(parsed))
        if not entities and parsed.people:
            queue.append(_holder_reverse_action(parsed))
        if not queue:
            queue.append(_document_action(parsed))
    elif family in {"ownership_snapshot", "ownership_compare", "ownership_penetration"}:
        if entities or parsed.people:
            queue.append(_holding_query_action(parsed))
        if entities and len(entities) == 1:
            start, end = _compare_boundary(parsed)
            if start and end:
                queue.append(_holding_compare_action(parsed, start, end))
        queue.append(_document_action(parsed))
    elif family == "event_investigation":
        if entities:
            queue.append(_event_action(parsed))
        queue.append(_document_action(parsed))
        if entities and parsed.metrics and parsed.periods:
            queue.append(_metric_query_action(parsed))
    elif family in {"event_query", "document_retrieval", "general_financial_explanation"}:
        if entities and family == "event_query":
            queue.append(_event_action(parsed))
        if family != "event_query" or not entities:
            queue.append(_document_action(parsed))
        if entities and parsed.metrics and parsed.periods and family != "event_query":
            queue.append(_metric_query_action(parsed))
    return queue


def _arguments_gain(action: AgentAction, state: AgentState) -> bool:
    """A repeated call is allowed only when its arguments differ from every executed call."""
    for entry in state.tool_call_history:
        if entry.tool_name == action.tool_name and entry.operation == action.operation:
            if entry.arguments == _canonical_args(action.arguments):
                return False
    return True


def _canonical_args(arguments: dict) -> dict:
    # `operation` is injected at execution for financial/ownership tools; exclude it so
    # LLM-proposed actions and recorded history fingerprints stay comparable.
    return {key: value for key, value in arguments.items() if key not in {"query", "reason", "operation"}}


def _metric_query_action(parsed: ParsedRequest) -> AgentAction:
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
        reason="优先获取结构化财务指标事实。",
        expected_evidence="指标数值、口径与来源证据",
    )


def _metric_compare_action(parsed: ParsedRequest) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="financial_metric_compare",
        tool_name="financial_analysis",
        operation="metric_compare",
        arguments={
            "query": parsed.raw_query,
            "operation": "metric_compare",
            "company_ids": parsed.entities,
            "metric_codes": parsed.metrics,
            "report_periods": parsed.periods,
            "comparison_method": "both",
        },
        reason="对多期指标做确定性比较。",
        expected_evidence="跨期序列与变化幅度",
    )


def _holding_query_action(parsed: ParsedRequest) -> AgentAction:
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
        reason="获取主要股东快照作为股权事实基础。",
        expected_evidence="主要股东名单与持股比例",
    )


def _holding_compare_action(parsed: ParsedRequest, start: str, end: str) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="ownership_compare",
        tool_name="ownership_analysis",
        operation="holding_compare",
        arguments={
            "query": parsed.raw_query,
            "operation": "holding_compare",
            "company_ids": parsed.entities[:1],
            "start_date": start,
            "end_date": end,
        },
        reason="比较两个时点的股东变化。",
        expected_evidence="股东进入、退出与增减持",
    )


def _holder_reverse_action(parsed: ParsedRequest) -> AgentAction:
    return AgentAction(
        action="call_tool",
        capability="ownership_snapshot",
        tool_name="ownership_analysis",
        operation="holding_query",
        arguments={"query": parsed.raw_query, "operation": "holding_query", "holder_ids": parsed.people, "top_n": 10},
        reason="按股东反查其持股的公司。",
        expected_evidence="股东出现的公司快照与持股比例",
    )


def _document_action(parsed: ParsedRequest, *, drop_type_filter: bool = False) -> AgentAction:
    arguments: dict = {"query": parsed.raw_query, "top_k": DEFAULT_TOP_K}
    if parsed.entities:
        arguments["company_ids"] = parsed.entities
    if parsed.document_types and not drop_type_filter:
        arguments["document_types"] = parsed.document_types
    return AgentAction(
        action="call_tool",
        capability="document_retrieval",
        tool_name="document_search",
        operation="search",
        arguments=arguments,
        reason="检索公告/研报文本证据补充解释。",
        expected_evidence="与调查主题相关的原文 Chunk",
    )


def _event_action(parsed: ParsedRequest) -> AgentAction:
    arguments: dict = {
        "query": parsed.raw_query,
        "scope": "entity",
        "entity_ids": parsed.entities,
    }
    if parsed.event_types:
        arguments["event_types"] = parsed.event_types
    return AgentAction(
        action="call_tool",
        capability="event_query",
        tool_name="event_timeline",
        operation="event_query",
        arguments=arguments,
        reason="获取监管与风险事件时间线。",
        expected_evidence="事件节点、类型与日期",
    )
